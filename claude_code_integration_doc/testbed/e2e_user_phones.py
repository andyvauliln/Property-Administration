"""One user per phone: booking-form tenant matching, user form validation, DB constraint, dedupe command."""
import json, os, sys, tempfile, django
os.environ["DJANGO_SETTINGS_MODULE"] = "testbed_settings"
django.setup()
from datetime import date, timedelta
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
assert connection.vendor == "sqlite"
from mysite.forms import CustomUserForm
from mysite.models import Apartment, Booking, User, is_placeholder_email

checks = []
def check(name, cond, extra=''):
    checks.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if extra and not cond else ''))

User.objects.bulk_create([User(email="Ann.Real@Mail.com", full_name="Ann Real", role="Tenant", phone="+15558880001"),
                          User(email="bob@mail.com", full_name="Bob", role="Tenant", phone="+15558880002"),
                          User(email="tenant_x1@example.com", full_name="Carl", role="Tenant", phone="+15558880003")])
ann, bob, carl = (User.objects.get(phone=p) for p in ("+15558880001", "+15558880002", "+15558880003"))

def tenant_for(email, phone, name="Someone"):
    b = Booking()
    b.get_or_create_tenant({'tenant_email': email, 'tenant_phone': phone, 'tenant_full_name': name})
    return b.tenant

check("placeholder emails are recognised", is_placeholder_email("tenant_abc@example.com") and not is_placeholder_email("a@b.com"))

n = User.objects.count()
t = tenant_for("tenant_random123@example.com", "5558880001", "Ann Real")
check("random placeholder email + known phone -> the existing user, no new user", t.pk == ann.pk and User.objects.count() == n)

t = tenant_for("ann.real@mail.com", "+15558880001", "Ann Real")
check("email in different letter case -> same user", t.pk == ann.pk and User.objects.count() == n)

t = tenant_for("carl.new@mail.com", "+15558880003", "Carl")
carl.refresh_from_db()
check("real email given for a placeholder-email user -> email filled in", t.pk == carl.pk and carl.email == "carl.new@mail.com")

t = tenant_for("ann.other@mail.com", "+15558880001", "Ann Real")
ann.refresh_from_db()
check("second real email for a known phone -> same user, email kept in notes", t.pk == ann.pk and "ann.other@mail.com" in (ann.notes or ""))

try:
    tenant_for("bob@mail.com", "+15558880001", "Bob")
    ok = False
except ValidationError as e:
    ok = "Ann Real" in str(e)
check("phone of one user + email of another -> clear error instead of overwriting", ok)
bob.refresh_from_db()
check("... and Bob's phone was not overwritten", bob.phone == "+15558880002")

t = tenant_for("dora@mail.com", "+15558880009", "Dora")
check("new phone + new email -> new user", t.pk and t.phone == "+15558880009" and User.objects.count() == n + 1)

try:
    tenant_for("eve@mail.com", "+15612205252", "Eve")
    ok = False
except ValidationError:
    ok = True
check("staff phone on a tenant is still refused", ok)

# user edit form
f = CustomUserForm(data={"email": "new@mail.com", "full_name": "New", "phone": "5558880002", "role": "Tenant"})
check("user form refuses a phone another user has (after normalizing)", not f.is_valid() and "phone" in f.errors)
f = CustomUserForm(data={"email": "bob@mail.com", "full_name": "Bob", "phone": "5558880002", "role": "Tenant"}, instance=bob)
check("user form accepts the user's own phone and stores it normalized", f.is_valid() and f.cleaned_data["phone"] == "+15558880002", f.errors)
f = CustomUserForm(data={"email": "x@mail.com", "full_name": "X", "phone": "+17282001917", "role": "Tenant"})
check("user form refuses a staff phone for a tenant", not f.is_valid() and "phone" in f.errors)

# database constraint
try:
    with transaction.atomic():
        User.objects.bulk_create([User(email="dup@mail.com", full_name="Dup", role="Tenant", phone="+15558880002")])
    ok = False
except IntegrityError:
    ok = True
check("the database itself refuses a second user with the same phone", ok)

# dedupe command on a small copy of the real situation (phones not unique yet -> bypass with odd formats)
Apartment.objects.bulk_create([Apartment(name="630-771", building_n="630", apartment_n="771", street="S", state="FL", city="WPB",
                                         zip_index="33401", bedrooms=1, bathrooms=1, apartment_type="In Management", status="Available")])
apt = Apartment.objects.get(name="630-771")
User.objects.bulk_create([User(email="keep@mail.com", full_name="Keeper", role="Tenant", phone="+15556660001"),
                          User(email="tenant_dup@example.com", full_name="Keeper", role="Tenant", phone="+5556660001"),
                          User(email="junk@example.com", full_name="John Doe", role="Tenant", phone="+15556660002")])
keep, dup, junk = (User.objects.get(email=e) for e in ("keep@mail.com", "tenant_dup@example.com", "junk@example.com"))
d = date.today()
Booking.objects.bulk_create([Booking(apartment=apt, tenant=dup, start_date=d, end_date=d + timedelta(days=5), status="Confirmed"),
                             Booking(apartment=apt, tenant=junk, start_date=d, end_date=d + timedelta(days=1), status="Waiting Contract")])
moved, test_booking = Booking.objects.get(tenant=dup), Booking.objects.get(tenant=junk)
plan = {"delete_bookings": [test_booking.id], "delete_cleanings_of_cleaners": [],
        "merges": [{"keep": keep.id, "merge": [dup.id], "rename": "Keeper Renamed"}],
        "delete_users": [junk.id], "clear_phone": []}
path = os.path.join(tempfile.mkdtemp(), "plan.json"); json.dump(plan, open(path, "w"))

call_command("dedupe_user_phones", plan=path)
check("dry run changes nothing", User.objects.filter(id=dup.id).exists() and Booking.objects.filter(id=test_booking.id).exists())

call_command("dedupe_user_phones", plan=path, apply=True, backup_dir=os.path.dirname(path))
keep.refresh_from_db(); moved.refresh_from_db()
check("apply: booking moved to the keeper, duplicate deleted",
      moved.tenant_id == keep.id and not User.objects.filter(id=dup.id).exists())
check("apply: keeper renamed and the merged email kept in notes",
      keep.full_name == "Keeper Renamed" and "tenant_dup@example.com" in (keep.notes or ""))
check("apply: test booking and junk user deleted",
      not Booking.objects.filter(id=test_booking.id).exists() and not User.objects.filter(id=junk.id).exists())

print(f"\n{sum(checks)}/{len(checks)} checks passed"); sys.exit(0 if all(checks) else 1)
