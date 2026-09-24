"""
Queue + processing for the AI agent.

enqueue_tenant_message()  called from the Twilio webhook / chat UI (fast, no AI call)
enqueue_staff_message()   same, for a manager's message in a tenant chat
fire_due_followups()      worker tick: due AIFollowUp -> FOLLOWUP_DUE event
process_events()          called by the run_ai_agent worker for one conversation batch
run_agent()               one Claude run, no sending, no saving (also used by ai_agent_replay)
"""
import os
import re
from datetime import timedelta

from django.utils import timezone

from mysite.ai_agent import actions as agent_actions
from mysite.ai_agent import config, inputs, prompts, run_report, runner
from mysite.ai_agent import answer_review, team_notify
from mysite.ai_agent import plan as agent_plan
from mysite.ai_agent.notify import report_error
from mysite.unified_logger import log_info, log_warning

REVIEW_ONLY_PREFIX = "[NOT SENT - staff answered first, shown for review only]"

# A message that looks like an emergency is never held back by the 1-minute wait
_EMERGENCY = re.compile(
    r"\b(fire|smoke|gas smell|smell(s)? (of )?gas|carbon monoxide|co alarm|flood(ing|ed)?|water (is )?(pouring|everywhere)|"
    r"sparks?|burning|break[- ]?in|broke in|intruder|injur(y|ed)|bleeding|ambulance|911|emergency)\b", re.I,
)


# ---------------------------------------------------------------------------
# Enqueue (webhook / chat UI side)
# ---------------------------------------------------------------------------

def _enqueue_message(event_type, conversation_sid, message_sid, body, send_allowed, payload):
    """Creates one AIEvent per stored message. Returns the event, or None when it already exists."""
    from mysite.models import AIEvent, TwilioConversation, TwilioMessage

    conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
    message = TwilioMessage.objects.filter(message_sid=message_sid).first() if message_sid else None
    existing = AIEvent.objects.filter(message=message, event_type=event_type) if message else None
    if existing is not None and existing.exists():
        if payload.get('source') == 'chat_ui':
            # The Twilio webhook may have queued the same message first; the chat UI knows
            # whether the manager ticked "Send to group chat"
            existing.filter(status=AIEvent.STATUS_PENDING).update(send_allowed=bool(send_allowed), payload=payload)
        return None
    return AIEvent.objects.create(
        event_type=event_type, conversation=conversation, conversation_sid=conversation_sid,
        message=message, body=body, send_allowed=bool(send_allowed), payload=payload,
    )


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
        from mysite.models import AIEvent
        from mysite.views.messaging import _is_skippable_message

        if _is_skippable_message(body):
            log_ai_customer_skipped(conversation_sid, body)
            return True

        payload = {'reply_author': reply_author, 'sender_phone': sender_phone, 'source': source}
        event = _enqueue_message(AIEvent.TYPE_TENANT_MESSAGE, conversation_sid, message_sid, body, send_allowed, payload)
        if event:
            conversation = event.conversation
            log_ai_customer_start(
                conversation_sid, reply_author, body,
                getattr(conversation, 'apartment_id', None), getattr(conversation, 'booking_id', None),
            )
        return True
    except Exception as e:
        # Never lose a tenant message because of the queue: fall back to the legacy path
        report_error(e, "could not queue a tenant message, the old AI handled it instead", source='webhook')
        return False


def enqueue_staff_message(conversation_sid, message_sid, body, source='webhook'):
    """
    A manager wrote in a tenant chat: the AI updates issues / follow-ups (normally without replying).
    Runs next to the legacy knowledge-base extraction, never instead of it. Returns True when queued.
    AI_AGENT_STAFF_EVENTS: all (default) | open_issues (only chats with open issues or follow-ups) | off
    """
    try:
        if not config.is_agent_backend_enabled():
            return False
        from mysite.models import AIEvent, AIFollowUp, AIIssue
        from mysite.views.messaging import _is_skippable_message

        setting = os.environ.get('AI_AGENT_STAFF_EVENTS', 'all').lower()
        if setting == 'off' or _is_skippable_message(body):
            return False
        if setting == 'open_issues':
            has_work = (
                AIIssue.objects.filter(conversation_sid=conversation_sid).exclude(state=AIIssue.STATE_RESOLVED).exists()
                or AIFollowUp.objects.filter(conversation_sid=conversation_sid, status=AIFollowUp.STATUS_PENDING).exists()
            )
            if not has_work:
                return False
        payload = {'source': source}
        return _enqueue_message(AIEvent.TYPE_STAFF_MESSAGE, conversation_sid, message_sid, body, True, payload) is not None
    except Exception as e:
        report_error(e, "could not queue a staff message for the AI agent", source='webhook')
        return False


def fire_due_followups():
    """Due follow-ups become FOLLOWUP_DUE events. Returns how many were fired."""
    from mysite.models import AIEvent, AIFollowUp, TwilioConversation

    fired = 0
    due = AIFollowUp.objects.filter(status=AIFollowUp.STATUS_PENDING, due_at__lte=timezone.now()).select_related('issue')
    for followup in due[:50]:
        claimed = AIFollowUp.objects.filter(id=followup.id, status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_FIRED, updated_at=timezone.now(),
        )
        if not claimed:
            continue
        if followup.issue and not followup.issue.is_open:
            AIFollowUp.objects.filter(id=followup.id).update(
                status=AIFollowUp.STATUS_CANCELLED, status_note='issue already resolved - AI not woken',
            )
            continue
        AIEvent.objects.create(
            event_type=AIEvent.TYPE_FOLLOWUP_DUE,
            conversation=TwilioConversation.objects.filter(conversation_sid=followup.conversation_sid).first(),
            conversation_sid=followup.conversation_sid,
            body=followup.reason,
            payload={'followup_id': followup.id, 'source': 'followup'},
        )
        fired += 1
    return fired


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
        'review_answer': (str(output.get('review_answer') or '').strip() or None) if no_answer else None,
        'actions': actions if isinstance(actions, list) else [],
    }


def run_agent(event_type, conversation_sid, apartment, booking, trigger_messages, mode,
              body_override=None, now=None, extra_block=None):
    """Runs Claude once. Returns a dict with everything needed for delivery and for the report."""
    started_at = timezone.now()
    run_dir = run_report.new_run_dir(conversation_sid, apartment, event_type)
    system_prompt, system_source = prompts.get_system_prompt()
    user_input, sources = inputs.build_agent_input(
        event_type, conversation_sid, apartment, booking, trigger_messages,
        body_override=body_override, now=now, extra_block=extra_block,
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
# Delivery
# ---------------------------------------------------------------------------

def _staff_replied_after(conversation_sid, last_message):
    from mysite.models import TwilioMessage
    if not last_message:
        return False
    later = TwilioMessage.objects.filter(conversation_sid=conversation_sid, id__gt=last_message.id)
    ai_answers = inputs._ai_answers(conversation_sid)
    return any(inputs.classify_sender(m, ai_answers)[0] == inputs.ROLE_STAFF for m in later)


def _leaked_access_code(answer, apartment, booking):
    """True when the answer contains a code that is hidden at this moment (outside the access window)."""
    from mysite.ai_agent import knowledge
    if not answer or knowledge.access_codes_allowed(booking):
        return False
    return any(value in answer for value in knowledge.hidden_code_values(apartment))


def _is_emergency(parsed, last_message):
    """Emergency answers are never held for review."""
    if any(isinstance(a, dict) and a.get('priority') == 'emergency' for a in parsed.get('actions') or []):
        return True
    return bool(_EMERGENCY.search(getattr(last_message, 'body', '') or ''))


def send_answer(conversation_sid, answer, reply_author, sender_phone, booking=None):
    """Sends one answer to the tenant group chat (notification-window gated). Returns {'sent_to_chat', 'note'}."""
    from mysite.group_chat_logger import log_ai_customer_sent
    from mysite.views.messaging import TWILIO_ASSISTANT_PHONE, send_tenant_sms_gated

    try:
        sent = send_tenant_sms_gated(
            conversation_sid, reply_author or 'Virtual Assistant', answer, sender_phone or TWILIO_ASSISTANT_PHONE, None,
        )
    except Exception as e:
        tenant = getattr(booking, 'tenant', None)
        report_error(e, "AI answer was NOT delivered to the tenant", {
            'tenant': getattr(tenant, 'full_name', None), 'phone': getattr(tenant, 'phone', None),
            'conversation_sid': conversation_sid, 'answer': answer,
        })
        return {'sent_to_chat': False, 'note': f'Twilio send failed: {e}', 'error': str(e)}
    if not sent:
        return {'sent_to_chat': False,
                'note': 'held - outside the 08:00-21:00 Florida notification window, will send at the next window open'}
    log_ai_customer_sent(conversation_sid, answer)
    return {'sent_to_chat': True, 'note': 'sent to Twilio group chat'}


def deliver(parsed, mode, conversation_sid, booking, last_message, payload, apartment=None):
    """
    Sends the answer to the Twilio group chat in live mode, or holds it for the staff review window
    (answer_review.py). Returns {'sent_to_chat', 'note'} (+ 'held': True when held).
    """
    from mysite.group_chat_logger import log_ai_customer_no_answer, log_ai_disabled
    from mysite.models import AIRun

    answer = parsed.get('answer')
    if not answer:
        log_ai_customer_no_answer(conversation_sid, getattr(last_message, 'body', '') or '', parsed.get('why') or '')
        return {'sent_to_chat': False, 'note': 'NO_ANSWER - nothing to send'}
    if _leaked_access_code(answer, apartment, booking):
        report_error(
            Exception("answer contained an access code outside the allowed window"),
            "AI answer BLOCKED - not sent to the tenant", {'conversation_sid': conversation_sid, 'mode': mode},
        )
        return {'sent_to_chat': False, 'note': 'BLOCKED - answer contained an access code outside the allowed window',
                'error': 'answer blocked: access code outside the allowed window'}
    live = mode == AIRun.MODE_LIVE
    if not live:
        log_ai_disabled(conversation_sid, payload.get('reply_author') or '', answer)
    if _staff_replied_after(conversation_sid, last_message):
        return {'sent_to_chat': False, 'note': 'suppressed - staff replied while the AI was working'}
    # Test mode goes through the same 15-minute review as live (user request 2026-09-23); only the
    # final Twilio send is skipped
    minutes = config.review_hold_minutes()
    if minutes > 0 and not _is_emergency(parsed, last_message):
        return {'sent_to_chat': False, 'held': True, 'hold_until': timezone.now() + timedelta(minutes=minutes),
                'note': f"held {minutes:g} min for staff review in Telegram, then "
                        + ('sent unless corrected' if live else 'final unless corrected (test mode - never sent to Twilio)')}
    if not live:
        return {'sent_to_chat': False, 'note': 'test mode - stored in DB, not sent to Twilio'}
    return send_answer(conversation_sid, answer, payload.get('reply_author'), payload.get('sender_phone'), booking)


# ---------------------------------------------------------------------------
# Worker side
# ---------------------------------------------------------------------------

def _finish_events(events, status, error=None):
    from mysite.models import AIEvent
    AIEvent.objects.filter(id__in=[e.id for e in events]).update(
        status=status, error=error, finished_at=timezone.now(), updated_at=timezone.now(),
    )


def _batch_event_type(events):
    """One run handles the whole batch; the most important event names it."""
    from mysite.models import AIEvent
    types = {e.event_type for e in events}
    for event_type in (AIEvent.TYPE_TENANT_MESSAGE, AIEvent.TYPE_STAFF_MESSAGE, AIEvent.TYPE_FOLLOWUP_DUE):
        if event_type in types:
            return event_type
    return events[-1].event_type


def _followup_block(events):
    """Text for the AI about the follow-up(s) that became due."""
    from mysite.models import AIEvent, AIFollowUp

    ids = [(e.payload or {}).get('followup_id') for e in events if e.event_type == AIEvent.TYPE_FOLLOWUP_DUE]
    followups = AIFollowUp.objects.filter(id__in=[i for i in ids if i]).select_related('issue')
    if not followups:
        return None
    lines = ["FOLLOWUP_DUE (re-check the current state before acting; cancel or do nothing when it is no longer needed):"]
    for f in followups:
        lines.append(
            f"- followup_id: {f.public_id} | kind: {f.kind} | issue_id: {f.issue.public_id if f.issue else '-'} | "
            f"scheduled: {f.created_at.astimezone(inputs._team_tz()).strftime('%Y-%m-%d %H:%M')} | reason: {f.reason or '-'}"
        )
    return "\n".join(lines)


def check_tickets_before_reminder(events):
    """
    A reminder became due: look at the ClickUp task of its issue first (status + latest comments).
    - task closed  -> the issue is resolved quietly: no reminder, no message to the tenant, timers stopped
    - task open    -> its status and comments go to Claude, which decides whether a reminder is still needed
    Returns (events_still_needing_claude, ticket_block_text_or_None).
    """
    from mysite.ai_agent import clickup
    from mysite.ai_agent.notify import notify_ai_chat
    from mysite.models import AICaseNote, AIEvent, AIFollowUp, AIIssue

    lines, remaining, checked = [], [], {}
    for event in events:
        followup = None
        if event.event_type == AIEvent.TYPE_FOLLOWUP_DUE:
            followup = AIFollowUp.objects.filter(id=(event.payload or {}).get('followup_id')).select_related('issue').first()
        issue = followup.issue if followup else None
        if not (issue and issue.ticket_ref and clickup.delivery_mode() != 'off'):
            remaining.append(event)
            continue
        if issue.id not in checked:
            try:
                checked[issue.id] = clickup.get_task_state(issue.ticket_ref)
            except clickup.ClickUpError as e:
                checked[issue.id] = {'error': str(e)}
        state = checked[issue.id]
        if state.get('closed'):
            if issue.is_open:
                issue.state, issue.resolved_at = AIIssue.STATE_RESOLVED, timezone.now()
                issue.save()
                issue.followups.filter(status=AIFollowUp.STATUS_PENDING).update(
                    status=AIFollowUp.STATUS_CANCELLED, status_note='ClickUp task closed', updated_at=timezone.now(),
                )
                AICaseNote.objects.create(
                    conversation_sid=issue.conversation_sid, booking=issue.booking, issue=issue,
                    text=f"Issue closed by the backend: its ClickUp task is closed (status: {state['status']}). Tenant was not contacted.",
                )
                notify_ai_chat(
                    f"✅ {issue.apartment.name if issue.apartment else ''} · {issue.public_id} closed: its ClickUp task is closed "
                    f"(status: {state['status']}).\n{issue.summary}\nReminders stopped, the tenant was not contacted.\n{issue.ticket_ref}"
                )
            AIEvent.objects.filter(id=event.id).update(
                status=AIEvent.STATUS_DONE, error='ClickUp task closed - reminder not needed', finished_at=timezone.now(),
            )
            continue
        remaining.append(event)
        if state.get('error'):
            lines.append(f"- ticket_id: t-{issue.id} | issue_id: {issue.public_id} | ClickUp task could not be read ({state['error'][:120]})")
        else:
            lines.append(
                f"- ticket_id: t-{issue.id} | issue_id: {issue.public_id} | ClickUp status: {state['status']} | "
                f"assigned to: {', '.join(state['assignees']) or 'nobody'} | last change: {state['updated']}"
            )
            lines += [f"    comment [{c['when']}] {c['user']} (STAFF, internal): {c['text']}" for c in state['comments']]
            if not state['comments']:
                lines.append("    (no comments on the task)")
    block = None
    if lines:
        block = ("CLICKUP_TASKS (read from ClickUp just now; internal - never quote this to the tenant):\n" + "\n".join(lines))
    return remaining, block


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
    events, ticket_block = check_tickets_before_reminder(events)
    if not events:
        return None   # every due reminder belonged to a task that is already closed in ClickUp
    last_event = events[-1]
    event_type = _batch_event_type(events)
    trigger_messages = sorted((e.message for e in events if e.message_id), key=lambda m: (m.message_timestamp, m.id))
    tenant_events = [e for e in events if e.event_type == AIEvent.TYPE_TENANT_MESSAGE]
    last_message = trigger_messages[-1] if trigger_messages else None
    last_tenant_message = next((e.message for e in reversed(tenant_events) if e.message_id), None)
    live = all(e.send_allowed for e in events) and _should_send_ai_to_group(apartment)
    mode = AIRun.MODE_LIVE if live else AIRun.MODE_TEST
    reply_payload = (tenant_events[-1].payload if tenant_events else None) or {}

    # Answers / plans of this chat that still wait for staff review: the AI sees them and does not repeat them
    pending_block = answer_review.pending_block(conversation_sid)
    outcome = run_agent(
        event_type, conversation_sid, apartment, booking, trigger_messages, mode,
        body_override="\n".join(e.body or '' for e in tenant_events if not e.message_id) or None,
        extra_block="\n\n".join(b for b in (_followup_block(events), ticket_block, pending_block) if b) or None,
    )
    run, parsed, meta = outcome['run'], outcome['parsed'], outcome['meta']

    ai_run = AIRun.objects.create(
        event=last_event, conversation_sid=conversation_sid, message=last_tenant_message or last_message,
        event_type=event_type, mode=mode, model=run.get('model'),
        report_dir=str(outcome['run_dir']), error=run.get('error'),
    )
    meta['run_id'] = ai_run.id
    meta['event_ids'] = [e.id for e in events]

    action_results, delivery = [], {'sent_to_chat': False, 'note': 'run failed - nothing sent'}
    if parsed:
        delivery = deliver(parsed, mode, conversation_sid, booking, last_message, reply_payload, apartment=apartment)
        action_ctx = agent_actions.ActionContext(
            mode, meta, outcome['new_messages_text'], conversation_sid,
            apartment=apartment, booking=booking, ai_run=ai_run,
            staff_in_trigger=any(e.event_type == AIEvent.TYPE_STAFF_MESSAGE for e in events),
        )
        plan_items = plan_actions = None
        if answer_review.review_applies(parsed, last_message, action_ctx):
            # Staff review: nothing is executed now - the actions become a plan in the Telegram alert and are
            # executed when the review window ends (answer_review.apply_plan)
            plan_items, plan_actions = agent_plan.describe(parsed, action_ctx)
            action_results = agent_plan.as_results(plan_items, plan_actions)
            delivery.setdefault('plan_until', delivery.get('hold_until') or answer_review.window_end())
        else:
            action_results = agent_actions.execute_actions(parsed, action_ctx)
        if parsed['review_answer']:
            delivery['note'] = 'NO_ANSWER - staff answered first; the review answer is stored for managers, never sent'
        if last_tenant_message:
            shown_answer, shown_why = parsed['answer'], parsed['why']
            if parsed['review_answer']:
                # Visible in the chat page like a test-mode answer, so managers can compare it with staff's reply
                shown_answer = parsed['review_answer']
                shown_why = f"{REVIEW_ONLY_PREFIX} {parsed['why'] or ''}".strip()
            elif parsed['answer'] and (delivery.get('note') or '').startswith('suppressed'):
                # Live mode, but a manager replied while the AI was working: keep the answer for review only
                shown_why = f"{REVIEW_ONLY_PREFIX} {parsed['why'] or ''}".strip()
            elif delivery.get('held'):
                shown_why = f"{answer_review.pending_prefix(delivery['hold_until'], mode)} {parsed['why'] or ''}".strip()
            _persist_customer_ai_result(
                last_tenant_message.message_sid,
                {'answer': shown_answer, 'why': shown_why},
                sent_to_chat=delivery['sent_to_chat'],
            )

    summary = run_report.write_report(
        outcome['run_dir'], meta, outcome['user_input'], run, parsed, action_results, delivery,
        new_messages_text=outcome['new_messages_text'],
    )
    AIRun.objects.filter(id=ai_run.id).update(
        answer=parsed['answer'] if parsed else None,
        why=parsed['why'] if parsed else None,
        no_answer=bool(parsed and parsed['no_answer']),
        review_answer=parsed['review_answer'] if parsed else None,
        actions=action_results, sent_to_chat=delivery['sent_to_chat'],
        delivery_note=(delivery.get('note') or '')[:255],
        error=run.get('error') or delivery.get('error'),
        session_id=summary['session_id'],
        input_tokens=summary['input_tokens'], output_tokens=summary['output_tokens'],
        cache_read_tokens=summary['cache_read_tokens'], cache_write_tokens=summary['cache_write_tokens'],
        cost_usd=summary['cost_usd'], num_turns=summary['num_turns'],
        tool_calls=len(summary['tool_calls']), duration_ms=summary['duration_ms'],
    )
    ai_run.refresh_from_db()

    if parsed:
        _finish_events(events, AIEvent.STATUS_DONE)
        trigger_text = outcome['new_messages_text']
        if not trigger_messages:
            trigger_text = "⏰ Reminder became due: " + "; ".join(e.body or '' for e in events)
        try:
            ai_run.refresh_from_db()
            team_notify.deliver(action_ctx, ai_run, parsed, action_results, delivery, trigger_text,
                                plan_items=plan_items, plan_actions=plan_actions)
            AIRun.objects.filter(id=ai_run.id).update(actions=action_results)
            run_report._write_json(outcome['run_dir'] / '07_actions.json', action_results)
        except Exception as e:
            report_error(e, "team notification failed", {'run': f"/ai-runs/{ai_run.id}/"})
        answer_review.start(ai_run, parsed, delivery, reply_payload, booking, has_tenant_message=bool(tenant_events),
                            plan_items=plan_items, plan_actions=plan_actions, action_ctx=action_ctx,
                            trigger_text=trigger_text)
        log_info(
            f"AI agent run #{ai_run.id} {event_type} {mode} {meta['apartment']}: "
            f"{'NO_ANSWER' if parsed['no_answer'] else 'ANSWER'}, {len(action_results)} actions, ${summary['cost_usd']:.4f}",
            category='sms',
        )
    else:
        _finish_events(events, AIEvent.STATUS_FAILED, run.get('error'))
        log_ai_error(conversation_sid, "ai_agent", run.get('error') or 'unknown error')
        # The message must still reach a human when the AI is down
        report_error(
            Exception(run.get('error') or 'AI agent run failed'),
            f"run failed - {event_type} needs a human ({meta['apartment']})",
            {
                'tenant': meta.get('tenant'), 'conversation_sid': conversation_sid,
                'message': outcome['new_messages_text'], 'report': f"/ai-runs/{ai_run.id}/",
            },
        )
    return ai_run


def _batch_wait_seconds(batch):
    from mysite.models import AIEvent
    if any(_EMERGENCY.search(e.body or '') for e in batch if e.event_type == AIEvent.TYPE_TENANT_MESSAGE):
        return 0
    if all(e.event_type == AIEvent.TYPE_FOLLOWUP_DUE for e in batch):
        return 0
    if all((e.payload or {}).get('source') == 'chat_ui' for e in batch):
        return config.chat_ui_debounce_seconds()
    return config.debounce_seconds()


def claim_next_batch():
    """
    Picks the oldest pending conversation whose newest pending event is older than its wait
    time, marks its events running and returns them (oldest first). Returns [] when idle.
    """
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
    from mysite.models import AIEvent

    stale_before = timezone.now() - timedelta(seconds=config.run_timeout_seconds() + 120)
    stale = AIEvent.objects.filter(status=AIEvent.STATUS_RUNNING, started_at__lt=stale_before)
    failed = stale.filter(attempts__gte=max_attempts).update(
        status=AIEvent.STATUS_FAILED, error='worker stopped while running', finished_at=timezone.now(),
    )
    requeued = stale.filter(attempts__lt=max_attempts).update(status=AIEvent.STATUS_PENDING)
    if failed or requeued:
        log_warning(f"AI agent: {requeued} stale events requeued, {failed} failed", category='sms')
