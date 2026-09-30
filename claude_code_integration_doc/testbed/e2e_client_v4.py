"""
Client spec v4 (plan: claude_code_integration_doc/client_spec_v4_plan.md): explicit approval with Telegram buttons,
checkboxes, drafts and notes, "I'll handle", stale proposals, the after-hours auto-message with its 5-hour cooldown,
URGENT / emergency phone calls, tenant deadlines, closed ClickUp tasks, failed-send retry, office hours and holidays.
Includes the client's test sequences.
"""
import os, sys, django
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
os.environ["AI_AGENT_REVIEW_HOLD_MINUTES"] = "15"   # review on; explicit approval is the default
os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

from django.utils import timezone
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement, AIEvent, AIRun,
                           AIIssue, AIFollowUp, AICaseNote, StaffMember, AIAfterHoursAck, AIAlertCall)
from mysite.ai_agent import (service, runner, config, answer_review, clickup, approval, after_hours, calls, cases,
                             notify, policy)
import mysite.ai_agent.team_notify as team_notify
import mysite.views.messaging as messaging
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_v4"
config.WORK_DIR = config.RUNS_DIR / "_cwd"
config.is_within_notification_window = lambda now=None: True
OFFICE = [True]
real_is_office_hours = config.is_office_hours
config.is_office_hours = lambda now=None: OFFICE[0]

# ---- fakes ---------------------------------------------------------------------------------
telegram, sms, markups, callbacks, tg_ids = [], [], [], [], [700000]   # ids unlike the other test files' (shared DB)
def fake_tg(text, reply_to=None, reply_markup=None):
    tg_ids[0] += 1
    telegram.append({'id': tg_ids[0], 'text': text, 'reply_to': reply_to, 'markup': reply_markup})
    return True, "sent", tg_ids[0]
for module in (team_notify, answer_review, notify, calls):
    module.send_ai_chat = fake_tg
approval.edit_reply_markup = lambda message_id, markup=None: markups.append((message_id, markup))
approval.answer_callback = lambda cid, text='', alert=False: callbacks.append((cid, text, alert))
errors = []
answer_review.report_error = service.report_error = after_hours.report_error = calls.report_error = \
    lambda e, ctx, info=None, source='task': errors.append(f"{ctx}: {e}")
sms_fail = [0]
def fake_sms(sid, author, message, sender, receiver):
    if sms_fail[0]:
        sms_fail[0] -= 1
        raise Exception("twilio down")
    sms.append((sid, message))
messaging.send_messsage_by_sid = fake_sms
dialled, call_status = [], ['completed']
class _Call:
    def __init__(self, sid): self.sid, self.status = sid, call_status[0]
    def fetch(self): return self
class _Calls:
    def create(self, to, from_, twiml, timeout): dialled.append(to); return _Call(f"CA{len(dialled)}")
    def __call__(self, sid): return _Call(sid)
class _Twilio: calls = _Calls()
messaging.get_twilio_client = lambda: _Twilio()
updates, interpreter_out = [], []
answer_review.fetch_updates = lambda: [updates.pop(0) for _ in range(len(updates))]
answer_review.run_interpreter = lambda prompt: interpreter_out.pop(0)
cu, task_closed = [], [False]
class _Map: list_id, channel_id, name = 'L1', None, 'list 720-201'
clickup.delivery_mode = lambda: 'api'
clickup.channel_for = lambda apartment: _Map()
clickup.post_message = lambda *a, **k: None
def fake_create(list_id, name, description, priority='routine', assignee_ids=None, due_at=None, tags=None):
    cu.append(('create', name, priority)); return f"tk{len(cu)}", f"https://app.clickup.com/t/tk{len(cu)}"
clickup.create_task = fake_create
clickup.set_task_closed = lambda ref, closed=True: (cu.append(('close', ref)), 'complete')[1]
clickup.add_task_comment = lambda ref, text: cu.append(('comment', ref, text))
clickup.get_task_state = lambda ref: {'status': 'complete' if task_closed[0] else 'to do', 'closed': task_closed[0],
                                      'assignees': ['Edy'], 'updated': 'x', 'due': None, 'url': ref, 'comments': []}

script, inputs_seen = [], []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    inputs_seen.append(user_input)
    return {'ok': True, 'error': None, 'output': script.pop(0), 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 5, 'timed_out': False}
runner.run_claude = fake_run_claude

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {str(extra)[:600]}" if extra and not cond else ''))

# ---- fixtures -------------------------------------------------------------------------------
TENANT_PHONE = "+15550004455"
if not User.objects.filter(email="v4@example.com").exists():
    User.objects.bulk_create([User(email="v4@example.com", full_name="Vera Tenant", role="Tenant", phone=TENANT_PHONE)])
tenant = User.objects.get(email="v4@example.com")
def make_apartment(name, live):
    Apartment.objects.bulk_create([Apartment(name=name, building_n="720", apartment_n=name[-3:], street="S", state="FL", city="WPB",
        zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_group_chat_enabled=live)])
    apt = Apartment.objects.get(name=name)
    Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=2),
                                         end_date=date.today() + timedelta(days=20), status="Confirmed")])
    sid = f"CHv4{name}"
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=sid, friendly_name=name, apartment=apt,
                                                               booking=Booking.objects.get(apartment=apt))])
    return apt, sid
apt, SID = make_apartment("720-201", live=True)
apt_test, SID_TEST = make_apartment("720-202", live=False)
AIManagement.objects.filter(prompt_key='ai_clickup_writes').delete()   # an earlier test file may have left it off
AIManagement.objects.update_or_create(prompt_key="ai_backend", defaults={'name': "backend", 'entry_type': "ai_model", 'content': "claude_cli"})
for name, role, phone, second in (("Edy", "operations", "+15612220001", None), ("Farid", "owner", "+15614603904", "+15612205252"),
                                  ("Kevin", "supervisor", None, None)):
    if not StaffMember.objects.filter(ai_name=name).exists():
        StaffMember.objects.bulk_create([StaffMember(ai_name=name, full_name=name, role=role, phone=phone, secondary_phone=second)])
StaffMember.objects.filter(ai_name="Farid").update(phone="+15614603904", secondary_phone="+15612205252")
EDY_PHONE = StaffMember.objects.get(ai_name="Edy").phone
n = [0]
def say(sid, body, author=TENANT_PHONE, enqueue=True):
    n[0] += 1
    conv = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"V4{n[0]:04d}", conversation=conv, conversation_sid=sid,
                                                     author=author, body=body, direction='inbound')])
    if enqueue and author == TENANT_PHONE:
        service.enqueue_tenant_message(sid, f"V4{n[0]:04d}", body)
    elif enqueue:
        service.enqueue_staff_message(sid, f"V4{n[0]:04d}", body)
    return TwilioMessage.objects.get(message_sid=f"V4{n[0]:04d}")
def drain():
    after_hours.process_pending()
    AIEvent.objects.filter(status='pending').update(created_at=timezone.now() - timedelta(minutes=5))
    runs = []
    while True:
        batch = service.claim_next_batch()
        if not batch: return runs
        runs.append(service.process_events(batch))
def run_with(sid, body, output):
    say(sid, body); script.append(output); r = drain()[-1]; r.refresh_from_db(); return r
def triage(**fields):
    base = {'primary_type': 'ROUTINE_MAINTENANCE', 'secondary_types': [], 'priority': 'routine', 'case_status': 'ACKNOWLEDGED',
            'issue_refs': ['new-1'], 'owner': 'Edy', 'next_action': 'Edy: send the plumber', 'tenant_deadline': '',
            'verified_facts': [], 'uncertainties': [], 'no_reply_reason': ''}
    base.update(fields); return base
def sink(title="Kitchen sink dripping", **tri):
    return dict(triage(**tri), answer="Thanks, I've logged the dripping sink with the team.", why='maintenance', actions=[
        {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': title, 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
        {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'priority': 'routine', 'title': title, 'description': 'drips'},
        {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'routine', 'responsible': ['Edy'], 'text': 'sink dripping'},
        {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': 'recheck'}])
def card_of(run):
    return next(t for t in telegram if t['id'] == run.telegram_message_id)
def buttons(markup):
    return [b for row in (markup or {}).get('inline_keyboard', []) for b in row]
def press(run, code, arg='', who="Kevin", version=None, message_id=None):
    run.refresh_from_db()
    v = (run.review or {}).get('version', 1) if version is None else version
    updates.append({'update_id': 100 + len(checks) + len(telegram), 'callback_query': {
        'id': f"cb{len(callbacks)}", 'from': {'first_name': who}, 'data': f"v4|{code}|{run.id}|{v}|{arg}",
        'message': {'message_id': message_id or run.telegram_message_id, 'chat': {'id': -500}}}})
    answer_review.poll_telegram(); run.refresh_from_db()
    return callbacks[-1][1] if callbacks else ''
def reply_to_message(run, message_id, text, who="Kevin", **decision):
    if decision:
        interpreter_out.append(dict(answer_review.EMPTY_DECISION, **decision))
    updates.append({'update_id': 5000 + len(checks) + len(telegram), 'message': {
        'message_id': 9000 + len(checks) + len(telegram), 'chat': {'id': -500}, 'from': {'first_name': who}, 'text': text,
        'date': int(timezone.now().timestamp()), 'reply_to_message': {'message_id': message_id}}})
    answer_review.poll_telegram(); run.refresh_from_db()
    return telegram[-1]['text']

# ---- 1. office hours and US federal holidays -------------------------------------------------
ET = ZoneInfo('America/New_York')
h26 = config.us_federal_holidays(2026)
check("2026 holidays: July 4 (Sat) observed Fri Jul 3, Thanksgiving Nov 26, MLK Jan 19, Memorial May 25",
      date(2026, 7, 3) in h26 and date(2026, 11, 26) in h26 and date(2026, 1, 19) in h26 and date(2026, 5, 25) in h26, h26)
check("office hours: Wed 09:30 ET yes, Wed 18:05 no, Sat 11:00 no, Thanksgiving 11:00 no",
      real_is_office_hours(datetime(2026, 9, 30, 9, 30, tzinfo=ET)) and not real_is_office_hours(datetime(2026, 9, 30, 18, 5, tzinfo=ET))
      and not real_is_office_hours(datetime(2026, 10, 3, 11, 0, tzinfo=ET))
      and not real_is_office_hours(datetime(2026, 11, 26, 11, 0, tzinfo=ET)))
due, _ = policy.due_at('staff_reminder', 'routine', now=datetime(2026, 9, 30, 20, 0, tzinfo=ET) - timedelta(hours=24))
check("routine staff reminders now start at 09:00 (was 10:00)", due.astimezone(ET).hour == 9, due)

# ---- 2. explicit approval: card with buttons, nothing happens by itself -----------------------------------
r1 = run_with(SID, "The kitchen sink is dripping", sink())
card = card_of(r1)
labels = [b['text'] for b in buttons(card['markup'])]
check("the card uses the client layout (type, owner, TENANT SAID, AI PROPOSES TO SEND, INTERNAL ACTION, Next step)",
      all(x in card['text'] for x in ("TYPE 2 Routine maintenance", "Owner: Edy", "TENANT SAID", "AI PROPOSES TO SEND",
                                       "INTERNAL ACTION", "Next step: Edy", "No decision = nothing is sent")), card['text'])
check("buttons: Approve all / Replace / Don't send / I'll handle / Stop all + one checkbox per plan item",
      labels[:5] == ['✅ Approve all', '✏️ Replace', "❌ Don't send", "👤 I'll handle", '🛑 Stop all']
      and sum(1 for l in labels if l.startswith('☑')) == 3, labels)
check("the answer waits with no timer", r1.hold_status == AIRun.HOLD_HOLDING and r1.hold_until is None, r1.hold_until)
AIRun.objects.filter(id=r1.id).update(created_at=timezone.now() - timedelta(hours=5))
answer_review.release_due()
r1.refresh_from_db()
check("hours later still NOTHING sent or done (no decision = nothing happens)",
      not sms and not cu and r1.hold_status == AIRun.HOLD_HOLDING and not AIIssue.objects.filter(conversation_sid=SID).exists())
check("the AI's triage is stored on the run", r1.triage.get('primary_type') == 'ROUTINE_MAINTENANCE' and r1.triage.get('owner') == 'Edy', r1.triage)

# ---- 3. checkbox + approve all + note --------------------------------------------------------------
t = press(r1, 't', 3)   # untick the reminder
r1.refresh_from_db()
check("unticking item 3 marks it removed, bumps the version and updates the buttons",
      r1.review['version'] == 2 and 'Unticked 3' in t and any(b['text'].startswith('☐ 3.') for b in buttons(markups[-1][1])), (t, markups[-1:]))
old_version_press = press(r1, 'a', version=1)
check("a press on the outdated keyboard is refused", 'changed meanwhile' in old_version_press and not sms, old_version_press)
press(r1, 'a')
issue1 = AIIssue.objects.filter(conversation_sid=SID).first()
check("Approve all: the answer is sent and the ticked items are done (issue + ClickUp task), the unticked reminder is not",
      len(sms) == 1 and issue1 and any(c[0] == 'create' for c in cu) and not AIFollowUp.objects.filter(issue=issue1).exists()
      and r1.hold_status == AIRun.HOLD_SENT, (sms, cu))
check("the buttons are removed and the decision is logged with the name",
      markups[-1] == (r1.telegram_message_id, None) and r1.review['decisions'][-1]['by'] == 'Kevin', markups[-1:])
check("the issue got the card as its thread, the next action, and reached 'acknowledged'",
      issue1.telegram_thread_message_id == r1.telegram_message_id and issue1.next_action == 'Edy: send the plumber'
      and issue1.stage == 'acknowledged', (issue1.telegram_thread_message_id, issue1.next_action, issue1.stage))
note_prompt = r1.review['note_prompts'][-1]
nothing = press(r1, 'a')
check("pressing Approve again on a decided card: nothing waits any more", 'Nothing waits' in nothing, nothing)
reply_to_message(r1, note_prompt, "Plumber can only come after 5pm")
check("a reply to the note request is saved as a case note on the issue",
      AICaseNote.objects.filter(issue=issue1, text__contains="after 5pm").exists() and "Note saved" in telegram[-1]['text'])

# ---- 4. repeat question: same issue, card in the issue thread ------------------------------------------
r2 = run_with(SID, "Any news about the sink??", dict(triage(issue_refs=[issue1.public_id], case_status='ACKNOWLEDGED'),
              answer="It is still being checked.", why='asked again', actions=[]))
card2 = card_of(r2)
issue1.refresh_from_db()
check("a follow-up about the same issue: posted in the issue's thread, tenant asked 2 times shown on the card",
      card2['reply_to'] == issue1.telegram_thread_message_id and issue1.tenant_asks == 2 and "tenant asked 2 times" in card2['text'],
      (card2['reply_to'], issue1.tenant_asks, card2['text']))

# ---- 5. Replace -> draft -> send, and typed replies still work ------------------------------------------
press(r2, 'r')
prompt_id = r2.review['replace_prompts'][-1]
check("Replace asks for the text with a force-reply message", telegram[-1]['markup'].get('force_reply'), telegram[-1])
reply_to_message(r2, prompt_id, "The plumber comes tomorrow after 5pm.")
r2.refresh_from_db()
draft = r2.review['draft']
check("the reply becomes a DRAFT card with Send this / Discard, NOT sent", draft['status'] == 'waiting' and len(sms) == 1
      and [b['text'] for b in buttons(next(t for t in telegram if t['id'] == draft['message_id'])['markup'])] == ['✅ Send this', '❌ Discard draft'])
press(r2, 'd', message_id=draft['message_id'])
check("Send this: the manager's text goes out instead of the AI answer",
      sms[-1][1] == "The plumber comes tomorrow after 5pm." and r2.hold_status == AIRun.HOLD_CORRECTED, sms[-1:])

r3 = run_with(SID, "And the bathroom light is out", dict(triage(issue_refs=[]), answer="Thanks, noted.", why='x', actions=[]))
t = reply_to_message(r3, r3.telegram_message_id, "say we will replace the bulb today", decision='replace',
                     corrected_answer="We'll replace the bulb today.")
r3.refresh_from_db()
check("a typed correction also becomes a draft (not sent at once)", r3.review.get('draft', {}).get('status') == 'waiting'
      and sms[-1][1] != "We'll replace the bulb today." and "DRAFT" in t, t)
t = reply_to_message(r3, r3.telegram_message_id, "why did you say that?", decision='question', staff_answer="Because it was reported.")
check("a typed question is answered in the thread, nothing changes", "Because it was reported." in t
      and r3.hold_status == AIRun.HOLD_HOLDING, t)
t = reply_to_message(r3, r3.telegram_message_id, "ok")
check("typed \"ok\" still approves (sends the AI answer)", sms[-1][1] == "Thanks, noted." and 'Sent the AI answer' in t, t)

# ---- 6. stale proposals: a new tenant message replaces the waiting card ----------------------------------
r4 = run_with(SID, "Also the AC is noisy", sink("AC noisy"))
say(SID, "Actually the AC stopped completely", enqueue=False)
refused = press(r4, 'a')
r4.refresh_from_db()
check("final recheck: the tenant wrote again after the proposal -> Approve is refused, nothing sent",
      'wrote again' in refused and r4.hold_status == AIRun.HOLD_HOLDING and sms[-1][1] == "Thanks, noted.", refused)
service.enqueue_tenant_message(SID, f"V4{n[0]:04d}", "Actually the AC stopped completely")
script.append(sink("AC stopped completely", priority='urgent'))
r5 = drain()[-1]; r5.refresh_from_db(); r4.refresh_from_db()
check("the new run was told the older proposal is replaced and must be repeated (PENDING_PROPOSAL)",
      "PENDING_PROPOSAL" in inputs_seen[-1] and "AC noisy" in inputs_seen[-1])
check("the older card is STALE: answer superseded, plan superseded, buttons removed",
      r4.hold_status == AIRun.HOLD_SUPERSEDED and r4.review['plan']['status'] == 'superseded' and r4.review.get('stale') == r5.id
      and (r4.telegram_message_id, None) in markups)
stale = press(r4, 'a')
check("a press on the stale card is refused", 'Replaced by a newer proposal' in stale, stale)
reply_to_message(r4, r4.telegram_message_id, "is this urgent?", decision='question', staff_answer="Yes, the AC stopped.")
r5.refresh_from_db()
check("a typed reply to the stale card goes to the proposal that replaced it",
      (r5.review.get('replies') or [{}])[-1].get('text') == "is this urgent?", r5.review.get('replies'))

# ---- 7. I'll handle -> AI stays out -> give back -------------------------------------------------------------
press(r5, 'h', who="Edy")
ac_issue = AIIssue.objects.filter(conversation_sid=SID, summary="AC stopped completely").first()
check("I'll handle: answer not sent, only the new issue is opened, marked handled; no ClickUp task, no reminder",
      ac_issue and ac_issue.handled_by == 'Edy' and ac_issue.state == 'STAFF_HANDLING' and r5.hold_status == AIRun.HOLD_CANCELLED
      and not AIFollowUp.objects.filter(issue=ac_issue).exists() and not any('AC stopped' in str(c) for c in cu),
      (ac_issue and ac_issue.handled_by, r5.hold_status, cu))
give_back_btn = buttons(telegram[-1]['markup'])
check("the confirmation has a Give back to AI button", give_back_btn and 'Give' in give_back_btn[0]['text'], telegram[-1])
sms_before = len(sms)
r6 = run_with(SID, "Is someone coming for the AC?", dict(triage(issue_refs=[ac_issue.public_id], priority='urgent'),
              answer="Edy will come at 3pm.", why='x', actions=[
                  {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': ac_issue.public_id, 'kind': 'staff_reminder', 'reason': 'check AC'}]))
check("a new message only about the handled issue: no answer proposed, actions dropped, info card with Give back",
      not r6.answer and r6.review_answer == "Edy will come at 3pm." and not AIFollowUp.objects.filter(issue=ac_issue).exists()
      and "handled by staff" in card_of(r6)['text'] and 'Give' in buttons(card_of(r6)['markup'])[0]['text'] and len(sms) == sms_before,
      card_of(r6)['text'])
press(r6, 'g', ac_issue.id)
ac_issue.refresh_from_db()
check("Give back: the AI handles the issue again (previous state restored)", not ac_issue.handled_by and ac_issue.state == 'MAINTENANCE_OPEN',
      (ac_issue.handled_by, ac_issue.state))

# ---- 8. tenant deadline -> reminders 24 h and 2 h before ---------------------------------------------------------
deadline = (timezone.now() + timedelta(days=3)).astimezone(ET).strftime('%Y-%m-%d %H:%M')
r7 = run_with(SID, "I arrive Friday, the bed must be delivered before", dict(
    triage(primary_type='TIME_SENSITIVE_LOGISTICS', tenant_deadline=deadline, next_action='Edy: schedule bed delivery'),
    answer="Thanks, we'll confirm the delivery time.", why='x',
    actions=[{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Bed delivery before arrival', 'owner': 'Edy', 'state': 'WAITING_FOR_EDY'}]))
check("the card shows the tenant deadline as Reply due", "Reply due: " in card_of(r7)['text'] and "no fixed deadline" not in card_of(r7)['text'])
press(r7, 'a')
bed = AIIssue.objects.get(summary='Bed delivery before arrival')
reminders = list(AIFollowUp.objects.filter(issue=bed, kind='deadline_reminder').order_by('due_at'))
check("after approval: the issue has the deadline and two reminders (24 h and 2 h before; Kevin at 2 h)",
      bed.tenant_deadline and len(reminders) == 2 and '[24h]' in reminders[0].reason and 'Kevin' in reminders[1].reason,
      [(f.due_at, f.reason) for f in reminders])

# ---- 9. closed ClickUp task -> propose telling the tenant (waits for approval) ------------------------------------
AIFollowUp.objects.create(conversation_sid=SID, issue=issue1, kind='staff_reminder', reason='recheck sink',
                          due_at=timezone.now() - timedelta(minutes=1))
task_closed[0] = True
script.append(dict(triage(issue_refs=[issue1.public_id], case_status='ANSWERED'),
                   answer="Our team marked the kitchen sink as fixed. If you still have any problem, just let us know.",
                   why='task closed', actions=[{'type': 'UPDATE_ISSUE_STATE', 'issue_id': issue1.public_id, 'state': 'RESOLVED'}]))
service.fire_due_followups(); r8 = drain()[-1]; r8.refresh_from_db(); issue1.refresh_from_db()
task_closed[0] = False
check("closed task: Claude is told to propose the 'marked as done' message; nothing sent, issue still open until approved",
      "ClickUp task CLOSED" in inputs_seen[-1] and r8.hold_status == AIRun.HOLD_HOLDING and issue1.is_open, inputs_seen[-1][-800:])
press(r8, 'a')
issue1.refresh_from_db()
check("approved: message sent and the issue resolved (stage resolved)", sms[-1][1].startswith("Our team marked") and not issue1.is_open
      and issue1.stage == 'resolved', (issue1.state, issue1.stage))

# ---- 10. after-hours auto-message (client sequences) ------------------------------------------------------------
OFFICE[0] = False
sms_start = len(sms)
r9 = run_with(SID, "The dishwasher is leaking a bit", sink("Dishwasher leaking"))
ack = AIAfterHoursAck.objects.order_by('-id').first()
check("after hours (live): the fixed message went out at once, before the AI answer, which still waits",
      ack.status == 'sent' and sms[sms_start][1].startswith("Automated message: We received your message outside")
      and "reply URGENT" in sms[sms_start][1] and len(sms) == sms_start + 1 and r9.hold_status == AIRun.HOLD_HOLDING, sms[sms_start:])
check("the card shows it and the AI was told (AFTER_HOURS_ACK)", "After-hours message: SENT" in card_of(r9)['text']
      and "AFTER_HOURS_ACK" in inputs_seen[-1], card_of(r9)['text'])
say(SID, "Also the TV remote is missing"); say(SID, "And a light bulb"); after_hours.process_pending()
acks = list(AIAfterHoursAck.objects.order_by('-id')[:2])
check("three messages within 5 hours: only one automatic message", all(a.status == 'suppressed' for a in acks)
      and len(sms) == sms_start + 1 and 'at most once per 5 hours' in acks[0].reason, [(a.status, a.reason) for a in acks])
AIAfterHoursAck.objects.filter(status='sent').update(sent_at=timezone.now() - timedelta(hours=5, minutes=5))
say(SID, "Hello? still waiting"); after_hours.process_pending()
check("a new message after 5 hours: the automatic message goes out again", AIAfterHoursAck.objects.order_by('-id').first().status == 'sent'
      and len(sms) == sms_start + 2)
say(SID, "URGENT the fridge stopped working"); after_hours.process_pending()
urgent_ack = AIAfterHoursAck.objects.order_by('-id').first()
call = AIAlertCall.objects.order_by('-id').first()
check("URGENT during the cooldown: no second auto-message, but Farid is phoned at once (live)",
      urgent_ack.status == 'suppressed' and call and call.status == 'calling' and dialled == ['+15614603904'], (urgent_ack.status, call, dialled))
call_status[0] = 'no-answer'
AIAlertCall.objects.filter(id=call.id).update(check_at=timezone.now() - timedelta(seconds=1)); calls.check_calls()
call.refresh_from_db()
check("no answer on the first number -> his second number is dialled", dialled == ['+15614603904', '+15612205252'] and call.phone_index == 1, dialled)
AIAlertCall.objects.filter(id=call.id).update(check_at=timezone.now() - timedelta(seconds=1)); calls.check_calls()
call.refresh_from_db()
check("no answer on either -> Telegram is told", call.status == 'unanswered' and 'did NOT answer' in telegram[-1]['text'], telegram[-1])
call_status[0] = 'completed'
script += [sink("Many small things")] * 1
urgent_runs = drain()
urgent_card = card_of(urgent_runs[-1])
check("the burst with the URGENT reply is one run; its card says URGENT, shows the call, and is 🔴 at least",
      len(urgent_runs) == 1 and "The tenant wrote URGENT" in urgent_card['text'] and "📞" in urgent_card['text']
      and urgent_card['text'].startswith('🔴'), urgent_card['text'][:400])
class _E: event_type, body, payload = 'TENANT_MESSAGE', 'this is URGENT please', {}
class _N: event_type, body, payload = 'TENANT_MESSAGE', 'not urgent, when you can', {}
check("URGENT skips the 1-minute burst wait; 'not urgent' does not", service._batch_wait_seconds([_E()]) == 0
      and service._batch_wait_seconds([_N()]) == config.debounce_seconds())
say(SID, "thanks!"); after_hours.process_pending()
check("a pure thanks gets no automatic message", AIAfterHoursAck.objects.order_by('-id').first().reason.startswith('only a thanks'))
AIEvent.objects.filter(status='pending').update(status='done')

# both chats belong to the same tenant: they share the 5-hour window and the call cooldown - start clean
AIAfterHoursAck.objects.update(sent_at=timezone.now() - timedelta(hours=6))
AIAlertCall.objects.update(created_at=timezone.now() - timedelta(hours=1))
sms_before = len(sms)
say(SID_TEST, "Test apartment: fridge is warm"); after_hours.process_pending()
test_ack = AIAfterHoursAck.objects.order_by('-id').first()
check("test apartment: WOULD_SEND recorded, nothing sent", test_ack.status == 'would_send' and len(sms) == sms_before)
say(SID_TEST, "URGENT fridge"); after_hours.process_pending()
check("test apartment URGENT: the call is only simulated", AIAlertCall.objects.order_by('-id').first().status == 'simulated' and len(dialled) == 2)
AIEvent.objects.filter(status='pending').update(status='done')

AIAfterHoursAck.objects.filter(conversation_sid=SID).update(sent_at=timezone.now() - timedelta(hours=6))
say(SID, "Edy here, I'm on it", author=EDY_PHONE, enqueue=False)
say(SID, "ok when?"); after_hours.process_pending()
check("staff are replying right now: no automatic message", AIAfterHoursAck.objects.order_by('-id').first().reason.startswith('staff are replying'))
AIEvent.objects.filter(status='pending').update(status='done')

TwilioMessage.objects.filter(conversation_sid=SID, author=EDY_PHONE).delete()
sms_fail[0] = 1
say(SID, "Door lock stuck"); after_hours.process_pending()
failed = AIAfterHoursAck.objects.order_by('-id').first()
check("failed auto-message: FAILED, alerted, retry in 3 minutes", failed.status == 'failed' and failed.retry_at and
      any('NOT delivered' in e for e in errors), (failed.status, errors[-1:]))
AIAfterHoursAck.objects.filter(id=failed.id).update(retry_at=timezone.now() - timedelta(seconds=1))
after_hours.process_pending(); failed.refresh_from_db()
check("the retry works: sent", failed.status == 'sent' and failed.attempts == 2)
AIEvent.objects.filter(status='pending').update(status='done')

msg = say(SID, "Duplicate webhook test", enqueue=False)
service.enqueue_tenant_message(SID, msg.message_sid, msg.body); service.enqueue_tenant_message(SID, msg.message_sid, msg.body)
after_hours.process_pending()
check("duplicate webhook: one event, one after-hours decision", AIEvent.objects.filter(message=msg).count() == 1
      and AIAfterHoursAck.objects.filter(event__message=msg).count() == 1)
AIEvent.objects.filter(status='pending').update(status='done')
OFFICE[0] = True

# ---- 11. emergency: at once + phone call; failed send retried once ----------------------------------------------------
dialled.clear(); AIAlertCall.objects.all().delete()
r10 = run_with(SID, "There is smoke coming from the oven!", dict(triage(primary_type='URGENT_PROPERTY_OR_ACCESS', priority='emergency'),
               answer="Please get to safety and call 911 now. We've alerted the team.", why='emergency', actions=[
               {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Smoke from oven', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
               {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'emergency', 'responsible': ['Edy', 'Kevin'], 'text': 'smoke'}]))
check("emergency: safety reply sent at once, actions done at once, Farid phoned",
      sms[-1][1].startswith("Please get to safety") and AIIssue.objects.filter(summary='Smoke from oven').exists() and dialled == ['+15614603904'])

sms_fail[0] = 1
r11 = run_with(SID, "Where do I put the trash?", dict(triage(primary_type='PROPERTY_FACTS', issue_refs=[]),
               answer="The trash room is next to the elevator.", why='kb', actions=[]))
press(r11, 'a')
check("failed send after approval: FAILED and a retry is scheduled", r11.hold_status == AIRun.HOLD_FAILED and r11.review.get('retry'), r11.review.get('retry'))
answer_review.retry_failed_sends(now=timezone.now() + timedelta(minutes=4)); r11.refresh_from_db()
check("the retry 3 minutes later sends it", r11.hold_status == AIRun.HOLD_SENT and sms[-1][1] == "The trash room is next to the elevator."
      and 'Retry worked' in telegram[-1]['text'], (r11.hold_status, telegram[-1]['text']))

# ---- 12. two issues in one message; manager answers before approval; ClickUp writes off --------------------------------
r12 = run_with(SID, "The shower drains slowly and the balcony door squeaks", dict(triage(issue_refs=['new-1', 'new-2']),
      answer="Thanks, I've logged both with the team.", why='two issues', actions=[
      {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Shower drains slowly', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
      {'type': 'CREATE_ISSUE', 'temp_id': 'new-2', 'summary': 'Balcony door squeaks', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
      {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'routine', 'responsible': ['Edy'], 'text': 'two things'}]))
say(SID, "Edy: I'll check both tomorrow", author=EDY_PHONE, enqueue=False)
before = len(sms)
press(r12, 'a')
check("two issues in one message are both opened on approval; staff answered first -> the AI answer is NOT sent",
      AIIssue.objects.filter(summary='Shower drains slowly').exists() and AIIssue.objects.filter(summary='Balcony door squeaks').exists()
      and len(sms) == before and r12.hold_status == AIRun.HOLD_SUPPRESSED, r12.hold_status)
AIManagement.objects.update_or_create(prompt_key='ai_clickup_writes', defaults={'name': 'w', 'entry_type': 'prompt', 'content': 'off'})
r13 = run_with(SID, "Mold on the bathroom ceiling", sink("Mold on bathroom ceiling"))
check("ClickUp writes OFF: the card says so", "ClickUp writes OFF" in card_of(r13)['text'])
AIManagement.objects.filter(prompt_key='ai_clickup_writes').delete()

# ---- 13. test apartment: approve = would send ------------------------------------------------------------------
before = len(sms)
r14 = run_with(SID_TEST, "Wifi password?", dict(triage(primary_type='PROPERTY_FACTS', issue_refs=[]), answer="It is B123.", why='kb', actions=[]))
press(r14, 'a')
check("test apartment: TEST on the card, approving sends nothing to Twilio", "🧪 TEST" in card_of(r14)['text'] and len(sms) == before
      and r14.hold_status == AIRun.HOLD_SENT and 'would be sent' in telegram[-1]['text'], telegram[-1]['text'])

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
