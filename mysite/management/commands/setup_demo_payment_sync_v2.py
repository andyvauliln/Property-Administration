import json
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

DEMO_KEYWORD_TAG = "__demo_v2__"


def _get_or_create(Model, defaults=None, **lookup):
    obj = Model.objects.filter(**lookup).first()
    if obj:
        return obj
    return Model.objects.create(**lookup, **(defaults or {}))


class Command(BaseCommand):
    help = "Create (or recreate) demo DB + file payments for Payment Sync V2 manual testing."

    def handle(self, *args, **options):
        from mysite.models import (
            Apartment,
            Booking,
            PaymenType,
            Payment,
            PaymentMethod,
        )

        # ---- Cleanup previous demo data ----
        old = Payment.objects.filter(keywords__contains=DEMO_KEYWORD_TAG)
        old_count = old.count()
        if old_count:
            old.delete()
            self.stdout.write(f"Deleted {old_count} previous demo payments.")

        old_bookings = Booking.objects.filter(visit_purpose=DEMO_KEYWORD_TAG)
        old_bookings_count = old_bookings.count()
        if old_bookings_count:
            old_bookings.delete()
            self.stdout.write(f"Deleted {old_bookings_count} previous demo bookings.")

        # ---- Resolve lookup objects ----
        User = get_user_model()
        admin = User.objects.filter(role="Admin").first()
        if not admin:
            self.stderr.write("No Admin user found. Create one first.")
            return

        def get_bank(name):
            return PaymentMethod.objects.filter(name__iexact=name, type="Bank").first()

        def get_pm(name):
            return PaymentMethod.objects.filter(name__iexact=name, type="Payment Method").first()

        def get_pt(name, direction):
            return PaymenType.objects.filter(name__iexact=name, type=direction).first()

        def get_apt(name):
            return Apartment.objects.filter(name=name).first()

        bank_ba = get_bank("BA")
        bank_chase = get_bank("Chase")
        if not bank_ba:
            bank_ba = _get_or_create(PaymentMethod, name="BA", type="Bank")
        if not bank_chase:
            bank_chase = _get_or_create(PaymentMethod, name="Chase", type="Bank")

        pm_wire = get_pm("Wire") or _get_or_create(PaymentMethod, name="Wire", type="Payment Method")
        pm_zelle = get_pm("Zelle") or _get_or_create(PaymentMethod, name="Zelle", type="Payment Method")
        pm_check = get_pm("Check") or _get_or_create(PaymentMethod, name="Check", type="Payment Method")
        pm_ach = get_pm("ACH") or _get_or_create(PaymentMethod, name="ACH", type="Payment Method")

        pt_rent_in = get_pt("Rent", "In") or _get_or_create(PaymenType, name="Rent", type="In", defaults={"category": "Operating"})
        pt_other_in = get_pt("Other", "In") or _get_or_create(PaymenType, name="Other", type="In", defaults={"category": "Operating"})
        pt_other_out = get_pt("Other", "Out") or _get_or_create(PaymenType, name="Other", type="Out", defaults={"category": "Operating"})

        apt_630 = get_apt("630-205")
        apt_780 = get_apt("780-306")
        apt_ph = get_apt("PH-402")
        apt_450 = get_apt("450-110")
        apt_999 = get_apt("999-999")

        missing_apts = []
        for name, ref in [("630-205", apt_630), ("780-306", apt_780), ("PH-402", apt_ph), ("450-110", apt_450)]:
            if not ref:
                missing_apts.append(name)
        if missing_apts:
            self.stderr.write(f"Missing apartments: {missing_apts}. Creating them.")
            for name in missing_apts:
                parts = name.split("-") if "-" in name else [name, "0"]
                Apartment.objects.create(
                    name=name,
                    building_n=parts[0],
                    street="Demo St",
                    apartment_n=parts[1] if len(parts) > 1 else "0",
                    state="FL",
                    city="Demo City",
                    zip_index="00000",
                    bedrooms=1,
                    bathrooms=1,
                    apartment_type="In Management",
                    status="Available",
                )
            apt_630 = get_apt("630-205")
            apt_780 = get_apt("780-306")
            apt_ph = get_apt("PH-402")
            apt_450 = get_apt("450-110")

        if not apt_999:
            Apartment.objects.create(
                name="999-999", building_n="999", street="Wrong St",
                apartment_n="999", state="FL", city="Demo City",
                zip_index="00000", bedrooms=1, bathrooms=1,
                apartment_type="In Management", status="Available",
            )
            apt_999 = get_apt("999-999")

        # Tenants
        def ensure_tenant(full_name):
            email = f"{full_name.lower().replace(' ', '.')}.demo@example.com"
            u = User.objects.filter(email=email).first()
            if u:
                return u
            return User.objects.create_user(email=email, password="demo", full_name=full_name, role="Tenant")

        t_mike = ensure_tenant("Michael Steinhardt")
        t_darren = ensure_tenant("Darren Wiggins")
        t_jane = ensure_tenant("Jane Keyword")
        t_amy = ensure_tenant("Amy Split")

        today = date.today()

        def mk_booking(apt, tenant, start_offset, end_offset):
            return Booking.objects.create(
                apartment=apt, tenant=tenant,
                start_date=today - timedelta(days=start_offset),
                end_date=today + timedelta(days=end_offset),
                animals="", visit_purpose=DEMO_KEYWORD_TAG, source="", status="Confirmed",
            )

        b_mike = mk_booking(apt_630, t_mike, 20, 20)
        b_darren = mk_booking(apt_780, t_darren, 25, 5)
        b_jane = mk_booking(apt_ph, t_jane, 10, 10)
        b_amy = mk_booking(apt_450, t_amy, 40, 1)

        created = []

        def mk(*, amount, pay_date, pt, notes, bank, pm, booking=None, apartment=None, status="Pending", keywords=DEMO_KEYWORD_TAG):
            # constraint: booking and apartment cannot both be set
            if booking and apartment:
                apartment = None
            p = Payment.objects.create(
                amount=amount,
                payment_date=pay_date,
                payment_type=pt,
                notes=notes,
                bank=bank,
                payment_method=pm,
                booking=booking,
                apartment=apartment,
                payment_status=status,
                merged_payment_key=None,
                keywords=keywords,
            )
            created.append(p)
            return p

        # =============================================
        # TEST CASES
        # =============================================
        # Dates are relative to today so DB payments always fall in the demo date window.

        d = lambda offset: today - timedelta(days=offset)

        # --- Test 1: Exact single match (amount + date + apartment + type) ---
        p1 = mk(
            amount=3300.00, pay_date=d(2), pt=pt_rent_in,
            notes="[DEMO_V2] Test 1 DB: exact match - $3300 Wire 630-205 Rent In",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )

        # --- Test 2: 1 file -> 2 DB (split). File=$3300, DB has 2x$1650 ---
        p2a = mk(
            amount=1650.00, pay_date=d(5), pt=pt_rent_in,
            notes="[DEMO_V2] Test 2 DB-A: split half A - $1650 Wire 630-205",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )
        p2b = mk(
            amount=1650.00, pay_date=d(5), pt=pt_rent_in,
            notes="[DEMO_V2] Test 2 DB-B: split half B - $1650 Wire 630-205",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )

        # --- Test 3: Multiple file -> 1 DB. 2 file rows ($1200+$900) sum to DB $2100 ---
        p3 = mk(
            amount=2100.00, pay_date=d(4), pt=pt_rent_in,
            notes="[DEMO_V2] Test 3 DB: combined target - $2100 Zelle 780-306",
            bank=bank_ba, pm=pm_zelle, booking=b_darren,
        )

        # --- Test 4: Correct apartment beats wrong apartment ---
        p4_good = mk(
            amount=500.00, pay_date=d(1), pt=pt_other_out,
            notes="[DEMO_V2] Test 4 DB-GOOD: correct apt 780-306 - $500 Out",
            bank=bank_ba, pm=pm_wire, apartment=apt_780,
        )
        p4_bad = mk(
            amount=500.00, pay_date=d(1), pt=pt_other_out,
            notes="[DEMO_V2] Test 4 DB-BAD: wrong apt 999-999 - $500 Out (decoy)",
            bank=bank_ba, pm=pm_wire, apartment=apt_999,
        )

        # --- Test 5: Keyword match even with different bank ---
        p5 = mk(
            amount=178.85, pay_date=d(3), pt=pt_other_out,
            notes="[DEMO_V2] Test 5 DB: keyword match - $178.85 Check PH-402 garage",
            bank=bank_chase, pm=pm_check, booking=b_jane,
        )

        # --- Test 6: Tenant name token in notes ---
        p6 = mk(
            amount=1250.00, pay_date=d(15), pt=pt_other_in,
            notes="[DEMO_V2] Test 6 DB: tenant token match - $1250 ACH 450-110 (Amy Split)",
            bank=bank_ba, pm=pm_ach, booking=b_amy,
        )

        # --- Test 7: Direction filter - In file should NOT match Out DB ---
        p7_good = mk(
            amount=990.00, pay_date=d(6), pt=pt_other_in,
            notes="[DEMO_V2] Test 7 DB-GOOD: correct direction In - $990 Wire",
            bank=bank_ba, pm=pm_wire,
        )
        p7_bad = mk(
            amount=990.00, pay_date=d(6), pt=pt_other_out,
            notes="[DEMO_V2] Test 7 DB-BAD: wrong direction Out - $990 Wire (decoy)",
            bank=bank_ba, pm=pm_wire,
        )

        # --- Test 8: Pending preferred over Merged ---
        p8_pending = mk(
            amount=330.00, pay_date=d(7), pt=pt_rent_in,
            notes="[DEMO_V2] Test 8 DB-PENDING: should rank higher - $330 Zelle 630-205",
            bank=bank_ba, pm=pm_zelle, booking=b_mike, status="Pending",
        )
        p8_merged = mk(
            amount=330.00, pay_date=d(7), pt=pt_rent_in,
            notes="[DEMO_V2] Test 8 DB-MERGED: should rank lower - $330 Zelle 630-205",
            bank=bank_ba, pm=pm_zelle, booking=b_mike, status="Merged",
        )

        # --- Test 9: Date slightly off (+5 days) ---
        p9 = mk(
            amount=750.00, pay_date=d(12), pt=pt_rent_in,
            notes="[DEMO_V2] Test 9 DB: date off test - $750 Wire 780-306 (file will be 5 days later)",
            bank=bank_ba, pm=pm_wire, booking=b_darren,
        )

        # --- Test 10: Amount difference (Small vs Large) ---
        # 10.1 Small diff ($3295 vs $3300) - should score high
        mk(
            amount=3300.00, pay_date=d(2), pt=pt_rent_in,
            notes="[DEMO_V2] Test 10.1 DB: small amount diff - $3300 (file is $3295)",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )
        # 10.2 Large diff ($3100 vs $3300) - should score low
        mk(
            amount=3300.00, pay_date=d(2), pt=pt_rent_in,
            notes="[DEMO_V2] Test 10.2 DB: large amount diff - $3300 (file is $3100)",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )

        # --- Test 11: Multi-factor Ambiguity (Date vs Amount) ---
        # 11.1 Better Date, Worse Amount
        mk(
            amount=1050.00, pay_date=d(0), pt=pt_rent_in,
            notes="[DEMO_V2] Test 11.1 DB: better date (0 days) but worse amount (+$50)",
            bank=bank_ba, pm=pm_zelle, booking=b_darren,
        )
        # 11.2 Worse Date, Better Amount
        mk(
            amount=1000.00, pay_date=d(10), pt=pt_rent_in,
            notes="[DEMO_V2] Test 11.2 DB: worse date (+10 days) but exact amount ($1000)",
            bank=bank_ba, pm=pm_zelle, booking=b_darren,
        )

        # --- Test 12: Partial Keyword Match vs Full Keyword Match ---
        # 12.1 Partial: just "garage"
        mk(
            amount=200.00, pay_date=d(5), pt=pt_other_out,
            notes="[DEMO_V2] Test 12.1 DB: partial keyword - 'garage'",
            bank=bank_chase, pm=pm_check, apartment=apt_ph,
            keywords=DEMO_KEYWORD_TAG + ",garage",
        )
        # 12.2 Full: "garage maintenance"
        mk(
            amount=200.00, pay_date=d(5), pt=pt_other_out,
            notes="[DEMO_V2] Test 12.2 DB: full keyword - 'garage maintenance'",
            bank=bank_chase, pm=pm_check, apartment=apt_ph,
            keywords=DEMO_KEYWORD_TAG + ",garage,maintenance",
        )

        # --- Test 13: Bank Name Match vs Mismatch ---
        # 13.1 Bank Match (BA)
        mk(
            amount=450.00, pay_date=d(8), pt=pt_other_in,
            notes="[DEMO_V2] Test 13.1 DB: bank match (BA)",
            bank=bank_ba, pm=pm_ach, apartment=apt_630,
        )
        # 13.2 Bank Mismatch (Chase)
        mk(
            amount=450.00, pay_date=d(8), pt=pt_other_in,
            notes="[DEMO_V2] Test 13.2 DB: bank mismatch (Chase)",
            bank=bank_chase, pm=pm_ach, apartment=apt_630,
        )

        # --- Test 14: Multiple Bookings for Same Tenant (Ambiguity) ---
        # 14.1 Booking A (Older)
        mk(
            amount=1500.00, pay_date=d(20), pt=pt_rent_in,
            notes="[DEMO_V2] Test 14.1 DB: Older booking for Michael",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )
        # 14.2 Booking B (Newer/Current)
        mk(
            amount=1500.00, pay_date=d(2), pt=pt_rent_in,
            notes="[DEMO_V2] Test 14.2 DB: Current booking for Michael",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )

        # --- Test 15: Exact Amount, Different Apartments (No Apartment in File) ---
        # 15.1 Apt 630
        mk(
            amount=888.00, pay_date=d(5), pt=pt_other_in,
            notes="[DEMO_V2] Test 15.1 DB: Apt 630-205 ($888)",
            bank=bank_ba, pm=pm_zelle, apartment=apt_630,
        )
        # 15.2 Apt 780
        mk(
            amount=888.00, pay_date=d(5), pt=pt_other_in,
            notes="[DEMO_V2] Test 15.2 DB: Apt 780-306 ($888)",
            bank=bank_ba, pm=pm_zelle, apartment=apt_780,
        )

        # --- Test 16: Zero/Negative Amount Handling (Edge Case) ---
        # (Note: Model validation might prevent 0, but we test small values)
        mk(
            amount=0.01, pay_date=d(1), pt=pt_other_in,
            notes="[DEMO_V2] Test 16 DB: Penny payment",
            bank=bank_ba, pm=pm_ach,
        )

        # --- Test 17: Very Old Payment (Outside 30-day window) ---
        mk(
            amount=1234.00, pay_date=d(80), pt=pt_rent_in,
            notes="[DEMO_V2] Test 17 DB: 80 days ago (outside default window)",
            bank=bank_ba, pm=pm_wire, booking=b_mike,
        )

        # --- Test 18: Notes Token Collision ---
        # 18.1 Target
        mk(
            amount=600.00, pay_date=d(3), pt=pt_other_in,
            notes="[DEMO_V2] Test 18.1 DB: Target for 'Refund'",
            bank=bank_ba, pm=pm_wire, keywords=DEMO_KEYWORD_TAG + ",refund",
        )
        # 18.2 Collision
        mk(
            amount=600.00, pay_date=d(3), pt=pt_other_in,
            notes="[DEMO_V2] Test 18.2 DB: Decoy with 'Refund' in notes but not keywords",
            bank=bank_ba, pm=pm_wire,
        )

        # --- Test 19: Payment Method Mismatch (Zelle vs Wire) ---
        mk(
            amount=555.00, pay_date=d(4), pt=pt_other_in,
            notes="[DEMO_V2] Test 19 DB: Zelle record (file will be Wire)",
            bank=bank_ba, pm=pm_zelle,
        )

        # --- Test 20: Apartment Name Variation (630-205 vs 630 205) ---
        mk(
            amount=444.00, pay_date=d(2), pt=pt_other_in,
            notes="[DEMO_V2] Test 20 DB: Apt 630-205",
            bank=bank_ba, pm=pm_wire, apartment=apt_630,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Created {len(created)} demo DB payments (keyword={DEMO_KEYWORD_TAG}).\n"
            f"Now go to /payments-sync-v2/?demo=1 to load the matching file payments."
        ))

        summary = []
        for p in created:
            summary.append({"id": p.id, "amount": str(p.amount), "notes": p.notes, "status": p.payment_status})
        self.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False))
