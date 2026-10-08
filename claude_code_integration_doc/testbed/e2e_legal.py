"""
Legal / contract questions (user request 2026-09-25) and the press that sends an answer (simple alerts,
simple_telegram_alerts.md). The AI reads the contract (get_contract) and SUGGESTS an answer; nothing is sent until a
manager presses 🤖 Send Answer under the alert - there is no timer. Also here (moved from the removed e2e_client_v4.py
and e2e_answer_review.py): the final recheck before a send, outdated alerts, a failed send retried once, an emergency
done at once, Telegram down, and how the worker reads Telegram (offset, replies it can not place, old-format buttons).
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

import json
from django.utils import timezone
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement, AIEvent, AIRun, AIIssue,
                           AIFollowUp, StaffMember, AIAlertCall)
from mysite.ai_agent import (service, runner, config, answer_review, approval, alerts_v5, calls, clickup, notify, prompts,
                             contract)
import mysite.ai_agent.team_notify as team_notify
import mysite.views.messaging as messaging
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_legal"
config.WORK_DIR = config.RUNS_DIR / "_cwd"
config.is_within_notification_window = lambda now=None: True   # this file is about the press, not the SMS hours
config.is_office_hours = lambda now=None: True                  # ... nor about the after-hours message

from mysite.ai_agent.sandbox_test.world import html_to_plain as _plain_of   # an alert sent as Telegram HTML, as read
# ---- fakes ---------------------------------------------------------------------------------
telegram, sms, tg_fail, markups, callbacks, tg_ids = [], [], [False], [], [], [600000]   # ids unlike the other files'
def fake_tg(text, reply_to=None, reply_markup=None, silent=False, parse_mode=None):
    text = _plain_of(text) if parse_mode == 'HTML' else text
    if tg_fail[0]:
        return False, "down", None
    tg_ids[0] += 1
    telegram.append({'id': tg_ids[0], 'text': text, 'reply_to': reply_to, 'markup': reply_markup})
    return True, "sent", tg_ids[0]
for module in (team_notify, answer_review, alerts_v5, notify, calls):
    module.send_ai_chat = fake_tg
for module in (alerts_v5, approval, notify):
    module.edit_reply_markup = lambda message_id, markup=None: markups.append((message_id, markup))
    module.answer_callback = lambda cid, text='', alert=False: callbacks.append((cid, text, alert))
notify.edit_message_text = lambda message_id, text, reply_markup=None, parse_mode=None: markups.append((message_id, reply_markup))
errors = []
answer_review.report_error = service.report_error = calls.report_error = \
    lambda e, ctx, info=None, source='task': errors.append(f"{ctx}: {e}")
sms_fail = [0]
def fake_sms(sid, author, message, sender, receiver):
    if sms_fail[0]:
        sms_fail[0] -= 1
        raise Exception("twilio down")
    sms.append((sid, message))
messaging.send_messsage_by_sid = fake_sms
dialled = []
class _Call:
    def __init__(self, sid): self.sid, self.status = sid, 'completed'
    def fetch(self): return self
class _Calls:
    def create(self, to, from_, twiml, timeout): dialled.append(to); return _Call(f"CA{len(dialled)}")
    def __call__(self, sid): return _Call(sid)
class _Twilio: calls = _Calls()
messaging.get_twilio_client = lambda: _Twilio()   # never a real phone call (testbed_settings)
updates, interpreter_out = [], []
answer_review.fetch_updates = lambda: [updates.pop(0) for _ in range(len(updates))]
answer_review.run_interpreter = lambda prompt: interpreter_out.pop(0)
class _Map: list_id, channel_id, name = 'L1', None, 'list 730'
clickup.delivery_mode = lambda: 'api'
clickup.channel_for = lambda apartment: _Map()
clickup.post_message = lambda *a, **k: None
clickup.create_task = lambda *a, **k: ("tk1", "https://app.clickup.com/t/tk1")

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
TENANT_PHONE = "+15550007788"
if not User.objects.filter(email="lg@example.com").exists():
    User.objects.bulk_create([User(email="lg@example.com", full_name="Lena Legal", role="Tenant", phone=TENANT_PHONE)])
tenant = User.objects.get(email="lg@example.com")
def make_apartment(name, live):
    Apartment.objects.bulk_create([Apartment(name=name, building_n="730", apartment_n=name[-3:], street="S", state="FL", city="WPB",
        zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_group_chat_enabled=live)])
    apt = Apartment.objects.get(name=name)
    Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=2),
                                         end_date=date.today() + timedelta(days=20), status="Confirmed", contract_id="555")])
    sid = f"CHlegal{name}"
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=sid, friendly_name=name, apartment=apt,
                                                               booking=Booking.objects.get(apartment=apt))])
    return apt, sid
apt, SID = make_apartment("730-201", live=True)
apt2, SID2 = make_apartment("730-202", live=True)
apt3, SID3 = make_apartment("730-203", live=True)
apt_test, SID_TEST = make_apartment("730-204", live=False)
apt5, SID5 = make_apartment("730-205", live=True)
AIManagement.objects.filter(prompt_key='ai_clickup_writes').delete()   # an earlier test file may have left it off
for name, role, phone in (("Edy", "operations", "+15612220001"), ("Farid", "owner", "+15614603904"), ("Kevin", "supervisor", None)):
    if not StaffMember.objects.filter(ai_name=name).exists():
        StaffMember.objects.bulk_create([StaffMember(ai_name=name, full_name=name, role=role, phone=phone)])
StaffMember.objects.filter(ai_name="Farid").update(phone="+15614603904", is_active=True)
# a manager writing from the CRM chat page counts as staff (Edy may have no phone in the shared test DB)
STAFF_AUTHOR = "ASSISTANT"
n = [0]
def say(sid, body, author=TENANT_PHONE, enqueue=True):
    n[0] += 1
    conv = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"LG{n[0]:04d}", conversation=conv, conversation_sid=sid,
                                                     author=author, body=body, direction='inbound')])
    if enqueue:
        service.enqueue_tenant_message(sid, f"LG{n[0]:04d}", body)
    return TwilioMessage.objects.get(message_sid=f"LG{n[0]:04d}")
def drain():
    AIEvent.objects.filter(status='pending').update(created_at=timezone.now() - timedelta(minutes=5))
    runs = []
    while True:
        batch = service.claim_next_batch()
        if not batch: return runs
        runs.append(service.process_events(batch))
def run_with(sid, body, output):
    say(sid, body); script.append(output); r = drain()[-1]; r.refresh_from_db(); return r
def alert_of(run):
    return next(t for t in telegram if t['id'] == run.telegram_message_id)
def buttons(markup):
    return [b for row in (markup or {}).get('inline_keyboard', []) for b in row]
def press(run, code, arg='', who="Kevin", message_id=None, prefix='v5', chat=-500):
    """A button press as the worker gets it from Telegram."""
    run.refresh_from_db()
    updates.append({'update_id': 3000 + len(callbacks) + len(telegram), 'callback_query': {
        'id': f"cb{len(callbacks)}", 'from': {'first_name': who}, 'data': f"{prefix}|{code}|{run.id}|{arg}",
        'message': {'message_id': message_id or run.telegram_message_id, 'chat': {'id': chat}}}})
    answer_review.poll_telegram(); run.refresh_from_db()
    return callbacks[-1][1] if callbacks else ''
def reply_to_message(run, message_id, text, who="Kevin", chat=-500, parent_is_bot=True, **decision):
    if decision:
        interpreter_out.append(dict(answer_review.EMPTY_DECISION, **decision))
    updates.append({'update_id': 5000 + len(checks) + len(telegram), 'message': {
        'message_id': 9500 + len(checks) + len(telegram), 'chat': {'id': chat}, 'from': {'first_name': who}, 'text': text,
        'date': int(timezone.now().timestamp()),
        'reply_to_message': {'message_id': message_id, 'from': {'is_bot': parent_is_bot}}}})
    answer_review.poll_telegram()
    if run:
        run.refresh_from_db()
    return telegram[-1]['text'] if telegram else ''
def triage(**fields):
    base = {'primary_type': 'LEGAL_OR_CONTRACT', 'secondary_types': [], 'priority': 'routine', 'case_status': 'ACKNOWLEDGED',
            'issue_refs': [], 'owner': 'Kevin', 'next_action': 'Kevin: confirm the answer', 'tenant_deadline': '',
            'verified_facts': [], 'uncertainties': [], 'no_reply_reason': ''}
    base.update(fields); return base

SUGGESTED = "Per your agreement, cancelling within 30 days of arrival means the deposit is not refunded."
BASIS = 'CANCELLATION OF CONTRACT: "cancellations within 30 days of arrival forfeit the deposit"'
def legal(extra_actions=(), answer=SUGGESTED):
    return dict(triage(), answer=answer, why='legal question about cancellation', needs_manager_confirmation=True,
                contract_basis=BASIS, actions=[*extra_actions])
def plain(answer, **tri):
    return dict(triage(primary_type='PROPERTY_FACTS', owner='Edy', **tri), answer=answer, why='kb', actions=[],
                needs_manager_confirmation=False, contract_basis='')

# ---- 1. legal question: the suggested answer waits for a press, with its contract basis ---------------
r1 = run_with(SID, "If I cancel now do I get my deposit back?", legal())
a1 = alert_of(r1)
if os.environ.get('SHOW_ALERT'): print(a1['text'] + "\n=====")
check("nothing sent at once", not sms)
check("run is held and marked as needing a manager", r1.hold_status == 'holding' and r1.review.get('needs_confirmation') is True
      and r1.review.get('contract_basis') == BASIS, (r1.hold_status, r1.review))
check("the AI's triage is stored on the run", r1.triage.get('primary_type') == 'LEGAL_OR_CONTRACT' and r1.triage.get('owner') == 'Kevin', r1.triage)
check("alert: the suggested answer and the contract basis right under it, with Send Answer (SMS) / Edit Answer (live apartment)",
      f'🤖 "{SUGGESTED}"\n⚖️ Contract: ' in a1['text'] and f"⚖️ Contract: {BASIS}" in a1['text']
      and [b['text'] for b in buttons(a1['markup'])][:2] == ['🤖 Send Answer (SMS)', '✏️ Edit Answer'], a1)
chat_why = json.dumps(TwilioMessage.objects.filter(conversation_sid=SID).order_by('-id').first().__dict__, default=str)
check("CRM chat page marks it as a legal suggestion", "LEGAL - SUGGESTED ANSWER" in chat_why, chat_why[:400])

# ---- 2. no timer: hours later the worker still sends nothing --------------------------------------------
AIRun.objects.filter(id=r1.id).update(created_at=timezone.now() - timedelta(hours=5))
before = len(telegram)
service.fire_due_followups(); drain(); answer_review.retry_failed_sends(now=timezone.now() + timedelta(hours=5))
r1.refresh_from_db()
check("hours later: still not sent, nothing posted again", not sms and r1.hold_status == 'holding' and len(telegram) == before)

# ---- 3. the manager presses Send Answer: the suggested answer goes out ------------------------------------
t = press(r1, 'sa')
check("Send Answer sends the suggested answer", sms == [(SID, SUGGESTED)] and r1.hold_status == 'sent', (sms, t))
check("the button shows who sent it, the press is logged with the name",
      buttons(markups[-1][1])[0]['text'].startswith("✅ Answer sent · Kevin") and r1.review['decisions'][-1]['by'] == 'Kevin', markups[-1:])
t = press(r1, 'sa', who="Edy")
check("a second press only says who did it", t.startswith("already done by Kevin") and len(sms) == 1, t)

# ---- 4. Edit Answer: the manager's own text is shown with its own Send button, sent on the press ---------
sms.clear()
r2 = run_with(SID2, "Can I break my lease early?", legal(answer="Early termination costs two months of rent."))
press(r2, 'ea')
prompt_id = r2.review['replace_prompts'][-1]
check("Edit Answer asks for the text with a force-reply message", telegram[-1]['markup'].get('force_reply'), telegram[-1])
FIXED = "The early termination fee is one month of rent."
t = reply_to_message(r2, prompt_id, FIXED, decision='replace', corrected_answer=FIXED)
proposal = r2.review['proposals'][-1]
check("the written text is a NEW ANSWER with its own Send button - NOT sent yet",
      "✏️ NEW ANSWER" in t and FIXED in t and not sms and r2.hold_status == 'holding'
      and [b['text'] for b in buttons(telegram[-1]['markup'])] == ['🤖 Send Answer'], t)
press(r2, 'ps', proposal['id'], message_id=proposal['message_id'])
check("its Send sends the manager's text instead of the AI answer", sms == [(SID2, FIXED)] and r2.hold_status == 'corrected', sms)

# ---- 5. a follow-up message can not slip the legal answer out: the new answer inherits the confirmation ---
sms.clear()
r3 = run_with(SID3, "What is the fine for smoking?", legal(answer="The contract sets a $500 fine for smoking."))
r4 = run_with(SID3, "hello??", plain("The contract sets a $500 fine for smoking. Anything else?"))
r3.refresh_from_db()
check("the new run was told the legal draft is replaced and must be repeated (PENDING_PROPOSAL, LEGAL)",
      "PENDING_PROPOSAL" in inputs_seen[-1] and "[LEGAL - needs_manager_confirmation]" in inputs_seen[-1], inputs_seen[-1][-600:])
check("newer answer replaces the legal draft and inherits the manager confirmation",
      r3.hold_status == 'superseded' and r3.review.get('stale') == r4.id and r4.hold_status == 'holding'
      and r4.review.get('needs_confirmation') is True, (r3.hold_status, r4.review))
check("the older alert says Outdated and has no buttons", r3.review.get('outdated', '').startswith("⚠️ Outdated")
      and (r3.telegram_message_id, None) in markups, r3.review.get('outdated'))
t = press(r3, 'sa')
check("a press on the outdated alert is refused, nothing sent", 'Outdated' in t and not sms, t)
t = reply_to_message(r3, r3.telegram_message_id, "is this urgent?")
check("a typed reply to the outdated alert points to the newer one", "outdated" in t and "newer one" in t, t)

# ---- 6. final recheck: the tenant wrote again after the proposal -> Send is refused --------------------------
r5 = run_with(SID, "Where do I put the trash?", plain("The trash room is next to the elevator."))
say(SID, "Actually where is the recycling?", enqueue=False)
t = press(r5, 'sa')
check("final recheck: the tenant wrote again after the proposal -> Send is refused, nothing sent",
      'wrote again' in t and r5.hold_status == 'holding' and len(sms) == 0, (t, sms))
TwilioMessage.objects.filter(body="Actually where is the recycling?").delete()

# ---- 7. a team member answered in the chat before the press -> the AI answer is not sent ---------------------
r6 = run_with(SID5, "Is parking free?", plain("Yes, parking is free."))
say(SID5, "Spot 12, blue sign. - Edy", author=STAFF_AUTHOR, enqueue=False)
t = press(r6, 'sa')
check("staff answered in the chat meanwhile -> Send does not send, the button says the team answered",
      not sms and r6.hold_status == 'suppressed' and "team answered" in t, (r6.hold_status, t))

# ---- 8. a send that fails after the press is retried once, 3 minutes later -----------------------------------
sms_fail[0] = 1
r7 = run_with(SID5, "What's the wifi password?", plain("It's on the fridge."))
t = press(r7, 'sa')
check("failed send after the press: FAILED, the group is told, a retry is scheduled",
      r7.hold_status == 'failed' and r7.review.get('retry') and "Could not send" in telegram[-1]['text'], (r7.hold_status, t))
answer_review.retry_failed_sends(now=timezone.now() + timedelta(minutes=4)); r7.refresh_from_db()
check("the retry 3 minutes later sends it", r7.hold_status == 'sent' and sms[-1] == (SID5, "It's on the fridge.")
      and 'Retry worked' in telegram[-1]['text'], (r7.hold_status, telegram[-1]['text']))

# ---- 9. test apartment: same alert, marked TEST; a pressed Send really sends (rule 1.1.2) ----------------------
sms.clear()
r8 = run_with(SID_TEST, "Can I sublet the unit?", legal(answer="Subletting is not allowed under your agreement."))
check("test mode: held, nothing sent, the alert says TEST", r8.hold_status == 'holding' and not sms and "🧪 TEST" in alert_of(r8)['text'])
press(r8, 'sa')
check("test mode: Send Answer sends for real", r8.hold_status == 'sent' and sms == [(SID_TEST, "Subletting is not allowed under your agreement.")], sms)

# ---- 10. Telegram down: nobody can press, so nothing is sent; the error alert says so ------------------------------
sms.clear(); tg_fail[0] = True
r9 = run_with(SID2, "Am I liable for the broken window?", legal(answer="Per the contract, damage is charged to the tenant."))
tg_fail[0] = False
check("Telegram down: the answer stays held, nothing sent, error reported",
      not sms and r9.hold_status == 'holding' and any("could NOT be posted for approval" in e for e in errors), errors[-1:])

# ---- 11. emergency: no press needed - answer sent, actions done, plain alert, Farid phoned ----------------------------
dialled.clear(); AIAlertCall.objects.all().delete()
r10 = run_with(SID3, "There is smoke coming from the oven!", dict(triage(primary_type='URGENT_PROPERTY_OR_ACCESS', priority='emergency',
               owner='Edy'), answer="Please get to safety and call 911 now. We've alerted the team.", why='emergency', actions=[
               {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Smoke from oven', 'owner': 'Edy', 'state': 'MAINTENANCE_OPEN'},
               {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'emergency', 'responsible': ['Edy', 'Kevin'], 'text': 'smoke'}]))
alert10 = alert_of(r10)['text'] if r10.telegram_message_id else telegram[-1]['text']
check("emergency: safety reply sent at once, issue opened at once, Farid phoned",
      sms[-1] == (SID3, "Please get to safety and call 911 now. We've alerted the team.")
      and AIIssue.objects.filter(summary='Smoke from oven').exists() and dialled == ['+15614603904'], (sms[-1:], dialled))
check("emergency: no plan waits for a press; the plain alert says what was done",
      not (r10.review or {}).get('plan') and alert10.startswith('🚨 EMERGENCY') and "sent to the tenant" in alert10, alert10[:400])

# ---- 12. the worker reading Telegram: offset, replies it can not place, other chats, old-format buttons ----------------
r11 = run_with(SID2, "Is the pool heated?", plain("Yes, it is heated all year."))
before_calls = len(interpreter_out)
updates.append({'update_id': 8800, 'message': {'message_id': 1, 'chat': {'id': -500}, 'from': {}, 'text': 'hello team', 'date': 0}})
answer_review.poll_telegram()
check("the offset is saved past the last update (a reply is never applied twice)", answer_review._read_offset() == 8801)
n_tg = len(telegram)
reply_to_message(r11, r11.telegram_message_id, "stop", chat=-999)
check("replies in other chats and plain messages are ignored", len(telegram) == n_tg and r11.hold_status == 'holding'
      and len(interpreter_out) == before_calls)
t = reply_to_message(None, 424242, "what about this?")
check("a reply to a bot message that belongs to no alert gets a hint, nothing else", t == answer_review.NO_RUN_HINT, t)
n_tg = len(telegram)
reply_to_message(None, 424243, "a reply to a person, not to the bot", parent_is_bot=False)
check("a reply to someone else's message that belongs to no alert stays silent", len(telegram) == n_tg)
t = press(r11, 'a', '', prefix='v4')
check("a press on a button of the old card only says it can not be used, nothing sent",
      'old format' in t and r11.hold_status == 'holding' and markups[-1] == (r11.telegram_message_id, None), t)
approval._update_review(r11, style='v4')   # a run made before the simple alerts
t = reply_to_message(r11, r11.telegram_message_id, "send it")
check("a typed reply to an alert of the old format says so, nothing sent", "old format" in t and r11.hold_status == 'holding', t)

# ---- 13. contract text for the agent: bank lines and links removed ----------------------------------------
contract._get = lambda path: ({'status': 'completed', 'completed_at': '2026-09-24T19:22:14Z', 'template': {'id': 1, 'name': 'Occupancy'},
                               'documents': [{'url': 'https://docuseal.com/file/x'}],
                               'submitters': [{'status': 'completed', 'values': [
                                   {'field': 'start_date', 'value': 'October 26 2026'},
                                   {'field': 'tenant_signeture', 'value': 'https://docuseal.com/file/sig'},
                                   {'field': 'photos', 'value': ['a', 'b']}]}]})
contract._pdf_text = lambda url, key: ("Payment information:\nZelle (561 460 3904)\nBank of America, Account #: 8981 111 09 646,\n"
                                       "ABA Wire: 026009593 SWIFT: BOFAUS3N\nPETS: fee $95. See https://example.com/rules\n")
text = contract.contract_for_booking(Booking.objects.get(apartment=apt))
check("get_contract: status, terms, clauses; no bank numbers, links or signature",
      "STATUS: SIGNED" in text and "- start_date: October 26 2026" in text and "PETS: fee $95" in text
      and not any(w in text for w in ("8981", "026009593", "BOFAUS", "561 460", "docuseal.com", "example.com", "signeture"))
      and "[bank / payment account details removed" in text, text)
b = Booking.objects.get(apartment=apt); b.contract_id = None
check("no contract on file -> says so", "No contract on file" in contract.contract_for_booking(b))
# ---- Send Answer (SMS) / Send Answer (CRM) (user decision 2026-10-08) --------------------------------------------------
from mysite.ai_agent import alerts_v5
def rows_of(markup):
    return [[b['text'] for b in row] for row in (markup or {}).get('inline_keyboard', [])]
SMS_B, CRM_B, EDIT_B = '🤖 Send Answer (SMS)', '📝 Send Answer (CRM)', '✏️ Edit Answer'
check("buttons: live -> Send Answer (SMS); test apartment -> (SMS) and (CRM), Edit Answer below; CRM-only chat -> (CRM)",
      rows_of(alerts_v5.keyboard(1, True, [], kind='live')) == [[SMS_B, EDIT_B]]
      and rows_of(alerts_v5.keyboard(1, True, [], kind='test')) == [[SMS_B, CRM_B], [EDIT_B]]
      and rows_of(alerts_v5.keyboard(1, True, [], kind='crm')) == [[CRM_B, EDIT_B]])
check("which chat is CRM-only: CHSANDBOX... / CHTEST... yes, a real Twilio chat no; a test apartment's real chat is 'test'",
      messaging.is_crm_only_chat('CHTEST0001') and messaging.is_crm_only_chat('CHSANDBOXAIAGENT00000000000000001')
      and not messaging.is_crm_only_chat(SID) and alerts_v5.send_kind('test', 'CHTEST0001') == 'crm'
      and alerts_v5.send_kind('test', SID) == 'test' and alerts_v5.send_kind('live', SID) == 'live')
apt9, SID9 = make_apartment("730-209", live=False)
r9 = run_with(SID9, "Is there a gym in the building?", {'answer': "Yes, the gym is on the 2nd floor, open 6am-10pm.", 'why': 'kb',
                                                         'primary_type': 'PROPERTY_FACTS', 'priority': 'routine', 'actions': []})
check("test apartment: the alert has Send Answer (SMS) and (CRM), Edit Answer below", rows_of(alert_of(r9)['markup'])[:2] == [[SMS_B, CRM_B], [EDIT_B]],
      rows_of(alert_of(r9)['markup']))
sms.clear()
popup = press(r9, 'sm')
local = TwilioMessage.objects.filter(conversation_sid=SID9, message_sid__startswith='LOCAL-', body__startswith='Yes, the gym').first()
check("Send Answer (CRM): the answer is written into the CRM chat only - no SMS; the button says so",
      not sms and local and local.direction == 'outbound' and r9.hold_status == 'sent'
      and rows_of(alerts_v5.keyboard_for(r9))[0][0].startswith('✅ Sent to CRM · Kevin'), (sms, local, r9.hold_status, rows_of(alerts_v5.keyboard_for(r9)), popup))

check("tool allowed + prompt + schema describe the legal flow",
      'mcp__crm__get_contract' in config.ALLOWED_MCP_TOOLS and "LEGAL QUESTIONS" in prompts.get_system_prompt()[0]
      and 'needs_manager_confirmation' in json.loads(config.SCHEMA_PATH.read_text())['properties']
      and 'def get_contract' in config.MCP_TOOLS_PATH.read_text())

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
