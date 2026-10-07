"""
After-hours acknowledgment (client spec v4, user decisions 2026-09-30).

A tenant who writes outside office hours (Monday-Friday 09:00-18:00 ET; weekends and US federal holidays are outside)
gets one fixed SMS at once: "We received your message outside our regular office hours ...". It is the only tenant
message sent without staff approval.

- At most once per rolling 5 hours per tenant (all chats of a tenant count as one, conversation_groups). Later
  messages do not reset the timer; the time is saved only after Twilio accepted the message.
- Sent at any hour: it skips the 21:00-08:00 SMS hold (send_messsage_by_sid, not send_tenant_sms_gated).
- Not sent when staff wrote in the tenant's chat in the last 30 minutes (they are replying), or for a pure
  "thanks" / "ok".
- Test apartments: nothing is sent; the row says WOULD_SEND and the Telegram card shows "WOULD AUTO-SEND".
- It never replaces anything else: the AI run, the Telegram card, emergencies and approvals go on as usual.
- A failed send is retried once, 3 minutes after the failure alert.

The worker calls process_pending() every loop, so the message goes out within seconds, before the 1-minute burst wait
and independent of Claude. A tenant message containing URGENT (not "not urgent") asks calls.py to phone the on-call
person outside office hours.
"""
import re
from datetime import timedelta

from django.utils import timezone

from mysite.ai_agent import config
from mysite.ai_agent.notify import report_error
from mysite.unified_logger import log_info

# The client's exact text + the emergency line chosen by the user (2026-09-30). The live text is the AIManagement
# prompt 'ai_after_hours_ack'.
DEFAULT_ACK_TEXT = (
    "Automated message: We received your message outside our regular office hours. Our team is available Monday "
    "through Friday, 9:00 AM–6:00 PM Eastern Time. On Saturdays and Sundays, we check messages approximately every "
    "two hours. We'll follow up as soon as a team member is available. If this is an emergency, call 911. For an "
    "urgent issue, reply URGENT."
)

COOLDOWN = timedelta(hours=5)
STAFF_ACTIVE = timedelta(minutes=30)     # staff wrote this recently: they are replying, no automatic message
LOOKBACK = timedelta(minutes=30)         # older events are never acknowledged (e.g. right after a deploy)
RETRY_AFTER = timedelta(minutes=3)

URGENT = re.compile(r"(?<!not )(?<!no )(?<!isn't )(?<!nothing )\burgent\b", re.I)
_ONLY_THANKS = re.compile(
    r"^\s*(ok(ay)?|k|thanks?( you)?( so much)?|thank u|thx|ty|great|got it|perfect|cool|sounds good|good|nice|awesome|"
    r"alright|sure|👍|🙏|❤️|😊)[\s.!]*$", re.I,
)


def ack_text():
    from mysite.ai_agent import prompt_library
    try:
        return prompt_library.raw('ai_after_hours_ack') or DEFAULT_ACK_TEXT
    except Exception:
        return DEFAULT_ACK_TEXT


def is_urgent(text):
    return bool(URGENT.search(text or ''))


def _local(value):
    from zoneinfo import ZoneInfo
    return value.astimezone(ZoneInfo(config.TEAM_TIMEZONE))


def _hhmm(value):
    return f"{_local(value):%a %H:%M} {config.TIMEZONE_LABEL}"


def _mode(event, conversation):
    from mysite.views.messaging import _should_send_ai_to_group
    apartment = getattr(conversation, 'apartment', None)
    return 'live' if event.send_allowed and _should_send_ai_to_group(apartment) else 'test'


def _staff_wrote_recently(sids, before):
    from mysite.ai_agent import inputs
    from mysite.models import TwilioMessage
    recent = TwilioMessage.objects.filter(
        conversation_sid__in=sids, message_timestamp__gte=before - STAFF_ACTIVE, message_timestamp__lte=before,
    ).order_by('-message_timestamp')
    ai_answers = inputs._ai_answers(sids)
    return next((m for m in recent if inputs.classify_sender(m, ai_answers)[0] == inputs.ROLE_STAFF), None)


def decide(event, now=None):
    """
    (status, reason, mode) for one tenant message event, without sending anything. status is an AIAfterHoursAck
    status; STATUS_SENT means "send it now".
    """
    from mysite import conversation_groups
    from mysite.models import AIAfterHoursAck as Ack, TwilioConversation

    received = event.created_at or now or timezone.now()
    conversation = TwilioConversation.objects.filter(conversation_sid=event.conversation_sid).select_related('apartment').first()
    mode = _mode(event, conversation)
    if config.is_office_hours(received):
        return Ack.STATUS_NOT_APPLICABLE, 'office hours', mode
    if not (conversation and conversation.apartment_id):
        return Ack.STATUS_NOT_APPLICABLE, 'chat is not linked to an apartment', mode
    if _ONLY_THANKS.match(event.body or ''):
        return Ack.STATUS_NOT_APPLICABLE, 'only a thanks / ok - nothing to acknowledge', mode
    if config.alert_style() == 'v5' and is_urgent(event.body):
        # The text says "for an urgent issue, reply URGENT": pointless for a tenant who just wrote URGENT. The urgent
        # flow (call + urgent alert) takes over (simple alerts, A13).
        return Ack.STATUS_NOT_APPLICABLE, 'the tenant wrote URGENT - handled as urgent, no after-hours text', mode
    sids = conversation_groups.group_sids(event.conversation_sid)
    counted = [Ack.STATUS_SENT] if mode == 'live' else [Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND]
    last = Ack.objects.filter(conversation_sid__in=sids, status__in=counted, sent_at__gt=received - COOLDOWN) \
        .order_by('-sent_at').first()
    if last:
        return Ack.STATUS_SUPPRESSED, f"already {'sent' if last.status == Ack.STATUS_SENT else 'would have been sent'} " \
                                      f"{_hhmm(last.sent_at)} (at most once per 5 hours)", mode
    staff = _staff_wrote_recently(sids, received)
    if staff:
        return Ack.STATUS_SUPPRESSED, f"staff are replying in the chat (message at {_hhmm(staff.message_timestamp)})", mode
    return (Ack.STATUS_SENT if mode == 'live' else Ack.STATUS_WOULD_SEND), None, mode


def _send(ack, event):
    """Twilio send, at any hour (no notification-window hold). Raises on failure."""
    from mysite.views import messaging
    payload = event.payload or {}
    messaging.send_messsage_by_sid(ack.conversation_sid, payload.get('reply_author') or 'Virtual Assistant', ack.text,
                                   payload.get('sender_phone') or messaging.TWILIO_ASSISTANT_PHONE, None)


def _try_send(ack, event, now):
    from mysite.models import AIAfterHoursAck as Ack
    ack.attempts += 1
    try:
        _send(ack, event)
    except Exception as e:
        ack.status, ack.error = Ack.STATUS_FAILED, str(e)[:2000]
        ack.retry_at = now + RETRY_AFTER if ack.attempts < 2 else None
        ack.save()
        report_error(e, "after-hours auto-message was NOT delivered to the tenant"
                        + (" - retrying once in 3 minutes" if ack.retry_at else " - retry failed too, not retried again"),
                     {'conversation_sid': ack.conversation_sid, 'message': (event.body or '')[:300]})
        return False
    ack.status, ack.sent_at, ack.retry_at, ack.error = Ack.STATUS_SENT, timezone.now(), None, None
    ack.save()
    log_info(f"After-hours auto-message sent to {ack.conversation_sid} (event {event.id})", category='sms')
    return True


def handle_event(event, now=None):
    """Decides, records and (live) sends the automatic message for one tenant event. Returns the AIAfterHoursAck."""
    from django.db import IntegrityError, transaction

    from mysite.models import AIAfterHoursAck as Ack
    now = now or timezone.now()
    status, reason, mode = decide(event, now)
    sending = status == Ack.STATUS_SENT
    try:
        with transaction.atomic():
            ack = Ack.objects.create(
                event=event, conversation_sid=event.conversation_sid, mode=mode,
                # a row that is being sent says so until Twilio answers; one row per event = never sent twice
                status=Ack.STATUS_FAILED if sending else status, reason=('sending' if sending else reason),
                text=ack_text() if status in (Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND) else None,
                sent_at=now if status == Ack.STATUS_WOULD_SEND else None,
            )
    except IntegrityError:
        return Ack.objects.get(event=event)
    if sending:
        ack.reason = None
        _try_send(ack, event, now)
    if config.alert_style() == 'v5' and ack.status in (Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND):
        # Simple alerts: what the AI sends by itself is shown to the team as an AI MESSAGE (D1 / D2)
        try:
            from mysite.ai_agent import alerts_v5
            alerts_v5.post_after_hours(event, ack)
        except Exception as e:
            report_error(e, "could not post the after-hours AI MESSAGE", {'event': event.id})
    if is_urgent(event.body) and not config.is_office_hours(event.created_at or now):
        from mysite.ai_agent import calls
        calls.request_call(event.conversation_sid, f"a tenant replied URGENT: {(event.body or '')[:160]}", mode,
                           event=event)
    return ack


def process_pending(now=None):
    """Worker tick: new tenant events get their after-hours decision; failed sends are retried once. Returns count."""
    from mysite.models import AIAfterHoursAck as Ack, AIEvent
    now = now or timezone.now()
    handled = 0
    events = AIEvent.objects.filter(
        event_type=AIEvent.TYPE_TENANT_MESSAGE, created_at__gte=now - LOOKBACK, after_hours_ack__isnull=True,
    ).order_by('id')[:50]
    for event in events:
        try:
            handle_event(event, now)
        except Exception as e:
            report_error(e, "after-hours auto-message check failed", {'event': event.id, 'conversation_sid': event.conversation_sid})
        handled += 1
    for ack in Ack.objects.filter(status=Ack.STATUS_FAILED, retry_at__lte=now).select_related('event')[:20]:
        Ack.objects.filter(id=ack.id).update(retry_at=None)   # claimed: tried once more at most
        if ack.event:
            _try_send(ack, ack.event, now)
    return handled


# ---------------------------------------------------------------------------
# For the AI input and the Telegram card
# ---------------------------------------------------------------------------

def acks_for(events):
    from mysite.models import AIAfterHoursAck as Ack
    return list(Ack.objects.filter(event__in=[e.id for e in events]).order_by('id'))


def _latest_relevant(acks):
    """The ack that matters for a burst: a sent / would-send one, else a suppressed one, else any."""
    from mysite.models import AIAfterHoursAck as Ack
    for statuses in ((Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND), (Ack.STATUS_FAILED,), (Ack.STATUS_SUPPRESSED,)):
        found = [a for a in acks if a.status in statuses]
        if found:
            return found[-1]
    return acks[-1] if acks else None


def input_block(events):
    """AFTER_HOURS_ACK line for the AI: what the tenant already got automatically."""
    from mysite.models import AIAfterHoursAck as Ack
    ack = _latest_relevant(acks_for(events))
    if not ack or ack.status == Ack.STATUS_NOT_APPLICABLE:
        return None
    if ack.status in (Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND):
        what = (f"the backend {'SENT' if ack.status == Ack.STATUS_SENT else 'would have sent (test apartment)'} the "
                f"automatic after-hours message to the tenant at {_hhmm(ack.sent_at)}")
    elif ack.status == Ack.STATUS_SUPPRESSED:
        what = f"no new automatic after-hours message ({ack.reason})"
    else:
        what = "the automatic after-hours message could not be delivered"
    return (f"AFTER_HOURS_ACK: {what}. Its text: \"{ack_text()}\" - it only says we received the message outside "
            "office hours. Never repeat it or promise when staff will reply; your answer is separate and waits for a "
            "manager's approval like any other.")


def card_line(events):
    """One line for the Telegram card, or None when every message came in during office hours."""
    from mysite.models import AIAfterHoursAck as Ack
    acks = acks_for(events)
    ack = _latest_relevant(acks)
    if not ack:
        return None
    if ack.status == Ack.STATUS_NOT_APPLICABLE:
        return None if ack.reason == 'office hours' else f"🌙 After-hours message: not sent ({ack.reason})"
    if ack.status == Ack.STATUS_SENT:
        return f"🌙 After-hours message: SENT {_hhmm(ack.sent_at)}"
    if ack.status == Ack.STATUS_WOULD_SEND:
        return f"🌙 After-hours message: WOULD AUTO-SEND {_hhmm(ack.sent_at)} (TEST - nothing sent)"
    if ack.status == Ack.STATUS_FAILED:
        return "🌙 After-hours message: FAILED" + (" - retrying once in 3 min" if ack.retry_at else "")
    return f"🌙 After-hours message: SUPPRESSED - {ack.reason}"
