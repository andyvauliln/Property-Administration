"""Duplicate tenant chats: reuse the existing group chat on contract/welcome sends, and keep
update_conversation_links away from staff phones (data/tenant_multiple_conversations_report.md)."""
import os, sys, django
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from datetime import date, timedelta
from types import SimpleNamespace
from django.db import connection
assert connection.vendor == "sqlite"
from mysite.models import User, Apartment, Booking, TwilioConversation, TwilioMessage
from mysite.views import messaging

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

# ---- fake Twilio: conversation sid -> (state, [participants]) --------------------------------
TENANT, OTHER = "+15557770001", "+15557770002"
def phone(p): return SimpleNamespace(identity=None, messaging_binding={'address': p})
def ident(name): return SimpleNamespace(identity=name, messaging_binding={'projected_address': '+13153524379'})
TWILIO = {}
created = []
class FakeConv:
    def __init__(self, sid): self.sid = sid
    def fetch(self):
        if self.sid not in TWILIO: raise Exception("20404 not found")
        return SimpleNamespace(state=TWILIO[self.sid][0])
    @property
    def participants(self): return SimpleNamespace(list=lambda: TWILIO[self.sid][1])
messaging.client = SimpleNamespace(conversations=SimpleNamespace(v1=SimpleNamespace(conversations=FakeConv)))
messaging.create_conversation_with_participants = lambda name, cfg, **k: created.append(name) or "CHdupNEW"
messaging.get_booking_from_phone = lambda p: None

# ---- fixtures (bulk_create: no model save() side effects) ------------------------------------
User.objects.bulk_create([User(email="dup1@example.com", full_name="Dup Tenant", role="Tenant", phone=TENANT),
                          User(email="dup2@example.com", full_name="Other Tenant", role="Tenant", phone=OTHER)])
tenant, other = User.objects.get(email="dup1@example.com"), User.objects.get(email="dup2@example.com")
kw = dict(street="Test St", state="FL", city="WPB", zip_index="33401", bedrooms=1, bathrooms=1,
          apartment_type="In Management", status="Available")
Apartment.objects.bulk_create([Apartment(name="630-881", building_n="630", apartment_n="881", **kw),
                               Apartment(name="630-882", building_n="630", apartment_n="882", **kw)])
apt, apt2 = Apartment.objects.get(name="630-881"), Apartment.objects.get(name="630-882")
d = date.today()
Booking.objects.bulk_create([Booking(apartment=apt, tenant=tenant, start_date=d, end_date=d + timedelta(days=30), status="Confirmed"),
                             Booking(apartment=apt2, tenant=tenant, start_date=d + timedelta(days=60), end_date=d + timedelta(days=90), status="Confirmed"),
                             Booking(apartment=apt, tenant=other, start_date=d + timedelta(days=40), end_date=d + timedelta(days=50), status="Confirmed")])
b1 = Booking.objects.get(apartment=apt, tenant=tenant)
b_other_apt = Booking.objects.get(apartment=apt2)
b_other_tenant = Booking.objects.get(tenant=other)

def conv(sid, booking, apartment, authors):
    TwilioConversation.objects.bulk_create([TwilioConversation(conversation_sid=sid, friendly_name=sid, booking=booking, apartment=apartment)])
    c = TwilioConversation.objects.get(conversation_sid=sid)
    TwilioMessage.objects.bulk_create([TwilioMessage(message_sid=f"IM{sid}{i}", conversation=c, conversation_sid=sid, author=a, body="x")
                                       for i, a in enumerate(authors)])
    return c

# ---- find_reusable_group_conversation ------------------------------------------------------------
check("no chat at all -> nothing to reuse", messaging.find_reusable_group_conversation(b1, TENANT) is None)

conv("CHdupA", b1, apt, [TENANT, "Virtual Assistant"])
TWILIO["CHdupA"] = ("active", [phone(TENANT), phone("+15612205252"), ident("Virtual Assistant")])
check("existing group chat for the same booking is reused", messaging.find_reusable_group_conversation(b1, TENANT) == "CHdupA")

TWILIO["CHdupA"] = ("active", [phone(TENANT), phone("+15612205252"), ident("ASSISTANT")])
check("an old chat with the legacy ASSISTANT identity is reused too", messaging.find_reusable_group_conversation(b1, TENANT) == "CHdupA")

TWILIO["CHdupA"] = ("closed", TWILIO["CHdupA"][1])
check("a closed Twilio chat is not reused", messaging.find_reusable_group_conversation(b1, TENANT) is None)

TWILIO["CHdupA"] = ("active", [phone("+15612205252"), ident("Virtual Assistant")])
check("a chat the tenant is no longer in is not reused", messaging.find_reusable_group_conversation(b1, TENANT) is None)

del TWILIO["CHdupA"]
check("a chat deleted in Twilio is skipped, not an error", messaging.find_reusable_group_conversation(b1, TENANT) is None)

conv("CHdupB", None, None, [TENANT])
TWILIO["CHdupB"] = ("active", [phone(TENANT), phone("+15612205252"), phone("+13153524379")])
TwilioConversation.objects.filter(conversation_sid="CHdupB").update(apartment=apt)
check("a Twilio-made regroup (no assistant identity) is not reused", messaging.find_reusable_group_conversation(b1, TENANT) is None)

TWILIO["CHdupA"] = ("active", [phone(TENANT), phone("+15612205252"), ident("Virtual Assistant")])
check("same apartment, new booking of the same tenant -> reuses the apartment chat",
      messaging.find_reusable_group_conversation(Booking(id=b1.id + 1000, apartment=apt, tenant=tenant), TENANT) == "CHdupA")
check("different apartment -> a new chat (no reuse across apartments)",
      messaging.find_reusable_group_conversation(b_other_apt, TENANT) is None)
check("another tenant in the same apartment never gets this tenant's chat",
      messaging.find_reusable_group_conversation(b_other_tenant, OTHER) is None)

# ---- create_conversation_config ------------------------------------------------------------------
sid = messaging.create_conversation_config("630-881 Dup Rental", TENANT, booking=b1)
check("create_conversation_config reuses instead of creating", sid == "CHdupA" and created == [])
sid = messaging.create_conversation_config("630-882 Dup Rental", TENANT, booking=b_other_apt)
check("create_conversation_config creates when nothing is reusable", sid == "CHdupNEW" and created == ["630-882 Dup Rental"])
created.clear()
sid = messaging.create_conversation_config("630-881 Dup Rental", TENANT)
check("without a booking it keeps the old always-create behaviour", sid == "CHdupNEW" and created == ["630-881 Dup Rental"])

# ---- update_conversation_links ignores staff phones ------------------------------------------------
STAFF = "+17282001917"
User.objects.bulk_create([User(email="dup3@example.com", full_name="Placeholder", role="Tenant", phone=STAFF)])
placeholder = User.objects.get(email="dup3@example.com")
Booking.objects.bulk_create([Booking(apartment=apt2, tenant=placeholder, start_date=d, end_date=d + timedelta(days=5), status="Blocked")])
b_staff = Booking.objects.get(tenant=placeholder)
c_staff = conv("CHdupC", b1, apt, [STAFF, TENANT])
b_staff.update_conversation_links()
c_staff.refresh_from_db()
check("a tenant record with a staff phone does not steal chats the staff member wrote in", c_staff.booking_id == b1.id)

print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
