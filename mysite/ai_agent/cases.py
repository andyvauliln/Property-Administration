"""
Case tracking of the client spec v4 (user decisions 2026-09-30), on top of AIIssue:

- stage: reported -> acknowledged -> owner_accepted -> answered -> resolved (only moves forward). "The tenant got
  a reply" is not "staff took it" is not "the tenant got the answer" is not "fixed".
- tenant_deadline + next_action from the AI's triage; reminders 24 h and 2 h before the deadline (Kevin at 2 h).
- "I'll handle": the AI stays out of the issue (no drafts, reminders, ClickUp changes) until "Give back to AI" or
  the issue is closed. Enforced here, not only in the prompt.
- One Telegram thread per issue: its first card; later cards about it reply to that card.
- How often the tenant asked about it (repeat-question block on the card).
"""
import re

from django.utils import timezone

from mysite.unified_logger import log_info

_ISSUE_REF = re.compile(r"^[it]-(\d+)$")

CASE_STATUS_STAGE = {'ACKNOWLEDGED': 'acknowledged', 'OWNER_ACCEPTED': 'owner_accepted', 'ANSWERED': 'answered'}


def _issue_model():
    from mysite.models import AIIssue
    return AIIssue


def _group_sids(conversation_sid):
    from mysite import conversation_groups
    return conversation_groups.group_sids(conversation_sid)


def existing_issues(conversation_sid, refs):
    """AIIssue rows of this tenant's chats named by i-N / t-N in refs (new-N and unknown ids are skipped)."""
    ids = [int(m.group(1)) for m in (_ISSUE_REF.match(str(r).strip()) for r in refs or []) if m]
    if not ids:
        return []
    return list(_issue_model().objects.filter(id__in=ids, conversation_sid__in=_group_sids(conversation_sid)))


def run_issues(run):
    """Every issue a run is about: triage refs, issues its plan created, and issues its actions named."""
    AIIssue = _issue_model()
    refs = list((run.triage or {}).get('issue_refs') or [])
    plan = (run.review or {}).get('plan') or {}
    for action in plan.get('actions') or []:
        if isinstance(action, dict):
            refs += [str(action.get(k)) for k in ('issue_id', 'ticket_id') if action.get(k)]
    for item in run.actions or []:
        action = item.get('action') if isinstance(item, dict) else None
        if isinstance(action, dict):
            refs += [str(action.get(k)) for k in ('issue_id', 'ticket_id') if action.get(k)]
    issues = {i.id: i for i in existing_issues(run.conversation_sid, refs)}
    temp_map = plan.get('temp_map') or {}
    for issue in AIIssue.objects.filter(id__in=list(temp_map.values())):
        issues[issue.id] = issue
    for issue in AIIssue.objects.filter(created_by_run=run):
        issues[issue.id] = issue
    return sorted(issues.values(), key=lambda i: i.id)


# ---------------------------------------------------------------------------
# "I'll handle"
# ---------------------------------------------------------------------------

def handled_ids(conversation_sid):
    """{public id} of this tenant's open issues that staff took over with "I'll handle"."""
    AIIssue = _issue_model()
    rows = AIIssue.objects.filter(conversation_sid__in=_group_sids(conversation_sid)).exclude(
        state=AIIssue.STATE_RESOLVED).exclude(handled_by__isnull=True).exclude(handled_by='')
    return {i.public_id for i in rows}


def _mentions(action, ids):
    return any(str(action.get(k) or '').strip().replace('t-', 'i-', 1) in ids for k in ('issue_id', 'ticket_id'))


def enforce_handled(parsed, conversation_sid):
    """
    Staff took some issues over: the AI's actions on them are dropped, and when the event is only about such issues
    the answer is dropped too (the card becomes information only). Returns the list of handled ids involved (or []).
    """
    ids = handled_ids(conversation_sid)
    if not (parsed and ids):
        return []
    actions = parsed.get('actions') or []
    dropped = [a for a in actions if isinstance(a, dict) and _mentions(a, ids)]
    parsed['actions'] = [a for a in actions if not (isinstance(a, dict) and _mentions(a, ids))]
    refs = [str(r).strip().replace('t-', 'i-', 1) for r in (parsed.get('triage') or {}).get('issue_refs') or []]
    involved = sorted({r for r in refs if r in ids} | {str(a.get('issue_id') or a.get('ticket_id')).replace('t-', 'i-', 1)
                                                        for a in dropped})
    only_handled = bool(refs) and all(r in ids for r in refs)
    if only_handled and parsed.get('answer'):
        parsed['review_answer'] = parsed['answer']   # kept for managers, never sent
        parsed['answer'], parsed['no_answer'] = None, True
    if dropped or only_handled:
        parsed['handled_info'] = {'issues': involved, 'dropped_actions': len(dropped), 'answer_dropped': only_handled}
    return involved


def take_over(issues, author):
    """ "I'll handle": marks the issues, stops their reminders. Returns the number of reminders stopped."""
    from mysite.models import AICaseNote, AIFollowUp
    AIIssue = _issue_model()
    stopped = 0
    for issue in issues:
        if not issue.is_open:
            continue
        issue.handled_prev_state = issue.handled_prev_state or issue.state
        issue.handled_by, issue.handled_at = author, timezone.now()
        issue.state = AIIssue.STATE_STAFF_HANDLING
        issue.reach_stage(AIIssue.STAGE_OWNER_ACCEPTED)
        issue.save()
        stopped += issue.followups.filter(status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_CANCELLED, status_note=f"{author} handles it (I'll handle)"[:255], updated_at=timezone.now())
        AICaseNote.objects.create(conversation_sid=issue.conversation_sid, booking=issue.booking, issue=issue,
                                  text=f"{author} pressed \"I'll handle\" in Telegram: staff handle this issue, the AI stays out.")
    return stopped


def give_back(issue, author):
    from mysite.models import AICaseNote
    AIIssue = _issue_model()
    if not issue.handled_by:
        return False
    issue.state = issue.handled_prev_state or AIIssue.STATE_WAITING_FOR_EDY
    issue.handled_by = issue.handled_at = issue.handled_prev_state = None
    issue.save()
    schedule_deadline_reminders(issue)
    AICaseNote.objects.create(conversation_sid=issue.conversation_sid, booking=issue.booking, issue=issue,
                              text=f"{author} gave this issue back to the AI.")
    return True


# ---------------------------------------------------------------------------
# Stages, deadlines, repeat questions, threads
# ---------------------------------------------------------------------------

def _et(value):
    from zoneinfo import ZoneInfo

    from mysite.ai_agent import config
    return f"{value.astimezone(ZoneInfo(config.TEAM_TIMEZONE)):%a %Y-%m-%d %H:%M} {config.TIMEZONE_LABEL}"


def schedule_deadline_reminders(issue, run=None):
    """(Re)creates the reminders before the tenant deadline. Returns how many were created."""
    from mysite.ai_agent import policy
    from mysite.models import AIFollowUp
    issue.followups.filter(kind=AIFollowUp.KIND_DEADLINE_REMINDER, status=AIFollowUp.STATUS_PENDING).update(
        status=AIFollowUp.STATUS_CANCELLED, status_note='deadline changed', updated_at=timezone.now())
    if not (issue.tenant_deadline and issue.is_open and not issue.handled_by):
        return 0
    created = 0
    for due, label, reason in policy.deadline_reminders(issue.tenant_deadline):
        AIFollowUp.objects.create(
            conversation_sid=issue.conversation_sid, issue=issue, kind=AIFollowUp.KIND_DEADLINE_REMINDER, due_at=due,
            reason=f"[{label}] {reason}. Tenant deadline {_et(issue.tenant_deadline)} for \"{issue.summary}\""
                   + (f"; next action: {issue.next_action}" if issue.next_action else ""),
            created_by_run=run,
        )
        created += 1
    return created


def apply_triage(issue, triage, run=None, tenant_event=False, save=True):
    """Deadline / next action / tenant asks from the AI's triage of an event about this issue."""
    from mysite.ai_agent import service
    changed_deadline = False
    if triage.get('next_action'):
        issue.next_action = triage['next_action'][:500]
    deadline = service.parse_tenant_deadline(triage.get('tenant_deadline'))
    if deadline and deadline != issue.tenant_deadline:
        issue.tenant_deadline, changed_deadline = deadline, True
    if tenant_event:
        issue.tenant_asks += 1
        issue.last_tenant_ask_at = timezone.now()
    if save:
        issue.save()
    if changed_deadline:
        schedule_deadline_reminders(issue, run)
    return changed_deadline


def note_run(ai_run, parsed, tenant_event):
    """After a run: existing issues it is about get the triage (deadline, next action, tenant asked again)."""
    triage = (parsed or {}).get('triage') or {}
    for issue in existing_issues(ai_run.conversation_sid, triage.get('issue_refs')):
        if issue.is_open and not issue.handled_by:
            apply_triage(issue, triage, ai_run, tenant_event)
        elif tenant_event:
            issue.tenant_asks += 1
            issue.last_tenant_ask_at = timezone.now()
            issue.save()


def after_apply(run, temp_map, tenant_event):
    """After a run's actions were carried out: new issues get the triage and the run's card as their thread."""
    AIIssue = _issue_model()
    triage = run.triage or {}
    for issue in AIIssue.objects.filter(id__in=list((temp_map or {}).values())):
        apply_triage(issue, triage, run, tenant_event and issue.tenant_asks == 0, save=False)
        if run.telegram_message_id and not issue.telegram_thread_message_id:
            issue.telegram_thread_message_id = run.telegram_message_id
        issue.save()
    for issue in run_issues(run):
        if run.telegram_message_id and not issue.telegram_thread_message_id:
            AIIssue.objects.filter(id=issue.id, telegram_thread_message_id__isnull=True).update(
                telegram_thread_message_id=run.telegram_message_id)


def mark_answer_sent(run, text_status='sent'):
    """The tenant got the run's answer (or, test mode, would have): the stage its triage says, at least acknowledged."""
    AIIssue = _issue_model()
    stage = CASE_STATUS_STAGE.get((run.triage or {}).get('case_status') or '', AIIssue.STAGE_ACKNOWLEDGED)
    for issue in run_issues(run):
        changed = issue.reach_stage(AIIssue.STAGE_ACKNOWLEDGED)
        changed = issue.reach_stage(stage) or changed
        if changed:
            issue.save()
            log_info(f"AI issue {issue.public_id}: stage {issue.stage} ({text_status}, run #{run.id})", category='sms')


def mark_acknowledged(conversation_sid, issues):
    AIIssue = _issue_model()
    for issue in issues:
        if issue.reach_stage(AIIssue.STAGE_ACKNOWLEDGED):
            issue.save()


def thread_for(conversation_sid, refs):
    """Telegram message id of the first card of the issue(s) this event is about (reply to it), or None."""
    for issue in existing_issues(conversation_sid, refs):
        if issue.telegram_thread_message_id:
            return issue.telegram_thread_message_id
    return None


def repeat_lines(conversation_sid, refs):
    """For the card: issues the tenant asked about more than once, with waiting time and owner."""
    from mysite.ai_agent.actions import staff_label
    lines = []
    now = timezone.now()
    for issue in existing_issues(conversation_sid, refs):
        if issue.tenant_asks < 2 or not issue.is_open:
            continue
        hours = (now - issue.created_at).total_seconds() / 3600
        waiting = f"{hours:.0f} h" if hours >= 1 else f"{hours * 60:.0f} min"
        lines.append(f"🔁 {issue.public_id} \"{issue.summary[:80]}\": tenant asked {issue.tenant_asks} times, waiting {waiting}, "
                     f"owner {staff_label(issue.owner) or 'nobody'}, stage {issue.stage}"
                     + (f", next: {issue.next_action}" if issue.next_action else ", next decision: not set"))
    return lines
