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
    """Returns True when the message was queued (or skipped as a short acknowledgment), False when queueing failed."""
    try:
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
        # The caller alerts the team (there is no fallback AI)
        report_error(e, "could not queue a tenant message for the AI agent", source='webhook')
        return False


def enqueue_staff_message(conversation_sid, message_sid, body, source='webhook'):
    """
    A manager wrote in a tenant chat: the AI updates issues / follow-ups (normally without replying).
    It is also how the agent learns knowledge from staff (KB_UPDATE). Returns True when queued.
    """
    try:
        from mysite.models import AIEvent
        from mysite.views.messaging import _is_skippable_message

        if _is_skippable_message(body):
            return False
        payload = {'source': source}
        return _enqueue_message(AIEvent.TYPE_STAFF_MESSAGE, conversation_sid, message_sid, body, True, payload) is not None
    except Exception as e:
        report_error(e, "could not queue a staff message for the AI agent", source='webhook')
        return False


def enqueue_notification(conversation_sid, kind, text, booking_id=None, source='sms_notifications'):
    """
    An automatic notification of the daily job (move-in, rent due, ...) is handed to the AI instead of being sent
    blindly: it checks the tenant's chats and the payments first (simple alerts, part 8a). Returns True when queued.
    """
    try:
        from mysite.ai_agent import alerts_v5
        from mysite.models import AIEvent, TwilioConversation
        conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
        AIEvent.objects.create(
            event_type=alerts_v5.NOTIFICATION, conversation=conversation, conversation_sid=conversation_sid, body=text,
            send_allowed=True, payload={'kind': kind, 'booking_id': booking_id, 'source': source},
        )
        return True
    except Exception as e:
        report_error(e, "could not queue an automatic notification for the AI agent", source='command')
        return False


SANDBOX_PREFIX = 'CHSANDBOX'   # the DB-only chats of the sandbox test runner (ai_agent_sandbox)


def fire_due_followups():
    """Due follow-ups become FOLLOWUP_DUE events. Returns how many were fired."""
    from mysite.models import AIEvent, AIFollowUp, TwilioConversation

    from django.db.models import Q
    from mysite.ai_agent import alerts_v5

    fired = 0
    due = AIFollowUp.objects.filter(status=AIFollowUp.STATUS_PENDING, due_at__lte=timezone.now()).select_related('issue')
    # The sandbox story's reminders belong to the test runner (it fires them itself, at story time); only a test
    # reminder asked for in Telegram (🧪 Send test reminder) is fired here, so it reaches the real group
    due = due.exclude(Q(conversation_sid__startswith=SANDBOX_PREFIX) & ~Q(issue__summary__startswith=alerts_v5.TEST_REMINDER))
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
        # Legal / contract question: the answer is a suggestion that waits for a manager, never sent by the timer
        'needs_confirmation': bool(output.get('needs_manager_confirmation')) and not no_answer,
        'contract_basis': str(output.get('contract_basis') or '').strip() or None,
        'triage': _parse_triage(output),
    }


TRIAGE_TYPE_LABELS = {
    'URGENT_PROPERTY_OR_ACCESS': '1 Urgent property / access', 'ROUTINE_MAINTENANCE': '2 Routine maintenance',
    'TIME_SENSITIVE_LOGISTICS': '3 Time-sensitive logistics', 'PAYMENTS_AND_DOCUMENTS': '4 Payments / documents',
    'BOOKING_AND_EXTENSION': '5 Booking / extension', 'PROPERTY_FACTS': '6 Property facts',
    'COMPLAINT_OR_DISPUTE': '7 Complaint / dispute', 'FOLLOW_UP_REQUEST': '8 Follow-up request', 'NO_REPLY': 'No reply needed',
}


def _parse_triage(output):
    """The AI's classification of the event (client spec v4). Missing fields stay empty - older outputs have none."""
    def text(key, limit=500):
        return str(output.get(key) or '').strip()[:limit]

    def strings(key):
        value = output.get(key)
        return [str(v).strip()[:300] for v in value if str(v).strip()] if isinstance(value, list) else []

    priority = text('priority')
    return {
        'primary_type': text('primary_type', 40) or None,
        'secondary_types': strings('secondary_types'),
        'priority': priority if priority in ('routine', 'urgent', 'emergency') else None,
        'case_status': text('case_status', 20) or None,
        'issue_refs': strings('issue_refs'),
        'owner': text('owner', 50) or None,
        'next_action': text('next_action') or None,
        'tenant_deadline': text('tenant_deadline', 40) or None,
        'verified_facts': strings('verified_facts'),
        'uncertainties': strings('uncertainties'),
        'no_reply_reason': text('no_reply_reason') or None,
    }


def parse_tenant_deadline(value):
    """'YYYY-MM-DD HH:MM' (property time) -> aware datetime, or None."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(value or '').strip()[:16], fmt).replace(tzinfo=ZoneInfo(config.PROPERTY_TIMEZONE))
        except ValueError:
            continue
    return None


def _tenant_chats_line(conversation_sid):
    from mysite import conversation_groups
    try:
        return conversation_groups.summary_line(conversation_sid)
    except Exception:
        return None


def _prompt_versions(system_source):
    """{prompt_key: last edit time} of the AIManagement prompts this run used (for the run report)."""
    from mysite.models import AIManagement
    keys = [part[3:] for part in system_source.split(' + ') if part.startswith('DB:')]
    return {key: f"{updated:%Y-%m-%d %H:%M:%S}" for key, updated in
            AIManagement.objects.filter(prompt_key__in=keys).values_list('prompt_key', 'updated_at')}


def _agent_images(media_ids):
    """The photos (by TwilioMessageMedia id) prepared for the model; ones that can't be decoded are dropped."""
    from mysite.models import TwilioMessageMedia
    from mysite.twilio_media import image_for_model

    if not media_ids:
        return []
    by_id = TwilioMessageMedia.objects.in_bulk(media_ids)
    images = []
    for media_id in media_ids:
        media = by_id.get(media_id)
        prepared = image_for_model(media) if media else None
        if prepared:
            images.append({'media_id': media_id, **prepared})
    return images


def run_agent(event_type, conversation_sid, apartment, booking, trigger_messages, mode,
              body_override=None, now=None, extra_block=None):
    """Runs Claude once. Returns a dict with everything needed for delivery and for the report."""
    started_at = timezone.now()
    run_dir = run_report.new_run_dir(conversation_sid, apartment, event_type)
    system_prompt, system_source = prompts.get_system_prompt(apartment)
    user_input, sources = inputs.build_agent_input(
        event_type, conversation_sid, apartment, booking, trigger_messages,
        body_override=body_override, now=now, extra_block=extra_block,
    )
    until_id = trigger_messages[-1].id if trigger_messages else None
    images = _agent_images(sources.get('agent_images'))
    sources['agent_images'] = [img['media_id'] for img in images]
    extra = {'images': images} if images else {}
    run = runner.run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=until_id, **extra)

    tenant = getattr(booking, 'tenant', None)
    meta = {
        'started_at': started_at.astimezone(inputs._team_tz()).strftime('%Y-%m-%d %H:%M:%S %Z'),
        'event_type': event_type,
        'mode': mode,
        'conversation_sid': conversation_sid,
        'apartment': run_report.apartment_label(apartment),
        'apartment_id': getattr(apartment, 'id', None),
        'booking_id': getattr(booking, 'id', None),
        'tenant': getattr(tenant, 'full_name', None),
        'tenant_chats': _tenant_chats_line(conversation_sid),
        'trigger_message_ids': [m.id for m in trigger_messages],
        'system_prompt_source': system_source,
        'prompt_versions': _prompt_versions(system_source),
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


def _ai_emergency(parsed):
    """The AI itself rated it an emergency (an action or its triage) - not just a word in the message."""
    if (parsed.get('triage') or {}).get('priority') == 'emergency':
        return True
    return any(isinstance(a, dict) and a.get('priority') == 'emergency' for a in parsed.get('actions') or [])


def _is_emergency(parsed, last_message):
    """Emergency answers are never held for review."""
    if any(isinstance(a, dict) and a.get('priority') == 'emergency' for a in parsed.get('actions') or []):
        return True
    return bool(_EMERGENCY.search(getattr(last_message, 'body', '') or ''))


def send_answer(conversation_sid, answer, reply_author, sender_phone, booking=None, any_hour=False, crm_only=False):
    """Sends one answer to the tenant group chat. Returns {'sent_to_chat', 'note'}. Held for the 08:00-21:00 SMS hours,
    except any_hour: the safety answer of a real emergency goes out at once, day or night (user decision 2026-10-08).
    crm_only (📝 Send Answer (CRM)): written into the CRM chat only, no SMS."""
    from mysite.group_chat_logger import log_ai_customer_sent
    from mysite.views.messaging import TWILIO_ASSISTANT_PHONE, send_messsage_by_sid, send_tenant_sms_gated, write_to_crm_chat

    if crm_only:
        if write_to_crm_chat(conversation_sid, reply_author or 'Virtual Assistant', answer) is None:
            return {'sent_to_chat': False, 'note': 'could not write the answer into the CRM chat',
                    'error': 'CRM chat write failed'}
        return {'sent_to_chat': True, 'note': 'written into the CRM chat only (no SMS)'}
    try:
        if any_hour:
            send_messsage_by_sid(conversation_sid, reply_author or 'Virtual Assistant', answer,
                                 sender_phone or TWILIO_ASSISTANT_PHONE, None)
            sent = True
        else:
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


def deliver(parsed, mode, conversation_sid, booking, last_message, payload, apartment=None, send_to=None):
    """
    Sends the answer to the Twilio group chat in live mode, or holds it for the staff review window
    (answer_review.py). Returns {'sent_to_chat', 'note'} (+ 'held': True when held).
    send_to: another chat of the same tenant to send to (a reminder goes to the chat the tenant wrote in last);
    it is returned as delivery['send_to'] so a held answer is released to the same chat.
    """
    result = _deliver(parsed, mode, conversation_sid, booking, last_message, payload, apartment,
                      send_to or conversation_sid)
    if send_to and send_to != conversation_sid:
        result['send_to'] = send_to
        result['note'] = f"{result.get('note') or ''} (to the tenant's main chat, where they wrote last)".strip()
    return result


def _deliver(parsed, mode, conversation_sid, booking, last_message, payload, apartment, send_to):
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
    if not _is_emergency(parsed, last_message):
        # Nothing is sent without a manager pressing Send (simple alerts) - no timer; test mode alike
        legal = bool(parsed.get('needs_confirmation'))
        return {'sent_to_chat': False, 'held': True, 'confirm': legal,
                'note': ('LEGAL question - ' if legal else '') + 'waits for a manager to approve it in Telegram, never '
                        'sent without approval' + ('' if live else ' (test mode - never sent to Twilio)')}
    if not live:
        return {'sent_to_chat': False, 'note': 'test mode - stored in DB, not sent to Twilio'}
    # Only an emergency gets here (everything else waits for a press): its safety answer goes out at once, any hour
    return send_answer(send_to, answer, payload.get('reply_author'), payload.get('sender_phone'), booking, any_hour=True)


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


def check_tickets_before_reminder(events, states=None):
    """
    A reminder became due: look at the ClickUp task of its issue first (status + latest comments).
    - task closed  -> (user decision 2026-09-30) the other reminders of the issue stop and Claude proposes telling the
                      tenant it is done ("... if you still have any problem, just let us know") + resolving the issue;
                      like every proposal it waits for a manager's approval. The old quiet close (no tenant message)
                      is kept with AI_AGENT_APPROVAL=timer.
    - task open    -> its status and comments go to Claude, which decides whether a reminder is still needed
    Returns (events_still_needing_claude, ticket_block_text_or_None). states (a dict): filled with {issue id: task state}.
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
        if states is not None:
            states[issue.id] = state
        if state.get('closed') and issue.is_open:
            issue.followups.filter(status=AIFollowUp.STATUS_PENDING).update(
                status=AIFollowUp.STATUS_CANCELLED, status_note='ClickUp task closed', updated_at=timezone.now(),
            )
            remaining.append(event)
            lines.append(
                f"- ticket_id: t-{issue.id} | issue_id: {issue.public_id} | ClickUp task CLOSED (status: {state['status']}) - "
                f"staff marked it done. PROPOSE: answer the tenant in one or two short sentences that the team marked "
                f"\"{issue.summary}\" as done and that they should just let us know if they still have any problem "
                f"(same language as the chat), and UPDATE_ISSUE_STATE {issue.public_id} RESOLVED. Nothing else, no "
                f"new reminder. Both wait for a manager's approval.")
            continue
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
    ticket_states = {}
    events, ticket_block = check_tickets_before_reminder(events, ticket_states)
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
    # (explicit approval: a new tenant / staff message replaces them - the AI repeats what is still needed)
    from mysite.ai_agent import after_hours, calls, cases
    pending_block = answer_review.pending_block(conversation_sid, event_type)
    ack_block = after_hours.input_block(events)
    from mysite.ai_agent import alerts_v5
    notification = alerts_v5.notification_of(events)
    # A reminder that became due (no new message in the batch): the simple REMINDER alert (C1-C8, D5, D6)
    reminder = alerts_v5.reminder_of(events, ticket_states) if event_type == AIEvent.TYPE_FOLLOWUP_DUE else None
    outcome = run_agent(
        event_type, conversation_sid, apartment, booking, trigger_messages, mode,
        body_override="\n".join(e.body or '' for e in tenant_events if not e.message_id) or None,
        extra_block="\n\n".join(b for b in (_followup_block(events), ticket_block, pending_block, ack_block,
                                             alerts_v5.notification_block(events) if notification else None,
                                             alerts_v5.reminder_block(reminder) if reminder else None) if b) or None,
    )
    run, parsed, meta = outcome['run'], outcome['parsed'], outcome['meta']
    urgent_reply = any(after_hours.is_urgent(e.body) for e in tenant_events)
    if parsed:
        if event_type == AIEvent.TYPE_STAFF_MESSAGE and parsed.get('answer'):
            # A team member just wrote to the tenant: the AI never adds its own message on top - it would repeat what
            # the tenant already has (user, 2026-10-08). Kept as the review answer, never sent.
            parsed['review_answer'], parsed['answer'], parsed['no_answer'] = parsed['answer'], None, True
        if urgent_reply and (parsed['triage'].get('priority') or 'routine') == 'routine':
            parsed['triage']['priority'] = 'urgent'      # the tenant replied URGENT: the card says so
    meta['urgent_reply'] = urgent_reply
    meta['after_hours_ack_sent'] = any(a.status in ('sent', 'would_send') for a in after_hours.acks_for(events))
    if parsed and parsed['answer'] and tenant_events and answer_review.confirmation_pending(conversation_sid):
        # The new answer replaces a legal draft that waits for a manager: it waits for a manager too, so the legal
        # answer can not slip out through the next message's 15-minute timer
        parsed['needs_confirmation'] = True

    ai_run = AIRun.objects.create(
        event=last_event, conversation_sid=conversation_sid, message=last_tenant_message or last_message,
        event_type=event_type, mode=mode, model=run.get('model'),
        report_dir=str(outcome['run_dir']), error=run.get('error'),
    )
    meta['run_id'] = ai_run.id
    meta['event_ids'] = [e.id for e in events]
    AIRun.objects.filter(id=ai_run.id).update(triage=(parsed or {}).get('triage') or {})
    ai_run.triage = (parsed or {}).get('triage') or {}
    first_at = min(e.created_at for e in events)
    try:
        if tenant_events and ((parsed and _ai_emergency(parsed)) or (not parsed and _EMERGENCY.search(
                "\n".join(e.body or '' for e in tenant_events)))):
            # Emergency (or the run failed on an emergency-looking message): phone the on-call person at any hour
            calls.request_call(conversation_sid, "emergency: " + " ".join((e.body or '') for e in tenant_events)[:160],
                               mode, ai_run=ai_run)
        if parsed:
            cases.note_run(ai_run, parsed, bool(tenant_events))
    except Exception as e:
        report_error(e, "case tracking / alert call failed (the run goes on)", {'run': f"/ai-runs/{ai_run.id}/"})
    meta['after_hours_line'] = after_hours.card_line(events)
    meta['call_line'] = calls.card_line(conversation_sid, first_at - timedelta(minutes=1))

    action_results, delivery = [], {'sent_to_chat': False, 'note': 'run failed - nothing sent'}
    if parsed:
        # A reply goes to the chat the message came from; a reminder / ticket update (no tenant or staff
        # message in the batch) goes to the tenant's main chat - the one they wrote in last
        send_to = None
        if not any(e.event_type in (AIEvent.TYPE_TENANT_MESSAGE, AIEvent.TYPE_STAFF_MESSAGE) for e in events):
            from mysite import conversation_groups
            send_to = conversation_groups.main_sid(conversation_sid)
        if notification:
            # The AI decided whether the planned notification is still needed: sent now, or it waits for a press
            meta['notification'] = alerts_v5.settle_notification(notification, parsed, mode)
            delivery = alerts_v5.deliver_notification(meta['notification'], parsed, mode, send_to or conversation_sid, booking)
        elif reminder:
            # The AI re-checked the reminder: already done -> closed quietly; a live tenant reminder -> sent now
            meta['reminder'] = alerts_v5.settle_reminder(reminder, parsed, mode)
            delivery = alerts_v5.deliver_reminder(
                meta['reminder'], parsed, mode, send_to or conversation_sid, booking,
                lambda: deliver(parsed, mode, conversation_sid, booking, last_message, reply_payload, apartment=apartment,
                                send_to=send_to))
            if not meta['reminder']['needed']:
                alerts_v5.close_done_reminder(conversation_sid, meta['reminder'])
        else:
            delivery = deliver(parsed, mode, conversation_sid, booking, last_message, reply_payload, apartment=apartment,
                               send_to=send_to)
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
            from mysite.ai_agent import alerts_v5
            if alerts_v5.applies(meta):
                alerts_v5.prepare(plan_actions, action_ctx, parsed.get('triage'))   # reminders exist before the alert shows them
            action_results = alerts_v5.results(agent_plan.as_results(plan_items, plan_actions), plan_actions)
        else:
            action_results = agent_actions.execute_actions(parsed, action_ctx)
            delivery['executed_now'] = True
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
                shown_why = (f"{answer_review.pending_prefix(mode, delivery.get('confirm'))} "
                             f"{parsed['why'] or ''}").strip()
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
        triage=(parsed or {}).get('triage') or {},
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
        try:
            ai_run.refresh_from_db()
            if plan_items is not None and (ai_run.review or {}).get('style') == 'v5':
                from mysite.ai_agent import alerts_v5
                alerts_v5.after_post(ai_run, bool(tenant_events))
            if plan_items is None:   # executed at once (emergency / review off)
                cases.after_apply(ai_run, {k: i.id for k, i in action_ctx.temp_ids.items()}, bool(tenant_events))
                if parsed['answer'] and (delivery.get('sent_to_chat') or mode == AIRun.MODE_TEST) \
                        and not (delivery.get('note') or '').startswith(('suppressed', 'BLOCKED')):
                    cases.mark_answer_sent(ai_run, delivery.get('note') or '')
            if meta.get('after_hours_ack_sent'):
                cases.mark_acknowledged(conversation_sid, cases.run_issues(ai_run))
        except Exception as e:
            report_error(e, "case tracking failed (the run itself is done)", {'run': f"/ai-runs/{ai_run.id}/"})
        log_info(
            f"AI agent run #{ai_run.id} {event_type} {mode} {meta['apartment']}: "
            f"{'NO_ANSWER' if parsed['no_answer'] else 'ANSWER'}, {len(action_results)} actions, ${summary['cost_usd']:.4f}",
            category='sms',
        )
    else:
        _finish_events(events, AIEvent.STATUS_FAILED, run.get('error'))
        log_ai_error(conversation_sid, "ai_agent", run.get('error') or 'unknown error')
        if notification and live:
            # A notification is never lost because the AI is down: the template goes out as it always did
            from mysite import conversation_groups
            send_answer(conversation_groups.main_sid(conversation_sid), notification['template'], 'Virtual Assistant', None, booking)
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
    from mysite.ai_agent import after_hours
    if any(_EMERGENCY.search(e.body or '') or after_hours.is_urgent(e.body) for e in batch
           if e.event_type == AIEvent.TYPE_TENANT_MESSAGE):
        return 0
    if all(e.event_type in (AIEvent.TYPE_FOLLOWUP_DUE, 'NOTIFICATION_DUE') for e in batch):
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
