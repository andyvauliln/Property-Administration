"""
Legal / contract questions (user request 2026-09-25): the AI reads the contract (get_contract) and SUGGESTS an answer;
it is never sent until a manager confirms it in Telegram - not even when the 15-minute review window ends.
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
os.environ["AI_AGENT_REVIEW_HOLD_MINUTES"] = "15"
os.environ["AI_AGENT_APPROVAL"] = "timer"   # this file tests the 15-minute timer review (explicit approval: e2e_client_v4.py)
os.environ["AI_AGENT_ALERT_CHAT_ID"] = "-500"
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

import json
from django.utils import timezone
from mysite.models import User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement, AIEvent, AIRun, AIIssue
from mysite.ai_agent import service, runner, config, answer_review, clickup, prompts, contract
import mysite.ai_agent.team_notify as team_notify
import mysite.views.messaging as messaging
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_legal"
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
updates, interpreter_out = [], []
answer_review.fetch_updates = lambda: [updates.pop(0) for _ in range(len(updates))]
answer_review.run_interpreter = lambda prompt: interpreter_out.pop(0)
class _Map: list_id, channel_id, name = 'L1', None, 'list 730'
clickup.delivery_mode = lambda: 'api'
clickup.channel_for = lambda apartment: _Map()
clickup.post_message = lambda *a, **k: None
clickup.create_task = lambda *a, **k: ("tk1", "https://app.clickup.com/t/tk1")

script = []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    return {'ok': True, 'error': None, 'output': script.pop(0), 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 5, 'timed_out': False}
runner.run_claude = fake_run_claude

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

# ---- fixtures -------------------------------------------------------------------------------
if not User.objects.filter(email="lg@example.com").exists():
    User.objects.bulk_create([User(email="lg@example.com", full_name="Lena Legal", role="Tenant", phone="+15550007788")])
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
AIManagement.objects.update_or_create(prompt_key="ai_backend", defaults={'name': "backend", 'entry_type': "ai_model", 'content': "claude_cli"})
n = [0]
def say(sid, body, author="+15550007788"):
    n[0] += 1
    conv = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"LG{n[0]:04d}", conversation=conv, conversation_sid=sid,
                                                     author=author, body=body, direction='inbound')])
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
    say(sid, body); script.append(output); r = drain()[0]; r.refresh_from_db(); return r
def reply(run, text, who="Kevin", **decision):
    if decision:
        interpreter_out.append(dict(answer_review.EMPTY_DECISION, **decision))
    run.refresh_from_db()
    updates.append({'update_id': 70 + len(checks) + len(telegram), 'message': {
        'message_id': 9500 + len(checks), 'chat': {'id': -500}, 'from': {'first_name': who}, 'text': text,
        'date': int(timezone.now().timestamp()), 'reply_to_message': {'message_id': run.telegram_message_id}}})
    answer_review.poll_telegram()
    run.refresh_from_db()
    return telegram[-1][0]
def expire(run):
    AIRun.objects.filter(id=run.id).update(hold_until=timezone.now() - timedelta(seconds=1)); answer_review.release_due(); run.refresh_from_db()

# Runs left over by earlier test files (shared testbed DB): let them finish now so they do not count here
AIRun.objects.filter(hold_until__isnull=False).update(hold_until=timezone.now() - timedelta(seconds=1))
answer_review.release_due(); sms.clear(); telegram.clear()

SUGGESTED = "Per your agreement, cancelling within 30 days of arrival means the deposit is not refunded."
BASIS = 'CANCELLATION OF CONTRACT: "cancellations within 30 days of arrival forfeit the deposit"'
def legal(extra_actions=(), answer=SUGGESTED):
    return {'answer': answer, 'why': 'legal question about cancellation', 'needs_manager_confirmation': True,
            'contract_basis': BASIS, 'actions': [
                {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'summary': 'Tenant asks about cancellation refund', 'owner': 'Kevin',
                 'state': 'WAITING_FOR_KEVIN'},
                {'type': 'INTERNAL_ALERT', 'issue_id': 'new-1', 'priority': 'routine', 'responsible': ['Kevin'],
                 'text': 'legal question: cancellation refund'}, *extra_actions]}

# ---- 1. legal question: suggested answer held, alert says it waits for a manager -------------------
r1 = run_with(SID, "If I cancel now do I get my deposit back?", legal())
a1 = next(t for t, _ in reversed(telegram) if "deposit back" in t)
if os.environ.get('SHOW_ALERT'): print(a1 + "\n=====")
check("nothing sent at once", not sms)
check("run is held and marked as needing a manager", r1.hold_status == 'holding' and r1.review.get('needs_confirmation') is True
      and r1.review.get('contract_basis') == BASIS, (r1.hold_status, r1.review))
check("alert: LEGAL header right under the title, not even after the window",
      a1.splitlines()[2].startswith("⚖️ LEGAL QUESTION - NEEDS MANAGER CONFIRMATION") and "NOT even after the 15-min review window" in a1, a1)
check("alert: suggested answer + contract basis, plan runs by itself EXCEPT the legal answer",
      "SUGGESTED answer based on the contract" in a1 and SUGGESTED in a1 and f"📄 Contract basis: {BASIS}" in a1
      and "except the legal answer" in a1 and "Send this answer to the tenant" not in a1, a1)
chat_why = json.dumps(TwilioMessage.objects.filter(conversation_sid=SID).order_by('-id').first().__dict__, default=str)
check("CRM chat page marks it as a legal suggestion", "LEGAL - SUGGESTED ANSWER" in chat_why, chat_why[:400])

# ---- 2. window ends: the plan runs, the answer does NOT ----------------------------------------------
expire(r1)
done = telegram[-1][0]
check("window over: plan applied, answer still held, no SMS", not sms and r1.hold_status == 'holding'
      and r1.review['plan']['status'] == 'applied' and AIIssue.objects.filter(created_by_run_id=r1.id).exists(), (sms, r1.hold_status))
check("thread is told the legal answer still waits", "⚖️ Answer: NOT sent - legal question" in done, done)
before = len(telegram)
answer_review.release_due(now=timezone.now() + timedelta(hours=5))
check("hours later: still not sent and not picked again", not sms and len(telegram) == before
      and AIRun.objects.get(id=r1.id).hold_status == 'holding')
check("status text for a 'test' reply says it waits for a manager",
      "LEGAL - waits for a manager" in answer_review._answer_status(AIRun.objects.get(id=r1.id)))

# ---- 3. manager "ok" sends it -------------------------------------------------------------------------
t = reply(r1, "ok")
check("'ok' sends the suggested answer", sms == [(SID, SUGGESTED)] and r1.hold_status == 'sent', (sms, t))

# ---- 4. manager correction sends the corrected text instead --------------------------------------------
sms.clear()
r2 = run_with(SID2, "Can I break my lease early?", legal(answer="Early termination costs two months of rent."))
expire(r2)
check("still held after the window", not sms and r2.hold_status == 'holding')
t = reply(r2, "tell her the fee is one month rent", decision='replace', corrected_answer="The early termination fee is one month of rent.")
check("correction is sent instead", sms == [(SID2, "The early termination fee is one month of rent.")] and r2.hold_status == 'corrected', (sms, t))

# ---- 5. a follow-up message can not slip the legal answer out through the normal timer ------------------
sms.clear()
r3 = run_with(SID3, "What is the fine for smoking?", legal(answer="The contract sets a $500 fine for smoking."))
r4 = run_with(SID3, "hello??", {'answer': "The contract sets a $500 fine for smoking. Anything else?", 'why': 'follow-up', 'actions': []})
r3.refresh_from_db()
check("newer answer replaces the legal draft and inherits the manager confirmation",
      r3.hold_status == 'superseded' and r4.hold_status == 'holding' and r4.review.get('needs_confirmation') is True, (r3.hold_status, r4.review))
expire(r4)
check("inherited: not sent after the window", not sms and r4.hold_status == 'holding')
r5 = run_with(SID3, "thanks", {'answer': "You're welcome!", 'why': 'thanks', 'actions': []})
check("pending input tells the AI the draft is LEGAL", "[LEGAL - waits for a manager to confirm]" in (answer_review.pending_block(SID3) or '')
      and r5.review.get('needs_confirmation') is True)
reply(r5, "don't send")
check("\"don't send\" cancels it", not sms and AIRun.objects.get(id=r5.id).hold_status == 'cancelled')

# ---- 6. Telegram down: a legal answer is NOT sent without review (normal answers are) -------------------
tg_fail[0] = True
r6 = run_with(SID2, "Am I liable for the broken window?", legal(answer="Per the contract, damage is charged to the tenant."))
tg_fail[0] = False
check("Telegram down: legal answer stays held, error reported", not sms and r6.hold_status == 'holding'
      and any("LEGAL answer waits for a manager" in t for t, _ in telegram))

# ---- 7. test mode: same, and "ok" never goes to Twilio -------------------------------------------------
r7 = run_with(SID_TEST, "Can I sublet the unit?", legal(answer="Subletting is not allowed under your agreement."))
expire(r7)
check("test mode: held after the window", r7.hold_status == 'holding' and not sms)
reply(r7, "ok")
check("test mode 'ok': final, nothing to Twilio", AIRun.objects.get(id=r7.id).hold_status == 'sent' and not sms)

# ---- 8. a normal (non-legal) answer still goes out when the window ends ----------------------------------
sms.clear()
r8 = run_with(SID, "What's the wifi password?", {'answer': "It's on the fridge.", 'why': 'wifi', 'actions': [],
                                                  'needs_manager_confirmation': False, 'contract_basis': ''})
expire(r8)
check("non-legal answer: sent by the timer as before", sms == [(SID, "It's on the fridge.")] and not r8.review.get('needs_confirmation'))

# ---- 9. contract text for the agent: bank lines and links removed ----------------------------------------
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
check("tool allowed + prompt + schema describe the legal flow",
      'mcp__crm__get_contract' in config.ALLOWED_MCP_TOOLS and "LEGAL QUESTIONS" in prompts.get_system_prompt()[0]
      and 'needs_manager_confirmation' in json.loads(config.SCHEMA_PATH.read_text())['properties']
      and 'def get_contract' in config.MCP_TOOLS_PATH.read_text())

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
