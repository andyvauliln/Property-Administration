"""
Buttons on the Telegram card (client spec v4, user decisions 2026-09-30). Nothing a run proposes happens until
someone presses a button (or replies "ok"); there is no timer. Anyone in the AI group may press; the name is logged.

  ✅ Approve all      send the proposed answer + do the ticked plan items
  ✏️ Replace          the bot asks for the text; the reply becomes a DRAFT card -> "✅ Send this" sends it
  ❌ Don't send       the answer is not sent; the plan still waits for its own decision
  👤 I'll handle      staff take it over: the answer is not sent, only the new issue(s) are opened and the AI stays out
                      of them (no drafts, reminders, ClickUp changes) until "🤖 Give back to AI" or they are closed
  🛑 Stop all         nothing at all
  ☑ / ☐ N. ...       tick / untick one plan item (unticking a new issue unticks what depends on it)
  ✅ Apply ticked      only the ticked plan items (the answer keeps waiting)

After a decision the bot posts what it did and asks for an optional NOTE: a reply to that message is saved as a case
note the AI sees on every later run of the chat. Typed replies keep working as before (answer_review.handle_reply):
questions, corrections (they become a draft), plan edits, ClickUp tasks, facts, lessons, "test".

Callback data: "v4|<code>|<run id>|<version>|<arg>". The version grows with every change of the proposal (checkbox,
plan edit, draft), so a press on an outdated keyboard is refused. A card replaced by a newer proposal of the same chat
(a new tenant or staff message) is STALE: its buttons are removed and presses are refused.
"""
import re

from django.utils import timezone

from mysite.ai_agent import config
from mysite.ai_agent.notify import ai_chat_id, answer_callback, edit_reply_markup
from mysite.unified_logger import log_info

PREFIX = 'v4'
NOTE_HINT = "📝 Optional: REPLY to this message with a note on how to handle it - the AI will follow it."


def _models():
    from mysite.models import AIRun
    return AIRun


def _ar():
    from mysite.ai_agent import answer_review
    return answer_review


def _btn(text, code, run_id, version, arg=''):
    return {'text': text[:64], 'callback_data': f"{PREFIX}|{code}|{run_id}|{version}|{arg}"[:64]}


def _item_label(item):
    first = re.sub(r'\s+', ' ', item['lines'][0]).strip()
    return first[:46] + ('…' if len(first) > 46 else '')


def keyboard(run_id, version, answer_waiting, plan_items, plan_pending):
    """The card's buttons for the current state, or None when nothing waits for a decision."""
    changes = [i for i in plan_items or [] if i.get('kind') == 'change' and i.get('n')] if plan_pending else []
    rows = []
    if answer_waiting:
        rows.append([_btn('✅ Approve all', 'a', run_id, version), _btn('✏️ Replace', 'r', run_id, version),
                     _btn("❌ Don't send", 'x', run_id, version)])
    elif changes:
        rows.append([_btn('✅ Apply ticked items', 'p', run_id, version)])
    if answer_waiting or changes:
        rows.append([_btn("👤 I'll handle", 'h', run_id, version), _btn('🛑 Stop all', 's', run_id, version)])
    for item in changes:
        rows.append([_btn(f"{'☐' if item.get('removed_by') else '☑'} {item['n']}. {_item_label(item)}", 't', run_id,
                          version, item['n'])])
    if answer_waiting and changes:
        rows.append([_btn('▶ Apply ticked items only (answer keeps waiting)', 'p', run_id, version)])
    return {'inline_keyboard': rows} if rows else None


def give_back_keyboard(run_id, issues):
    rows = [[_btn(f"🤖 Give {i.public_id} back to AI", 'g', run_id, 0, i.id)] for i in issues if i.handled_by]
    return {'inline_keyboard': rows} if rows else None


def keyboard_for(run):
    review = run.review or {}
    if review.get('style') == 'v5':
        from mysite.ai_agent import alerts_v5
        return alerts_v5.keyboard_for(run)
    plan = review.get('plan') or {}
    return keyboard(run.id, review.get('version', 1), run.hold_status == _models().HOLD_HOLDING,
                    plan.get('items'), plan.get('status') == 'pending')


def refresh_card(run):
    """Puts the buttons of the run's card in line with its state (removed when nothing waits any more)."""
    if run.telegram_message_id:
        edit_reply_markup(run.telegram_message_id, keyboard_for(run))


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
    log_info(f"AI card run #{run.id}: {action} by {author}", category='sms')


def _confirm(run, text, reply_markup=None):
    """Thread message after a decision, with the note hint; replies to it are saved as notes."""
    message_id = _say(run, f"{text}\n\n{NOTE_HINT}", reply_markup=reply_markup)
    if message_id:
        _append(run, 'note_prompts', int(message_id))
    return message_id


# ---------------------------------------------------------------------------
# Final checks before anything reaches the tenant
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


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

def approve_all(run, author):
    ar, AIRun = _ar(), _models()
    lines = []
    if run.hold_status == AIRun.HOLD_HOLDING:
        result = ar._claim_and_release(run, run.answer, AIRun.HOLD_SENT, f"approved by {author} in Telegram", announce=False)
        lines.append(ar._release_line(result, "✅ Answer sent to the tenant as written." if run.mode == AIRun.MODE_LIVE
                                      else "✅ Answer approved (TEST: it would be sent now - nothing sent to Twilio)."))
    run.refresh_from_db()
    if ar._plan(run).get('status') == 'pending':
        lines += ["Plan (ticked items):"] + ar.apply_plan(run, f"approved by {author}")
    return lines


def dont_send(run, author):
    AIRun = _models()
    if AIRun.objects.filter(id=run.id, hold_status=AIRun.HOLD_HOLDING).update(
            hold_status=AIRun.HOLD_CANCELLED, delivery_note=f"not sent - {author} pressed Don't send in Telegram"):
        _ar()._mark_chat(run, f"[NOT SENT: {author} pressed Don't send in Telegram]")
        run.refresh_from_db()
        pending = _ar()._plan(run).get('status') == 'pending'
        return ["❌ Answer NOT sent - the tenant gets nothing from the AI for this message."
                + (" The plan still waits: tick what is needed and press ✅ Apply ticked items, or 🛑 Stop all." if pending else "")]
    return ["ℹ The answer was already handled."]


def stop_all(run, author):
    lines = dont_send(run, author) if run.answer else []
    lines.append("🛑 Plan cancelled: nothing of it will be done." if _ar().cancel_plan(run, author)
                 else "ℹ No plan waiting.")
    return lines


def apply_ticked(run, author):
    ar = _ar()
    if ar._plan(run).get('status') != 'pending':
        return ["ℹ No plan waiting."]
    return ["Plan (ticked items):"] + ar.apply_plan(run, f"ticked items approved by {author}")


def toggle(run, n, author):
    """Ticks / unticks plan item n. Unticking a new issue unticks the items that only exist for it. Returns a notice."""
    ar = _ar()
    plan = ar._plan(run)
    if plan.get('status') != 'pending':
        return "No plan waiting any more"
    item = next((i for i in plan['items'] if i.get('n') == n), None)
    if not item:
        return f"There is no item {n}"
    action = plan['actions'][item['idx']]
    group = [action]
    if action.get('type') == 'CREATE_ISSUE' and action.get('temp_id'):
        group += [a for a in plan['actions'] if isinstance(a, dict) and a is not action
                  and action['temp_id'] in (a.get('issue_id'), a.get('ticket_id'))]
    untick = not action.get('removed_by')
    for a in group:
        if untick:
            a['removed_by'] = f"{author} (unticked)"
        else:
            a.pop('removed_by', None)
    ar._save_plan(run, ar._redescribe(run, plan))
    _update_review(run, bump=True)
    _append(run, 'decisions', {'by': author, 'at': timezone.now().isoformat(), 'action': f"{'untick' if untick else 'tick'} {n}"})
    refresh_card(run)
    return f"{'Unticked' if untick else 'Ticked'} {n}" + (f" (+{len(group) - 1} depending item(s))" if len(group) > 1 else "")


def take_over(run, author):
    """I'll handle: no answer, only the new issues are opened, then the AI stays out of every issue of this card."""
    from mysite.ai_agent import cases
    from mysite.models import AIIssue
    ar, AIRun = _ar(), _models()
    lines = []
    if AIRun.objects.filter(id=run.id, hold_status=AIRun.HOLD_HOLDING).update(
            hold_status=AIRun.HOLD_CANCELLED, delivery_note=f"not sent - {author} handles it (I'll handle)"):
        ar._mark_chat(run, f"[NOT SENT: {author} handles it - I'll handle]")
        lines.append("Answer NOT sent - you reply to the tenant yourself.")
    run.refresh_from_db()
    plan = ar._plan(run)
    if plan.get('status') == 'pending':
        for action in plan['actions']:
            if isinstance(action, dict) and action.get('type') != 'CREATE_ISSUE':
                action['removed_by'] = f"{author} (I'll handle)"
        ar._save_plan(run, plan)
        ar.apply_plan(run, f"I'll handle by {author}: only the new issue(s) opened")
        run.refresh_from_db()
    issues = [i for i in cases.run_issues(run) if i.is_open]
    if not issues:
        conversation = ar._conversation(run)
        issues = [AIIssue.objects.create(
            conversation_sid=run.conversation_sid, apartment=getattr(conversation, 'apartment', None),
            booking=getattr(conversation, 'booking', None), mode=run.mode, created_by_run=run,
            summary=(re.sub(r'\s+', ' ', ar._tenant_text(run)).strip()[:200] or 'Handled by staff'),
            state=AIIssue.STATE_STAFF_HANDLING, telegram_thread_message_id=run.telegram_message_id,
        )]
    stopped = cases.take_over(issues, author)
    lines.append(f"👤 {author} handles " + ", ".join(f"{i.public_id} \"{i.summary[:60]}\"" for i in issues)
                 + ". The AI stays out of it (no replies, reminders or ClickUp changes)"
                 + (f"; {stopped} reminder(s) stopped" if stopped else "")
                 + ". Press 🤖 Give back to AI below to hand it back.")
    return lines, issues


def give_back(run, issue_id, author):
    from mysite.ai_agent import cases
    from mysite.models import AIIssue
    issue = AIIssue.objects.filter(id=issue_id).first()
    if not issue:
        return "That issue does not exist any more"
    if not cases.give_back(issue, author):
        return f"{issue.public_id} is not handled by staff"
    _say(run, f"🤖 {author} gave {issue.public_id} \"{issue.summary[:80]}\" back to the AI - it handles new messages "
              f"about it again.")
    return f"{issue.public_id} is the AI's again"


# ---------------------------------------------------------------------------
# Replace -> draft
# ---------------------------------------------------------------------------

def ask_for_text(run, author):
    review = run.review or {}
    if review.get('style') == 'v5':
        tenant = str((review.get('meta') or {}).get('tenant') or 'the tenant').split()[0]
        ask = f"✏️ {author}, reply to THIS message with the correct answer for {tenant}."
    else:
        ask = (f"✏️ {author}: REPLY to THIS message with the exact text the tenant should get. It becomes a "
               f"draft first; it is sent only after you press ✅ Send this.")
    message_id = _say(run, ask,
                      reply_markup={'force_reply': True, 'selective': False,
                                    'input_field_placeholder': 'Text for the tenant'})
    if message_id:
        _append(run, 'replace_prompts', int(message_id))


def create_draft(run, text, author, how='written'):
    """A replacement answer waits as a DRAFT card with its own ✅ Send this. Returns a report line."""
    AIRun = _models()
    run.refresh_from_db()
    if run.hold_status != AIRun.HOLD_HOLDING:
        return f"ℹ Too late for a new answer: {_ar()._state_text(run)}."
    review = _update_review(run, bump=True)
    version = review['version']
    draft = {'text': text, 'by': author, 'at': timezone.now().isoformat(), 'version': version, 'status': 'waiting'}
    message_id = _say(run, f"📝 DRAFT by {author} ({how}) - replaces the AI answer, NOT sent yet:\n\"{text[:1500]}\"",
                      reply_markup={'inline_keyboard': [[_btn('✅ Send this', 'd', run.id, version),
                                                         _btn('❌ Discard draft', 'dx', run.id, version)]]})
    draft['message_id'] = message_id
    _update_review(run, draft=draft)
    refresh_card(run)
    return ("📝 Your text is a DRAFT now - press ✅ Send this under it to send it" +
            ("" if run.mode == AIRun.MODE_LIVE else " (TEST: nothing is sent to Twilio)") + ".")


def send_draft(run, author, version):
    ar, AIRun = _ar(), _models()
    draft = (run.review or {}).get('draft') or {}
    if not draft or draft.get('status') != 'waiting' or draft.get('version') != version:
        return ["ℹ This draft is not current any more."]
    result = ar._claim_and_release(run, draft['text'], AIRun.HOLD_CORRECTED,
                                   f"written by {draft['by']}, approved by {author} in Telegram", announce=False)
    line = ar._release_line(result, "✅ Draft sent to the tenant (the AI answer was NOT sent)." if run.mode == AIRun.MODE_LIVE
                            else "✅ Draft approved (TEST: it would be sent now - nothing sent to Twilio).")
    if result and result[0] in (AIRun.HOLD_CORRECTED, AIRun.HOLD_SENT):
        _update_review(run, draft=dict(draft, status='sent', approved_by=author))
    if draft.get('message_id'):
        edit_reply_markup(draft['message_id'], None)
    return [line]


def discard_draft(run, author):
    draft = (run.review or {}).get('draft') or {}
    if draft.get('status') != 'waiting':
        return ["ℹ No draft waiting."]
    _update_review(run, draft=dict(draft, status='discarded', discarded_by=author), bump=True)
    if draft.get('message_id'):
        edit_reply_markup(draft['message_id'], None)
    refresh_card(run)
    return ["🗑 Draft discarded. The AI answer still waits for a decision."]


# ---------------------------------------------------------------------------
# Telegram entry points (answer_review.poll_telegram)
# ---------------------------------------------------------------------------

CODE_NAMES = {'a': 'approve all', 'x': "don't send", 's': 'stop all', 'p': 'apply ticked items', 'h': "I'll handle",
              'r': 'replace', 'd': 'send draft', 'dx': 'discard draft', 't': 'tick / untick', 'g': 'give back to AI'}


def handle_callback(callback):
    """One button press. Always answers the callback (else the button keeps spinning)."""
    ar, AIRun = _ar(), _models()
    callback_id = callback.get('id')
    message = callback.get('message') or {}
    parts = (callback.get('data') or '').split('|')
    author = ar._author(callback.get('from') or {})
    if parts[0] == 'v5':
        from mysite.ai_agent import alerts_v5
        return alerts_v5.handle_callback(callback)
    if len(parts) != 5 or parts[0] != PREFIX or str((message.get('chat') or {}).get('id')) != str(ai_chat_id()):
        answer_callback(callback_id, 'Unknown button')
        return
    code, run_id, arg = parts[1], int(parts[2] or 0), parts[4]
    version = int(parts[3] or 0)
    run = AIRun.objects.filter(id=run_id).first()
    if not run:
        answer_callback(callback_id, 'This card no longer exists', alert=True)
        return
    if code == 'g':
        answer_callback(callback_id, give_back(run, int(arg or 0), author))
        return
    review = run.review or {}
    if review.get('stale'):
        answer_callback(callback_id, f"Replaced by a newer proposal (run #{review['stale']}) - use the newest card.", alert=True)
        edit_reply_markup(message.get('message_id'), None)
        return
    if version != review.get('version', 1):
        answer_callback(callback_id, 'This card changed meanwhile - the buttons are updated now, press again.', alert=True)
        if code in ('d', 'dx'):
            edit_reply_markup(message.get('message_id'), None)
        else:
            refresh_card(run)
        return
    plan_pending = ar._plan(run).get('status') == 'pending'
    if code in ('a', 'x', 's', 'p') and run.hold_status != AIRun.HOLD_HOLDING and not plan_pending:
        answer_callback(callback_id, 'Nothing waits on this card any more.')
        refresh_card(run)
        return
    if code == 't':
        answer_callback(callback_id, toggle(run, int(arg or 0), author))
        return
    if code == 'r':
        answer_callback(callback_id, 'Reply to my message with the text')
        ask_for_text(run, author)
        return
    if code in ('a', 'd') and run.hold_status == AIRun.HOLD_HOLDING:
        newer = tenant_wrote_after(run)
        if newer:
            answer_callback(callback_id, 'NOT sent: the tenant wrote again after this proposal - an updated proposal '
                                         'follows in this thread.', alert=True)
            return
    answer_callback(callback_id, f"{CODE_NAMES.get(code, code)}: working on it...")
    issues = []
    if code == 'a':
        lines = approve_all(run, author)
    elif code == 'x':
        lines = dont_send(run, author)
    elif code == 's':
        lines = stop_all(run, author)
    elif code == 'p':
        lines = apply_ticked(run, author)
    elif code == 'h':
        lines, issues = take_over(run, author)
    elif code == 'd':
        lines = send_draft(run, author, version)
    elif code == 'dx':
        lines = discard_draft(run, author)
    else:
        return
    run.refresh_from_db()
    _record(run, author, CODE_NAMES[code], " / ".join(lines))
    refresh_card(run)
    _confirm(run, f"{'👤' if code == 'h' else '🧾'} {author} pressed {CODE_NAMES[code].upper()}:\n" + "\n".join(lines),
             reply_markup=give_back_keyboard(run.id, issues) if code == 'h' else None)


def handle_prompt_reply(run, parent_id, text, author):
    """A reply to the bot's "reply with the text" or note request. Returns True when it was one of those."""
    review = run.review or {}
    if int(parent_id) in (review.get('replace_prompts') or []):
        if review.get('style') == 'v5':
            # Simple alerts: the written text is shown as the new answer with its own Send button (doc E3)
            from mysite.ai_agent import alerts_v5
            alerts_v5.handle_reply(run, text.strip(), author, timezone.now(), reply_to=int(parent_id), written=True)
            return True
        _say(run, create_draft(run, text.strip(), author))
        return True
    if int(parent_id) in (review.get('note_prompts') or []):
        _say(run, save_note(run, text.strip(), author))
        return True
    return False


def save_note(run, text, author):
    from mysite.ai_agent import cases
    from mysite.models import AICaseNote
    issues = [i for i in cases.run_issues(run) if i.is_open]
    conversation = _ar()._conversation(run)
    AICaseNote.objects.create(conversation_sid=run.conversation_sid, booking=getattr(conversation, 'booking', None),
                              issue=issues[0] if issues else None, created_by_run=run,
                              text=f"Staff note from {author} (Telegram): {text[:1500]}")
    _append(run, 'notes', {'by': author, 'at': timezone.now().isoformat(), 'text': text[:1500]})
    return (f"📝 Note saved{' on ' + issues[0].public_id if issues else ''}. The AI sees it on every later message of "
            f"this chat (CASE_NOTES).")


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
# Newer proposal -> older cards are stale
# ---------------------------------------------------------------------------

def supersede_older(ai_run):
    """
    A new tenant / staff message produced a new proposal: the older proposals of this chat that still wait (answer
    and plan) can no longer be approved. The AI was told to repeat what is still needed (answer_review.pending_block).
    """
    from django.db.models import Q
    ar, AIRun = _ar(), _models()
    v5 = config.alert_style() == 'v5'
    if v5 and ai_run.event_type == 'STAFF_MESSAGE':
        # Simple alerts: a team message does not replace the earlier alert - its task / reminder buttons stay valid.
        # Only an AI answer that still waited is not needed any more: the team answered (A18).
        from mysite.ai_agent import alerts_v5
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
            from mysite.ai_agent import alerts_v5
            alerts_v5.mark_outdated(run, ai_run)   # "⚠️ Outdated - Vera wrote again 14:50, see the newer alert", no buttons
        elif run.telegram_message_id:
            edit_reply_markup(run.telegram_message_id, None)
            _say(run, f"⤴ Replaced by a newer proposal (run #{ai_run.id}, after a new message in the chat) - these "
                      f"buttons no longer work; nothing of this card was done.")
        ar._write_review_file(run.id)
