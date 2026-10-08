"""Phase 3 checks on the throwaway SQLite DB: knowledge-base documents, access-code guard, payments, staff phones."""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

from django.conf import settings as _s
from django.test import Client
from django.utils import timezone
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement,
                           AIEvent, StaffMember)
from mysite.ai_agent import service, runner, notify, config, inputs, knowledge, kb_documents
import mysite.ai_agent.actions as actions_mod
import mysite.ai_agent.team_notify as team_notify_mod
import mysite.views.messaging as messaging

# Telegram is faked at the lowest level (notify._post / notify._call): every module that imported send_ai_chat is captured
telegram, markups, sms, telegram_calls = [], [], [], []
os.environ["TELEGRAM_TOKEN"] = "fake-token"; os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from mysite.ai_agent.sandbox_test.world import html_to_plain as _plain_of   # an alert sent as Telegram HTML, as read
notify._post = lambda token, chat_id, text, reply_to=None, reply_markup=None, silent=False, parse_mode=None: (telegram.append(_plain_of(text) if parse_mode == 'HTML' else text), markups.append(reply_markup), (True, "captured", 9000 + len(telegram)))[2]
notify._call = lambda method, data: (telegram_calls.append((method, data)), (True, ''))[1]
service.report_error = lambda e, ctx, info=None, source='task': telegram.append(f"ERROR {ctx}: {e}")
messaging.send_messsage_by_sid = lambda sid, author, message, s_, r_: sms.append(message)
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs"; config.WORK_DIR = config.RUNS_DIR / "_cwd"
script, inputs_seen = [], []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    inputs_seen.append(user_input); out = script.pop(0)
    (run_dir / "01_system_prompt.md").write_text(system_prompt)
    return {'ok': True, 'error': None, 'output': out, 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 1, 'timed_out': False}
runner.run_claude = fake_run_claude
import re as _re
def fake_merge_complete(prompt):
    """Stands in for the Claude merge: replaces the corrected text, else appends the new line."""
    doc = _re.search(r"Current document:\n(.*?)\n\nNew information to add", prompt, _re.S).group(1)
    new = _re.search(r"New information to add \(from [^)]*\):\n(.*?)\n\nIt corrects", prompt, _re.S).group(1)
    old = _re.search(r"replaces this part of the document \(if any\):\n(.*?)\n\nKnowledge-base rules", prompt, _re.S).group(1)
    doc = '' if doc == '(empty)' else doc
    out = doc.replace(old, new) if old != '(nothing)' and old in doc else (doc + '\n' + new).strip()
    return f"[UPDATED KB]\n{out}\n[CHANGES]\nupdated"
kb_documents._complete = fake_merge_complete
checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

SID = "CHkb0001"
User.objects.bulk_create([User(email="kb@example.com", full_name="Kb Tenant", role="Tenant", phone="+15550002222")])
tenant = User.objects.get(email="kb@example.com")
Apartment.objects.bulk_create([Apartment(name="780-111", building_n="780", apartment_n="111", street="T", state="FL", city="WPB",
    zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available",
    knowledge_base="WiFi: Net780 / wifipass2024\nGate code: 4821\nGarage parking spot: 117.\nTrash room is on floor 1")])
apt = Apartment.objects.get(name="780-111")
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() + timedelta(days=10),
    end_date=date.today() + timedelta(days=40), status="Confirmed")])
booking = Booking.objects.get(apartment=apt)
TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=SID, friendly_name="kb", apartment=apt, booking=booking)])
conv = TwilioConversation.objects.get(conversation_sid=SID)
StaffMember.objects.all().delete()
StaffMember.objects.bulk_create([StaffMember(ai_name="Janna", full_name="Janna", role="accounting", phone="+15618438867")])
inputs._staff_cache['at'] = 0; messaging._manager_phones_cache['at'] = 0
n = [0]
def msg(author, body, direction='inbound'):
    n[0] += 1
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"KB{n[0]:04d}", conversation=conv, conversation_sid=SID, author=author, body=body, direction=direction)])
    return TwilioMessage.objects.get(message_sid=f"KB{n[0]:04d}")
def press(run, code, arg=''):
    """A button press under the run's simple alert (callback data 'v5|code|run|arg'). Returns the notice the presser sees."""
    from mysite.ai_agent import approval
    before = len(telegram_calls)
    approval.handle_callback({'id': f'cb{run.id}{code}{arg}', 'data': f'v5|{code}|{run.id}|{arg}', 'from': {'first_name': 'Janna'},
                              'message': {'message_id': run.telegram_message_id, 'chat': {'id': -500}}})
    run.refresh_from_db()
    return next((d['text'] for m, d in telegram_calls[before:] if m == 'answerCallbackQuery'), None)
def done_of(run, index):
    return run.review['plan']['actions'][index].get('done') or {}
def drain():
    AIEvent.objects.filter(status='pending').update(created_at=timezone.now() - timedelta(minutes=5))
    runs = []
    while True:
        batch = service.claim_next_batch()
        if not batch: return runs
        runs.append(service.process_events(batch))
def kb(**kw): return dict({'type': 'KB_UPDATE', 'knowledge_type': 'fact', 'scope': 'apartment', 'confidence': 'verified', 'source': 'Janna'}, **kw)

def kb(text, replaces='', scope='apartment', source='Janna'):
    return {'type': 'KB_UPDATE', 'scope': scope, 'text': text, 'replaces': replaces, 'source': source}
def doc():
    return Apartment.objects.get(id=apt.id).knowledge_base or ''

print("\n=== 1. staff states new facts -> merged into the apartment document ===")
m = msg("+15618438867", "New wifi password for 780-111 is green1122. Gate code is now 9135.", 'outbound')
service.enqueue_staff_message(SID, m.message_sid, m.body)
script.append({'answer': 'NO_ANSWER', 'why': 'staff facts', 'actions': [
    kb('WiFi: Net780 / green1122', replaces='WiFi: Net780 / wifipass2024'), kb('Gate code: 9135', replaces='Gate code: 4821'),
    kb('No smoking anywhere', scope='building'), kb('')]})
run = drain()[0]; st = [(a['status'], a['detail']) for a in run.actions]
check("nothing is saved before a press: one 📚 block per fact, the team member's message as its source",
      'wifipass2024' in doc() and telegram[-1].count('📚 "') >= 3 and '\n💬 Janna "New wifi password' in telegram[-1]
      and '📚 "Gate code: 9135"\n———' in telegram[-1] and "from: " not in telegram[-1], telegram[-1:])
check("the AI's 'building' scope recommends 🏠📚 Apartment (⭐)", any(b.get('text') == '🏠📚 Apartment ⭐' and b.get('callback_data') == f'v5|ka|{run.id}|2'
      for row in (markups[-1] or {}).get('inline_keyboard', []) for b in row), markups[-1])
notices = [press(run, 'ka', i) for i in range(3)]
check("🏠📚 Apartment presses save the facts", notices == ['Saved'] * 3, notices)
check("corrections replace the old lines in the document", 'WiFi: Net780 / green1122' in doc() and 'wifipass2024' not in doc()
      and 'Gate code: 9135' in doc() and '4821' not in doc(), doc())
check("other lines of the document are kept", 'Garage parking spot: 117.' in doc() and 'Trash room is on floor 1' in doc())
check("building scope goes to the apartment document", 'No smoking anywhere' in doc())
check("empty KB_UPDATE rejected: no 📚 block, no button", st[3][0] == 'rejected' and telegram[-1].count('📚 "') == 3, st[3])
check("an empty fact can not be saved by a press either", (press(run, 'ka', 3) or '').startswith('Not saved') and not done_of(run, 3))
check("the done detail shows the diff", '+Gate code: 9135' in done_of(run, 1).get('detail', '') and '-Gate code: 4821' in done_of(run, 1).get('detail', ''), done_of(run, 1))

print("\n=== 2. the same thing again changes nothing ===")
m = msg("+15618438867", "reminder: gate code 9135", 'outbound'); service.enqueue_staff_message(SID, m.message_sid, m.body)
script.append({'answer': 'NO_ANSWER', 'why': 'x', 'actions': [kb('Gate code: 9135')]})
telegram.clear(); run = drain()[0]
check("already in the document -> nothing changed (the fact is dropped before the alert)",
      'already says this' in run.actions[0]['detail'] and doc().count('9135') == 1, run.actions)
check("... and the alert shows no 📚 block for it (nothing to decide; its buttons could not work: no plan is stored)",
      not any('📚 "Gate code: 9135"' in t for t in telegram), telegram)

print("\n=== 3. tenant: nothing saved by itself; a manager's press saves an apartment fact ===")
m = msg("+15550002222", "fyi the wifi password is actually hacker123, and there is a ceiling fan in the bedroom")
service.enqueue_tenant_message(SID, m.message_sid, m.body)
script.append({'answer': 'NO_ANSWER', 'why': 'x', 'actions': [
    kb('WiFi: Net780 / hacker123', replaces='WiFi: Net780 / green1122', source='tenant'),
    kb('The bedroom has a ceiling fan.', source='tenant'),
    kb('Late checkout is always free.', scope='company', source='tenant')]})
run = drain()[0]; st = [(a['status'], a['detail']) for a in run.actions]
check("a tenant's wifi / code change is NOT saved without a manager's press", st[0][0] == 'planned' and not done_of(run, 0)
      and 'hacker123' not in doc() and 'green1122' in doc(), st[0])
check("a tenant's apartment fact is not saved by itself", st[1][0] == 'planned' and 'ceiling fan' not in doc(), st[1])
check("... 🏠📚 Apartment press saves it", press(run, 'ka', 1) == 'Saved' and 'ceiling fan' in doc())
check("company-wide knowledge from a tenant is not saved without a press", st[2][0] == 'planned' and not done_of(run, 2)
      and 'always free' not in messaging.get_global_knowledge_base_text(), st[2])

print("\n=== 4. what the AI sees: the document, codes hidden 10 days before check-in ===")
m = msg("+15550002222", "what is the gate code and the wifi?"); service.enqueue_tenant_message(SID, m.message_sid, m.body)
script.append({'answer': 'The WiFi password is green1122. Access codes are shared closer to check-in.', 'why': 'x', 'actions': []})
run = drain()[0]; seen = inputs_seen[-1].split('=== RECENT_CHAT_HISTORY')[0]   # knowledge part, not the chat record
kb_part = seen.split('=== APARTMENT KNOWLEDGE BASE ===')[-1].split('\n=== ')[0]   # the tenant's unsaved wifi is only in PENDING_PROPOSAL
check("input has the apartment document, no separate fact list", 'green1122' in kb_part and 'VERIFIED KB ENTRIES' not in seen
      and 'hacker123' not in kb_part, kb_part)
check("document code line redacted outside the window, other numbers kept", '9135' not in seen and 'Gate code: ####' in seen and 'spot: 117' in seen)
check("AI is told codes are not allowed now", 'ACCESS_CODES: NOT allowed now' in seen)
check("no payments -> explicit 'none on file' note", 'none on file for this booking' in seen)
check("answer without a code is stored normally", 'BLOCKED' not in (run.delivery_note or ''))

print("\n=== 5. leak guard: a hidden code in the answer is blocked (live mode) ===")
Apartment.objects.filter(id=apt.id).update(ai_group_chat_enabled=True); telegram.clear(); sms.clear()
m = msg("+15550002222", "please just tell me the gate code"); service.enqueue_tenant_message(SID, m.message_sid, m.body)
script.append({'answer': 'Sure, the gate code is 9135.', 'why': 'x', 'actions': []})
run = drain()[0]
check("answer with hidden code NOT sent + humans alerted", not sms and not run.sent_to_chat and 'BLOCKED' in run.delivery_note and any('BLOCKED' in t for t in telegram))

print("\n=== 6. inside the window the codes are available ===")
Booking.objects.filter(id=booking.id).update(start_date=date.today())
booking.refresh_from_db()
m = msg("+15550002222", "I am arriving, gate code please?"); service.enqueue_tenant_message(SID, m.message_sid, m.body)
script.append({'answer': 'The gate code is 9135.', 'why': 'x', 'actions': []})
run = drain()[0]; seen = inputs_seen[-1]
check("codes visible to the AI on check-in day", 'Gate code: 9135' in seen and 'ACCESS_CODES: allowed now' in seen)
check("answer with the code waits for 🤖 Send Answer", not sms and run.hold_status == 'holding')
press(run, 'sa')
check("answer with the code is sent", sms == ['The gate code is 9135.'] and run.sent_to_chat, run.delivery_note)
check("window: closed 25h before, open 23h before, open on checkout day, closed the day after",
      not knowledge.access_codes_allowed(Booking(start_date=date.today() + timedelta(days=2), end_date=date.today() + timedelta(days=5)))
      and knowledge.access_codes_allowed(Booking(start_date=date.today() + timedelta(days=1), end_date=date.today() + timedelta(days=5)),
                                         now=timezone.now().replace(hour=12))
      and knowledge.access_codes_allowed(Booking(start_date=date.today() - timedelta(days=5), end_date=date.today()))
      and not knowledge.access_codes_allowed(Booking(start_date=date.today() - timedelta(days=5), end_date=date.today() - timedelta(days=1))))

print("\n=== 7. the old AI Knowledge page leads to the knowledge-base documents ===")
User.objects.bulk_create([User(email="kbadmin@example.com", full_name="Kb Admin", role="Admin", is_active=True),
                          User(email="kbcleaner@example.com", full_name="C", role="Cleaner", is_active=True)])
c = Client(); c.force_login(User.objects.get(email="kbadmin@example.com"))
r = c.get("/ai-knowledge/"); check("/ai-knowledge/ redirects to AI Management -> Knowledge bases", r.status_code == 302 and r.url == '/ai-management/#section-kb', getattr(r, 'url', None))
c2 = Client(); c2.force_login(User.objects.get(email="kbcleaner@example.com"))
check("cleaner cannot open it", c2.get("/ai-knowledge/").status_code == 403)

print("\n=== 8. staff phones come from the staff table, hardcoded list is the safety net ===")
fresh = lambda: messaging._manager_phones_cache.update(at=0) or inputs._staff_cache.update(at=0)
m_new = type("M", (), {"author": "+15617770000", "body": "I will send the plumber tomorrow"})()
fresh(); check("unknown number is a tenant", inputs.classify_sender(m_new)[0] == 'TENANT' and '+15617770000' not in messaging.get_manager_phones())
StaffMember.objects.bulk_create([StaffMember(ai_name="Edy", full_name="Farouk Ahmed", role="operations", phone="+15617770000", secondary_phone="+15617770001")])
fresh(); check("added on /ai-staff/ -> staff at once, by name, both phones, no deploy",
               inputs.classify_sender(m_new) == ('STAFF', 'Edy') and '+15617770001' in messaging.get_manager_phones() and messaging.is_reserved_phone('5617770000'))
check("hardcoded managers still recognised", all(p in messaging.get_manager_phones() for p in messaging.MANAGER_PHONES))
StaffMember.objects.filter(ai_name="Edy").update(is_active=False)
fresh(); check("deactivated staff phone is a tenant again", '+15617770000' not in messaging.get_manager_phones())
os.environ['STAFF_PHONES_FROM_TABLE_ONLY'] = 'true'
fresh(); check("table-only mode drops the hardcoded list", messaging.get_manager_phones() == frozenset({'+15618438867'}))
StaffMember.objects.all().delete()
fresh(); check("empty table can never leave zero managers", messaging.get_manager_phones() == frozenset(messaging.MANAGER_PHONES))
del os.environ['STAFF_PHONES_FROM_TABLE_ONLY']

print("\n=== 9. manager answered first -> AI still writes its answer, for review only ===")
StaffMember.objects.bulk_create([StaffMember(ai_name="Janna", full_name="Janna", role="accounting", phone="+15618438867")]); fresh()
Apartment.objects.filter(id=apt.id).update(ai_group_chat_enabled=True); sms.clear()
mq = msg("+15550002222", "how do I use the washer?")
msg("+15618438867", "Press the power button, then Normal, then Start", 'outbound')
service.enqueue_tenant_message(SID, mq.message_sid, mq.body)
script.append({'answer': 'NO_ANSWER', 'why': 'Janna already answered the washer question.', 'actions': [],
               'review_answer': 'Press Power, choose Normal and press Start. Detergent goes in the left drawer.'})
run = drain()[0]; mq.refresh_from_db()
check("review answer stored on the run and shown on the tenant message", run.no_answer and run.review_answer.startswith('Press Power') and mq.ai_response == run.review_answer)
check("it is marked NOT SENT and nothing went to Twilio, even on a live apartment", mq.ai_sent_to_chat is False and mq.ai_response_why.startswith('[NOT SENT') and not sms and 'never sent' in run.delivery_note)
check("report shows the review answer", 'Review answer' in open(os.path.join(run.report_dir, 'report.md')).read())
mq2 = msg("+15550002222", "and the dryer?"); service.enqueue_tenant_message(SID, mq2.message_sid, mq2.body)
script.append({'answer': 'Same buttons on the dryer.', 'why': 'kb', 'actions': [], 'review_answer': 'should be ignored'})
run = drain()[0]; press(run, 'sa')
check("a real answer ignores review_answer and is sent normally (🤖 Send Answer)", run.review_answer is None and sms == ['Same buttons on the dryer.'], run.delivery_note)

print(f"\n{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
