"""
The knowledge base is only the documents (one per apartment + the global one): the agent's KB_UPDATE merge and its
guard, company scope, a tenant's credential change that waits for the knowledge button of its simple alert, chat-page
"Generate" from a whole chat, and
no legacy AI when queueing fails. Throwaway DB; Claude, Telegram and Twilio faked.
"""
import os, sys, django
from datetime import date, timedelta
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite", "refusing to run outside the testbed"

import json
import re
from django.conf import settings as _s
from django.test import Client
from django.utils import timezone
from mysite.models import User, Apartment, Booking, TwilioConversation, TwilioMessage, AIEvent, AIRun, StaffMember
from mysite.ai_agent import service, runner, notify, config, inputs, kb_documents, alerts_v5
import mysite.ai_agent.team_notify as team_notify_mod
import mysite.views.messaging as messaging

config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_kbdocs"; config.WORK_DIR = config.RUNS_DIR / "_cwd"
checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

telegram = []
fake_notify = lambda text: (telegram.append(text), (True, "captured"))[1]
team_notify_mod.send_ai_chat = lambda t, reply_to=None: (*fake_notify(t), 900 + len(telegram))
team_notify_mod.notify_ai_chat = fake_notify; notify.notify_ai_chat = fake_notify
from mysite.ai_agent.sandbox_test.world import html_to_plain as _plain_of   # an alert sent as Telegram HTML, as read
alerts_v5.send_ai_chat = lambda t, reply_to=None, reply_markup=None, silent=False, parse_mode=None: (*fake_notify(_plain_of(t) if parse_mode == 'HTML' else t), 900 + len(telegram))
alerts_v5.edit_reply_markup = lambda *a, **k: None
messaging.send_messsage_by_sid = lambda *a, **k: None
script = []
def fake_run_claude(system_prompt, user_input, conversation_sid, run_dir, until_message_id=None, model=None, images=None):
    return {'ok': True, 'error': None, 'output': script.pop(0), 'events': [], 'result_event': {}, 'stdout': '', 'stderr': '',
            'command': 'fake', 'mcp_config': {}, 'model': 'fake', 'exit_code': 0, 'duration_ms': 1, 'timed_out': False}
runner.run_claude = fake_run_claude

merge_mode = ['good']
prompts_seen = []
def fake_complete(prompt):
    prompts_seen.append(prompt)
    if prompt.startswith("You maintain the knowledge base"):   # generate from a chat
        return ("[APARTMENT KB]\nWi-Fi: Net / pass1\nParking: spot 12 in the back lot\n[GLOBAL KB]\nQuiet hours 22-07\n"
                "[SUMMARY]\nAdded the parking spot (Janna, 10:02).")
    doc = re.search(r"Current document:\n(.*?)\n\nNew information to add", prompt, re.S).group(1)
    new = re.search(r"New information to add \(from [^)]*\):\n(.*?)\n\nIt corrects", prompt, re.S).group(1)
    old = re.search(r"replaces this part of the document \(if any\):\n(.*?)\n\nKnowledge-base rules", prompt, re.S).group(1)
    doc = '' if doc == '(empty)' else doc
    if merge_mode[0] == 'drops':
        return f"[UPDATED KB]\n{new}\n[CHANGES]\nrewrote everything"
    if merge_mode[0] == 'garbage':
        return "sorry, I can't"
    out = doc.replace(old, new) if old != '(nothing)' and old in doc else (doc + '\n' + new).strip()
    return f"[UPDATED KB]\n{out}\n[CHANGES]\nupdated"
kb_documents._complete = fake_complete

# ---- fixtures -------------------------------------------------------------------------------------
if not User.objects.filter(email="kd@example.com").exists():
    User.objects.bulk_create([User(email="kd@example.com", full_name="Kim Doc", role="Tenant", phone="+15550004411"),
                              User(email="kd-admin@example.com", full_name="Admin Kd", role="Admin", is_active=True)])
tenant, admin = User.objects.get(email="kd@example.com"), User.objects.get(email="kd-admin@example.com")
DOC = "Wi-Fi: Net / pass1\nGate code: 1111\nTrash room: floor 1\nCheckout: 10:00\nLaundry: in the unit\nPool: 8-22"
Apartment.objects.bulk_create([Apartment(name="790-501 test", building_n="790", apartment_n="501", street="S", state="FL",
    city="WPB", zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", knowledge_base=DOC)])
apt = Apartment.objects.get(name="790-501 test")
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=1),
                                     end_date=date.today() + timedelta(days=9), status="Confirmed")])
SID = "CHkbdocs790"
TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=SID, friendly_name="790-501", apartment=apt,
                                                           booking=Booking.objects.get(apartment=apt))])
conv = TwilioConversation.objects.get(conversation_sid=SID)
StaffMember.objects.all().delete()
StaffMember.objects.bulk_create([StaffMember(ai_name="Janna", full_name="Janna", role="accounting", phone="+15618438867")])
inputs._staff_cache['at'] = 0; messaging._manager_phones_cache['at'] = 0
messaging.save_global_knowledge_base_text("Quiet hours 22-07")
n = [0]
def msg(author, body, direction='inbound'):
    n[0] += 1
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"KD{n[0]:04d}", conversation=conv, conversation_sid=SID,
                                                     author=author, body=body, direction=direction)])
    return TwilioMessage.objects.get(message_sid=f"KD{n[0]:04d}")
def drain():
    AIEvent.objects.filter(status='pending').update(created_at=timezone.now() - timedelta(minutes=5))
    runs = []
    while True:
        batch = service.claim_next_batch()
        if not batch: return runs
        runs.append(service.process_events(batch))
def doc():
    return Apartment.objects.get(id=apt.id).knowledge_base or ''
def kb(text, replaces='', scope='apartment', source='Janna'):
    return {'type': 'KB_UPDATE', 'scope': scope, 'text': text, 'replaces': replaces, 'source': source}
def press_kb(run, scope='apartment', author='Janna'):
    """🏠📚 Apartment / 🌍📚 Global under the alert: the knowledge is saved only now. Returns (popup, done detail)."""
    run = AIRun.objects.get(id=run.id)
    actions = run.review['plan']['actions']
    index = next(i for i, a in enumerate(actions) if a.get('type') == 'KB_UPDATE' and not a.get('done'))
    popup = alerts_v5.save_knowledge(run, index, scope, author)
    run.refresh_from_db()
    return popup, (run.review['plan']['actions'][index].get('done') or {}).get('detail', '')

# ---- 1. the merge guard ------------------------------------------------------------------------------
m = msg("+15618438867", "the pool is now open 7-23", 'outbound'); service.enqueue_staff_message(SID, m.message_sid, m.body)
merge_mode[0] = 'drops'
script.append({'answer': 'NO_ANSWER', 'why': 'x', 'actions': [kb('Pool: 7-23')]})
run = drain()[0]
check("a staff fact waits for its knowledge button: nothing written by the run", doc() == DOC, doc())
popup, detail = press_kb(run)
check("a merge that drops most of the document is not trusted: the text is appended instead",
      popup == "Saved" and doc().startswith(DOC) and doc().endswith('Pool: 7-23') and 'appended' in detail, (popup, doc(), detail))
merge_mode[0] = 'garbage'
m = msg("+15618438867", "dishwasher tabs are under the sink", 'outbound'); service.enqueue_staff_message(SID, m.message_sid, m.body)
script.append({'answer': 'NO_ANSWER', 'why': 'x', 'actions': [kb('Dishwasher tabs: under the sink')]})
press_kb(drain()[0])
check("an unreadable merge answer also falls back to appending", doc().endswith('Dishwasher tabs: under the sink') and 'Trash room: floor 1' in doc())
merge_mode[0] = 'good'
check("the merge prompt got the document, the new text, who said it (the team message) and the KB rules",
      'Current document:\nWi-Fi: Net / pass1' in prompts_seen[-1] and "(from Janna's message " in prompts_seen[-1] and 'Knowledge-base rules from staff' in prompts_seen[-1])

# ---- 2. company scope from staff -> the global document -----------------------------------------------
m = msg("+15618438867", "for all units: late checkout costs $20 per hour", 'outbound'); service.enqueue_staff_message(SID, m.message_sid, m.body)
script.append({'answer': 'NO_ANSWER', 'why': 'x', 'actions': [kb('Late checkout: $20 per hour (all apartments).', scope='company')]})
press_kb(drain()[0], scope='company')
check("company-wide knowledge from staff goes to the global document, not the apartment one",
      'Late checkout: $20 per hour' in messaging.get_global_knowledge_base_text() and 'Late checkout' not in doc())

# ---- 3. a tenant's credential change waits for a manager's knowledge button -----------------------------------
Apartment.objects.filter(id=apt.id).update(ai_group_chat_enabled=True)
m = msg("+15550004411", "btw the wifi password on the router sticker is pass2")
service.enqueue_tenant_message(SID, m.message_sid, m.body)
script.append({'answer': 'Thanks!', 'why': 'x', 'actions': [kb('Wi-Fi: Net / pass2', replaces='Wi-Fi: Net / pass1', source='tenant')]})
run = drain()[0]
alert = next((t for t in reversed(telegram) if 'Wi-Fi: Net / pass2' in t), '')
run.refresh_from_db()
markup = alerts_v5.keyboard_for(run)
labels = [b['text'] for row in (markup or {}).get('inline_keyboard', []) for b in row]
check("the alert shows the tenant's knowledge update with its Apartment / Global buttons",
      alert and '🏠📚 Apartment ⭐' in labels and '🌍📚 Global' in labels, (alert, labels))
check("nothing is written before a manager presses", 'pass2' not in doc())
popup, detail = press_kb(run, author='Kevin')
check("after the 🏠📚 Apartment press the tenant's correction is written", popup == "Saved" and 'Wi-Fi: Net / pass2' in doc()
      and 'pass1' not in doc(), (popup, doc()))

# ---- 4. chat page "Generate": drafts from the whole chat, nothing saved ----------------------------------------
c = Client(); c.force_login(admin)
before_doc, before_global = doc(), messaging.get_global_knowledge_base_text()
r = c.post(f"/chat/{SID}/knowledge-base/generate/", json.dumps({}), content_type='application/json')
data = r.json()
check("Generate returns drafts for both documents and a summary", r.status_code == 200 and data.get('success')
      and 'spot 12' in data['apartment_kb'] and data['global_kb'] == 'Quiet hours 22-07' and 'parking spot' in data['summary'], data)
check("the history prompt got the whole chat with roles, both documents and the KB rules",
      'Janna (STAFF)' in prompts_seen[-1] and '(TENANT)' in prompts_seen[-1] and 'Current global knowledge base' in prompts_seen[-1]
      and 'Knowledge-base rules from staff' in prompts_seen[-1])
check("nothing is saved by Generate", doc() == before_doc and messaging.get_global_knowledge_base_text() == before_global)
r = c.post(f"/chat/{SID}/knowledge-base/", json.dumps({'apartment_kb': data['apartment_kb'], 'global_kb': data['global_kb']}),
           content_type='application/json')
check("Save KB writes the reviewed drafts", r.status_code == 200 and 'spot 12' in doc())
check("the KB window's prompt buttons open the new prompts",
      '/chat/knowledge-base-prompts/ai_kb_from_history/' in c.get(f'/chat/{SID}/').content.decode())

# ---- 5. no legacy AI: queueing fails -> the team is alerted -------------------------------------------------
telegram.clear()
service.enqueue_tenant_message = lambda *a, **k: False
resp = Client().post('/conversation-created-webhook/', {'EventType': 'onMessageAdded', 'MessageSid': 'IMkdfail', 'ConversationSid': SID,
                                                         'Author': '+15550004411', 'Body': 'is there a hair dryer?'})
check("a tenant message that cannot be queued alerts the team (no fallback AI answer)",
      resp.status_code < 500 and any('could not be queued' in t and 'hair dryer' in t for t in telegram), telegram)
check("the legacy answer code is gone", not hasattr(messaging, 'ai_answer_customer_detailed') and not hasattr(messaging, 'ai_extract_knowledge'))

print(f"{sum(checks)}/{len(checks)} checks passed")
sys.exit(0 if all(checks) else 1)
