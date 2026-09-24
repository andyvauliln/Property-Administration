"""
AI agent worker: drains the AIEvent queue, one conversation at a time.
Run under PM2 (app `ai-agent` in pm2.config.js) or once with --once.
"""
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from mysite.ai_agent import answer_review, config, service
from mysite.ai_agent.notify import report_error


class Command(BaseCommand):
    help = "Process pending AI agent events (tenant messages) with the Claude Code CLI"

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process what is pending now, then exit')
        parser.add_argument('--poll-seconds', type=float, default=2.0, help='Sleep between queue checks')

    def handle(self, *args, **options):
        from mysite.models import AIEvent

        self.stdout.write(f"[{timezone.now():%Y-%m-%d %H:%M:%S}] ai-agent worker started")
        last_stale_check = last_followup_check = last_poll = last_release = 0.0
        while True:
            close_old_connections()
            try:
                # Staff replies to AI answers in Telegram; release_due() also reads them before sending anything
                if time.monotonic() - last_poll > config.review_poll_seconds():
                    answer_review.poll_telegram()
                    last_poll = time.monotonic()
                if time.monotonic() - last_release > 5:
                    released = answer_review.release_due()
                    if released:
                        self.stdout.write(f"[{timezone.now():%H:%M:%S}] {released} reviewed answer(s) released")
                    last_release = time.monotonic()
            except Exception as e:
                report_error(e, "answer review tick failed (Telegram replies / held answers)")
                last_poll = last_release = time.monotonic()
            if time.monotonic() - last_stale_check > 60:
                service.release_stale_events()
                last_stale_check = time.monotonic()
            if time.monotonic() - last_followup_check > 30:
                fired = service.fire_due_followups()
                if fired:
                    self.stdout.write(f"[{timezone.now():%H:%M:%S}] {fired} follow-up(s) became due")
                last_followup_check = time.monotonic()

            batch = service.claim_next_batch()
            if not batch:
                if options['once'] and not AIEvent.objects.filter(status=AIEvent.STATUS_PENDING).exists():
                    return
                time.sleep(options['poll_seconds'])
                continue

            ids = [event.id for event in batch]
            self.stdout.write(f"[{timezone.now():%H:%M:%S}] {batch[-1].conversation_sid}: events {ids}")
            try:
                ai_run = service.process_events(batch)
                if ai_run:
                    self.stdout.write(f"    run #{ai_run.id} [{ai_run.mode}] -> {ai_run.report_dir}")
            except Exception as e:
                AIEvent.objects.filter(id__in=ids).update(
                    status=AIEvent.STATUS_FAILED, error=str(e)[:2000], finished_at=timezone.now(),
                )
                report_error(
                    e, "worker crashed on a batch - tenant message needs a human",
                    {'event_ids': ids, 'conversation_sid': batch[-1].conversation_sid, 'tenant_message': batch[-1].body},
                )
