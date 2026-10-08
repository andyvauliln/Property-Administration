"""
The review state of a run and the Telegram entry points around it (simple alerts, simple_telegram_alerts.md).

Nothing a run proposes happens until someone presses a button under its alert (alerts_v5.py); there is no timer.
Anyone in the AI group may press; the name is logged (review['decisions']). A newer proposal of the same chat (a new
tenant message) makes the older alert OUTDATED: its buttons stop working and the AI proposes again (supersede_older).

Callback data of the alerts: "v5|<code>|<run id>|<arg>" (alerts_v5.handle_callback). Buttons of the old card
("v4|...", before 7 Oct 2026) only answer that the card can not be used any more.
"""
from django.utils import timezone

from mysite.ai_agent.notify import answer_callback, edit_reply_markup
from mysite.unified_logger import log_info


def _models():
    from mysite.models import AIRun
    return AIRun


def _ar():
    from mysite.ai_agent import answer_review
    return answer_review


def _update_review(run, **changes):
    """Saves review keys (the rest is kept); 'version' grows by one when bump=True."""
    from django.db import transaction
    AIRun = _models()
    bump = changes.pop('bump', False)
    with transaction.atomic():
        fresh = AIRun.objects.select_for_update().get(id=run.id)
        review = dict(fresh.review or {})
        review.update(changes)
        if bump:
            review['version'] = review.get('version', 1) + 1
        AIRun.objects.filter(id=run.id).update(review=review)
    run.review = review
    return review


def _append(run, key, value, limit=100):
    review = dict(_models().objects.get(id=run.id).review or {})
    return _update_review(run, **{key: ((review.get(key) or []) + [value])[-limit:]})


def _say(run, text, reply_to=None, reply_markup=None):
    from mysite.ai_agent.notify import send_ai_chat
    ok, note, message_id = send_ai_chat(text, reply_to=reply_to or run.telegram_message_id, reply_markup=reply_markup)
    if message_id:
        _ar().remember_bot_message(run.id, message_id)
    return message_id


def _record(run, author, action, detail=''):
    _append(run, 'decisions', {'by': author, 'at': timezone.now().isoformat(), 'action': action,
                               'version': (run.review or {}).get('version', 1), 'detail': detail[:500]})
    _ar()._write_review_file(run.id)
    log_info(f"AI alert run #{run.id}: {action} by {author}", category='sms')


# ---------------------------------------------------------------------------
# Final check before anything reaches the tenant
# ---------------------------------------------------------------------------

def tenant_wrote_after(run):
    """The tenant's newest message came after this proposal was made: it may be out of date (refreshed one follows)."""
    from mysite import conversation_groups
    from mysite.ai_agent import inputs
    from mysite.models import TwilioMessage
    if not run.message_id:
        return None
    sids = conversation_groups.group_sids(run.conversation_sid)
    later = TwilioMessage.objects.filter(conversation_sid__in=sids, message_timestamp__gt=run.message.message_timestamp) \
        .order_by('message_timestamp')
    ai_answers = inputs._ai_answers(sids)
    return next((m for m in later if inputs.classify_sender(m, ai_answers)[0] == inputs.ROLE_TENANT), None)


def dont_send(run, author):
    """The answer of the run is not sent (a typed "don't send" after its Apply Change press)."""
    AIRun = _models()
    if AIRun.objects.filter(id=run.id, hold_status=AIRun.HOLD_HOLDING).update(
            hold_status=AIRun.HOLD_CANCELLED, delivery_note=f"not sent - {author} said so in Telegram"):
        _ar()._mark_chat(run, f"[NOT SENT: {author} said so in Telegram]")
        return ["❌ Answer NOT sent - the tenant gets nothing from the AI for this message."]
    return ["ℹ The answer was already handled."]


def ask_for_text(run, author):
    """✏️ Edit Answer: the bot asks for the text; the reply to that message is the new answer (alerts_v5, doc E3)."""
    tenant = str(((run.review or {}).get('meta') or {}).get('tenant') or 'the tenant').split()[0]
    message_id = _say(run, f"✏️ {author}, reply to THIS message with the correct answer for {tenant}.",
                      reply_markup={'force_reply': True, 'selective': False, 'input_field_placeholder': 'Text for the tenant'})
    if message_id:
        _append(run, 'replace_prompts', int(message_id))


# ---------------------------------------------------------------------------
# Telegram entry points (answer_review.poll_telegram)
# ---------------------------------------------------------------------------

def handle_callback(callback):
    """One button press. Always answers the callback (else the button keeps spinning)."""
    parts = (callback.get('data') or '').split('|')
    if parts[0] == 'v5':
        from mysite.ai_agent import alerts_v5
        return alerts_v5.handle_callback(callback)
    answer_callback(callback.get('id'), 'This card has the old format and can not be used any more - see the newer alert.',
                    alert=True)
    message = callback.get('message') or {}
    if message.get('message_id'):
        edit_reply_markup(message['message_id'], None)


def handle_prompt_reply(run, parent_id, text, author):
    """A reply to the bot's "reply with the text" request. Returns True when it was one."""
    if int(parent_id) in ((run.review or {}).get('replace_prompts') or []):
        # The written text is shown as the new answer with its own Send button (doc E3)
        from mysite.ai_agent import alerts_v5
        alerts_v5.handle_reply(run, text.strip(), author, timezone.now(), reply_to=int(parent_id), written=True)
        return True
    return False


def newest_proposal(run):
    """The run whose proposal replaced this stale one (following the chain), or the run itself."""
    AIRun = _models()
    seen = set()
    while (run.review or {}).get('stale') and run.id not in seen:
        seen.add(run.id)
        newer = AIRun.objects.filter(id=run.review['stale']).first()
        if not newer:
            break
        run = newer
    return run


# ---------------------------------------------------------------------------
# Newer proposal -> older alerts are outdated
# ---------------------------------------------------------------------------

def supersede_older(ai_run):
    """
    A new tenant message produced a new proposal: the older proposals of this chat that still wait (answer and plan)
    can no longer be approved; the AI was told to repeat what is still needed (answer_review.pending_block). A team
    message does not replace the earlier alert - its task / reminder buttons stay valid; only an AI answer that still
    waited is not needed any more: the team answered (A18).
    """
    from django.db.models import Q
    from mysite.ai_agent import alerts_v5
    ar, AIRun = _ar(), _models()
    if ai_run.event_type == 'STAFF_MESSAGE':
        return alerts_v5.answered_by_team(ai_run)
    older = AIRun.objects.filter(conversation_sid=ai_run.conversation_sid, id__lt=ai_run.id).filter(
        Q(hold_status=AIRun.HOLD_HOLDING) | Q(review__plan__status='pending'))
    for run in older:
        changed = AIRun.objects.filter(id=run.id, hold_status=AIRun.HOLD_HOLDING).update(
            hold_status=AIRun.HOLD_SUPERSEDED, delivery_note=f'not sent - replaced by the newer proposal of run #{ai_run.id}')
        if changed:
            ar._mark_chat(run, f"[REPLACED by the newer AI proposal of run #{ai_run.id} - not sent]")
        run.refresh_from_db()
        plan = ar._plan(run)
        if plan.get('status') == 'pending':
            plan.update(status='superseded', superseded_by=ai_run.id)
            ar._save_plan(run, plan)
        _update_review(run, stale=ai_run.id)
        if (run.review or {}).get('style') == 'v5':
            alerts_v5.mark_outdated(run, ai_run)   # "⚠️ Outdated - Vera wrote again 14:50, see the newer alert", no buttons
        elif run.telegram_message_id:
            edit_reply_markup(run.telegram_message_id, None)
        ar._write_review_file(run.id)
