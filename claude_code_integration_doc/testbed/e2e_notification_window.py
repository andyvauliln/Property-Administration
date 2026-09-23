"""Tenant SMS 08:00-21:00 Florida notification window: pure window logic, gated sending, flush command."""
import os, sys, django
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.db import connection
assert connection.vendor == "sqlite"
from django.core.management import call_command
from mysite.ai_agent import config
from mysite.models import PendingOutboundMessage
from mysite.views import messaging

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

ET = ZoneInfo(config.TEAM_TIMEZONE)
d = lambda h, m=0: datetime(2026, 9, 22, h, m, tzinfo=ET)

# --- pure window logic ---
check("07:59 is outside the window", not config.is_within_notification_window(d(7, 59)))
check("08:00 is inside the window", config.is_within_notification_window(d(8, 0)))
check("20:59 is inside the window", config.is_within_notification_window(d(20, 59)))
check("21:00 is outside the window (end is exclusive)", not config.is_within_notification_window(d(21, 0)))
check("14:30 is inside the window", config.is_within_notification_window(d(14, 30)))

check("before 08:00 -> next window start is today 08:00",
      config.next_notification_window_start(d(3, 0)) == d(8, 0))
check("after 21:00 -> next window start is tomorrow 08:00",
      config.next_notification_window_start(d(22, 0)) == d(8, 0) + timedelta(days=1))
check("exactly 08:00 -> next window start is tomorrow (today's start already passed/equal)",
      config.next_notification_window_start(d(8, 0)) == d(8, 0) + timedelta(days=1))

# --- send_tenant_sms_gated ---
sent_calls = []
messaging.send_messsage_by_sid = lambda *a, **k: sent_calls.append(a)

config.is_within_notification_window = lambda now=None: True
result = messaging.send_tenant_sms_gated("CHwin1", "Virtual Assistant", "hi", "+15550000000", "+15551111111")
check("inside the window: sends immediately and returns True", result is True and len(sent_calls) == 1 and not PendingOutboundMessage.objects.exists())

config.is_within_notification_window = lambda now=None: False
config.next_notification_window_start = lambda now=None: d(8, 0) + timedelta(days=1)
result = messaging.send_tenant_sms_gated("CHwin2", "Virtual Assistant", "hi late", "+15550000000", "+15552222222")
pending = PendingOutboundMessage.objects.get(conversation_sid="CHwin2")
check("outside the window: not sent, held instead, returns False",
      result is False and len(sent_calls) == 1 and pending.body == "hi late" and pending.send_after == d(8, 0) + timedelta(days=1))

# --- flush_pending_sms command ---
from django.utils import timezone
PendingOutboundMessage.objects.filter(conversation_sid="CHwin2").update(send_after=timezone.now() - timedelta(minutes=1))

config.is_within_notification_window = lambda now=None: False
call_command('flush_pending_sms')
check("flush does nothing outside the window", len(sent_calls) == 1 and not PendingOutboundMessage.objects.get(conversation_sid="CHwin2").sent_at)

config.is_within_notification_window = lambda now=None: True
call_command('flush_pending_sms')
pending.refresh_from_db()
check("flush sends a due message once the window opens", len(sent_calls) == 2 and pending.sent_at is not None)

call_command('flush_pending_sms')
check("already-sent messages are not resent", len(sent_calls) == 2)

# --- flush failure path notifies the manager chat instead of crashing ---
def boom(*a, **k):
    raise Exception("twilio down")
messaging.send_messsage_by_sid = boom
failure_alerts = []
messaging._notify_manager_chat_delivery_failed = lambda *a, **k: failure_alerts.append(a)
PendingOutboundMessage.objects.create(conversation_sid="CHwin3", author="Virtual Assistant", body="x",
                                       sender_phone="+1", receiver_phone="+1", send_after=timezone.now() - timedelta(minutes=1))
call_command('flush_pending_sms')
failed_row = PendingOutboundMessage.objects.get(conversation_sid="CHwin3")
check("a failed flush marks the row failed and alerts the manager chat instead of losing it",
      failed_row.failed and 'twilio down' in (failed_row.error or '') and len(failure_alerts) == 1)
call_command('flush_pending_sms')
check("a failed row is not retried automatically", len(failure_alerts) == 1)

print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
