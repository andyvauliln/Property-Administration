"""
The check of one test step (simple_telegram_alerts.md 11.4): what the agent really did (alerts as posted, buttons,
its run row, reminders, sends) against the case's structured expectations - exact, by code - and against the example
in the document - by meaning, by a Claude judge. Also the "test assistant" that writes the 🧪 TEST NOTES.

The checks read the agent's result from the outside (Telegram calls, database rows), so they do not depend on how
the alert code is written: the same checks run against the old card and the new alerts.
"""
import json
import os
import re
from datetime import timedelta

import yaml

from mysite.ai_agent.sandbox_test import catalog
from mysite.ai_agent.sandbox_test.world import SANDBOX_SID, SANDBOX_SIDS, button_label, buttons_of

EVENT_ALERT = {'TENANT_MESSAGE': 'TENANT MESSAGE', 'STAFF_MESSAGE': 'TEAM MESSAGE', 'FOLLOWUP_DUE': 'REMINDER'}
REMINDER_KIND = {'staff_reminder': 'staff', 'escalation_check': 'staff', 'tenant_nudge': 'tenant',
                 'second_tenant_nudge': 'tenant', 'deadline_reminder': 'deadline'}
URGENCY = re.compile(r'(?:🟢|🔴|🚨)\s*(routine|urgent|emergency)', re.I)
PART = re.compile(r'^\((\d+)/(\d+)\)')
TOLERANCE = timedelta(minutes=10)
ICON = {True: '✅', False: '❌', None: '⚪'}
URGENCY_LABEL = {'routine': '🟢 Routine', 'urgent': '🔴 Urgent', 'emergency': '🚨 Emergency'}


# ---------------------------------------------------------------------------
# What happened
# ---------------------------------------------------------------------------

def header_type(text):
    """The alert type written in the alert's header (new layout), or None."""
    head = "\n".join((text or '').strip().splitlines()[:4])
    for name in ('TENANT MESSAGE', 'TEAM MESSAGE', 'AI MESSAGE'):
        if name in head:
            return name
    if head.lstrip().startswith('📋'):
        return 'REPORT'
    return 'REMINDER' if re.search(r'⏰\s*REMINDER', head) else None


def snapshot():
    """Taken before a step, so collect() knows what the step added."""
    from mysite.models import AIAlertCall, AIFollowUp, AIRun
    followups = AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID)
    return {
        'run': AIRun.objects.filter(conversation_sid=SANDBOX_SID).order_by('-id').values_list('id', flat=True).first() or 0,
        'followup': followups.order_by('-id').values_list('id', flat=True).first() or 0,
        'pending': set(followups.filter(status=AIFollowUp.STATUS_PENDING).values_list('id', flat=True)),
        'call': AIAlertCall.objects.filter(conversation_sid=SANDBOX_SID).order_by('-id').values_list('id', flat=True).first() or 0,
    }


def collect(world, step, before, now, note=None, earlier=None, answers=False):
    """Everything one step produced, in one dict (see the keys at the end). answers: the step was a typed reply or
    a button press, so what the bot said back is its "REPLY" alert."""
    from mysite.models import AIAlertCall, AIFollowUp, AIRun

    runs = list(AIRun.objects.filter(conversation_sid=SANDBOX_SID, id__gt=before['run']).order_by('id'))
    by_message = {run.telegram_message_id: run for run in runs if run.telegram_message_id}
    alerts, others = [], []
    for message in world.tap.in_step(step):
        run = by_message.get(message['id'])
        part = PART.match(message['text'].strip())
        if part and int(part.group(1)) > 1 and alerts:   # (2/2) of a long alert: same alert, the buttons are here
            alerts[-1]['parts'].append(message)
            alerts[-1].update(text=alerts[-1]['text'] + "\n" + message['text'], markup=message['markup'],
                              run=alerts[-1]['run'] or run)
            continue
        kind = header_type(message['text'])
        if kind or run:
            alerts.append(dict(message, type=kind or EVENT_ALERT.get(run.event_type, run.event_type), in_header=bool(kind),
                               run=run, parts=[message]))
        else:
            others.append(message)
    if answers and others and not alerts:
        said = next((m for m in reversed(others) if buttons_of(m['markup'])), others[-1])
        alerts.append(dict(said, type='REPLY', in_header=True, run=None, parts=[said]))
    followups = AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID)
    return {
        'alerts': alerts, 'others': others, 'runs': runs, 'now': now, 'note': note, 'earlier': earlier, 'before': before,
        'popups': [p['text'] for p in world.tap.popups if p['step'] == step],
        'sent': [s for s in world.sent if s['step'] == step], 'held': [h for h in world.held if h['step'] == step],
        'calls': list(AIAlertCall.objects.filter(conversation_sid=SANDBOX_SID, id__gt=before['call'])
                      .exclude(status=AIAlertCall.STATUS_SKIPPED)),
        'new_followups': list(followups.filter(id__gt=before['followup']).select_related('issue')),
        'closed_followups': followups.filter(id__in=before['pending'], status=AIFollowUp.STATUS_CANCELLED).count(),
        'knowledge': [k for k in world.state['knowledge'] if k.get('step') == step],
        'rules': [r for r in world.state['rules'] if r.get('step') == step],
        'clickup': [c for c in world.clickup_log if c['step'] == step],
    }


def main_alert(got, wanted=None):
    """The alert the expectations are about: the last one of the wanted type, else the last one."""
    matching = [a for a in got['alerts'] if a['type'] == wanted]
    return (matching or got['alerts'] or [None])[-1]


def _actions(got, *types):
    """(action, stored item) of the step's runs, rejected ones left out."""
    found = []
    for run in got['runs']:
        for item in run.actions or []:
            action = item.get('action') if isinstance(item, dict) else None
            if isinstance(action, dict) and item.get('status') != 'rejected' and action.get('type') in types:
                found.append((action, item))
    return found


def _owner_of(got, issue_id):
    from mysite.models import AIIssue
    for action, _ in _actions(got, 'CREATE_ISSUE'):
        if action.get('temp_id') and action.get('temp_id') == issue_id:
            return action.get('owner')
    match = re.fullmatch(r'[it]-(\d+)', str(issue_id or ''))
    issue = AIIssue.objects.filter(id=int(match.group(1))).first() if match else None
    return issue.owner if issue else None


def new_tasks(got):
    tasks = []
    for action, item in _actions(got, 'CREATE_TICKET'):
        if 'No new ClickUp task' in str(item.get('detail')):
            continue
        responsible = action.get('responsible') or _owner_of(got, action.get('issue_id')) or []
        tasks.append({'title': action.get('title'), 'priority': action.get('priority') or 'routine',
                      'who': [responsible] if isinstance(responsible, str) else list(responsible)})
    return tasks


def new_reminders(got):
    from mysite.ai_agent import policy
    reminders = [{'kind': REMINDER_KIND.get(f.kind, f.kind), 'due': f.due_at, 'who': f.issue.owner if f.issue else None,
                  'text': f.reason} for f in got['new_followups']]
    for action, item in _actions(got, 'SCHEDULE_FOLLOWUP'):
        if item.get('status') != 'planned':   # executed ones are rows already
            continue
        priority = 'routine'
        for issue_action, _ in _actions(got, 'CREATE_ISSUE'):
            if issue_action.get('temp_id') == action.get('issue_id'):
                priority = issue_action.get('priority') or 'routine'
        due, _ = policy.due_at(action.get('kind') or 'staff_reminder', priority, now=got['now'])
        reminders.append({'kind': REMINDER_KIND.get(action.get('kind'), action.get('kind')), 'due': due,
                          'who': _owner_of(got, action.get('issue_id')), 'text': action.get('reason'), 'planned': True})
    return reminders


def _when(moment):
    from mysite.ai_agent import policy
    return f"{moment.astimezone(policy._tz()):%a %d %b %H:%M}"


def _span(delta):
    minutes = round(delta.total_seconds() / 60)
    return f"{minutes} min" if abs(minutes) < 120 else f"{round(minutes / 60)} h"


def _reminder_text(reminder, now):
    """'a reminder for the team (Edy) in 2 h (Tue 06 Oct 16:34)' - plain words, this goes to the test group."""
    whom = {'staff': 'for the team', 'tenant': 'to the tenant', 'deadline': 'before the tenant deadline'}.get(reminder['kind'], reminder['kind'])
    return (f"a reminder {whom}" + (f" ({reminder['who']})" if reminder.get('who') and reminder['kind'] != 'tenant' else "")
            + f" in {_span(reminder['due'] - now)} ({_when(reminder['due'])})"
            + (" - only planned, not created" if reminder.get('planned') else ""))


# ---------------------------------------------------------------------------
# Structured checks: one function per `expect` key; each adds (ok, text) lines
# ---------------------------------------------------------------------------

def _diff(what, agreed, now):
    """A failed check in plain words, the same form everywhere (it is posted in the test group)."""
    return f"{what}\n   We agreed: {agreed}\n   The AI now: {now}"


def _plain(labels):
    return ", ".join(re.sub(r'^[^\w]+', '', str(label)).strip() for label in labels) or 'none'


def _same_person(wanted, name):
    """Edy == edy; Farid == Kevin (config.STAFF_ALSO: the same person / the same authority)."""
    from mysite.ai_agent import config
    a, b = str(wanted or '').strip().lower(), str(name or '').strip().lower()
    also = {k.lower(): [v.lower() for v in vs] for k, vs in config.STAFF_ALSO.items()}
    return a in b or b in also.get(a, []) or a in also.get(b, [])


def _texts(got):
    """Everything the bot posted in the step: the alert(s) and what it said next to them."""
    seen = {id(part) for a in got['alerts'] for part in a['parts']}
    return "\n".join([a['text'] for a in got['alerts']] + [m['text'] for m in got['others'] if id(m) not in seen])


def _has(fragment, text):
    return str(fragment).lower() in (text or '').lower()


def _check_alert(want, got, add, ctx):
    types = [a['type'] for a in got['alerts']]
    if str(want).lower() == 'none':
        return add(not types, "no alert" if not types else _diff("An alert was posted, but there is nothing for the manager to do.", "no alert", f"{', '.join(types)} alert"))
    alert = ctx['alert']
    if not alert or alert['type'] != want:
        return add(False, _diff("The kind of alert is different.", f"{want} alert", ', '.join(types) or 'no alert was posted'))
    add(alert['in_header'], f"alert type {want}" if alert['in_header'] else
        f"alert: it is a {want} run, but the alert has no \"{want}\" header (old card layout)")


def _check_also(want, got, add, ctx):
    types = [a['type'] for a in got['alerts']]
    for kind in want or []:
        add(kind in types, f"also posts {kind}" if kind in types else _diff(f"The {kind} alert is missing.", f"also a {kind} alert", ', '.join(types) or 'no alert'))


def _check_urgency(want, got, add, ctx):
    alert = ctx['alert']
    shown = URGENCY.search(alert['text']) if alert else None
    triage = (alert['run'].triage or {}).get('priority') if alert and alert['run'] else None
    if not shown:
        return add(False, _diff("The alert does not show the urgency.", URGENCY_LABEL.get(want, want),
                                f"no 🟢 / 🔴 / 🚨 on the alert (the AI rated it {triage or 'nothing'})"))
    have = shown.group(1).lower()
    add(have == want, f"urgency {want}" if have == want else _diff("The urgency is different.", URGENCY_LABEL.get(want, want), URGENCY_LABEL.get(have, have)))


def _check_sound(want, got, add, ctx):
    alert = ctx['alert']
    if not alert:
        return add(False, "sound: no alert to check")
    with_sound = not alert['parts'][-1]['silent']
    add(with_sound == bool(want), ("with sound" if want else "no sound") if with_sound == bool(want)
        else _diff("The sound is different.", f"posted {'with' if want else 'without'} sound", f"posted {'with' if with_sound else 'without'} sound"))


def _check_parts(want, got, add, ctx):
    have = len(ctx['alert']['parts']) if ctx['alert'] else 0
    add(have == want, f"alert in {want} part(s)" if have == want else _diff("A long alert is split differently.", f"{want} messages", f"{have} message(s)"))


def _check_contains(want, got, add, ctx):
    missing = [f for f in want or [] if not _has(f, _texts(got))]
    add(not missing, "alert text has: " + ", ".join(map(str, want)) if not missing else "The alert does not show: " + ", ".join(f'"{m}"' for m in missing))


def _check_not_contains(want, got, add, ctx):
    found = [f for f in want or [] if _has(f, _texts(got))]
    add(not found, "alert text is free of: " + ", ".join(map(str, want)) if not found else "The alert shows what it should not: " + ", ".join(f'"{m}"' for m in found))


def _check_conversation(want, got, add, ctx):
    text = ctx['alert']['text'] if ctx['alert'] else ''
    before = re.search(r'↪️\s*(.+?)\s*"', text)
    if 'before' in want:
        if want['before'] is None:
            add(not before, "no ↪️ line" if not before else _diff("The ↪️ line should not be there.", "no ↪️ line (the other side never wrote)", f"↪️ {before.group(1)}"))
        else:
            ok = bool(before) and before.group(1).lower().startswith(str(want['before']).lower())
            add(ok, f"↪️ {want['before']}" if ok else _diff("The ↪️ line (what the other side wrote before) is different.",
                                                            f"↪️ {want['before']} \"…\"", f"↪️ {before.group(1)}" if before else "no ↪️ line"))
    if want.get('now_contains'):
        now = re.search(r'💬[^"\n]*"(.+?)"\s*(?:\n|$)', text, re.S)
        ok = bool(now) and _has(want['now_contains'], now.group(1))
        add(ok, "💬 message block" if ok else _diff("The 💬 line (the new message) is different.", f"💬 \"…{want['now_contains']}…\"",
                                                    f"💬 \"{now.group(1)[:80]}\"" if now else "no 💬 line"))


def _answer(got, ctx):
    run = ctx['alert']['run'] if ctx['alert'] else (got['runs'][-1] if got['runs'] else None)
    block = re.search(r'🤖\s*"(.+?)"\s*🤖', _texts(got), re.S)
    return (run.answer if run and run.answer else '') or (block.group(1) if block else '')


def _check_answer(want, got, add, ctx):
    have = bool(_answer(got, ctx))
    add(have == bool(want), ("answer for the tenant" if want else "no answer for the tenant") if have == bool(want)
        else (_diff("There is no answer for the tenant.", "an answer to send", "no answer") if want else
              _diff("There should be no answer for the tenant.", "no answer", f"\"{_answer(got, ctx)[:160]}\"")))


def _check_answer_contains(want, got, add, ctx):
    missing = [f for f in want or [] if not _has(f, _answer(got, ctx))]
    add(not missing, "answer says: " + ", ".join(map(str, want)) if not missing
        else _diff("Something is missing in the answer.", "the answer mentions " + ", ".join(f'"{m}"' for m in missing), f"\"{_answer(got, ctx)[:200]}\""))


def _check_answer_not_contains(want, got, add, ctx):
    found = [f for f in want or [] if _has(f, _answer(got, ctx))]
    add(not found, "answer avoids: " + ", ".join(map(str, want)) if not found else _diff("The answer says something it should not.", "no " + ", ".join(f'"{m}"' for m in want), f"\"{_answer(got, ctx)[:200]}\""))


def _check_tasks(want, got, add, ctx):
    tasks = new_tasks(got)
    titles = ", ".join(f"\"{t['title']}\"" for t in tasks)
    add(len(tasks) == want, f"{want} new task(s)" + (f": {titles}" if tasks else "") if len(tasks) == want
        else _diff("The number of new tasks is different.", f"{want} new task{'s' if want != 1 else ''}" if want else "no new task",
                   (f"{len(tasks)} new task{'s' if len(tasks) != 1 else ''}: {titles}" if tasks else "no new task")))


def _check_task_details(want, got, add, ctx):
    from mysite.ai_agent import clickup
    tasks = new_tasks(got)
    for index, expected in enumerate(want or []):
        if index >= len(tasks):
            add(False, f"task {index + 1}: missing")
            continue
        task, problems = tasks[index], []
        if expected.get('who') and not any(_same_person(expected['who'], who) for who in task['who']):
            problems.append(f"it is for {', '.join(task['who']) or 'nobody'} (we agreed: {expected['who']})")
        if expected.get('priority') and task['priority'] != expected['priority']:
            problems.append(f"it is {task['priority']} (we agreed: {expected['priority']})")
        if expected.get('due_in'):
            hours = clickup.DUE_IN_HOURS.get(task['priority'], 72)
            if abs(timedelta(hours=hours) - catalog.parse_delta(expected['due_in'])) > TOLERANCE:
                problems.append(f"it is due in {hours} h (we agreed: {expected['due_in']})")
        add(not problems, f"task {index + 1}: " + ("; ".join(problems) if problems else
                                                  ", ".join(f"{k} {v}" for k, v in expected.items())))


def _check_updates(want, got, add, ctx):
    updates = [(a, i) for a, i in _actions(got, 'TICKET_COMMENT', 'UPDATE_TICKET') if not a.get('with_close')] + [
        (a, i) for a, i in _actions(got, 'UPDATE_ISSUE_STATE') if a.get('state') == 'RESOLVED' and 'ClickUp' in str(i.get('detail'))]
    add(len(updates) == want, f"{want} update(s) of an existing task" if len(updates) == want
        else _diff("The number of updates of an existing task is different.", f"{want} update(s)", f"{len(updates)} update(s)"))


def _check_reminders(want, got, add, ctx):
    have, left = new_reminders(got), None
    left = list(have)
    problems = []
    for expected in want or []:
        target = None
        if expected.get('in'):
            target = got['now'] + catalog.parse_delta(expected['in'])
        elif expected.get('at'):
            target = ctx['world'].moved(expected['at'])
        match = next((r for r in left if r['kind'] == (expected.get('kind') or r['kind'])
                      and (target is None or abs(r['due'] - target) <= TOLERANCE)
                      and (not expected.get('who') or not r.get('who') or _same_person(expected['who'], r['who']))
                      and not r.get('planned')), None)
        if match:
            left.remove(match)
        else:
            whom = {'staff': 'for the team', 'tenant': 'to the tenant', 'deadline': 'before the tenant deadline'}[expected.get('kind', 'staff')]
            problems.append(f"a reminder {whom}" + (f" ({expected['who']})" if expected.get('who') else "")
                            + (f" in {_span(catalog.parse_delta(expected['in']))}" if expected.get('in') else f" at {_when(target)}" if target else ''))
        if expected.get('n') and not _has(expected['n'], _texts(got)):
            add(False, f"The reminder on the alert does not show its number ({expected['n']}).")
    ok = not problems and not left
    got_text = "; ".join(_reminder_text(r, got['now']) for r in have) or 'no reminder'
    add(ok, f"{len(want or [])} reminder(s): {got_text}" if ok else
        "The reminder is different." + (f"\n   We agreed: {'; '.join(problems)}" if problems else "\n   We agreed: no other reminder")
        + f"\n   The AI now: {got_text}")


def _check_reminders_closed(want, got, add, ctx):
    have = max(len(_actions(got, 'CANCEL_FOLLOWUP')), got['closed_followups'])
    add(have == want, f"{want} reminder(s) closed" if have == want else _diff("The number of closed reminders is different.", f"{want} closed", f"{have} closed"))


def _check_knowledge(want, got, add, ctx):
    have = [{'scope': 'global' if a.get('scope') == 'company' else 'apartment', 'text': str(a.get('text') or a.get('value') or '')}
            for a, _ in _actions(got, 'KB_UPDATE')] + [{'scope': k['scope'], 'text': k['text']} for k in got['knowledge']]
    missing = [e for e in want or [] if not any((not e.get('scope') or e['scope'] == h['scope'])
                                                and _has(e.get('contains') or '', h['text']) for h in have)]
    ok = not missing and (bool(want) or not have)
    got_text = "; ".join(f"{h['scope']}: {h['text'][:80]}" for h in have) or 'none'
    add(ok, (f"knowledge: {got_text}" if want else "no knowledge block") if ok else
        _diff("The knowledge to save is different.", "; ".join(f"{e.get('scope', 'any')}: …{e.get('contains', '')}…" for e in want or []) or 'no knowledge block', got_text))


def _check_rules(want, got, add, ctx):
    """Rules saved for the AI by a press (📏 Save rule, or ✅ Apply Change on an agent change request)."""
    have = [f"{r.get('kind', 'lesson')}: {r['rule']}" for r in got['rules']]
    missing = [e for e in want or [] if not any(_has(e.get('contains') or '', h) and (not e.get('kind') or h.startswith(e['kind'] + ':'))
                                                for h in have)]
    ok = not missing and (bool(want) or not have)
    got_text = "; ".join(h[:120] for h in have) or 'none'
    add(ok, (f"rules saved: {got_text}" if want else "no rule saved") if ok else
        _diff("The rules saved for the AI are different.", "; ".join(f"{e.get('kind', 'any')}: …{e.get('contains', '')}…" for e in want or []) or 'no rule', got_text))


def _check_crm(want, got, add, ctx):
    have = [{'change': a.get('change'), 'spot': str(a.get('spot') or '').lstrip('#')} for a, _ in _actions(got, 'CRM_CHANGE')
            if a.get('label')]
    missing = [w for w in want or [] if not any(w.get('change') == h['change'] and (not w.get('spot') or str(w['spot']) == h['spot'])
                                                for h in have)]
    ok = not missing and (bool(want) or not have)
    say = lambda items: "; ".join(f"{i.get('change')}" + (f" #{i['spot']}" if i.get('spot') else '') for i in items) or 'no CRM change'
    add(ok, f"CRM change proposed: {say(have)}" if want and ok else "no CRM change proposed" if ok else
        _diff("The proposed CRM change is different.", say(want or []), say(have)))


def _check_crm_done(want, got, add, ctx):
    """The real CRM rows of the story booking after the press: [{change: book_parking, spot: "14"}] = that spot is
    booked for the booking now; cancel_parking = it is not."""
    from mysite.models import ParkingBooking
    booking = ctx['world'].booking
    booked = [str(p.parking.number) for p in ParkingBooking.objects.filter(booking=booking).select_related('parking') if p.parking]
    problems = []
    for w in want or []:
        spot = str(w.get('spot') or '').lstrip('#')
        if w.get('change') == 'book_parking' and (spot not in booked if spot else not booked):
            problems.append(f"spot #{spot or '?'} is not booked for the booking")
        if w.get('change') == 'cancel_parking' and (spot in booked if spot else booked):
            problems.append(f"spot #{spot or '?'} is still booked for the booking")
    add(not problems, f"CRM rows: parking booked for the booking: {', '.join('#' + b for b in booked) or 'none'}" if not problems else
        _diff("The CRM rows after the press are different.", "; ".join(f"{w.get('change')} #{w.get('spot', '')}" for w in want or []),
              "; ".join(problems)))


def _check_legal(want, got, add, ctx):
    have = any((run.review or {}).get('needs_confirmation') for run in got['runs']) or '⚖️' in _texts(got)
    add(have == bool(want), ("legal: contract basis shown" if want else "not a legal question") if have == bool(want)
        else _diff("The ⚖️ contract line is different.", "a ⚖️ contract line" if want else "no ⚖️ contract line", "it is there" if have else "it is missing"))


def _check_buttons(want, got, add, ctx):
    have = [b.get('text') or '' for b in buttons_of(ctx['alert']['markup'])] if ctx['alert'] else []
    # "Save rule?" = may be there or not (it depends on what the AI finds in the reply)
    must = sorted(button_label(b) for b in want or [] if not str(b).endswith('?'))
    may = [button_label(str(b)[:-1]) for b in want or [] if str(b).endswith('?')]
    left = [b for b in sorted(button_label(b) for b in have) if b not in may]
    same = left == must
    add(same, "buttons: " + (", ".join(map(str, want)) or 'none') if same else
        _diff("The buttons are different.", _plain(want or []), _plain(have)))


def _check_sent_to_tenant(want, got, add, ctx):
    have = bool(got['sent'])
    add(have == bool(want), ("sent to the tenant (sandbox chat)" if want else "nothing sent to the tenant") if have == bool(want)
        else (_diff("Nothing was sent to the tenant.", "a message goes to the tenant", "nothing was sent" + (" (it is held until 08:00)" if got['held'] else ""))
              if want else _diff("Something was sent to the tenant by itself.", "nothing is sent", f"the tenant got \"{got['sent'][0]['text'][:160]}\"")))


def _check_sent_contains(want, got, add, ctx):
    text = "\n".join(s['text'] for s in got['sent'])
    missing = [f for f in want or [] if not _has(f, text)]
    add(not missing, "sent text has: " + ", ".join(map(str, want)) if not missing else "sent text misses: " + ", ".join(map(str, missing)))


def _check_call(want, got, add, ctx):
    have = bool(got['calls'])
    add(have == bool(want), ("call to Farid (simulated)" if want else "no call") if have == bool(want)
        else _diff("The phone call to Farid is different.", "a call" if want else "no call", "a call was made (simulated)" if have else "no call"))


def _check_earlier(want, got, add, ctx):
    earlier = got.get('earlier')
    if not earlier:
        return add(False, "earlier alert: there is none in this case")
    labels = [b.get('text') or '' for b in buttons_of(earlier['markup'])]
    if want.get('outdated'):
        ok = 'outdated' in earlier['text'].lower() and not labels
        add(ok, "earlier alert marked ⚠️ Outdated, buttons removed" if ok else
            f"earlier alert: expected \"⚠️ Outdated\" and no buttons, got buttons [{', '.join(labels) or 'none'}] and "
            f"{'the' if 'outdated' in earlier['text'].lower() else 'no'} Outdated mark")
    if want.get('contains'):
        ok = _has(want['contains'], earlier['text'] + "\n" + "\n".join(labels))
        add(ok, f"earlier alert shows \"{want['contains']}\"" if ok else f"earlier alert: \"{want['contains']}\" is not on it")
    if 'buttons' in want:
        same = sorted(map(button_label, labels)) == sorted(map(button_label, want['buttons'] or []))
        add(same, "earlier alert buttons: " + (", ".join(want['buttons']) or 'none') if same else
            _diff("The buttons of the earlier alert are different.", _plain(want['buttons'] or []), _plain(labels)))


def _check_reply_contains(want, got, add, ctx):
    text = _texts(got)
    missing = [f for f in want or [] if not _has(f, text)]
    add(not missing, "bot answer has: " + ", ".join(map(str, want)) if not missing else
        "bot answer misses: " + ", ".join(map(str, missing)) + (f" (answer: \"{text.strip()[:200]}\")" if text.strip() else " (the bot said nothing)"))


def _check_reply_not_contains(want, got, add, ctx):
    text = _texts(got)
    found = [f for f in want or [] if _has(f, text)]
    add(not found, "bot answer avoids: " + ", ".join(map(str, want)) if not found else
        _diff("The bot's answer says something it should not.", "no " + ", ".join(f'"{m}"' for m in found), f"\"{text.strip()[:300]}\""))


def _check_popup_contains(want, got, add, ctx):
    ok = any(_has(want, p) for p in got['popups'])
    add(ok, f"popup \"{want}\"" if ok else _diff("The small notice after the press is different.", f"\"{want}\"", " / ".join(got['popups']) or 'no notice'))


CHECKS = {name[len('_check_'):]: fn for name, fn in list(globals().items()) if name.startswith('_check_')}


def structured(case, got, world):
    """[(ok, text)]: ok None = the code could not check it (left to the judge)."""
    expect = case.get('expect') or {}
    lines = []
    wanted = expect.get('alert')
    ctx = {'world': world, 'alert': main_alert(got, wanted if str(wanted).lower() != 'none' else None)}
    if got.get('note') and str(wanted).lower() != 'none' and not got['alerts']:
        lines.append((False, got['note']))
    for held in got['held']:
        lines.append((None, f"held for the tenant until {_when(held['until'])} (SMS hours): \"{held['text'][:100]}\""))
    for key, want in expect.items():
        check = CHECKS.get(key)
        if not check:
            lines.append((None, f"{key}: not checked by code"))
            continue
        try:
            check(want, got, lambda ok, text: lines.append((ok, text)), ctx)
        except Exception as e:
            lines.append((False, f"{key}: the check itself failed ({type(e).__name__}: {e})"))
    return lines


# ---------------------------------------------------------------------------
# Claude: judge and test assistant
# ---------------------------------------------------------------------------

def _claude(prompt, system):
    """(text, cost) of one Claude call without tools."""
    from mysite.ai_agent import oneshot
    result = oneshot.complete(prompt, system=system, model=os.environ.get('AI_AGENT_SANDBOX_JUDGE_MODEL'))
    return result['text'], float(result.get('cost_usd') or 0)


def _json(text):
    start, end = text.find('{'), text.rfind('}')
    return json.loads(text[start:end + 1])


def render_result(got):
    """The step's result as text, for the judge, the report file and the terminal."""
    blocks = []
    for alert in got['alerts']:
        buttons = " ".join(f"[{b.get('text')}]" for b in buttons_of(alert['markup']))
        sound = 'without sound' if alert['parts'][-1]['silent'] else 'with sound'
        blocks.append(f"--- ALERT ({alert['type']}, posted {sound}, {len(alert['parts'])} message(s)) ---\n{alert['text']}\n"
                      f"BUTTONS: {buttons or 'none'}")
    shown = {id(part) for alert in got['alerts'] for part in alert['parts']}
    for message in got['others']:
        if id(message) in shown:
            continue
        buttons = " ".join(f"[{b.get('text')}]" for b in buttons_of(message['markup']))
        blocks.append(f"--- BOT MESSAGE ---\n{message['text']}" + (f"\nBUTTONS: {buttons}" if buttons else ""))
    blocks += [f"--- SENT TO THE TENANT (sandbox chat) ---\n{s['text']}" for s in got['sent']]
    blocks += [f"--- HELD FOR THE TENANT until {_when(h['until'])} ---\n{h['text']}" for h in got['held']]
    blocks += [f"--- POPUP AFTER A PRESS ---\n{p}" for p in got['popups']]
    blocks += [f"--- CALL ---\n{c.staff_name}: {c.status} ({c.note})" for c in got['calls']]
    if got.get('earlier'):
        earlier = got['earlier']
        blocks.append("--- THE EARLIER ALERT AS IT IS NOW (after this step) ---\n" + earlier['text'] + "\nBUTTONS: "
                      + (" ".join(f"[{b.get('text')}]" for b in buttons_of(earlier['markup'])) or 'none'))
    if not blocks:
        blocks.append(f"(nothing was posted or sent{': ' + got['note'] if got.get('note') else ''})")
    return "\n\n".join(blocks)


JUDGE_SYSTEM = (
    "You judge one test of the Telegram alerts of a property management company's AI assistant. Be strict about what "
    "the specification fixes and relaxed about wording. Your lines are read by a property manager, not a programmer: "
    "short plain sentences, everyday words, no technical or checker terms (never 'spec', 'result', 'commit', 'window', "
    "'dispatch', 'payload', 'mismatch'). Say 'the AI' and 'our example'. Reply with one JSON object and nothing else."
)
JUDGE_PROMPT = """The specification gives an EXAMPLE of what the team must see for this case. Compare the RESULT of the
test run with it BY MEANING, not word by word. The EXAMPLE is the target. The RESULT may come from older alert code
that is not rebuilt yet: describe what differs from the example, never call the result "new" or suggest going "back".

Must match: the alert type; which blocks exist (conversation lines, answer, tasks, updates, reminders, knowledge,
contract basis, test / status lines) and their layout marks (icons, order, header line, footer); the buttons; what the
answer to the tenant says and promises (it may be worded differently, it must not promise more or less); who gets a
task or reminder and when. "No alert" in the example means nothing may be posted.

Does NOT count as a difference: other words for the same thing. Task titles, reminder texts and the answer are written
by the AI and are worded differently on every run - "check a technician is scheduled" and "check the task has a visit
date" are the same reminder. A text only differs when it means something else: another problem, another person, a
promise to the tenant that our example does not make, or a promise that is missing. "We'll call you right away" and
"we'll get someone to help you right away" are the same promise (quick help). The answer to the tenant passes when it
(1) is about the same matter, (2) tells the tenant in some form what happens next (the team is told / will follow up /
will get back), and (3) does not promise something our example does not: a time or deadline for a repair or a visit
("a technician within 24 hours"), money, or a sure result ("it will be fixed"). HOW STRONGLY or WHEN the tenant hears
back - "today", "by tomorrow", "within the hour", "shortly", or no time at all - is not a difference, and neither is
which team member is named. Extra helpful information or house instructions from the knowledge base (a walkthrough
video at check-in, parking rules) and a clarifying question to the tenant are not differences either. The ⚖️ contract
line may be worded differently; it must point to the same rule. Also
not a difference: the dates (this run happened {shift} day(s) after the example's date, same weekday and
time of day); the unit name (the sandbox runs on a test apartment, with a 🧪 TEST mark); run / chat links and ids; the
"🧪 SANDBOX TEST" header line and the 🧪 TEST NOTES block. The test is ONE STORY: the ↪️ and 💬 quoted lines show the
last message of each side of the real sandbox chat at that moment, so their names and texts differ from the example's
(the example was written for its own short chat) - that is not a difference; what matters is that the lines exist, in
that layout, and that the decision / the reason line is right for the story's chat (THE CHAT BEFORE THIS MESSAGE).
The rule of those two lines (specification 1.2.12 / 1.3.7): ↪️ = the last message(s) of the side that wrote BEFORE,
💬 = the last message(s) of the side that wrote LAST; several consecutive messages of one side - also by different
people and the AI - are joined into ONE line with all their names. So which side comes first, and how many messages
a line holds, follows the real chat, never the example: not a difference.
In an example of a typed reply, the line "↩ Andy: <text>" is the reply the PERSON typed (shown for the reader), not a
part of the bot's answer: the bot's answer starts after it, and its absence in the RESULT is not a difference.
For the same reason a block the example closes or changes (a reminder "closes quietly", a task is updated) can be
absent in the RESULT when the team already did it by hand in an earlier chapter (a ✅ Reminder closed / ✅ Closed button
on THE EARLIER ALERT shows that): then there is nothing left to close and that is not a difference. A ⏰ line in THE
EARLIER ALERT is the reminder as it was created, not proof that it is still open.

CASE {case_id}: {title}
What it checks: {checks}
What was done in this test: {step}
Judge what THIS case is about. When the test is a button press or a typed reply, the example may show the alert as it
was BEFORE that action: then buttons that were replaced by their result (✅ Answer sent · Andy, ⏳ Answer will be sent
08:00 · Andy, ✅ Task created ...) and blocks that changed because of the action are what should happen, not
differences. "THE EARLIER ALERT AS IT IS NOW" is the alert that was acted on.

=== EXAMPLE (from the specification) ===
{example}

=== THE CHAT BEFORE THIS MESSAGE (the alert only shows its last lines; the AI saw all of it) ===
{history}

=== RESULT (what the test run really posted) ===
{result}

=== ALREADY CHECKED BY CODE (do not repeat these, do not contradict them) ===
{code_lines}

Reply as JSON:
{{"verdict": "pass" or "fail",
  "lines": [{{"ok": true or false, "text": "one plain sentence: what is the same, or what is different and why it matters",
             "example": "only when ok is false: the few words of our example that matter, copied exactly",
             "ai": "only when ok is false: the few words the AI really wrote / showed instead, copied exactly"}}],
  "suggestion": "when it fails: one plain sentence on what has to change so it matches our example; else empty"}}
When in doubt whether a wording difference matters, it does not: pass it. Lines: 1 to 4, only about what the code did
NOT check above - above all whether the answer to the tenant says and
promises the same, and the layout and order of the blocks. Do not write a line for anything listed under ALREADY
CHECKED BY CODE."""

RULES_PROMPT = """This alert came from replaying a REAL chat message through the assistant; there is no example to
compare with. Check it against the RULES of the specification that apply to every alert, and compare what the AI
proposed with WHAT THE TEAM REALLY DID next in that chat.

=== RULES ===
{rules}

=== RESULT (what the test run really posted) ===
{result}

=== WHAT THE TEAM REALLY DID NEXT ===
{team_next}

Reply as JSON:
{{"verdict": "pass" or "fail",   (fail = a rule is broken; a different but reasonable proposal is not a failure)
  "lines": [{{"ok": true or false, "text": "one short line per rule group you checked, and one line 'AI proposed ... / team did ...'"}}],   (3 to 7 lines)
  "suggestion": "a rule the AI could learn from the difference to the team, or empty"}}"""


def describe_step(step):
    if isinstance(step, list):
        who = 'tenant' if (step[0].get('from') or 'tenant') == 'tenant' else 'team member'
        return f"a new message from the {who}: " + " / ".join(f"\"{str(m.get('text'))[:300]}\"" for m in step)
    step = step or {}
    if 'press' in step:
        return f"{step.get('by') or 'Andy'} pressed the button [{step['press']}]" + (" on the FIRST (older) alert" if step.get('on') == 'first' else "")
    if 'reply' in step:
        return f"{step.get('by') or 'Andy'} typed this reply to the alert: \"{step['reply']}\""
    if 'notification' in step:
        return (f"the daily job planned the automatic notification '{step['notification']}' for this tenant; nobody wrote a "
                f"new message. The AI decides whether it is still needed")
    if 'reminder' in step:
        return "a reminder of the case became due"
    return "the 18:00 end-of-day reports were produced" if 'report' in step else str(step)


def chat_before(world, moment, limit=30):
    """The story's chat before this step, as the judge should see it: the last messages of the sandbox chat."""
    from mysite.models import TwilioMessage
    from mysite.ai_agent import inputs
    rows = TwilioMessage.objects.filter(conversation_sid__in=SANDBOX_SIDS, message_timestamp__lt=moment).order_by('-message_timestamp', '-id')[:limit]
    names = inputs._staff_names()
    lines = []
    for m in reversed(list(rows)):
        who = 'AI (sent)' if m.author == 'Virtual Assistant' else names.get(m.author, m.author) if m.direction == 'outbound' \
            else getattr(world.tenant, 'full_name', None) or 'tenant'
        side = 'team' if m.direction == 'outbound' and m.author != 'Virtual Assistant' else 'AI' if m.author == 'Virtual Assistant' else 'tenant'
        lines.append(f"{m.message_timestamp:%a %d %b %H:%M} {who} ({side}): {(m.body or '')[:300]}")
    return "\n".join(lines) or '(nothing)'


def judge(case, section, got, code_lines, shift_days, history='(nothing)'):
    """{'verdict': 'pass'|'fail'|None, 'lines': [(ok, text)], 'suggestion', 'cost'}; verdict None when it could not run."""
    if not section:
        return {'verdict': None, 'lines': [(None, "judge: the document has no section for this case")], 'suggestion': '', 'cost': 0}
    prompt = JUDGE_PROMPT.format(
        shift=shift_days, case_id=case['id'], title=case.get('title') or '', checks=case.get('checks') or '-',
        step=describe_step(case.get('trigger')), example=section['text'][:8000], result=render_result(got)[:24000],
        history=history[:4000],
        code_lines="\n".join(f"{'ok' if ok else 'FAILED' if ok is False else 'not checked'}: {text}" for ok, text in code_lines) or '(none)')
    return _run_judge(prompt)


# After a failed check: where the fix belongs (the person decides with a button whether to have it fixed)
DIAGNOSIS_SYSTEM = "You are the engineer of a QA suite for an AI assistant. Reply with one JSON object and nothing else."
DIAGNOSIS_PROMPT = """A test chapter of the Telegram alerts of our AI property-management assistant did not pass. Decide WHERE the fix belongs
and say it in plain words for the team (not for developers). The test is ONE STORY: the chapters follow each other in
one chat, and what the team pressed or typed in earlier chapters is part of the state.

Layout rule of the two quoted lines of an alert (not a bug): ↪️ = the last message(s) of the side that wrote before,
💬 = the last message(s) of the side that wrote last; consecutive messages of one side (also by different people and the
AI) are joined into one line. Which side is first follows the real chat.

The four possible places:
- agent: the AI decided or wrote the wrong thing (a wrong answer, a promise, a missing or wrong task / reminder,
  wrong urgency). Fixed in the AI's rules: mysite/ai_agent/prompts.py (ALERTS_V5_NOTES).
- alert: the alert itself is wrong (a block, a button, the layout, what a press did, the bot's reply to a typed
  reply). Fixed in the alert code: mysite/ai_agent/alerts_v5.py (and answer_review.py for typed replies).
- test: the AI and the alert are right; the expectation does not fit the story (a check in
  claude_code_integration_doc/testbed/sandbox_cases.yaml, the example in simple_telegram_alerts.md, or a rule of the
  judge in mysite/ai_agent/sandbox_test/check.py).
- story: the AI and the alert are right for what happened before (what the team pressed or typed earlier changed the
  story), so this chapter can not come out as written. Nothing to fix; the team chooses to skip it or to run the
  chapter before again.

CHAPTER {case_id}: {title}
What it checks: {checks}
What did not pass:
{failed}

The chat before this step:
{history}

The AI's own reasoning for this alert:
{why}

What was posted:
{result}

Reply with JSON: {{"where": "agent|alert|test|story", "what": "1-2 sentences for the team: what is wrong and what will be changed",
"files": ["the file(s) to change, from the list above"], "sure": "high|medium|low"}}"""


def diagnose(case, got, lines, history):
    """{'where', 'what', 'files', 'sure', 'cost'} or None when Claude could not answer."""
    failed = "\n".join(f"- {text}" for ok, text in lines if ok is False) or '(see the judge)'
    why = "\n".join((run.why or '')[:800] for run in got['runs'] if run.why) or '(no AI run)'
    try:
        text, cost = _claude(DIAGNOSIS_PROMPT.format(case_id=case['id'], title=case.get('title') or '', checks=case.get('checks') or '-',
                                                     failed=failed[:3000], history=(history or '')[:3000], why=why,
                                                     result=render_result(got)[:12000]), DIAGNOSIS_SYSTEM)
        data = _json(text)
        where = str(data.get('where') or '').lower()
        if where not in ('agent', 'alert', 'test', 'story'):
            return None
        return {'where': where, 'what': str(data.get('what') or '').strip(), 'files': [str(f) for f in data.get('files') or []],
                'sure': str(data.get('sure') or ''), 'cost': cost}
    except Exception:
        return None


WHERE_LABEL = {'agent': "the AI's rules (how it decides and writes)", 'alert': "the alert code (blocks, buttons, what a press does)",
               'test': "the test itself (the expectation does not fit the story)",
               'story': "nothing - the story went another way because of what was pressed or typed before"}


def diagnosis_text(diag):
    if not diag:
        return "🔧 I could not tell where the fix belongs. Press 🔧 Fix to have it looked at and this chapter run again, or 👌 No fix needed to go on."
    lines = [f"🔧 What is wrong: {WHERE_LABEL.get(diag['where'], diag['where'])}", f"   {diag['what']}"]
    if diag['where'] != 'story' and diag.get('files'):
        lines.append("   Files: " + ", ".join(diag['files']))
    if diag['where'] == 'story':
        lines.append("   Press 👌 No fix needed to go on (or run the chapter before again).")
    else:
        lines.append("   Press 🔧 Fix: it is fixed in those files and this chapter runs again from where it started. "
                     "Press 👌 No fix needed when the result is fine as it is.")
    return "\n".join(lines)


def judge_rules(got, team_next):
    return _run_judge(RULES_PROMPT.format(rules=catalog.doc_rules()[:12000], result=render_result(got)[:9000],
                                          team_next=team_next or '(nothing: no team message or task after it)'))


def _run_judge(prompt):
    try:
        text, cost = _claude(prompt, JUDGE_SYSTEM)
        data = _json(text)
        lines = []
        for line in data.get('lines') or []:
            if not isinstance(line, dict):
                continue
            text = str(line.get('text') or '')[:300]
            if not line.get('ok') and (line.get('example') or line.get('ai')):
                agreed = str(line.get('example') or '-').strip(' "')[:220]
                now = str(line.get('ai') or '-').strip(' "')[:220]
                text += f'\n   We agreed: "{agreed}"\n   The AI now: "{now}"'
            lines.append((bool(line.get('ok')), text))
        verdict = 'pass' if str(data.get('verdict')).lower() == 'pass' and all(ok for ok, _ in lines) else 'fail'
        return {'verdict': verdict, 'lines': lines, 'suggestion': str(data.get('suggestion') or '').strip(), 'cost': cost}
    except Exception as e:
        return {'verdict': None, 'lines': [(None, f"judge could not run ({type(e).__name__}: {str(e)[:150]})")],
                'suggestion': '', 'cost': 0}


# What a press does, in the tester's words (user, 2026-10-07: the notes are ONE list of exact actions, no options)
PRESS_RESULT = {
    'send answer': "the answer goes to the tenant (see the CRM chat); the button becomes ✅ Answer sent",
    'send answer sms': "the answer goes to the tenant (see the CRM chat); the button becomes ✅ Answer sent",
    'send answer crm': "the answer is written into the CRM chat only (no SMS); the button becomes ✅ Sent to CRM",
    'send + create task': "the answer goes to the tenant AND the task is created; ✅ Answer sent, ✅ Task created",
    'send + create tasks': "the answer goes to the tenant AND the tasks are created",
    'create task': "a [SANDBOX] task appears in the ClickUp TEST list; the button becomes ✅ Task created",
    'apply update': "the update is written on the task; the button becomes ✅ Commented / ✅ Closed",
    'close task': "the task is closed; the button becomes ✅ Closed",
    'close reminder': "the reminder is closed; the button becomes ✅ Reminder closed",
    'apply in crm': "the record is changed in the CRM; the block shows ✅ Done in CRM",
    'apartment': "the fact is saved to the apartment's knowledge page; the button becomes ✅ Saved to apartment",
    'global': "the fact is saved for all apartments (sandbox only); the button becomes ✅ Saved to global",
    'send message': "one more message goes to the tenant; the button becomes ✅ Message sent",
    'apply change': "the change is made; the button becomes ✅ Changed",
    'save rule': "the AI follows the rule next time; the button becomes ✅ Rule saved",
    'send anyway': "the held text goes to the tenant; the alert shows ✅ Sent",
    'send now': "the text goes to the tenant; the alert shows ✅ Sent",
    'edit answer': "the bot asks you for the text",
}


def _press_result(label):
    key = button_label(label.split('|')[0])
    for name, text in sorted(PRESS_RESULT.items(), key=lambda item: -len(item[0])):   # the longest name first
        if key.startswith(name):
            return text
    return "see what the button does"


def static_notes(case, clickable=True, markup=None):
    """The 🧪 TEST NOTES of an alert: one line of what is tested (the chapter's title) and ONE list of exact actions
    with their result - the chapter's `presses` (the same the runner does in --auto), then Next test. Always the
    same for a chapter, never written by Claude."""
    lines = ["🧪 TEST NOTES", f"What we test: {case.get('title') or case['id']}"]
    steps = []
    for press in case.get('presses') or []:
        label = str(press.get('button') or '').split('|')[0].strip()
        if not label:
            continue
        on_alert = next((b.get('text') for b in buttons_of(markup) if button_label(b.get('text') or '').startswith(button_label(label))), None)
        if on_alert is None and press.get('optional'):
            continue
        steps.append(f"Press {on_alert or label} → {_press_result(label)}.")
    if not clickable:
        lines += ["View only: the buttons can not be pressed in this run."]
    elif steps:
        lines += ["Do this:"] + [f"{n}. {t}" for n, t in enumerate(steps, 1)] + [f"{len(steps) + 1}. Press 🧪 Next test."]
    else:
        lines += ["Nothing to press here. Press 🧪 Next test."]
    return "\n".join(lines)


def test_notes(case, section, alert_text, markup, clickable=True, use_claude=True):
    """(notes text, cost): the notes are built from the case file and the alert's buttons, never written by Claude."""
    return static_notes(case, clickable, markup), 0


CHANGE_SYSTEM = "You maintain the test cases of a QA suite. Reply with one JSON object and nothing else."
CHANGE_PROMPT = """A person asked to change a test case. Propose the change; nothing is applied until they approve.

REQUEST: {request}

The case in testbed/sandbox_cases.yaml (keys: title, time, mode, clickup, tenant, setup, history, prelude, trigger,
expect, presses, checks, try - keep the existing style and keys):
{yaml_block}

Its example in the specification document (the first code block of the section is what the alert must look like):
{example}

What the agent really posted in the last run of this case:
{result}

Reply as JSON:
{{"explanation": "2-4 short lines for the person: what will change in the case file, what in the document example, why",
  "yaml": "the complete new YAML block of this case, starting with '{case_id}:' - or null when the case file does not change",
  "doc_example": "the complete new text of the example code block (without the ``` lines) - or null when the document does not change"}}"""


def propose_change(case, section, got, request):
    """{'explanation', 'yaml', 'doc_example', 'cost'} or {'error'}."""
    try:
        text, cost = _claude(CHANGE_PROMPT.format(
            request=request, yaml_block=catalog.case_yaml(case['id']) or yaml.safe_dump({case['id']: case}, allow_unicode=True),
            example=(section or {}).get('text', '')[:6000], result=render_result(got)[:6000] if got else '(not run)',
            case_id=case['id']), CHANGE_SYSTEM)
        data = _json(text)
        return {'explanation': str(data.get('explanation') or '').strip(), 'yaml': data.get('yaml') or None,
                'doc_example': data.get('doc_example') or None, 'cost': cost}
    except Exception as e:
        return {'error': f"{type(e).__name__}: {str(e)[:200]}"}


NEW_CASE_PROMPT = """A real tenant chat was replayed in the test sandbox. The person watching wants this situation as a new
test case. Write the case; nothing is saved until they approve it.

Rules:
- PRIVACY: this goes into the code repository. Replace every real person's name, phone, e-mail, door / wifi / lock code
  and the unit name with invented ones (tenant: an invented full name; unit: an invented name like "630-214"; team
  members keep their names: {staff}). Keep the meaning and the tone of the messages; shorten very long ones.
- The case id is the placeholder NEW (first line of the YAML block: "NEW:").
- history: at most the last 6 messages before the trigger that matter for it, oldest first, with "at" times.
- trigger: the new message(s) exactly as they came in the replay (anonymized).
- setup.knowledge: only the facts the AI's answer really used (invented values for codes / passwords). setup.parking /
  setup.payments only when the alert depends on them.
- expect: what the alert below shows - it becomes the agreed result. Use only these keys: {expect_keys}.
  Follow the style of the example cases. Check texts loosely (answer_contains with 1-2 key words, not whole sentences).
- A reminder, task or knowledge block that the alert shows was CREATED by this run: it belongs in expect (reminders:
  [{{kind, in, n}}], tasks: 1, ...), never in setup. setup.issues / setup.reminders are only for what was already open
  BEFORE the new message.
- checks: one sentence, what this case checks. try: one or two things to press.
- doc_example: the alert as it should look in the specification - copy the alert below (anonymized, the unit name as
  in your case, lines wrapped at about 72 characters), body only, WITHOUT the 🧪 header, the TEST NOTES, the footer
  links and the buttons line; end it with "(footer)" and the buttons in [ ] like the example.

EXAMPLE CASES (format):
{examples}

EXAMPLE of a doc_example:
{doc_example}

=== THE REPLAY ===
Unit: {apartment} · tenant: {tenant} · mode: test · replayed message time: {when}
Earlier messages (oldest first):
{history}

New message(s) the AI handled:
{trigger}

What the AI did (the alert as posted, with its buttons):
{result}

Reply as JSON:
{{"title": "short title of the case", "group": "A for a tenant message, B for a team message",
  "explanation": "2-3 short lines for the person: what the case covers and what was changed for privacy",
  "yaml": "the complete YAML block starting with 'NEW:'", "doc_example": "the example block text"}}"""


def propose_new_case(info):
    """A new catalog case from one replayed message. {'title', 'group', 'explanation', 'yaml', 'doc_example', 'cost'} or {'error'}."""
    try:
        sections = catalog.doc_sections()
        text, cost = _claude(NEW_CASE_PROMPT.format(
            staff="Edy, Kevin, Janna, Farid", expect_keys=", ".join(sorted(CHECKS)),
            examples=(catalog.case_yaml('A1') + "\n" + catalog.case_yaml('B2'))[:6000],
            doc_example=(sections.get('A1') or {}).get('text', '')[:1800],
            apartment=info['apartment'], tenant=info['tenant'], when=info['when'],
            history="\n".join(info['history'][-12:]) or '(none)', trigger="\n".join(info['trigger']),
            result=info['result'][:9000]), CHANGE_SYSTEM)
        data = _json(text)
        block = str(data.get('yaml') or '').strip()
        if not block.startswith('NEW:') or not str(data.get('doc_example') or '').strip():
            raise ValueError('the proposal has no case block or no example')
        yaml.safe_load(block)
        return {'title': str(data.get('title') or 'Case from a real chat')[:90], 'group': 'B' if str(data.get('group')).upper().startswith('B') else 'A',
                'explanation': str(data.get('explanation') or '')[:800], 'yaml': block, 'doc_example': str(data['doc_example']), 'cost': cost}
    except Exception as e:
        return {'error': f"{type(e).__name__}: {str(e)[:200]}"}


def verdict_text(title, lines, suggestion=''):
    """The 🧪 RESULT message. Returns (text, passed)."""
    passed = not any(ok is False for ok, _ in lines)
    text = f"🧪 RESULT · {title} · {'✅ PASS' if passed else '❌ FAIL'}\n\n" + "\n".join(f"{ICON[ok]} {line}" for ok, line in lines)
    if suggestion and not passed:
        text += f"\nSuggestion: {suggestion}"
    return text, passed


def failure_text(title, lines, suggestion=''):
    """What goes to the test group: only what did NOT pass (user, 2026-10-06: a passed test needs no message)."""
    failed = [line for ok, line in lines if ok is False]
    text = (f"🧪 {title} · ❌ {len(failed)} thing{'s are' if len(failed) != 1 else ' is'} different from our example\n\n"
            + "\n\n".join(f"{number}. {line}" for number, line in enumerate(failed, 1)))
    return text + (f"\n\n💡 What to change: {suggestion}" if suggestion else "")
