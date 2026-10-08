"""End-to-end Phase 2 test on a throwaway SQLite DB with a scripted fake Claude."""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

from django.utils import timezone
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement,
                           AIEvent, AIRun, AIIssue, AIFollowUp, AICaseNote, StaffMember)
from mysite.ai_agent import service, runner, actions, notify, config, policy
import mysite.ai_agent.actions as actions_mod
import mysite.ai_agent.team_notify as team_notify_mod
import mysite.views.messaging as messaging

# ---- capture every outward side effect -------------------------------------------------
# Telegram is faked at the lowest level (notify._post / notify._call), so every module that imported send_ai_chat,
# edit_reply_markup or answer_callback is captured too: telegram = the posted texts, markups = their buttons.
telegram, markups, sms, telegram_html = [], [], [], []   # telegram: as read (plain); telegram_html: as sent
os.environ["TELEGRAM_TOKEN"] = "fake-token"
os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from mysite.ai_agent.sandbox_test.world import html_to_plain as _plain_of   # an alert sent as Telegram HTML, as read
def fake_post(token, chat_id, text, reply_to=None, reply_markup=None, silent=False, parse_mode=None):
    telegram_html.append(text); telegram.append(_plain_of(text) if parse_mode == 'HTML' else text); markups.append(reply_markup); return True, "captured", 9000 + len(telegram)
notify._post = fake_post
telegram_calls = []   # editMessageReplyMarkup / answerCallbackQuery ...
notify._call = lambda method, data: (telegram_calls.append((method, data)), (True, ''))[1]
service.report_error = lambda e, ctx, info=None, source='task': telegram.append(f"ERROR {ctx}: {e}")
def fake_send(sid, author, message, sender, receiver): sms.append((sid, author, message))
messaging.send_messsage_by_sid = fake_send
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs"
config.WORK_DIR = config.RUNS_DIR / "_cwd"

script, inputs_seen = [], []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    inputs_seen.append(user_input)
    out = script.pop(0)
    (run_dir / "01_system_prompt.md").write_text(system_prompt)
    return {'ok': out is not None, 'error': None if out is not None else 'claude CLI timed out after 180s',
            'output': out, 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '', 'command': 'fake',
            'mcp_config': {}, 'model': 'fake-model', 'exit_code': 0, 'duration_ms': 5, 'timed_out': out is None}
runner.run_claude = fake_run_claude

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

# ---- fixtures (bulk_create: no model save() side effects such as DocuSeal) --------------
SID = "CHtest0001"
User.objects.bulk_create([User(email="t@example.com", full_name="Test Tenant", role="Tenant", phone="+15550001111")])
tenant = User.objects.get(email="t@example.com")
Apartment.objects.bulk_create([Apartment(name="630-999", building_n="630", apartment_n="999", street="Test St", state="FL",
    city="WPB", zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available",
    knowledge_base="WiFi: TestNet / pass123")])
apt = Apartment.objects.get(name="630-999")
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today()-timedelta(days=3),
    end_date=date.today()+timedelta(days=30), status="Confirmed")])
booking = Booking.objects.get(apartment=apt)
TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=SID, friendly_name="test", apartment=apt, booking=booking)])
conv = TwilioConversation.objects.get(conversation_sid=SID)
StaffMember.objects.bulk_create([StaffMember(ai_name="Edy", full_name="Farouk Ahmed", role="operations", phone="+15612205252"),
                                 StaffMember(ai_name="Janna", full_name="Janna", role="accounting", phone="+15618438867")])
n = [0]
def msg(author, body, direction='inbound'):
    n[0] += 1
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"IM{n[0]:04d}", conversation=conv, conversation_sid=SID,
        author=author, body=body, direction=direction)])
    return TwilioMessage.objects.get(message_sid=f"IM{n[0]:04d}")

def press(run, code, arg=''):
    """A button press under the run's simple alert, as Telegram delivers it (callback data 'v5|code|run|arg').
    Returns the notice the presser sees."""
    from mysite.ai_agent import approval
    before = len(telegram_calls)
    approval.handle_callback({'id': f'cb{run.id}{code}{arg}', 'data': f'v5|{code}|{run.id}|{arg}', 'from': {'first_name': 'Andy'},
                              'message': {'message_id': run.telegram_message_id, 'chat': {'id': -500}}})
    run.refresh_from_db()
    return next((d['text'] for m, d in telegram_calls[before:] if m == 'answerCallbackQuery'), None)

def plan_index(run, kind):
    return next(i for i, a in enumerate(run.review['plan']['actions']) if a.get('type') == kind)

def drain():
    """What the worker loop does, without waiting."""
    AIEvent.objects.filter(status='pending').update(created_at=timezone.now()-timedelta(minutes=5))
    runs = []
    while True:
        batch = service.claim_next_batch()
        if not batch: return runs
        runs.append(service.process_events(batch))

print("\n=== 1. tenant reports a problem (test mode) ===")
m1 = msg("+15550001111", "The kitchen sink is dripping")
check("enqueue returns True", service.enqueue_tenant_message(SID, m1.message_sid, m1.body))
check("same message is not queued twice", service.enqueue_tenant_message(SID, m1.message_sid, m1.body) and AIEvent.objects.count() == 1)
script.append({'answer': "Thanks for letting us know. I've logged the dripping kitchen sink and passed it to the team.", 'why': 'routine maintenance',
  'actions': [
    {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': 'recheck sink'},   # before CREATE_ISSUE on purpose
    {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Kitchen sink dripping', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
    {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'priority': 'routine', 'title': 'Kitchen sink dripping - 630-999', 'description': 'drips'},
    {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'routine', 'responsible': ['Edy'], 'text': '630-999 sink dripping'},
    {'type': 'KB_UPDATE', 'key': 'x', 'value': 'y'},
    {'type': 'DELETE_EVERYTHING'},
    {'type': 'UPDATE_ISSUE_STATE', 'issue_id': 'i-9999', 'state': 'RESOLVED'},
  ]})
run1 = drain()[0]
issue = AIIssue.objects.get()
st = {a['action'].get('type'): a['status'] for a in run1.actions}
check("issue created with state + owner", issue.state == 'MAINTENANCE_OPEN' and issue.owner == 'Edy' and issue.mode == 'test')
check("follow-up created although it came before CREATE_ISSUE", AIFollowUp.objects.filter(issue=issue, kind='staff_reminder', status='pending').count() == 1)
check("unknown action rejected", st.get('DELETE_EVERYTHING') == 'rejected')
check("foreign/unknown issue id rejected", st.get('UPDATE_ISSUE_STATE') == 'rejected')
# Simple alerts: a fact is only proposed (📚 block) from a team member's message - its source; a tenant's KB_UPDATE is dropped
check("KB_UPDATE from a tenant is not proposed and nothing is written", st.get('KB_UPDATE') == 'rejected'
      and 'X: y' not in (Apartment.objects.get(id=apt.id).knowledge_base or ''))
check("ONE simple alert for the run: TEST mark, staff name, ticket block, reminder, tenant text once, full report link",
      len(telegram) == 1 and telegram[0].startswith('📨 TENANT MESSAGE · 🧪 TEST · ') and 'Edy · routine' in telegram[0]
      and '🎫 Kitchen sink dripping - 630-999\n' in telegram[0] and '⏰ recheck sink' in telegram[0]
      and telegram[0].count('The kitchen sink is dripping') == 1 and telegram[0].endswith('\n🔗 AI run · CRM chat')
      and '<a href="http://crm.test/ai-runs/' in telegram_html[0], telegram)
run1.refresh_from_db()
st_detail = {a['action'].get('type'): (a['status'], a['detail']) for a in run1.actions}
check("the team note is done with the alert; the task waits for its button", 'Telegram: sent' in st_detail['INTERNAL_ALERT'][1]
      and st_detail['CREATE_TICKET'][0] == 'planned' and not issue.ticket_title, st_detail)
check("the alert has a Create Task button", any(b.get('callback_data') == f"v5|ct|{run1.id}|{plan_index(run1, 'CREATE_TICKET')}"
      for row in (markups[0] or {}).get('inline_keyboard', []) for b in row), markups[0])
notice = press(run1, 'ct', plan_index(run1, 'CREATE_TICKET'))
issue.refresh_from_db()
check("🎫 Create Task press: ticket title stored on issue (no ClickUp List here: noted)", issue.ticket_title == 'Kitchen sink dripping - 630-999'
      and run1.review['plan']['actions'][plan_index(run1, 'CREATE_TICKET')].get('done'), notice)
check("a second press only says who did it", (press(run1, 'ct', plan_index(run1, 'CREATE_TICKET')) or '').startswith('already done by Andy'))
m1.refresh_from_db()
check("answer stored on message, NOT sent (test mode)", m1.ai_response and m1.ai_sent_to_chat is False and not sms)
check("run report folder written", os.path.exists(os.path.join(run1.report_dir, 'report.md')) and os.path.exists(os.path.join(run1.report_dir, '07_actions.json')))

print("\n=== 2. follow-up becomes due -> AI is woken ===")
f1 = AIFollowUp.objects.get()
check("routine staff reminder is ~2h away (simple alerts) and inside staff hours", f1.due_at > timezone.now() + timedelta(minutes=115)
      and config.is_office_hours(f1.due_at), f1.due_at)
AIFollowUp.objects.filter(id=f1.id).update(due_at=timezone.now() - timedelta(minutes=1))
check("due follow-up fires one event", service.fire_due_followups() == 1 and service.fire_due_followups() == 0)
telegram.clear()
script.append({'answer': 'NO_ANSWER', 'why': 'no update from staff, remind Edy', 'next_action': 'Edy: check the sink is fixed',
  'actions': [{'type': 'INTERNAL_ALERT', 'issue_id': issue.public_id, 'priority': 'routine', 'responsible': ['Edy'], 'text': 'Reminder: sink still open'},
              {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': issue.public_id, 'kind': 'staff_reminder', 'reason': 'recheck again'}]})
run2 = drain()[0]
seen = inputs_seen[-1]
check("FOLLOWUP_DUE input shows event, the follow-up, open issue and ticket", all(x in seen for x in ("EVENT: FOLLOWUP_DUE", "followup_id: f-1", "issue_id: i-1", "ticket_id: t-1", "STAFF (authorized")), seen[:600])
check("reminder alert posted + the backend sets the 2/2 reminder (the AI's own SCHEDULE_FOLLOWUP is dropped)",
      len(telegram) == 1 and '⏰' in telegram[0] and AIFollowUp.objects.filter(status='pending').count() == 1
      and {a['action']['type']: a['status'] for a in run2.actions}.get('SCHEDULE_FOLLOWUP') == 'rejected', (telegram, run2.actions))
check("run is linked as FOLLOWUP_DUE, no message touched", run2.event_type == 'FOLLOWUP_DUE' and run2.message_id is None)

print("\n=== 3. staff says fixed -> AI asks tenant to confirm ===")
m2 = msg("+15612205252", "Plumber was there, sink is fixed now", direction='outbound')
check("staff message queued", service.enqueue_staff_message(SID, m2.message_sid, m2.body))
script.append({'answer': 'Just checking - is the sink working properly now?', 'why': 'staff reported fixed',
  'actions': [{'type': 'UPDATE_ISSUE_STATE', 'issue_id': 'i-1', 'state': 'WAITING_FOR_TENANT_CONFIRMATION'},
              {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'i-1', 'kind': 'tenant_nudge', 'reason': 'waiting for confirmation'},
              {'type': 'CASE_NOTE', 'issue_id': 'i-1', 'text': 'Plumber visited, reported fixed by Edy'}]})
run3 = drain()[0]
issue.refresh_from_db()
check("staff sender shown by name + role STAFF", "Edy (STAFF): Plumber was there" in inputs_seen[-1], inputs_seen[-1][-400:])
check("state moved, case note saved", issue.state == 'WAITING_FOR_TENANT_CONFIRMATION' and AICaseNote.objects.filter(issue=issue).count() == 1)
# Doc B4 (the same sink case as A1, after its team reminder 1/2): "⏰ Ask Vera to confirm the sink works (1/2, to tenant)"
check("team says fixed -> the tenant reminder is set although the case used its team reminders (doc B4)",
      AIFollowUp.objects.filter(kind='tenant_nudge', status='pending').exists(), run3.actions)
check("STAFF_MESSAGE run did not overwrite a tenant message's AI fields", run3.event_type == 'STAFF_MESSAGE' and TwilioMessage.objects.get(id=m2.id).ai_response is None)

print("\n=== 4. tenant confirms -> resolved, timers cancelled ===")
m3 = msg("+15550001111", "Yes all good now, thank you")
service.enqueue_tenant_message(SID, m3.message_sid, m3.body)
script.append({'answer': 'NO_ANSWER', 'why': 'confirmed fixed',
  'actions': [{'type': 'UPDATE_TICKET', 'ticket_id': 't-1', 'status': 'tenant_confirmed_fixed'},
              {'type': 'UPDATE_ISSUE_STATE', 'issue_id': 'i-1', 'state': 'RESOLVED'}]})
run4 = drain()[0]
issue.refresh_from_db()
check("issue RESOLVED with timestamp", issue.state == 'RESOLVED' and issue.resolved_at is not None)
check("all pending follow-ups cancelled", not AIFollowUp.objects.filter(status='pending').exists())
check("case notes appear in the next input", "CASE_NOTES" in inputs_seen[-1] and "Plumber visited" in inputs_seen[-1])
check("the ticket update waits for its button", not AICaseNote.objects.filter(issue=issue, text__contains='UPDATE_TICKET').exists())
press(run4, 'up', plan_index(run4, 'UPDATE_TICKET'))
check("🔄 Apply Update press: ticket update kept as a note on the issue", AICaseNote.objects.filter(issue=issue, text__contains='UPDATE_TICKET').exists())

print("\n=== 5. promise without action -> backend adds the alert ===")
telegram.clear()
m4 = msg("+15550001111", "My neighbour is very loud every night")
service.enqueue_tenant_message(SID, m4.message_sid, m4.body)
script.append({'answer': "Thank you. I've passed your message to the team.", 'why': 'x', 'actions': []})
run5 = drain()[0]
check("backend-added INTERNAL_ALERT executed", len(run5.actions) == 1 and run5.actions[0]['status'] == 'executed'
      and run5.actions[0]['action'].get('backend_added') and len(telegram) == 1, run5.actions)

print("\n=== 6. live mode: answer is really sent; staff-reply guard; burst = one run ===")
Apartment.objects.filter(id=apt.id).update(ai_group_chat_enabled=True)
m5 = msg("+15550001111", "what is the wifi")
m6 = msg("+15550001111", "password?")
service.enqueue_tenant_message(SID, m5.message_sid, m5.body); service.enqueue_tenant_message(SID, m6.message_sid, m6.body)
script.append({'answer': 'WiFi is TestNet, password pass123.', 'why': 'kb', 'actions': []})
runs = drain()
check("two quick messages -> ONE run, both shown as new", len(runs) == 1 and "what is the wifi" in inputs_seen[-1].split("NEW MESSAGE(S)")[1] and "password?" in inputs_seen[-1].split("NEW MESSAGE(S)")[1])
check("live answer waits for 🤖 Send Answer", not sms and runs[0].mode == 'live' and runs[0].hold_status == AIRun.HOLD_HOLDING)
press(runs[0], 'sa')
check("live answer sent once as Virtual Assistant", sms == [(SID, 'Virtual Assistant', 'WiFi is TestNet, password pass123.')] and runs[0].sent_to_chat, (sms, runs[0].delivery_note))
sms.clear()
m7 = msg("+15550001111", "where do I park?")
service.enqueue_tenant_message(SID, m7.message_sid, m7.body)
msg("+15612205252", "Spot 12, right side", direction='outbound')     # staff answers while the AI is "thinking"
script.append({'answer': 'Parking spot 12.', 'why': 'kb', 'actions': []})
run7 = drain()[0]
check("AI answer suppressed because staff replied first", not sms and not run7.sent_to_chat and 'staff replied' in (run7.delivery_note or ''))
m8 = msg("+15550001111", "ok can you check the AC")
service.enqueue_tenant_message(SID, m8.message_sid, m8.body, send_allowed=False, source='chat_ui')
script.append({'answer': 'Which room is the AC in?', 'why': 'clarify', 'actions': []})
run8 = drain()[0]
check("'Send to group chat' unticked -> test mode even on a live apartment", run8.mode == 'test' and not sms)

print("\n=== 7. failures and limits ===")
telegram.clear()
m9 = msg("+15550001111", "hello anyone there?")
service.enqueue_tenant_message(SID, m9.message_sid, m9.body)
script.append(None)                                   # Claude times out
run9 = drain()[0]
check("failed run: event failed, error kept, human alerted with the message", AIEvent.objects.get(message=m9).status == 'failed'
      and 'timed out' in (run9.error or '') and len(telegram) == 1 and 'needs a human' in telegram[0], telegram)
check("emergency skips the 1-minute wait; normal waits 60s; follow-up 0s",
      service._batch_wait_seconds([AIEvent(event_type='TENANT_MESSAGE', body='there is smoke in the kitchen', payload={})]) == 0
      and service._batch_wait_seconds([AIEvent(event_type='TENANT_MESSAGE', body='wifi?', payload={'source': 'webhook'})]) == 60
      and service._batch_wait_seconds([AIEvent(event_type='FOLLOWUP_DUE', body='x', payload={})]) == 0)
big = AIIssue.objects.create(conversation_sid=SID, summary='loop test', state='WAITING_FOR_EDY')
ctx = actions.ActionContext('test', {'apartment': '630-999', 'tenant': 'T'}, 'm', SID, apartment=apt, booking=booking)
for i in range(12):
    AIFollowUp.objects.filter(issue=big, status='pending').update(status='fired')
    res = actions.execute_actions({'answer': None, 'actions': [{'type': 'SCHEDULE_FOLLOWUP', 'issue_id': big.public_id, 'kind': 'staff_reminder', 'reason': 'r'}]}, ctx)
check("follow-up loop is capped per issue (2 reminders per case)", res[0]['status'] == 'rejected'
      and big.followups.count() == policy.MAX_REMINDERS_PER_CASE_V5, res)
other = AIIssue.objects.create(conversation_sid="CHother", summary='other tenant issue', state='WAITING_FOR_EDY')
res = actions.execute_actions({'answer': None, 'actions': [{'type': 'UPDATE_ISSUE_STATE', 'issue_id': other.public_id, 'state': 'RESOLVED'}]}, ctx)
other.refresh_from_db()
check("cannot touch another conversation's issue", res[0]['status'] == 'rejected' and other.state == 'WAITING_FOR_EDY')
res = actions.execute_actions({'answer': None, 'actions': [{'type': 'CREATE_ISSUE', 'summary': 'replay issue', 'state': 'WAITING_FOR_EDY'}]},
                              actions.ActionContext('test', {}, 'm', SID, persist=False, notify=False))
check("replay saves nothing", res[0]['status'] == 'simulated' and not AIIssue.objects.filter(summary='replay issue').exists())
print("\n=== 8. reminder due -> look at the ClickUp task first ===")
from mysite.ai_agent import clickup
clickup.delivery_mode = lambda: 'api'
task_state = {}
clickup.get_task_state = lambda ref: dict(task_state) if 'error' not in task_state else (_ for _ in ()).throw(clickup.ClickUpError(task_state['error']))
Apartment.objects.filter(id=apt.id).update(ai_group_chat_enabled=True)
def due_issue(summary):
    issue = AIIssue.objects.create(conversation_sid=SID, apartment=apt, booking=booking, summary=summary, state='MAINTENANCE_OPEN', owner='Edy',
                                   ticket_title=summary, ticket_ref='https://app.clickup.com/t/abc999')
    AIFollowUp.objects.create(conversation_sid=SID, issue=issue, kind='staff_reminder', reason='recheck', due_at=timezone.now() - timedelta(minutes=1))
    AIFollowUp.objects.create(conversation_sid=SID, issue=issue, kind='tenant_nudge', reason='photo', due_at=timezone.now() + timedelta(hours=5))
    service.fire_due_followups(); return issue
telegram.clear(); sms.clear()
task_state.update(status='complete', closed=True, assignees=['Farouk Ahmed'], updated='2026-09-21 10:00', comments=[])
closed = due_issue('Dishwasher broken')
script.append({'answer': 'The team marked the dishwasher as done. If you still have any problem, just let us know.', 'why': 'ClickUp task closed',
               'next_action': 'confirm with the tenant', 'actions': [{'type': 'UPDATE_ISSUE_STATE', 'issue_id': closed.public_id, 'state': 'RESOLVED'}]})
run = drain()[0]; seen = inputs_seen[-1]; closed.refresh_from_db()
check("task CLOSED in ClickUp: its other reminders stop, Claude is told to propose the 'team marked it done' message",
      not closed.followups.filter(status='pending').exists() and 'ClickUp task CLOSED' in seen and 'PROPOSE' in seen, seen[-1500:])
check("... the tenant gets nothing until a press: ONE reminder alert with the answer, the case is resolved",
      not sms and run.hold_status == AIRun.HOLD_HOLDING and len(telegram) == 1 and 'marked the dishwasher as done' in telegram[0]
      and closed.state == 'RESOLVED', (telegram, closed.state))
telegram.clear(); task_state.clear()
task_state.update(status='in progress', closed=False, assignees=['Farouk Ahmed'], updated='2026-09-21 10:05',
                  comments=[{'when': '2026-09-21 10:04', 'user': 'Farouk Ahmed', 'text': 'Plumber booked for 3 PM today'}])
opened = due_issue('Toilet keeps running')
script.append({'answer': 'NO_ANSWER', 'why': 'staff commented progress in ClickUp, no reminder needed', 'actions': [
    {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': opened.public_id, 'kind': 'staff_reminder', 'reason': 'recheck after the 3 PM visit'}]})
run = drain()[0]; seen = inputs_seen[-1]
check("task OPEN: Claude gets status, assignee and the staff comment, marked internal", 'CLICKUP_TASKS' in seen and 'ClickUp status: in progress' in seen
      and 'Plumber booked for 3 PM today' in seen and 'never quote this to the tenant' in seen and run.event_type == 'FOLLOWUP_DUE', seen[:900])
opened.refresh_from_db(); check("... and the issue stays open", opened.state == 'MAINTENANCE_OPEN')
task_state.clear(); task_state['error'] = 'ClickUp not reachable'
broken = due_issue('Window does not close')
script.append({'answer': 'NO_ANSWER', 'why': 'x', 'actions': []})
run = drain()[0]
check("ClickUp unreachable: the reminder still runs, Claude is told the task could not be read", run is not None and 'could not be read' in inputs_seen[-1])
Apartment.objects.filter(id=apt.id).update(ai_group_chat_enabled=False)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
