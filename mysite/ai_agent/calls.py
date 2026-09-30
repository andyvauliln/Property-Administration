"""
Phone calls to the on-call person (user decision 2026-09-30).

A Telegram bot cannot ring anyone, so for an emergency (confirmed by the AI) or a tenant's URGENT reply outside office
hours Twilio calls a staff member and reads a short message: "... check the Telegram AI group". The person is
StaffMember AI_AGENT_CALL_STAFF (default Farid); his phone is dialled first, then his secondary phone when the first
call is not answered. The worker checks each call about a minute later (check_calls).

- One call per tenant per 30 minutes (a burst of URGENT messages rings once).
- Test apartments never call (status simulated, shown as "WOULD CALL" on the card) unless AI_AGENT_CALLS_IN_TEST=on.
- AI_AGENT_CALLS=off switches every call off (simulated).
"""
import os
from datetime import timedelta
from xml.sax.saxutils import escape

from django.utils import timezone

from mysite.ai_agent import config
from mysite.ai_agent.notify import report_error, send_ai_chat
from mysite.unified_logger import log_info

CALL_COOLDOWN = timedelta(minutes=30)
CHECK_AFTER = timedelta(seconds=75)
RING_SECONDS = 25
# Twilio call statuses that end a call without anyone answering -> try the next number
NOT_ANSWERED = ('busy', 'failed', 'no-answer', 'canceled')


def call_staff_name():
    return (os.environ.get('AI_AGENT_CALL_STAFF') or 'Farid').strip()


def call_from():
    from mysite.views.messaging import TWILIO_ASSISTANT_PHONE
    return (os.environ.get('AI_AGENT_CALL_FROM') or TWILIO_ASSISTANT_PHONE).strip()


def _enabled(mode):
    if (os.environ.get('AI_AGENT_CALLS') or 'on').strip().lower() in ('off', '0', 'false', 'no'):
        return False
    return mode == 'live' or (os.environ.get('AI_AGENT_CALLS_IN_TEST') or 'off').strip().lower() in ('on', '1', 'true', 'yes')


def _twiml(text):
    said = escape(text)
    return (f'<Response><Say voice="alice">{said}</Say><Pause length="1"/>'
            f'<Say voice="alice">Again: {said}</Say></Response>')


def _unit(conversation_sid):
    from mysite.models import TwilioConversation
    conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).select_related('apartment').first()
    return getattr(getattr(conversation, 'apartment', None), 'name', None) or 'a tenant chat'


def request_call(conversation_sid, reason, mode, event=None, ai_run=None):
    """Creates (and, when calls are on, dials) one call. Returns the AIAlertCall. Never raises."""
    from mysite import conversation_groups
    from mysite.models import AIAlertCall, StaffMember

    try:
        sids = conversation_groups.group_sids(conversation_sid)
        recent = AIAlertCall.objects.filter(conversation_sid__in=sids, created_at__gte=timezone.now() - CALL_COOLDOWN) \
            .exclude(status=AIAlertCall.STATUS_SKIPPED).order_by('-id').first()
        staff = StaffMember.objects.filter(ai_name__iexact=call_staff_name(), is_active=True).first()
        call = AIAlertCall(conversation_sid=conversation_sid, event=event, ai_run=ai_run, reason=reason[:500],
                           staff_name=getattr(staff, 'ai_name', call_staff_name()), phones=staff.phones if staff else [],
                           mode=mode)
        if recent:
            call.status, call.note = AIAlertCall.STATUS_SKIPPED, f"already called about this tenant at {recent.created_at:%H:%M} UTC"
            call.save()
            return call
        if not call.phones:
            call.status, call.note = AIAlertCall.STATUS_FAILED, f"StaffMember {call.staff_name} has no phone number"
            call.save()
            send_ai_chat(f"📞 Could NOT call {call.staff_name} about {_unit(conversation_sid)}: no phone number in AI staff.")
            return call
        if not _enabled(mode):
            call.status = AIAlertCall.STATUS_SIMULATED
            call.note = 'test apartment - nobody called' if mode != 'live' else 'calls are switched off (AI_AGENT_CALLS=off)'
            call.save()
            return call
        call.save()
        _dial(call)
        return call
    except Exception as e:
        report_error(e, "could not start the alert phone call", {'conversation_sid': conversation_sid, 'reason': reason})
        return None


def _dial(call):
    from mysite.models import AIAlertCall
    from mysite.views.messaging import get_twilio_client

    phone = call.phones[call.phone_index]
    text = (f"Urgent message from the property A I assistant about {_unit(call.conversation_sid)}. {call.reason}. "
            f"Please check the Telegram A I group now.")
    try:
        twilio_call = get_twilio_client().calls.create(to=phone, from_=call_from(), twiml=_twiml(text), timeout=RING_SECONDS)
    except Exception as e:
        call.status, call.note = AIAlertCall.STATUS_FAILED, f"Twilio refused the call to {phone}: {str(e)[:300]}"
        call.save()
        report_error(e, f"alert phone call to {call.staff_name} FAILED", {'reason': call.reason, 'phone': phone})
        return
    call.call_sid, call.status, call.check_at = twilio_call.sid, AIAlertCall.STATUS_CALLING, timezone.now() + CHECK_AFTER
    call.save()
    send_ai_chat(f"📞 Calling {call.staff_name} ({phone}) about {_unit(call.conversation_sid)}: {call.reason}")
    log_info(f"Alert call {call.id} to {call.staff_name} {phone}: {call.call_sid}", category='sms')


def check_calls(now=None):
    """Worker tick: calls that were dialled a minute ago - answered, or try the next number. Returns how many."""
    from mysite.models import AIAlertCall
    from mysite.views.messaging import get_twilio_client

    now = now or timezone.now()
    checked = 0
    for call in AIAlertCall.objects.filter(status=AIAlertCall.STATUS_CALLING, check_at__lte=now)[:10]:
        checked += 1
        try:
            status = get_twilio_client().calls(call.call_sid).fetch().status
        except Exception as e:
            call.check_at = now + CHECK_AFTER
            call.note = f"could not read the call status: {str(e)[:200]}"
            call.save()
            continue
        if status in NOT_ANSWERED:
            if call.phone_index + 1 < len(call.phones):
                call.phone_index += 1
                call.note = f"{call.phones[call.phone_index - 1]}: {status}"
                call.save()
                _dial(call)
            else:
                call.status, call.note = AIAlertCall.STATUS_UNANSWERED, f"last number: {status}"
                call.save()
                send_ai_chat(f"📞 {call.staff_name} did NOT answer ({', '.join(call.phones)}) about "
                             f"{_unit(call.conversation_sid)}: {call.reason}")
        elif status == 'completed':
            call.status, call.note = AIAlertCall.STATUS_ANSWERED, f"answered on {call.phones[call.phone_index]}"
            call.save()
        else:   # queued / ringing / in-progress: look again shortly
            call.check_at = now + timedelta(seconds=30)
            call.save()
    return checked


def card_line(conversation_sid, since):
    """'📞 ...' for the Telegram card: the call made for this chat since the given time, or None."""
    from mysite import conversation_groups
    from mysite.models import AIAlertCall
    call = AIAlertCall.objects.filter(conversation_sid__in=conversation_groups.group_sids(conversation_sid),
                                      created_at__gte=since).order_by('-id').first()
    if not call:
        return None
    label = {
        AIAlertCall.STATUS_CALLING: f"CALLING {call.staff_name}", AIAlertCall.STATUS_PENDING: f"CALLING {call.staff_name}",
        AIAlertCall.STATUS_ANSWERED: f"{call.staff_name} ANSWERED", AIAlertCall.STATUS_UNANSWERED: f"{call.staff_name} did NOT answer",
        AIAlertCall.STATUS_FAILED: f"call to {call.staff_name} FAILED", AIAlertCall.STATUS_SIMULATED: f"WOULD CALL {call.staff_name}",
        AIAlertCall.STATUS_SKIPPED: f"no new call ({call.note})",
    }.get(call.status, call.status)
    return f"📞 {label}" + (f" - {call.note}" if call.status in (AIAlertCall.STATUS_SIMULATED, AIAlertCall.STATUS_FAILED) else "")
