"""
Case tracking (client spec v4, user decisions 2026-09-30; kept for the simple alerts), on top of AIIssue:

- stage: reported -> acknowledged -> owner_accepted -> answered -> resolved (only moves forward). "The tenant got
  a reply" is not "staff took it" is not "the tenant got the answer" is not "fixed".
- tenant_deadline + next_action from the AI's triage; reminders 24 h and 2 h before the deadline (Kevin at 2 h).
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
    if not (issue.tenant_deadline and issue.is_open):
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
        if issue.is_open:
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
