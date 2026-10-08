"""
The simple Telegram alerts (claude_code_integration_doc/simple_telegram_alerts.md).

    📨 TENANT MESSAGE · 6 Oct, Tue 14:34 ET

    🏠 720-201 · 👤 Vera Lopez · 🟢 Routine

    ———
    ↪️ Edy (team) "Hi Vera, welcome! ..." ↪️

    💬 "Hi, the kitchen sink is dripping since yesterday" 💬
    ———
    🤖 "Hi Vera, thanks for letting us know. ..." 🤖
    ———
    🎫 Kitchen sink dripping – 720-201 🎫
       Edy · routine · due Fri 9 Oct 14:34
    ———
    ⏰ Check the sink task has a visit date – today 16:34 (1/2) ⏰
    ———

    ↩ Reply to this message for questions, notes or custom actions.

    📤 AI sends to chat: ON (live) · 🎫 AI Auto ClickUp: OFF
    🔗 AI run: ...   💬 CRM chat: ...

    [🤖 Send Answer] [✏️ Edit Answer]
    [🤖🎫 Send + Create Task]
    [🎫 Create Task]
    [✅ Close Reminder]

One block per thing, one button per block, and a press does only its own part - no confirmation questions anywhere
(an answer with a task also gets one button that does both) - for real, in live and in test, also
when the AI's own ClickUp writing is OFF (the press is the confirmation). After a press the button shows the result
(✅ Answer sent · Andy 14:36); a second press only says "already done by Andy 14:36". Reminders are not proposed:
they are created at once, the button closes them.

The run keeps its proposal as before (AIRun.review['plan']: the AI's actions). What is new is that each action is
carried out on its own: a finished action gets a 'done' entry {'by', 'at', 'label', ...}.

Built: tenant / team message alerts, automatic notifications, reminders that become due (C1-C8, D5, D6), the
after-hours AI MESSAGE, typed replies. An emergency (done at once) still gets the plain alert of team_notify.
"""
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from mysite.ai_agent import actions as agent_actions
from mysite.ai_agent import clickup, config
from mysite.ai_agent.notify import ai_chat_id, answer_callback, edit_reply_markup, send_ai_chat
from mysite.unified_logger import log_info

PREFIX = 'v5'
LINE = '———'
URGENCY = {'routine': '🟢 Routine', 'urgent': '🔴 Urgent', 'emergency': '🚨 Emergency'}
FOOTER = "↩ Reply to this message for questions, notes or custom actions."
SEND_SMS = '🤖 Send Answer (SMS)'   # the answer goes to the tenant's group chat (Twilio)
SEND_CRM = '📝 Send Answer (CRM)'   # the answer is only written into the CRM chat, no SMS (test chats)
AUTO = 'automatic'   # who "did" what the backend does by itself


NOTIFICATION = 'NOTIFICATION_DUE'   # AIEvent.TYPE_NOTIFICATION_DUE: an automatic notification handed to the AI
# The automatic notifications of the sms_notifications job (prompt_key of their sms_template): label on the alert,
# and why the job planned it - told to the AI
NOTIFICATIONS = {
    'move_in': ("Move-in tomorrow", "the booking starts tomorrow; the text asks when the tenant arrives"),
    'unsigned_contract_1d': ("Contract not signed (1 day)", "the booking was created yesterday and its contract is still not signed"),
    'unsigned_contract_3d': ("Contract not signed (3 days)", "the booking was created 3 days ago and its contract is still not signed"),
    'unsigned_contract_7d': ("Contract not signed (7 days)", "the booking was created 7 days ago and its contract is still not signed"),
    'pending_rent_3d': ("Rent 3 days late", "a Rent payment was due 3 days ago and is still Pending in the CRM"),
    'deposit_reminder': ("Deposit not received", "the booking waits for its hold deposit since 2 days"),
    'due_payment': ("Rent due tomorrow", "a Rent payment is due tomorrow and is still Pending in the CRM"),
    'extension': ("Stay ends soon – extension?", "the stay ends soon; the text asks whether the tenant wants to extend"),
    'move_out': ("Move-out tomorrow", "the booking ends tomorrow; the text asks when the tenant leaves"),
    'safe_travel': ("After checkout", "the booking ended yesterday; a thank-you message"),
}


def applies(meta):
    """True when this run's alert is a simple alert: a tenant message, a team message in a tenant chat, an automatic
    notification, or a reminder that became due."""
    return (meta or {}).get('event_type') in ('TENANT_MESSAGE', 'STAFF_MESSAGE', NOTIFICATION, REMINDER)


def notification_of(events):
    """The automatic notification this batch is about ({'kind', 'label', 'rule', 'template'}), or None."""
    event = next((e for e in events if e.event_type == NOTIFICATION), None)
    if not event:
        return None
    kind = (event.payload or {}).get('kind') or 'notification'
    label, rule = NOTIFICATIONS.get(kind, (kind.replace('_', ' ').capitalize(), "planned by the notification job"))
    return {'kind': kind, 'label': label, 'rule': rule, 'template': (event.body or '').strip()}


def notification_block(events):
    """What the AI is told about a planned notification."""
    note = notification_of(events)
    if not note:
        return None
    return (
        f"NOTIFICATION_DUE: the daily job planned an automatic message to this tenant now.\n"
        f"- kind: {note['kind']} ({note['label']}) - planned because {note['rule']}\n"
        f"- template text: \"{note['template']}\"\n"
        "Nobody wrote a new message. Decide from RECENT_CHAT_HISTORY (it holds ALL chats of this tenant), PAYMENT_RECORDS and "
        "the booking whether this message is still needed today:\n"
        "- STILL NEEDED -> \"answer\" = the text to send: the template, adapted to the conversation when that reads more "
        "natural: the tenant's first name, the language of the chat, and one short sentence when it answers something "
        "the tenant asked and the team already answered in the chat. Keep it as short as the template and keep every "
        "fact of the template as it is (times, amounts). Do NOT add payment details, account or Zelle data, instructions, "
        "new questions, promises or anything else the template does not have. In \"why\": one short sentence why it is "
        "still needed (\"rent $2,150 due 7 Oct is Pending, nothing about it in the chat\").\n"
        "- NOT NEEDED or DOUBTFUL (the tenant already answered what it asks, says they already paid / signed / sent it, "
        "the plan changed, the team already handled it) -> \"answer\" = NO_ANSWER and \"no_reply_reason\" = one sentence "
        "for the team: what the tenant wrote and when, and what the CRM still shows (\"Vera wrote on 5 Oct that she "
        "already paid by Zelle, but the payment is still Pending in the CRM\"). When a person must check something "
        "(a payment the CRM does not show), add ONE SCHEDULE_FOLLOWUP staff_reminder for the owner (\"Janna: check Vera's "
        "October payment\") - with CREATE_ISSUE for it when no open issue covers it. When nothing has to be checked, no "
        "actions.\n"
        "Never CREATE_TICKET for a notification, and no INTERNAL_ALERT: the alert itself tells the team. Leave "
        "tenant_deadline empty and open no case for an arrival or a move-out here - this run is only about the planned message."
    )


def settle_notification(note, parsed, mode):
    """After the AI decided: what the alert shows. A held notification keeps the template as the text that
    📤 Send anyway would send."""
    triage = parsed.get('triage') or {}
    held = not parsed.get('answer')
    info = dict(note, held=held, reason=((triage.get('no_reply_reason') if held else None) or parsed.get('why') or '').strip(),
                at=timezone.now().isoformat())
    if held:
        parsed['answer'], parsed['no_answer'] = note['template'], False
    return info


def deliver_notification(info, parsed, mode, send_to, booking):
    """Still needed + live -> sent at once (inside SMS hours, else it waits for 08:00). Held by the AI, or a test
    apartment -> it waits for a press. Returns the delivery dict of the run."""
    from mysite.ai_agent import service
    if info['held']:
        return {'sent_to_chat': False, 'held': True, 'send_to': send_to,
                'note': f"automatic notification HELD by the AI: {info['reason']}"[:250]}
    if mode != 'live':
        return {'sent_to_chat': False, 'held': True, 'send_to': send_to,
                'note': 'automatic notification: test apartment - not sent by itself, waits for Send now'}
    result = service.send_answer(send_to, parsed['answer'], 'Virtual Assistant', None, booking)
    result['send_to'] = send_to
    if result.get('sent_to_chat'):
        info['sent_at'] = timezone.now().isoformat()
    elif not result.get('error'):
        info['waits_until'] = config.next_notification_window_start().isoformat()
    else:
        info['failed'] = str(result.get('error'))[:200]
    return result


# ---------------------------------------------------------------------------
# Reminders that become due (part 5 of the document, C1-C8, D5, D6)
# ---------------------------------------------------------------------------
REMINDER = 'FOLLOWUP_DUE'
TENANT_KINDS = ('tenant_nudge', 'second_tenant_nudge')
RETRY_AFTER = timedelta(minutes=3)   # a live tenant reminder that could not be sent is tried once more (D6)
TEST_REMINDER = "Test reminder asked by"   # the case of a 🧪 Send test reminder press (run_operation)
# What the AI may still propose on a reminder: a real change somebody has to press for
_REMINDER_WORK = ('CREATE_TICKET', 'TICKET_COMMENT', 'UPDATE_TICKET', 'CRM_CHANGE', 'KB_UPDATE')


def reminder_of(events, ticket_states=None):
    """The reminder this batch is about ({'followup_id', 'kind', 'reason', 'n', 'last', ...}), or None.
    ticket_states: {issue id: ClickUp task state} read just before (service.check_tickets_before_reminder)."""
    from mysite.models import AIEvent, AIFollowUp
    ids = [(e.payload or {}).get('followup_id') for e in events if e.event_type == AIEvent.TYPE_FOLLOWUP_DUE]
    followup = AIFollowUp.objects.filter(id__in=[i for i in ids if i]).select_related('issue').order_by('id').first()
    if not followup:
        return None
    deadline = followup.kind == AIFollowUp.KIND_DEADLINE_REMINDER
    info = {'followup_id': followup.id, 'public_id': followup.public_id, 'kind': followup.kind,
            'reason': (followup.reason or 'Reminder').strip(), 'due': followup.due_at.isoformat(),
            'to_tenant': followup.kind in TENANT_KINDS, 'deadline': deadline}
    issue = followup.issue
    if issue:
        info.update(issue_id=issue.public_id, issue_pk=issue.id, summary=issue.summary, owner=issue.owner,
                    priority=issue.priority)
        if deadline and issue.tenant_deadline:
            hours = round((issue.tenant_deadline - followup.due_at).total_seconds() / 3600)
            info.update(deadline_at=issue.tenant_deadline.isoformat(), before='2 h' if hours <= 6 else '24 h')
        series = issue.followups.exclude(kind=AIFollowUp.KIND_DEADLINE_REMINDER) \
            .exclude(status=AIFollowUp.STATUS_CANCELLED, status_note__startswith='alert outdated')
        # the team's reminders and the tenant's are two series of the case, each 1/2 and 2/2
        series = series.filter(kind__in=TENANT_KINDS) if info['to_tenant'] else series.exclude(kind__in=TENANT_KINDS)
        series = list(series.order_by('id').values_list('id', flat=True))
        position = series.index(followup.id) + 1 if followup.id in series else 1
        state = (ticket_states or {}).get(issue.id)
        if state and not state.get('error'):
            last = (state.get('comments') or [None])[-1]
            info['task'] = {'title': issue.ticket_title or issue.summary, 'status': state.get('status'),
                            'closed': bool(state.get('closed')), 'updated': state.get('updated'),
                            'last_comment': f"{last.get('when')} {last.get('user')}".strip() if last else None}
    else:
        family = TENANT_KINDS if info['to_tenant'] else (followup.kind,)
        position = AIFollowUp.objects.filter(conversation_sid=followup.conversation_sid, kind__in=family,
                                             reason=followup.reason, id__lte=followup.id).count()
    info.update(n=min(position, 2), last=position >= 2 and not deadline)
    return info


def reminder_block(info):
    """What the AI is told about the reminder that became due (it decides: still needed, or already done)."""
    target = ("to the TENANT (a message the team asked the tenant for)" if info['to_tenant']
              else f"before the tenant's deadline ({info.get('before', '')} before)" if info['deadline'] else "for the TEAM")
    return (
        f"REMINDER_DUE: the reminder {info['public_id']} ({info['kind']}, {info['n']}/2) {target} became due now: "
        f"\"{info['reason']}\"" + (f" - case {info['issue_id']} \"{info.get('summary')}\"" if info.get('issue_id') else '') + ".\n"
        "Nobody wrote a new message. Re-check RECENT_CHAT_HISTORY (all chats of this tenant), OPEN_ISSUES and CLICKUP_TASKS: "
        "is what the reminder is about still not done?\n"
        "- ALREADY DONE or NO LONGER NEEDED (the tenant sent / did it, the team confirmed it in the chat, a ClickUp comment "
        "shows it is handled, the plan changed) -> answer NO_ANSWER, next_action '', NO actions, and no_reply_reason = one "
        "sentence what shows it is done (\"Vera sent the meter photo at 17:00\"). The reminder then closes quietly.\n"
        + ("- STILL NEEDED -> \"answer\" = a short, friendly reminder to the tenant about exactly what the team asked for "
           "(one or two sentences, the language of the chat, the tenant's first name). No new facts, promises, payment "
           "details or questions. next_action = what the tenant still owes.\n" if info['to_tenant'] else
           "- STILL NEEDED -> answer NO_ANSWER (the reminder is for the team) and next_action = what must be done now and "
           "by whom, one sentence. Add an action only when something NEW must be pressed (a comment on the task, a CRM "
           "change).\n")
        + "Never SCHEDULE_FOLLOWUP (the backend times the next reminder) and never INTERNAL_ALERT (the reminder alert itself "
          "tells the team). When CLICKUP_TASKS shows the task CLOSED, follow the instruction on its line."
    )


def settle_reminder(info, parsed, mode):
    """After the AI decided: still needed (an alert / an automatic message) or already done (closed quietly, C5)."""
    triage = parsed.get('triage') or {}
    work = [a for a in parsed.get('actions') or [] if isinstance(a, dict) and a.get('type') in _REMINDER_WORK]
    task = info.get('task') or {}
    needed = bool(parsed.get('answer') or str(triage.get('next_action') or '').strip() or work or task.get('closed'))
    done_note = str(triage.get('no_reply_reason') or parsed.get('why') or '').strip()
    return dict(info, needed=needed, at=timezone.now().isoformat(), mode=mode,
                next_action=str(triage.get('next_action') or '').strip(), done_note='' if needed else done_note)


def deliver_reminder(info, parsed, mode, send_to, booking, deliver):
    """A tenant reminder that is still needed: live -> sent now (D5; inside SMS hours, else at 08:00), a failed send is
    tried again in 3 minutes (D6); test -> it waits for 🤖 Send now (C4). Anything else as every answer (deliver()).
    Returns the delivery dict of the run."""
    from mysite.ai_agent import service
    if not (info['to_tenant'] and info['needed'] and parsed.get('answer')):
        return deliver()
    if mode != 'live':
        return {'sent_to_chat': False, 'held': True, 'send_to': send_to,
                'note': 'tenant reminder: test apartment - not sent by itself, waits for Send now'}
    result = service.send_answer(send_to, parsed['answer'], 'Virtual Assistant', None, booking)
    result['send_to'] = send_to
    if result.get('sent_to_chat'):
        info['sent_at'] = timezone.now().isoformat()
    elif not result.get('error'):
        info['waits_until'] = config.next_notification_window_start().isoformat()
    else:
        info.update(failed=str(result.get('error'))[:200], retry_at=(timezone.now() + RETRY_AFTER).isoformat())
    return result


def close_done_reminder(conversation_sid, info):
    """The AI found the reminder already done (C5): no alert. The next reminder of the same case goes too, and the alert
    that showed the reminder says so on its button."""
    from mysite.models import AIFollowUp
    note = f"done: {info.get('done_note') or 'no longer needed'}"[:255]
    AIFollowUp.objects.filter(id=info['followup_id']).update(status_note=note, updated_at=timezone.now())
    if info.get('issue_pk'):
        kinds = TENANT_KINDS if info['to_tenant'] else ('staff_reminder', 'escalation_check')
        AIFollowUp.objects.filter(issue_id=info['issue_pk'], kind__in=kinds, status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_CANCELLED, status_note=note, updated_at=timezone.now())
    reminder_closed_elsewhere(conversation_sid, info['followup_id'], f"done – checked {_hm()}")


def _next_day_10(kind):
    """When the 2/2 reminder (and "Remind again in 24 hours") fires: the next day 10:00 - for the team the next working day."""
    from datetime import datetime, time
    from mysite.ai_agent import policy
    local = _local(timezone.now())
    moment = datetime.combine(local.date() + timedelta(days=1), time(10, 0), tzinfo=local.tzinfo)
    return moment if kind in TENANT_KINDS else policy._office_moment(moment)


def _plan_next_reminder(ctx, info):
    """1/2 is due and still needed: the 2/2 of the case is set for the next day (rule 1.3.2), unless one is pending."""
    from mysite.models import AIFollowUp
    if info['n'] != 1 or info['deadline'] or (info.get('task') or {}).get('closed') or info.get('next') \
            or str(info.get('summary') or '').startswith(TEST_REMINDER):
        return   # a test reminder from Telegram (run_operation) shows the alert once
    kind = 'second_tenant_nudge' if info['to_tenant'] else 'staff_reminder'
    pending = AIFollowUp.objects.filter(issue_id=info['issue_pk'], status=AIFollowUp.STATUS_PENDING,
                                        kind__in=TENANT_KINDS if info['to_tenant'] else ('staff_reminder', 'escalation_check')) \
        .order_by('due_at').first() if info.get('issue_pk') else None
    if not pending:
        pending = AIFollowUp.objects.create(
            conversation_sid=ctx.conversation_sid, issue_id=info.get('issue_pk'), kind=kind, reason=info['reason'],
            due_at=_next_day_10(kind), created_by_run=ctx.ai_run)
    info['next'] = {'followup_id': pending.id, 'due': pending.due_at.isoformat()}


def _when(moment):
    """'tomorrow 10:00', 'today 16:34', 'Mon 10:00'."""
    local, today = _local(moment), _local(timezone.now()).date()
    if local.date() == today + timedelta(days=1):
        return f"tomorrow {local:%H:%M}"
    return _day_time(moment)


def _reminder_head(info, live, priority):
    if info['deadline']:
        place = f"deadline in {info.get('before') or 'soon'}" + (" · @Kevin" if info.get('before') == '2 h' else '')
    elif info['to_tenant'] and not live:
        place = f"{info['n']}/2 (to tenant)"
    elif info['last']:
        place = "2/2 (last)"
    elif info.get('next'):
        from datetime import datetime
        place = f"1/2 (2/2 {_when(datetime.fromisoformat(info['next']['due']))})"
    else:
        place = f"{info['n']}/2"
    return [URGENCY.get(priority, URGENCY['routine']), place]


def reminder_priority(info, triage):
    rank = ['routine', 'urgent', 'emergency']
    found = [p for p in (info.get('priority'), (triage or {}).get('priority')) if p in rank]
    if info['deadline']:
        found.append('urgent')   # something must be ready for the tenant soon
    return max(found or ['routine'], key=rank.index)


def compose_reminder(ctx, ai_run, parsed, delivery, actions):
    """⏰ REMINDER (C1, C2, C4, C6, C7), or 🤖 AI MESSAGE for a live tenant reminder the AI sent by itself (D5, D6)."""
    from datetime import datetime
    from mysite.ai_agent import team_notify
    from mysite.models import AIRun
    meta = ctx.meta
    info, live = meta['reminder'], meta.get('mode') == 'live'
    review = ai_run.review or {}
    done = review.get('answer_done') or {}
    auto = info['to_tenant'] and live and parsed.get('answer')
    priority = reminder_priority(info, parsed.get('triage'))
    head = [f"🏠 {meta.get('apartment')}", f"👤 {meta.get('tenant') or 'Unknown'}"]
    if auto:
        head.append(f"⏰ Reminder {info['n']}/2 auto-sent" if info.get('sent_at') or info.get('retried') == 'sent'
                    else f"⏰ Reminder {info['n']}/2")
    else:
        head += _reminder_head(info, live, priority)
    if not live:
        head.append("🧪 TEST")
    title = "🤖 AI MESSAGE" if auto else "⏰ REMINDER"
    lines = [f"{title} · {_stamp(datetime.fromisoformat(info['at']))}", "", " · ".join(head), "", LINE]
    if not info['to_tenant']:
        owner = info.get('owner') or (parsed.get('triage') or {}).get('owner')
        text = str(info.get('summary') if info['deadline'] else info['reason']).rstrip('. ')
        lines.append(f"⏰ {text}" + (f" (for {owner})" if owner else "") + " ⏰")
        if info['deadline'] and info.get('deadline_at'):
            when = _local(datetime.fromisoformat(info['deadline_at']))
            lines.append(f"   tenant deadline {when:%a} {when.day} {when:%b %H:%M}")
        task = info.get('task') or {}
        if task and not task.get('closed'):
            lines.append(f"   ClickUp: {task.get('status') or '?'} · "
                         + (f"last comment {task['last_comment']}" if task.get('last_comment') else "no comment yet"))
        lines.append(LINE)
    task = info.get('task') or {}
    if task.get('closed'):
        lines += [f"🎫 Task \"{task['title']}\" was CLOSED ({task.get('status')}"
                  + (f", {task['updated']}" if task.get('updated') else "") + ")", LINE]
    talk = last_exchange(ai_run.conversation_sid, meta.get('tenant'), skip_text=ai_run.final_answer or parsed.get('answer'))
    if talk:
        lines += talk + [LINE]
    if parsed.get('answer'):
        lines += [f"🤖 \"{parsed['answer']}\" 🤖", LINE]
        if auto:
            if done:
                status = done.get('label') or "✅ Sent"
            elif info.get('sent_at'):
                status = f"✅ Sent {_hm(datetime.fromisoformat(info['sent_at']))} – reminder {info['n']}/2 was due, still needed, nobody closed it"
            elif info.get('waits_until'):
                status = f"⏳ Will be sent {_hm(datetime.fromisoformat(info['waits_until']))} (SMS hours)"
            elif info.get('retried') == 'sent':
                status = f"✅ Sent on retry {_hm(datetime.fromisoformat(info['retried_at']))}"
            elif info.get('retried') == 'failed':
                status = f"❌ Retry failed – please send by hand ({info.get('failed')})"
            elif info.get('failed'):
                status = (f"❌ Could not send to {str(meta.get('tenant') or 'the tenant').split()[0]} ({info['failed']}) – "
                          f"retry at {_hm(datetime.fromisoformat(info['retry_at']))}")
            else:
                status = "❌ Not sent"
            lines.append(status)
            if info.get('next') and not (review.get('reminder_done') or {}).get('closed'):
                lines.append(f"⏰ Next: 2/2 {_when(datetime.fromisoformat(info['next']['due']))}")
            lines.append(LINE)
        elif info['to_tenant'] and not done and ai_run.hold_status != AIRun.HOLD_CANCELLED:
            lines += ["🧪 Test mode: NOT sent automatically. Press to send for real.", LINE]
    items = blocks(actions)
    numbers = _numbered(items)
    for kind, index, action in items:
        lines += _block_lines(kind, action, numbers[index], ctx) + [LINE]
    lines += [
        "", FOOTER, "",
        f"📤 AI sends to chat: {'ON (live)' if live else 'OFF (test)'} · 🎫 AI Auto ClickUp: {'ON' if clickup.writes_enabled(ctx.apartment) else 'OFF'}",
        f"🔗 AI run: {team_notify.report_url(ai_run.id)}",
        f"💬 CRM chat: {config.site_url()}/chat/{ai_run.conversation_sid}/",
    ]
    return "\n".join(lines)


def reminder_buttons(run_id, info, state):
    """The reminder's own row(s): ✅ Close Reminder, and on the last one ⏰ Remind again in 24 hours (C2)."""
    state = state or {}
    if info.get('to_tenant') and info.get('mode') == 'live' and not info.get('next'):
        return []   # a live tenant reminder that was the last one: nothing left to stop
    if state.get('closed'):
        rows = [[_btn(state['closed'], 'dn', run_id, 'r')]]
    else:
        rows = [[_btn("✅ Close Reminder", 'rc', run_id)]]
    if info.get('last') and not info.get('to_tenant'):
        rows.append([_btn(state['again'], 'dn', run_id, 'g') if state.get('again') else _btn("⏰ Remind again in 24 hours", 'rg', run_id)])
    return rows


def close_due_reminder(run, author):
    """✅ Close Reminder on a REMINDER alert: the reminders still pending for this case will not fire."""
    from mysite.models import AIFollowUp
    _, approval = _review()
    review = run.review or {}
    info = (review.get('meta') or {}).get('reminder') or {}
    state = dict(review.get('reminder_done') or {})
    if state.get('closed'):
        return f"already done by {state.get('closed_by')} {_hm_of({'at': state.get('closed_at')})}"
    pending = AIFollowUp.objects.filter(status=AIFollowUp.STATUS_PENDING)
    if info.get('issue_pk'):
        pending = pending.filter(issue_id=info['issue_pk'])
        if not info.get('deadline'):
            pending = pending.exclude(kind=AIFollowUp.KIND_DEADLINE_REMINDER)
    else:
        pending = pending.filter(id__in=[(info.get('next') or {}).get('followup_id') or 0])
    pending.update(status=AIFollowUp.STATUS_CANCELLED, status_note=f"closed by {author} in Telegram"[:255], updated_at=timezone.now())
    state.update(closed=f"✅ Reminder closed · {author}", closed_by=author, closed_at=timezone.now().isoformat())
    approval._update_review(run, reminder_done=state)
    _finish(run, None, author, 'close reminder')
    refresh_alert(run)
    return "Reminder closed"


def remind_again(run, author):
    """⏰ Remind again in 24 hours (only on the last reminder): one more reminder, the next day 10:00."""
    from datetime import datetime
    from mysite.models import AIFollowUp
    _, approval = _review()
    review = run.review or {}
    info = (review.get('meta') or {}).get('reminder') or {}
    state = dict(review.get('reminder_done') or {})
    if state.get('again'):
        return f"already done: {state['again']}"
    if state.get('closed'):
        return "The reminder was closed – nothing to remind again."
    followup = AIFollowUp.objects.create(
        conversation_sid=run.conversation_sid, issue_id=info.get('issue_pk'), kind=info.get('kind') or 'staff_reminder',
        reason=info.get('reason') or 'Reminder', due_at=_next_day_10(info.get('kind')), created_by_run=run)
    when = _when(followup.due_at)
    state.update(again=f"⏰ Next: {when} · {author}", again_id=followup.id, again_due=followup.due_at.isoformat())
    approval._update_review(run, reminder_done=state)
    _finish(run, None, author, 'remind again', f"{followup.public_id} due {datetime.isoformat(followup.due_at)}")
    return f"Next reminder {when}"


def retry_due(only=None, skip_prefix=None):
    """D6: a live tenant reminder that could not be sent is tried once more when its retry time came (worker tick).
    only: these chats only (the sandbox runner); skip_prefix: chats the caller must not touch (the live worker skips the
    sandbox chats). Returns how many were retried."""
    from datetime import datetime
    from mysite.models import AIRun
    _, approval = _review()
    runs = AIRun.objects.filter(event_type=REMINDER, review__style='v5', created_at__gte=timezone.now() - timedelta(days=1))
    if only:
        runs = runs.filter(conversation_sid__in=list(only))
    count = 0
    for run in runs.order_by('id'):
        review = run.review or {}
        meta = dict(review.get('meta') or {})
        info = dict(meta.get('reminder') or {})
        if (skip_prefix and run.conversation_sid.startswith(skip_prefix)) or not info.get('retry_at') or info.get('retried') \
                or datetime.fromisoformat(info['retry_at']) > timezone.now():
            continue
        from mysite.ai_agent import service
        result = service.send_answer(review.get('send_to') or run.conversation_sid, run.answer, 'Virtual Assistant', None)
        if result.get('sent_to_chat') or not result.get('error'):
            info.update(retried='sent', retried_at=timezone.now().isoformat())
            AIRun.objects.filter(id=run.id).update(sent_to_chat=bool(result.get('sent_to_chat')), final_answer=run.answer,
                                                   hold_status=AIRun.HOLD_SENT, delivery_note='sent on retry'[:255])
        else:
            info.update(retried='failed', retried_at=timezone.now().isoformat(), failed=str(result.get('error'))[:200])
            # Nobody can be left without the message: it waits for 🤖 Send now (or a person sends it by hand)
            AIRun.objects.filter(id=run.id).update(hold_status=AIRun.HOLD_HOLDING, delivery_note='retry failed - waits for Send now')
        meta['reminder'] = info
        approval._update_review(run, meta=meta)
        refresh_alert(run)
        count += 1
    return count


def _team(meta):
    return (meta or {}).get('event_type') == 'STAFF_MESSAGE'


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _local(moment):
    return moment.astimezone(ZoneInfo(config.TEAM_TIMEZONE))


def _hm(moment=None):
    return f"{_local(moment or timezone.now()):%H:%M}"


def _stamp(moment):
    local = _local(moment)
    return f"{local.day} {local:%b}, {local:%a %H:%M} ET"


def _short_stamp(moment):
    local = _local(moment)
    return f"{local.day} {local:%b %H:%M}"


def _day_time(moment, with_date=False):
    """'today 16:34', else 'Wed 09:00' (with_date: 'Fri 9 Oct 14:34')."""
    local, today = _local(moment), _local(timezone.now()).date()
    if local.date() == today:
        return f"today {local:%H:%M}"
    return f"{local:%a} {local.day} {local:%b %H:%M}" if with_date else f"{local:%a %H:%M}"


def _btn(text, code, run_id, arg=''):
    return {'text': text[:64], 'callback_data': f"{PREFIX}|{code}|{run_id}|{arg}"[:64]}


def _names(value):
    return [str(v) for v in ([value] if isinstance(value, str) else value or []) if str(v).strip()]


def _done(action, by, label, **extra):
    action['done'] = dict({'by': by, 'at': timezone.now().isoformat(), 'label': label}, **extra)


# ---------------------------------------------------------------------------
# The blocks of an alert, from the AI's actions
# ---------------------------------------------------------------------------

def prepare(actions, ctx, triage=None):
    """
    Before the alert is written: marks what each action means on the alert, and does what happens by itself -
    every reminder is created now, with the case (issue) it belongs to. Changes the action dicts in place.
    """
    from mysite.models import AIFollowUp

    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in agent_actions.ACTION_TYPES:
            continue
        kind = action['type']
        issue = _existing_issue(ctx, action.get('issue_id') or action.get('ticket_id'))
        if kind == 'CREATE_TICKET' and issue and issue.ticket_ref:
            action['existing_task'] = issue.ticket_title or issue.summary      # no second task for the same case
        elif kind in ('TICKET_COMMENT', 'UPDATE_TICKET') and issue:
            action['task_title'] = issue.ticket_title or issue.summary
            action['task_issue'] = issue.id
            rank = ['routine', 'urgent', 'emergency']
            wanted = action.get('priority') if action.get('priority') in rank else (triage or {}).get('priority')
            if wanted in rank and rank.index(wanted) > rank.index(issue.priority if issue.priority in rank else 'routine'):
                action['raise_to'] = wanted   # the case got more urgent: the update raises the task too ("make urgent + comment")
        if kind == 'CREATE_TICKET' and not action.get('responsible'):
            action['case_owner'] = (issue.owner if issue else None) or next(
                (a.get('owner') for a in actions if isinstance(a, dict) and a.get('type') == 'CREATE_ISSUE'
                 and str(a.get('temp_id')) == str(action.get('issue_id'))), None)
        elif kind == 'UPDATE_ISSUE_STATE' and action.get('state') == 'RESOLVED' and issue and issue.ticket_ref \
                and not ((ctx.meta.get('reminder') or {}).get('task') or {}).get('closed'):   # C6: closed in ClickUp already
            action['closes_task'] = issue.ticket_title or issue.summary
            action['task_issue'] = issue.id
    # What has no block and no button is internal bookkeeping (the case itself, notes, a reminder that is no longer
    # needed, a state change that touches nothing in ClickUp): done at once, or nobody could ever make it happen.
    team = _team(ctx.meta)
    ctx.meta['v5_priority'] = (triage or {}).get('priority')
    if ctx.meta.get('event_type') == 'TENANT_MESSAGE':
        _retire_older_reminders(ctx, triage)
        _raise_cases(ctx, triage)
    resolving = {str(a.get('issue_id')) for a in actions if isinstance(a, dict) and a.get('closes_task')}
    closing = {a['task_issue'] for a in actions if isinstance(a, dict) and a.get('closes_task')}
    for action in actions:   # a comment on a task that the same alert closes goes with the close: one block, one button
        if isinstance(action, dict) and action.get('type') in ('TICKET_COMMENT', 'UPDATE_TICKET') and action.get('task_issue') in closing:
            action['with_close'] = True
    if ctx.meta.get('event_type') == 'TENANT_MESSAGE':
        # One reminder per alert: two problems in one message get one "check both" reminder, not one each
        first = None
        for action in actions:
            if isinstance(action, dict) and action.get('type') == 'SCHEDULE_FOLLOWUP' and not action.get('removed_by'):
                if first is None:
                    first = action
                else:
                    action['removed_by'] = "backend (one reminder per alert)"
    for action in actions:   # the cases first: the rest may be about them
        if isinstance(action, dict) and action.get('type') == 'CREATE_ISSUE' and not action.get('done'):
            if action.get('temp_id'):
                _ensure_issue(ctx, actions, {'issue_id': action['temp_id']}, AUTO)
            else:
                status, detail = _run(ctx, action)
                _done(action, AUTO, 'case opened', detail=str(detail)[:200])
    for action in actions:
        if not isinstance(action, dict) or action.get('done') or action.get('removed_by'):
            continue
        kind = action.get('type')
        if kind == 'CANCEL_FOLLOWUP':
            followup = _followup(ctx, action.get('followup_id'))
            if followup and followup.status == 'pending' and followup.issue_id and followup.issue.public_id in resolving:
                # The reminder of a task this alert proposes to close: shown with its own Close Reminder button (A10)
                action['open_reminder'] = {'followup_id': followup.id, 'reason': followup.reason or 'Reminder', 'due': followup.due_at.isoformat()}
                continue
            if followup:
                action['closed_reminder'] = followup.reason or 'Reminder'
                action['closed_followup'] = followup.id
        if kind == 'KB_UPDATE' and team and ctx.ai_run and ctx.ai_run.message_id:
            # A fact a team member wrote in the chat: the source is that message
            from mysite.ai_agent import inputs
            name = inputs.classify_sender(ctx.ai_run.message)[1]
            action['source'] = f"{name}'s message {_short_stamp(ctx.ai_run.message.message_timestamp)}"
        if kind in ('CASE_NOTE', 'CANCEL_FOLLOWUP') or (kind == 'UPDATE_ISSUE_STATE' and not action.get('closes_task')):
            status, detail = _run(ctx, action)
            if status == agent_actions.STATUS_EXECUTED:
                _done(action, AUTO, 'done', detail=str(detail)[:200])
                if action.get('closed_followup'):
                    reminder_closed_elsewhere(ctx.conversation_sid, action['closed_followup'],
                                              'answered in the chat' if team else 'no longer needed')
            else:
                action['removed_by'] = f"backend ({str(detail)[:150]})"
        elif kind == 'CREATE_TICKET' and action.get('existing_task'):
            _done(action, AUTO, 'no new task', detail=f"the case already has the task \"{action['existing_task']}\"")
        elif kind == 'CRM_CHANGE':
            # Shown only when the CRM really allows it now; the words of the block come from the CRM, not from the AI
            from mysite.ai_agent import crm_changes
            try:
                action['label'] = crm_changes.check(ctx.booking, ctx.apartment, action)
            except agent_actions.ActionError as e:
                action['removed_by'] = f"backend ({str(e)[:150]})"
        elif kind == 'KB_UPDATE' and not str(action.get('text') or action.get('value') or '').strip():
            action['removed_by'] = "backend (an empty fact is not shown)"
        elif kind == 'KB_UPDATE' and _already_known(ctx, action):
            action['removed_by'] = "backend (the knowledge base already says this)"
        elif kind == 'KB_UPDATE' and not str(action.get('source') or '').strip():
            action['removed_by'] = "backend (a fact without a source message is not shown)"
        elif kind in ('INTERNAL_ALERT', 'QUEUE_FOR_REVIEW'):
            _done(action, AUTO, 'team note', detail='the alert itself is the notification')
    if ctx.meta.get('event_type') == 'TENANT_MESSAGE':   # a deadline is when the TENANT needs something ready
        _deadline(actions, ctx, triage)
    if any(isinstance(a, dict) and a.get('type') == 'DEADLINE' for a in actions):
        for action in actions:   # the 24 h / 2 h deadline reminders follow this case up: no extra reminder next to them
            if isinstance(action, dict) and action.get('type') == 'SCHEDULE_FOLLOWUP' and not action.get('done') and not action.get('removed_by'):
                action['removed_by'] = "backend (the deadline reminders cover this case)"
    reminder = ctx.meta.get('reminder')
    if reminder:
        for action in actions:   # the backend times the reminders of a case (1/2, 2/2): the AI does not add more here
            if isinstance(action, dict) and action.get('type') == 'SCHEDULE_FOLLOWUP' and not action.get('done'):
                action['removed_by'] = "backend (the next reminder of the case is set by the backend)"
        if reminder.get('needed'):
            _plan_next_reminder(ctx, reminder)
            reminder_closed_elsewhere(ctx.conversation_sid, reminder['followup_id'], 'the reminder alert',
                                      label=f"⏰ Due {_hm()} – see the reminder alert")
    if ctx.meta.get('event_type') == 'TENANT_MESSAGE' and not any(isinstance(a, dict) and a.get('type') == 'DEADLINE' for a in actions):
        # Every case a tenant message opens is followed up: when the AI scheduled no reminder for it, the backend does
        # (rule 1.3.1 "created automatically when the case needs one" - the AI alone forgets it on some runs).
        for action in list(actions):
            ref = str(action.get('temp_id') or '') if isinstance(action, dict) else ''
            if not ref or action.get('type') != 'CREATE_ISSUE' or not (action.get('done') or {}).get('issue_pk') \
                    or action.get('state') == 'RESOLVED':
                continue
            if not any(isinstance(a, dict) and a.get('type') == 'SCHEDULE_FOLLOWUP' and not a.get('removed_by') for a in actions):
                reason = str((triage or {}).get('next_action') or '').strip() or f"Check: {action.get('summary')}"
                actions.append({'type': 'SCHEDULE_FOLLOWUP', 'issue_id': ref, 'kind': 'staff_reminder', 'reason': reason[:200],
                                'backend_added': True})
    for action in actions:
        if not isinstance(action, dict) or action.get('type') != 'SCHEDULE_FOLLOWUP' or action.get('done') or action.get('removed_by'):
            continue
        if action.get('kind') == 'escalation_check':
            # One kind of team reminder in the simple alerts: its time comes from the urgency of the case (2 h, urgent
            # 30 min), not from which kind the AI happened to pick
            action.update(kind='staff_reminder', kind_set_by='backend: team reminders are timed by urgency')
        if ctx.meta.get('event_type') == 'TENANT_MESSAGE' and action.get('kind') in ('tenant_nudge', 'second_tenant_nudge'):
            # After a tenant's own message the reminder is always for the team: they must make sure it gets solved
            # ("Check Mark got in"). A reminder TO the tenant only follows a request of the team (B4, B5). The AI
            # does not choose this reliably, so the backend does.
            action.update(kind='staff_reminder', kind_set_by='backend: after a tenant message the reminder is for the team')
        _ensure_issue(ctx, actions, action, AUTO)
        before = set(AIFollowUp.objects.filter(conversation_sid=ctx.conversation_sid).values_list('id', flat=True))
        status, detail = _run(ctx, action)
        created = AIFollowUp.objects.filter(conversation_sid=ctx.conversation_sid).exclude(id__in=before).first()
        if status != agent_actions.STATUS_EXECUTED or not created:
            action['removed_by'] = f"backend ({str(detail)[:150]})"   # e.g. the case already had its 2 reminders
            continue
        number = created.issue.followups.exclude(kind=AIFollowUp.KIND_DEADLINE_REMINDER).filter(id__lte=created.id) \
            .exclude(status=AIFollowUp.STATUS_CANCELLED, status_note__startswith='alert outdated').count() if created.issue_id else 1
        _done(action, AUTO, 'created', followup_id=created.id, due=created.due_at.isoformat(), detail=str(detail)[:200],
              number=f"{min(number, 2)}/2")


def _already_known(ctx, action):
    """The fact is already written in the apartment's or the global knowledge base (word for word, any case)."""
    from mysite.views.messaging import get_global_knowledge_base_text

    def plain(text):
        return re.sub(r'\s+', ' ', str(text or '')).strip().lower()
    fact = plain(action.get('text') or action.get('value'))
    known = plain(getattr(ctx.apartment, 'knowledge_base', '')) + ' \n ' + plain(get_global_knowledge_base_text())
    return bool(fact) and fact in known


def _waiting_alerts(conversation_sid, before_run=None):
    """The simple alerts of this chat that still wait for a press (not outdated)."""
    from django.db.models import Q
    from mysite.models import AIRun
    runs = AIRun.objects.filter(conversation_sid=conversation_sid, review__style='v5').filter(
        Q(hold_status=AIRun.HOLD_HOLDING) | Q(review__plan__status='pending'))
    if before_run is not None:
        runs = runs.filter(id__lt=before_run.id)
    return [run for run in runs.order_by('id') if not (run.review or {}).get('stale')]


def _retire_older_reminders(ctx, triage=None):
    """The tenant wrote again about the SAME case: the earlier alerts of this chat are outdated in a moment (A17), and
    their reminders for that case go with them - the new alert gets its own reminder, again 1/2. A reminder of another
    case stays: "the balcony light is out" must not stop the reminder about the water meter photo."""
    from mysite.ai_agent import cases
    from mysite.models import AIFollowUp
    same = [i.id for i in cases.existing_issues(ctx.conversation_sid, (triage or {}).get('issue_refs'))]
    if not same:
        return
    for run in _waiting_alerts(ctx.conversation_sid, ctx.ai_run):
        ids = [a['done']['followup_id'] for a in ((run.review or {}).get('plan') or {}).get('actions') or []
               if isinstance(a, dict) and a.get('type') == 'SCHEDULE_FOLLOWUP' and (a.get('done') or {}).get('followup_id')]
        AIFollowUp.objects.filter(id__in=ids, issue_id__in=same, status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_CANCELLED, status_note='alert outdated - the tenant wrote again, see the newer alert',
            updated_at=timezone.now())


def _raise_cases(ctx, triage):
    """A case is as urgent as the newest message about it: "now there's water on the floor" makes the sink case urgent."""
    from mysite.ai_agent import cases
    rank = ['routine', 'urgent', 'emergency']
    wanted = (triage or {}).get('priority')
    if wanted not in rank:
        return
    for issue in cases.existing_issues(ctx.conversation_sid, (triage or {}).get('issue_refs')):
        # A case with a ClickUp task follows its task: that one becomes urgent with 🔄 Apply Update ("make urgent"), a press
        if issue.is_open and not issue.ticket_ref and rank.index(wanted) > rank.index(issue.priority if issue.priority in rank else 'routine'):
            issue.priority = wanted
            issue.save()


def reminder_closed_elsewhere(conversation_sid, followup_id, why, label=None):
    """A reminder shown on an earlier alert was closed by something else (the team answered, the case was solved), or
    became due (label: "⏰ Due 16:34 – see the reminder alert"): its Close Reminder button shows that instead of
    pretending the reminder is still open."""
    ar, _ = _review()
    for run in _waiting_alerts(conversation_sid):
        plan = ar._plan(run)
        changed = False
        for action in plan.get('actions') or []:
            done = action.get('done') or {} if isinstance(action, dict) else {}
            if action.get('type') == 'SCHEDULE_FOLLOWUP' and done.get('followup_id') == followup_id and not done.get('closed'):
                done.update(closed=label or f"✅ Reminder closed · {why}", closed_by=why, closed_at=timezone.now().isoformat())
                changed = True
        if changed:
            ar._save_plan(run, plan)
            if run.telegram_message_id:
                edit_reply_markup(run.telegram_message_id, keyboard_for(run))


def _followup(ctx, public_id):
    from mysite.models import AIFollowUp
    match = re.fullmatch(r'f-(\d+)', str(public_id or '').strip())
    return AIFollowUp.objects.filter(id=int(match.group(1)), conversation_sid=ctx.conversation_sid).select_related('issue').first() if match else None


def _deadline(actions, ctx, triage):
    """A tenant deadline: the cases of this run get it now, so their 24 h / 2 h reminders exist when the alert is
    written; the alert shows them as one block (they do not count in the 2 reminders of a case)."""
    from mysite.ai_agent import cases, service
    from mysite.models import AIFollowUp, AIIssue
    deadline = service.parse_tenant_deadline((triage or {}).get('tenant_deadline'))
    if not deadline or deadline <= timezone.now():
        return
    if deadline - timezone.now() <= timedelta(hours=48) and (triage.get('priority') or 'routine') == 'routine':
        triage['priority'] = 'urgent'   # something must be ready for the tenant within two days
    issues = {i.id: i for i in cases.existing_issues(ctx.conversation_sid, (triage or {}).get('issue_refs'))}

    def opened():
        for action in actions:
            if isinstance(action, dict) and action.get('type') == 'CREATE_ISSUE' and (action.get('done') or {}).get('issue_pk'):
                issue = AIIssue.objects.filter(id=action['done']['issue_pk']).first()
                if issue:
                    issues[issue.id] = issue
    opened()
    if not issues and ctx.meta.get('event_type') == 'TENANT_MESSAGE':
        # The AI named a deadline but opened no case: the backend opens it, or nobody would be reminded
        when = _local(deadline)
        summary = str(triage.get('next_action') or '').strip() or f"Tenant needs it by {when:%a} {when.day} {when:%b %H:%M}"
        case = {'type': 'CREATE_ISSUE', 'temp_id': 'deadline-1', 'summary': summary[:200], 'owner': triage.get('owner') or 'Edy',
                'priority': triage.get('priority') or 'urgent', 'backend_added': True}
        actions.append(case)
        _ensure_issue(ctx, actions, {'issue_id': 'deadline-1'}, AUTO)
        opened()
    for issue in issues.values():
        if issue.is_open and issue.tenant_deadline != deadline:
            cases.apply_triage(issue, triage, ctx.ai_run, tenant_event=False)
    ids = list(AIFollowUp.objects.filter(issue_id__in=list(issues), kind=AIFollowUp.KIND_DEADLINE_REMINDER,
                                         status=AIFollowUp.STATUS_PENDING).values_list('id', flat=True))
    if ids:
        actions.append({'type': 'DEADLINE', 'deadline': deadline.isoformat(), 'backend_added': True,
                        'done': {'by': AUTO, 'at': timezone.now().isoformat(), 'label': 'created', 'followup_ids': ids}})


def results(stored, actions=()):
    """AIRun.actions rows: what was carried out at once is 'executed', the rest stays 'planned'."""
    known = [id(row.get('action')) for row in stored]
    stored += [{'action': a, 'status': 'planned', 'detail': 'added by the backend'} for a in actions
               if isinstance(a, dict) and a.get('backend_added') and id(a) not in known]
    for row in stored:
        action = row.get('action') if isinstance(row.get('action'), dict) else {}
        if action.get('done'):
            row.update(status=agent_actions.STATUS_EXECUTED, detail=f"done at once: {action['done'].get('detail') or action['done']['label']}")
        elif action.get('removed_by') and str(action['removed_by']).startswith('backend'):
            row.update(status=agent_actions.STATUS_REJECTED, detail=str(action['removed_by']))
    return stored


def _existing_issue(ctx, ref):
    from mysite.models import AIIssue
    match = re.fullmatch(r'[it]-(\d+)', str(ref or '').strip())
    return AIIssue.objects.filter(id=int(match.group(1)), conversation_sid=ctx.conversation_sid).first() if match else None


def _run(ctx, action):
    try:
        return agent_actions.HANDLERS[action['type']](ctx, action)
    except agent_actions.ActionError as e:
        return agent_actions.STATUS_REJECTED, str(e)


def _ensure_issue(ctx, actions, action, by):
    """Creates the new case the action is about when it does not exist yet ("also creates the case")."""
    ref = str(action.get('issue_id') or action.get('ticket_id') or '')
    for candidate in actions:
        if isinstance(candidate, dict) and candidate.get('type') == 'CREATE_ISSUE' and str(candidate.get('temp_id')) == ref \
                and not candidate.get('done'):
            status, detail = _run(ctx, candidate)
            if status == agent_actions.STATUS_EXECUTED and ref in ctx.temp_ids:
                issue = ctx.temp_ids[ref]
                # The case is as urgent as the most urgent thing proposed for it (reminder times depend on it)
                rank = ['routine', 'urgent', 'emergency']
                wanted = [a.get('priority') for a in actions if isinstance(a, dict) and a.get('priority') in rank
                          and ref in (str(a.get('temp_id')), str(a.get('issue_id')), str(a.get('ticket_id')))]
                if ctx.meta.get('v5_priority') in rank:
                    wanted.append(ctx.meta['v5_priority'])   # how urgent the AI rated the message as a whole
                top = max(wanted + [issue.priority], key=rank.index)
                if top != issue.priority:
                    issue.priority = top
                    issue.save()
                _done(candidate, by, 'case opened', issue_pk=issue.id, detail=str(detail)[:200])


def blocks(actions):
    """[(kind, index, action)] in alert order: task, update, CRM change, reminder, knowledge."""
    found = {'task': [], 'update': [], 'crm': [], 'reminder': [], 'knowledge': []}
    for index, action in enumerate(actions or []):
        if not isinstance(action, dict) or action.get('removed_by'):
            continue
        kind = action.get('type')
        if kind == 'CREATE_TICKET' and not action.get('existing_task'):
            found['task'].append(('task', index, action))
        elif kind in ('TICKET_COMMENT', 'UPDATE_TICKET') and action.get('task_title') and not action.get('with_close'):
            found['update'].append(('update', index, action))
        elif kind == 'UPDATE_ISSUE_STATE' and action.get('closes_task'):
            found['update'].append(('update', index, action))
        elif kind == 'SCHEDULE_FOLLOWUP' and (action.get('done') or {}).get('followup_id'):
            found['reminder'].append(('reminder', index, action))
        elif kind == 'DEADLINE' or (kind == 'CANCEL_FOLLOWUP' and action.get('open_reminder')):
            found['reminder'].append(('reminder', index, action))
        elif kind == 'CRM_CHANGE' and action.get('label'):
            found['crm'].append(('crm', index, action))
        elif kind == 'KB_UPDATE' and str(action.get('source') or '').strip():
            found['knowledge'].append(('knowledge', index, action))   # a fact without a source message is not shown
    return found['task'] + found['update'] + found['crm'] + found['reminder'] + found['knowledge']


def _numbered(items):
    """{action index: '' or ' 2'}: several of the same kind are numbered."""
    numbers, per_kind = {}, {}
    for kind, index, _ in items:
        per_kind.setdefault(kind, []).append(index)
    for indexes in per_kind.values():
        for position, index in enumerate(indexes, 1):
            numbers[index] = f" {position}" if len(indexes) > 1 else ''
    return numbers


def team_notes(actions):
    """What the AI wants the team to know when it proposes nothing else (no answer, no block)."""
    return [a for a in actions or [] if isinstance(a, dict) and a.get('type') in ('INTERNAL_ALERT', 'QUEUE_FOR_REVIEW')
            and not a.get('removed_by') and str(a.get('text') or '').strip()]


def waiting(delivery, actions, meta=None):
    """True while something on the alert still waits for a press."""
    reminder = (meta or {}).get('reminder') or {}
    if reminder.get('needed') and reminder_buttons(0, reminder, None):
        return True
    return bool((delivery or {}).get('held')) or any(
        not action.get('done') or (kind == 'reminder' and not action['done'].get('closed')) for kind, _, action in blocks(actions))


def closed_reminders(actions):
    """Reminders this run closed quietly because they are no longer needed (shown on team alerts only)."""
    return [a['closed_reminder'] for a in actions or [] if isinstance(a, dict) and a.get('type') == 'CANCEL_FOLLOWUP'
            and a.get('closed_reminder') and a.get('done')]


def has_content(parsed, delivery, actions, meta=None):
    """An alert is posted only when the manager has something to decide or to know (rule 1.1.5). What the AI sends or
    holds by itself is always shown."""
    if (meta or {}).get('reminder'):
        return bool(meta['reminder'].get('needed'))   # already done: closed quietly, no alert (C5)
    return bool((meta or {}).get('notification')) or bool((parsed or {}).get('answer')) or bool(blocks(actions)) or bool(team_notes(actions))


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------

def _join(texts):
    """Several messages in a row become one text: "Hi, the sink is dripping. Now there's water on the floor"."""
    parts = [re.sub(r'\s+', ' ', text or '').strip() for text in texts]
    parts = [part for part in parts if part]
    return ' '.join(part + '.' if index < len(parts) - 1 and part[-1].isalnum() else part for index, part in enumerate(parts))


def conversation_lines(conversation_sid, until_message, tenant_name, team=False, named=False):
    """
    The message part of an alert (rule 1.2.12). Returns (lines, names of who wrote now).
    '↪️ Edy (team) "…" ↪️' = before: the other side's last messages joined into one text;
    '💬 "…" 💬' = now: all messages of this side written after that, joined - the new ones and the earlier ones in a
    row. team=False: the tenant wrote now; team=True: the team wrote now (several members are one side).
    """
    from mysite import conversation_groups
    from mysite.ai_agent import inputs
    from mysite.models import TwilioMessage

    sids = conversation_groups.group_sids(conversation_sid)
    messages = TwilioMessage.objects.filter(conversation_sid__in=sids).exclude(message_sid__startswith='KB-UPDATE-')
    if until_message:
        messages = inputs._until(messages, until_message)
    ai_answers = inputs._ai_answers(sids)
    from mysite.ai_agent import after_hours
    first_name = str(tenant_name or 'Tenant').split()[0]
    automatic = after_hours.ack_text().strip()
    now, before, writers = [], [], []
    for message in messages.order_by('-message_timestamp', '-id')[:60]:
        role, name = inputs.classify_sender(message, ai_answers)
        text = inputs._strip_ui_markers(message.body)
        if role != inputs.ROLE_TENANT and text.strip() == automatic:
            continue   # the automatic after-hours text is not "what the other side wrote": the tenant's messages stay one line
        label = f"{first_name} (tenant)" if role == inputs.ROLE_TENANT else 'AI (sent)' if role == inputs.ROLE_AI else f"{name} (team)"
        mine = (role == inputs.ROLE_STAFF) if team else (role == inputs.ROLE_TENANT)
        if mine and not before:
            now.insert(0, text)
            if name not in writers:
                writers.insert(0, name)
        elif mine or not now:
            break   # an older message of this side, or the newest message is not from this side
        elif team and role == inputs.ROLE_AI:
            if before:
                break   # the tenant's messages since the AI's last answer are enough: one question, not the whole day
            continue   # the AI answered the tenant's last message: it is not "what the other side wrote" - look past it
        else:
            before.insert(0, (label, text))
    lines = []
    if before:
        who = ", ".join(dict.fromkeys(label for label, _ in before))
        lines += [f"↪️ {who} \"{_join([text for _, text in before])}\" ↪️", ""]
    lines.append(f"💬 \"{_join(now)}\" 💬")
    return lines, writers


def last_exchange(conversation_sid, tenant_name, skip_text=None):
    """The two lines of an alert nobody's message started (rule 1.3.7): the last joined message of each side, with
    names, the older side first - from all chats of the tenant."""
    from mysite import conversation_groups
    from mysite.ai_agent import after_hours, inputs
    from mysite.models import TwilioMessage

    sids = conversation_groups.group_sids(conversation_sid, fresh=True)
    messages = TwilioMessage.objects.filter(conversation_sid__in=sids).exclude(message_sid__startswith='KB-UPDATE-')
    ai_answers = inputs._ai_answers(sids)
    first_name = str(tenant_name or 'Tenant').split()[0]
    automatic = after_hours.ack_text().strip()
    runs = []   # newest first: [is tenant, [labels], [texts]]
    for message in messages.order_by('-message_timestamp', '-id')[:60]:
        role, name = inputs.classify_sender(message, ai_answers)
        text = inputs._strip_ui_markers(message.body)
        if role != inputs.ROLE_TENANT and text.strip() in (automatic, (skip_text or '').strip()):
            continue   # the automatic after-hours text, and the message this alert itself is about
        tenant = role == inputs.ROLE_TENANT
        label = f"{first_name} (tenant)" if tenant else 'AI (sent)' if role == inputs.ROLE_AI else f"{name} (team)"
        if runs and runs[-1][0] == tenant:
            runs[-1][1].insert(0, label)
            runs[-1][2].insert(0, text)
        elif len(runs) == 2:
            break
        else:
            runs.append([tenant, [label], [text]])
    lines = []
    for mark, (_, labels, texts) in zip(('💬', '↪️'), runs):
        lines.insert(0, f"{mark} {', '.join(dict.fromkeys(labels))} \"{_join(texts)}\" {mark}")
    return lines[:1] + [""] + lines[1:] if len(lines) == 2 else lines


def compose_notification(ctx, ai_run, parsed, delivery, actions):
    """🤖 AI MESSAGE for an automatic notification: sent (H1), waiting in a test apartment (H3), or held by the AI (H2)."""
    from datetime import datetime
    from mysite.ai_agent import team_notify
    from mysite.models import AIRun
    meta = ctx.meta
    info, live = meta['notification'], meta.get('mode') == 'live'
    done = (ai_run.review or {}).get('answer_done') or {}
    head = [f"🏠 {meta.get('apartment')}", f"👤 {meta.get('tenant') or 'Unknown'}", f"📅 {info['label']}"]
    if info['held'] and not done:
        head.append("⏸ HELD")
    if not live:
        head.append("🧪 TEST")
    lines = [f"🤖 AI MESSAGE · {_stamp(datetime.fromisoformat(info['at']))}", "", " · ".join(head), "", LINE]
    talk = last_exchange(ai_run.conversation_sid, meta.get('tenant'), skip_text=ai_run.final_answer or parsed.get('answer'))
    if talk:
        lines += talk + [LINE]
    lines += [f"🤖 \"{parsed.get('answer') or info['template']}\" 🤖", LINE]
    reason = info.get('reason') or ''
    if done:
        status = done.get('label') or "✅ Sent"
    elif ai_run.hold_status == AIRun.HOLD_CANCELLED:
        status = "❌ Not sent"
    elif info['held']:
        status = f"⏸ NOT sent – {reason or 'the AI is not sure it is still needed'}"
    elif info.get('sent_at'):
        status = f"✅ Sent {_hm(datetime.fromisoformat(info['sent_at']))}" + (f" – still needed: {reason}" if reason else "")
    elif info.get('waits_until'):
        status = f"⏳ Will be sent {_hm(datetime.fromisoformat(info['waits_until']))} (SMS hours)" + (f" – still needed: {reason}" if reason else "")
    elif info.get('failed'):
        status = f"❌ Could not send ({info['failed']})"
    else:
        status = "🧪 Test mode: NOT sent automatically. Press to send for real."
    lines += [status, LINE]
    items = blocks(actions)
    numbers = _numbered(items)
    for kind, index, action in items:
        lines += _block_lines(kind, action, numbers[index], ctx) + [LINE]
    lines += [
        "", FOOTER, "",
        f"📤 AI sends to chat: {'ON (live)' if live else 'OFF (test)'} · 🎫 AI Auto ClickUp: {'ON' if clickup.writes_enabled(ctx.apartment) else 'OFF'}",
        f"🔗 AI run: {team_notify.report_url(ai_run.id)}",
        f"💬 CRM chat: {config.site_url()}/chat/{ai_run.conversation_sid}/",
    ]
    return "\n".join(lines)


def _repeat_line(conversation_sid, triage):
    """'🔁 Asked 2 times · waiting 20 h · task open' for a case the tenant asks about again."""
    from mysite.ai_agent import cases
    for issue in cases.existing_issues(conversation_sid, (triage or {}).get('issue_refs')):
        hours = int((timezone.now() - issue.created_at).total_seconds() // 3600)
        if issue.tenant_asks >= 2 and issue.is_open and hours >= 1:   # a second message minutes later is not "asking again"
            waited = f"{hours} h" if hours < 48 else f"{hours // 24} days"
            return f"🔁 Asked {issue.tenant_asks} times · waiting {waited}" + (" · task open" if issue.ticket_ref else "")
    return None


def head_notes(meta, conversation_sid):
    """What already happened by itself for this message, for the header line: the after-hours message, the call."""
    from mysite import conversation_groups
    from mysite.models import AIAfterHoursAck as Ack, AIAlertCall, AIEvent
    notes = []
    events = AIEvent.objects.filter(id__in=meta.get('event_ids') or [])
    acks = list(Ack.objects.filter(event__in=events).order_by('id'))
    shown = next((a for a in reversed(acks) if a.status in (Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND, Ack.STATUS_FAILED)), None) \
        or next((a for a in reversed(acks) if a.status == Ack.STATUS_SUPPRESSED and 'already' in (a.reason or '')), None)
    if shown and shown.status == Ack.STATUS_SENT:
        notes.append(f"🌙 after-hours message sent {_hm(shown.sent_at)}")
    elif shown and shown.status == Ack.STATUS_WOULD_SEND:
        notes.append(f"🌙 after-hours message NOT sent (test) {_hm(shown.sent_at)}")
    elif shown and shown.status == Ack.STATUS_FAILED:
        notes.append("🌙 after-hours message FAILED")
    elif shown:
        earlier = Ack.objects.filter(conversation_sid__in=conversation_groups.group_sids(conversation_sid), sent_at__isnull=False,
                                     status__in=(Ack.STATUS_SENT, Ack.STATUS_WOULD_SEND), id__lt=shown.id).order_by('-sent_at').first()
        notes.append(f"🌙 already sent {_hm(earlier.sent_at)}" if earlier else "🌙 already sent")
    first = events.order_by('created_at').first()
    call = AIAlertCall.objects.filter(conversation_sid__in=conversation_groups.group_sids(conversation_sid),
                                      created_at__gte=first.created_at - timedelta(minutes=1)).order_by('-id').first() if first else None
    if call and call.status != AIAlertCall.STATUS_SKIPPED:
        at = _hm(call.created_at)
        notes.append({AIAlertCall.STATUS_ANSWERED: f"📞 {call.staff_name} called {at} – answered",
                      AIAlertCall.STATUS_UNANSWERED: f"📞 {call.staff_name} called {at} – no answer",
                      AIAlertCall.STATUS_SIMULATED: f"📞 call to {call.staff_name} simulated (test)",
                      AIAlertCall.STATUS_FAILED: f"📞 call to {call.staff_name} FAILED"}.get(call.status, f"📞 calling {call.staff_name} {at}"))
    return notes


def _task_title(action, unit):
    title = re.sub(r'^\s*\[AI\]\s*', '', str(action.get('title') or 'Task')).strip()
    return title if not unit or str(unit).lower() in title.lower() else f"{title} – {unit}"


def _block_lines(kind, action, number, ctx):
    from mysite.models import AIFollowUp
    if kind == 'task':
        priority = action.get('priority') or 'routine'
        who = ", ".join(_names(action.get('responsible')) or _names(action.get('case_owner'))) or 'team'
        due = timezone.now() + timedelta(hours=clickup.DUE_IN_HOURS.get(priority, clickup.DUE_IN_HOURS['routine']))
        mark = f"🎫{number.strip()}"
        return [f"{mark} {_task_title(action, ctx.meta.get('apartment'))} {mark}",
                f"   {who} · {'🔴 urgent' if priority == 'urgent' else '🚨 emergency' if priority == 'emergency' else priority}"
                f" · due {_day_time(due, with_date=True)}"]
    if kind == 'update':
        mark = f"🔄{number.strip()}"
        if action.get('closes_task'):
            return [f"{mark} Close task \"{action['closes_task']}\" {mark}"]
        text = str(action.get('text') or f"status → {action.get('status')}")
        if action.get('raise_to'):
            return [f"{mark} Task \"{action['task_title']}\": make {action['raise_to']} + comment \"{text}\" {mark}"]
        return [f"{mark} Comment on task \"{action['task_title']}\":", f"\"{text}\" {mark}"]
    if kind == 'crm':
        mark = f"🗂{number.strip()}"
        why = str(action.get('reason') or '').strip().rstrip('.')
        return [f"{mark} CRM: {action['label']} {mark}"] + ([f"   why: {why}"] if why else [])
    if kind == 'reminder' and action.get('type') == 'DEADLINE':
        from datetime import datetime
        mark = f"⏰{number.strip()}"
        when = _local(datetime.fromisoformat(action['deadline']))
        return [f"{mark} Deadline {when:%a} {when.day} {when:%b %H:%M} – reminders 24 h and 2 h before (Kevin at 2 h) {mark}"]
    if kind == 'reminder' and action.get('open_reminder'):
        from datetime import datetime
        mark = f"⏰{number.strip()}"
        info = action['open_reminder']
        return [f"{mark} Open reminder \"{str(info['reason']).rstrip('. ')}\" ({_day_time(datetime.fromisoformat(info['due']))}) {mark}"]
    if kind == 'reminder':
        done = action['done']
        followup = AIFollowUp.objects.filter(id=done['followup_id']).first()
        due = followup.due_at if followup else timezone.now()
        mark = f"⏰{number.strip()}"
        to_tenant = ', to tenant' if action.get('kind') in ('tenant_nudge', 'second_tenant_nudge') else ''
        return [f"{mark} {str(action.get('reason') or 'Reminder').rstrip('. ')} – {_day_time(due)} ({done.get('number', '1/2')}{to_tenant}) {mark}"]
    text = str(action.get('text') or action.get('value') or '')
    return [f"📚 \"{text}\" 📚", f"   from: {action.get('source')}"]


def compose(ctx, ai_run, parsed, delivery, actions):
    from mysite.ai_agent import team_notify

    if ctx.meta.get('notification'):
        return compose_notification(ctx, ai_run, parsed, delivery, actions)
    if ctx.meta.get('reminder'):
        return compose_reminder(ctx, ai_run, parsed, delivery, actions)
    meta, triage = ctx.meta, parsed.get('triage') or {}
    live, team = meta.get('mode') == 'live', _team(meta)
    received = ai_run.message.message_timestamp if ai_run.message_id else timezone.now()
    talk, writers = conversation_lines(ai_run.conversation_sid, ai_run.message if ai_run.message_id else None, meta.get('tenant'), team=team)
    head = [f"🏠 {meta.get('apartment')}", f"👤 {meta.get('tenant') or 'Unknown'}"]
    # A team message is not an alarm: no urgency, the priority of a proposed task is on its own line (rule 1.2.1)
    head.append(f"🧑‍🔧 {', '.join(writers) or 'team'}" if team else URGENCY.get(triage.get('priority') or 'routine', URGENCY['routine']))
    if not live:
        head.append("🧪 TEST")
    if 'v5_head' not in meta:
        meta['v5_head'] = head_notes(meta, ai_run.conversation_sid)
    head += meta['v5_head']
    lines = [f"{'🧑‍🔧 TEAM MESSAGE' if team else '📨 TENANT MESSAGE'} · {_stamp(received)}", "", " · ".join(head), "", LINE] + talk
    closing = any(isinstance(a, dict) and a.get('closes_task') and not a.get('removed_by') for a in actions or [])
    repeat = None if team or closing else _repeat_line(ai_run.conversation_sid, triage)   # "it is fixed" is not asking again
    if repeat:
        lines.append(repeat)
    lines.append(LINE)
    if parsed.get('answer'):
        lines.append(f"🤖 \"{parsed['answer']}\" 🤖")
        if delivery.get('confirm') or parsed.get('needs_confirmation'):
            lines.append(f"⚖️ Contract: {parsed.get('contract_basis') or 'not given by the AI - check the contract'}")
        lines.append(LINE)
    missing = [str(u).strip() for u in triage.get('uncertainties') or [] if str(u).strip().lower().startswith(('not in the knowledge base', 'not in the crm'))]
    if missing and not team:
        lines += [f"❓ {missing[0]}", LINE]
    items = blocks(actions)
    numbers = _numbered(items)
    for kind, index, action in items:
        lines += _block_lines(kind, action, numbers[index], ctx) + [LINE]
    if team:
        for reason in closed_reminders(actions):
            lines += [f"✅ Reminder \"{str(reason).rstrip('. ')}\" closed – answered in the chat", LINE]
    if not parsed.get('answer') and not items:
        for note in team_notes(actions):
            who = ", ".join(_names(note.get('responsible') or note.get('owner'))) or 'team'
            lines += [f"❗ For {who}: {str(note.get('text')).strip()}", LINE]
    lines += [
        "", FOOTER, "",
        f"📤 AI sends to chat: {'ON (live)' if live else 'OFF (test)'} · "
        f"🎫 AI Auto ClickUp: {'ON' if clickup.writes_enabled(ctx.apartment) else 'OFF'}",
        f"🔗 AI run: {team_notify.report_url(ai_run.id)}",
        f"💬 CRM chat: {config.site_url()}/chat/{ai_run.conversation_sid}/",
    ]
    return "\n".join(lines)


def split_parts(text, limit=3800):
    """A long alert as several messages '(1/2)', '(2/2)': nothing is cut (rule 1.2.10). Lines stay whole; only a line
    that does not fit in a message (a very long tenant text) is broken, at a space, filling the message it starts in."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ''
    for line in text.split("\n"):
        while len(current) + len(line) + 1 > limit:
            room = limit - len(current) - 1
            if len(line) <= limit and current:   # the line fits a message of its own: start a new one
                pieces.append(current)
                current, room = '', limit
                continue
            cut = line.rfind(' ', 0, room)
            cut = cut if cut > 0 else room
            pieces.append(f"{current}\n{line[:cut]}" if current else line[:cut])
            current, line = '', line[cut:].lstrip()
        current = f"{current}\n{line}" if current else line
    if current.strip():
        pieces.append(current)
    return [f"({number}/{len(pieces)})\n{piece.strip(chr(10))}" for number, piece in enumerate(pieces, 1)]


# ---------------------------------------------------------------------------
# The buttons
# ---------------------------------------------------------------------------

def send_kind(mode, conversation_sid):
    """Which send buttons an answer gets: 'live' -> SMS; 'test' (test apartment, real Twilio chat) -> SMS and CRM;
    'crm' (a chat that exists only in the CRM) -> CRM."""
    from mysite.views import messaging
    if messaging.is_crm_only_chat(conversation_sid):
        return 'crm'
    return 'live' if mode == 'live' else 'test'


def keyboard(run_id, answer_waiting, actions, answer_done=None, edit_by=None, send_label=SEND_SMS, reminder=None,
             reminder_state=None, kind='live'):
    """One row per kind of block; a finished button shows its result and only answers "already done".
    reminder: the due reminder of a REMINDER alert - its own buttons come last. kind: send_kind()."""
    rows = []
    edit = _btn(f"✏️ Waiting for text · {edit_by}" if edit_by else '✏️ Edit Answer', 'ea', run_id)
    if answer_done:
        rows.append([_btn(answer_done['label'], 'dn', run_id, 'a')])
    elif answer_waiting and send_label != SEND_SMS:
        rows.append([_btn(send_label, 'sa', run_id), edit])   # Send now / Send anyway (a CRM-only chat writes it in the CRM)
    elif answer_waiting and kind == 'crm':
        rows.append([_btn(SEND_CRM, 'sm', run_id), edit])
    elif answer_waiting and kind == 'test':
        rows += [[_btn(SEND_SMS, 'sa', run_id), _btn(SEND_CRM, 'sm', run_id)], [edit]]
    elif answer_waiting:
        rows.append([_btn(SEND_SMS, 'sa', run_id), edit])
    items = blocks(actions)
    numbers = _numbered(items)
    open_tasks = [action for kind, _, action in items if kind == 'task' and not action.get('done')]
    if answer_waiting and not answer_done and open_tasks:
        rows.append([_btn(f"🤖🎫 Send + Create Task{'s' if len(open_tasks) > 1 else ''}", 'sc', run_id)])
    row_kind, row = None, []
    for kind, index, action in items:
        done = action.get('done') or {}
        if kind == 'reminder':
            closed = done.get('closed') or (action.get('open_reminder') and done.get('label'))
            buttons = [_btn(closed, 'dn', run_id, index) if closed else _btn(f"✅ Close Reminder{numbers[index]}", 'cr', run_id, index)]
        elif kind == 'knowledge':
            star = 'global' if action.get('scope') == 'company' else 'apartment'
            buttons = [_btn(done['label'], 'dn', run_id, index)] if done else [
                _btn("🏠📚 Apartment" + (" ⭐" if star == 'apartment' else ""), 'ka', run_id, index),
                _btn("🌍📚 Global" + (" ⭐" if star == 'global' else ""), 'kg', run_id, index)]
        elif done:
            buttons = [_btn(done['label'], 'dn', run_id, index)]
            if done.get('url'):
                buttons.append({'text': '↗ Open in ClickUp', 'url': done['url']})
        elif kind == 'task':
            buttons = [_btn(action.get('failed') or f"🎫 Create Task{numbers[index]}", 'ct', run_id, index)]
        elif kind == 'crm':
            buttons = [_btn(f"🗂 Apply in CRM{numbers[index]}", 'cc', run_id, index)]
        else:
            buttons = [_btn(action.get('failed') or ("🔄 Close Task" if action.get('closes_task') else "🔄 Apply Update") + numbers[index],
                            'up', run_id, index)]
        if kind != row_kind or len(row) + len(buttons) > 2 or kind == 'knowledge':
            if row:
                rows.append(row)
            row, row_kind = [], kind
        row += buttons
    if row:
        rows.append(row)
    if reminder:
        rows += reminder_buttons(run_id, reminder, reminder_state)
    return {'inline_keyboard': rows} if rows else None


def keyboard_for(run):
    from mysite.models import AIRun
    review = run.review or {}
    if review.get('stale'):
        return None
    answer_done = review.get('answer_done')
    if not answer_done and run.hold_status in (AIRun.HOLD_SENT, AIRun.HOLD_CORRECTED):
        answer_done = {'label': "✅ Answer sent"}   # sent another way (the corrected text, a typed "ok")
    meta = review.get('meta') or {}
    reminder = meta.get('reminder')
    if reminder and reminder.get('to_tenant') and reminder.get('mode') == 'live' and run.hold_status != AIRun.HOLD_HOLDING:
        answer_done = None   # the AI sent it by itself (D5): the status line says so, no button
    return keyboard(run.id, run.hold_status == AIRun.HOLD_HOLDING, (review.get('plan') or {}).get('actions') or [],
                    answer_done, review.get('edit_by'), send_label=_send_label(meta.get('notification'), reminder),
                    reminder=reminder, reminder_state=review.get('reminder_done'),
                    kind=send_kind(meta.get('mode') or run.mode, run.conversation_sid))


def _send_label(notification, reminder=None):
    if reminder and reminder.get('to_tenant'):
        return '🤖 Send now'
    if not notification:
        return SEND_SMS
    return '📤 Send anyway' if notification.get('held') else '🤖 Send now'


def post(ctx, ai_run, parsed, delivery, actions):
    """Posts the alert of a run, in the thread of the case. Routine is posted without sound; a team message always,
    unless it proposes an urgent task. A long alert goes out in parts, the buttons on the last one.
    Returns (ok, note, message id of the part with the buttons)."""
    from mysite.ai_agent import cases
    triage = parsed.get('triage') or {}
    note, reminder = ctx.meta.get('notification'), ctx.meta.get('reminder')
    auto = bool(reminder and reminder['to_tenant'] and ctx.meta.get('mode') == 'live' and not delivery.get('held'))
    markup = keyboard(ai_run.id, bool(delivery.get('held')), actions, send_label=_send_label(note, reminder),
                      reminder=reminder, kind=send_kind(ctx.meta.get('mode'), ai_run.conversation_sid))
    refs = list(triage.get('issue_refs') or [])
    if reminder:
        refs.append(reminder.get('issue_id'))
        # urgent, an emergency or 2 h before a deadline: with sound; a message the AI sent by itself: none
        loud = not auto and (reminder_priority(reminder, triage) != 'routine')
    elif note:
        loud = bool(note.get('held'))   # held: somebody has to look; sent or test: no sound
    elif _team(ctx.meta):
        loud = any(kind == 'task' and action.get('priority') in ('urgent', 'emergency') for kind, _, action in blocks(actions))
    else:
        loud = (triage.get('priority') or 'routine') != 'routine'
    parts = split_parts(compose(ctx, ai_run, parsed, delivery, actions))
    reply_to = cases.thread_for(ai_run.conversation_sid, [r for r in refs if r])
    result, earlier = (False, 'nothing to post', None), []
    for number, part in enumerate(parts, 1):
        last = number == len(parts)
        result = send_ai_chat(part, reply_markup=markup if last else None, reply_to=reply_to, silent=not loud)
        if not result[0]:
            break
        if not last:
            earlier.append(result[2])
            reply_to = result[2]
    if earlier and delivery is not None:
        delivery['telegram_part_ids'] = earlier   # a reply to any part reaches the run
    return result


def post_after_hours(event, ack):
    """🤖 AI MESSAGE: the after-hours text the AI sent by itself (or, in a test apartment, would have sent) - D1 / D2."""
    from mysite.models import AIAfterHoursAck as Ack, TwilioConversation
    conversation = TwilioConversation.objects.filter(conversation_sid=event.conversation_sid).select_related('apartment', 'booking__tenant').first()
    tenant = getattr(getattr(getattr(conversation, 'booking', None), 'tenant', None), 'full_name', None)
    live = ack.status == Ack.STATUS_SENT
    talk, _ = conversation_lines(event.conversation_sid, event.message if event.message_id else None, tenant)
    head = [f"🏠 {getattr(getattr(conversation, 'apartment', None), 'name', '?')}", f"👤 {tenant or 'Unknown'}", "🌙 After-hours message"]
    if not live:
        head.append("🧪 TEST")
    when = ack.sent_at or timezone.now()
    lines = [f"🤖 AI MESSAGE · {_stamp(when)}", "", " · ".join(head), "", LINE] + talk + [
        LINE, f"🤖 \"{ack.text}\" 🤖", LINE,
        f"✅ Sent to the tenant {_hm(when)}" if live else "🧪 NOT sent – test mode. In live this text would go to the tenant now.", LINE,
        "", f"📤 AI sends to chat: {'ON (live)' if live else 'OFF (test)'}", f"💬 CRM chat: {config.site_url()}/chat/{event.conversation_sid}/"]
    result, reply_to = (False, 'nothing to post', None), None
    for part in split_parts("\n".join(lines)):   # a very long tenant message: in parts, never cut
        result = send_ai_chat(part, silent=True, reply_to=reply_to)
        reply_to = result[2]
    return result


def answered_by_team(staff_run):
    """A team member wrote in the chat while an AI answer still waited (A18): that answer is not needed any more. Its
    alert keeps the task / reminder buttons; only Send / Edit turn into the note."""
    from mysite.ai_agent import inputs
    from mysite.models import AIRun
    _, approval = _review()
    message = staff_run.message
    if not message:
        return
    name = inputs.classify_sender(message)[1]
    older = AIRun.objects.filter(conversation_sid=staff_run.conversation_sid, id__lt=staff_run.id, hold_status=AIRun.HOLD_HOLDING,
                                 review__style='v5')
    for run in older:
        if not AIRun.objects.filter(id=run.id, hold_status=AIRun.HOLD_HOLDING).update(
                hold_status=AIRun.HOLD_SUPPRESSED, delivery_note=f"not sent - {name} answered in the chat"[:255]):
            continue
        run.refresh_from_db()
        approval._update_review(run, answer_done={'by': name, 'at': timezone.now().isoformat(),
                                                  'label': f"✅ {name} answered in the chat {_hm(message.message_timestamp)} – AI answer not needed"},
                                staff_answer={'by': name, 'text': message.body[:2000]})   # with the AI's draft, for learning (1.5)
        if run.telegram_message_id:
            edit_reply_markup(run.telegram_message_id, keyboard_for(run))


def mark_outdated(run, newer_run):
    """The tenant wrote again: the older alert loses its buttons and says so (rule 1.2.9, A17); so do the change
    proposals the bot made under it (F6)."""
    from mysite.ai_agent.notify import edit_message_text
    _, approval = _review()
    who = str(((run.review or {}).get('meta') or {}).get('tenant') or 'The tenant').split()[0]
    at = _hm(newer_run.message.message_timestamp) if newer_run.message_id else _hm()
    line = f"⚠️ Outdated – {who} wrote again {at}, see the newer alert"
    approval._update_review(run, outdated=line)
    refresh_alert(run)
    for proposal in (run.review or {}).get('proposals') or []:
        if proposal.get('message_id') and proposal.get('report') and not all((proposal.get('done') or {}).values() or [False]):
            edit_message_text(proposal['message_id'], f"{proposal['report']}\n\n⚠️ Outdated – {who} wrote again {at}", None)


def after_post(run, tenant_event):
    """The cases opened at once get the run's triage and this alert as their Telegram thread."""
    from mysite.ai_agent import cases
    actions = ((run.review or {}).get('plan') or {}).get('actions') or []
    opened = {str(a.get('temp_id')): a['done']['issue_pk'] for a in actions
              if isinstance(a, dict) and a.get('type') == 'CREATE_ISSUE' and (a.get('done') or {}).get('issue_pk')}
    if opened:
        cases.after_apply(run, opened, tenant_event)


# ---------------------------------------------------------------------------
# Presses
# ---------------------------------------------------------------------------

def _review():
    from mysite.ai_agent import answer_review, approval
    return answer_review, approval


def _context(run):
    """(action context with the cases this run already opened, plan) for carrying out one of the run's actions."""
    from mysite.models import AIIssue
    ar, _ = _review()
    plan = ar._plan(run)
    ctx = ar._ctx_for(run)
    for action in plan.get('actions') or []:
        if isinstance(action, dict) and action.get('type') == 'CREATE_ISSUE' and (action.get('done') or {}).get('issue_pk'):
            issue = AIIssue.objects.filter(id=action['done']['issue_pk']).first()
            if issue:
                ctx.temp_ids[str(action.get('temp_id'))] = issue
    return ctx, plan


def _finish(run, plan, author, what, detail=''):
    ar, approval = _review()
    if plan:
        ar._save_plan(run, plan)
    run.refresh_from_db()
    approval._record(run, author, what, detail)
    if run.telegram_message_id:
        edit_reply_markup(run.telegram_message_id, keyboard_for(run))


def send_answer(run, author, crm=False):
    """🤖 Send Answer (SMS): sends it, nothing else and no question (user, 2026-10-06: no confirmations anywhere - who wants
    the task too presses 🤖🎫 Send + Create Task). crm (📝 Send Answer (CRM)): written into the CRM chat only, no SMS.
    Returns (popup text, show it as a dialog)."""
    from mysite.models import AIRun
    ar, approval = _review()
    review = run.review or {}
    if run.hold_status != AIRun.HOLD_HOLDING:
        done = review.get('answer_done') or {}
        return (f"already done by {done.get('by')} {_hm_of(done)}" if done else "The answer is not waiting any more."), False
    newer = approval.tenant_wrote_after(run)
    if newer:
        first = (ar._plan(run).get('meta') or {}).get('tenant') or 'the tenant'
        return f"⛔ Not sent – {str(first).split()[0]} wrote again at {_hm(newer.message_timestamp)}. See the newer alert.", True
    result = ar._claim_and_release(run, run.answer, AIRun.HOLD_SENT,
                                   f"Send Answer ({'CRM' if crm else 'SMS'}) pressed by {author}", announce=False, crm_only=crm)
    run.refresh_from_db()
    if result is None:
        return "The answer was already handled.", False
    status, note = result
    if status == AIRun.HOLD_HOLDING:
        return f"⛔ Not sent – {note}", True
    if status == AIRun.HOLD_SUPPRESSED:
        label = "✅ The team answered in the chat – AI answer not needed"
    elif status == AIRun.HOLD_FAILED:
        label = "❌ Send failed – see below"
        approval._say(run, f"❌ Could not send to {(ar._plan(run).get('meta') or {}).get('tenant') or 'the tenant'} ({note})")
    elif str(note).startswith('held'):
        label = f"⏳ Answer will be sent {_hm(config.next_notification_window_start())} · {author}"
    elif crm:
        label = f"✅ Sent to CRM · {author} {_hm()}"
    else:
        label = f"✅ Answer sent · {author} {_hm()}"
    notification = ((run.review or {}).get('meta') or {}).get('notification')
    if notification and label.startswith('✅ Answer sent'):
        label = f"✅ Sent · {author} {_hm()}"
    approval._update_review(run, answer_done={'by': author, 'at': timezone.now().isoformat(), 'label': label})
    _finish(run, None, author, 'send answer', f"{status}: {note}")
    if notification:
        refresh_alert(run)
    return label, False


def _hm_of(done):
    from datetime import datetime
    try:
        return _hm(datetime.fromisoformat(done['at']))
    except (KeyError, TypeError, ValueError):
        return ''


def create_task(run, index, author):
    """🎫 Create Task N: the ClickUp task for real (and the case when there is none yet). Returns the popup text."""
    from mysite.ai_agent import team_notify
    ctx, plan = _context(run)
    action = plan['actions'][index]
    if action.get('done'):
        return f"already done by {action['done']['by']} {_hm_of(action['done'])}"
    _ensure_issue(ctx, plan['actions'], action, author)
    with clickup.pressed():
        status, detail = _run(ctx, action)
        groups, note = team_notify.deliver_clickup_now(ctx, plan.get('trigger_text'), run)
    group = groups[0] if groups else {}
    if status != agent_actions.STATUS_EXECUTED or 'FAILED' in str(note):
        action['failed'] = "❌ Task not created – ClickUp error · press to retry"
        _finish(run, plan, author, f"create task {index}", f"FAILED: {detail} {note}")
        return "ClickUp error - the task was NOT created. Press again to retry."
    action.pop('failed', None)
    url = group.get('task_url') if group.get('task_created') else None
    label = f"✅ Task created · {author} {_hm()}" if url else f"✅ Task noted · {author} {_hm()} (no ClickUp List)"
    _done(action, author, label, url=url if str(url).startswith('http') else None, detail=str(note)[:200])
    _finish(run, plan, author, f"create task {index}", str(note))
    return "Task created" if url else "Noted - this apartment has no ClickUp List"


def apply_update(run, index, author):
    """🔄 Apply Update N / Close Task: comment on, change or close the existing ClickUp task."""
    ctx, plan = _context(run)
    action = plan['actions'][index]
    if action.get('done'):
        return f"already done by {action['done']['by']} {_hm_of(action['done'])}"
    with clickup.pressed():
        for other in plan['actions']:   # the comment that belongs to this close goes first
            if action.get('closes_task') and isinstance(other, dict) and other.get('with_close') and not other.get('done') \
                    and other.get('task_issue') == action.get('task_issue'):
                _run(ctx, other)
                _done(other, author, 'commented with the close')
        status, detail = _run(ctx, action)
    if status != agent_actions.STATUS_EXECUTED or 'FAILED' in str(detail):
        action['failed'] = "❌ Not done – ClickUp error · press to retry"
        _finish(run, plan, author, f"update {index}", f"FAILED: {detail}")
        return "ClickUp error - nothing was changed. Press again to retry."
    action.pop('failed', None)
    if action.get('raise_to') and clickup.delivery_mode() == 'api':
        from mysite.models import AIIssue
        issue = AIIssue.objects.filter(id=action.get('task_issue')).first()
        if issue and issue.ticket_ref:
            try:
                with clickup.pressed():
                    clickup.update_task(issue.ticket_ref, priority=action['raise_to'])
                issue.priority = action['raise_to']
                issue.save()
            except clickup.ClickUpError as e:
                detail = f"{detail}; priority NOT raised ({str(e)[:120]})"
    _done(action, author, f"✅ {'Closed' if action.get('closes_task') else 'Commented'} · {author} {_hm()}", detail=str(detail)[:200])
    if action.get('closes_task'):   # closing the task also stops the reminders of its case: show that on their buttons
        from mysite.models import AIFollowUp
        for other in plan['actions']:
            info = other.get('open_reminder') if isinstance(other, dict) else None
            if info and not (other.get('done') or {}).get('closed') and not AIFollowUp.objects.filter(id=info['followup_id'], status='pending').exists():
                other.setdefault('done', {}).update(closed=f"✅ Reminder closed · {author}", closed_by=author, closed_at=timezone.now().isoformat(),
                                                    by=author, at=timezone.now().isoformat(), label='closed')
    _finish(run, plan, author, f"update {index}", str(detail))
    return "Done"


def close_reminder(run, index, author):
    """✅ Close Reminder N: that reminder (for a deadline: both of its reminders) will not fire."""
    from mysite.models import AIFollowUp
    ar, _ = _review()
    plan = ar._plan(run)
    action = plan['actions'][index]
    done = action.setdefault('done', {})
    if done.get('closed'):
        return f"already done by {done.get('closed_by')} {_hm_of({'at': done.get('closed_at')})}"
    ids = done.get('followup_ids') or [done.get('followup_id') or (action.get('open_reminder') or {}).get('followup_id')]
    AIFollowUp.objects.filter(id__in=[i for i in ids if i], status=AIFollowUp.STATUS_PENDING).update(
        status=AIFollowUp.STATUS_CANCELLED, status_note=f"closed by {author} in Telegram"[:255], updated_at=timezone.now())
    done.update(closed=f"✅ Reminder closed · {author}", closed_by=author, closed_at=timezone.now().isoformat(), by=done.get('by') or author,
                at=done.get('at') or timezone.now().isoformat(), label=done.get('label') or 'closed')
    _finish(run, plan, author, f"close reminder {index}")
    return "Reminder closed"


def save_knowledge(run, index, scope, author):
    """🏠📚 Apartment / 🌍📚 Global: the fact is saved, verified, in that scope."""
    ctx, plan = _context(run)
    action = plan['actions'][index]
    if action.get('done'):
        return f"already done by {action['done']['by']} {_hm_of(action['done'])}"
    action.update(scope=scope, approved_by=author)
    status, detail = _run(ctx, action)
    if status != agent_actions.STATUS_EXECUTED:
        return f"Not saved: {str(detail)[:150]}"
    _done(action, author, f"✅ Saved to {'global' if scope == 'company' else 'apartment'} · {author}", detail=str(detail)[:200])
    _finish(run, plan, author, f"save knowledge {index} ({scope})", str(detail))
    return "Saved"


def apply_crm(run, index, author):
    """🗂 Apply in CRM: the proposed record change is written to the CRM, checked once more against its state now."""
    ctx, plan = _context(run)
    action = plan['actions'][index]
    if action.get('done'):
        return f"already done by {action['done']['by']} {_hm_of(action['done'])}"
    action['approved_by'] = author
    status, detail = _run(ctx, action)
    if status != agent_actions.STATUS_EXECUTED:
        action.pop('approved_by', None)
        return f"⛔ Not changed – {str(detail)[:160]}"
    _done(action, author, f"✅ Done in CRM · {author} {_hm()}", detail=str(detail)[:200])
    _finish(run, plan, author, f"CRM change {index} ({action.get('change')})", str(detail))
    return "Done in the CRM"


def handle_callback(callback):
    """One press on a simple alert. Always answers the callback (else the button keeps spinning)."""
    from mysite.models import AIRun
    ar, approval = _review()
    callback_id, message = callback.get('id'), callback.get('message') or {}
    parts = (callback.get('data') or '').split('|')
    author = ar._author(callback.get('from') or {})
    if len(parts) != 4 or str((message.get('chat') or {}).get('id')) != str(ai_chat_id()):
        return answer_callback(callback_id, 'Unknown button')
    code, arg = parts[1], parts[3]
    run = AIRun.objects.filter(id=int(parts[2] or 0)).first()
    if not run:
        return answer_callback(callback_id, 'This alert no longer exists', alert=True)
    review = run.review or {}
    if review.get('stale'):
        answer_callback(callback_id, "⚠️ Outdated – see the newer alert.", alert=True)
        return edit_reply_markup(message.get('message_id'), None)
    if code in ('pa', 'ps', 'pr', 'pk', 'pg', 'pd'):   # a proposal made after a typed reply
        text = press_proposal(run, code, arg, author)
        log_info(f"AI alert run #{run.id}: {code} {arg} by {author}: {text}", category='sms')
        return answer_callback(callback_id, text, alert=text.startswith(('⛔', '⚠️', 'Too late')))
    actions = (review.get('plan') or {}).get('actions') or []
    index = int(arg) if arg.isdigit() else None
    if index is not None and index >= len(actions):
        return answer_callback(callback_id, 'This button is out of date')
    dialog = False
    if code == 'dn' and arg in ('r', 'g'):   # the reminder of a REMINDER alert: closed / reminded again
        state = review.get('reminder_done') or {}
        text = (f"already done by {state.get('closed_by')} {_hm_of({'at': state.get('closed_at')})}" if arg == 'r'
                else f"already done: {state.get('again') or ''}").strip()
    elif code == 'dn':
        done = review.get('answer_done') if arg == 'a' else (actions[index].get('done') or {})
        by = done.get('closed_by') or done.get('by')
        at = {'at': done.get('closed_at')} if done.get('closed_by') else done
        text = f"already done by {by} {_hm_of(at)}".strip()
    elif code == 'sa':
        text, dialog = send_answer(run, author)
    elif code == 'sm':
        text, dialog = send_answer(run, author, crm=True)
    elif code == 'sc':   # 🤖🎫 Send + Create Task: both, in one press
        failed = False
        for kind, i, action in blocks(actions):
            if kind == 'task' and not action.get('done'):
                create_task(run, i, author)
                run.refresh_from_db()
                failed = failed or not ((run.review or {}).get('plan') or {}).get('actions', [{}] * (i + 1))[i].get('done')
        if failed:
            text, dialog = "ClickUp error - the task was NOT created, so the answer was NOT sent. Press again to retry.", True
        else:
            text, dialog = send_answer(run, author)
    elif code == 'ea':
        approval._update_review(run, edit_by=author)
        approval.ask_for_text(run, author)
        _finish(run, None, author, 'edit answer')
        text = "Reply to my message with the correct text"
    elif code == 'ct':
        text = create_task(run, index, author)
    elif code == 'up':
        text = apply_update(run, index, author)
    elif code == 'cr':
        text = close_reminder(run, index, author)
    elif code == 'rc':
        text = close_due_reminder(run, author)
    elif code == 'rg':
        text = remind_again(run, author)
    elif code in ('ka', 'kg'):
        text = save_knowledge(run, index, 'company' if code == 'kg' else 'apartment', author)
    elif code == 'cc':
        text = apply_crm(run, index, author)
        dialog = text.startswith('⛔')
    else:
        text = 'Unknown button'
    log_info(f"AI alert run #{run.id}: {code} {arg} by {author}: {text}", category='sms')
    return answer_callback(callback_id, text, alert=dialog)


# ---------------------------------------------------------------------------
# Typed replies (part 7 of the document)
# ---------------------------------------------------------------------------
# A typed reply never changes anything by itself (rule 1.1.3). A question gets an answer. A change request gets a
# PROPOSAL: the bot says what it will change and shows a button; the change happens only after the press. The reply is
# read by the same interpreter as before (answer_review.interpret); what is new is that its result is stored as a
# proposal (AIRun.review['proposals']) instead of being carried out at once.

def render_text(run):
    """The alert's text from the run's current state (what refresh_alert writes)."""
    review = run.review or {}
    ctx, plan = _context(run)
    ctx.meta = dict(review.get('meta') or {}, **(ctx.meta or {}))
    ctx.meta.setdefault('v5_head', review.get('v5_head') or [])
    parsed = {'answer': run.final_answer or run.answer, 'triage': run.triage or {},
              'needs_confirmation': review.get('needs_confirmation'), 'contract_basis': review.get('contract_basis')}
    text = compose(ctx, run, parsed, {'confirm': review.get('needs_confirmation')}, plan.get('actions') or [])
    if review.get('outdated'):
        text = f"{review['outdated']}\n\n{text}"
    return text


def refresh_alert(run):
    """Writes the alert again from the run's current state (after a change of a task, a reminder, the answer)."""
    from mysite.ai_agent.notify import edit_message_text
    run.refresh_from_db()
    if not run.telegram_message_id:
        return
    text = render_text(run)
    if len(split_parts(text)) > 1:
        return edit_reply_markup(run.telegram_message_id, keyboard_for(run))   # a long alert in parts: only its buttons change
    edit_message_text(run.telegram_message_id, text, keyboard_for(run))


def _block_label(action):
    kind = action.get('type')
    if kind == 'CREATE_TICKET':
        return f"🎫 {action.get('title') or 'task'}"
    if kind == 'SCHEDULE_FOLLOWUP':
        return f"⏰ {str(action.get('reason') or 'reminder').rstrip('. ')}"
    if kind == 'CRM_CHANGE':
        return f"🗂 CRM: {action.get('label') or action.get('change')}"
    if kind == 'KB_UPDATE':
        return f"📚 {str(action.get('text') or action.get('value') or '')[:80]}"
    if kind in ('TICKET_COMMENT', 'UPDATE_TICKET'):
        return f"🔄 comment on \"{action.get('task_title') or 'the task'}\""
    if kind == 'UPDATE_ISSUE_STATE':
        return f"🔄 close task \"{action.get('closes_task') or 'the task'}\""
    return str(action.get('summary') or action.get('text') or kind)[:80]


def _task_line(action):
    who = ", ".join(_names(action.get('responsible')) or _names(action.get('case_owner'))) or 'team'
    return f"{who} · {action.get('priority') or 'routine'}"


def _plan_changes(run, plan_ops):
    """[(line(s) for the explanation, what to do on Apply)] for the interpreter's changes of this alert's blocks."""
    ar, _ = _review()
    plan = ar._plan(run)
    by_number = {item['n']: item for item in plan.get('items') or [] if item.get('n')}
    changes = []
    for op in plan_ops:
        item = by_number.get(op.get('n'))
        action = plan['actions'][item['idx']] if item else None
        if not isinstance(action, dict) or action.get('removed_by'):
            continue
        kind, done = action.get('type'), action.get('done') or {}
        if op.get('op') == 'remove':
            if kind == 'SCHEDULE_FOLLOWUP' and done.get('followup_id') and not done.get('closed'):
                changes.append((f"❌ Close the reminder: {_block_label(action)}", {'do': 'close_reminder', 'idx': item['idx']}))
            elif not done and kind in ('CREATE_TICKET', 'KB_UPDATE', 'TICKET_COMMENT', 'UPDATE_TICKET', 'UPDATE_ISSUE_STATE', 'CRM_CHANGE'):
                changes.append((f"❌ Remove from this alert: {_block_label(action)}", {'do': 'remove', 'idx': item['idx']}))
            continue
        if op.get('op') != 'change' or (done and kind != 'SCHEDULE_FOLLOWUP'):
            continue
        fields = _changes_for(action, op)
        fields = {k: v for k, v in fields.items() if v and v != action.get(k)}
        if not fields:
            continue
        after = dict(action, **fields)
        if kind == 'CREATE_TICKET':
            lines = [f"🎫 {action.get('title')}" + (f" → {after['title']}" if 'title' in fields else ''),
                     f"   now:   {_task_line(action)}", f"   after: {_task_line(after)}"]
            if set(fields) <= {'title', 'description'}:
                lines = lines[:1] + ([f"   description: {fields['description'][:200]}"] if 'description' in fields else [])
        else:
            key = next(iter(fields))
            lines = [f"{_block_label(action)}", f"   now:   {str(action.get(key) or '-')[:200]}", f"   after: {str(fields[key])[:200]}"]
        changes.append(("\n".join(lines), {'do': 'change', 'idx': item['idx'], 'fields': fields}))
    return changes


def _changes_for(action, op):
    """Fields of a plan change the reply interpreter read (plan_ops) -> the action's own field names."""
    kind, out = action.get('type'), {}
    value = {k: (op.get(k) or '').strip() for k in ('priority', 'title', 'text', 'value', 'state', 'owner')}
    if value['priority'] and kind in ('CREATE_TICKET', 'INTERNAL_ALERT', 'QUEUE_FOR_REVIEW', 'CREATE_ISSUE'):
        out['priority'] = value['priority']
    if value['title']:
        out['title' if kind == 'CREATE_TICKET' else 'summary'] = value['title']
    if value['text']:
        out[{'CREATE_TICKET': 'description', 'SCHEDULE_FOLLOWUP': 'reason'}.get(kind, 'text')] = value['text']
    if value['value'] and kind == 'KB_UPDATE':
        out['text'] = value['value']
    if value['state'] and kind in ('UPDATE_ISSUE_STATE', 'CREATE_ISSUE'):
        out['state'] = value['state']
    if value['owner']:
        out['responsible' if kind == 'CREATE_TICKET' else 'owner'] = [value['owner']] if kind == 'CREATE_TICKET' else value['owner']
    return out


def proposal_keyboard(run_id, proposal):
    pid, done = proposal['id'], proposal.get('done') or {}
    rows = []
    if proposal.get('corrected'):
        rows.append([_btn(done['answer'], 'pd', run_id, f"{pid}.answer") if done.get('answer')
                     else _btn('🤖 Send Message' if proposal.get('followup') else '🤖 Send Answer', 'ps', run_id, pid)])
    if proposal.get('lesson'):
        rows.append([_btn(done['rule'], 'pd', run_id, f"{pid}.rule") if done.get('rule') else _btn('📏 Save rule', 'pr', run_id, pid)])
    for index, fact in enumerate(proposal.get('facts') or []):
        key = f"fact{index}"
        if done.get(key):
            rows.append([_btn(done[key], 'pd', run_id, f"{pid}.{key}")])
        else:
            star = 'global' if fact.get('scope') == 'company' else 'apartment'
            rows.append([_btn("🏠📚 Apartment" + (" ⭐" if star == 'apartment' else ""), 'pk', run_id, f"{pid}.{index}"),
                         _btn("🌍📚 Global" + (" ⭐" if star == 'global' else ""), 'pg', run_id, f"{pid}.{index}")])
    if proposal.get('changes'):
        ops = [c.get('op') for c in proposal['changes'] if c.get('do') == 'operation']
        label = OPERATIONS[ops[0]]['button'] if len(ops) == len(proposal['changes']) == 1 else '✅ Apply Change'
        rows.append([_btn(done['apply'], 'pd', run_id, f"{pid}.apply") if done.get('apply') else _btn(label, 'pa', run_id, pid)])
    return {'inline_keyboard': rows} if rows else None


# System operations a typed reply can start (AGENT_NOTE, OPERATIONS): what the bot says, the button, after the press
OPERATIONS = {
    'test_reminder': {
        'line': "🧪 TEST REMINDER on the \"Sandbox Test\" apartment (test apartment: nothing reaches a tenant), due 1 minute "
                "after the press",
        'button': "🧪 Send test reminder",
        'after': "After the press: in about 1 minute the ⏰ REMINDER alert comes to this group.",
    },
}


def run_operation(op, author):
    """One operation from OPERATIONS, after its press. Returns a note."""
    from mysite.management.commands.ai_agent_sandbox import SANDBOX_SID
    from mysite.models import AIFollowUp, AIIssue, TwilioConversation
    if op != 'test_reminder':
        return f"✗ unknown operation {op}"
    conversation = TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID).select_related('apartment', 'booking').first()
    if not (conversation and conversation.apartment_id and conversation.booking_id):
        return "✗ the Sandbox Test chat is not set up (run the sandbox story once)"
    if conversation.apartment.ai_group_chat_enabled:
        return "✗ the Sandbox Test apartment is in live mode - no test reminder (it could reach a tenant)"
    issue = AIIssue.objects.create(conversation_sid=SANDBOX_SID, apartment=conversation.apartment, booking=conversation.booking,
                                   summary=f"{TEST_REMINDER} {author} in Telegram", owner='Edy', mode='test')
    followup = AIFollowUp.objects.create(
        conversation_sid=SANDBOX_SID, issue=issue, kind=AIFollowUp.KIND_STAFF_REMINDER, due_at=timezone.now() + timedelta(minutes=1),
        reason=f"Test reminder asked by {author}: show the reminder alert (always still needed)")
    return f"test reminder {followup.public_id} due {_hm(followup.due_at)}"


# Rules for the interpreter of every typed reply to a simple alert
REPLY_NOTE = ("NOTE: use plan_ops remove only when the manager says in words that an item is not needed (\"no reminder\", "
              "\"remove 2\", \"no task\"). A fact or a new answer in the reply is NOT a request to remove a reminder or a task.")
# A reply about the agent itself ("ClickUp off means ...", "never create a task for ...") is a change request for the
# agent's rules, handled from Telegram like every other reply: the bot shows the rule(s) it will add and where, and the
# press saves them (TEAM RULES of the system prompt). Harmful requests are refused in words; what needs code is said so.
AGENT_NOTE = """AGENT CHANGE REQUESTS: when the reply is (also) about how the AI or the system should work IN GENERAL - what it does
when ClickUp is off, when to create tasks or reminders, what it may read, how to word alerts, whom to notify - and not
only about this alert, fill agent_changes: one rule per point, written as a self-contained instruction to the AI in the
second person (1-3 sentences, with every concrete fact the manager gave), key = snake_case topic, scope company unless
the manager speaks about this unit only. Set already = true when HOW THE SYSTEM WORKS says it is already so (then the rule
only makes it explicit). Do NOT send the manager to a developer and do not say it is impossible: the rules ARE applied from
this reply after the press. Only what a rule can not do goes to cannot: a new button or block, new data in the alert, a
new integration or page, a change of the site - name the exact part, one sentence, else ''. refused (else ''): a request
that is harmful - give tenants' or the team's personal data to outsiders, lie to or threaten tenants, switch off
emergency handling or the on-call call, show or guess hidden access codes, send to tenants without any approval, break a
law or a contract, treat people unequally - say in one sentence why it is not applied; then agent_changes is [].
Such a reply has decision changes_only (or question when it only asks) and lesson ''. staff_answer: when the manager
also asks or states something that needs an answer, answer it; when a point is already so, say so in one sentence.
Never write that a rule is saved, applied or written down - that happens only after the press, the bot says so itself.
When refused is filled, do not repeat the reason in staff_answer (answer only a question, if there is one).
HOW THE SYSTEM WORKS (facts for already / cannot):
- The team sees the AI's proposal as an alert with one button per item; nothing reaches the tenant, ClickUp or the
  knowledge base without a press. Reminders are created automatically (max 2 per case). Emergencies and the after-hours
  message are the only automatic messages; live tenant reminders are sent automatically, test apartments never.
- ClickUp writes OFF (a setting on the site, always for test apartments): the AI never creates a task by itself; a press
  on 🎫 Create Task or 🤖🎫 Send + Create Task ALWAYS creates the task, also with ClickUp writes off. The AI always
  reads the chat's ClickUp tasks and their comments (EXISTING TASKS).
- Test / live and ClickUp on / off are switched on the site, never from Telegram.
- The AI's behaviour = its prompts in AI Management plus the ANSWER LESSONS and TEAM RULES the team adds from Telegram.
- A reminder that becomes due is re-checked by the AI: already done -> closed quietly, no alert; still needed -> a
  ⏰ REMINDER alert (✅ Close Reminder; the last one also ⏰ Remind again in 24 hours); a live tenant reminder is sent by
  itself and shown as 🤖 AI MESSAGE. Alerts posted before 7 Oct 2026 16:26 ET have the old card format.
- The worker always runs the code that is deployed: a restart does not change any alert or behaviour. Code changes are
  made and deployed outside Telegram.
OPERATIONS a reply can start (button under the bot's answer, done only after the press) - fill operations ONLY when the
manager asks for one:
- test_reminder: a test reminder on the "Sandbox Test" apartment (a test apartment: nothing reaches a tenant), due 1
  minute after the press, so the team sees a ⏰ REMINDER alert in this group.
A restart, a deploy or running the sandbox story is not an operation: say so in one sentence (cannot).
HONESTY: answer questions only from the alert, the run and the facts above. When they do not show the cause, say "I do
not know" and what you can see - never list guessed causes. Never send the manager to Andrei, Engineering, a developer
or anybody else, in any field.
"""
WRITTEN_NOTE = ("NOTE: the manager pressed Edit Answer and wrote this text as the exact message for the tenant: "
                "decision = replace and corrected_answer = the text VERBATIM. When the text tells the tenant who comes or "
                "when (a visit, a time) and the task of this alert is in EXISTING TASKS, also add ONE task_actions comment "
                "with that fact (\"Edy visits today 17:00\").")


def handle_reply(run, text, author, at, reply_to=None, written=False):
    """A typed reply to a simple alert: answers a question, or explains the change and waits for the press.
    written: the text is the answer itself, typed after ✏️ Edit Answer."""
    from mysite.models import AIRun
    ar, approval = _review()
    run.refresh_from_db()
    reply_to = reply_to or run.telegram_message_id
    dry = bool(ar.TEST_REPLY.match(text)) and not written
    body = ar.TEST_REPLY.sub('', text, count=1).strip() if dry else text.strip()
    approval._append(run, 'typed_replies', {'by': author, 'at': at.isoformat(), 'text': text[:2000]})
    sent = _sent_answer(run)
    decision = ar.interpret(run, body, author, run.hold_status == AIRun.HOLD_HOLDING,
                            sent_note=SENT_NOTE.format(at=sent['at'], text=sent['text']) if sent else None,
                            extra_note=REPLY_NOTE + "\n" + AGENT_NOTE + ("\n" + WRITTEN_NOTE if written else "")) \
        if body else dict(ar.EMPTY_DECISION, decision='unclear')
    kind = decision.get('decision') or 'unclear'
    corrected = (decision.get('corrected_answer') or '').strip() if kind == 'replace' else ''
    if written and body:
        kind, corrected = 'replace', body   # their words go out exactly as typed
        if (run.review or {}).get('edit_by'):
            approval._update_review(run, edit_by=None)
            refresh_alert(run)
    lesson = (decision.get('lesson') or '').strip()
    question = (decision.get('staff_answer') or '').strip()
    facts = [f for f in decision.get('new_facts') or [] if isinstance(f, dict) and str(f.get('value') or '').strip()]
    task_actions = [a for a in decision.get('task_actions') or [] if isinstance(a, dict)]
    changes = _plan_changes(run, [o for o in decision.get('plan_ops') or [] if isinstance(o, dict)])
    waiting = run.hold_status == AIRun.HOLD_HOLDING
    tasks_open = [i for k, i, a in blocks(ar._plan(run).get('actions')) if k == 'task' and not a.get('done')]
    if kind == 'send_as_is' and (waiting or tasks_open):
        changes.append(("🤖 Send the answer as it is written" if waiting else "", {'do': 'send'}))
        changes += [(f"🎫 Create the task: {ar._plan(run)['actions'][i].get('title')}", {'do': 'create_task', 'idx': i}) for i in tasks_open]
        changes = [c for c in changes if c[0]]
    elif kind in ('do_not_send', 'stop_all') and waiting:
        changes.append(("🤖 Do NOT send the answer (the tenant gets nothing from the AI for this message)", {'do': 'dont_send'}))
    if task_actions:
        for line in ar.apply_task_actions(run, task_actions, author, True):
            if not line.startswith('✗'):
                changes.append((("🔄 " + line[len('Would '):] if line.startswith('Would ') else line), None))
        changes.append(("", {'do': 'task_actions', 'actions': task_actions}))
    refused = str(decision.get('refused') or '').strip()
    cannot = str(decision.get('cannot') or '').strip()
    rules = [] if refused else [r for r in decision.get('agent_changes') or [] if isinstance(r, dict) and str(r.get('rule') or '').strip()]
    for op in dict.fromkeys(o for o in decision.get('operations') or [] if o in OPERATIONS):
        changes.append((OPERATIONS[op]['line'], {'do': 'operation', 'op': op}))
    for rule in rules:
        where = "this apartment only" if rule.get('scope') == 'apartment' else "all apartments"
        text = f"📏 AGENT RULE: \"{' '.join(str(rule['rule']).split())}\"\n   where: AI Management → Agent - team rules · {where}"
        if rule.get('already'):
            text += "\n   (already so today - the rule only makes it explicit)"
        changes.append((text, {'do': 'team_rule', 'key': str(rule.get('key') or 'team_rule'), 'rule': str(rule['rule']).strip(),
                               'scope': 'apartment' if rule.get('scope') == 'apartment' else 'company'}))

    lines = [f"🤖 {question}"] if question else []
    if refused:
        lines.append(f"⛔ NOT APPLIED: {refused}\nNothing was changed.")
    if cannot:
        lines.append(f"🛠 Needs a change in the code, not possible from a reply: {cannot}")
    followup = bool(corrected and sent and not waiting)   # the answer already went out: this is one more message
    if corrected:
        tenant = str((run.review or {}).get('meta', {}).get('tenant') or 'the tenant').split()[0]
        lines += [f"✉️ FOLLOW-UP MESSAGE for {tenant} (the answer was already sent {sent['at']})" if followup
                  else f"✏️ NEW ANSWER for {tenant}", f"🤖 \"{corrected}\" 🤖"]
    if lesson:
        lines.append(f"📏 Rule I learned: \"{lesson}\"")
    for fact in facts:
        key, value = str(fact.get('key') or '').replace('_', ' ').strip(), str(fact['value']).strip()
        named = key and not value.lower().startswith(key.lower())   # "WiFi password: Sun2026" already names the topic
        fact['text'] = f"{key[:1].upper()}{key[1:]}: {value}" if named else value
        lines += [f"📚 \"{fact['text']}\" 📚", f"   from: {author}'s reply {_short_stamp(at)}"]
    shown = [c[0] for c in changes if c[0]]
    if shown:
        kinds = {(c[1] or {}).get('do') for c in changes if c[1]}
        lines += (["✏️ I WILL CHANGE"] if not dry else []) + shown + [
            "Why: your answer says it." if written else "Why: you asked for it.",
            "After the press: the AI follows the rule from its next run on, in every alert. This alert does not change."
            if kinds == {'team_rule'} else
            "After the press: the alert shows the new values. A task is still created only with 🎫 Create Task."
            if kinds <= {'change', 'remove'} else
            OPERATIONS[changes[-1][1]['op']]['after'] if kinds == {'operation'} else
            "After the press: it is done for real."]
    if not lines:
        lines = ["🤔 I did not find anything to change in your reply, so nothing was changed.",
                 "Write it as an order (\"task for Kevin\", \"no task needed\", \"tell her the plumber comes at 10\") or ask a question."]
    elif not (corrected or lesson or facts or shown):
        pass   # only a question: an answer, nothing else
    # Every bot answer ends with the links of the alert it belongs to (part 7 of the document)
    from mysite.ai_agent import team_notify
    lines += ["", f"🔗 AI run: {team_notify.report_url(run.id)}", f"💬 CRM chat: {config.site_url()}/chat/{run.conversation_sid}/"]
    if dry:
        report = "🧪 DRY RUN – nothing will happen. I would:\n" + "\n".join(lines)
        ar._say(run, report, reply_to=reply_to)
        return report
    proposal = {'id': len((run.review or {}).get('proposals') or []) + 1, 'by': author, 'at': at.isoformat(), 'text': body[:2000],
                'corrected': corrected, 'followup': followup, 'lesson': lesson, 'lesson_key': decision.get('lesson_key') or 'answer_lesson',
                'lesson_scope': decision.get('lesson_scope') or 'company', 'facts': facts,
                'changes': [c[1] for c in changes if c[1]], 'done': {}}
    markup = proposal_keyboard(run.id, proposal)
    report = "\n".join(lines)
    ok, note, message_id = send_ai_chat(report, reply_to=reply_to, reply_markup=markup)
    if message_id:
        ar.remember_bot_message(run.id, message_id)
    if markup:
        proposal.update(message_id=message_id, report=report)
        approval._append(run, 'proposals', proposal)
    return report


# For the interpreter of a typed reply, when the alert's answer is no longer a draft
SENT_NOTE = """NOTE: the answer of this alert was ALREADY SENT to the tenant at {at}: "{text}"
It can not be changed any more. When the manager's reply means the tenant must now be told something new or different
(a corrected password, another time, "tell him ..."), use decision replace and write corrected_answer as a short
FOLLOW-UP message that the tenant reads right after the sent answer: no new greeting, do NOT repeat what the sent answer
already said and what is still right, only what is new or corrected - with a short apology when the sent answer gave
wrong information. When the tenant needs no new message, do not use replace."""


def _sent_answer(run):
    """{'at': 'HH:MM', 'text'} when this alert's answer already went to the tenant, else None."""
    from mysite.models import AIRun
    done = (run.review or {}).get('answer_done') or {}
    if run.hold_status not in (AIRun.HOLD_SENT, AIRun.HOLD_CORRECTED) or not (run.final_answer or run.answer):
        return None
    return {'at': _hm_of(done) or _hm(run.updated_at), 'text': (run.final_answer or run.answer).strip()}


def _send_followup(run, proposal, author):
    """One more message to the tenant after the alert's answer went out. Returns the popup text."""
    from mysite.ai_agent import service
    ar, approval = _review()
    review = run.review or {}
    if review.get('stale'):
        return "⚠️ Outdated – the tenant wrote again. See the newer alert."
    result = service.send_answer(review.get('send_to') or run.conversation_sid, proposal['corrected'],
                                 review.get('reply_author'), review.get('sender_phone'))
    if result.get('error'):
        return f"❌ Not sent – {result['note']}"
    held = str(result.get('note') or '').startswith('held')
    proposal['done']['answer'] = (f"⏳ Message will be sent {_hm(config.next_notification_window_start())} · {author}" if held
                                  else f"✅ Message sent · {author} {_hm()}")
    _save_proposal(run, proposal)
    approval._record(run, author, 'send follow-up message', proposal['corrected'][:300])
    return "Will be sent when SMS hours start" if held else "Sent"


def _proposal(run, pid):
    return next((p for p in (run.review or {}).get('proposals') or [] if p.get('id') == pid), None)


def _save_proposal(run, proposal):
    _, approval = _review()
    run.refresh_from_db()
    proposals = [proposal if p.get('id') == proposal['id'] else p for p in (run.review or {}).get('proposals') or []]
    approval._update_review(run, proposals=proposals)
    if proposal.get('message_id'):
        edit_reply_markup(proposal['message_id'], proposal_keyboard(run.id, proposal))


def press_proposal(run, code, arg, author):
    """A press on a proposal the bot made after a typed reply. Returns the popup text."""
    from mysite.ai_agent import kb_documents, prompt_library
    from mysite.models import AIFollowUp, AIRun
    ar, approval = _review()
    pid, _, sub = str(arg).partition('.')
    proposal = _proposal(run, int(pid or 0))
    if not proposal:
        return "This proposal no longer exists"
    done = proposal.setdefault('done', {})
    stamp = f"{author} {_hm()}"
    if code == 'pd':
        return f"already done: {done.get(sub) or ''}".strip()
    if code == 'ps':   # the corrected answer
        if done.get('answer'):
            return f"already done: {done['answer']}"
        if proposal.get('followup'):
            return _send_followup(run, proposal, author)
        if run.hold_status != AIRun.HOLD_HOLDING:
            return "Too late: the answer of this alert was already handled."
        old = run.answer
        result = ar._claim_and_release(run, proposal['corrected'], AIRun.HOLD_CORRECTED,
                                       f"written by {proposal['by']}, sent by {author}", announce=False)
        if not result or result[0] == AIRun.HOLD_HOLDING:
            return f"⛔ Not sent – {result[1] if result else 'already handled'}"
        done['answer'] = f"✅ Answer sent · {stamp}"
        approval._update_review(run, answer_done={'by': author, 'at': timezone.now().isoformat(), 'label': done['answer']},
                                corrected={'old': old, 'new': proposal['corrected'], 'by': proposal['by']})   # for learning (1.5.1)
        _save_proposal(run, proposal)
        approval._record(run, author, 'send corrected answer', proposal['corrected'][:300])
        refresh_alert(run)
        return "Sent"
    if code == 'pr':
        if done.get('rule'):
            return f"already done: {done['rule']}"
        apartment = ar._apartment(run) if proposal.get('lesson_scope') == 'apartment' else None
        prompt_library.upsert_lesson(apartment, proposal.get('lesson_key') or 'answer_lesson', proposal['lesson'])
        done['rule'] = f"✅ Rule saved · {author}"
        _save_proposal(run, proposal)
        approval._record(run, author, 'save rule', proposal['lesson'][:300])
        return "Rule saved"
    if code in ('pk', 'pg'):
        key = f"fact{sub}"
        if done.get(key):
            return f"already done: {done[key]}"
        fact = proposal['facts'][int(sub or 0)]
        scope = 'company' if code == 'pg' else 'apartment'
        kb_documents.merge(scope, ar._apartment(run) if scope == 'apartment' else None, fact['text'], source=f"{proposal['by']} (Telegram)")
        done[key] = f"✅ Saved to {'global' if scope == 'company' else 'apartment'} · {author}"
        _save_proposal(run, proposal)
        approval._record(run, author, f"save knowledge ({scope})", fact['text'][:300])
        return "Saved"
    if code == 'pa':
        if done.get('apply'):
            return f"already done: {done['apply']}"
        if (run.review or {}).get('stale'):
            return "⚠️ Outdated – the tenant wrote again. See the newer alert."
        notes = []
        for change in proposal.get('changes') or []:
            run.refresh_from_db()
            plan = ar._plan(run)
            what = change.get('do')
            if what in ('change', 'remove'):
                action = plan['actions'][change['idx']]
                if what == 'change':
                    action.update(change['fields'])
                    action['changed_by'] = author
                else:
                    action['removed_by'] = author
                ar._save_plan(run, plan)
            elif what == 'close_reminder':
                close_reminder(run, change['idx'], author)
            elif what == 'create_task':
                notes.append(create_task(run, change['idx'], author))
            elif what == 'send':
                notes.append(send_answer(run, author)[0])
            elif what == 'dont_send':
                approval.dont_send(run, author)
            elif what == 'task_actions':
                with clickup.pressed():
                    notes += ar.apply_task_actions(run, change['actions'], author, False)
            elif what == 'operation':
                notes.append(run_operation(change['op'], author))
            elif what == 'team_rule':
                apartment = ar._apartment(run) if change.get('scope') == 'apartment' else None
                detail = prompt_library.upsert_team_rule(apartment, change.get('key') or 'team_rule', change['rule'])
                notes.append(f"team rule {detail}: {change['rule'][:200]}")
        done['apply'] = f"✅ Changed · {stamp}"
        _save_proposal(run, proposal)
        approval._record(run, author, 'apply change', " / ".join(str(n) for n in notes)[:500])
        refresh_alert(run)
        failed = [n for n in notes if str(n).startswith(('✗', '⛔', 'ClickUp error'))]
        if failed:
            ar._say(run, "⚠️ " + "\n".join(str(n) for n in failed), reply_to=proposal.get('message_id'))
        return "Changed"
    return "Unknown button"
