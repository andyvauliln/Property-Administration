"""Watchdog, Telegram activity feed and Florida-time display. Throwaway DB, Telegram faked."""
import os, sys, django
from datetime import timedelta, datetime, timezone as dt_tz
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from django.db import connection
assert connection.vendor == "sqlite"
from django.conf import settings as _s
from django.core.management import call_command
from django.utils import timezone
from mysite.models import AIEvent, AIRun, AIManagement
from mysite.ai_agent import config, notify
import mysite.management.commands.ai_agent_watchdog as wd
from mysite.templatetags.custom_filters import ai_local

sent = []
fake = lambda text: (sent.append(text), (True, "ok"))[1]
notify.notify_ai_chat = fake; wd.notify_ai_chat = fake
config.RUNS_DIR = _s.TESTBED_DIR / "ai_runs_misc"
checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

AIManagement.objects.update_or_create(prompt_key="ai_backend", defaults={'name': 'b', 'entry_type': 'ai_model', 'content': 'claude_cli'})
AIEvent.objects.all().delete(); AIRun.objects.all().delete()
call_command('ai_agent_watchdog'); check("healthy queue -> silent", not sent)
e = AIEvent.objects.create(conversation_sid="CHw", body="hello?")
AIEvent.objects.filter(id=e.id).update(created_at=timezone.now() - timedelta(minutes=12))
call_command('ai_agent_watchdog'); check("message waiting 12 min -> alert with the wait time", len(sent) == 1 and '12 min' in sent[0] and 'pm2' in sent[0], sent)
call_command('ai_agent_watchdog'); check("no repeat within 30 minutes", len(sent) == 1)
AIEvent.objects.filter(id=e.id).update(status='done')
call_command('ai_agent_watchdog'); check("queue moving again -> one recovery message", len(sent) == 2 and 'moving again' in sent[1])
call_command('ai_agent_watchdog'); check("then silent", len(sent) == 2)
for i in range(3): AIRun.objects.create(conversation_sid="CHw", error="claude CLI error: login expired")
call_command('ai_agent_watchdog'); check("three failed runs in a row -> alert", len(sent) == 3 and 'all failed' in sent[2])
AIManagement.objects.filter(prompt_key="ai_backend").update(content='openrouter'); sent.clear()
(config.RUNS_DIR / '.watchdog.json').unlink()
call_command('ai_agent_watchdog'); check("an old 'openrouter' backend row no longer silences the watchdog (Claude is the only backend)",
                                        len(sent) == 1 and 'OpenRouter' not in sent[0] and 'openrouter' not in sent[0], sent)

sent.clear()
from mysite.ai_agent import team_notify, actions
team_notify.notify_ai_chat = fake
team_notify.send_ai_chat = lambda t, reply_to=None: (*fake(t), None)
run = AIRun.objects.create(conversation_sid="CHa", duration_ms=6200, cost_usd="0.0172")
meta = {'apartment': '630-429', 'tenant': 'John Smith', 'event_type': 'STAFF_MESSAGE', 'mode': 'test'}
parsed = {'answer': None, 'why': 'Staff stated a reusable fact.', 'review_answer': None}
acts = [{'action': {'type': 'KB_UPDATE', 'scope': 'apartment', 'text': 'WiFi password: blue7788'}, 'status': 'executed', 'detail': "the 630-429 knowledge base updated - merged: WiFi password changed\n-old\n+new"},
        {'action': {'type': 'SCHEDULE_FOLLOWUP'}, 'status': 'executed', 'detail': 'created f-1'}, {'action': {'type': 'SCHEDULE_FOLLOWUP'}, 'status': 'executed', 'detail': 'created f-2'},
        {'action': {'type': 'NOPE'}, 'status': 'rejected', 'detail': 'unknown action type'}]
ctx = actions.ActionContext('test', meta, 'x', 'CHa')
team_notify.deliver(ctx, run, parsed, acts, {'sent_to_chat': False, 'note': 'NO_ANSWER'}, "[09:12] Janna (STAFF): new wifi password is blue7788")
m = sent[0] if sent else ''
check("no alerts -> one quiet 💬 message: unit, tenant, event, TEST mark, Florida time", len(sent) == 1 and m.startswith('💬 630-429 · John Smith') and all(x in m for x in ('STAFF_MESSAGE', '🧪 TEST', 'ET, Florida')), m)
check("it shows the incoming text, no-answer, why, KB update, compact action line, rejected action, full link, and NO cost",
      all(x in m for x in ('Janna (STAFF)', 'no answer to the tenant', 'Staff stated', '📚 WiFi password: blue7788 - the 630-429 knowledge base updated', '2× reminder set', '✗ NOPE', 'http://crm.test/ai-runs/')) and '$' not in m, m)
sent.clear()
team_notify.deliver(actions.ActionContext('live', dict(meta, mode='live'), 'x', 'CHa'), run, {'answer': 'The password is blue7788.', 'why': 'kb'}, [], {'sent_to_chat': True, 'note': 'sent'}, "[09:15] John (TENANT): wifi?")
check("live answer is shown as sent", '🟢 LIVE' in sent[0] and 'sent to the tenant' in sent[0])
os.environ['AI_AGENT_TELEGRAM_ACTIVITY'] = 'off'; sent.clear()
team_notify.deliver(ctx, run, parsed, acts, {}, "x"); check("feed off + no alerts -> silent", not sent)
ctx2 = actions.ActionContext('test', meta, 'x', 'CHa'); ctx2.alerts.append({'action': {'type': 'INTERNAL_ALERT', 'priority': 'emergency', 'responsible': ['Edy'], 'text': 'water everywhere'}, 'issue': None})
team_notify.deliver(ctx2, run, parsed, [], {}, "x"); check("feed off but an alert exists -> still sent, emergency header", len(sent) == 1 and sent[0].startswith('🚨 EMERGENCY'))
del os.environ['AI_AGENT_TELEGRAM_ACTIVITY']

check("pages show Florida time, with daylight saving: 16:30 UTC in July -> 12:30 ET, in January -> 11:30 ET",
      ai_local(datetime(2026, 7, 1, 16, 30, tzinfo=dt_tz.utc)) == 'Jul 01, 12:30 ET' and ai_local(datetime(2026, 1, 5, 16, 30, tzinfo=dt_tz.utc)) == 'Jan 05, 11:30 ET')
print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
