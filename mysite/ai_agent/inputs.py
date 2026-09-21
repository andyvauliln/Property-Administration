"""Builds the user-side input for one agent run (the INPUTS block of the system prompt)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Q

from mysite.ai_agent import config

HISTORY_LIMIT = 20

ROLE_TENANT = 'TENANT'
ROLE_STAFF = 'STAFF'
ROLE_AI = 'AI'


def _team_tz():
    try:
        return ZoneInfo(config.TEAM_TIMEZONE)
    except Exception:
        return ZoneInfo('UTC')


def _strip_ui_markers(body):
    from mysite.views.messaging import CLIENT_SUFFIX, KB_SUFFIX, _extract_marked_body
    body = (body or '').strip()
    for marker in (CLIENT_SUFFIX, KB_SUFFIX):
        marked = _extract_marked_body(body, marker)
        if marked:
            return marked
    return body


_staff_cache = {'at': 0.0, 'names': {}}


def _staff_names():
    """phone -> ai_name from the StaffMember table, cached for 30 seconds."""
    import time

    from mysite.models import StaffMember
    if time.monotonic() - _staff_cache['at'] > 30:
        try:
            _staff_cache['names'] = {
                phone: m.ai_name for m in StaffMember.objects.filter(is_active=True) for phone in m.phones
            }
        except Exception:
            _staff_cache['names'] = {}
        _staff_cache['at'] = time.monotonic()
    return _staff_cache['names']


def classify_sender(message, ai_answers=frozenset()):
    """
    Returns (role, sender_name). Role comes from metadata only, never from message text.
    ai_answers: bodies of AI answers already given in this conversation.
    """
    from mysite.views.messaging import (
        CLIENT_SUFFIX, KB_SUFFIX, MANAGER_PHONES, MANAGER_PHONE_NAMES,
        TWILIO_ASSISTANT_PHONE, _extract_marked_body,
    )
    author = (message.author or '').strip()
    body = (message.body or '').strip()

    # Chat UI simulation markers: (+++) = written as the tenant, (+) = written as a manager
    if _extract_marked_body(body, CLIENT_SUFFIX):
        return ROLE_TENANT, 'Tenant (test message from CRM)'
    if _extract_marked_body(body, KB_SUFFIX):
        return ROLE_STAFF, 'Manager (CRM)'
    staff_name = _staff_names().get(author)
    if staff_name:
        return ROLE_STAFF, staff_name
    if author in MANAGER_PHONES:
        return ROLE_STAFF, MANAGER_PHONE_NAMES.get(author, f'Manager {author[-4:]}')
    if body in ai_answers or author == 'Virtual Assistant':
        return ROLE_AI, config.ASSISTANT_NAME
    if author == 'ASSISTANT':
        return ROLE_STAFF, 'Manager (CRM)'
    if author == TWILIO_ASSISTANT_PHONE:
        return ROLE_STAFF, 'Automated CRM message'
    return ROLE_TENANT, 'Tenant'


def _ai_answers(conversation_sid):
    from mysite.models import TwilioMessage
    return frozenset(
        (a or '').strip()
        for a in TwilioMessage.objects
        .filter(conversation_sid=conversation_sid, ai_sent_to_chat=True)
        .exclude(ai_response__isnull=True)
        .values_list('ai_response', flat=True)
    )


def format_message_line(message, ai_answers=frozenset(), tenant_name=None):
    role, sender = classify_sender(message, ai_answers)
    if role == ROLE_TENANT and tenant_name and sender == 'Tenant':
        sender = tenant_name
    ts = message.message_timestamp.astimezone(_team_tz()).strftime('%Y-%m-%d %H:%M')
    return f"[{ts}] {sender} ({role}): {_strip_ui_markers(message.body)}"


def staff_block():
    """STAFF metadata for the AI: who is who. Names in chat history match ai_name."""
    from mysite.models import StaffMember
    members = StaffMember.objects.filter(is_active=True)
    if not members.exists():
        return "STAFF: not configured (use Edy / Kevin / Janna as in your instructions)"
    return "STAFF (authorized, role from metadata):\n" + "\n".join(
        f"- {m.ai_name}: {m.get_role_display()}" for m in members
    )


def tracking_block(conversation_sid, booking, sources=None):
    """OPEN_ISSUES, OPEN_TICKETS, PENDING_FOLLOWUPS and CASE_NOTES of this conversation."""
    from mysite.models import AICaseNote, AIFollowUp, AIIssue

    tz = _team_tz()
    issues = list(AIIssue.objects.filter(conversation_sid=conversation_sid).exclude(state=AIIssue.STATE_RESOLVED).order_by('id'))
    followups = list(AIFollowUp.objects.filter(
        conversation_sid=conversation_sid, status=AIFollowUp.STATUS_PENDING,
    ).select_related('issue').order_by('due_at'))
    notes = list(AICaseNote.objects.filter(conversation_sid=conversation_sid).order_by('-id')[:10])
    notes.reverse()
    if sources is not None:
        sources.update({'open_issues': len(issues), 'pending_followups': len(followups), 'case_notes': len(notes)})

    def issue_line(i):
        opened = i.created_at.astimezone(tz).strftime('%Y-%m-%d %H:%M')
        return f"- issue_id: {i.public_id} | state: {i.state} | owner: {i.owner or '-'} | priority: {i.priority} | opened: {opened} | {i.summary}"

    tickets = [i for i in issues if i.ticket_title]
    return "\n".join([
        "OPEN_ISSUES:" + ("" if issues else " []"),
        *[issue_line(i) for i in issues],
        "OPEN_TICKETS:" + ("" if tickets else " []"),
        *[f"- ticket_id: t-{i.id} | issue_id: {i.public_id} | priority: {i.priority} | {i.ticket_title}" for i in tickets],
        "PENDING_FOLLOWUPS:" + ("" if followups else " []"),
        *[
            f"- followup_id: {f.public_id} | kind: {f.kind} | issue_id: {f.issue.public_id if f.issue else '-'} | "
            f"due: {f.due_at.astimezone(tz).strftime('%Y-%m-%d %H:%M')} | reason: {f.reason or '-'}"
            for f in followups
        ],
        "CASE_NOTES (one-off arrangements for this tenant/stay):" + ("" if notes else " []"),
        *[f"- [{n.created_at.astimezone(tz).strftime('%Y-%m-%d %H:%M')}] {n.text}" for n in notes],
    ])


def _before(qs, message):
    return qs.filter(
        Q(message_timestamp__lt=message.message_timestamp)
        | Q(message_timestamp=message.message_timestamp, id__lt=message.id)
    )


def _until(qs, message):
    return qs.filter(
        Q(message_timestamp__lt=message.message_timestamp)
        | Q(message_timestamp=message.message_timestamp, id__lte=message.id)
    )


def build_agent_input(event_type, conversation_sid, apartment, booking, trigger_messages, body_override=None, now=None,
                      extra_block=None):
    """
    trigger_messages: TwilioMessage list (oldest first) this run must react to.
    body_override: text to use when there is no stored message (manual replay).
    now: aware datetime treated as the current moment (replay uses the message time).
    extra_block: event-specific text, e.g. the follow-up that became due.
    Returns (input_text, sources) — sources is a dict describing what was included.
    """
    from mysite.models import TwilioMessage
    from mysite.views.messaging import build_full_context

    first = trigger_messages[0] if trigger_messages else None
    last = trigger_messages[-1] if trigger_messages else None
    tenant_name = getattr(getattr(booking, 'tenant', None), 'full_name', None)
    ai_answers = _ai_answers(conversation_sid)

    now = now.astimezone(_team_tz()) if now else datetime.now(_team_tz())
    context, sources = build_full_context(
        conversation_sid, apartment, booking, history_before=first, include_history=False,
        now=now.replace(tzinfo=None),
    )
    context = context.replace("(as given by caller)", f"({config.TEAM_TIMEZONE})")

    parts = [
        f"EVENT: {event_type}",
        f"CURRENT_TIME: {now.strftime('%A %Y-%m-%d %H:%M')} ({config.TEAM_TIMEZONE})",
        f"TEAM_TIMEZONE: {config.TEAM_TIMEZONE}",
        f"TENANT_TIMEZONE: not provided (assume {config.TEAM_TIMEZONE})",
        "IS_HOLIDAY: not provided",
        staff_block(),
        tracking_block(conversation_sid, booking, sources),
        "RECENT_CLICKUP_HISTORY: [] (ClickUp is not connected yet)",
        extra_block,
        context,
    ]

    all_messages = TwilioMessage.objects.filter(conversation_sid=conversation_sid)
    history_qs = _before(all_messages, first) if first else all_messages
    history = list(history_qs.order_by('-message_timestamp', '-id')[:HISTORY_LIMIT])
    history.reverse()
    sources['agent_history_messages'] = len(history)
    if history:
        parts.append(
            "=== RECENT_CHAT_HISTORY (oldest first; role comes from metadata) ===\n"
            + "\n".join(format_message_line(m, ai_answers, tenant_name) for m in history)
        )
    else:
        parts.append("=== RECENT_CHAT_HISTORY ===\n(no earlier messages)")

    if first and last:
        new_qs = _until(all_messages, last).exclude(id__in=[m.id for m in history])
        new_qs = new_qs.exclude(id__in=_before(all_messages, first).values('id'))
        new_lines = [
            format_message_line(m, ai_answers, tenant_name)
            for m in new_qs.order_by('message_timestamp', 'id')
        ]
    elif body_override:
        new_lines = [f"[now] {tenant_name or 'Tenant'} ({ROLE_TENANT}): {body_override}"]
    else:
        new_lines = ["(no new chat message - this run was started by the event above)"]
    sources['agent_new_messages'] = len(new_lines)
    parts.append("=== NEW MESSAGE(S) TO HANDLE NOW ===\n" + "\n".join(new_lines))

    return "\n\n".join(p for p in parts if p), sources
