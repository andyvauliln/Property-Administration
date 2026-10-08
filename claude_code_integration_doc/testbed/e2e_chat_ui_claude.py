"""
Chat-page AI tools, all on Claude (the OpenRouter backend was removed 2026-09-28):
- the helpers (rules, KB drafts, explanations) call oneshot.complete directly
- "Teach AI answer" adds a line to the answer lessons prompt, "Add KB rule" goes into the agent system prompt
- "Generate AI" / "Generate all" run the agent in the background: nothing sent, no action executed
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

import json
import threading
from django.test import Client
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement, AIRun, AIIssue)
from mysite.ai_agent import runner, config, prompts, oneshot, regenerate, knowledge
import mysite.views.messaging as messaging
from django.conf import settings as _s
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_chat_ui"
config.WORK_DIR = config.RUNS_DIR / "_cwd"

# ---- fakes ---------------------------------------------------------------------------------
script = []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None):
    return {'ok': True, 'error': None, 'output': script.pop(0), 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 5, 'timed_out': False}
runner.run_claude = fake_run_claude
oneshot_calls = []
def fake_complete(prompt, system=None, model=None, timeout=None):
    oneshot_calls.append((system, prompt, model))
    return {'text': '- Early check-in depends on cleaning; confirm the day before.', 'model': 'claude-fake',
            'usage': {'input_tokens': 10, 'output_tokens': 5}, 'cost_usd': 0.001, 'duration_ms': 3}
oneshot.complete = fake_complete

class _SyncThread:
    """The regenerate worker thread runs inline so the test is deterministic."""
    def __init__(self, target, args=(), **kw): self.target, self.args = target, args
    def start(self): self.target(*self.args)
regenerate.threading = type('T', (), {'Thread': _SyncThread})

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

# ---- fixtures -------------------------------------------------------------------------------
if not User.objects.filter(email="cu@example.com").exists():
    User.objects.bulk_create([User(email="cu@example.com", full_name="Cora Ui", role="Tenant", phone="+15550009911")])
    User.objects.bulk_create([User(email="cu-admin@example.com", full_name="Admin Ui", role="Admin", is_active=True)])
tenant, admin = User.objects.get(email="cu@example.com"), User.objects.get(email="cu-admin@example.com")
Apartment.objects.bulk_create([Apartment(name="740-101", building_n="740", apartment_n="101", street="S", state="FL", city="WPB",
    zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_group_chat_enabled=True)])
apt = Apartment.objects.get(name="740-101")
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=2),
                                     end_date=date.today() + timedelta(days=20), status="Confirmed")])
SID = "CHchatui740"
TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=SID, friendly_name="740-101", apartment=apt,
                                                           booking=Booking.objects.get(apartment=apt))])
conv = TwilioConversation.objects.get(conversation_sid=SID)
def msg(sid_suffix, body, **extra):
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"CU{sid_suffix}", conversation=conv, conversation_sid=SID,
                                                     author="+15550009911", body=body, direction='inbound', **extra)])
    return TwilioMessage.objects.get(message_sid=f"CU{sid_suffix}")
m1 = msg("0001", "Can I check in at 10am tomorrow?")
m2 = msg("0002", "Also where do I park my car?")
m_sent = msg("0003", "What is the wifi password?", ai_response="Wifi is Guest123", ai_sent_to_chat=True)
m_short = msg("0004", "ok")

c = Client(); c.force_login(admin)

# ---- helpers use the Claude one-shot call -------------------------------------------------------
r = c.post(f"/chat/{SID}/messages/{m1.id}/answer-rule/generate/",
           json.dumps({'correct_answer': 'Depends on cleaning, we confirm the day before.'}), content_type='application/json')
check("Teach AI answer: rule generated through Claude", r.status_code == 200 and r.json().get('rule', '').startswith('- Early')
      and oneshot_calls, r.content[:300])

# ---- Teach AI answer -> answer lesson ---------------------------------------------------------
r = c.post(f"/chat/{SID}/messages/{m1.id}/answer-rule/save/",
           json.dumps({'rule': '- Early check-in depends on cleaning; confirm the day before.', 'scope': 'apartment'}),
           content_type='application/json')
from mysite.ai_agent import prompt_library
lessons = prompt_library.rule_lines('ai_agent_answer_lessons')
check("Teach AI answer saved as an apartment line of the answer lessons prompt", r.status_code == 200
      and r.json().get('saved_as') == 'lesson' and any(l.startswith(f"- [apartment #{apt.id}") and 'Early check-in' in l for l in lessons),
      (r.content[:300], lessons))
check("the agent sees the lesson", 'Early check-in depends on cleaning' in prompts.get_system_prompt(apt)[0])
r = c.post(f"/chat/{SID}/messages/{m1.id}/answer-rule/save/", json.dumps({'rule': 'Always greet by first name.'}),
           content_type='application/json')
check("default lesson scope is company-wide", any(l.startswith("- [company]") and 'Always greet by first name.' in l
      for l in prompt_library.rule_lines('ai_agent_answer_lessons')), r.content[:300])

# ---- Add KB rule -> the agent's KB rules only -----------------------------------------------------
import mysite.views.chat as chat_views
chat_views.is_kb_rule_eligible_message = lambda message: True
r = c.post(f"/chat/{SID}/messages/{m1.id}/kb-rule/save/",
           json.dumps({'rule': 'Do not save one-time schedule details.', 'scope': 'apartment'}), content_type='application/json')
check("KB rule saved to 'Agent - KB rules' only", r.status_code == 200 and r.json().get('prompt_key') == 'ai_agent_kb_rules'
      and not AIManagement.objects.filter(prompt_key__in=['ai_extract_check', 'ai_extract_global_check']).exists(), r.content[:300])
messaging.append_rule_to_agent_kb_rules("- Save building-wide trash rules.", scope='global')
system_prompt, source = prompts.get_system_prompt()
check("KB rules are in the agent system prompt", "KB RULES" in system_prompt
      and "- [apartment KB] Do not save one-time schedule details." in system_prompt
      and "- [company-wide KB] Save building-wide trash rules." in system_prompt, system_prompt[-400:])
check("system prompt source names the KB rules row", 'ai_agent_kb_rules' in source, source)

# ---- Generate AI (one message) --------------------------------------------------------------
issues_before = AIIssue.objects.count()
script.append({'answer': 'Early check-in depends on cleaning.', 'why': 'policy', 'actions': [
    {'type': 'CREATE_ISSUE', 'temp_id': 'new-1', 'category': 'other', 'priority': 'routine', 'summary': 'x'}]})
r = c.post(f"/chat/{SID}/messages/{m1.id}/generate-ai-answer/", json.dumps({}), content_type='application/json')
data = r.json()
check("Generate AI queues a Claude run", r.status_code == 200 and data.get('queued') and len(data.get('run_ids', [])) == 1, data)
m1.refresh_from_db()
check("answer stored on the message, not sent", m1.ai_response == 'Early check-in depends on cleaning.' and not m1.ai_sent_to_chat)
check("no action executed (replay)", AIIssue.objects.count() == issues_before)
run = AIRun.objects.get(id=data['run_ids'][0])
check("AIRun kept with report + simulated actions", run.delivery_note == regenerate.DONE_NOTE and run.report_dir
      and all(a['status'] == 'simulated' for a in run.actions), (run.delivery_note, run.actions))
r = c.get(f"/chat/{SID}/ai-regenerate-status/?runs={run.id}")
item = r.json()['runs'][0]
check("status endpoint reports the finished run", item['finished'] and item['ai_response'] == m1.ai_response
      and item['run']['url'] == f'/ai-runs/{run.id}/', item)

# ---- the answer that was really sent is not overwritten ------------------------------------
script.append({'answer': 'New wifi answer', 'why': 'kb', 'actions': []})
data = c.post(f"/chat/{SID}/messages/{m_sent.id}/generate-ai-answer/", json.dumps({}), content_type='application/json').json()
m_sent.refresh_from_db()
check("sent AI answer kept on the message", m_sent.ai_response == 'Wifi is Guest123' and m_sent.ai_sent_to_chat)
item = c.get(f"/chat/{SID}/ai-regenerate-status/?runs={data['run_ids'][0]}").json()['runs'][0]
check("...the new answer is still shown to the page", item['ai_response'] == 'New wifi answer'
      and item['ai_response_why'].startswith(regenerate.SENT_KEPT_PREFIX), item)

# ---- Generate all -----------------------------------------------------------------------------
script.extend([{'answer': 'A1', 'why': 'w', 'actions': []}, {'answer': 'NO_ANSWER', 'why': 'staff handles it', 'actions': []},
               {'answer': 'A3', 'why': 'w', 'actions': []}])
data = c.post(f"/chat/{SID}/generate-ai-answers/", json.dumps({}), content_type='application/json').json()
check("Generate all queues every real tenant message, skips 'ok'", data.get('queued') and len(data['run_ids']) == 3
      and data['skipped'] == 1 and data['total_customer_messages'] == 4, data)
m1.refresh_from_db(); m2.refresh_from_db()
check("answers stored in order", m1.ai_response == 'A1' and m2.ai_response is None and m2.ai_response_why == 'staff handles it',
      (m1.ai_response, m2.ai_response, m2.ai_response_why))
check("no Claude call left over", not script, script)

# ---- a failed run is reported, not lost ------------------------------------------------------
def failing_run(*a, **k):
    return {'ok': False, 'error': 'claude CLI timed out after 180s', 'output': None, 'events': [], 'result_event': None,
            'stdout': '', 'stderr': '', 'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': None,
            'duration_ms': 5, 'timed_out': True}
runner.run_claude = failing_run
data = c.post(f"/chat/{SID}/messages/{m2.id}/generate-ai-answer/", json.dumps({}), content_type='application/json').json()
item = c.get(f"/chat/{SID}/ai-regenerate-status/?runs={data['run_ids'][0]}").json()['runs'][0]
check("failed run: finished with the error", item['finished'] and 'timed out' in (item['error'] or ''), item)

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
