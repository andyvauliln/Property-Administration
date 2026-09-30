"""
Sends tenant SMS that were held by send_tenant_sms_gated for the 08:00-21:00 Florida notification
window, once the window has opened. Run frequently (see cron.js) - it is a no-op outside the window.

  python manage.py flush_pending_sms
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send tenant SMS held for the 08:00-21:00 Florida notification window, once it opens"

    def handle(self, *args, **options):
        from datetime import timedelta

        from django.utils import timezone

        from mysite.ai_agent import config
        from mysite.models import PendingOutboundMessage
        from mysite.views.messaging import _notify_manager_chat_delivery_failed, send_messsage_by_sid

        if not config.is_within_notification_window():
            self.stdout.write("outside the 08:00-21:00 Florida notification window, nothing to flush")
            return

        due = PendingOutboundMessage.objects.filter(sent_at__isnull=True, failed=False, send_after__lte=timezone.now())
        sent, failed = 0, 0
        for pending in due:
            try:
                send_messsage_by_sid(
                    pending.conversation_sid, pending.author, pending.body,
                    pending.sender_phone, pending.receiver_phone,
                )
                pending.sent_at = timezone.now()
                pending.attempts += 1
                pending.save(update_fields=['sent_at', 'attempts'])
                sent += 1
            except Exception as e:
                pending.attempts += 1
                pending.error = str(e)
                if pending.attempts < 2:
                    # One automatic retry, 3 minutes after the failure alert (user decision 2026-09-30)
                    pending.send_after = timezone.now() + timedelta(minutes=3)
                else:
                    pending.failed = True
                pending.save(update_fields=['failed', 'error', 'attempts', 'send_after'])
                failed += 1
                _notify_manager_chat_delivery_failed(pending.author, pending.receiver_phone, pending.body, pending.conversation_sid)
        self.stdout.write(f"sent {sent}, failed {failed}")
