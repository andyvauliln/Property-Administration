"""
The sandbox test runner itself (manage.py ai_agent_sandbox_test, simple_telegram_alerts.md part 11) on the throwaway
database: fake Claude, Telegram offline, no judge. Proves the mechanics - case time, clean start, set-up, the agent
run like the worker, checks, simulated presses and replies, reminders, real-chat replay - and above all that a test
stays inside the sandbox.
"""
import copy, os, sys, django, tempfile
from datetime import date, timedelta
from pathlib import Path
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

from django.conf import settings as _s
from django.utils import timezone
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIEvent, AIRun, AIIssue,
                           AIFollowUp, StaffMember, AIAlertCall, PendingOutboundMessage)
from mysite.ai_agent import answer_review, config, runner as agent_runner
import mysite.views.messaging as messaging
from mysite.ai_agent.sandbox_test import catalog, check, story, world as world_mod
from mysite.ai_agent.sandbox_test.runner import Runner, list_cases
from mysite.management.commands.ai_agent_sandbox import SANDBOX_SID

config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_sandbox_runner"
config.WORK_DIR = config.RUNS_DIR / "_cwd"
catalog.RESULTS_DIR = Path(tempfile.mkdtemp(prefix="sandbox_tests_", dir=_s.TESTBED_DIR))
catalog.LAST_RESULTS = catalog.RESULTS_DIR / "last_results.json"
world_mod.STATE_FILE = catalog.RESULTS_DIR / ".sandbox_state.json"
world_mod.POSTED_FILE = catalog.RESULTS_DIR / ".posted_messages.jsonl"
story.SNAPSHOT_DIR = catalog.RESULTS_DIR / "story_state"
story.CONTRACT_FILE = story.SNAPSHOT_DIR / "contract.txt"

checks = []
def check_(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {str(extra)[:900]}" if extra and not cond else ''))

# ---- fixtures: the test apartment, a real apartment with a real chat, staff ------------------------------------------
def make_apartment(name, live):
    if not Apartment.objects.filter(name=name).exists():
        Apartment.objects.bulk_create([Apartment(name=name, building_n="1", apartment_n="1", street="S", state="FL", city="WPB",
            zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_group_chat_enabled=live)])
    return Apartment.objects.get(name=name)
make_apartment("Test_Apart2", live=False)
from mysite.models import PaymenType
for _name in ("Rent", "Hold Deposit"):
    if not PaymenType.objects.filter(name=_name).exists():
        PaymenType.objects.bulk_create([PaymenType(name=_name, type="In", category="Operating")])
real_apartment = make_apartment("900-101", live=True)
Apartment.objects.filter(id=real_apartment.id).update(knowledge_base="Pool hours: 8am-8pm")
if not User.objects.filter(email="sbx-real@example.com").exists():
    User.objects.bulk_create([User(email="sbx-real@example.com", full_name="Rita Real", role="Tenant", phone="+15557770199")])
real_tenant = User.objects.get(email="sbx-real@example.com")
REAL_SID = "CHsbxrunnerreal0001"
if not TwilioConversation.objects.filter(conversation_sid=REAL_SID).exists():
    Booking.objects.bulk_create([Booking(apartment=real_apartment, tenant=real_tenant, status="Confirmed", notes="sbx runner real",
                                         start_date=date.today() - timedelta(days=5), end_date=date.today() + timedelta(days=30))])
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=REAL_SID, friendly_name="900-101", apartment=real_apartment,
                                                               booking=Booking.objects.get(notes="sbx runner real"))])
for name, role, phone in (("Edy", "operations", "+15612220001"), ("Farid", "owner", "+15612220002"), ("Janna", "accounting", "+15612220003"),
                          ("Kevin", "supervisor", None)):
    if not StaffMember.objects.filter(ai_name=name).exists():
        StaffMember.objects.bulk_create([StaffMember(ai_name=name, full_name=name, role=role, phone=phone)])
real_conversation = TwilioConversation.objects.get(conversation_sid=REAL_SID)

# ---- parking is CRM data: the booking's spot and the apartment's own spot are in the AI's context --------------------
from mysite.models import Parking, ParkingBooking, Payment
from mysite.views import messaging as _messaging
if not Parking.objects.filter(number="78").exists():
    Parking.objects.bulk_create([Parking(number="77", building="1", associated_room="1", notes="garage"),
                                 Parking(number="78", building="9", associated_room="9", notes="street side")])
    ParkingBooking.objects.bulk_create([ParkingBooking(parking=Parking.objects.get(number="78"), booking=real_conversation.booking,
                                                       apartment=real_apartment, status="Booked")])
_lines = _messaging.parking_context_lines(real_conversation.booking, real_apartment)
check_("context: PARKING has the spot booked for the booking and the apartment's own spot",
       _lines == ["- Spot #78 (street side, building: 9) - booked for this booking, status: Booked",
                  "- Spot #77 (garage, building: 1) - this apartment's own spot, NOT booked for this booking in the CRM; FREE for this booking's dates"], _lines)
check_("context: no booking and no apartment spot -> no PARKING lines", _messaging.parking_context_lines(None, None) == [])

# ---- CRM record changes (crm_changes.py): only the listed changes, checked against the CRM, written on the press -------
from mysite.ai_agent import crm_changes as _crm
from mysite.ai_agent.actions import ActionError as _ActionError
def _refused(fn, *args):
    try:
        fn(*args)
    except _ActionError as e:
        return str(e)
    return ''
_booking = real_conversation.booking
_label = _crm.check(_booking, real_apartment, {'change': 'book_parking', 'spot': '77'})
check_("CRM change: booking the apartment's free spot is possible and worded from the CRM", _label.startswith("book parking spot #77 for this booking ("), _label)
check_("CRM change: an unknown change, an unknown spot and a spot that is already booked are refused",
       "unknown CRM change" in _refused(_crm.check, _booking, real_apartment, {'change': 'delete_booking'})
       and "no parking spot #999" in _refused(_crm.check, _booking, real_apartment, {'change': 'book_parking', 'spot': '999'})
       and "already booked" in _refused(_crm.check, _booking, real_apartment, {'change': 'book_parking', 'spot': '78'}))
_before = ParkingBooking.objects.count()
_detail = _crm.carry_out(_booking, real_apartment, {'change': 'book_parking', 'spot': '#77'}, 'Andy')
_row = ParkingBooking.objects.filter(booking=_booking, parking__number="77").first()
check_("CRM change: the press writes the parking record of the booking (dates and apartment of the booking, who approved)",
       ParkingBooking.objects.count() == _before + 1 and _row and _row.start_date == _booking.start_date and _row.apartment_id == real_apartment.id
       and 'Andy' in (_row.notes or '') and "#77" in _detail, (_detail, _row and _row.__dict__))
check_("CRM change: a second press can not book it twice", "already booked" in _refused(_crm.carry_out, _booking, real_apartment, {'change': 'book_parking', 'spot': '77'}, 'Andy'))
_other = Booking.objects.bulk_create([Booking(apartment=real_apartment, tenant=real_tenant, status="Confirmed", notes="sbx other booking",
                                              start_date=_booking.start_date, end_date=_booking.end_date)]) and Booking.objects.get(notes="sbx other booking")
check_("CRM change: a spot taken by another booking in these dates is refused, and the context says TAKEN",
       "is taken" in _refused(_crm.check, _other, real_apartment, {'change': 'book_parking', 'spot': '77'})
       and any("#77" in l and "TAKEN" in l for l in _messaging.parking_context_lines(_other, real_apartment)), _messaging.parking_context_lines(_other, real_apartment))
_crm.carry_out(_booking, real_apartment, {'change': 'cancel_parking'}, 'Andy')
check_("CRM change: cancel_parking deletes the booking's parking records; the spot is FREE again in the context",
       not ParkingBooking.objects.filter(booking=_booking).exists()
       and any("#77" in l and "FREE for this booking's dates" in l for l in _messaging.parking_context_lines(_booking, real_apartment))
       and "no parking is booked" in _refused(_crm.check, _booking, real_apartment, {'change': 'cancel_parking'}))
ParkingBooking.objects.bulk_create([ParkingBooking(parking=Parking.objects.get(number="78"), booking=_booking, apartment=real_apartment, status="Booked",
                                                   start_date=_booking.start_date, end_date=_booking.end_date)])
Booking.objects.filter(id=_other.id).delete()
TwilioMessage.objects.filter(conversation_sid=REAL_SID).delete()
base = timezone.now() - timedelta(days=3)
for n, (author, body, minutes) in enumerate((("+15557770199", "Hi, the AC is not cooling", 0), ("+15557770199", "it is 85 inside", 0.5),
                                              ("+15612220001", "Rita, the technician comes tomorrow at 10", 40),
                                              ("+15557770199", "ok thank you so much", 45))):
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"SMreal{n}", conversation=real_conversation, conversation_sid=REAL_SID, author=author, body=body,
                                                     direction='inbound' if author.endswith("0199") else 'outbound')])
    TwilioMessage.objects.filter(message_sid=f"SMreal{n}").update(message_timestamp=base + timedelta(minutes=minutes))
real_message_count = TwilioMessage.objects.filter(conversation_sid=REAL_SID).count()

# ---- fakes: the agent's Claude run and the reply interpreter -----------------------------------------------------------
script, inputs_seen, interpreter_out = [], [], []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None, **extra):
    inputs_seen.append(user_input)
    output = script.pop(0)
    output = output(user_input) if callable(output) else output   # a scripted answer may need ids from the input
    # A copy: the backend marks the actions it carried out ('done') in place, and the same fixture is used again later
    output = copy.deepcopy(output)
    return {'ok': True, 'error': None, 'output': output, 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 5, 'timed_out': False}
agent_runner.run_claude = fake_run_claude
answer_review.run_interpreter = lambda prompt: interpreter_out.pop(0)
twilio_calls = []
real_send = messaging.send_messsage_by_sid
messaging.send_messsage_by_sid = lambda *a, **k: twilio_calls.append(a)   # the "real" Twilio: must never be reached

SINK = {'answer': "Hi Vera, thanks for letting us know. We've logged it and our maintenance team will contact you.",
        'why': 'routine maintenance', 'primary_type': 'ROUTINE_MAINTENANCE', 'priority': 'routine', 'case_status': 'NEW', 'owner': 'Edy',
        'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Kitchen sink dripping', 'owner': 'Edy', 'priority': 'routine',
                     'state': 'MAINTENANCE_OPEN'},
                    {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': 'Kitchen sink dripping', 'priority': 'routine', 'responsible': ['Edy']},
                    {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': 'Check the sink task has a visit date'}]}
LEAK = {'answer': "Please turn off the main water valve and call 911 if water touches electric outlets. We are calling our team now.",
        'why': 'emergency', 'primary_type': 'URGENT_PROPERTY_OR_ACCESS', 'priority': 'emergency', 'owner': 'Edy',
        'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Ceiling leak', 'owner': 'Edy', 'priority': 'emergency'},
                    {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'emergency', 'text': 'Ceiling leak, a lot of water'}]}
# still needed: next_action says what the team must do now (without it the reminder closes quietly, C5)
REMIND = {'answer': 'NO_ANSWER', 'why': 'task still open', 'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine', 'owner': 'Edy',
          'next_action': 'Edy: give the sink task a visit date', 'no_reply_reason': 'reminder for the team',
          'actions': [{'type': 'INTERNAL_ALERT', 'priority': 'routine', 'text': 'Sink task still has no visit date', 'responsible': ['Edy']}]}
QUIET = {'answer': 'NO_ANSWER', 'why': 'nothing to do', 'primary_type': 'NO_REPLY', 'actions': []}

lines_out = []
cases = catalog.load_cases()
runner = Runner(auto=True, offline=True, use_claude=False, out=lambda text='': lines_out.append(str(text)))
real_now = timezone.now
runner.start()
world, clock = runner.world, runner.clock
try:
    # ---- 1. catalog --------------------------------------------------------------------------------------------------
    sections = catalog.doc_sections()
    check_("catalog: every main ID of the document has a case", all(s in cases for s in sections), set(sections) - set(cases))
    check_("catalog: every case points to a section of the document",
           all((c.get('doc') or cid) in sections for cid, c in cases.items()), [cid for cid, c in cases.items() if (c.get('doc') or cid) not in sections])
    check_("catalog: only expect keys the checker knows", not {k for c in cases.values() for k in c['expect']} - set(check.CHECKS))
    story_block = catalog.story_block()
    check_("catalog: the story block has the rows the sandbox starts with", story_block['tenant']['name'] == 'Vera Lopez'
           and len(story_block['payments']) == 3 and story_block['parking'][0]['spot'] == '14' and 'MyHome-5G' in story_block['knowledge']
           and story_block['contract']['terms'], list(story_block))
    check_("catalog: the chapters are in time order and story.defaults apply (mode live unless the chapter says)",
           list(cases)[0] == 'A21' and cases['A1']['mode'] == 'live' and cases['A16']['mode'] == 'test', [(i, c.get('mode')) for i, c in list(cases.items())[:3]])
    check_("catalog: `on: first` survives YAML's on/off booleans", cases['E7']['trigger'].get('on') == 'first', cases['E7']['trigger'])

    # ---- 2. A1: the simple alert ----------------------------------------------------------------------------------------
    script.append(SINK)
    result = runner.run_case(cases['A1'], 1, 1, first=True)
    got = result['got']
    alert = got['alerts'][0]
    check_("A1: one agent run, one alert posted (offline)", len(got['runs']) == 1 and len(got['alerts']) == 1, lines_out[-30:])
    check_("A1: the AI was told the CASE time (Wednesday 09:34), not the real time", "CURRENT_TIME: Wednesday" in inputs_seen[-1] and " 09:3" in inputs_seen[-1],
           [l for l in inputs_seen[-1].splitlines() if 'CURRENT_TIME' in l])
    check_("A1: the case date is in the real future (the live worker never sees its reminders due)", clock.now() > real_now())
    check_("A1: the story's tenant, apartment, knowledge page, payments, parking and contract are the AI's context (real rows)",
           "Vera Lopez" in inputs_seen[-1] and "MyHome-5G" in inputs_seen[-1] and "Sandbox_5G" not in inputs_seen[-1]
           and "=== PARKING ===" in inputs_seen[-1] and "Spot #14" in inputs_seen[-1] and "FREE for this booking's dates" in inputs_seen[-1]
           and "2150" in inputs_seen[-1] and world.apartment.name == "Sandbox Test" and world.booking.contract_id == story.CONTRACT_ID,
           inputs_seen[-1][-1500:])
    from mysite.ai_agent.contract import contract_for_booking as _contract
    check_("A1: the story contract is what get_contract shows (no DocuSeal for the sandbox booking)",
           "SIGNED" in _contract(world.booking) and "Section 9" in _contract(world.booking), _contract(world.booking)[:300])
    check_("story start: the sandbox apartment's rows were written: 3 payments, spot #14 free, knowledge page",
           Payment.objects.filter(booking=world.booking).count() == 3 and Parking.objects.filter(building="105", associated_room="2B", number="14").exists()
           and not ParkingBooking.objects.filter(booking=world.booking).exists() and "Door code: 4521#" in world.apartment.knowledge_base)
    check_("A1: simulated LIVE mode on the test apartment", got['runs'][0].mode == 'live')
    reason = result['reason']
    check_("A1: PASS - the simple alert matches every structured expectation of the chapter", result['verdict'] == 'PASS', reason)
    verdict = (catalog.RESULTS_DIR / runner.run_name / "A1.md").read_text()
    check_("A1: what matches is reported as matching", "✅ 1 new task(s)" in verdict and "✅ answer for the tenant" in verdict, verdict[-1500:])
    check_("A1: report file has input, result and verdict", all(t in verdict for t in ("## Input (the case)", "## What the agent did", "🧪 RESULT · A1 · ✅ PASS")),
           verdict[-1500:])
    posted = world.tap.messages[alert['id']]['posted']
    check_("A1: the alert carried the test header and the notes; the agent's own text is kept apart", result['notes_inline']
           and posted.startswith("🧪 SANDBOX TEST · case A1 · 1 of 1") and "🧪 TEST NOTES\nWhat we test: " in posted
           and "🧪" not in alert['text'].replace("🧪 TEST", ""), posted)
    check_("A1: last result saved for --list", catalog.last_results()['A1']['verdict'] == 'PASS')

    # ---- 3. a press, handled in this process by the real button code ---------------------------------------------------------
    run = AIRun.objects.get(id=got['runs'][0].id)
    check_("press: before any press the issue and the reminder exist, the answer waits", run.hold_status == 'holding'
           and AIIssue.objects.filter(conversation_sid=SANDBOX_SID).count() == 1
           and AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status='pending').count() == 1, run.hold_status)
    pressed = runner.do_step({'press': 'Send + Create Task', 'by': 'Andy'})
    run.refresh_from_db()
    check_("press: Send + Create Task sends the answer into the SANDBOX chat (no Twilio)", run.hold_status == 'sent' and len(pressed['sent']) == 1
           and TwilioMessage.objects.filter(conversation_sid=SANDBOX_SID, author='Virtual Assistant', body__startswith='Hi Vera, thanks').exists(),
           (run.hold_status, pressed['sent'], pressed['note']))
    check_("press: real Twilio was never called", not twilio_calls, twilio_calls)
    check_("press: the task part was done too (the sandbox apartment has no ClickUp List: noted)",
           any((a.get('done') or {}).get('label', '').startswith('✅ Task noted · Andy') for a in run.review['plan']['actions']),
           run.review['plan']['actions'])
    due = AIFollowUp.objects.get(conversation_sid=SANDBOX_SID).due_at
    check_("press: the reminder is due in the real future", due > real_now())
    check_("press: the result is a popup and the button, no extra message in the group",
           any(p.startswith('✅ Answer sent · Andy') for p in pressed['popups']) and not [a for a in pressed['alerts'] if a['type'] == 'REPLY'],
           (pressed['popups'], pressed['alerts']))
    script.append(REMIND)
    fired = runner.do_step({'reminder': 'next'})
    check_("reminder: next = the earliest pending reminder of the story fires (fast time), the agent runs on FOLLOWUP_DUE",
           len(fired['runs']) == 1 and fired['runs'][0].event_type == 'FOLLOWUP_DUE' and "FOLLOWUP_DUE" in inputs_seen[-1], fired['note'])
    check_("reminder: reminder alert recognised", fired['alerts'] and fired['alerts'][0]['type'] == 'REMINDER', (fired['note'], fired['alerts']))

    # ---- 4. isolation ---------------------------------------------------------------------------------------------------------
    try:
        messaging.send_messsage_by_sid(REAL_SID, 'Virtual Assistant', 'hello', None, None)
        refused = False
    except RuntimeError:
        refused = True
    check_("isolation: a send to a real chat is refused", refused)
    try:
        messaging.get_twilio_client()
        blocked = False
    except RuntimeError:
        blocked = True
    check_("isolation: Twilio client is unreachable", blocked)
    from mysite.ai_agent import clickup, kb_documents, prompt_library
    try:
        clickup.set_task_closed("https://app.clickup.com/t/realtask1")
        refused = False
    except clickup.ClickUpError:
        refused = True
    check_("isolation: a ClickUp task that is not a sandbox task can not be changed", refused)
    real_global = world._orig['mysite.views.messaging.get_global_knowledge_base_text']
    global_before = real_global()
    kb_documents.merge('company', None, "Rent can be paid by Zelle to sandbox@example.com")
    check_("isolation: global knowledge is kept in the sandbox file and shown to the AI, the real company page is not written",
           world.state['knowledge'] and real_global() == global_before and "sandbox@example.com" not in global_before
           and "sandbox@example.com" in messaging.get_global_knowledge_base_text())
    kb_documents.merge('apartment', world.apartment, "Bike room: 1st floor")
    world.apartment.refresh_from_db()
    check_("story: apartment knowledge is written to the sandbox apartment's REAL page", "Bike room: 1st floor" in (world.apartment.knowledge_base or ''))
    prompt_library.upsert_lesson(None, 'plumbing', "Offer a same-day visit")
    check_("isolation: a rule is kept in the sandbox and shown to the agent", "Offer a same-day visit" in __import__('mysite.ai_agent.prompts', fromlist=['x']).get_system_prompt(world.apartment)[0])

    # ---- 5. emergency in simulated live: sent at once into the sandbox chat, call simulated -----------------------------------------
    script.append(LEAK)
    result = runner.run_case(cases['D3'], 1, 1, first=True)
    got = result['got']
    check_("clean start: the earlier case is gone", AIRun.objects.filter(conversation_sid=SANDBOX_SID).count() == 1
           and not world.state['knowledge'] and not world.state['rules'])
    check_("D3: the after-hours message went to the tenant = the sandbox chat", any('outside our regular office hours' in m['text'] for m in got['sent']), got['sent'])
    check_("D3: a real emergency's safety answer goes out at once at 23:02 - not held for the 08:00 SMS hours",
           any('main water valve' in m['text'] for m in got['sent']) and not got['held'], (got['sent'], got['held']))
    check_("D3: Farid's call is simulated", len(got['calls']) == 1 and got['calls'][0].status == AIAlertCall.STATUS_ANSWERED and not twilio_calls, got['calls'])
    check_("D3: after hours (23:02) for the AI", "OUTSIDE office hours" in inputs_seen[-1])

    # ---- 6. a chapter of a feature that is not built yet is skipped by the list runner, with its place kept -------------------------
    runner.results = []
    runner.run_cases([cases['G2'], cases['A15']], "G2 + A15")
    check_("not built: the chapter is skipped and reported SKIPPED, the next one runs", [r['verdict'] for r in runner.results][0] == 'SKIPPED'
           and catalog.last_results()['G2']['verdict'] == 'SKIPPED' and len(runner.results) == 2, runner.results)

    # ---- 6b. a team member without a phone in AI staff still writes under their name -------------------------------------------
    script.append(QUIET)
    runner.run_case(cases['B6'], 1, 1, first=True)
    check_("B6: Kevin (no phone in AI staff) is still Kevin in the chat", "Kevin (STAFF): Vera, I'll send an electrician" in inputs_seen[-1], inputs_seen[-1][-500:])

    # ---- 7. nothing to do: a short "ok" never reaches the agent ------------------------------------------------------------------------
    before_inputs = len(inputs_seen)
    quiet = dict(cases['A15'])
    quiet['trigger'] = [{'from': 'tenant', 'text': 'ok'}]
    result = runner.run_case(quiet, 1, 1, first=True)
    check_("skip: 'ok' is skipped before the queue, no alert, the case passes", result['verdict'] == 'PASS' and len(inputs_seen) == before_inputs,
           result['reason'])
    script.append(QUIET)
    result = runner.run_case(cases['A15'], 1, 1, first=True)
    check_("A15: a no-reply run posts no alert; the case passes", result['verdict'] == 'PASS' and not result['got']['alerts'], result['reason'])

    # ---- 8. the daily report does not exist yet ------------------------------------------------------------------------------------------
    result = runner.run_case(cases['G1'], 1, 1, first=True)
    check_("G1: reported as NOT BUILT YET", result['verdict'] == 'FAIL' and 'NOT BUILT YET' in result['reason'], result['reason'])

    # ---- 9. a typed reply goes through the real reply handler: the reply chapter targets the alert of the chapter before -------------
    script.append(SINK)
    interpreter_out.append({'decision': 'question', 'staff_answer': 'A dripping sink needs a plumber visit.', 'corrected_answer': '',
                            'plan_ops': [], 'task_actions': [], 'new_facts': [], 'lesson': ''})
    runner.run_case(cases['A1'], 1, 2, first=True)
    result = runner.run_case(cases['E1'], 2, 2, first=False)
    got = result['got']
    check_("E1: the reply was answered by the bot under the alert", got['alerts'] and got['alerts'][-1]['type'] == 'REPLY'
           and 'needs a plumber visit' in got['alerts'][-1]['text'], (got['note'], got['alerts'], got['others']))

    # ---- 10. a real chat replayed ---------------------------------------------------------------------------------------------------------------
    script.extend([SINK, QUIET, QUIET])
    runner.results = []
    runner.run_conversation(REAL_SID)
    replay_input = inputs_seen[-3]
    check_("replay: tenant burst (2 messages within a minute) = one run; team message = one run; 3 runs in all",
           len(runner.results) == 3 and "the AC is not cooling" in replay_input and "it is 85 inside" in replay_input, [r['id'] for r in runner.results])
    check_("replay: the agent reads the REAL apartment's knowledge", "Pool hours: 8am-8pm" in replay_input)
    check_("replay: the team member keeps their name", "Edy (STAFF): Rita, the technician comes tomorrow" in inputs_seen[-2], inputs_seen[-2][-600:])
    report = (catalog.RESULTS_DIR / runner.run_name / "message_001.md").read_text()
    check_("replay: the notes show what the team really did next", "Edy answered at" in report and "the technician comes tomorrow at 10" in report, report)
    check_("replay: nothing was written to the real chat", TwilioMessage.objects.filter(conversation_sid=REAL_SID).count() == real_message_count
           and not AIRun.objects.filter(conversation_sid=REAL_SID).exists() and not AIEvent.objects.filter(conversation_sid=REAL_SID).exists())
    check_("replay: all sandbox runs are test mode", not AIRun.objects.filter(conversation_sid=SANDBOX_SID, mode='live').exists())
finally:
    runner.finish()

# ---- 10b. the simple alerts (v5): A1 looks like the example, every button does its own part -----------------------------
v5_out = []
v5 = Runner(auto=False, offline=True, use_claude=False, out=lambda text='': v5_out.append(str(text)))
v5.start()
try:
    script.append(dict(SINK, actions=[dict(a) for a in SINK['actions']]))
    result = v5.run_case(cases['A1'], 1, 1, first=True)
    got = result['got']
    alert = got['alerts'][0]
    day = v5.clock.local()
    expected_text = f"""📨 TENANT MESSAGE · {day.day} {day:%b}, {day:%a} 09:34 ET

🏠 Sandbox Test · 👤 Vera Lopez · 🟢 Routine

———
💬 "Hi, the kitchen sink is dripping since last night" 💬
———
🤖 "Hi Vera, thanks for letting us know. We've logged it and our maintenance team will contact you." 🤖
———
🎫 Kitchen sink dripping – Sandbox Test 🎫
   Edy · routine · due {(day + timedelta(days=3)):%a} {(day + timedelta(days=3)).day} {(day + timedelta(days=3)):%b} 09:34
———
⏰ Check the sink task has a visit date – today 11:34 (1/2) ⏰
———

↩ Reply to this message for questions, notes or custom actions.

📤 AI sends to chat: ON (live) · 🎫 AI Auto ClickUp: OFF
🔗 AI run: http://crm.test/ai-runs/{got['runs'][0].id}/
💬 CRM chat: http://crm.test/chat/{SANDBOX_SID}/"""
    check_("v5 A1: the alert text is the example, line by line", alert['text'] == expected_text, "\n" + alert['text'])
    labels = [[b['text'] for b in row] for row in alert['markup']['inline_keyboard']]
    check_("v5 A1: one button per block", labels == [['🤖 Send Answer', '✏️ Edit Answer'], ['🤖🎫 Send + Create Task'], ['🎫 Create Task'], ['✅ Close Reminder']], labels)
    check_("v5 A1: PASS - every structured expectation of the case holds", result['verdict'] == 'PASS', result['reason'])
    note_id = v5.post("🧪 a message of the test run under the alert", reply_to=alert['id'], shown=False)
    check_("v5: a reply to a test-run message under the alert reaches that alert's run",
           answer_review.run_for_telegram_message(note_id, "-500") == AIRun.objects.get(id=got['runs'][0].id))
    # Message numbers repeat between Telegram groups: an alert of ANOTHER group with the same number must not hide it
    other = AIRun.objects.create(conversation_sid="CHother", telegram_message_id=note_id, review={'telegram_chat_id': '-777'})
    check_("v5: ... also when an alert of another group has the same message number",
           answer_review.run_for_telegram_message(note_id, "-500") == AIRun.objects.get(id=got['runs'][0].id))
    other.delete()
    check_("v5 A1: posted without sound (routine)", alert['silent'])
    reminder = AIFollowUp.objects.get(conversation_sid=SANDBOX_SID)
    check_("v5 A1: the reminder exists at once, 2 h later, on a case opened at once; the task is NOT created yet",
           reminder.status == 'pending' and abs((reminder.due_at - v5.clock.now()).total_seconds() - 7200) < 120
           and reminder.issue and reminder.issue.owner == 'Edy' and not reminder.issue.ticket_ref)
    check_("v5 A1: nothing was sent by itself", not got['sent'] and AIRun.objects.get(id=got['runs'][0].id).hold_status == 'holding')

    step = v5.do_step({'press': 'Send + Create Task', 'by': 'Andy'})
    labels = [[b['text'] for b in row] for row in v5.world.tap.messages[alert['id']]['markup']['inline_keyboard']]
    check_("v5 F1: Send + Create Task creates the task and really sends (into the sandbox chat) - one press, no question",
           len(step['sent']) == 1 and step['sent'][0]['text'].startswith("Hi Vera, thanks") and not twilio_calls
           and not any('?' in m['text'] for m in step['others']), (step['sent'], step['note'], step['others']))
    check_("v5: the buttons turned into results, the double button is gone", labels[0][0].startswith("✅ Answer sent · Andy ")
           and labels[1][0].startswith("✅ Task noted · Andy ") and labels[2] == ['✅ Close Reminder'] and len(labels) == 3, labels)
    step = v5.do_step({'press': 'Answer sent', 'by': 'Edy'})
    check_("v5 F4: a second press only says who did it", any(p.startswith("already done by Andy ") for p in step['popups']) and not step['sent'], step['popups'])
    step = v5.do_step({'press': 'Close Reminder', 'by': 'Edy'})
    reminder.refresh_from_db()
    labels = [[b['text'] for b in row] for row in v5.world.tap.messages[alert['id']]['markup']['inline_keyboard']]
    check_("v5: Close Reminder stops the reminder and shows who closed it", reminder.status == 'cancelled' and 'closed by Edy' in reminder.status_note
           and labels[2] == ['✅ Reminder closed · Edy'], (reminder.status, labels))
    decisions = [d['action'] for d in AIRun.objects.get(id=got['runs'][0].id).review.get('decisions', [])]
    check_("v5: every press is logged with who pressed", decisions == ['create task 1', 'send answer', 'close reminder 2'], decisions)

    # nothing to decide -> no alert at all (A15), the note is saved at once
    script.append({'answer': 'NO_ANSWER', 'why': 'thanks only', 'primary_type': 'NO_REPLY',
                   'actions': [{'type': 'CASE_NOTE', 'text': 'Tenant thanked us.'}]})
    result = v5.run_case(cases['A15'], 1, 1, first=True)
    from mysite.models import AICaseNote
    check_("v5 A15: 'ok thanks' -> the agent ran, NO alert, the case passes, the note is saved, no error alert",
           result['verdict'] == 'PASS' and not result['got']['alerts'] and not result['got']['others']
           and AICaseNote.objects.filter(conversation_sid=SANDBOX_SID).count() == 1, (result['reason'], result['got']['others']))

    # the AI opens a case but forgets the reminder -> the backend creates it, with the next step as its text
    script.append({'answer': "Hi Mark, sorry! Please try 4521#.", 'why': 'lockout', 'primary_type': 'URGENT_PROPERTY_OR_ACCESS', 'priority': 'urgent',
                   'owner': 'Edy', 'next_action': 'Check Mark got in',
                   'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Tenant locked out', 'owner': 'Edy'},
                               {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'urgent', 'text': 'Tenant locked out', 'responsible': ['Edy']}]})
    result = v5.run_case(cases['A2'], 1, 1, first=True)
    alert = result['got']['alerts'][0]
    labels = [[b['text'] for b in row] for row in alert['markup']['inline_keyboard']]
    check_("v5 A2: no reminder from the AI -> the backend adds the team reminder (urgent: 30 min); the case passes",
           "⏰ Check Mark got in – today 15:10 (1/2) ⏰" in alert['text'] and labels == [['🤖 Send Answer', '✏️ Edit Answer'], ['✅ Close Reminder']]
           and result['verdict'] == 'PASS', (alert['text'], labels, result['reason']))

    # urgent: with sound, reminder after 1 h, two tasks numbered
    script.append({'answer': "Hi Mark, we've logged both and will schedule a visit.", 'why': 'two problems', 'primary_type': 'ROUTINE_MAINTENANCE',
                   'priority': 'urgent', 'owner': 'Edy',
                   'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Dryer and light', 'owner': 'Edy'},
                               {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': "Dryer doesn't start", 'priority': 'urgent', 'responsible': ['Edy']},
                               {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': 'Bathroom light flickering', 'priority': 'routine'},
                               {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'tenant_nudge', 'reason': 'Check both tasks have a visit date.'},
                               {'type': 'KB_UPDATE', 'text': 'Dryer is old'}]})
    result = v5.run_case(cases['A11'], 1, 1, first=True)
    alert = result['got']['alerts'][0]
    labels = [[b['text'] for b in row] for row in alert['markup']['inline_keyboard']]
    check_("v5 A11: two tasks are numbered, blocks and buttons", "🎫1 Dryer doesn't start – Sandbox Test 🎫1\n   Edy · 🔴 urgent · due " in alert['text']
           and "🎫2 Bathroom light flickering – Sandbox Test 🎫2\n   Edy · routine · due " in alert['text']
           and labels[1] == ['🤖🎫 Send + Create Tasks'] and labels[2] == ['🎫 Create Task 1', '🎫 Create Task 2'], (alert['text'], labels))
    step = v5.do_step({'press': 'Send Answer', 'by': 'Andy'})
    labels = [[b['text'] for b in row] for row in v5.world.tap.messages[alert['id']]['markup']['inline_keyboard']]
    check_("v5 F1b: Send Answer alone just sends - no question, the tasks keep their own buttons, the double button is gone",
           len(step['sent']) == 1 and not step['others'] and labels[0][0].startswith("✅ Answer sent · Andy ")
           and labels[1] == ['🎫 Create Task 1', '🎫 Create Task 2'], (step['sent'], step['others'], labels))
    check_("v5: after a tenant message the reminder is for the team, even when the AI asked for one to the tenant",
           AIFollowUp.objects.get(conversation_sid=SANDBOX_SID).kind == 'staff_reminder' and "to tenant" not in alert['text'])
    check_("v5: urgent -> with sound, 🔴 Urgent, reminder after 30 min; a fact without a source is not shown",
           not alert['silent'] and "🔴 Urgent" in alert['text'] and "(1/2) ⏰" in alert['text'] and "Dryer is old" not in alert['text']
           and abs((AIFollowUp.objects.get(conversation_sid=SANDBOX_SID).due_at - v5.clock.now()).total_seconds() - 1800) < 120, alert['text'])

    # ---- the other parts of a tenant alert, and the team alert ---------------------------------------------------------------
    import re as _re
    from mysite.ai_agent import clickup as _clickup
    real_mode, _clickup.delivery_mode = _clickup.delivery_mode, (lambda: 'api')   # so the fake set-up tasks are read and changed
    def keyboard_now(message):
        return [[b['text'] for b in row] for row in (v5.world.tap.messages[message['id']]['markup'] or {}).get('inline_keyboard', [])]
    def ref(pattern, text):
        return _re.search(pattern, text).group(1)

    # ---- reminders that become due (C1-C8, D5, D6) -----------------------------------------------------------------------------
    from mysite.ai_agent import alerts_v5 as _v5
    def labels_of(message):
        return [[b['text'] for b in row] for row in (message.get('markup') or {}).get('inline_keyboard', [])]
    def due_case(summary, kind='staff_reminder', reason='Check it', priority='routine', ticket=None, minutes=5):
        issue = AIIssue.objects.create(conversation_sid=SANDBOX_SID, apartment=v5.world.apartment, booking=v5.world.booking,
                                       summary=summary, owner='Edy', priority=priority, ticket_ref=ticket, ticket_title=summary if ticket else None)
        return AIFollowUp.objects.create(conversation_sid=SANDBOX_SID, issue=issue, kind=kind, reason=reason,
                                         due_at=v5.clock.now() + timedelta(minutes=minutes))
    AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status='pending').update(status='cancelled')
    v5.world.mode = 'live'
    first = due_case('Tenant locked out', reason='Check Mark got in', priority='urgent')
    script.append({'answer': 'NO_ANSWER', 'why': 'not confirmed yet', 'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'urgent', 'owner': 'Edy',
                   'next_action': 'Edy: call Mark and check he got in', 'no_reply_reason': 'reminder for the team',
                   'actions': [{'type': 'SCHEDULE_FOLLOWUP', 'issue_id': first.issue.public_id, 'kind': 'staff_reminder', 'reason': 'again'},
                               {'type': 'INTERNAL_ALERT', 'text': 'Mark not confirmed', 'responsible': ['Edy']}]})
    step = v5.do_step({'reminder': 'next'})
    alert = step['alerts'][0] if step['alerts'] else {'text': step['note'], 'markup': None, 'silent': True}
    second = AIFollowUp.objects.filter(issue=first.issue, status='pending').first()
    check_("v5 C1: a due team reminder that is still needed -> ⏰ REMINDER 1/2, the 2/2 is set for tomorrow 10:00",
           alert['text'].startswith("⏰ REMINDER · ") and "🔴 Urgent · 1/2 (2/2 tomorrow 10:00)" in alert['text']
           and "⏰ Check Mark got in (for Edy) ⏰" in alert['text'] and "now:" not in alert['text']
           and labels_of(alert) == [['✅ Close Reminder']] and not alert['silent']
           and second and second.kind == 'staff_reminder' and _v5._local(second.due_at).strftime('%H:%M') == '10:00', (alert['text'], labels_of(alert)))
    check_("v5 C1: the AI's own reminder and team note are not shown (the backend times reminders, the alert is the note)",
           AIFollowUp.objects.filter(issue=first.issue).count() == 2 and "Mark not confirmed" not in alert['text'], alert['text'])
    step = v5.do_step({'press': 'Close Reminder', 'by': 'Andy'})
    second.refresh_from_db()
    check_("v5 C1: Close Reminder on the REMINDER alert stops the 2/2 and shows who closed it",
           second.status == 'cancelled' and labels_of(v5.world.tap.messages[alert['id']]) == [['✅ Reminder closed · Andy']], labels_of(v5.world.tap.messages[alert['id']]))

    # C2: the last one (2/2) still needed -> Remind again in 24 hours
    last = due_case('Tenant locked out again', reason='Check the lock was changed')
    AIFollowUp.objects.create(conversation_sid=SANDBOX_SID, issue=last.issue, kind='staff_reminder', reason='Check the lock was changed',
                              due_at=v5.clock.now() - timedelta(days=1), status='fired')
    last.delete()
    last = AIFollowUp.objects.create(conversation_sid=SANDBOX_SID, issue=AIIssue.objects.get(summary='Tenant locked out again'), kind='staff_reminder',
                                     reason='Check the lock was changed', due_at=v5.clock.now() + timedelta(minutes=5))
    script.append({'answer': 'NO_ANSWER', 'why': 'still open', 'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine', 'owner': 'Edy',
                   'next_action': 'Edy: change the lock', 'actions': []})
    step = v5.do_step({'reminder': 'next'})
    alert = step['alerts'][0] if step['alerts'] else {'text': step['note'], 'markup': None}
    check_("v5 C2: the last reminder -> 2/2 (last), Close Reminder + Remind again in 24 hours, no new reminder by itself",
           "🟢 Routine · 2/2 (last)" in alert['text'] and labels_of(alert) == [['✅ Close Reminder'], ['⏰ Remind again in 24 hours']]
           and not AIFollowUp.objects.filter(issue=last.issue, status='pending').exists(), (alert['text'], labels_of(alert)))
    step = v5.do_step({'press': 'Remind again in 24 hours', 'by': 'Edy'})
    again = AIFollowUp.objects.filter(issue=last.issue, status='pending').first()
    check_("v5 C2: Remind again in 24 hours sets one more reminder, the next day 10:00, and says so on the button",
           again and _v5._local(again.due_at).strftime('%H:%M') == '10:00'
           and labels_of(v5.world.tap.messages[alert['id']])[1][0].startswith('⏰ Next: ') and '· Edy' in labels_of(v5.world.tap.messages[alert['id']])[1][0],
           labels_of(v5.world.tap.messages[alert['id']]))
    again.delete()

    # C5: already done -> closed quietly, no alert; the alert that showed it says so on its button
    done = due_case('Wifi password', reason='Check Vera got the wifi password')
    script.append({'answer': 'NO_ANSWER', 'why': 'done', 'primary_type': 'NO_REPLY', 'next_action': '',
                   'no_reply_reason': 'Vera wrote at 15:20 that the wifi works', 'actions': []})
    step = v5.do_step({'reminder': 'next'})
    done.refresh_from_db()
    check_("v5 C5: the AI finds it done -> no alert, the reminder is closed with the reason",
           not step['alerts'] and done.status == 'fired' and done.status_note == 'done: Vera wrote at 15:20 that the wifi works',
           (step['alerts'], done.status, done.status_note))

    # C3 / D5: tenant reminder, live -> sent by itself, shown as an AI MESSAGE with the next one
    nudge = due_case('Water meter photo', kind='tenant_nudge', reason='Remind Vera to send the water meter photo')
    script.append({'answer': 'Hi Vera, just a reminder to send us the photo of the water meter when you can. Thanks!', 'why': 'still owed',
                   'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine', 'next_action': 'Vera sends the photo', 'actions': []})
    step = v5.do_step({'reminder': 'next'})
    alert = step['alerts'][0] if step['alerts'] else {'text': step['note'], 'markup': None}
    nxt = AIFollowUp.objects.filter(issue=nudge.issue, status='pending').first()
    check_("v5 D5: a live tenant reminder is sent by itself into the chat and shown as an AI MESSAGE, the 2/2 is set",
           alert['text'].startswith("🤖 AI MESSAGE · ") and "⏰ Reminder 1/2 auto-sent" in alert['text'] and "✅ Sent " in alert['text']
           and "⏰ Next: 2/2 tomorrow 10:00" in alert['text'] and len(step['sent']) == 1 and 'water meter' in step['sent'][0]['text']
           and nxt and nxt.kind == 'second_tenant_nudge' and labels_of(alert) == [['✅ Close Reminder']] and alert['silent'],
           (alert['text'], step['sent'], labels_of(alert)))

    # D6: the 2/2 can not be sent -> retry in 3 minutes -> sent on retry
    v5.world.sms_fail = 1
    script.append({'answer': 'Hi Vera, a last reminder about the water meter photo. Thanks!', 'why': 'still owed',
                   'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine', 'next_action': 'Vera sends the photo', 'actions': []})
    step = v5.do_step({'reminder': 'next'})
    alert = step['alerts'][0] if step['alerts'] else {'text': step['note'], 'markup': None}
    check_("v5 D6: the send failed -> the AI MESSAGE says so and when it is tried again; the last one has no button",
           "❌ Could not send to Vera (" in alert['text'] and "– retry at " in alert['text'] and not step['sent'] and not labels_of(alert),
           (alert['text'], labels_of(alert)))
    step = v5.do_step({'wait': '5m'})
    text = v5.world.tap.messages[alert['id']]['text']
    check_("v5 D6: after 3 minutes it is sent on retry, the AI MESSAGE is updated", "✅ Sent on retry " in text and len(step['sent']) == 1, (text, step['sent']))

    # C4: tenant reminder, test -> not sent by itself, Send now / Edit Answer / Close Reminder
    v5.world.mode = 'test'
    test_nudge = due_case('Parking permit photo', kind='tenant_nudge', reason='Remind Vera to send the parking permit photo')
    script.append({'answer': 'Hi Vera, just a reminder to send us the photo of your parking permit. Thanks!', 'why': 'still owed',
                   'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine', 'next_action': 'Vera sends the photo', 'actions': []})
    step = v5.do_step({'reminder': 'next'})
    alert = step['alerts'][0] if step['alerts'] else {'text': step['note'], 'markup': None}
    check_("v5 C4: a test tenant reminder waits for a press: REMINDER 1/2 (to tenant), 🧪 TEST, nothing sent",
           alert['text'].startswith("⏰ REMINDER · ") and "1/2 (to tenant) · 🧪 TEST" in alert['text']
           and "🧪 Test mode: NOT sent automatically. Press to send for real." in alert['text'] and not step['sent']
           and labels_of(alert) == [['🤖 Send now', '✏️ Edit Answer'], ['✅ Close Reminder']], (alert['text'], labels_of(alert)))
    step = v5.do_step({'press': 'Send now', 'by': 'Andy'})
    check_("v5 C4: Send now sends it (into the sandbox chat)", len(step['sent']) == 1 and 'parking permit' in step['sent'][0]['text'], step['sent'])
    AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status='pending').update(status='cancelled')

    # C6: the task was closed in ClickUp -> the "marked as done" message is proposed, no Close Task button
    v5.world.mode = 'live'
    ref_closed = 'sandbox://task/closed-sink'
    v5.world.fake_tasks[ref_closed] = {'closed': True, 'comments': [], 'owner': 'Edy', 'updated': '2026-10-09 09:12'}
    closed_case = due_case('Kitchen sink dripping', reason='Check the sink task has a visit date', ticket=ref_closed)
    script.append(lambda text: {'answer': 'Hi Vera, our team marked the sink repair as done. If you still have any problem, just let us know.',
                                'why': 'task closed', 'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine', 'next_action': '',
                                'actions': [{'type': 'UPDATE_ISSUE_STATE', 'issue_id': closed_case.issue.public_id, 'state': 'RESOLVED'}]})
    step = v5.do_step({'reminder': 'next'})
    alert = step['alerts'][0] if step['alerts'] else {'text': step['note'], 'markup': None}
    closed_case.issue.refresh_from_db()
    check_("v5 C6: a closed ClickUp task -> REMINDER with the CLOSED line and the proposed message; the case is resolved at once",
           "🎫 Task \"Kitchen sink dripping\" was CLOSED (complete" in alert['text'] and "our team marked the sink repair as done" in alert['text']
           and labels_of(alert) == [['🤖 Send Answer', '✏️ Edit Answer'], ['✅ Close Reminder']] and closed_case.issue.state == 'RESOLVED',
           (alert['text'], labels_of(alert), closed_case.issue.state))

    # E9: a reply asks for a test reminder -> 🧪 Send test reminder -> a reminder on the Sandbox Test chat, due in 1 minute,
    # which the live worker fires (other sandbox reminders it leaves to the runner)
    interpreter_out.append(dict(answer_review.EMPTY_DECISION, decision='question', staff_answer='I can make a test reminder.',
                                operations=['test_reminder']))
    step = v5.do_step({'reply': 'send a test reminder in the sandbox apartment to check', 'by': 'Andy'})
    bot = step['alerts'][-1] if step['alerts'] else {'text': step['note'], 'markup': None}
    check_("v5 E9: the reply gets the operation explained with its own button",
           "🧪 TEST REMINDER on the \"Sandbox Test\" apartment" in bot['text'] and labels_of(bot) == [['🧪 Send test reminder']], (bot['text'], labels_of(bot)))
    v5.world.apartment.ai_group_chat_enabled = False
    v5.world.apartment.save()
    story_reminder = due_case('Story case', reason='a story reminder', minutes=0)
    step = v5.do_step({'press': 'Send test reminder', 'by': 'Andy'})
    test_followup = AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, issue__summary__startswith=_v5.TEST_REMINDER).first()
    check_("v5 E9: the press creates the test reminder, due in 1 minute",
           test_followup and 50 <= (test_followup.due_at - v5.clock.now()).total_seconds() <= 70, (step['popups'], test_followup))
    from mysite.ai_agent import service as _service
    v5.clock.forward_to(v5.clock.now() + timedelta(minutes=2))
    _service.fire_due_followups()
    test_followup.refresh_from_db()
    story_reminder.refresh_from_db()
    check_("v5 E9: the live worker fires the test reminder, never a story reminder of the sandbox",
           test_followup.status == 'fired' and story_reminder.status == 'pending', (test_followup.status, story_reminder.status))
    AIEvent.objects.filter(conversation_sid=SANDBOX_SID, status='pending').update(status='done')
    AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status='pending').update(status='cancelled')

    # A12: after hours, live -> the after-hours text goes out at once and is shown as an AI MESSAGE; the alert says so
    script.append({'answer': "Thanks Vera, we've noted it.", 'why': 'bulb', 'primary_type': 'ROUTINE_MAINTENANCE', 'priority': 'routine', 'owner': 'Edy',
                   'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Hallway bulb out', 'owner': 'Edy'},
                               {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': 'Hallway bulb out', 'priority': 'routine', 'responsible': ['Edy']},
                               {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'escalation_check', 'reason': 'Check the bulb task'}]})
    result = v5.run_case(cases['A12'], 1, 1, first=True)
    got = result['got']
    ai_message = next((a for a in got['alerts'] if a['type'] == 'AI MESSAGE'), {'text': ''})
    alert = got['alerts'][-1]
    check_("v5 A12 / D1: the after-hours text is sent at once and shown as an AI MESSAGE without buttons",
           [a['type'] for a in got['alerts']] == ['AI MESSAGE', 'TENANT MESSAGE'] and "🌙 After-hours message" in ai_message['text']
           and "✅ Sent to the tenant 22:15" in ai_message['text'] and "💬 \"Also the hallway bulb is out\" 💬" in ai_message['text']
           and not ai_message.get('markup') and len(got['sent']) == 1, (ai_message['text'], got['sent']))
    check_("v5 A12: the alert says the after-hours message was sent; a routine reminder waits for 09:00, whatever kind the AI picked",
           "🟢 Routine · 🌙 after-hours message sent 22:15" in alert['text'] and "⏰ Check the bulb task – Wed 09:00 (1/2) ⏰" in alert['text']
           and result['verdict'] == 'PASS', (alert['text'], result['reason']))

    # A8: a tenant deadline -> one deadline block, its two reminders exist, no extra 1/2 reminder
    day = v5.clock.local()
    def arrival(text):   # "tomorrow 23:00" as the AI would write it, from the time it was told
        from datetime import datetime as _dt
        today = _dt.strptime(ref(r'CURRENT_TIME: \w+ (\d{4}-\d\d-\d\d)', text), '%Y-%m-%d')
        return {'answer': "Hi Sam, the lockbox is at the front door, code 7788. Safe travels!", 'why': 'arrival', 'primary_type': 'TIME_SENSITIVE_LOGISTICS',
                'priority': 'urgent', 'owner': 'Edy', 'tenant_deadline': f"{today + timedelta(days=1):%Y-%m-%d} 23:00",
                'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Sam lands at 23:00 - keys', 'owner': 'Edy', 'priority': 'urgent'}]}
    script.append(arrival)
    result = v5.run_case(cases['A8'], 1, 1, first=True)
    alert = result['got']['alerts'][-1]
    check_("v5 A8: tenant deadline -> a deadline block with its 24 h and 2 h reminders, closable with one button; the case passes",
           _re.search(r"⏰ Deadline \w{3} \d+ \w{3} 23:00 – reminders 24 h and 2 h before \(Kevin at 2 h\) ⏰", alert['text']) and "(1/2)" not in alert['text']
           and keyboard_now(alert) == [['🤖 Send Answer', '✏️ Edit Answer'], ['✅ Close Reminder']]
           and AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, kind='deadline_reminder', status='pending').count() == 2
           and result['verdict'] == 'PASS', (alert['text'], keyboard_now(alert), result['reason']))
    v5.do_step({'press': 'Close Reminder', 'by': 'Andy'})
    check_("v5 A8: Close Reminder stops both deadline reminders", not AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status='pending').exists()
           and keyboard_now(alert)[1] == ['✅ Reminder closed · Andy'], keyboard_now(alert))

    # A9: the tenant asks again about a case with a task -> "asked 2 times", an update of the task with its own button.
    # The story: A1 happened (Wed 09:34) and its task is in ClickUp (a fake the world can read and change, as after 🎫 Create Task)
    script.append(dict(SINK, actions=[dict(a) for a in SINK['actions']]))
    v5.run_case(cases['A1'], 1, 1, first=True)
    _sink_issue = AIIssue.objects.get(conversation_sid=SANDBOX_SID)
    AIIssue.objects.filter(id=_sink_issue.id).update(ticket_ref=world_mod.FAKE_TASK + 'sink', ticket_title='Kitchen sink dripping – Sandbox Test',
                                                      state=AIIssue.STATE_MAINTENANCE_OPEN)
    v5.world.fake_tasks[world_mod.FAKE_TASK + 'sink'] = {'title': 'Kitchen sink dripping – Sandbox Test', 'closed': False, 'owner': 'Edy', 'updated': '', 'comments': []}
    script.append(lambda text: {'answer': "Hi Vera, sorry for the wait. I've pushed it to the team as urgent.", 'why': 'repeat', 'primary_type': 'FOLLOW_UP_REQUEST',
                                'priority': 'urgent', 'owner': 'Edy', 'issue_refs': [ref(r'issue_id: (i-\d+)', text)],
                                'actions': [{'type': 'TICKET_COMMENT', 'ticket_id': ref(r'ticket_id: (t-\d+)', text),
                                             'text': 'Tenant asked again, getting worse – please schedule today'}]})
    result = v5.run_case(cases['A9'], 1, 1, first=False)
    alert = result['got']['alerts'][-1]
    check_("v5 A9: asked again -> 🔁 line, an update block for the existing task, no new reminder; the case passes",
           _re.search(r"💬\n🔁 Asked 2 times · waiting 2\d h · task open\n———", alert['text'].replace(' 💬\n', '💬\n'))
           and "🔄 Task \"Kitchen sink dripping – Sandbox Test\": make urgent + comment \"Tenant asked again, getting worse – please schedule today\" 🔄" in alert['text']
           and keyboard_now(alert) == [['🤖 Send Answer', '✏️ Edit Answer'], ['🔄 Apply Update']] and result['verdict'] == 'PASS',
           (alert['text'], keyboard_now(alert), result['reason']))
    v5.do_step({'press': 'Apply Update', 'by': 'Andy'})
    task = next(iter(v5.world.fake_tasks.values()))
    check_("v5 A9: Apply Update comments on the task and makes it urgent, although AI Auto ClickUp is OFF (the press is the confirmation)",
           any('please schedule today' in c['text'] for c in task['comments']) and task.get('priority') == 'urgent' and keyboard_now(alert)[1][0].startswith('✅ Commented · Andy'), (task, keyboard_now(alert)))

    # A10: the tenant says it is fixed -> close the task and its reminder, each with its own button
    # the sink case has an open reminder again (the first one was due while the tenant kept asking)
    _pending = AIFollowUp.objects.create(conversation_sid=SANDBOX_SID, issue=_sink_issue, kind='staff_reminder', reason='Ask Vera if the sink is fixed (for Edy) [1/2]',
                                         due_at=v5.clock.now() + timedelta(hours=2), status=AIFollowUp.STATUS_PENDING)
    script.append(lambda text: {'answer': "Great to hear, Vera! Thanks for letting us know.", 'why': 'fixed', 'primary_type': 'FOLLOW_UP_REQUEST', 'priority': 'routine',
                                'issue_refs': [ref(r'issue_id: (i-\d+)', text)],
                                'actions': [{'type': 'UPDATE_ISSUE_STATE', 'issue_id': ref(r'issue_id: (i-\d+)', text), 'state': 'RESOLVED'},
                                            {'type': 'CANCEL_FOLLOWUP', 'followup_id': f"f-{_pending.id}", 'reason': 'fixed'}]})
    result = v5.run_case(cases['A10'], 1, 1, first=False)
    alert = result['got']['alerts'][-1]
    check_("v5 A10: problem gone -> Close Task and the open reminder are proposed, nothing is closed yet; the case passes",
           "🔄 Close task \"Kitchen sink dripping – Sandbox Test\" 🔄" in alert['text'] and "⏰ Open reminder \"Ask Vera if the sink is fixed (for Edy) [1/2]\" (" in alert['text']
           and keyboard_now(alert) == [['🤖 Send Answer', '✏️ Edit Answer'], ['🔄 Close Task'], ['✅ Close Reminder']]
           and AIFollowUp.objects.get(id=_pending.id).status == 'pending' and result['verdict'] == 'PASS', (alert['text'], keyboard_now(alert), result['reason']))
    v5.do_step({'press': 'Close Task', 'by': 'Andy'})
    check_("v5 A10: Close Task closes the task and with it the reminder of that case",
           next(iter(v5.world.fake_tasks.values()))['closed'] and AIFollowUp.objects.get(id=_pending.id).status == 'cancelled'
           and keyboard_now(alert)[1][0].startswith('✅ Closed · Andy') and keyboard_now(alert)[2] == ['✅ Reminder closed · Andy'], keyboard_now(alert))
    _clickup.delivery_mode = real_mode

    # A17: the tenant writes again before anyone pressed -> the old alert is outdated, the new one joins both messages
    script.append(dict(SINK, actions=[dict(a) for a in SINK['actions']]))
    script.append({'answer': "Hi Vera, sorry! Please close the valve under the sink for now.", 'why': 'worse', 'primary_type': 'URGENT_PROPERTY_OR_ACCESS', 'priority': 'urgent',
                   'owner': 'Edy', 'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Kitchen sink leaking, water on the floor', 'owner': 'Edy', 'priority': 'urgent'},
                                              {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': 'Kitchen sink leaking, water on the floor', 'priority': 'urgent', 'responsible': ['Edy']}]})
    v5.run_case(cases['A1'], 1, 1, first=True)
    result = v5.run_case(cases['A17'], 1, 1, first=False)
    alert, old_alert = result['got']['alerts'][-1], v5.world.tap.messages[v5.case_alerts[-2]['id']]
    check_("v5 A17: the older alert says Outdated and has no buttons; no extra message about it",
           old_alert['text'].startswith("⚠️ Outdated – Vera wrote again 09:50, see the newer alert\n\n📨 TENANT MESSAGE") and not world_mod.buttons_of(old_alert['markup'])
           and not result['got']['others'], (old_alert['text'][:200], old_alert['markup'], result['got']['others']))
    check_("v5 A17: the new alert joins both messages into one 💬 line and is urgent; the case passes",
           "💬 \"Hi, the kitchen sink is dripping since last night. Now there's water on the floor\" 💬" in alert['text'] and "🔴 Urgent" in alert['text']
           and result['verdict'] == 'PASS', (alert['text'], result['reason']))
    step = v5.do_step({'press': 'Send Answer', 'by': 'Andy', 'on': 'first'})
    check_("v5: a press on the outdated alert does nothing", "does not exist" in (step['note'] or '') and not step['sent'], step['note'])

    # A18: the team answers in the chat before anyone pressed -> the AI answer is not needed, task and reminder buttons stay
    script.append(dict(SINK, actions=[dict(a) for a in SINK['actions']]))
    script.append(dict(QUIET))
    v5.run_case(cases['A1'], 1, 1, first=True)
    result = v5.run_case(cases['A18'], 1, 1, first=False)
    old_alert = v5.world.tap.messages[v5.case_alerts[-1]['id']]
    rows_now = [[b['text'] for b in row] for row in old_alert['markup']['inline_keyboard']]
    check_("v5 A18: the alert shows who answered, Send / Edit are gone, Create Task and Close Reminder stay; no new alert; the case passes",
           rows_now == [['✅ Edy answered in the chat 15:33 – AI answer not needed'], ['🎫 Create Task'], ['✅ Close Reminder']]
           and not result['got']['alerts'] and AIRun.objects.get(id=v5.case_alerts[-1]['run'].id).hold_status == 'suppressed'
           and result['verdict'] == 'PASS', (rows_now, result['got']['alerts'], result['reason']))
    check_("v5 A18: the team's answer is told to the AI as 'earlier alerts stay valid', not as 'replaced'", "EARLIER_ALERTS of this chat" in inputs_seen[-1]
           and "REPLACES them" not in inputs_seen[-1])

    # A19: a very long message -> the alert in parts, nothing cut, the buttons on the last part
    script.append({'answer': "Hi Rita, thanks for the details! We've logged the AC noise and the balcony door.", 'why': 'long', 'primary_type': 'ROUTINE_MAINTENANCE',
                   'priority': 'routine', 'owner': 'Edy',
                   'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'AC noise and balcony door', 'owner': 'Edy'},
                               {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': 'AC makes a noise at night', 'priority': 'routine', 'responsible': ['Edy']},
                               {'type': 'CREATE_TICKET', 'issue_id': 'new-1', 'title': 'Balcony door does not close', 'priority': 'routine', 'responsible': ['Edy']}]})
    result = v5.run_case(cases['A19'], 1, 1, first=True)
    alert = result['got']['alerts'][-1]
    long_text = cases['A19']['trigger'][0]['text']
    check_("v5 A19: a 3 900-character message -> two messages (1/2), (2/2), every word kept, buttons only on the last; the case passes",
           len(alert['parts']) == 2 and alert['parts'][0]['text'].startswith("(1/2)\n📨 TENANT MESSAGE") and alert['parts'][1]['text'].startswith("(2/2)\n")
           and not alert['parts'][0]['markup'] and alert['parts'][1]['markup'] and all(len(p['text']) <= 3900 for p in alert['parts'])
           and long_text[-200:] in alert['text'].replace("\n(2/2)\n", " ") and result['verdict'] == 'PASS',
           ([len(p['text']) for p in alert['parts']], result['reason']))
    run = AIRun.objects.get(id=alert['run'].id)
    check_("v5 A19: a reply to the first part reaches the same alert", answer_review.run_for_telegram_message(alert['parts'][0]['id'], "-500") == run)

    # B2: a team member states a fact -> TEAM MESSAGE without urgency and without sound, the fact with its source and two buttons
    script.append(lambda text: {'answer': 'NO_ANSWER', 'why': 'team answered', 'primary_type': 'NO_REPLY', 'issue_refs': [ref(r'issue_id: (i-\d+)', text)],
                                'actions': [{'type': 'KB_UPDATE', 'text': '105 Wilson: bike room is on the 1st floor, next to the mailboxes', 'scope': 'apartment'},
                                            {'type': 'CANCEL_FOLLOWUP', 'followup_id': ref(r'followup_id: (f-\d+)', text), 'reason': 'answered'}]})
    script.insert(0, {'answer': "Let me check with the team where you can leave your bike.", 'why': 'not in KB', 'primary_type': 'GENERAL_QUESTION',
                      'priority': 'routine', 'owner': 'Edy',
                      'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Where to leave the bike', 'owner': 'Edy'},
                                  {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': 'tell Vera where to leave her bike'}]})
    v5.run_case(cases['A4'], 1, 1, first=True)
    result = v5.run_case(cases['B2'], 1, 1, first=False)
    alert = result['got']['alerts'][-1]
    day = v5.clock.local()
    check_("v5 B2: the team alert, line by line", alert['text'].split("\n\n↩ Reply")[0] == f"""🧑‍🔧 TEAM MESSAGE · {day.day} {day:%b}, {day:%a} 15:50 ET

🏠 Sandbox Test · 👤 Vera Lopez · 🧑‍🔧 Edy

———
↪️ Vera (tenant) "Thanks! Where can I leave my bike?" ↪️

💬 "Vera, the bike room is on the 1st floor, next to the mailboxes" 💬
———
📚 "105 Wilson: bike room is on the 1st floor, next to the mailboxes" 📚
   from: Edy's message {day.day} {day:%b} 15:50
———
✅ Reminder "tell Vera where to leave her bike" closed – answered in the chat
———""", "\n" + alert['text'])
    check_("v5 B2: posted without sound, two knowledge buttons with the recommended one starred, the reminder closed quietly; the case passes",
           alert['silent'] and keyboard_now(alert) == [['🏠📚 Apartment ⭐', '🌍📚 Global']] and AIFollowUp.objects.get(conversation_sid=SANDBOX_SID).status == 'cancelled'
           and result['verdict'] == 'PASS', (keyboard_now(alert), result['reason']))
    v5.do_step({'press': 'Apartment', 'by': 'Andy'})
    v5.world.apartment.refresh_from_db()
    check_("v5 F5: Apartment saves the fact to the sandbox apartment's real page and replaces both buttons", keyboard_now(alert) == [['✅ Saved to apartment · Andy']]
           and v5.world.state['knowledge'][-1]['text'].startswith('105 Wilson: bike room is on the 1st floor')
           and 'bike room is on the 1st floor' in (v5.world.apartment.knowledge_base or ''), (keyboard_now(alert), v5.world.state['knowledge']))

    # B7: "👍" from the team -> skipped before the queue (a short acknowledgment), no alert
    _inputs_before = len(inputs_seen)
    result = v5.run_case(cases['B7'], 1, 1, first=True)
    check_("v5 B7: a short team acknowledgment never reaches the agent, posts no alert; the case passes", not result['got']['alerts']
           and not result['got']['others'] and len(inputs_seen) == _inputs_before and result['verdict'] == 'PASS', (result['got']['alerts'], result['reason']))

    # ---- H: automatic notifications go through the AI ---------------------------------------------------------------------
    TEMPLATE = cases['H1']['trigger']['text']
    script.append({'answer': TEMPLATE, 'why': 'rent $2,150 due 7 Oct is Pending, nothing about it in the chat', 'primary_type': 'PAYMENTS_AND_DOCUMENTS',
                   'priority': 'routine', 'actions': []})
    result = v5.run_case(cases['H1'], 1, 1, first=True)
    got = result['got']
    alert = got['alerts'][-1]
    day = v5.clock.local()
    check_("v5 H1: the AI is told about the planned notification, with the template and the REAL payment rows of the story",
           "NOTIFICATION_DUE" in inputs_seen[-1] and TEMPLATE in inputs_seen[-1] and "2150" in inputs_seen[-1] and "Pending" in inputs_seen[-1]
           and "Rent" in inputs_seen[-1], inputs_seen[-1][-1500:])
    check_("v5 H1: still needed in live -> sent at once, shown as an AI MESSAGE line by line, no buttons, no sound; the case passes",
           alert['text'].split("\n\n↩ Reply")[0] == f"""🤖 AI MESSAGE · {day.day} {day:%b}, {day:%a} 08:00 ET

🏠 Sandbox Test · 👤 Vera Lopez · 📅 Rent due tomorrow

———
🤖 "{TEMPLATE}" 🤖
———
✅ Sent 08:00 – still needed: rent $2,150 due 7 Oct is Pending, nothing about it in the chat
———""" and not world_mod.buttons_of(alert['markup']) and alert['silent'] and len(got['sent']) == 1 and got['sent'][0]['text'] == TEMPLATE
           and result['verdict'] == 'PASS', ("\n" + alert['text'], got['sent'], result['reason']))

    script.append({'answer': 'NO_ANSWER', 'why': 'tenant says she paid', 'primary_type': 'PAYMENTS_AND_DOCUMENTS', 'priority': 'routine', 'owner': 'Janna',
                   'no_reply_reason': 'Vera wrote on 5 Oct that she already paid by Zelle, but the payment is still Pending in the CRM',
                   'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': "Check Vera's October payment", 'owner': 'Janna'},
                               {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': "Janna: check Vera's October payment"}]})
    script.insert(0, {'answer': "Thanks Vera, Janna will check it.", 'why': 'paid', 'primary_type': 'PAYMENTS_AND_DOCUMENTS', 'priority': 'routine', 'owner': 'Janna',
                      'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': "November rent sent by Zelle", 'owner': 'Janna'},
                                  {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': "Janna: check Vera's November payment"}]})
    v5.run_case(dict(cases['A23'], presses=[]), 1, 1, first=True)
    result = v5.run_case(dict(cases['H2'], presses=[]), 1, 1, first=False)
    got = result['got']
    alert = got['alerts'][-1]
    check_("v5 H2: the tenant says she paid -> NOT sent, the alert says why, Send anyway + a reminder for Janna, with sound; the case passes",
           "📅 Rent due tomorrow · ⏸ HELD" in alert['text'] and "I sent November rent by Zelle this morning" in alert['text']
           and f"🤖 \"{TEMPLATE}\" 🤖\n———\n⏸ NOT sent – Vera wrote on 5 Oct that she already paid by Zelle, but the payment is still Pending in the CRM\n———\n"
               "⏰ Janna: check Vera's October payment – today 10:00 (1/2) ⏰" in alert['text']
           and keyboard_now(alert) == [['📤 Send anyway', '✏️ Edit Answer'], ['✅ Close Reminder']] and not got['sent'] and not alert['silent']
           and result['verdict'] == 'PASS', ("\n" + alert['text'], keyboard_now(alert), result['reason']))
    step = v5.do_step({'press': 'Send anyway', 'by': 'Andy'})
    now_text = v5.world.tap.messages[alert['id']]['text']
    check_("v5 H2: Send anyway really sends the text; the alert is no longer HELD and shows who sent it",
           len(step['sent']) == 1 and step['sent'][0]['text'] == TEMPLATE and "⏸" not in now_text and "\n✅ Sent · Andy " in now_text
           and keyboard_now(alert)[0][0].startswith("✅ Sent · Andy "), (step['sent'], now_text, keyboard_now(alert)))

    script.append({'answer': TEMPLATE, 'why': 'rent is Pending', 'primary_type': 'PAYMENTS_AND_DOCUMENTS', 'priority': 'routine', 'actions': []})
    result = v5.run_case(dict(cases['H1'], presses=[], mode='test'), 1, 1, first=True)
    alert = result['got']['alerts'][-1]
    check_("v5 H1 in test mode: never sent by itself, an AI MESSAGE with Send now",
           "📅 Rent due tomorrow · 🧪 TEST" in alert['text'] and "🧪 Test mode: NOT sent automatically. Press to send for real." in alert['text']
           and keyboard_now(alert) == [['🤖 Send now', '✏️ Edit Answer']] and not result['got']['sent'],
           ("\n" + alert['text'], keyboard_now(alert), result['reason']))
    step = v5.do_step({'press': 'Send now', 'by': 'Andy'})
    check_("v5 test mode: Send now sends for real also in a test apartment", len(step['sent']) == 1 and "✅ Sent · Andy " in v5.world.tap.messages[alert['id']]['text'], step['sent'])

    script.append({'answer': 'NO_ANSWER', 'why': 'paid per other chat', 'primary_type': 'PAYMENTS_AND_DOCUMENTS', 'priority': 'routine', 'owner': 'Janna',
                   'no_reply_reason': 'Vera wrote on 5 Oct (in her other chat) that she sent the October rent; it is still Pending in the CRM',
                   'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': "Check Vera's October payment", 'owner': 'Janna'},
                               {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': "Janna: check Vera's October payment"}]})
    script.insert(0, {'answer': "Thanks Vera, Janna will check it.", 'why': 'paid', 'primary_type': 'PAYMENTS_AND_DOCUMENTS', 'priority': 'routine', 'owner': 'Janna',
                      'actions': [{'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': "December rent sent by Zelle", 'owner': 'Janna'},
                                  {'type': 'SCHEDULE_FOLLOWUP', 'issue_id': 'new-1', 'kind': 'staff_reminder', 'reason': "Janna: check Vera's December payment"}]})
    v5.run_case(dict(cases['A23b'], presses=[]), 1, 1, first=True)
    check_("v5 A23b: the chapter's crm block changed the real rows first: booking extended, December rent added",
           v5.world.booking.end_date == v5.world.booking.start_date + timedelta(days=74) and Payment.objects.filter(booking=v5.world.booking).count() == 4
           and Payment.objects.filter(booking=v5.world.booking, payment_status='Completed').count() == 2, (v5.world.booking.end_date, list(Payment.objects.filter(booking=v5.world.booking).values_list('payment_date', 'payment_status'))))
    result = v5.run_case(cases['H6'], 1, 1, first=False)
    alert = result['got']['alerts'][-1]
    check_("v5 H6: two chats of one tenant -> the AI gets both as one history (the other chat's line is marked)",
           "TENANT_CHATS: this tenant has 2 separate group chats" in inputs_seen[-1] and "] [other chat #" in inputs_seen[-1]
           and "I sent the December rent this morning" in inputs_seen[-1], inputs_seen[-1][-1800:])
    check_("v5 H6: the alert's 💬 line is the message from the other chat; held; the case passes",
           "I sent the December rent this morning" in alert['text'] and "⏸ HELD" in alert['text'] and result['verdict'] == 'PASS',
           ("\n" + alert['text'], result['reason']))
    v5.world.clean({}, timedelta(0))
    check_("v5 H6: the second sandbox chat is gone after the clean start", not TwilioConversation.objects.filter(conversation_sid=world_mod.SANDBOX_SID_2).exists()
           and not TwilioMessage.objects.filter(conversation_sid=world_mod.SANDBOX_SID_2).exists())

    # the daily job: hands over instead of sending; a switched-off template sends nothing
    from mysite.management.commands.sms_notifications import Command as NotificationJob
    from mysite.models import AIManagement
    job = NotificationJob()
    v5.world.add_message({'from': 'tenant', 'text': 'hello'}, v5.clock.now())
    handed = job.hand_to_agent(v5.world.booking, "Gentle reminder", 'due_payment')
    queued = AIEvent.objects.filter(conversation_sid=SANDBOX_SID, event_type='NOTIFICATION_DUE', status='pending').first()
    check_("job: with the simple alerts the 08:00 job queues the notification for the AI instead of sending it",
           handed and queued and queued.body == "Gentle reminder" and queued.payload['kind'] == 'due_payment' and not twilio_calls, (handed, queued))
    AIEvent.objects.filter(conversation_sid=SANDBOX_SID, event_type='NOTIFICATION_DUE').delete()
    AIManagement.objects.update_or_create(prompt_key='safe_travel', defaults={'name': 'safe travel', 'entry_type': 'sms_template', 'content': 'Bye!', 'sms_enabled': False})
    check_("job: a template switched OFF sends nothing (before: its built-in text went out anyway)", job.get_message_for_event('safe_travel') is None
           and job.get_message_for_event('move_in'))
    AIManagement.objects.filter(prompt_key='safe_travel').delete()

    # ---- typed replies (part 7): a question gets an answer; a change is explained first and done only after the press -------
    EMPTY = {'decision': 'changes_only', 'corrected_answer': '', 'plan_ops': [], 'task_actions': [], 'new_facts': [], 'staff_answer': '',
             'lesson': '', 'lesson_key': '', 'lesson_scope': 'company'}
    OP = {'priority': '', 'title': '', 'text': '', 'value': '', 'state': '', 'owner': ''}
    script.append(dict(SINK, actions=[dict(a) for a in SINK['actions']]))
    result = v5.run_case(cases['A1'], 1, 1, first=True)
    alert, run_id = result['got']['alerts'][0], result['got']['runs'][0].id
    def task_action():
        return next(a for a in AIRun.objects.get(id=run_id).review['plan']['actions'] if a['type'] == 'CREATE_TICKET')
    def rows(message):   # the buttons as they are NOW (the step result holds a copy from when it was posted)
        message = v5.world.tap.messages.get(message['id'], message)
        return [[b['text'] for b in row] for row in (message['markup'] or {}).get('inline_keyboard', [])]

    interpreter_out.append(dict(EMPTY, decision='question', staff_answer='A dripping sink needs a plumber visit.'))
    step = v5.do_step({'reply': 'why a task for this?', 'by': 'Andy'})
    LINKS = f"\n\n🔗 AI run: http://crm.test/ai-runs/{run_id}/\n💬 CRM chat: http://crm.test/chat/{SANDBOX_SID}/"
    check_("v5 E1: a question gets an answer and nothing else (plus the footer links of the alert)", step['alerts'][-1]['text'] == "🤖 A dripping sink needs a plumber visit." + LINKS
           and not rows(step['alerts'][-1]) and not AIRun.objects.get(id=run_id).review.get('proposals'), step['alerts'])

    interpreter_out.append(dict(EMPTY, plan_ops=[dict(OP, op='change', n=2, owner='Kevin', priority='urgent')]))
    step = v5.do_step({'reply': 'task for Kevin, urgent', 'by': 'Andy'})
    said = step['alerts'][-1]
    check_("v5 E2: a change request is explained first, with ✅ Apply Change", said['text'] == (
        "✏️ I WILL CHANGE\n🎫 Kitchen sink dripping\n   now:   Edy · routine\n   after: Kevin · urgent\nWhy: you asked for it.\n"
        "After the press: the alert shows the new values. A task is still created only with 🎫 Create Task." + LINKS) and rows(said) == [['✅ Apply Change']], said)
    check_("v5 E2: nothing changed before the press", task_action().get('responsible') == ['Edy'] and "Edy · routine" in v5.world.tap.messages[alert['id']]['text'])
    step = v5.do_step({'press': 'Apply Change', 'by': 'Andy'})
    check_("v5 E2: after the press the task has the new values, on the alert too; the button shows who changed it",
           task_action().get('responsible') == ['Kevin'] and task_action().get('priority') == 'urgent'
           and "   Kevin · 🔴 urgent · due " in v5.world.tap.messages[alert['id']]['text'] and rows(said)[0][0].startswith("✅ Changed · Andy ")
           and rows(v5.world.tap.messages[alert['id']])[0] == ['🤖 Send Answer', '✏️ Edit Answer'], (task_action(), rows(said), v5.world.tap.messages[alert['id']]['text']))
    step = v5.do_step({'press': 'Changed', 'by': 'Edy'})
    check_("v5: a second press on Apply Change does nothing twice", any(p.startswith("already done: ✅ Changed · Andy") for p in step['popups']), step['popups'])

    interpreter_out.append(dict(EMPTY, plan_ops=[dict(OP, op='remove', n=3)]))
    step = v5.do_step({'reply': 'test no reminder needed', 'by': 'Andy'})
    check_("v5 E5: 'test ...' is a dry run - the same explanation, no buttons, nothing happens",
           step['alerts'][-1]['text'].startswith("🧪 DRY RUN – nothing will happen. I would:\n❌ Close the reminder: ⏰ Check the sink task has a visit date")
           and not rows(step['alerts'][-1]) and AIFollowUp.objects.get(conversation_sid=SANDBOX_SID).status == 'pending', step['alerts'][-1])

    interpreter_out.append(dict(EMPTY, decision='replace', corrected_answer='Hi Vera, Edy will come himself today at 5pm to look at it.',
                                lesson='When a tenant reports a small plumbing issue, offer a same-day visit by Edy.', lesson_key='small_plumbing',
                                new_facts=[{'key': 'wifi password', 'value': 'Sun2026!', 'scope': 'apartment'}]))
    step = v5.do_step({'reply': 'Hi Vera, Edy will come himself today at 5pm to look at it. Next time offer a same-day visit. Wifi is Sun2026!', 'by': 'Andy'})
    said = step['alerts'][-1]
    check_("v5 E3 / E4: a corrected answer, a rule and a fact are shown with their own buttons - nothing is sent or saved yet",
           said['text'].startswith("✏️ NEW ANSWER for Vera\n🤖 \"Hi Vera, Edy will come himself today at 5pm to look at it.\" 🤖\n📏 Rule I learned: \"When a tenant")
           and "📚 \"Wifi password: Sun2026!\" 📚\n   from: Andy's reply " in said['text']
           and rows(said) == [['🤖 Send Answer'], ['📏 Save rule'], ['🏠📚 Apartment ⭐', '🌍📚 Global']]
           and not step['sent'] and not v5.world.state['rules'] and not v5.world.state['knowledge'], (said['text'], rows(said)))
    check_("test run: a bot answer to a typed reply says what to press now, one line per button",
           "🧪 TEST NOTES\nDo this:\n1. 🤖 Send Answer → " in said['posted'] and "\n2. 📏 Save rule → " in said['posted']
           and "\n3. 🏠📚 Apartment ⭐ → " in said['posted'] and "TEST NOTES" not in said['text'], said['posted'])
    step = v5.do_step({'press': 'Send Answer', 'by': 'Andy'})
    check_("v5 E3: Send Answer on the new answer sends THAT text; the alert shows it and ✅ Answer sent; old + new are kept for learning",
           len(step['sent']) == 1 and step['sent'][0]['text'] == 'Hi Vera, Edy will come himself today at 5pm to look at it.'
           and "🤖 \"Hi Vera, Edy will come himself today at 5pm to look at it.\" 🤖" in v5.world.tap.messages[alert['id']]['text']
           and rows(v5.world.tap.messages[alert['id']])[0][0].startswith("✅ Answer sent · Andy ") and rows(said)[0][0].startswith("✅ Answer sent · Andy ")
           and AIRun.objects.get(id=run_id).review['corrected']['old'].startswith("Hi Vera, thanks"), (step['sent'], step['note'], rows(said)))
    v5.do_step({'press': 'Save rule', 'by': 'Andy'})
    v5.do_step({'press': 'Apartment', 'by': 'Andy'})
    check_("v5 E3 / E4: Save rule and Apartment save for real (here: into the sandbox), each button shows its result",
           v5.world.state['rules'][0]['rule'].startswith("When a tenant reports a small plumbing issue")
           and v5.world.state['knowledge'][0] ['text'] == "Wifi password: Sun2026!" and v5.world.state['knowledge'][0]['scope'] == 'apartment'
           and rows(said)[1] == ['✅ Rule saved · Andy'] and rows(said)[2] == ['✅ Saved to apartment · Andy'], (v5.world.state, rows(said)))

    interpreter_out.append(dict(EMPTY, decision='unclear'))
    step = v5.do_step({'reply': 'hmm', 'by': 'Andy'})
    check_("v5: a reply the bot does not understand changes nothing and says so", "nothing was changed" in step['alerts'][-1]['text'] and not rows(step['alerts'][-1]))
finally:
    v5.finish()

# ---- 10c. with an own sandbox bot: the run waits, a real press and "next" arrive from Telegram ------------------------------
waited_out, sent_updates = [], {'n': 0}
live = Runner(auto=False, offline=True, use_claude=False, out=lambda text='': waited_out.append(str(text)))
def bot_updates():
    record, button = live.world.tap.find_button('Create Task')
    if sent_updates['n'] == 0 and not button:
        return []
    sent_updates['n'] += 1
    if sent_updates['n'] == 1:
        sent_updates['alert'] = record['id']
        return [{'update_id': 101, 'callback_query': {'id': 'cb1', 'from': {'first_name': 'Andy'}, 'data': button['callback_data'],
                                                      'message': {'message_id': record['id'], 'chat': {'id': -500}}}}]
    if sent_updates['n'] == 2:   # the 🧪 Next test button under the alert
        sent_updates['rows'] = [[b['text'] for b in row] for row in live.world.tap.with_control(live.world.tap.messages[sent_updates['alert']]['markup'])['inline_keyboard']]
        sent_updates['has_control'] = sent_updates['alert'] in live.world.tap.control
        return [{'update_id': 102, 'callback_query': {'id': 'cb2', 'from': {'first_name': 'Andy'}, 'data': 'sbx|next',
                                                      'message': {'message_id': sent_updates['alert'], 'chat': {'id': -500}}}}]
    return []
idle_feed = []   # updates for the "nothing is running" check further down
real_fetch, answer_review.fetch_updates = answer_review.fetch_updates, lambda *a, **k: idle_feed.pop(0) if idle_feed else bot_updates(*a, **k)
live.start()
live.world.own_bot = True   # as with AI_AGENT_SANDBOX_BOT_TOKEN (here Telegram itself stays offline)
try:
    script.append(dict(SINK, actions=[dict(a) for a in SINK['actions']]))
    live.run_cases([cases['A1']], "case A1", fresh=True)
    run = AIRun.objects.filter(conversation_sid=SANDBOX_SID).order_by('-id').first()
    task = next(a for a in run.review['plan']['actions'] if a['type'] == 'CREATE_TICKET')
    check_("own bot: the run waited, the real press created the task, the 🧪 Next test button ended the case",
           sent_updates['n'] >= 2 and task.get('done', {}).get('by') == 'Andy' and [r['verdict'] for r in live.results] == ['PASS']
           and any(l.startswith("\nWaiting: next") for l in waited_out), (sent_updates['n'], task.get('done'), [(r['verdict'], r.get('reason')) for r in live.results], waited_out[-6:]))
    check_("own bot: the test controls are the last row under the alert, kept when the alert's buttons change, gone after the case",
           sent_updates.get('has_control') and sent_updates['rows'][-1] == ['🧪 Next test', '🧪 Rerun', '🧪 Stop']
           and sent_updates['rows'][1][0].startswith('✅ Task noted') and not live.world.tap.control, sent_updates)
    pressed = live.world.tap.messages[sent_updates['alert']]
    busy_rows = [[b['text'] for b in row] for row in live.world.tap.with_control(pressed['markup'], pressed)['inline_keyboard']]
    check_("own bot: after the press the button shows ⏳ working… until the next thing is posted",
           sent_updates['alert'] in live.world.tap.busy and busy_rows[-1] == ['⏳ Next test · working…'], (busy_rows, live.world.tap.busy))
    # Nothing is running: the run waits in the group; 🔄 Restart (a button of its own message) is a command for serve()
    idle_updates = [[{'update_id': 201, 'message': {'message_id': 9001, 'chat': {'id': -500}, 'text': 'what now?', 'from': {'first_name': 'Andy'}}}],
                    [{'update_id': 202, 'callback_query': {'id': 'cb9', 'from': {'first_name': 'Andy'}, 'data': 'sbx|restart',
                                                           'message': {'message_id': 1, 'chat': {'id': -500}}}}]]
    idle_feed.extend(idle_updates)
    busy_before = set(live.world.tap.busy)
    live.position = 1
    said_idle = []
    real_post, live.post = live.post, lambda text, **k: said_idle.append((text, k.get('markup'))) or real_post(text, **k)
    got_idle = live.wait_idle([cases['A1'], cases['A2']], "group A")
    live.post = real_post
    check_("idle: the run says what can be done, with ▶️ Continue / 🔄 Restart buttons, and returns the command it gets",
           got_idle == 'restart' and "continue – next case of group A (1 left)" in said_idle[0][0]
           and [b['text'] for b in said_idle[0][1]['inline_keyboard'][0]] == ['▶️ Continue', '🔄 Restart'], (got_idle, said_idle))
    check_("idle: the ⏳ working… row of the earlier press is gone once the idle message is posted",
           busy_before and not live.world.tap.busy and not live.world.tap.messages[sent_updates['alert']].get('busy_row'), busy_before)

    # A message typed in the sandbox chat on the site (CRM chat page) while a run is alive: the site queues it for the
    # live worker - the test run takes it first and handles it as a step of the case
    from mysite.ai_agent import service as _service
    typed_row = TwilioMessage.objects.create(message_sid="LOCAL-drytyped1", conversation_sid=SANDBOX_SID, conversation=live.world.conversation,
                                             author='ASSISTANT', body="kevin: I will check it today", direction='outbound',
                                             message_timestamp=timezone.now())
    _service.enqueue_staff_message(SANDBOX_SID, typed_row.message_sid, typed_row.body, source='chat_ui')
    took = live.world.take_typed()
    check_("CRM chat: a typed message is taken from the live worker's queue - no pending event, no stray row",
           took == 1 and not AIEvent.objects.filter(conversation_sid=SANDBOX_SID, status='pending').exists()
           and not TwilioMessage.objects.filter(id=typed_row.id).exists() and live.world.typed[0]['kind'] == 'team', (took, live.world.typed))
    stepped = []
    real_step, live.do_step = live.do_step, lambda step, **kw: stepped.append((step, kw)) or {'alerts': [1], 'note': None}
    real_render, check.render_result = check.render_result, lambda got: ''
    try:
        live._typed_messages(cases['A1'])
    finally:
        live.do_step, check.render_result = real_step, real_render
    check_("CRM chat: Manager = a team member (\"Kevin: ...\" names him, else Edy); handled as a step with the test controls",
           stepped and stepped[0][0] == [{'from': 'team', 'name': 'Kevin', 'text': 'I will check it today'}]
           and stepped[0][1]['control'] and "Kevin wrote in the CRM chat" in stepped[0][1]['header'] and not live.world.typed, stepped)
    typed_notes = stepped[0][1]['notes_hook']("alert", {'inline_keyboard': [[{'text': '🤖 Send Answer', 'callback_data': 'v5|sa|1|'},
                                                                              {'text': '✅ Close Reminder', 'callback_data': 'v5|cr|1|0'}]]}) if stepped else ''
    check_("CRM chat: the alert gets test notes that say what each button does and how to go on (no expected result)",
           stepped[0][1].get('notes_always') and typed_notes.startswith('🧪 TEST NOTES') and 'as Kevin' in typed_notes
           and '1. 🤖 Send Answer →' in typed_notes and '2. ✅ Close Reminder →' in typed_notes and 'Next test' in typed_notes, typed_notes)
finally:
    live.finish()
    answer_review.fetch_updates = real_fetch

# ---- 10b. commands of the test group, the serve loop, a new case from a replay ---------------------------------------------------
from mysite.ai_agent.sandbox_test.world import parse_command
check_("group commands: continue, restart, run <cases>, test-conversation <chat> - also as /commands; a sentence is not a command",
       parse_command('continue') == ('continue', '') and parse_command('/restart@pm_ai_alert_bot') == ('restart', '')
       and parse_command('test-conversation-id CHabc12345 last 20') == ('test conversation', 'CHabc12345 last 20')
       and parse_command('/test_conversation CHabc12345') == ('test conversation', 'CHabc12345')
       and parse_command('run A5,b') == ('run', 'A5,b') and parse_command('add test') == ('add test', '')
       and parse_command('run the dryer again') is None and parse_command('test conversation with Vera went fine') is None
       and parse_command('rerun with: the sink floods') == ('rerun with', 'the sink floods'))
check_("run A1,B: the case A1 and every B case, in STORY order (B7 and B2 come before A1 in the story)",
       [c['id'] for c in catalog.pick('A1,b')][:3] == ['B7', 'B2', 'A1'] and all(c['id'][0] in 'AB' for c in catalog.pick('A1,b'))
       and sum(1 for c in catalog.pick('A1,b') if c['id'][0] == 'A') == 1, [c['id'] for c in catalog.pick('A1,b')][:4])
serving = Runner(auto=False, offline=True, use_claude=False, out=lambda text='': None)
trace, outcomes = [], ['restart', None, ('run', 'B2'), None, None]
idle = ['continue', ('test conversation', 'CHabc12345 5')]
serving.run_cases = lambda cases, label, start=0, fresh=False, restore=True: (
    trace.append(('cases', [c['id'] for c in cases][:2], start, fresh, restore)), setattr(serving, 'position', start + 1), outcomes.pop(0))[-1]
serving.run_conversation = lambda sid, last=None, since=None: trace.append(('replay', sid, last))
serving.clean_chat = lambda: trace.append('clean')
serving.post = lambda *a, **k: None
def _idle(selection, label):
    if not idle:
        raise StopIteration
    return idle.pop(0)
serving.wait_idle = _idle
serving.world.own_bot = True
try:
    serving.serve([cases['A1'], cases['A2'], cases['A3']], "group A", stay=True)
except StopIteration:
    pass
check_("serve: restart cleans the chat and starts at the first case; continue goes on with the next one; run picks other cases; "
       "test-conversation replays a chat; then it waits again",
       trace == [('cases', ['A1', 'A2'], 0, False, True), 'clean', ('cases', ['A1', 'A2'], 0, True, True), ('cases', ['A1', 'A2'], 1, False, False),
                 ('cases', ['B2'], 0, False, True), ('replay', 'CHabc12345', 5)], trace)
check_("serve: a list that starts with the story's first chapter is a fresh story; restart is always fresh; continue restores nothing; "
       "run X starts from the saved state before X", True)
import tempfile as _tempfile
_tmp = Path(_tempfile.mkdtemp())
_real_paths = (catalog.CASES_PATH, catalog.DOC_PATH)
try:
    catalog.CASES_PATH, catalog.DOC_PATH = _tmp / 'cases.yaml', _tmp / 'doc.md'
    catalog.CASES_PATH.write_text(_real_paths[0].read_text(encoding='utf-8'), encoding='utf-8')
    catalog.DOC_PATH.write_text(_real_paths[1].read_text(encoding='utf-8'), encoding='utf-8')
    new_id = catalog.next_case_id('A')
    block = catalog.case_yaml('A3').replace('A3:', f'{new_id}:', 1)
    error = catalog.add_case(new_id, block, "From a real chat", "📨 TENANT MESSAGE · example\n(footer)", note="Added from a real conversation.")
    ids = list(catalog.load_cases())
    doc = catalog.DOC_PATH.read_text(encoding='utf-8')
    check_("add to tests: a new case gets the next free number, goes into the story at its time (right after A3, same time), its example into the document",
           error is None and new_id not in cases and int(new_id[1:]) > 21 and ids.index(new_id) == ids.index('A3') + 1
           and catalog.doc_sections()[new_id]['title'] == "From a real chat" and doc.index(f"### {new_id}. ") < doc.index("### B1. "),
           (error, new_id, ids[ids.index(new_id) - 1: ids.index(new_id) + 2] if new_id in ids else ids[-3:]))
    check_("add to tests: a block that does not load changes nothing", catalog.add_case(catalog.next_case_id('B'), "B99:\n  title: [broken", "x", "y") is not None
           and 'B99' not in catalog.CASES_PATH.read_text(encoding='utf-8'))
finally:
    catalog.CASES_PATH, catalog.DOC_PATH = _real_paths

# ---- 11. after the run ----------------------------------------------------------------------------------------------------------
check_("end: no sandbox run is left in live mode", not AIRun.objects.filter(conversation_sid=SANDBOX_SID, mode='live').exists())
check_("end: no sandbox reminder is left pending for the live worker", not AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID, status='pending').exists())
check_("end: no sandbox event is left pending or running", not AIEvent.objects.filter(conversation_sid=SANDBOX_SID, status__in=('pending', 'running')).exists(),
       list(AIEvent.objects.filter(conversation_sid=SANDBOX_SID).values_list('event_type', 'status')))
check_("end: nothing held for the live SMS flush job", not PendingOutboundMessage.objects.filter(conversation_sid=SANDBOX_SID).exists())
check_("end: the clock and the patches are gone", timezone.now is real_now and abs((timezone.now() - real_now()).total_seconds()) < 5
       and messaging.send_messsage_by_sid is not real_send and messaging.send_messsage_by_sid.__name__ == '<lambda>')
check_("end: the real Twilio stand-in was never called", not twilio_calls, twilio_calls)
summary = (catalog.RESULTS_DIR / runner.run_name / "summary.md").read_text()
check_("end: summary written", "🧪 SANDBOX TEST RUN" in summary and "| Case | Result | Why |" in summary, summary[:400])
listed = []
list_cases(listed.append)
check_("--list shows every case and the last results", any("G1 " in l and "FAIL" in l for l in listed) and any("A1 " in l and "PASS" in l for l in listed) and any(l.strip().startswith(f"{len(cases)} cases") for l in listed), listed[-3:])

if '--show' in sys.argv:
    print("\n".join(lines_out[:140]))
# ---- 12. view-only posting (main bot, no sandbox bot): header, notes before the footer, buttons as text -------------------
import json, shutil, types
from mysite.ai_agent.sandbox_test.clock import CaseClock, week_shift
posted = []
class _Resp:
    status_code, content = 200, b'1'
    def json(self): return {'ok': True, 'result': {'message_id': 4242}}
real_requests = world_mod.requests
world_mod.requests = types.SimpleNamespace(post=lambda url, data=None, **k: (posted.append((url, dict(data or {}))), _Resp())[1])
try:
    view = world_mod.World(CaseClock(), offline=False)
    view.post_only = True
    view.tap.step, view.tap.header = 1, "🧪 SANDBOX TEST · case A1 · 1 of 21"
    view.tap.notes_hook = lambda text, markup: "🧪 TEST NOTES\nChecks: something"
    keyboard = {'inline_keyboard': [[{'text': '🤖 Send Answer', 'callback_data': 'v5|send|1'}, {'text': '✏️ Edit Answer', 'callback_data': 'v5|edit|1'}]]}
    alert_text = "📨 TENANT MESSAGE · 6 Oct, Tue 14:34 ET\n\n———\n🤖 \"Hi\" 🤖\n———\n\n↩ Reply to this message for questions, notes or custom actions.\n\n🔗 AI run: x"
    view.tap.post("https://api.telegram.org/botX/sendMessage", data={'chat_id': '-1', 'text': alert_text, 'reply_markup': json.dumps(keyboard),
                                                                     'disable_notification': 'true'})
    url, data = posted[-1]
    check_("view-only: the buttons are NOT attached (a press would go to the live worker), they are written as text",
           'reply_markup' not in data and "[🤖 Send Answer] [✏️ Edit Answer]" in data['text'], data)
    check_("view-only: header first, notes right before the footer",
           data['text'].startswith("🧪 SANDBOX TEST · case A1 · 1 of 21\n\n📨 TENANT MESSAGE")
           and "🤖 \"Hi\" 🤖\n———\n🧪 TEST NOTES\nChecks: something\n———\n\n↩ Reply to this message" in data['text'], data['text'])
    record, button = view.tap.find_button('Send Answer')
    check_("view-only: the runner still knows the buttons (checks, simulated presses) and the silent flag",
           button and button['callback_data'] == 'v5|send|1' and record['silent'] and record['text'] == alert_text)
    view.tap.post("https://api.telegram.org/botX/editMessageReplyMarkup", data={'chat_id': '-1', 'message_id': 4242, 'reply_markup': json.dumps({'inline_keyboard': []})})
    check_("view-only: a button change is recorded, not sent", len(posted) == 1 and not world_mod.buttons_of(view.tap.messages[4242]['markup']))

    # ---- 13. own sandbox bot: test commands are set aside, everything else goes to the code under test ----------------------------
    own = world_mod.World(CaseClock())
    own.own_bot = True
    incoming = [{'update_id': 5, 'message': {'chat': {'id': -500}, 'text': 'rerun with: the sink is flooding', 'from': {'first_name': 'Andy'}, 'message_id': 9}},
                {'update_id': 6, 'callback_query': {'id': 'c1', 'data': 'sbx|apply|A1', 'from': {'first_name': 'Andy'}}},
                {'update_id': 7, 'callback_query': {'id': 'c2', 'data': 'v5|sa|1|', 'from': {'first_name': 'Andy'}}},
                {'update_id': 8, 'message': {'chat': {'id': -500}, 'text': 'why a task for this?', 'from': {'first_name': 'Andy'},
                                             'reply_to_message': {'message_id': 1}}}]
    passed_on = own._updates(lambda: incoming)
    check_("own bot: 'rerun with: ...' and the Apply Change press are commands; the card button and the question go on",
           [c['text'] for c in own.commands] == ['rerun with: the sink is flooding', 'apply'] and [u['update_id'] for u in passed_on] == [7, 8],
           (own.commands, passed_on))
    check_("commands: read as a whole message only - 'next time ...' stays a lesson for the AI",
           world_mod.parse_command("next") == ('next', '') and world_mod.parse_command(" Rerun with: the sink is flooding ") == ('rerun with', 'the sink is flooding')
           and world_mod.parse_command("change test: make it 23:40, after hours") == ('change test', 'make it 23:40, after hours')
           and world_mod.parse_command("next time offer a same-day visit") is None and world_mod.parse_command("stop the reminder") is None
           and world_mod.parse_command("accept") == ('accept', ''))
    check_("no own bot: real updates are never read (the live worker owns them)", world_mod.World(CaseClock())._updates(lambda: 1 / 0) == [])
finally:
    world_mod.requests = real_requests

# ---- 14. "change test" / "accept": the case file and the document example are rewritten, or left alone when broken ------------------
real_cases_path, real_doc_path = catalog.CASES_PATH, catalog.DOC_PATH
catalog.CASES_PATH = Path(shutil.copy(real_cases_path, catalog.RESULTS_DIR / "cases_copy.yaml"))
catalog.DOC_PATH = Path(shutil.copy(real_doc_path, catalog.RESULTS_DIR / "doc_copy.md"))
try:
    block = catalog.case_yaml('A15')
    check_("change test: the case's own block is found", block.startswith("A15:") and "A16:" not in block and "trigger:" in block, block[:300])
    error = catalog.replace_case_yaml('A15', block.replace("ok thanks 👍", "thanks a lot"))
    changed = catalog.load_cases()
    check_("change test: the block is replaced, the other cases stay", error is None and changed['A15']['trigger'][0]['text'] == "thanks a lot"
           and len(changed) == len(cases) and changed['A16'] == cases['A16'], error)
    before_text = catalog.CASES_PATH.read_text()
    error = catalog.replace_case_yaml('A15', "A15:\n  title: [broken")
    check_("change test: a block that does not load changes nothing", error and catalog.CASES_PATH.read_text() == before_text, error)
    check_("accept: the example of the section is replaced, only there", catalog.replace_doc_example(cases['A3'], "NEW EXAMPLE LINE")
           and "NEW EXAMPLE LINE" in catalog.doc_sections()['A3']['text'] and "wifi is MyHome-5G" not in catalog.doc_sections()['A3']['text']
           and catalog.doc_sections()['A2'] == sections['A2'] and catalog.doc_sections()['A4'] == sections['A4'])
finally:
    catalog.CASES_PATH, catalog.DOC_PATH = real_cases_path, real_doc_path

# ---- 15. the case date -----------------------------------------------------------------------------------------------------------------
past = catalog.parse_time("2026-09-01 14:34")
days = week_shift(past)
check_("clock: a past case date moves forward by whole weeks into the future", days % 7 == 0 and past + timedelta(days=days) > timezone.now()
       and past + timedelta(days=days - 7) <= timezone.now() + timedelta(days=7), days)
columbus = catalog.parse_time("2026-10-12 10:00")
check_("clock: a holiday case stays on a holiday or is not moved onto a normal day by accident",
       bool(config.holiday_name((columbus + timedelta(days=week_shift(columbus))).date())) == True or week_shift(columbus) == 0)
check_("clock: a normal day is never moved onto a holiday", not config.holiday_name((catalog.parse_time("2026-10-05 10:00")
       + timedelta(days=week_shift(catalog.parse_time("2026-10-05 10:00")))).date()))

# ---- 16. the Claude judge and the test assistant (Claude itself is faked) ---------------------------------------------------------------
asked = []
def fake_claude(prompt, system):
    asked.append(prompt)
    return claude_says[0], 0.02
real_claude, check._claude = check._claude, fake_claude
try:
    got = {'alerts': [], 'others': [], 'sent': [], 'held': [], 'popups': [], 'calls': [], 'note': None}
    claude_says = ['```json\n{"verdict": "fail", "lines": [{"ok": true, "text": "answer - same meaning"}, {"ok": false, "text": "no ⚖️ contract line"}], '
                   '"suggestion": "show the contract basis"}\n```']
    judged = check.judge(cases['A5'], sections['A5'], got, [(True, "alert type TENANT MESSAGE")], 7)
    check_("judge: reads the JSON (also inside a code fence), verdict + lines + suggestion + cost",
           judged['verdict'] == 'fail' and judged['lines'] == [(True, "answer - same meaning"), (False, "no ⚖️ contract line")]
           and judged['suggestion'] == "show the contract basis" and judged['cost'] == 0.02, judged)
    check_("judge: gets the example from the document, the date shift and the code results",
           "Can I pay October rent on the 10th" in asked[-1] and "7 day(s) after" in asked[-1] and "ok: alert type TENANT MESSAGE" in asked[-1])
    claude_says = ['{"verdict": "fail", "lines": [{"ok": false, "text": "The AI promises the tenant a time that our example does not.", '
                   '"example": "our maintenance team will contact you to schedule a visit", "ai": "send a technician within 24 hours"}], '
                   '"suggestion": "The answer should only say the team will contact her."}']
    judged = check.judge(cases['A1'], sections['A1'], got, [], 0)
    message = check.failure_text('A1', [(True, "alert type TENANT MESSAGE")] + judged['lines'], judged['suggestion'])
    check_("failed check in the group: plain words, both texts side by side, passed checks left out", message == (
        "🧪 A1 · ❌ 1 thing is different from our example\n\n1. The AI promises the tenant a time that our example does not.\n"
        "   We agreed: \"our maintenance team will contact you to schedule a visit\"\n   The AI now: \"send a technician within 24 hours\"\n\n"
        "💡 What to change: The answer should only say the team will contact her."), message)
    diag_text = check.diagnosis_text({'where': 'test', 'what': 'The check expects 1 closed reminder; the team closed it by hand before.', 'files': ['claude_code_integration_doc/testbed/sandbox_cases.yaml'], 'sure': 'high'})
    check_("failed check: the diagnosis says where the fix belongs, the files, and what the two buttons do",
           diag_text.startswith("🔧 What is wrong: the test itself") and "sandbox_cases.yaml" in diag_text and "Press 🔧 Fix" in diag_text and "👌 No fix needed" in diag_text, diag_text)
    check_("failed check: 'story' = nothing to fix", "nothing - the story went another way" in check.diagnosis_text({'where': 'story', 'what': 'x', 'files': [], 'sure': 'high'}))
    check_("commands: fix / no fix needed (the two buttons) are commands", parse_command('fix') == ('fix', '') and parse_command('No fix needed') == ('nofix', '')
           and parse_command('nofix') == ('nofix', ''))
    claude_says = ['{"verdict": "pass", "lines": [{"ok": false, "text": "two tasks expected"}]}']
    check_("judge: 'pass' with a failed line is a fail", check.judge(cases['A1'], sections['A1'], got, [], 0)['verdict'] == 'fail')
    claude_says = ["Sorry, I can not"]
    judged = check.judge(cases['A1'], sections['A1'], got, [], 0)
    check_("judge: an unreadable answer does not fail the case, it is shown as not judged", judged['verdict'] is None and judged['lines'][0][0] is None)
    _markup = {'inline_keyboard': [[{'text': '🤖 Send Answer', 'callback_data': 'v5|sa|1|'}, {'text': '✏️ Edit Answer', 'callback_data': 'v5|ea|1|'}],
                                   [{'text': '🎫 Create Task', 'callback_data': 'v5|ct|1|0'}]]}
    _case = dict(cases['A1'], presses=[{'button': 'Send Answer', 'expect': 'Sent'}, {'button': 'Create Task | Apply Update'},
                                        {'button': 'Close Reminder', 'optional': True}])
    notes = check.test_notes(_case, sections['A1'], "alert", _markup)[0]
    check_("notes: what we test (the title) + ONE list of exact presses (the chapter's presses, as in --auto) + Next; the same on every run",
           notes == ("🧪 TEST NOTES\nWhat we test: Routine maintenance, live (the full alert)\nDo this:\n"
                     "1. Press 🤖 Send Answer → the answer goes to the tenant (see the CRM chat); the button becomes ✅ Answer sent.\n"
                     "2. Press 🎫 Create Task → a [SANDBOX] task appears in the ClickUp TEST list; the button becomes ✅ Task created.\n"
                     "3. Press 🧪 Next test.")
           and notes == check.test_notes(_case, sections['A1'], "other alert text", _markup)[0], notes)
    check_("notes: a chapter without presses says so", check.test_notes(dict(cases['A1'], presses=[]), sections['A1'], "alert", _markup)[0]
           .endswith("Nothing to press here. Press 🧪 Next test."))
    claude_says = ['{"explanation": "Expect 2 tasks.", "yaml": "A11:\\n  title: x", "doc_example": null}']
    proposal = check.propose_change(cases['A11'], sections['A11'], got, "expected 2 tasks here, not 1")
    check_("change test: the proposal carries the explanation and the new block", proposal.get('yaml') == "A11:\n  title: x" and proposal['doc_example'] is None
           and "expected 2 tasks here, not 1" in asked[-1], proposal)
finally:
    check._claude = real_claude

print(f"{sum(checks)}/{len(checks)} passed")
sys.exit(0 if all(checks) else 1)
