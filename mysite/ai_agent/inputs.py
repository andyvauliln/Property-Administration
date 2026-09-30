"""Builds the user-side input for one agent run (the INPUTS block of the system prompt)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db.models import Q

from mysite.ai_agent import config, knowledge

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
        CLIENT_SUFFIX, KB_SUFFIX, MANAGER_PHONE_NAMES,
        TWILIO_ASSISTANT_PHONE, _extract_marked_body, get_manager_phones,
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
    if author in get_manager_phones():
        return ROLE_STAFF, MANAGER_PHONE_NAMES.get(author, f'Manager {author[-4:]}')
    if body in ai_answers or author == 'Virtual Assistant':
        return ROLE_AI, config.ASSISTANT_NAME
    if author == 'ASSISTANT':
        return ROLE_STAFF, 'Manager (CRM)'
    if author == TWILIO_ASSISTANT_PHONE:
        return ROLE_STAFF, 'Automated CRM message'
    return ROLE_TENANT, 'Tenant'


def _ai_answers(conversation_sids):
    from mysite.models import TwilioMessage
    if isinstance(conversation_sids, str):
        conversation_sids = [conversation_sids]
    return frozenset(
        (a or '').strip()
        for a in TwilioMessage.objects
        .filter(conversation_sid__in=conversation_sids, ai_sent_to_chat=True)
        .exclude(ai_response__isnull=True)
        .values_list('ai_response', flat=True)
    )


def format_message_line(message, ai_answers=frozenset(), tenant_name=None, other_chats=None):
    """other_chats: {conversation_sid: chat id} of the tenant's OTHER chats - their lines get an [other chat #N] mark."""
    role, sender = classify_sender(message, ai_answers)
    if role == ROLE_TENANT and tenant_name and sender == 'Tenant':
        sender = tenant_name
    ts = message.message_timestamp.astimezone(_team_tz()).strftime('%Y-%m-%d %H:%M')
    where = (other_chats or {}).get(message.conversation_sid)
    mark = f"[other chat #{where}] " if where else ''
    photos = ''.join(f" [photo #{m.id}]" for m in message.media.all())
    return f"[{ts}] {mark}{sender} ({role}): {_strip_ui_markers(message.body)}{photos}".rstrip()


def collect_agent_images(new_messages, history, limit=None):
    """
    Photo media the model gets to see: the new messages' photos first, then the newest photos of the
    recent history. Returns a list of TwilioMessageMedia (downloaded images only).
    """
    from mysite.twilio_media import MODEL_MAX_IMAGES
    limit = MODEL_MAX_IMAGES if limit is None else limit
    picked = []
    for message in [*new_messages, *reversed(history)]:
        for media in message.media.all():
            if media.is_image and media.is_downloaded and len(picked) < limit:
                picked.append(media)
    return picked


def tenant_chats_block(conversation_sid, sources=None):
    """
    When the tenant has several chats (conversation_groups): explains the merged history. Returns
    (block or None, {sid: id} of the other chats, list of all sids to read history from).
    """
    from mysite import conversation_groups
    group = conversation_groups.group_for(conversation_sid, fresh=True)
    if not group:
        return None, {}, [conversation_sid]
    here = next(r for r in group['conversations'] if r['sid'] == conversation_sid)
    others = {r['sid']: r['id'] for r in group['conversations'] if r['sid'] != conversation_sid}
    lines = [f"- chat #{r['id']}" + (f" ({r['apartment']})" if r['apartment'] else '')
             + (" = THIS chat" if r['sid'] == conversation_sid else '')
             + (" = MAIN chat (the tenant wrote there last)" if r['is_main'] else '')
             for r in group['conversations']]
    if sources is not None:
        sources['tenant_chats'] = [r['id'] for r in group['conversations']]
    block = (
        f"TENANT_CHATS: this tenant has {len(group['conversations'])} separate group chats with us (separate SMS "
        f"threads on their phone). The chat history below is MERGED from all of them in time order; lines marked "
        f"[other chat #N] were written in another chat, so people in this chat may not have seen them. You are "
        f"answering in chat #{here['id']} (where the new message arrived) - your answer is sent to this chat only.\n"
        + "\n".join(lines)
    )
    return block, others, group['sids']


def staff_block():
    """STAFF metadata for the AI: who is who. Names in chat history match ai_name."""
    from mysite.models import StaffMember
    members = StaffMember.objects.filter(is_active=True)
    if not members.exists():
        return "STAFF: not configured (use Edy / Kevin / Janna as in your instructions)"
    lines = []
    for m in members:
        also = ", ".join(config.STAFF_ALSO.get(m.ai_name, ()))
        lines.append(f"- {m.ai_name}: {m.get_role_display()}" + (f" (also {also}: same person / same authority)" if also else ""))
    return "STAFF (authorized, role from metadata):\n" + "\n".join(lines)


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
    new_messages = []
    tenant_name = getattr(getattr(booking, 'tenant', None), 'full_name', None)
    chats_block, other_chats, chat_sids = tenant_chats_block(conversation_sid)
    ai_answers = _ai_answers(chat_sids)

    now = now.astimezone(_team_tz()) if now else datetime.now(_team_tz())
    context, sources = build_full_context(
        conversation_sid, apartment, booking, history_before=first, include_history=False,
        now=now.replace(tzinfo=None),
    )
    if other_chats:
        sources['tenant_chats'] = chat_sids
    context = context.replace("(as given by caller)", f"({config.TEAM_TIMEZONE})")
    context = context.replace("=== BOOKING PAYMENTS ===", "=== PAYMENT_RECORDS (every payment row the CRM has for this booking) ===")
    if not sources.get('payments'):
        context += "\n\n=== PAYMENT_RECORDS ===\nnone on file for this booking - do not state any payment status, route payment questions to Janna"

    # Code-level guard: access codes only from 24h before check-in until checkout
    codes_allowed = knowledge.access_codes_allowed(booking, now)
    if not codes_allowed:
        context, hidden_lines = knowledge.redact_access_codes(context)
        sources['kb_text_code_lines_hidden'] = hidden_lines
    sources['access_codes_allowed'] = codes_allowed
    access_line = (
        "ACCESS_CODES: allowed now (inside the window: 24h before check-in until checkout)" if codes_allowed else
        "ACCESS_CODES: NOT allowed now (outside the window) - codes are hidden from you; if asked, say they are "
        "shared closer to check-in, and route to Edy when the tenant needs access earlier"
    )

    parts = [
        f"EVENT: {event_type}",
        f"CURRENT_TIME: {now.strftime('%A %Y-%m-%d %H:%M')} ({config.TEAM_TIMEZONE} = US Eastern Time, Florida)",
        f"TEAM_TIMEZONE: {config.TEAM_TIMEZONE}",
        f"TENANT_TIMEZONE: {config.PROPERTY_TIMEZONE} (the property is in Florida; all times in this input are in this zone)",
        "IS_HOLIDAY: not provided",
        f"APARTMENT_NAME: {getattr(apartment, 'name', '')} (use this name for the unit in alerts, tickets and notes)",
        staff_block(),
        tracking_block(conversation_sid, booking, sources),
        chats_block,
        "RECENT_CLICKUP_HISTORY: [] (ClickUp is not connected yet)",
        extra_block,
        access_line,
        context,
    ]

    # 'KB-UPDATE-...' rows are CRM-only notes of the old knowledge extractor (never sent to anyone): not chat
    # A tenant with several chats: the history is merged from all of them (tenant_chats_block)
    all_messages = TwilioMessage.objects.filter(conversation_sid__in=chat_sids).exclude(message_sid__startswith='KB-UPDATE-')
    history_qs = _before(all_messages, first) if first else all_messages
    history = list(history_qs.prefetch_related('media').order_by('-message_timestamp', '-id')[:HISTORY_LIMIT])
    history.reverse()
    sources['agent_history_messages'] = len(history)
    if history:
        parts.append(
            "=== RECENT_CHAT_HISTORY (oldest first; role comes from metadata) ===\n"
            + "\n".join(format_message_line(m, ai_answers, tenant_name, other_chats) for m in history)
        )
    else:
        parts.append("=== RECENT_CHAT_HISTORY ===\n(no earlier messages)")

    if first and last:
        new_qs = _until(all_messages, last).exclude(id__in=[m.id for m in history])
        new_qs = new_qs.exclude(id__in=_before(all_messages, first).values('id'))
        new_messages = list(new_qs.prefetch_related('media').order_by('message_timestamp', 'id'))
        new_lines = [format_message_line(m, ai_answers, tenant_name, other_chats) for m in new_messages]
    elif body_override:
        new_lines = [f"[now] {tenant_name or 'Tenant'} ({ROLE_TENANT}): {body_override}"]
    else:
        new_lines = ["(no new chat message - this run was started by the event above)"]
    sources['agent_new_messages'] = len(new_lines)
    images = collect_agent_images(new_messages, history)
    sources['agent_images'] = [m.id for m in images]
    if images:
        parts.append(
            "=== PHOTOS ===\nThe images attached to this input, in this order: "
            + ", ".join(f"photo #{m.id}" for m in images)
            + ". Each [photo #N] in the chat lines above is one of them (older photos may not be attached)."
        )
    parts.append("=== NEW MESSAGE(S) TO HANDLE NOW ===\n" + "\n".join(new_lines))

    return "\n\n".join(p for p in parts if p), sources
