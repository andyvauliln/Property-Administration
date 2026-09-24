"""
Alerts the AI Telegram chat when messages are stuck in the AI agent queue (worker stopped, hung or
Claude login expired). Run every 5 minutes from cron.js. Quiet when everything is fine.

  python manage.py ai_agent_watchdog [--max-wait-minutes 5] [--repeat-minutes 30]
"""
import json
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from mysite.ai_agent import config
from mysite.ai_agent.notify import notify_ai_chat


class Command(BaseCommand):
    help = "Alert when AI agent events wait too long or recent runs keep failing"

    def add_arguments(self, parser):
        parser.add_argument('--max-wait-minutes', type=int, default=5)
        parser.add_argument('--repeat-minutes', type=int, default=30)

    def handle(self, *args, **options):
        from mysite.models import AIEvent, AIRun

        if not config.is_agent_backend_enabled():
            return
        now = timezone.now()
        waiting = AIEvent.objects.filter(
            status__in=[AIEvent.STATUS_PENDING, AIEvent.STATUS_RUNNING],
            created_at__lt=now - timedelta(minutes=options['max_wait_minutes']),
        ).order_by('id')
        recent = list(AIRun.objects.filter(created_at__gte=now - timedelta(minutes=30)).order_by('-id')[:5])
        all_failing = len(recent) >= 3 and all(r.error and not r.answer and not r.no_answer for r in recent)

        problems = []
        if waiting.exists():
            oldest = waiting.first()
            minutes = int((now - oldest.created_at).total_seconds() // 60)
            problems.append(
                f"{waiting.count()} message(s) are waiting for the AI, the oldest for {minutes} min "
                f"(chat {oldest.conversation_sid}). Nobody is answering them - is the worker running? "
                f"Check: pm2 status, pm2 logs ai-agent"
            )
        from mysite.ai_agent import answer_review
        overdue = answer_review._due_runs(now - timedelta(minutes=options['max_wait_minutes']))
        if overdue.exists():
            problems.append(
                f"{overdue.count()} AI answer(s) / plan(s) passed their review window but were not done "
                f"(run #{overdue.first().id}). Is the worker running? Check: pm2 logs ai-agent"
            )
        if all_failing:
            problems.append(f"The last {len(recent)} AI runs all failed: {str(recent[0].error)[:200]}")

        state_file = config.RUNS_DIR / '.watchdog.json'
        state = {}
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            pass
        if not problems:
            if state.get('alerting'):
                notify_ai_chat("✅ AI agent: the queue is moving again.")
                config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
                state_file.write_text(json.dumps({'alerting': False}))
            return
        if state.get('alerting') and time.time() - state.get('last_alert', 0) < options['repeat_minutes'] * 60:
            return
        notify_ai_chat("🚨 AI agent watchdog\n\n" + "\n\n".join(problems) + "\n\nTenants are NOT getting AI answers right now. "
                       "Quick fallback: AI Management → AI Backend → openrouter.")
        config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({'alerting': True, 'last_alert': time.time()}))
        self.stdout.write("alert sent")
