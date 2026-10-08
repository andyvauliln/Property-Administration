"""Merged view of a tenant's several chats: grouping + main chat, CRM list/page, AI input, alert line, where
answers and notifications are sent (conversation_groups)."""
import os, sys, django
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from datetime import date, timedelta
from django.db import connection
from django.test import Client
from django.utils import timezone
assert connection.vendor == "sqlite"
from mysite import conversation_groups
from mysite.ai_agent import inputs, service
from mysite.models import AIRun, Apartment, Booking, TwilioConversation, TwilioMessage, User

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

T, OTHER, STAFF = "+15554440001", "+15554440002", "+17282001917"
User.objects.bulk_create([User(email="merge-t@mail.com", full_name="Mia Merge", role="Tenant", phone=T),
                          User(email="merge-o@mail.com", full_name="Otto Other", role="Tenant", phone=OTHER),
                          User(email="merge-admin@mail.com", full_name="Admin M", role="Admin", is_active=True)])
tenant, other = User.objects.get(phone=T), User.objects.get(phone=OTHER)
admin = User.objects.get(email="merge-admin@mail.com")
Apartment.objects.bulk_create([Apartment(name="630-661", building_n="630", apartment_n="661", street="S", state="FL", city="WPB",
                                         zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available")])
apt = Apartment.objects.get(name="630-661")
d = date.today()
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=d, end_date=d + timedelta(days=30), status="Confirmed"),
                             Booking(apartment=apt, tenant=other, start_date=d + timedelta(days=40), end_date=d + timedelta(days=50), status="Confirmed")])
booking = Booking.objects.get(tenant=tenant)
other_booking = Booking.objects.get(tenant=other)

now = timezone.now()
def conv(sid, booking_=None, msgs=()):
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=sid, friendly_name=sid, booking=booking_,
                                                               apartment=apt if booking_ else None)])
    c = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"IM{sid}{i}", conversation=c, conversation_sid=sid, author=a,
                                                     body=b, message_timestamp=now - timedelta(hours=h),
                                                     direction='outbound' if a in ('Virtual Assistant', STAFF) else 'inbound')
                                       for i, (a, b, h) in enumerate(msgs)])
    for i, (a, b, h) in enumerate(msgs):   # message_timestamp is auto_now_add: set the real times afterwards
        TwilioMessage.objects.filter(message_sid=f"IM{sid}{i}").update(message_timestamp=now - timedelta(hours=h))
    return c

# A: the booking chat (contract), tenant wrote there 5h ago. B: a regroup the tenant wrote in 1h ago -> main.
A = conv("CHmergeA", booking, [("Virtual Assistant", "Welcome Mia", 10), (T, "wifi does not work", 5)])
B = conv("CHmergeB", None, [(T, "still no wifi in the regroup", 1)])
C = conv("CHmergeC", other_booking, [(OTHER, "hello from Otto", 2)])   # another tenant: never merged
# the tenant is also in a chat of a placeholder booking with a staff phone -> must not be pulled in
User.objects.bulk_create([User(email="merge-ph@mail.com", full_name="Placeholder", role="Tenant", phone="+15612205252")])
Booking.objects.bulk_create([Booking(apartment=apt, tenant=User.objects.get(email="merge-ph@mail.com"),
                                     start_date=d, end_date=d, status="Blocked")])

g = conversation_groups.group_for(A, fresh=True)
check("the tenant's two chats form one group", g and set(g['ids']) == {A.id, B.id}, g)
check("main chat = where the tenant wrote last (the regroup)", g and g['main_sid'] == "CHmergeB")
check("group is found from any member, by object / id / sid",
      conversation_groups.group_for(B.id) is not None and conversation_groups.group_for("CHmergeA") is not None)
check("another tenant's chat is not merged", conversation_groups.group_for(C) is None)
check("a lone chat keeps its own sid as 'main'", conversation_groups.main_sid("CHmergeC") == "CHmergeC")
line = conversation_groups.summary_line("CHmergeA")
check("alert line names both chats, the main one, and this one",
      line and "2 chats" in line and f"#{B.id}" in line and "main" in line and f"This one is #{A.id}" in line, line)

# ---- CRM ----------------------------------------------------------------------------------------------
c = Client(); c.force_login(admin)
r = c.get("/chat/"); html = r.content.decode()
check("chat list: one row for the tenant, with a '2 chats' badge",
      r.status_code == 200 and "🔗 2 chats" in html and html.count("data-conversation-sid=\"CHmergeA\"") == 0
      and html.count("data-conversation-sid=\"CHmergeB\"") == 1, r.status_code)
check("chat list: the other tenant keeps a normal row", 'data-conversation-sid="CHmergeC"' in html)
r = c.get("/chat/CHmergeA/"); html = r.content.decode()
timeline = html[html.find('id="messages-container"'):]
check("chat page: merged banner with both chats", r.status_code == 200 and "has <strong>2 chats</strong>" in html
      and "main: tenant wrote here last" in html, r.status_code)
check("chat page: messages of both chats, in time order",
      0 <= timeline.find("Welcome Mia") < timeline.find("wifi does not work") < timeline.find("still no wifi in the regroup")
      and "hello from Otto" not in timeline)
check("chat page: the message from the other chat is marked", f"↪ chat #{B.id}" in html and f"↪ chat #{A.id}" not in html)
msg_b = TwilioMessage.objects.get(conversation=B)
r = c.post(f"/chat/CHmergeA/messages/{msg_b.id}/notes/", data='{"notes": "seen"}', content_type="application/json")
msg_b.refresh_from_db()
check("message actions work on a merged message from the other chat", r.status_code == 200 and msg_b.notes == "seen", r.status_code)
msg_c = TwilioMessage.objects.get(conversation=C)
r = c.post(f"/chat/CHmergeA/messages/{msg_c.id}/notes/", data='{"notes": "x"}', content_type="application/json")
check("... but never on another tenant's message", r.status_code in (404, 500) and not TwilioMessage.objects.get(id=msg_c.id).notes)

# ---- AI input -------------------------------------------------------------------------------------------
trigger = TwilioMessage.objects.get(conversation=A, author=T)
text, sources = inputs.build_agent_input("TENANT_MESSAGE", "CHmergeA", apt, booking, [trigger])
check("AI input explains the merged chats and where the answer goes",
      "TENANT_CHATS: this tenant has 2 separate group chats" in text and f"answering in chat #{A.id}" in text)
b_trigger = TwilioMessage.objects.get(conversation=B)
text, sources = inputs.build_agent_input("TENANT_MESSAGE", "CHmergeB", apt, booking, [b_trigger])
check("AI history includes the other chat, marked", f"[other chat #{A.id}]" in text and "wifi does not work" in text
      and "hello from Otto" not in text)

# ---- where answers / reminders are sent ----------------------------------------------------------------
sent = []
service.send_answer = lambda sid, *a, **k: sent.append(sid) or {'sent_to_chat': True, 'note': 'sent'}
from mysite.ai_agent import answer_review
parsed = {'answer': 'We are on it', 'actions': [], 'why': ''}
def press_send(sid, message, result):
    """What 🤖 Send Answer does once its checks passed: the held answer is released to the chat the run kept."""
    run = AIRun.objects.create(conversation_sid=sid, message=message, mode=AIRun.MODE_LIVE, answer=parsed['answer'],
                               hold_status=AIRun.HOLD_HOLDING, review={'style': 'v5', 'send_to': result.get('send_to')})
    return answer_review._release(run, run.answer, AIRun.HOLD_SENT, "Send Answer pressed by Andy")
result = service.deliver(parsed, AIRun.MODE_LIVE, "CHmergeA", booking, trigger, {}, apartment=apt)
check("an answer is held for the press, nothing sent yet", result.get('held') and not sent and 'send_to' not in result, (sent, result))
press_send("CHmergeA", trigger, result)
check("an answer to a message goes to the chat the message came from", sent == ["CHmergeA"], sent)
sent.clear()
result = service.deliver(parsed, AIRun.MODE_LIVE, "CHmergeA", booking, None, {}, apartment=apt,
                         send_to=conversation_groups.main_sid("CHmergeA"))
check("a reminder answer is held, keeps the main chat and says so", not sent and result.get('send_to') == "CHmergeB"
      and "main chat" in result['note'], (sent, result))
press_send("CHmergeA", None, result)
check("... and the press sends it to the main chat", sent == ["CHmergeB"], sent)

print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
