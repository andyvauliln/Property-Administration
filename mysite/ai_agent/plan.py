"""
Staff review, part 1: what a run WOULD do, described without changing anything (user request 2026-09-23).

While the 15-minute review is on (answer_review.py), a run does not execute its actions. describe() turns them into
numbered plan items for the Telegram alert ("⏳ NOTHING IS DONE YET. At 14:35 ET, unless someone replies: 1. ...").
Staff replies can remove or change items; answer_review.apply_plan() executes what is left with the normal action
handlers when the window ends (or at once on "ok"). Emergencies skip all this and run at once.

INTERNAL_ALERT / QUEUE_FOR_REVIEW are not changes: the Telegram alert itself is that notification, so they are shown
as "FOR THE TEAM" and are not numbered.
"""
import re

from mysite.ai_agent import actions as agent_actions
from mysite.ai_agent import clickup, knowledge, policy

TEAM_TYPES = ('INTERNAL_ALERT', 'QUEUE_FOR_REVIEW')


class _Ref:
    """What an issue id in an action points to, for the description."""

    def __init__(self, label, issue=None, summary='', owner=None, priority='routine', error=None):
        self.label, self.issue, self.summary, self.owner, self.priority, self.error = label, issue, summary, owner, priority, error


def _new_issues(actions):
    return {str(a.get('temp_id')): a for a in actions
            if isinstance(a, dict) and a.get('type') == 'CREATE_ISSUE' and a.get('temp_id')}


def _resolve(ctx, issue_id, new_issues):
    from mysite.models import AIIssue, AIRun

    issue_id = str(issue_id or '').strip()
    if not issue_id or issue_id.lower() in ('null', 'none'):
        return None
    if issue_id in new_issues:
        created = new_issues[issue_id]
        return _Ref(f"the new issue \"{created.get('summary')}\"", summary=created.get('summary') or '',
                    owner=created.get('owner'), priority=created.get('priority') or 'routine')
    earlier = re.fullmatch(r"r(\d+):(new-\d+)", issue_id)
    if earlier:
        run = AIRun.objects.filter(id=int(earlier.group(1)), conversation_sid=ctx.conversation_sid).first()
        plan = ((run.review or {}).get('plan') or {}) if run else {}
        created = _new_issues(plan.get('actions') or []).get(earlier.group(2))
        if not created or plan.get('status') == 'cancelled':
            return _Ref(issue_id, error=f"{issue_id}: that earlier plan was cancelled or has no such issue")
        return _Ref(f"the issue \"{created.get('summary')}\" (from the earlier plan of run #{run.id})",
                    summary=created.get('summary') or '', owner=created.get('owner'))
    match = re.fullmatch(r"[it]-(\d+)", issue_id)
    issue = AIIssue.objects.filter(id=int(match.group(1)), conversation_sid=ctx.conversation_sid).first() if match else None
    if not issue:
        return _Ref(issue_id, error=f"issue {issue_id} does not exist in this chat")
    return _Ref(f"{issue.public_id} \"{issue.summary}\"", issue=issue, summary=issue.summary, owner=issue.owner,
                priority=issue.priority)


def _details(ref_url, cache):
    from mysite.ai_agent import team_notify
    return [line.replace('     ', '   ', 1) for line in team_notify.task_details(ref_url, cache)]


def describe(parsed, ctx):
    """
    Returns plan items [{'idx', 'n' (None for team items), 'kind': change|team|info, 'lines': [...], 'error'}]
    plus the list of actions they index into. Nothing is written anywhere.
    """
    from mysite.ai_agent import team_notify
    from mysite.ai_agent.actions import staff_label

    actions, _ = agent_actions.with_safety_net(parsed, ctx)
    new_issues = _new_issues(actions)
    mapping = clickup.channel_for(ctx.apartment)
    cache, items, number = {}, [], 0
    for idx, action in enumerate(actions):
        if not isinstance(action, dict) or action.get('type') not in agent_actions.ACTION_TYPES:
            items.append({'idx': idx, 'n': None, 'kind': 'info', 'error': 'unknown action type - ignored',
                          'lines': [f"✗ ignored: unknown action {str(action)[:80]}"]})
            continue
        kind = action['type']
        ref = _resolve(ctx, action.get('issue_id') or action.get('ticket_id'), new_issues)
        error = ref.error if ref else None
        about = f" about {ref.label}" if ref and not ref.error else ""
        lines = []
        if kind in TEAM_TYPES:
            priority = action.get('priority') or (ref.priority if ref else 'routine')
            who = staff_label(action.get('responsible') or action.get('owner') or (ref.owner if ref else None)) or 'team'
            head = f"• {str(priority).upper()} · for {who}" + (" · queued for review" if kind == 'QUEUE_FOR_REVIEW' else "")
            lines = [head + about, f"  {str(action.get('text') or '')[:600]}"]
            if action.get('backend_added'):
                lines[0] += " · added by the backend"
            items.append({'idx': idx, 'n': None, 'kind': 'team', 'lines': lines, 'error': None})
            continue

        if kind == 'CREATE_ISSUE':
            lines = [f"🆕 Open issue \"{action.get('summary')}\" · {action.get('state') or 'WAITING_FOR_EDY'}"
                     + (f" · owner {action.get('owner')}" if action.get('owner') else "")]
        elif kind == 'UPDATE_ISSUE_STATE':
            old = ref.issue.state if ref and ref.issue else '?'
            lines = [f"🔄 Issue {ref.label if ref else '?'}: {old} → {action.get('state')}"]
            if action.get('state') == 'RESOLVED':
                lines[0] += " (its reminders stop)"
                if ref and ref.issue and ref.issue.ticket_ref:
                    lines.append(f"   🔒 and CLOSE its ClickUp task → {ref.issue.ticket_ref}")
                    lines += _details(ref.issue.ticket_ref, cache)
        elif kind == 'CREATE_TICKET':
            title = str(action.get('title') or (ref.summary if ref else 'AI ticket'))
            if ref and ref.issue and ref.issue.ticket_ref:
                lines = [f"🎫 No new ClickUp task: {ref.label} already has one → {ref.issue.ticket_ref}"]
                lines += _details(ref.issue.ticket_ref, cache)
            elif not (mapping and mapping.list_id):
                lines = [f"🎫 Ticket \"{title}\" - NO ClickUp task: this apartment has no ClickUp List (Telegram only)"]
            else:
                priority = action.get('priority') or 'routine'
                responsible = action.get('responsible') or ([ref.owner] if ref and ref.owner else [])
                assignees = team_notify.task_assignees(ctx.is_test, responsible if isinstance(responsible, list) else [responsible])
                lines = [f"🎫 Create ClickUp task \"{team_notify.task_name(title, ctx.meta.get('apartment'), ctx.is_test)}\"",
                         f"   List: {mapping.name} · {priority} · due {clickup.DUE_IN_HOURS.get(priority, 72)}h after creation · "
                         f"assigned: {team_notify.staff_names(assignees)}"]
        elif kind in ('TICKET_COMMENT', 'UPDATE_TICKET'):
            text = action.get('text') or f"status -> {action.get('status')}"
            if ref and ref.issue and ref.issue.ticket_ref:
                lines = [f"💬 Comment on ClickUp task t-{ref.issue.id} \"{ref.issue.ticket_title or ref.summary}\" → {ref.issue.ticket_ref}"]
                lines += _details(ref.issue.ticket_ref, cache)
                lines.append(f"   comment: \"{str(text)[:400]}\"")
            else:
                lines = [f"📝 Internal note{about} (no ClickUp task to comment on): {str(text)[:400]}"]
        elif kind == 'SCHEDULE_FOLLOWUP':
            priority = ref.priority if ref else 'routine'
            due, _ = policy.due_at(action.get('kind') or 'staff_reminder', priority)
            lines = [f"⏰ Reminder ({action.get('kind')}){about}: {action.get('reason') or ''} - would fire about "
                     f"{due.astimezone(policy._tz()).strftime('%a %H:%M')} (counted from when it is applied)"]
        elif kind == 'CANCEL_FOLLOWUP':
            lines = [f"⏹ Cancel reminder {action.get('followup_id')}" + (f": {action.get('reason')}" if action.get('reason') else "")]
        elif kind == 'CASE_NOTE':
            lines = [f"📝 Internal note{about}: {str(action.get('text') or '')[:400]}"]
        elif kind == 'KB_UPDATE':
            try:
                plan = knowledge.plan_kb_update(ctx, action, ctx.staff_in_trigger or bool(action.get('approved_by')))
            except agent_actions.ActionError as e:
                plan, error = None, str(e)
            if plan and plan['already']:
                items.append({'idx': idx, 'n': None, 'kind': 'info', 'error': None,
                              'lines': [f"📚 Already in {plan['where']}: {plan['text'][:160]} - nothing to change"]})
                continue
            if plan:
                who = f" · {plan['source']}" if plan.get('source') else ''
                lines = [f"📚 Update {plan['where']}: {plan['text'][:400]}{who}"]
                if plan['replaces']:
                    lines.append(f"   replacing: {plan['replaces'][:200]}")
                if plan['approved_by']:
                    lines.append(f"   → approved by {plan['approved_by']}")
                elif plan['needs_approval']:
                    lines.append("   → FROM A TENANT and about a code / password / wifi: NOT saved unless a manager "
                                 "replies \"approve N\"")
                elif plan['from_tenant']:
                    lines.append("   → from the tenant (about this apartment) - saved unless you remove it")
            else:
                lines = [f"📚 Update the knowledge base: {str(action.get('text') or action.get('value') or '')[:200]}"]
        number += 1
        if error:
            lines.append(f"   ✗ will be SKIPPED: {error}")
        items.append({'idx': idx, 'n': number, 'kind': 'change', 'lines': lines, 'error': error,
                      'removed_by': action.get('removed_by'), 'changed_by': action.get('changed_by')})
    return items, actions


def has_changes(items):
    return any(item['kind'] == 'change' and not item.get('removed_by') for item in items or [])


def as_results(items, actions):
    """What the run stores in AIRun.actions before the plan is applied."""
    results = []
    for item in items:
        detail = " ".join(item['lines'])[:600]
        status = 'rejected' if item['kind'] == 'info' and item.get('error') else 'planned'
        if item['kind'] == 'team':
            status, detail = 'executed', 'team notification - the Telegram alert itself'
        results.append({'action': actions[item['idx']], 'status': status,
                        'detail': (f"plan #{item['n']}: " if item['n'] else '') + detail})
    return results


def render(items, removed_note=True):
    """Plan lines for Telegram: numbered changes (removed ones struck out as text)."""
    lines = []
    for item in items:
        if item['kind'] != 'change':
            continue
        first, rest = item['lines'][0], item['lines'][1:]
        if item.get('removed_by'):
            if removed_note:
                lines.append(f"{item['n']}. ❌ REMOVED by {item['removed_by']}: {first}")
            continue
        edited = f" (changed by {item['changed_by']})" if item.get('changed_by') else ""
        lines.append(f"{item['n']}. {first}{edited}")
        lines += rest
    return lines


def team_lines(items):
    return [line for item in items if item['kind'] == 'team' for line in item['lines']]


def info_lines(items):
    return [line for item in items if item['kind'] == 'info' for line in item['lines']]


def top_priority(items, actions):
    rank = {'routine': 0, 'urgent': 1, 'emergency': 2}
    best = None
    for item in items:
        priority = actions[item['idx']].get('priority') if isinstance(actions[item['idx']], dict) else None
        if priority in rank and (best is None or rank[priority] > rank[best]):
            best = priority
    return best
