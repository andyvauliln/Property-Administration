"""
Executes the [ACTIONS] the AI returned. The AI decides WHAT, this file decides HOW.

Internal state (issues, follow-ups, case notes) is saved in test mode too, so the AI sees its own
open issues on the next message and the whole workflow can be reviewed before going live.
Only tenant-facing sending is gated by test/live, and that happens in service.deliver().
"""
import re

from django.utils import timezone

from mysite.ai_agent import config, policy

ACTION_TYPES = frozenset([
    'CREATE_ISSUE', 'UPDATE_ISSUE_STATE', 'CREATE_TICKET', 'TICKET_COMMENT', 'UPDATE_TICKET',
    'INTERNAL_ALERT', 'QUEUE_FOR_REVIEW', 'SCHEDULE_FOLLOWUP', 'CANCEL_FOLLOWUP', 'KB_UPDATE', 'CASE_NOTE',
])
# Actions staff must actually see
NOTIFY_ACTION_TYPES = frozenset(['INTERNAL_ALERT', 'QUEUE_FOR_REVIEW', 'CREATE_TICKET'])
# Actions that make "I've logged it / passed it to the team" true
# A comment on an existing ticket also counts: the team already tracks the matter there
LOGGING_ACTION_TYPES = NOTIFY_ACTION_TYPES | {'CREATE_ISSUE', 'TICKET_COMMENT', 'UPDATE_TICKET'}
_PROMISE = re.compile(r"\b(logged|passed (it|this|that|your message) (on )?to|reported this|let the team know)\b", re.I)

STATUS_EXECUTED = 'executed'
STATUS_SIMULATED = 'simulated'
STATUS_REJECTED = 'rejected'


class ActionError(Exception):
    """Raised by a handler; the action is stored as rejected with this text."""


class ActionContext:
    def __init__(self, mode, meta, new_messages_text, conversation_sid, apartment=None, booking=None,
                 ai_run=None, persist=True, notify=True, staff_in_trigger=False):
        self.mode = mode
        self.meta = meta
        self.new_messages_text = new_messages_text
        self.conversation_sid = conversation_sid
        self.apartment = apartment
        self.booking = booking
        self.ai_run = ai_run
        self.persist = persist      # False: replay, nothing is written
        self.notify = notify        # False: replay, nobody is notified
        self.staff_in_trigger = staff_in_trigger  # an authorized STAFF message started this run
        self.temp_ids = {}          # "new-1" -> AIIssue
        self.alerts = []            # [{'action', 'issue'}] collected for team_notify.deliver()
        self.ticket_updates = []    # [{'issue', 'text', 'clickup'}] comments / closes on existing tickets

    @property
    def is_test(self):
        return self.mode != 'live'


def _ok(detail):
    return STATUS_EXECUTED, detail


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _issue(ctx, issue_id, required=True):
    from mysite.models import AIIssue

    issue_id = str(issue_id or '').strip()
    if not issue_id or issue_id.lower() in ('null', 'none'):
        if required:
            raise ActionError("issue_id is missing")
        return None
    if issue_id in ctx.temp_ids:
        return ctx.temp_ids[issue_id]
    pending = re.fullmatch(r"r(\d+):(new-\d+)", issue_id)
    if pending:
        # An issue that an earlier staff-review plan of this chat creates (plan.py); applied first when still waiting
        from mysite.ai_agent import answer_review
        issue = answer_review.issue_from_earlier_plan(int(pending.group(1)), pending.group(2), ctx.conversation_sid)
        if issue is None:
            raise ActionError(f"{issue_id}: that earlier plan was cancelled or did not create this issue")
        return issue
    # "t-41" is the ticket of issue i-41 (tickets become ClickUp tasks in Phase 4)
    match = re.fullmatch(r"[it]-(\d+)", issue_id)
    issue = AIIssue.objects.filter(id=int(match.group(1)), conversation_sid=ctx.conversation_sid).first() if match else None
    if issue is None:
        raise ActionError(f"issue {issue_id} does not exist in this conversation")
    return issue


def staff_label(names):
    """['Edy'] -> 'Edy (Farouk Ahmed)'. Unknown names are kept as they are."""
    from mysite.models import StaffMember

    if isinstance(names, str):
        names = [names]
    labels = []
    for name in names or []:
        member = StaffMember.objects.filter(ai_name__iexact=str(name).strip(), is_active=True).first()
        also = ", also " + ", ".join(config.STAFF_ALSO.get(member.ai_name, ())) if member and config.STAFF_ALSO.get(member.ai_name) else ""
        if member and member.full_name:
            labels.append(f"{member.ai_name} ({member.full_name}{also})")
        elif member:
            labels.append(f"{member.ai_name} (role not assigned to a person yet)")
        else:
            labels.append(str(name))
    return ", ".join(labels)


# ---------------------------------------------------------------------------
# Handlers: each returns (status, detail)
# ---------------------------------------------------------------------------

def _create_issue(ctx, action):
    from mysite.models import AIIssue

    summary = str(action.get('summary') or '').strip()
    if not summary:
        raise ActionError("summary is missing")
    state = action.get('state') or AIIssue.STATE_WAITING_FOR_EDY
    if state not in dict(AIIssue.STATE_CHOICES):
        raise ActionError(f"unknown state {state}")

    duplicate = AIIssue.objects.filter(
        conversation_sid=ctx.conversation_sid, summary__iexact=summary,
    ).exclude(state=AIIssue.STATE_RESOLVED).first()
    issue = duplicate or AIIssue.objects.create(
        conversation_sid=ctx.conversation_sid, apartment=ctx.apartment, booking=ctx.booking,
        summary=summary[:500], owner=(action.get('owner') or '')[:50] or None, state=state,
        mode=ctx.mode, created_by_run=ctx.ai_run,
    )
    if action.get('temp_id'):
        ctx.temp_ids[str(action['temp_id'])] = issue
    if duplicate:
        return _ok(f"same open issue already exists: {issue.public_id}")
    return _ok(f"created {issue.public_id}")


def _update_issue_state(ctx, action):
    from mysite.models import AIFollowUp, AIIssue

    issue = _issue(ctx, action.get('issue_id'))
    state = action.get('state')
    if state not in dict(AIIssue.STATE_CHOICES):
        raise ActionError(f"unknown state {state}")
    old_state = issue.state
    issue.state = state
    issue.resolved_at = timezone.now() if state == AIIssue.STATE_RESOLVED else None
    if state == AIIssue.STATE_RESOLVED:
        issue.reach_stage(AIIssue.STAGE_RESOLVED)
        issue.handled_by = issue.handled_at = issue.handled_prev_state = None
    issue.save()
    detail = f"{issue.public_id}: {old_state} -> {state}"
    if state == AIIssue.STATE_RESOLVED:
        cancelled = issue.followups.filter(status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_CANCELLED, status_note='issue resolved', updated_at=timezone.now(),
        )
        if cancelled:
            detail += f", {cancelled} pending follow-up(s) cancelled"
        if old_state != AIIssue.STATE_RESOLVED and issue.ticket_ref:
            detail += "; " + _close_ticket(ctx, issue)
    return _ok(detail)


def _close_ticket(ctx, issue):
    """The AI resolved an issue that has a ClickUp task: close the task too (planned during the staff review)."""
    from mysite.ai_agent import clickup

    if not (ctx.notify and ctx.persist and clickup.delivery_mode() == 'api'):
        return "ClickUp task left as it is (no API token / replay)"
    if not clickup.writes_enabled(issue.apartment or ctx.apartment):
        result = 'ClickUp writes OFF - task NOT closed in ClickUp (would close it)'
        ctx.ticket_updates.append({'issue': issue, 'text': '', 'clickup': result})
        return result
    try:
        status = clickup.set_task_closed(issue.ticket_ref)
        ctx.ticket_updates.append({'issue': issue, 'text': '', 'clickup': f'task CLOSED (status: {status})'})
        return f"ClickUp task closed (status: {status})"
    except clickup.ClickUpError as e:
        return f"ClickUp task close FAILED ({str(e)[:150]})"


def _schedule_followup(ctx, action):
    from mysite.models import AIFollowUp

    kind = action.get('kind')
    if kind not in dict(AIFollowUp.KIND_CHOICES) or kind == AIFollowUp.KIND_DEADLINE_REMINDER:
        raise ActionError(f"unknown follow-up kind {kind}" + (" - deadline reminders are set by the backend from "
                                                               "tenant_deadline" if kind == AIFollowUp.KIND_DEADLINE_REMINDER else ""))
    issue = _issue(ctx, action.get('issue_id'), required=False)
    reason = str(action.get('reason') or '').strip()

    if issue is not None:
        if issue.handled_by and issue.is_open:
            raise ActionError(f"{issue.public_id} is handled by staff ({issue.handled_by} pressed \"I'll handle\") - no AI reminders")
        if issue.followups.exclude(kind=AIFollowUp.KIND_DEADLINE_REMINDER).count() >= policy.MAX_FOLLOWUPS_PER_ISSUE:
            raise ActionError(f"{issue.public_id} reached the limit of {policy.MAX_FOLLOWUPS_PER_ISSUE} follow-ups")
        pending = issue.followups.filter(kind=kind, status=AIFollowUp.STATUS_PENDING).first()
        if pending:
            return _ok(f"{pending.public_id} ({kind}) is already pending for {issue.public_id}, due {pending.due_at:%Y-%m-%d %H:%M %Z}")

    due, note = policy.due_at(kind, issue.priority if issue else 'routine')
    followup = AIFollowUp.objects.create(
        conversation_sid=ctx.conversation_sid, issue=issue, kind=kind, reason=reason,
        due_at=due, created_by_run=ctx.ai_run,
    )
    return _ok(f"created {followup.public_id}, due {due:%Y-%m-%d %H:%M %Z} ({note})")


def _cancel_followup(ctx, action):
    from mysite.models import AIFollowUp

    match = re.fullmatch(r"f-(\d+)", str(action.get('followup_id') or '').strip())
    followup = AIFollowUp.objects.filter(
        id=int(match.group(1)), conversation_sid=ctx.conversation_sid,
    ).first() if match else None
    if followup is None:
        raise ActionError(f"follow-up {action.get('followup_id')} does not exist in this conversation")
    if followup.status == AIFollowUp.STATUS_PENDING:
        followup.status = AIFollowUp.STATUS_CANCELLED
        followup.status_note = 'cancelled by AI'
        followup.save()
    return _ok(f"{followup.public_id} is {followup.status}")


def _case_note(ctx, action, prefix=''):
    from mysite.models import AICaseNote

    text = str(action.get('text') or '').strip()
    if not text:
        raise ActionError("text is missing")
    issue = None
    try:
        issue = _issue(ctx, action.get('issue_id') or action.get('ticket_id'), required=False)
    except ActionError:
        pass  # keep the note even when the AI referenced an issue that does not exist
    AICaseNote.objects.create(
        conversation_sid=ctx.conversation_sid, booking=ctx.booking, issue=issue,
        text=prefix + text, created_by_run=ctx.ai_run,
    )
    return _ok("case note saved" + (f" on {issue.public_id}" if issue else ""))


def _notify_action(ctx, action):
    """
    INTERNAL_ALERT / QUEUE_FOR_REVIEW / CREATE_TICKET: nothing is sent here. The alert is collected and
    team_notify.deliver() sends ONE grouped message per run (Telegram, and ClickUp for mapped apartments).
    """
    issue = _issue(ctx, action.get('issue_id'), required=False)
    priority = action.get('priority')
    if issue and priority in ('routine', 'urgent', 'emergency'):
        rank = ['routine', 'urgent', 'emergency']
        if rank.index(priority) > rank.index(issue.priority):
            issue.priority = priority
    if issue and action['type'] == 'CREATE_TICKET':
        issue.ticket_title = str(action.get('title') or '')[:255] or issue.ticket_title
    if issue:
        issue.save()
    ctx.alerts.append({'action': action, 'issue': issue})
    return _ok("included in this run's team notification" + (f", linked to {issue.public_id}" if issue else ""))


def _kb_update(ctx, action):
    from mysite.ai_agent import knowledge
    # A staff member approved or corrected this entry in the Telegram review: it counts as said by staff
    return _ok(knowledge.apply_kb_update(ctx, action, ctx.staff_in_trigger or bool(action.get('approved_by'))))


def _ticket_note(ctx, action):
    """Case note + (API token, not a replay) a real comment on the issue's ClickUp task."""
    from mysite.ai_agent import clickup

    text = action.get('text') or f"ticket status -> {action.get('status')}"
    status, detail = _case_note(ctx, {'issue_id': None, 'ticket_id': action.get('ticket_id'), 'text': text},
                                prefix=f"[{action['type']} {action.get('ticket_id') or ''}] ")
    try:
        issue = _issue(ctx, action.get('ticket_id'), required=False)
    except ActionError:
        issue = None
    if not issue:
        return status, detail
    result = ''
    if ctx.notify and ctx.persist and issue.ticket_ref and clickup.delivery_mode() == 'api' \
            and not clickup.writes_enabled(issue.apartment or ctx.apartment):
        result = 'ClickUp writes OFF - comment NOT posted in ClickUp (would add it)'
        detail += f"; {result}"
    elif ctx.notify and ctx.persist and issue.ticket_ref and clickup.delivery_mode() == 'api':
        try:
            clickup.add_task_comment(issue.ticket_ref, f"🤖 AI{' [TEST]' if ctx.is_test else ''}: {text}")
            result = 'comment added in ClickUp'
        except clickup.ClickUpError as e:
            result = f'ClickUp comment FAILED ({str(e)[:150]})'
        detail += f"; {result}"
    ctx.ticket_updates.append({'issue': issue, 'text': str(text), 'clickup': result})
    return status, detail


HANDLERS = {
    'CREATE_ISSUE': _create_issue,
    'UPDATE_ISSUE_STATE': _update_issue_state,
    'SCHEDULE_FOLLOWUP': _schedule_followup,
    'CANCEL_FOLLOWUP': _cancel_followup,
    'CASE_NOTE': _case_note,
    'INTERNAL_ALERT': _notify_action,
    'QUEUE_FOR_REVIEW': _notify_action,
    'CREATE_TICKET': _notify_action,
    'TICKET_COMMENT': _ticket_note,
    'UPDATE_TICKET': _ticket_note,
    'KB_UPDATE': _kb_update,
}


def with_safety_net(parsed, ctx):
    """
    The AI's actions, plus one INTERNAL_ALERT when the answer tells the tenant the team knows but the AI gave
    no action for it. Returns (actions, backend_added_or_None).
    """
    actions = list(parsed.get('actions') or [])
    answer = parsed.get('answer') or ''
    emitted = {a.get('type') for a in actions if isinstance(a, dict)}
    backend_added = None
    if answer and _PROMISE.search(answer) and not (emitted & LOGGING_ACTION_TYPES):
        # Attach it to the chat's newest open issue when there is one, so the alert shows that issue and its ticket.
        from mysite.models import AIIssue
        open_issue = AIIssue.objects.filter(conversation_sid=ctx.conversation_sid).exclude(
            state=AIIssue.STATE_RESOLVED).order_by('-id').first() if ctx.persist else None
        backend_added = {
            'type': 'INTERNAL_ALERT', 'priority': getattr(open_issue, 'priority', None) or 'routine',
            'responsible': [getattr(open_issue, 'owner', None) or 'Edy'],
            'text': "The AI told the tenant the team is aware, but gave no alert itself - please check.",
            'backend_added': True,
        }
        if open_issue:
            backend_added['issue_id'] = open_issue.public_id
        actions.append(backend_added)
    return actions, backend_added


def execute_actions(parsed, ctx):
    """Returns [{'action', 'status', 'detail'}] in the order the AI gave them."""
    actions, backend_added = with_safety_net(parsed, ctx)

    # Issues first so "new-1" exists, then alerts/tickets so the issue priority is known before
    # follow-up times are calculated - whatever order the AI used
    def _rank(action):
        kind = action.get('type') if isinstance(action, dict) else None
        return 0 if kind == 'CREATE_ISSUE' else 1 if kind in NOTIFY_ACTION_TYPES else 2

    ordered = sorted(actions, key=_rank)
    results = {}
    for action in ordered:
        note = ('added by backend: the answer promised escalation but no matching action was emitted. '
                if action is backend_added or (isinstance(action, dict) and action.get('backend_added')) else '')
        if not isinstance(action, dict) or action.get('type') not in ACTION_TYPES:
            status, detail = STATUS_REJECTED, 'unknown action type'
        elif not ctx.persist or (action['type'] in NOTIFY_ACTION_TYPES and not ctx.notify):
            status, detail = STATUS_SIMULATED, 'replay - nothing saved, nobody notified'
        else:
            try:
                status, detail = HANDLERS[action['type']](ctx, action)
            except ActionError as e:
                status, detail = STATUS_REJECTED, str(e)
        results[id(action)] = {'action': action, 'status': status, 'detail': note + detail}
    return [results[id(action)] for action in actions]
