"""
All AI prompts live in AIManagement (prompt_library): seeding, the agent reads the DB, chat-page rules and Telegram
lessons write into prompts, the AI Management prompts panel. Throwaway DB, Claude faked.
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

import io
import json
import re
from django.apps import apps as django_apps
from django.conf import settings as _s
from django.core.management import call_command
from django.test import Client
from mysite.models import User, Apartment, Booking, TwilioConversation, TwilioMessage, AIManagement
from mysite.ai_agent import config, prompts, prompt_library as lib, oneshot, runner, service
import mysite.views.messaging as messaging

config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_prompts"
config.WORK_DIR = config.RUNS_DIR / "_cwd"

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

oneshot.complete = lambda prompt, system=None, model=None, timeout=None: {
    'text': '- When a tenant asks about late checkout, say it depends on the next booking.', 'model': 'fake',
    'usage': {}, 'cost_usd': 0, 'duration_ms': 1}
seen = []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None, images=None):
    seen.append(system_prompt)
    return {'ok': True, 'error': None, 'output': {'answer': 'NO_ANSWER', 'why': 'x', 'actions': []}, 'events': [],
            'result_event': {}, 'stdout': '', 'stderr': '', 'command': 'fake', 'mcp_config': {}, 'model': 'fake',
            'exit_code': 0, 'duration_ms': 1, 'timed_out': False}
runner.run_claude = fake_run_claude

# ---- fixtures -------------------------------------------------------------------------------
if not User.objects.filter(email="pr@example.com").exists():
    User.objects.bulk_create([User(email="pr@example.com", full_name="Pia Prompt", role="Tenant", phone="+15550006611")])
    User.objects.bulk_create([User(email="pr-admin@example.com", full_name="Admin Pr", role="Admin", is_active=True)])
    User.objects.bulk_create([User(email="pr-tenant2@example.com", full_name="Tom T", role="Tenant", phone="+15550006612")])
admin = User.objects.get(email="pr-admin@example.com")
for name in ("760-301", "760-302"):
    Apartment.objects.bulk_create([Apartment(name=name, building_n="760", apartment_n=name[-3:], street="S", state="FL",
        city="WPB", zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available")])
apt1, apt2 = Apartment.objects.get(name="760-301"), Apartment.objects.get(name="760-302")
Booking.objects.bulk_create([Booking(apartment=apt1, tenant=User.objects.get(email="pr@example.com"),
                                     start_date=date.today(), end_date=date.today() + timedelta(days=9), status="Confirmed")])
SID = "CHprompts760"
TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=SID, friendly_name="760-301", apartment=apt1,
                                                           booking=Booking.objects.get(apartment=apt1))])
conv = TwilioConversation.objects.get(conversation_sid=SID)
TwilioMessage.objects.bulk_create([TwilioMessage(message_sid="PR0001", conversation=conv, conversation_sid=SID,
                                                 author="+15550006611", body="Can I check out at 2pm?", direction='inbound')])
msg = TwilioMessage.objects.get(message_sid="PR0001")
AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY)).delete()
AIManagement.objects.update_or_create(prompt_key="ai_backend", defaults={'name': 'b', 'entry_type': 'ai_model', 'content': 'claude_cli'})

# ---- 1. seeding -----------------------------------------------------------------------------------
out = io.StringIO(); call_command('sync_ai_prompts', '--dry-run', stdout=out)
check("dry run lists missing prompts and creates nothing", 'MISSING' in out.getvalue()
      and not AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY)).exists())
call_command('sync_ai_prompts', stdout=io.StringIO())
keys = set(AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY)).values_list('prompt_key', flat=True))
check("every registry prompt is in the DB", keys == set(lib.BY_KEY), set(lib.BY_KEY) - keys)
row = AIManagement.objects.get(prompt_key='ai_agent_system')
check("seeded rows are prompts with name + what/how/when description",
      row.entry_type == 'prompt' and row.name == 'Agent - main system prompt' and row.description.startswith('WHAT:'))
check("seed-only note of the .md file is not part of the prompt", 'SEED ONLY' not in row.content and row.content.startswith('AI PROPERTY MANAGER'))
before = {e.prompt_key: e.updated_at for e in AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY))}
call_command('sync_ai_prompts', stdout=io.StringIO())
after = {e.prompt_key: e.updated_at for e in AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY))}
check("running sync again changes nothing", before == after)

# ---- 2. the agent prompt comes from the DB and is unchanged by the move --------------------------------
file_text = re.sub(r"\A<!--.*?-->\s*", '', config.DEFAULT_SYSTEM_PROMPT_PATH.read_text(encoding='utf-8'), flags=re.S)
old_style = prompts._fill_placeholders(file_text).strip() + "\n\n" + prompts.RUNTIME_NOTES
new_prompt, source = prompts.get_system_prompt(apt1)
check("composed agent prompt is byte-identical to the old file + RUNTIME_NOTES", new_prompt == old_style,
      (len(new_prompt), len(old_style)))
check("source names the DB prompts", source == 'DB:ai_agent_system + DB:ai_agent_runtime_notes', source)

AIManagement.objects.filter(prompt_key='ai_agent_system').update(content="You are TestBot for {{COMPANY_NAME}}.")
service.run_agent('TENANT_MESSAGE', SID, apt1, conv.booking, [msg], 'test')
check("an edit in the DB is what the agent gets", seen and seen[-1].startswith(f"You are TestBot for {config.COMPANY_NAME}."), seen[-1][:80] if seen else None)
AIManagement.objects.filter(prompt_key='ai_agent_system').delete()
text = lib.raw('ai_agent_system')
check("a missing row is created again from the default", AIManagement.objects.filter(prompt_key='ai_agent_system').exists()
      and text.startswith('AI PROPERTY MANAGER'))
AIManagement.objects.filter(prompt_key='ai_agent_system').update(content='   ')
check("a blank text prompt falls back to the default", lib.raw('ai_agent_system').startswith('AI PROPERTY MANAGER'))
lib.seed('ai_agent_system', force=True)

# ---- 3. placeholders ---------------------------------------------------------------------------------
filled = lib.get('ai_agent_clickup_delivery', steps="STEP 1: x", data='{"MESSAGE": "{hi}"}')
check("safe fill: placeholders replaced, JSON braces and braces in values kept",
      'STEP 1: x' in filled and '{"MESSAGE": "{hi}"}' in filled and '{"tasks": [{"name"' in filled and '{steps}' not in filled)
check("ClickUp read prompt unchanged by the move", lib.get('ai_agent_clickup_read', task_id='abc') ==
      'Call clickup_get_task with task_id "abc". Then call clickup_get_task_comments with task_id "abc". Then reply with the single word: done.')
merge_prompt = lib.get('ai_kb_merge', document_label='the 760-301 knowledge base', knowledge_base='Wi-Fi: A',
                       new_information='Wi-Fi: B', replaces='Wi-Fi: A', source='Janna', kb_rules='(none)')
check("KB merge prompt fills every placeholder", 'Current document:\nWi-Fi: A' in merge_prompt and '(from Janna)' in merge_prompt
      and '{' not in merge_prompt.split('[UPDATED KB]')[0].replace('{{', ''))
check("missing placeholder detected", lib.missing_placeholders('ai_agent_clickup_read', 'no id here') == ['task_id'])
AIManagement.objects.filter(prompt_key='ai_oneshot_system').update(content='Custom helper system.')
check("one-shot client default system prompt comes from the DB", oneshot._default_system() == 'Custom helper system.')

# ---- 4. Teach AI answer + Telegram lessons -> one prompt ---------------------------------------------------
c = Client(); c.force_login(admin)
url = f"/chat/{SID}/messages/{msg.id}/answer-rule/save/"
r = c.post(url, json.dumps({'rule': '- Late checkout depends on the next booking; offer to ask the manager.', 'scope': 'apartment'}),
           content_type='application/json')
lines = lib.rule_lines('ai_agent_answer_lessons')
check("Teach AI answer adds an apartment lesson line to the prompt", r.status_code == 200 and len(lines) == 1
      and lines[0].startswith(f"- [apartment #{apt1.id} 760-301] ") and 'Late checkout depends' in lines[0], (r.content[:200], lines))
detail = lib.upsert_lesson(apt1, lines[0].split('] ')[1].split(':')[0], 'Late checkout is free until 1pm.')
lines = lib.rule_lines('ai_agent_answer_lessons')
check("same scope + key replaces the older line", len(lines) == 1 and 'free until 1pm' in lines[0] and 'replaced 1 older' in detail, (detail, lines))
lib.upsert_lesson(None, 'pets', 'Say pets need written approval from the manager.')
lib.upsert_lesson(apt2, 'parking', 'Parking spot 12 is in the back lot.')
p1, _ = prompts.get_system_prompt(apt1)
p2, _ = prompts.get_system_prompt(apt2)
check("agent of apartment 1 gets company + its own lessons, not apartment 2's",
      'ANSWER_LESSONS' in p1 and 'free until 1pm' in p1 and 'written approval' in p1 and 'spot 12' not in p1)
check("agent of apartment 2 gets its own lesson, not apartment 1's", 'spot 12' in p2 and 'free until 1pm' not in p2 and 'written approval' in p2)

from mysite.ai_agent import answer_review
class _Run:  # just what _lesson_scope needs
    conversation_sid = SID; id = 1
answer_review._apartment = lambda run: apt1
apartment, where = answer_review._lesson_scope(_Run(), {'lesson_scope': 'company'})
lib.upsert_lesson(apartment, 'deposit_return', 'Deposits are returned within 14 days after checkout.')
check("a Telegram lesson lands in the same prompt", any('deposit_return' in l for l in lib.rule_lines('ai_agent_answer_lessons')))

# ---- 5. Add KB rule -> agent KB rules + extract check ------------------------------------------------------
TwilioMessage.objects.bulk_create([TwilioMessage(message_sid="PR0002", conversation=conv, conversation_sid=SID,
                                                 author="+15612205252", body="The courier comes at 3pm today.", direction='outbound')])
mgr = TwilioMessage.objects.get(message_sid="PR0002")
r = c.post(f"/chat/{SID}/messages/{mgr.id}/kb-rule/save/", json.dumps({'rule': 'Never save one-off delivery times.', 'scope': 'apartment'}),
           content_type='application/json')
kb_rules = lib.raw('ai_agent_kb_rules')
check("Add KB rule writes the agent KB rules prompt", '- [apartment KB] Never save one-off delivery times.' in kb_rules, (r.status_code, r.content[:200], kb_rules))
check("KB rules are part of the agent prompt", 'KB RULES' in prompts.get_system_prompt(apt1)[0])

# ---- 6. migration 0095: fact rows -> documents, old staff rules -> agent prompts, legacy rows deleted ------------
import importlib
mig = importlib.import_module('mysite.migrations.0095_knowledge_documents_only')
class _Fact:
    def __init__(self, **kw): self.__dict__.update(dict({'apartment_id': None, 'building': None}, **kw))
class _Facts:
    rows = [_Fact(scope='apartment', apartment_id=apt2.id, key='pull_out_couch', value='Yes, the apartment has a pull-out couch'),
            _Fact(scope='company', key='quiet_hours', value='22:00 - 07:00')]
    def filter(self, **kw): return self
    def order_by(self, *a): return self.rows
class _Apps:
    def get_model(self, app, name):
        return type('M', (), {'objects': _Facts()}) if name == 'AIKnowledge' else django_apps.get_model(app, name)
AIManagement.objects.update_or_create(prompt_key='ai_extract_check', defaults={'name': 'x', 'entry_type': 'prompt',
    'content': "RULES:\n- Reply NO — never save one-time billing overages, payment requests, or transaction-specific owner instructions to the KB.\nManager message to evaluate:"})
AIManagement.objects.update_or_create(prompt_key='ai_answer_system', defaults={'name': 'x', 'entry_type': 'prompt',
    'content': "You answer tenants.\n- If a tenant says they will respond later the same day, send a brief follow-up asking for an update."})
AIManagement.objects.update_or_create(prompt_key='ai_conversation_model', defaults={'name': 'x', 'entry_type': 'ai_model', 'content': 'openai/x'})
mig.forwards(_Apps(), None)
apt2.refresh_from_db()
check("a fact row becomes a line of its apartment document", 'Pull out couch: Yes, the apartment has a pull-out couch' in (apt2.knowledge_base or ''), apt2.knowledge_base)
check("a company fact becomes a line of the global document", 'Quiet hours: 22:00 - 07:00' in messaging.get_global_knowledge_base_text())
check("a staff KB rule in the old extract prompt moves to the agent KB rules",
      '- [apartment KB] Never save one-time billing overages, payment requests, or transaction-specific owner instructions to the knowledge base.' in lib.raw('ai_agent_kb_rules'))
check("a staff answer rule in the old answer prompt moves to the answer lessons",
      any('tenant_will_reply_later' in l for l in lib.rule_lines('ai_agent_answer_lessons')))
check("legacy prompt / backend rows are deleted", not AIManagement.objects.filter(
      prompt_key__in=['ai_extract_check', 'ai_answer_system', 'ai_conversation_model', 'ai_backend']).exists())
mig.forwards(_Apps(), None)
check("running it twice adds nothing twice", (apt2.knowledge_base or '').count('Pull out couch') == 1
      and lib.raw('ai_agent_kb_rules').count('billing overages') == 1)
apt2.knowledge_base = None; apt2.save()

# ---- 7. AI Management UI -------------------------------------------------------------------------------------
anon = Client()
check("prompt endpoints need login", anon.get('/ai-management/prompts/ai_agent_system/').status_code in (302, 403))
html = c.get('/ai-management/').content.decode()
check("prompts panel shows every prompt with what / how / when", all(f'id="prompt-{k}"' in html for k in lib.BY_KEY)
      and 'What: ' in html and 'When: ' in html and 'Legacy' not in html)
d = c.get('/ai-management/prompts/ai_agent_runtime_notes/').json()
check("detail returns live text, default and diff", d['content'] == d['default'] and d['edited'] is False and d['diff'] == '')
r = c.post('/ai-management/prompts/ai_agent_clickup_read/', json.dumps({'content': 'Read the task please.'}), content_type='application/json')
check("saving from the UI works and warns about a removed placeholder", r.json().get('missing_placeholders') == ['task_id']
      and lib.raw('ai_agent_clickup_read') == 'Read the task please.')
check("an empty text prompt is refused", c.post('/ai-management/prompts/ai_agent_system/', json.dumps({'content': ' '}),
                                              content_type='application/json').status_code == 400)
c.post('/ai-management/prompts/ai_agent_clickup_read/reset/')
check("reset restores the default", lib.raw('ai_agent_clickup_read') == lib.CLICKUP_READ_DEFAULT.strip())
pv = c.get(f'/ai-management/prompts/preview/?apartment={apt2.id}').json()
check("preview shows the parts the agent gets for that apartment", [p['key'] for p in pv['parts']] ==
      ['ai_agent_system', 'ai_agent_runtime_notes', 'ai_agent_kb_rules', 'ai_agent_answer_lessons']
      and 'spot 12' in pv['parts'][-1]['text'] and 'free until 1pm' not in pv['parts'][-1]['text'])
chat_html = c.get(f'/chat/{SID}/').content.decode()
check("chat page prompt buttons open the agent prompts on the Claude backend",
      '/chat/knowledge-base-prompts/ai_agent_system/' in chat_html and '/chat/knowledge-base-prompts/ai_agent_answer_lessons/' in chat_html)
check("chat prompt editor opens agent prompts", c.get('/chat/knowledge-base-prompts/ai_agent_answer_lessons/').json().get('success'))

# ---- 8. page sections, knowledge bases, no duplicates -------------------------------------------------------------
from mysite.views.messaging import get_global_knowledge_base_text, save_global_knowledge_base_text, build_full_context
save_global_knowledge_base_text("Quiet hours are 22:00-07:00.")
AIManagement.objects.update_or_create(prompt_key='ai_clickup_writes', defaults={'name': 'w', 'content': 'off', 'entry_type': 'knowledge'})
AIManagement.objects.update_or_create(prompt_key='welcome_message_template', defaults={'name': 'Welcome', 'content': 'Hi', 'entry_type': 'sms_template'})
html = c.get('/ai-management/').content.decode()
check("three collapsible sections", all(f'<details id="section-{x}"' in html for x in ('settings', 'kb', 'prompts')))
table_ids = set(int(i) for i in re.findall(r'aimanagement-row" data-id="(\d+)"', html))
prompt_ids = set(AIManagement.objects.filter(prompt_key__in=[*lib.BY_KEY, 'global_knowledge_base']).values_list('id', flat=True))
check("settings table has no prompt or global-KB rows (no duplicates)", table_ids and not (table_ids & prompt_ids)
      and AIManagement.objects.get(prompt_key='welcome_message_template').id in table_ids, (table_ids & prompt_ids))
check("knowledge section shows the global KB and apartments", 'Quiet hours are 22:00-07:00.' in html and f'id="kb-apt-{apt1.id}"' in html)
r = c.post('/ai-management/knowledge-base/', json.dumps({'content': 'Quiet hours are 23:00-07:00.'}), content_type='application/json')
check("global KB saved from AI Management", r.status_code == 200 and get_global_knowledge_base_text() == 'Quiet hours are 23:00-07:00.')
r = c.post(f'/ai-management/knowledge-base/{apt1.id}/', json.dumps({'content': 'Trash room on floor 1.'}), content_type='application/json')
apt1.refresh_from_db()
check("apartment KB saved from AI Management", r.status_code == 200 and apt1.knowledge_base == 'Trash room on floor 1.')
check("regression: saving the global KB keeps other 'knowledge'-type rows (ClickUp writes switch)",
      AIManagement.objects.filter(prompt_key='ai_clickup_writes', content='off').exists())
check("regression: the switch value is not part of the global KB text", 'off' not in get_global_knowledge_base_text().split())
ctx, _src = build_full_context(SID, apt1, conv.booking, include_history=False)
check("regression: AI context global KB has only the global KB row", 'Quiet hours are 23:00-07:00.' in ctx
      and '\noff' not in ctx.split('=== GLOBAL KNOWLEDGE BASE ===')[1][:200])
check("KB endpoints need login", anon.post('/ai-management/knowledge-base/').status_code in (302, 403))

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
