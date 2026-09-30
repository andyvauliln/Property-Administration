"""
Staff review in the Telegram AI group: NOTHING a run wants happens at once. The answer is held and every action becomes
a numbered plan; after 15 minutes (or on "ok") it is all done, unless staff changed / removed / stopped it.
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
os.environ["AI_AGENT_REVIEW_HOLD_MINUTES"] = "15"
os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

from django.core.management import call_command
from django.utils import timezone
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement, AIEvent, AIRun,
                           AIIssue, AIFollowUp, StaffMember)
from mysite.ai_agent import service, runner, config, answer_review, clickup
import mysite.ai_agent.team_notify as team_notify
import mysite.views.messaging as messaging
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_review"
config.WORK_DIR = config.RUNS_DIR / "_cwd"
config.is_within_notification_window = lambda now=None: True

# ---- fakes ---------------------------------------------------------------------------------
telegram, sms, tg_fail = [], [], [False]
def fake_tg(text, reply_to=None):
    telegram.append((text, reply_to))
    return (False, "down", None) if tg_fail[0] else (True, "sent", 1000 + len(telegram))
team_notify.send_ai_chat = fake_tg
answer_review.send_ai_chat = fake_tg
answer_review.report_error = service.report_error = lambda e, ctx, info=None, source='task': telegram.append((f"ERROR {ctx}: {e}", None))
messaging.send_messsage_by_sid = lambda sid, author, message, sender, receiver: sms.append((sid, message))
updates, interpreter_calls, interpreter_out = [], [], []
answer_review.fetch_updates = lambda: [updates.pop(0) for _ in range(len(updates))]
def fake_interpreter(prompt):
    interpreter_calls.append(prompt); return interpreter_out.pop(0)
answer_review.run_interpreter = fake_interpreter
cu = []   # every ClickUp call
class _Map: list_id, channel_id, name = 'L1', None, 'list 720-101'
clickup.delivery_mode = lambda: 'api'
clickup.channel_for = lambda apartment: _Map()
clickup.post_message = lambda *a, **k: None
def fake_create(list_id, name, description, priority='routine', assignee_ids=None, due_at=None, tags=None):
    cu.append(('create', name, priority)); return f"tk{len(cu)}", f"https://app.clickup.com/t/tk{len(cu)}"
clickup.create_task = fake_create
clickup.set_task_closed = lambda ref, closed=True: (cu.append(('close' if closed else 'reopen', ref)), 'complete' if closed else 'to do')[1]
clickup.delete_task = lambda ref: cu.append(('delete', ref))
clickup.update_task = lambda ref, name=None, description=None, priority=None: cu.append(('update', ref, name, priority))
clickup.add_task_comment = lambda ref, text: cu.append(('comment', ref, text))
clickup.get_task_state = lambda ref: {'status': 'to do', 'closed': False, 'assignees': ['Andrei'], 'updated': '2026-09-23 09:40',
                                      'due': '2026-09-26 10:00', 'url': ref, 'comments': [{'when': 'x', 'user': 'Andrei', 'text': 'router restarted', 'ms': 1}]}

script, inputs_seen, systems_seen = [], [], []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    inputs_seen.append(user_input); systems_seen.append(system_prompt)
    return {'ok': True, 'error': None, 'output': script.pop(0), 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 5, 'timed_out': False}
runner.run_claude = fake_run_claude

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

# ---- fixtures -------------------------------------------------------------------------------
if not User.objects.filter(email="r@example.com").exists():
    User.objects.bulk_create([User(email="r@example.com", full_name="Rita Tenant", role="Tenant", phone="+15550002233")])
tenant = User.objects.get(email="r@example.com")
def make_apartment(name, live):
    Apartment.objects.bulk_create([Apartment(name=name, building_n="720", apartment_n=name[-3:], street="S", state="FL", city="WPB",
        zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_group_chat_enabled=live)])
    apt = Apartment.objects.get(name=name)
    Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=2),
                                         end_date=date.today() + timedelta(days=20), status="Confirmed")])
    sid = f"CHrev{name}"
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=sid, friendly_name=name, apartment=apt,
                                                               booking=Booking.objects.get(apartment=apt))])
    return apt, sid
apt, SID = make_apartment("720-101", live=True)
apt_test, SID_TEST = make_apartment("720-102", live=False)
AIManagement.objects.update_or_create(prompt_key="ai_backend", defaults={'name': "backend", 'entry_type': "ai_model", 'content': "claude_cli"})
if not StaffMember.objects.filter(ai_name="Edy").exists():
    StaffMember.objects.bulk_create([StaffMember(ai_name="Edy", full_name="Farouk Ahmed", role="operations", phone="+15612205252")])
StaffMember.objects.filter(ai_name="Edy", phone__isnull=True).update(phone="+15612205252")
EDY_PHONE = StaffMember.objects.get(ai_name="Edy").phone
n = [0]
def say(sid, body, author="+15550002233"):
    n[0] += 1
    conv = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"RV{n[0]:04d}", conversation=conv, conversation_sid=sid,
                                                     author=author, body=body, direction='inbound')])
    if author == "+15550002233":
        service.enqueue_tenant_message(sid, f"RV{n[0]:04d}", body)
    return TwilioMessage.objects.get(message_sid=f"RV{n[0]:04d}")
def drain():
    AIEvent.objects.filter(status='pending').update(created_at=timezone.now() - timedelta(minutes=5))
    runs = []
    while True:
        batch = service.claim_next_batch()
        if not batch: return runs
        runs.append(service.process_events(batch))
def run_with(sid, body, output):
    say(sid, body); script.append(output); r = drain()[0]; r.refresh_from_db(); return r
def reply(run, text, chat=-500, who="Kevin", **decision):
    if decision:
        interpreter_out.append(dict(answer_review.EMPTY_DECISION, **decision))
    run.refresh_from_db()
    updates.append({'update_id': 50 + len(checks) + len(telegram), 'message': {
        'message_id': 9000 + len(checks), 'chat': {'id': chat}, 'from': {'first_name': who}, 'text': text,
        'date': int(timezone.now().timestamp()), 'reply_to_message': {'message_id': run.telegram_message_id}}})
    answer_review.poll_telegram()
    run.refresh_from_db()
    return telegram[-1][0]
def expire(run):
    AIRun.objects.filter(id=run.id).update(hold_until=timezone.now() - timedelta(seconds=1)); answer_review.release_due(); run.refresh_from_db()
def alert_of(text):
    return next(t for t, _ in reversed(telegram) if text in t)
def op(kind, n, **fields):
    return dict({'op': kind, 'n': n, 'priority': '', 'title': '', 'text': '', 'value': '', 'state': '', 'owner': ''}, **fields)
def task_op(kind, ticket, **fields):
    return dict({'op': kind, 'ticket_id': ticket, 'title': '', 'description': '', 'priority': '', 'comment': ''}, **fields)

SINK = {'answer': "Thanks, I've logged the dripping sink with the team.", 'why': 'maintenance', 'actions': [
    {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Kitchen sink dripping', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
    {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'priority': 'routine', 'title': 'Kitchen sink dripping', 'description': 'drips'},
    {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'routine', 'responsible': ['Edy'], 'text': 'sink dripping'},
    {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': 'recheck the sink'},
    {'type': 'KB_UPDATE', 'key': 'sink brand', 'value': 'Moen', 'scope': 'apartment'}]}
def sink(sid=None, body="The kitchen sink is dripping"):
    """The SINK plan; every other call gets its own issue title so it is not merged into an open issue."""
    actions = [dict(a) for a in SINK['actions']]
    if body != "The kitchen sink is dripping":
        for a in actions:
            if a['type'] == 'CREATE_ISSUE': a['summary'] = body
            if a['type'] == 'CREATE_TICKET': a['title'] = body
    return run_with(sid or SID, body, {**SINK, 'actions': actions})

# ---- 1. nothing happens at once: answer held + numbered plan --------------------------------------
r1 = sink()
a1 = alert_of("kitchen sink is dripping")
if os.environ.get('SHOW_ALERT'): print(a1 + "\n=====")
check("nothing was done: no SMS, no issue, no reminder, no knowledge, nothing in ClickUp",
      not sms and not AIIssue.objects.filter(conversation_sid=SID).exists() and not AIFollowUp.objects.filter(conversation_sid=SID).exists()
      and 'Moen' not in (Apartment.objects.get(id=apt.id).knowledge_base or '') and not cu)
check("alert says right under the header that it all happens AUTOMATICALLY at HH:MM unless someone replies, and again at the end",
      a1.splitlines()[2].startswith("⏰ AUTOMATIC at") and "(in 15 min)" in a1.splitlines()[2]
      and "the whole plan above runs AUTOMATICALLY" in a1, a1)
check("alert: NOTHING IS DONE YET + time, the answer, every change numbered with details",
      "📋 PLAN - NOTHING IS DONE YET. At" in a1 and "Send this answer to the tenant" in a1 and '1. 🆕 Open issue "Kitchen sink dripping"' in a1
      and '2. 🎫 Create ClickUp task "[AI] 720-101 · Kitchen sink dripping"' in a1 and "List: list 720-101" in a1
      and "3. ⏰ Reminder (staff_reminder)" in a1 and "4. 📚 Update the 720-101 knowledge base: Sink brand: Moen" in a1
      and "from the tenant" in a1, a1)
check("the team alert is shown as the notification itself, not as a planned change",
      "👤 FOR THE TEAM (this message is the notification):" in a1 and "sink dripping" in a1)
check("reply instructions list ok / stop / remove / change / test", '"ok" = do it all now' in a1 and '"remove 3"' in a1 and '"test"' in a1)
check("run state: answer held, plan pending, both on the same timer",
      r1.hold_status == 'holding' and r1.review['plan']['status'] == 'pending' and r1.hold_until and r1.telegram_message_id
      and all(a['status'] in ('planned', 'executed') for a in r1.actions))
answer_review.release_due()
check("nothing released before the window ends", not sms and not cu and AIRun.objects.get(id=r1.id).review['plan']['status'] == 'pending')

# ---- 2. staff edit the plan, then the window ends -----------------------------------------------
t = reply(r1, "make the task urgent and no reminder", decision='changes_only', plan_ops=[op('change', 2, priority='urgent'), op('remove', 3)])
check("plan edits are reported and the plan is shown as it stands now",
      "✏ Changed #2" in t and "priority → urgent" in t and "❌ Removed #3" in t and "Plan (happens automatically at" in t
      and "3. ❌ REMOVED by Kevin" in t and "(changed by Kevin)" in t, t)
check("still nothing done after the edits", not cu and not AIIssue.objects.filter(conversation_sid=SID).exists())
expire(r1)
issue1 = AIIssue.objects.filter(created_by_run_id=r1.id).first()
check("window over: answer sent, issue opened, task created URGENT, no reminder, tenant's apartment fact in the document",
      sms == [(SID, SINK['answer'])] and issue1 and issue1.ticket_ref and cu == [('create', cu[0][1], 'urgent')]
      and not AIFollowUp.objects.filter(issue=issue1).exists() and 'Sink brand: Moen' in (Apartment.objects.get(id=apt.id).knowledge_base or ''), (sms, cu))
done1 = telegram[-1][0]
if os.environ.get('SHOW_ALERT'): print(t + "\n=====\n" + done1 + "\n=====")
check("thread gets what was done", done1.startswith("⏰ Review window over") and "✅ Answer: sent" in done1
      and "2. 🎫 Create ClickUp task" in done1 and "CREATED → https://app.clickup.com/t/" in done1 and "1. 🆕 Open issue" in done1, done1)
answer_review.release_due()
check("done once only", len(cu) == 1 and len(sms) == 1)
check("run report + stored actions show the real results", AIRun.objects.get(id=r1.id).review['plan']['status'] == 'applied'
      and any('created i-' in str(a['detail']) for a in AIRun.objects.get(id=r1.id).actions)
      and os.path.exists(os.path.join(r1.report_dir, '09_review.json')))

# ---- 3. "ok" = everything now; "stop" = nothing; "don't send" = only the answer -------------------
sms.clear(); cu.clear()
r2 = sink(body="Bathroom sink drips too")
t = reply(r2, "ok")
check("'ok': answer and plan done at once", sms and cu and "Done now:" in t and AIRun.objects.get(id=r2.id).review['plan']['status'] == 'applied', t)
sms.clear(); cu.clear()
r3 = sink(body="Shower drips")
t = reply(r3, "stop")
expire(r3)
check("'stop': nothing at all - no answer, no plan, even after the window",
      not sms and not cu and r3.hold_status == 'cancelled' and r3.review['plan']['status'] == 'cancelled' and "🛑 Plan cancelled" in t, t)
r4 = sink(body="Tub drips")
reply(r4, "don't send")
expire(r4)
check("\"don't send\": the answer is cancelled but the plan still happens", not sms and cu and r4.hold_status == 'cancelled'
      and r4.review['plan']['status'] == 'applied')

# ---- 4. corrected answer, lesson, facts from the reply -------------------------------------------
sms.clear(); cu.clear()
r5 = run_with(SID, "what's the wifi password?", {'answer': 'It is sunny2026.', 'why': 'kb', 'actions': []})
check("an answer without actions: held, no plan", r5.hold_status == 'holding' and not r5.review.get('plan'))
t = reply(r5, "say check password B123H4689", decision='replace', corrected_answer='The WiFi password is B123H4689.',
          new_facts=[{'key': 'wifi password', 'value': 'B123H4689', 'scope': 'apartment'}])
kb_now = Apartment.objects.get(id=apt.id).knowledge_base or ''
check("correction sent at once + the fact written into the apartment document at once, report says both",
      sms == [(SID, 'The WiFi password is B123H4689.')] and 'Wifi password: B123H4689' in kb_now
      and "Sent your corrected answer" in t and "The 720-101 knowledge base: added Wifi password: B123H4689" in t, (t, kb_now))
check("the interpreter saw the tenant text and the AI answer",
      "what's the wifi password?" in interpreter_calls[-1] and "It is sunny2026." in interpreter_calls[-1])
t = reply(r5, "next time always add the router location", decision='lesson_only',
          lesson="When a tenant asks for the WiFi password, also say the router is in the hallway closet.",
          lesson_key='wifi password answer', lesson_scope='apartment')
from mysite.ai_agent import prompt_library
check("lesson saved into the answer lessons prompt and reported",
      any(l.startswith(f"- [apartment #{apt.id}") and 'wifi_password_answer: When a tenant asks for the WiFi password' in l
          for l in prompt_library.rule_lines('ai_agent_answer_lessons')) and "AI instructions updated" in t)
r6 = run_with(SID, "wifi again?", {'answer': 'Password is B123H4689, router in the hallway closet.', 'why': 'lesson', 'actions': []})
check("the next run gets ANSWER_LESSONS (system prompt) and the fact in the apartment document (input)", "ANSWER_LESSONS" in systems_seen[-1]
      and "hallway closet" in systems_seen[-1] and "Wifi password: B123H4689" in inputs_seen[-1])
reply(r6, "ok")

# ---- 5. existing task: tenant says it's fixed -> close planned; keep it open ----------------------
cu.clear(); sms.clear()
fixed = AIIssue.objects.create(conversation_sid=SID, apartment=apt, summary='WiFi down', state='MAINTENANCE_OPEN',
                               ticket_title='WiFi down', ticket_ref='https://app.clickup.com/t/wifi1', mode='live')
RESOLVE = lambda issue: {'answer': 'Great, glad it works!', 'why': 'fixed', 'actions': [
    {'type': 'UPDATE_ISSUE_STATE', 'issue_id': issue.public_id, 'state': 'RESOLVED'},
    {'type': 'TICKET_COMMENT', 'ticket_id': f't-{issue.id}', 'text': 'Tenant confirmed it works'}]}
r7 = run_with(SID, "wifi works now thanks", RESOLVE(fixed))
fixed.refresh_from_db(); a7 = alert_of("wifi works now")
check("resolve + comment on an existing task are only planned; alert shows task details, close and the comment",
      fixed.is_open and not cu and f"1. 🔄 Issue {fixed.public_id}" in a7 and "🔒 and CLOSE its ClickUp task → https://app.clickup.com/t/wifi1" in a7
      and "Status: to do · assigned: Andrei · due 2026-09-26 10:00" in a7 and "Last comment (Andrei" in a7
      and "2. 💬 Comment on ClickUp task" in a7 and "Tenant confirmed it works" in a7, a7)
expire(r7); fixed.refresh_from_db()
check("window over: issue resolved, task closed, comment added", not fixed.is_open
      and ('close', fixed.ticket_ref) in cu and any(c[0] == 'comment' and 'Tenant confirmed' in c[2] for c in cu), cu)
cu.clear()
heat = AIIssue.objects.create(conversation_sid=SID, apartment=apt, summary='Heater', state='MAINTENANCE_OPEN',
                              ticket_title='Heater', ticket_ref='https://app.clickup.com/t/heat1', mode='live')
r8 = run_with(SID, "heater ok", RESOLVE(heat))
t = reply(r8, "keep it open, part still missing", decision='changes_only', plan_ops=[op('remove', 1)])
expire(r8); heat.refresh_from_db()
check("'keep it open': the resolve/close item removed - task stays open, issue open, comment still added",
      heat.is_open and not [c for c in cu if c[0] == 'close'] and [c for c in cu if c[0] == 'comment'], cu)

# ---- 6. "no task needed" removes the task and what only exists for it -------------------------------
cu.clear()
r9 = sink(body="small scratch on the door")
t = reply(r9, "no task needed", decision='changes_only', plan_ops=[op('remove', 1)])
check("removing the new issue also removes the items that depend on it", "and 3 item(s) that depend on it" in t, t)
expire(r9)
check("window over: no issue, no task, no reminder",
      not cu and AIIssue.objects.filter(created_by_run_id=r9.id).count() == 0)

# ---- 7. knowledge corrected / approved in the plan -----------------------------------------------------
r10 = run_with(SID, "the gym is open 6-22", {'answer': 'Thanks!', 'why': 'info', 'actions': [
    {'type': 'KB_UPDATE', 'key': 'gym hours', 'value': 'Gym 6am-10pm', 'scope': 'apartment'},
    {'type': 'KB_UPDATE', 'key': 'pool rule', 'value': 'No glass', 'scope': 'company'}]})
t = reply(r10, "gym is 5-23, and the pool rule is right", decision='changes_only',
          plan_ops=[op('change', 1, value='Gym 5am-11pm'), op('approve', 2)])
check("plan shows the corrected / approved knowledge", "approved by Kevin" in t, t)
expire(r10)
apt_kb, global_kb = Apartment.objects.get(id=apt.id).knowledge_base or '', messaging.get_global_knowledge_base_text()
check("written at the end: the corrected text in the apartment document; the approved company-wide rule in the global one",
      'Gym 5am-11pm' in apt_kb and 'Gym 6am-10pm' not in apt_kb and 'Pool rule: No glass' in global_kb, (apt_kb, global_kb))

# ---- 8. existing ClickUp tasks from a reply: done at once -------------------------------------------
cu.clear()
ac = AIIssue.objects.create(conversation_sid=SID, apartment=apt, summary='AC broken', state='MAINTENANCE_OPEN',
                            ticket_title='AC broken', ticket_ref='https://app.clickup.com/t/ac1', mode='live')
reminder = AIFollowUp.objects.create(conversation_sid=SID, issue=ac, kind='staff_reminder', due_at=timezone.now() + timedelta(hours=5))
r11 = run_with(SID, "AC still broken", {'answer': 'NO_ANSWER', 'why': 'staff handles', 'actions': [
    {'type': 'INTERNAL_ALERT', 'issue_id': ac.public_id, 'priority': 'urgent', 'responsible': ['Edy'], 'text': 'AC still broken'}]})
check("an alert with no answer and no changes: says so, can still be replied to", r11.telegram_message_id and not r11.review.get('plan')
      and "no answer to the tenant - and nothing else to change" in alert_of("AC still broken"))
t = reply(r11, "test done", decision='changes_only', task_actions=[task_op('close', f't-{ac.id}')])
check("'test done': says it would close, nothing changed", "Would CLOSE" in t and not cu and AIIssue.objects.get(id=ac.id).is_open)
t = reply(r11, "done", decision='changes_only', task_actions=[task_op('close', f't-{ac.id}')])
ac.refresh_from_db()
check("'done' on an existing task: closed at once, issue resolved, reminders stopped", cu == [('close', ac.ticket_ref)]
      and not ac.is_open and AIFollowUp.objects.get(id=reminder.id).status == 'cancelled' and "✅ CLOSED" in t, t)
t = reply(r11, "create a task for the filter", decision='changes_only', task_actions=[task_op('create', '', title='Replace AC filter', priority='routine')])
check("'create a task' from a reply: created at once", cu[-1][0] == 'create' and "✅ CREATED task" in t)

# ---- 9. a later run sees the pending plan and can use its issue ------------------------------------
cu.clear()
r12 = sink(body="Toilet runs all night")
r13 = run_with(SID, "and now it's leaking on the floor", {'answer': 'I have raised the priority.', 'why': 'worse', 'actions': [
    {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': f'r{r12.id}:new-1', 'kind': 'staff_reminder', 'reason': 'check the leak'}]})
check("the next run is told about the pending plan and how to refer to its new issue",
      f"PENDING_PLAN of your earlier run #{r12.id}" in inputs_seen[-1] and f"r{r12.id}:new-1" in inputs_seen[-1])
dbg = reply(r13, "ok")
new_issue = AIIssue.objects.filter(created_by_run_id=r12.id).first()
check("'ok' on the later plan does the earlier plan first, then resolves its reference to that issue",
      new_issue and AIRun.objects.get(id=r12.id).review['plan']['status'] == 'applied'
      and f"for {new_issue.public_id}" in dbg and AIFollowUp.objects.filter(issue=new_issue).exists(), dbg)
reply(r12, "ok")

# ---- 10. answers: supersede, staff answers directly, emergencies, Telegram down ---------------------
sms.clear(); cu.clear()
r14 = run_with(SID, "Is parking free?", {'answer': 'Yes, parking is free.', 'why': 'kb', 'actions': []})
r15 = run_with(SID, "Where do I park?", {'answer': 'Parking is free, spot 12.', 'why': 'kb', 'actions': []})
check("the newer answer replaces the held one; the AI saw the draft", "PENDING_AI_ANSWER" in inputs_seen[-1]
      and AIRun.objects.get(id=r14.id).hold_status == 'superseded' and AIRun.objects.get(id=r15.id).hold_status == 'holding')
say(SID, "Spot 12, blue sign. - Edy", author=EDY_PHONE)
expire(r15)
check("staff answered in the chat meanwhile -> the held answer is dropped", not sms and r15.hold_status == 'suppressed')
r16 = run_with(SID, "There is smoke from the oven!", {'answer': 'Leave the unit and call 911 now.', 'why': 'emergency', 'actions': [
    {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Oven smoke', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
    {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'emergency', 'responsible': ['Edy'], 'text': 'SMOKE'}]})
check("emergency: no review - answer sent and issue opened at once",
      sms == [(SID, 'Leave the unit and call 911 now.')] and AIIssue.objects.filter(summary='Oven smoke').exists() and not r16.review.get('plan'))
sms.clear(); tg_fail[0] = True
r17 = sink(body="Fridge is noisy")
tg_fail[0] = False
check("Telegram down: nobody can review, so everything happens at once", sms and cu and r17.review['plan']['status'] == 'applied')

# ---- 11. test mode: same review, never sent -------------------------------------------------------
sms.clear(); cu.clear()
r18 = sink(SID_TEST, "Test flat: sink drips")
a18 = alert_of("Test flat: sink drips")
check("test mode: same plan + held answer, marked TEST", r18.hold_status == 'holding' and r18.review['plan']['status'] == 'pending'
      and "TEST: shown in the CRM chat, never sent to Twilio" in a18 and "[AI] [TEST]" in a18, a18)
t = reply(r18, "say a plumber comes tomorrow", decision='replace', corrected_answer='A plumber comes tomorrow.')
expire(r18)
check("test mode: correction final, never sent; plan still done at the end (task marked TEST)",
      not sms and r18.hold_status == 'corrected' and r18.final_answer == 'A plumber comes tomorrow.' and cu and '[TEST]' in cu[0][1])

# ---- 12. dry runs, noise, unclear -----------------------------------------------------------------
cu.clear()
r19 = sink(body="Window stuck")
t = reply(r19, "test remove 2", decision='changes_only', plan_ops=[op('remove', 2)])
check("'test remove 2': would remove, nothing changed", t.startswith("🧪 TEST") and "Would REMOVE #2" in t
      and not AIRun.objects.get(id=r19.id).review['plan']['actions'][1].get('removed_by'))
t = reply(r19, "test stop")
check("'test stop': would cancel everything, nothing changed", "Would CANCEL the whole plan" in t and "Would cancel the AI answer" in t
      and AIRun.objects.get(id=r19.id).review['plan']['status'] == 'pending')
t = reply(r19, "Test")
check("plain 'test': shows the answer state and the plan", "Answer: waits for review" in t and "1. 🆕 Open issue" in t)
before = len(interpreter_calls)
reply(r19, "stop", chat=-999)
updates.append({'update_id': 99999, 'message': {'message_id': 1, 'chat': {'id': -500}, 'from': {}, 'text': 'hello team', 'date': 0}})
answer_review.poll_telegram()
check("replies in other chats and plain messages are ignored", AIRun.objects.get(id=r19.id).review['plan']['status'] == 'pending'
      and len(interpreter_calls) == before)
t = reply(r19, "hmm?", decision='unclear')
check("unclear: asks again, everything stays on its timer", "❓ Not sure" in t and AIRun.objects.get(id=r19.id).review['plan']['status'] == 'pending')

# ---- 13. watchdog ---------------------------------------------------------------------------------
import mysite.management.commands.ai_agent_watchdog as wd
alerts = []; wd.notify_ai_chat = lambda text: (alerts.append(text), (True, 'ok'))[1]
AIRun.objects.filter(id=r19.id).update(hold_until=timezone.now() - timedelta(minutes=20))
call_command('ai_agent_watchdog')
check("watchdog alerts on answers / plans past their window", alerts and 'passed their review window' in alerts[-1])

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
