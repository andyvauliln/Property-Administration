"""
Queue + processing for the AI agent.

enqueue_tenant_message()  called from the Twilio webhook / chat UI (fast, no AI call)
process_events()          called by the run_ai_agent worker for one conversation batch
run_agent()               one Claude run + run report, no sending (also used by ai_agent_replay)
"""
import re

from django.utils import timezone

from mysite.ai_agent import config, inputs, prompts, run_report, runner
from mysite.ai_agent.notify import notify_ai_chat, report_error
from mysite.unified_logger import log_info, log_warning

ACTION_TYPES = frozenset([
    'CREATE_ISSUE', 'UPDATE_ISSUE_STATE', 'CREATE_TICKET', 'TICKET_COMMENT', 'UPDATE_TICKET',
    'INTERNAL_ALERT', 'QUEUE_FOR_REVIEW', 'SCHEDULE_FOLLOWUP', 'CANCEL_FOLLOWUP', 'KB_UPDATE', 'CASE_NOTE',
])
# Until issue tracking / ClickUp are connected, these are the actions staff must actually see
FORWARDED_ACTION_TYPES = frozenset(['INTERNAL_ALERT', 'QUEUE_FOR_REVIEW', 'CREATE_TICKET'])
# Actions that make "I've logged it / passed it to the team" true
LOGGING_ACTION_TYPES = FORWARDED_ACTION_TYPES | {'CREATE_ISSUE'}
_PROMISE = re.compile(r"\b(logged|passed (it|this|that|your message) (on )?to|reported this|let the team know)\b", re.I)

# A message that looks like an emergency is never held back by the 1-minute wait
_EMERGENCY = re.compile(
    r"\b(fire|smoke|gas smell|smell(s)? (of )?gas|carbon monoxide|co alarm|flood(ing|ed)?|water (is )?(pouring|everywhere)|"
    r"sparks?|burning|break[- ]?in|broke in|intruder|injur(y|ed)|bleeding|ambulance|911|emergency)\b", re.I,
)

STATUS_EXECUTED = 'executed'
STATUS_SIMULATED = 'simulated'
STATUS_REJECTED = 'rejected'


# ---------------------------------------------------------------------------
# Enqueue (webhook / chat UI side)
# ---------------------------------------------------------------------------

def enqueue_tenant_message(conversation_sid, message_sid, body, send_allowed=True,
                           reply_author='Virtual Assistant', sender_phone=None, source='webhook'):
    """
    Returns True when the agent backend took the message (caller must NOT run the legacy AI),
    False when the legacy OpenRouter path should handle it.
    """
    try:
        if not config.is_agent_backend_enabled():
            return False

        from mysite.group_chat_logger import log_ai_customer_skipped, log_ai_customer_start
        from mysite.models import AIEvent, TwilioConversation, TwilioMessage
        from mysite.views.messaging import _is_skippable_message

        if _is_skippable_message(body):
            log_ai_customer_skipped(conversation_sid, body)
            return True

        conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
        message = TwilioMessage.objects.filter(message_sid=message_sid).first() if message_sid else None
        payload = {'reply_author': reply_author, 'sender_phone': sender_phone, 'source': source}
        existing = AIEvent.objects.filter(message=message, event_type=AIEvent.TYPE_TENANT_MESSAGE) if message else None
        if existing is not None and existing.exists():
            if source == 'chat_ui':
                # The Twilio webhook may have queued the same message first; the chat UI knows
                # whether the manager ticked "Send to group chat"
                existing.filter(status=AIEvent.STATUS_PENDING).update(send_allowed=bool(send_allowed), payload=payload)
            return True

        AIEvent.objects.create(
            event_type=AIEvent.TYPE_TENANT_MESSAGE,
            conversation=conversation,
            conversation_sid=conversation_sid,
            message=message,
            body=body,
            send_allowed=bool(send_allowed),
            payload=payload,
        )
        log_ai_customer_start(
            conversation_sid, reply_author, body,
            getattr(conversation, 'apartment_id', None), getattr(conversation, 'booking_id', None),
        )
        return True
    except Exception as e:
        # Never lose a tenant message because of the queue: fall back to the legacy path
        report_error(e, "could not queue a tenant message, the old AI handled it instead", source='webhook')
        return False


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------

def _parse_output(output):
    if not isinstance(output, dict):
        return None
    answer = str(output.get('answer') or '').strip()
    no_answer = (not answer) or answer.upper() == 'NO_ANSWER'
    actions = output.get('actions')
    return {
        'answer': None if no_answer else answer,
        'why': str(output.get('why') or '').strip() or None,
        'no_answer': no_answer,
        'actions': actions if isinstance(actions, list) else [],
    }


def run_agent(event_type, conversation_sid, apartment, booking, trigger_messages, mode, body_override=None, now=None):
    """Runs Claude once. Returns a dict with everything needed for delivery and for the report."""
    started_at = timezone.now()
    run_dir = run_report.new_run_dir(conversation_sid, apartment, event_type)
    system_prompt, system_source = prompts.get_system_prompt()
    user_input, sources = inputs.build_agent_input(
        event_type, conversation_sid, apartment, booking, trigger_messages, body_override=body_override, now=now,
    )
    until_id = trigger_messages[-1].id if trigger_messages else None
    run = runner.run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=until_id)

    tenant = getattr(booking, 'tenant', None)
    meta = {
        'started_at': started_at.astimezone(inputs._team_tz()).strftime('%Y-%m-%d %H:%M:%S %Z'),
        'event_type': event_type,
        'mode': mode,
        'backend': config.BACKEND_CLAUDE_CLI,
        'conversation_sid': conversation_sid,
        'apartment': run_report.apartment_label(apartment),
        'apartment_id': getattr(apartment, 'id', None),
        'booking_id': getattr(booking, 'id', None),
        'tenant': getattr(tenant, 'full_name', None),
        'trigger_message_ids': [m.id for m in trigger_messages],
        'system_prompt_source': system_source,
        'context_sources': sources,
        'model': run.get('model'),
        'timed_out': run.get('timed_out'),
        'exit_code': run.get('exit_code'),
    }
    new_messages_text = user_input.rsplit("=== NEW MESSAGE(S) TO HANDLE NOW ===\n", 1)[-1]
    return {
        'run': run,
        'run_dir': run_dir,
        'meta': meta,
        'user_input': user_input,
        'parsed': _parse_output(run.get('output')) if run.get('ok') else None,
        'new_messages_text': new_messages_text,
    }


# ---------------------------------------------------------------------------
# Actions + delivery
# ---------------------------------------------------------------------------

def _alert_text(action, meta, new_messages_text, is_test=False):
    priority = str(action.get('priority') or 'routine').upper()
    responsible = action.get('responsible') or action.get('owner') or ''
    if isinstance(responsible, list):
        responsible = ", ".join(str(r) for r in responsible)
    text = action.get('text') or action.get('description') or action.get('title') or action.get('summary') or ''
    return (
        f"{'🧪 TEST MODE (tenant was NOT answered) - ' if is_test else ''}🤖 AI {action.get('type')} [{priority}]\n"
        f"Unit: {meta.get('apartment')} | Tenant: {meta.get('tenant')}\n"
        f"For: {responsible or 'team'}\n\n{text}\n\n"
        f"Tenant message:\n{new_messages_text[:1500]}"
    )


def handle_actions(parsed, mode, meta, new_messages_text, forward_alerts=True):
    """
    Validates actions and returns [{'action', 'status', 'detail'}].
    Alerts go to the AI Telegram chat in live AND test mode (test alerts are marked).
    forward_alerts=False (replay) records everything without notifying anyone.
    """
    from mysite.models import AIRun

    actions = list(parsed.get('actions') or [])
    answer = parsed.get('answer') or ''
    emitted = {a.get('type') for a in actions if isinstance(a, dict)}
    backend_added = None
    if answer and _PROMISE.search(answer) and not (emitted & LOGGING_ACTION_TYPES):
        backend_added = {
            'type': 'INTERNAL_ALERT', 'priority': 'routine', 'responsible': ['Edy'],
            'text': f"AI told the tenant the matter was passed to the team: \"{answer}\"",
        }
        actions.append(backend_added)

    results = []
    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in ACTION_TYPES:
            results.append({'action': action, 'status': STATUS_REJECTED, 'detail': 'unknown action type'})
            continue
        detail = ''
        if action is backend_added:
            detail = 'added by backend: the answer promised escalation but no matching action was emitted. '
        is_test = mode != AIRun.MODE_LIVE
        if action['type'] in FORWARDED_ACTION_TYPES and not forward_alerts:
            results.append({'action': action, 'status': STATUS_SIMULATED, 'detail': detail + 'replay - nobody notified'})
        elif action['type'] in FORWARDED_ACTION_TYPES:
            ok, note = notify_ai_chat(_alert_text(action, meta, new_messages_text, is_test))
            results.append({
                'action': action,
                'status': STATUS_EXECUTED if ok else STATUS_REJECTED,
                'detail': detail + note,
            })
        else:
            results.append({
                'action': action, 'status': STATUS_SIMULATED,
                'detail': detail + ('test mode, ' if is_test else '') + 'recorded only (issue tracking not connected yet)',
            })
    return results


def _staff_replied_after(conversation_sid, last_message):
    from mysite.models import TwilioMessage
    if not last_message:
        return False
    later = TwilioMessage.objects.filter(conversation_sid=conversation_sid, id__gt=last_message.id)
    ai_answers = inputs._ai_answers(conversation_sid)
    return any(inputs.classify_sender(m, ai_answers)[0] == inputs.ROLE_STAFF for m in later)


def deliver(parsed, mode, conversation_sid, booking, last_message, payload):
    """Sends the answer to the Twilio group chat in live mode. Returns {'sent_to_chat', 'note'}."""
    from mysite.group_chat_logger import log_ai_customer_no_answer, log_ai_customer_sent, log_ai_disabled
    from mysite.models import AIRun
    from mysite.views.messaging import (
        TWILIO_ASSISTANT_PHONE, send_messsage_by_sid,
    )

    answer = parsed.get('answer')
    if not answer:
        log_ai_customer_no_answer(conversation_sid, getattr(last_message, 'body', '') or '', parsed.get('why') or '')
        return {'sent_to_chat': False, 'note': 'NO_ANSWER - nothing to send'}
    if mode != AIRun.MODE_LIVE:
        log_ai_disabled(conversation_sid, payload.get('reply_author') or '', answer)
        return {'sent_to_chat': False, 'note': 'test mode - stored in DB, not sent to Twilio'}
    if _staff_replied_after(conversation_sid, last_message):
        return {'sent_to_chat': False, 'note': 'suppressed - staff replied while the AI was working'}

    try:
        send_messsage_by_sid(
            conversation_sid,
            payload.get('reply_author') or 'Virtual Assistant',
            answer,
            payload.get('sender_phone') or TWILIO_ASSISTANT_PHONE,
            None,
        )
    except Exception as e:
        tenant = getattr(booking, 'tenant', None)
        report_error(e, "AI answer was NOT delivered to the tenant", {
            'tenant': getattr(tenant, 'full_name', None), 'phone': getattr(tenant, 'phone', None),
            'conversation_sid': conversation_sid, 'answer': answer,
        })
        return {'sent_to_chat': False, 'note': f'Twilio send failed: {e}', 'error': str(e)}
    log_ai_customer_sent(conversation_sid, answer)
    return {'sent_to_chat': True, 'note': 'sent to Twilio group chat'}


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------

def _finish_events(events, status, error=None):
    from mysite.models import AIEvent
    AIEvent.objects.filter(id__in=[e.id for e in events]).update(
        status=status, error=error, finished_at=timezone.now(), updated_at=timezone.now(),
    )


def process_events(events):
    """
    events: pending AIEvent rows of ONE conversation (oldest first), already marked running.
    One Claude run answers the whole batch. Returns the AIRun (or None when skipped).
    """
    from mysite.group_chat_logger import log_ai_error, log_no_conv_link
    from mysite.models import AIEvent, AIRun, Apartment, Booking, TwilioConversation
    from mysite.views.messaging import _persist_customer_ai_result, _should_send_ai_to_group

    last_event = events[-1]
    conversation_sid = last_event.conversation_sid
    conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
    if not (conversation and conversation.apartment_id and conversation.booking_id):
        log_no_conv_link(conversation_sid, '', last_event.body or '')
        _finish_events(events, AIEvent.STATUS_SKIPPED, 'conversation is not linked to apartment + booking')
        return None

    apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=conversation.apartment_id)
    booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)
    trigger_messages = [e.message for e in events if e.message_id]
    last_message = trigger_messages[-1] if trigger_messages else None
    live = all(e.send_allowed for e in events) and _should_send_ai_to_group(apartment)
    mode = AIRun.MODE_LIVE if live else AIRun.MODE_TEST

    outcome = run_agent(
        last_event.event_type, conversation_sid, apartment, booking, trigger_messages, mode,
        body_override="\n".join(e.body or '' for e in events),
    )
    run, parsed, meta = outcome['run'], outcome['parsed'], outcome['meta']

    actions, delivery = [], {'sent_to_chat': False, 'note': 'run failed - nothing sent'}
    if parsed:
        delivery = deliver(parsed, mode, conversation_sid, booking, last_message, last_event.payload or {})
        actions = handle_actions(parsed, mode, meta, outcome['new_messages_text'])
        if last_message:
            _persist_customer_ai_result(
                last_message.message_sid,
                {'answer': parsed['answer'], 'why': parsed['why']},
                sent_to_chat=delivery['sent_to_chat'],
            )

    ai_run = AIRun.objects.create(
        event=last_event, conversation_sid=conversation_sid, message=last_message,
        event_type=last_event.event_type, mode=mode, model=run.get('model'),
        answer=parsed['answer'] if parsed else None,
        why=parsed['why'] if parsed else None,
        no_answer=bool(parsed and parsed['no_answer']),
        actions=actions, sent_to_chat=delivery['sent_to_chat'], delivery_note=(delivery.get('note') or '')[:255],
        report_dir=str(outcome['run_dir']), error=run.get('error') or delivery.get('error'),
    )
    meta['run_id'] = ai_run.id
    meta['event_ids'] = [e.id for e in events]
    summary = run_report.write_report(
        outcome['run_dir'], meta, outcome['user_input'], run, parsed, actions, delivery,
        new_messages_text=outcome['new_messages_text'],
    )
    AIRun.objects.filter(id=ai_run.id).update(
        session_id=summary['session_id'],
        input_tokens=summary['input_tokens'], output_tokens=summary['output_tokens'],
        cache_read_tokens=summary['cache_read_tokens'], cache_write_tokens=summary['cache_write_tokens'],
        cost_usd=summary['cost_usd'], num_turns=summary['num_turns'],
        tool_calls=len(summary['tool_calls']), duration_ms=summary['duration_ms'],
    )

    if parsed:
        _finish_events(events, AIEvent.STATUS_DONE)
        log_info(
            f"AI agent run #{ai_run.id} {mode} {meta['apartment']}: "
            f"{'NO_ANSWER' if parsed['no_answer'] else 'ANSWER'}, {len(actions)} actions, ${summary['cost_usd']:.4f}",
            category='sms',
        )
    else:
        _finish_events(events, AIEvent.STATUS_FAILED, run.get('error'))
        log_ai_error(conversation_sid, "ai_agent", run.get('error') or 'unknown error')
        # The tenant message must still reach a human when the AI is down
        report_error(
            Exception(run.get('error') or 'AI agent run failed'),
            f"run failed - tenant message needs a human ({meta['apartment']})",
            {
                'tenant': meta.get('tenant'), 'conversation_sid': conversation_sid,
                'tenant_message': outcome['new_messages_text'], 'report': f"/ai-runs/{ai_run.id}/",
            },
        )
    return ai_run


def _batch_wait_seconds(batch):
    if any(_EMERGENCY.search(e.body or '') for e in batch):
        return 0
    if all((e.payload or {}).get('source') == 'chat_ui' for e in batch):
        return config.chat_ui_debounce_seconds()
    return config.debounce_seconds()


def claim_next_batch():
    """
    Picks the oldest pending conversation whose newest pending event is older than the debounce
    window, marks its events running and returns them (oldest first). Returns [] when idle.
    """
    from datetime import timedelta

    from mysite.models import AIEvent

    now = timezone.now()
    pending = AIEvent.objects.filter(status=AIEvent.STATUS_PENDING)
    seen = set()
    for conversation_sid in pending.order_by('id').values_list('conversation_sid', flat=True)[:200]:
        if conversation_sid in seen:
            continue
        seen.add(conversation_sid)
        batch = list(pending.filter(conversation_sid=conversation_sid).order_by('id'))
        if not batch or batch[-1].created_at > now - timedelta(seconds=_batch_wait_seconds(batch)):
            continue
        claimed = AIEvent.objects.filter(
            id__in=[e.id for e in batch], status=AIEvent.STATUS_PENDING,
        ).update(status=AIEvent.STATUS_RUNNING, started_at=timezone.now(), updated_at=timezone.now())
        if claimed != len(batch):
            continue
        for event in batch:
            event.status = AIEvent.STATUS_RUNNING
        AIEvent.objects.filter(id__in=[e.id for e in batch]).update(attempts=batch[0].attempts + 1)
        return batch
    return []


def release_stale_events(max_attempts=2):
    """Events left 'running' by a crashed worker go back to pending (or fail after max_attempts)."""
    from datetime import timedelta

    from mysite.models import AIEvent

    stale_before = timezone.now() - timedelta(seconds=config.run_timeout_seconds() + 120)
    stale = AIEvent.objects.filter(status=AIEvent.STATUS_RUNNING, started_at__lt=stale_before)
    failed = stale.filter(attempts__gte=max_attempts).update(
        status=AIEvent.STATUS_FAILED, error='worker stopped while running', finished_at=timezone.now(),
    )
    requeued = stale.filter(attempts__lt=max_attempts).update(status=AIEvent.STATUS_PENDING)
    if failed or requeued:
        log_warning(f"AI agent: {requeued} stale events requeued, {failed} failed", category='sms')
