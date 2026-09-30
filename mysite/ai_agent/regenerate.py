"""
"Generate AI" / "Generate all AI answers" on the chat page while the Claude backend is on.

A stored tenant message is run through the agent like ai_agent_replay: nothing is sent, no action is executed or
notified. The answer is stored on the message (as the legacy regenerate did) and an AIRun with a full report is kept.
A Claude run takes longer than a web request may, so it runs in a background thread; the page polls status().
"""
import threading

from django.db import close_old_connections, connection

from mysite.unified_logger import log_warning

PENDING_NOTE = 'regenerating from the chat page...'
DONE_NOTE = 'regenerated from the chat page - never sent, actions not executed'
SENT_KEPT_PREFIX = "[REGENERATED - not stored: the AI answer already sent for this message is kept]"


def start(messages):
    """messages: tenant TwilioMessages of one conversation, oldest first. Returns the AIRun ids (one per message)."""
    from mysite.models import AIEvent, AIRun

    runs = [
        AIRun.objects.create(
            conversation_sid=m.conversation_sid, message=m, event_type=AIEvent.TYPE_TENANT_MESSAGE,
            mode=AIRun.MODE_TEST, delivery_note=PENDING_NOTE,
        )
        for m in messages
    ]
    pairs = [(run.id, m.id) for run, m in zip(runs, messages)]
    threading.Thread(target=_run_all, args=(pairs,), daemon=True, name='ai-regenerate').start()
    return [run_id for run_id, _ in pairs]


def _run_all(pairs):
    from mysite.models import AIRun

    close_old_connections()
    try:
        for run_id, message_id in pairs:
            try:
                _run_one(run_id, message_id)
            except Exception as e:
                log_warning(f"AI regenerate run #{run_id} failed: {e}", category='sms')
                AIRun.objects.filter(id=run_id).update(error=str(e)[:2000], delivery_note='regenerate failed')
    finally:
        connection.close()


def _run_one(run_id, message_id):
    from mysite.ai_agent import actions as agent_actions
    from mysite.ai_agent import run_report, service
    from mysite.models import AIEvent, AIRun, Apartment, Booking, TwilioMessage

    message = TwilioMessage.objects.select_related('conversation').get(id=message_id)
    conversation = message.conversation
    apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=conversation.apartment_id)
    booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)

    outcome = service.run_agent(
        AIEvent.TYPE_TENANT_MESSAGE, message.conversation_sid, apartment, booking, [message], AIRun.MODE_TEST,
        now=message.message_timestamp,
    )
    run, parsed, meta = outcome['run'], outcome['parsed'], outcome['meta']
    meta.update({'replay': True, 'run_id': run_id, 'source': 'chat page regenerate'})
    action_results = agent_actions.execute_actions(parsed, agent_actions.ActionContext(
        AIRun.MODE_TEST, meta, outcome['new_messages_text'], message.conversation_sid,
        apartment=apartment, booking=booking, persist=False, notify=False,
    )) if parsed else []
    delivery = {'sent_to_chat': False, 'note': DONE_NOTE if parsed else 'run failed - nothing stored'}
    summary = run_report.write_report(
        outcome['run_dir'], meta, outcome['user_input'], run, parsed, action_results, delivery,
        new_messages_text=outcome['new_messages_text'],
    )
    AIRun.objects.filter(id=run_id).update(
        model=run.get('model'), report_dir=str(outcome['run_dir']),
        answer=parsed['answer'] if parsed else None,
        why=parsed['why'] if parsed else None,
        no_answer=bool(parsed and parsed['no_answer']),
        review_answer=parsed['review_answer'] if parsed else None,
        actions=action_results, sent_to_chat=False, delivery_note=delivery['note'][:255],
        error=run.get('error'), session_id=summary['session_id'],
        input_tokens=summary['input_tokens'], output_tokens=summary['output_tokens'],
        cache_read_tokens=summary['cache_read_tokens'], cache_write_tokens=summary['cache_write_tokens'],
        cost_usd=summary['cost_usd'], num_turns=summary['num_turns'],
        tool_calls=len(summary['tool_calls']), duration_ms=summary['duration_ms'],
    )
    if not parsed:
        return

    answer, why = parsed['answer'], parsed['why']
    if parsed['review_answer']:
        answer, why = parsed['review_answer'], f"{service.REVIEW_ONLY_PREFIX} {why or ''}".strip()
    message.refresh_from_db()
    if message.ai_sent_to_chat:
        # The sent answer must stay on the message: the agent recognises its own earlier replies in the chat
        # history by it (inputs._ai_answers). The new answer is in the AIRun only.
        return
    message.ai_response, message.ai_response_why, message.ai_sent_to_chat = answer, why, False
    message.save(update_fields=['ai_response', 'ai_response_why', 'ai_sent_to_chat', 'updated_at'])


def status(conversation_sid, run_ids):
    """Polled by the chat page. One dict per run, in the order asked."""
    from mysite.models import AIRun

    runs = {r.id: r for r in AIRun.objects.filter(id__in=run_ids, conversation_sid=conversation_sid).select_related('message')}
    items = []
    for run_id in run_ids:
        run = runs.get(run_id)
        if not run:
            continue
        finished = run.delivery_note != PENDING_NOTE
        item = {'run_id': run.id, 'message_id': run.message_id, 'finished': finished, 'error': run.error}
        if finished and run.message:
            message = run.message
            item.update({
                'ai_response': message.ai_response, 'ai_response_why': message.ai_response_why,
                'ai_sent_to_chat': message.ai_sent_to_chat,
            })
            if message.ai_sent_to_chat and not run.error:
                item.update({
                    'ai_response': run.review_answer or run.answer,
                    'ai_response_why': f"{SENT_KEPT_PREFIX} {run.why or ''}".strip(),
                    'ai_sent_to_chat': False,
                })
            item['run'] = {
                'id': run.id, 'actions': len(run.actions or []), 'tokens': run.total_tokens,
                'cost_usd': float(run.cost_usd or 0), 'url': f'/ai-runs/{run.id}/',
            }
        items.append(item)
    return items
