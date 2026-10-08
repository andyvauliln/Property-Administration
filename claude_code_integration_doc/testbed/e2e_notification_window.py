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
check("a failed flush alerts the manager chat and schedules ONE retry in 3 minutes (not failed yet)",
      not failed_row.failed and failed_row.attempts == 1 and 'twilio down' in (failed_row.error or '')
      and len(failure_alerts) == 1 and failed_row.send_after > timezone.now() + timedelta(minutes=2))
call_command('flush_pending_sms')
check("the retry waits for its 3 minutes", len(failure_alerts) == 1)
PendingOutboundMessage.objects.filter(id=failed_row.id).update(send_after=timezone.now() - timedelta(seconds=1))
call_command('flush_pending_sms')
failed_row.refresh_from_db()
check("the retry failed too: the row is marked failed and alerted again", failed_row.failed and failed_row.attempts == 2
      and len(failure_alerts) == 2)
call_command('flush_pending_sms')
check("a failed row is not retried a second time", len(failure_alerts) == 2)

# --- office hours, US federal holidays and the after-hours auto-message (moved from e2e_client_v4.py) ---
from datetime import date
from mysite.models import (User, Apartment, Booking, TwilioConversation, TwilioMessage, AIEvent, AIAfterHoursAck,
                           AIAlertCall, StaffMember)
from mysite.ai_agent import after_hours, calls, policy, service, alerts_v5

h26 = config.us_federal_holidays(2026)
check("2026 holidays: July 4 (Sat) observed Fri Jul 3, Thanksgiving Nov 26, MLK Jan 19, Memorial May 25",
      date(2026, 7, 3) in h26 and date(2026, 11, 26) in h26 and date(2026, 1, 19) in h26 and date(2026, 5, 25) in h26, h26)
check("office hours: Wed 09:30 ET yes, Wed 18:05 no, Sat 11:00 no, Thanksgiving 11:00 no",
      config.is_office_hours(datetime(2026, 9, 30, 9, 30, tzinfo=ET)) and not config.is_office_hours(datetime(2026, 9, 30, 18, 5, tzinfo=ET))
      and not config.is_office_hours(datetime(2026, 10, 3, 11, 0, tzinfo=ET))
      and not config.is_office_hours(datetime(2026, 11, 26, 11, 0, tzinfo=ET)))
due, _ = policy.due_at('staff_reminder', 'routine', now=datetime(2026, 9, 30, 20, 0, tzinfo=ET) - timedelta(hours=24))
check("routine staff reminders start at 09:00", due.astimezone(ET).hour == 9, due)

# fakes: Twilio SMS (can fail on demand), Twilio calls, the AI group
OFFICE = [False]
config.is_office_hours = lambda now=None: OFFICE[0]
sms, sms_fail, telegram, errors = [], [0], [], []
def fake_sms(sid, author, message, sender, receiver):
    if sms_fail[0]:
        sms_fail[0] -= 1
        raise Exception("twilio down")
    sms.append((sid, message))
messaging.send_messsage_by_sid = fake_sms
dialled, call_status = [], ['completed']
class _Call:
    def __init__(self, sid): self.sid, self.status = sid, call_status[0]
    def fetch(self): return self
class _Calls:
    def create(self, to, from_, twiml, timeout): dialled.append(to); return _Call(f"CA{len(dialled)}")
    def __call__(self, sid): return _Call(sid)
class _Twilio: calls = _Calls()
messaging.get_twilio_client = lambda: _Twilio()
from mysite.ai_agent.sandbox_test.world import html_to_plain as _plain_of   # an alert sent as Telegram HTML, as read
def fake_tg(text, reply_to=None, reply_markup=None, silent=False, parse_mode=None):
    telegram.append(_plain_of(text) if parse_mode == 'HTML' else text); return True, "sent", 800000 + len(telegram)
alerts_v5.send_ai_chat = calls.send_ai_chat = fake_tg
after_hours.report_error = calls.report_error = lambda e, ctx, info=None, source='task': errors.append(f"{ctx}: {e}")

# fixtures: one tenant with a live and a test apartment, Edy and Farid (two phone numbers)
TENANT_PHONE = "+15550009933"
if not User.objects.filter(email="nw@example.com").exists():
    User.objects.bulk_create([User(email="nw@example.com", full_name="Nora Window", role="Tenant", phone=TENANT_PHONE)])
tenant = User.objects.get(email="nw@example.com")
def make_apartment(name, live):
    Apartment.objects.bulk_create([Apartment(name=name, building_n="740", apartment_n=name[-3:], street="S", state="FL", city="WPB",
        zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available", ai_group_chat_enabled=live)])
    apt = Apartment.objects.get(name=name)
    Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=date.today() - timedelta(days=2),
                                         end_date=date.today() + timedelta(days=20), status="Confirmed")])
    sid = f"CHnw{name}"
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=sid, friendly_name=name, apartment=apt,
                                                               booking=Booking.objects.get(apartment=apt))])
    return sid
SID, SID_TEST = make_apartment("740-201", live=True), make_apartment("740-202", live=False)
for name, role, phone in (("Edy", "operations", "+15612220001"), ("Farid", "owner", "+15614603904")):
    if not StaffMember.objects.filter(ai_name=name).exists():
        StaffMember.objects.bulk_create([StaffMember(ai_name=name, full_name=name, role=role, phone=phone)])
StaffMember.objects.filter(ai_name="Farid").update(phone="+15614603904", secondary_phone="+15612205252", is_active=True)
# a manager writing from the CRM chat page counts as staff (Edy may have no phone in the shared test DB)
STAFF_AUTHOR = "ASSISTANT"
n = [0]
def say(sid, body, author=TENANT_PHONE, enqueue=True):
    n[0] += 1
    conv = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"NW{n[0]:04d}", conversation=conv, conversation_sid=sid,
                                                     author=author, body=body, direction='inbound')])
    if enqueue:
        service.enqueue_tenant_message(sid, f"NW{n[0]:04d}", body)
    return TwilioMessage.objects.get(message_sid=f"NW{n[0]:04d}")
def latest_ack():
    return AIAfterHoursAck.objects.order_by('-id').first()

say(SID, "The dishwasher is leaking a bit"); after_hours.process_pending()
check("after hours (live): the fixed message goes out at once, with the URGENT hint",
      latest_ack().status == 'sent' and len(sms) == 1 and sms[0][1].startswith("Automated message: We received your message outside")
      and "reply URGENT" in sms[0][1], sms)
check("the team sees it as an AI MESSAGE without buttons", telegram and telegram[-1].startswith("🤖 AI MESSAGE")
      and "After-hours message" in telegram[-1] and "✅ Sent to the tenant" in telegram[-1], telegram[-1:])
say(SID, "Also the TV remote is missing"); say(SID, "And a light bulb"); after_hours.process_pending()
acks = list(AIAfterHoursAck.objects.order_by('-id')[:2])
check("three messages within 5 hours: only one automatic message", all(a.status == 'suppressed' for a in acks)
      and len(sms) == 1 and 'at most once per 5 hours' in acks[0].reason, [(a.status, a.reason) for a in acks])
AIAfterHoursAck.objects.filter(status='sent').update(sent_at=timezone.now() - timedelta(hours=5, minutes=5))
say(SID, "Hello? still waiting"); after_hours.process_pending()
check("a new message after 5 hours: the automatic message goes out again", latest_ack().status == 'sent' and len(sms) == 2)

say(SID, "URGENT the fridge stopped working"); after_hours.process_pending()
call = AIAlertCall.objects.order_by('-id').first()
check("URGENT: no automatic message (pointless for a tenant who wrote URGENT), but Farid is phoned at once (live)",
      latest_ack().status == 'not_applicable' and 'URGENT' in latest_ack().reason and len(sms) == 2
      and call and call.status == 'calling' and dialled == ['+15614603904'], (latest_ack().status, latest_ack().reason, dialled))
call_status[0] = 'no-answer'
AIAlertCall.objects.filter(id=call.id).update(check_at=timezone.now() - timedelta(seconds=1)); calls.check_calls()
call.refresh_from_db()
check("no answer on the first number -> his second number is dialled", dialled == ['+15614603904', '+15612205252'] and call.phone_index == 1, dialled)
AIAlertCall.objects.filter(id=call.id).update(check_at=timezone.now() - timedelta(seconds=1)); calls.check_calls()
call.refresh_from_db()
check("no answer on either -> Telegram is told", call.status == 'unanswered' and 'did NOT answer' in telegram[-1], telegram[-1:])
call_status[0] = 'completed'
class _E: event_type, body, payload = 'TENANT_MESSAGE', 'this is URGENT please', {}
class _N: event_type, body, payload = 'TENANT_MESSAGE', 'not urgent, when you can', {}
check("URGENT skips the 1-minute burst wait; 'not urgent' does not", service._batch_wait_seconds([_E()]) == 0
      and service._batch_wait_seconds([_N()]) == config.debounce_seconds())
# created directly: "thanks!" is not even queued for the AI (skippable), the rule must hold anyway
event = AIEvent.objects.create(event_type='TENANT_MESSAGE', conversation_sid=SID, body="thanks!", send_allowed=True, payload={})
after_hours.handle_event(event)
check("a pure thanks gets no automatic message", latest_ack().reason.startswith('only a thanks'), latest_ack().reason)
AIEvent.objects.filter(status='pending').update(status='done')

# both chats belong to the same tenant: they share the 5-hour window and the call cooldown - start clean
AIAfterHoursAck.objects.update(sent_at=timezone.now() - timedelta(hours=6))
AIAlertCall.objects.update(created_at=timezone.now() - timedelta(hours=1))
sms_before = len(sms)
say(SID_TEST, "Test apartment: fridge is warm"); after_hours.process_pending()
check("test apartment: WOULD_SEND recorded, nothing sent, the AI MESSAGE says TEST", latest_ack().status == 'would_send'
      and len(sms) == sms_before and "🧪 NOT sent – test mode" in telegram[-1], telegram[-1:])
say(SID_TEST, "URGENT fridge"); after_hours.process_pending()
check("test apartment URGENT: the call is only simulated", AIAlertCall.objects.order_by('-id').first().status == 'simulated' and len(dialled) == 2)
AIEvent.objects.filter(status='pending').update(status='done')

AIAfterHoursAck.objects.filter(conversation_sid__in=[SID, SID_TEST]).update(sent_at=timezone.now() - timedelta(hours=6))
say(SID, "Edy here, I'm on it", author=STAFF_AUTHOR, enqueue=False)
say(SID, "when will you come?"); after_hours.process_pending()
check("staff are replying right now: no automatic message", latest_ack().reason.startswith('staff are replying'), latest_ack().reason)
AIEvent.objects.filter(status='pending').update(status='done')

TwilioMessage.objects.filter(conversation_sid=SID, author=STAFF_AUTHOR).delete()
sms_fail[0] = 1
say(SID, "Door lock stuck"); after_hours.process_pending()
failed = latest_ack()
check("failed auto-message: FAILED, alerted, retry in 3 minutes", failed.status == 'failed' and failed.retry_at and
      any('NOT delivered' in e for e in errors), (failed.status, errors[-1:]))
AIAfterHoursAck.objects.filter(id=failed.id).update(retry_at=timezone.now() - timedelta(seconds=1))
after_hours.process_pending(); failed.refresh_from_db()
check("the retry works: sent", failed.status == 'sent' and failed.attempts == 2)
AIEvent.objects.filter(status='pending').update(status='done')

msg = say(SID, "Duplicate webhook test", enqueue=False)
service.enqueue_tenant_message(SID, msg.message_sid, msg.body); service.enqueue_tenant_message(SID, msg.message_sid, msg.body)
after_hours.process_pending()
check("duplicate webhook: one event, one after-hours decision", AIEvent.objects.filter(message=msg).count() == 1
      and AIAfterHoursAck.objects.filter(event__message=msg).count() == 1)
AIEvent.objects.filter(status='pending').update(status='done')
OFFICE[0] = True
say(SID, "Is the pool open?"); after_hours.process_pending()
check("office hours: no automatic message", latest_ack().status == 'not_applicable' and latest_ack().reason == 'office hours')
AIEvent.objects.filter(status='pending').update(status='done')

print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
