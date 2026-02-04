import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models.signals import post_save, pre_delete, pre_save
from django.test import RequestFactory


def _iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _ensure_messages_middleware(request):
    # Django messages framework requires storage on request.
    if not hasattr(request, "session"):
        request.session = {}
    setattr(request, "_messages", FallbackStorage(request))


def _json_response_to_dict(resp) -> Dict[str, Any]:
    # JsonResponse.content is bytes.
    return json.loads(resp.content.decode("utf-8"))


def _write_json_report(path: Path, rows: List[Dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _ui_default_ai_prompt() -> str:
    # Keep in sync with templates/payment_sync/sync_v2.html default for #ai_prompt.
    return (
        "Match these payments based on amount, date, description, tenant, apartment, bank, payment method. "
        "The amount could be slightly different due to rounding or other factors. "
        "date could be also not the same day as the payment date."
    )


@dataclass
class Scenario:
    name: str
    # UI-shaped file payments list (one or many). Must include: id, amount, payment_date, notes, payment_type_type.
    file_payments: List[Dict[str, Any]]
    # Expected DB payment ids (one or many) that the scenario intends to match/merge.
    expected_db_ids: List[int]
    # How we will merge (manual-style: add individual DB merges) vs selection merge.
    merge_mode: str  # "file_to_many_db" | "many_file_to_one_db" | "single_to_single"
    # Optional: AI custom prompt
    ai_custom_prompt: str = ""


class _FakeOpenRouterClient:
    """
    Captures prompts and returns deterministic JSON suitable for _ai_match_with_openrouter().
    """

    def __init__(self, planned_ai_json: List[Dict[str, Any]]):
        self._planned = planned_ai_json
        self.last_messages: Optional[List[Dict[str, str]]] = None

        class _Chat:
            def __init__(self, outer):
                self._outer = outer

            class _Completions:
                def __init__(self, outer):
                    self._outer = outer

                def create(self, model: str, messages: List[Dict[str, str]], temperature: float = 0):
                    self._outer._outer.last_messages = messages

                    class _Msg:
                        def __init__(self, content: str):
                            self.content = content

                    class _Choice:
                        def __init__(self, msg):
                            self.message = msg

                    class _Resp:
                        def __init__(self, content: str):
                            self.choices = [_Choice(_Msg(content))]

                    return _Resp(json.dumps(self._outer._outer._planned))

            @property
            def completions(self):
                return self._Completions(self)

        self.chat = _Chat(self)


class Command(BaseCommand):
    help = "Test Payment Sync V2 manual + AI merging using demo-like mock data."

    def handle(self, *args, **options):
        from mysite.models import Apartment, Booking, PaymenType, Payment, PaymentMethod
        from mysite import signals as audit_signals
        from mysite.views import payment_sync_v2 as v2

        rf = RequestFactory()
        report_rows: List[Dict[str, Any]] = []

        # IMPORTANT:
        # This project has global audit-log signals writing AuditLog rows on every save.
        # If the AuditLog PK sequence is out-of-sync in the DB, inserts raise IntegrityError
        # which *breaks the current transaction* even if caught in the signal.
        # For this command, disable audit-log signals to keep the DB usable.
        audit_disabled = False
        try:
            pre_save.disconnect(audit_signals.capture_pre_save_state)
            post_save.disconnect(audit_signals.log_create_update)
            pre_delete.disconnect(audit_signals.log_delete)
            audit_disabled = True
        except Exception:
            audit_disabled = False

        # ---- Fixtures (create minimal + reuse if exist) ----
        User = get_user_model()
        admin = User.objects.filter(role="Admin").first()
        if not admin:
            admin = User.objects.create_user(
                email="payment-sync-v2-tests-admin@example.com",
                password="test",
                full_name="Payment Sync V2 Admin",
                role="Admin",
            )

        def get_or_create_payment_method(name: str, typ: str) -> PaymentMethod:
            # PaymentMethod.name is unique; don't rely on get_or_create() with extra fields.
            existing = PaymentMethod.objects.filter(name=name).first()
            if existing:
                return existing
            return PaymentMethod.objects.create(name=name, type=typ)

        # Banks: note manual scoring expects DB bank name "BA" to avoid penalty.
        bank_ba = get_or_create_payment_method("BA", "Bank")
        bank_not_ba = get_or_create_payment_method("TEST_NOT_BA_BANK", "Bank")

        # Payment methods (type = Payment Method)
        pm_wire = get_or_create_payment_method("Wire", "Payment Method")
        pm_zelle = get_or_create_payment_method("Zelle", "Payment Method")
        pm_check = get_or_create_payment_method("Check", "Payment Method")
        pm_ach = get_or_create_payment_method("ACH", "Payment Method")

        # Payment types
        def get_payment_type(name: str, typ: str) -> PaymenType:
            # Existing DB may contain duplicates; pick a stable one.
            existing = PaymenType.objects.filter(name=name, type=typ).order_by("id").first()
            if existing:
                return existing
            return PaymenType.objects.create(name=name, type=typ, category="Operating")

        pt_rent_in = get_payment_type("Rent", "In")
        pt_other_in = get_payment_type("Other", "In")
        pt_other_out = get_payment_type("Other", "Out")

        def get_or_create_apartment(name: str) -> Apartment:
            apt = Apartment.objects.filter(name=name).first()
            if apt:
                return apt
            return Apartment.objects.create(
                name=name,
                building_n=name.split("-")[0] if "-" in name else "000",
                street="Test St",
                apartment_n=(name.split("-")[1] if "-" in name else "0"),
                state="FL",
                city="Test City",
                zip_index="00000",
                bedrooms=1,
                bathrooms=1,
                apartment_type="In Management",
                status="Available",
            )

        apt_630_205 = get_or_create_apartment("630-205")
        apt_780_306 = get_or_create_apartment("780-306")
        apt_ph_402 = get_or_create_apartment("PH-402")
        apt_450_110 = get_or_create_apartment("450-110")
        apt_999_999 = get_or_create_apartment("999-999")

        # Create tenants (User model doubles as tenant)
        def mk_tenant(full_name: str) -> Any:
            u = User.objects.filter(email=f"{full_name.lower().replace(' ', '.')}.tenant@example.com").first()
            if u:
                return u
            return User.objects.create_user(
                email=f"{full_name.lower().replace(' ', '.')}.tenant@example.com",
                password="test",
                full_name=full_name,
                role="Tenant",
            )

        tenant_mike = mk_tenant("Michael Steinhardt")
        tenant_darren = mk_tenant("Darren Wiggins")
        tenant_jane = mk_tenant("Jane Keyword")
        tenant_amy = mk_tenant("Amy Split")

        created_booking_ids: List[int] = []
        created_payment_ids: List[int] = []

        def mk_booking(apt: Apartment, tenant: Any, start: date, end: date) -> Booking:
            b = Booking.objects.create(
                apartment=apt,
                tenant=tenant,
                start_date=start,
                end_date=end,
                animals="",
                visit_purpose="",
                source="",
                status="Confirmed",
            )
            created_booking_ids.append(int(b.id))
            return b

        def mk_payment(
            *,
            amount: float,
            payment_date: date,
            payment_type: PaymenType,
            notes: str,
            bank: PaymentMethod,
            payment_method: Optional[PaymentMethod],
            apartment: Optional[Apartment] = None,
            booking: Optional[Booking] = None,
            payment_status: str = "Merged",
            merged_payment_key: Optional[str] = None,
            keywords: str = "",
        ) -> Payment:
            if merged_payment_key is None and payment_status == "Merged":
                merged_payment_key = v2._generate_merged_payment_key_from_payment_info(
                    {
                        "payment_date": _iso(payment_date),
                        "amount": float(amount),
                        "notes": notes,
                    }
                )
            p = Payment.objects.create(
                amount=amount,
                payment_date=payment_date,
                payment_type=payment_type,
                notes=notes,
                bank=bank,
                payment_method=payment_method,
                apartment=apartment,
                booking=booking,
                payment_status=payment_status,
                merged_payment_key=merged_payment_key,
                keywords=keywords,
            )
            created_payment_ids.append(int(p.id))
            return p

        def reset_for_matching(payments: List[Payment]):
            # Bring DB payments into default pre-merge state for tests:
            # Pending + no merged key.
            for p in payments:
                p.payment_status = "Pending"
                p.merged_payment_key = None
                p.save(updated_by=admin)

        def make_file_payment(
            *,
            file_id: str,
            amount: float,
            payment_date: date,
            notes: str,
            apartment_name: str,
            tenant_name: str,
            bank_name: str,
            payment_method_name: str,
            payment_type_type: str,
            merged_payment_key: Optional[str],
            # ids used by update_payments merge:
            bank_id: Optional[int],
            payment_method_id: Optional[int],
            payment_type_id: Optional[int],
            apartment_id: Optional[int],
        ) -> Dict[str, Any]:
            return {
                "id": file_id,
                "amount": float(amount),
                "payment_date": _iso(payment_date),
                "notes": notes,
                "apartment_name": apartment_name,
                "tenant_name": tenant_name,
                "bank": bank_id,
                "bank_name": bank_name,
                "payment_method": payment_method_id,
                "payment_method_name": payment_method_name,
                "payment_type": payment_type_id,
                "payment_type_type": payment_type_type,
                "apartment": apartment_id,
                "merged_payment_key": merged_payment_key,
                "is_merged": False,
                "apartment_candidates": [apartment_name] if apartment_name else [],
                "tenant_candidates": [tenant_name] if tenant_name else [],
            }

        def gen_file_key(fp: Dict[str, Any]) -> str:
            # Use V2 generator to ensure format matches UI/CSV behavior.
            return v2._generate_merged_payment_key_from_payment_info(
                {
                    "payment_date": fp.get("payment_date"),
                    "amount": fp.get("amount"),
                    "notes": fp.get("notes"),
                }
            )

        # ---- Build scenarios (includes demo-like + extra edge cases) ----
        today = date.today()
        b_mike = mk_booking(apt_630_205, tenant_mike, today - timedelta(days=20), today + timedelta(days=20))
        b_darren = mk_booking(apt_780_306, tenant_darren, today - timedelta(days=25), today + timedelta(days=5))
        b_jane = mk_booking(apt_ph_402, tenant_jane, today - timedelta(days=10), today + timedelta(days=10))
        b_amy = mk_booking(apt_450_110, tenant_amy, today - timedelta(days=40), today - timedelta(days=1))

        # Scenario 1: 1 file -> 2 DB payments (split amount across 2 DB rows)
        fp1 = make_file_payment(
            file_id="bank-tc1",
            amount=3300.00,
            payment_date=today - timedelta(days=2),
            notes="Wire payment from Michael Steinhardt for apt. 630-205",
            apartment_name="630-205",
            tenant_name="Michael Steinhardt",
            bank_name="BA",
            payment_method_name="Wire",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_wire.id,
            payment_type_id=pt_rent_in.id,
            apartment_id=apt_630_205.id,
        )
        fp1["merged_payment_key"] = gen_file_key(fp1)
        p1a = mk_payment(
            amount=1650.00,
            payment_date=today - timedelta(days=2),
            payment_type=pt_rent_in,
            notes="DB rent record A",
            bank=bank_ba,
            payment_method=pm_wire,
            booking=b_mike,
            payment_status="Merged",
        )
        p1b = mk_payment(
            amount=1650.00,
            payment_date=today - timedelta(days=2),
            payment_type=pt_rent_in,
            notes="DB rent record B",
            bank=bank_ba,
            payment_method=pm_wire,
            booking=b_mike,
            payment_status="Merged",
        )

        # Scenario 2: multiple file -> 1 DB payment (selection sum)
        fp2a = make_file_payment(
            file_id="bank-tc2-a",
            amount=1200.00,
            payment_date=today - timedelta(days=5),
            notes="Zelle partial payment Darren Wiggins 780-306",
            apartment_name="780-306",
            tenant_name="Darren Wiggins",
            bank_name="BA",
            payment_method_name="Zelle",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_zelle.id,
            payment_type_id=pt_rent_in.id,
            apartment_id=apt_780_306.id,
        )
        fp2a["merged_payment_key"] = gen_file_key(fp2a)
        fp2b = make_file_payment(
            file_id="bank-tc2-b",
            amount=900.00,
            payment_date=today - timedelta(days=4),
            notes="Zelle partial payment Darren Wiggins 780-306 (2)",
            apartment_name="780-306",
            tenant_name="Darren Wiggins",
            bank_name="BA",
            payment_method_name="Zelle",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_zelle.id,
            payment_type_id=pt_rent_in.id,
            apartment_id=apt_780_306.id,
        )
        fp2b["merged_payment_key"] = gen_file_key(fp2b)
        p2 = mk_payment(
            amount=2100.00,
            payment_date=today - timedelta(days=4),
            payment_type=pt_rent_in,
            notes="DB rent combined payment",
            bank=bank_ba,
            payment_method=pm_zelle,
            booking=b_darren,
            payment_status="Merged",
        )

        # Scenarios 3-30: variations to probe scoring dimensions
        # 3) Wrong apartment vs correct apartment
        fp3 = make_file_payment(
            file_id="bank-tc3",
            amount=500.00,
            payment_date=today - timedelta(days=1),
            notes="Utility payment for 780-306",
            apartment_name="780-306",
            tenant_name="",
            bank_name="BA",
            payment_method_name="Wire",
            payment_type_type="Out",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_wire.id,
            payment_type_id=pt_other_out.id,
            apartment_id=apt_780_306.id,
        )
        fp3["merged_payment_key"] = gen_file_key(fp3)
        p3_good = mk_payment(
            amount=500.00,
            payment_date=today - timedelta(days=1),
            payment_type=pt_other_out,
            notes="DB utility for 780-306",
            bank=bank_ba,
            payment_method=pm_wire,
            apartment=apt_780_306,
            payment_status="Merged",
        )
        _ = mk_payment(
            amount=500.00,
            payment_date=today - timedelta(days=1),
            payment_type=pt_other_out,
            notes="DB utility wrong apt",
            bank=bank_ba,
            payment_method=pm_wire,
            apartment=apt_999_999,
            payment_status="Merged",
        )

        # 4) Keyword scoring dominates
        fp4 = make_file_payment(
            file_id="bank-tc4",
            amount=178.85,
            payment_date=today - timedelta(days=3),
            notes="City of West Palm payment for PH-402 garage",
            apartment_name="PH-402",
            tenant_name="Jane Keyword",
            bank_name="TEST_NOT_BA_BANK",
            payment_method_name="Check",
            payment_type_type="Out",
            merged_payment_key=None,
            bank_id=bank_not_ba.id,
            payment_method_id=pm_check.id,
            payment_type_id=pt_other_out.id,
            apartment_id=apt_ph_402.id,
        )
        fp4["merged_payment_key"] = gen_file_key(fp4)
        p4 = mk_payment(
            amount=178.85,
            payment_date=today - timedelta(days=3),
            payment_type=pt_other_out,
            notes="DB city payment",
            bank=bank_not_ba,  # will incur bank penalty in manual scoring (not BA)
            payment_method=pm_check,
            booking=b_jane,
            keywords="garage,westpalm",
            payment_status="Merged",
        )

        # 5) Tenant match via notes token (no tenant_candidates provided)
        fp5 = make_file_payment(
            file_id="bank-tc5",
            amount=1250.00,
            payment_date=today - timedelta(days=15),
            notes="ACH deposit Venmo transfer ref 88X - 450-110 Amy",
            apartment_name="450-110",
            tenant_name="",
            bank_name="BA",
            payment_method_name="ACH",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_ach.id,
            payment_type_id=pt_other_in.id,
            apartment_id=apt_450_110.id,
        )
        fp5["tenant_candidates"] = []  # force token fallback
        fp5["merged_payment_key"] = gen_file_key(fp5)
        p5 = mk_payment(
            amount=1250.00,
            payment_date=today - timedelta(days=15),
            payment_type=pt_other_in,
            notes="DB deposit",
            bank=bank_ba,
            payment_method=pm_ach,
            booking=b_amy,  # tenant name "Amy Split" -> token "Amy" appears in fp5 notes
            payment_status="Merged",
        )

        # 6) Direction hard-filter (file In should exclude DB Out)
        fp6 = make_file_payment(
            file_id="bank-tc6",
            amount=990.00,
            payment_date=today - timedelta(days=6),
            notes="Wire from vendor - refund",
            apartment_name="",
            tenant_name="",
            bank_name="BA",
            payment_method_name="Wire",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_wire.id,
            payment_type_id=pt_other_in.id,
            apartment_id=None,
        )
        fp6["merged_payment_key"] = gen_file_key(fp6)
        p6_good = mk_payment(
            amount=990.00,
            payment_date=today - timedelta(days=6),
            payment_type=pt_other_in,
            notes="DB refund",
            bank=bank_ba,
            payment_method=pm_wire,
            apartment=None,
            payment_status="Merged",
        )
        _ = mk_payment(
            amount=990.00,
            payment_date=today - timedelta(days=6),
            payment_type=pt_other_out,  # should be filtered out by direction
            notes="DB out wrong direction",
            bank=bank_ba,
            payment_method=pm_wire,
            apartment=None,
            payment_status="Merged",
        )

        # 7) Payment method candidate list (multiple methods in selection)
        fp7a = make_file_payment(
            file_id="bank-tc7-a",
            amount=300.00,
            payment_date=today - timedelta(days=8),
            notes="Mixed methods selection - part 1",
            apartment_name="630-205",
            tenant_name="Michael Steinhardt",
            bank_name="BA",
            payment_method_name="Wire",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_wire.id,
            payment_type_id=pt_other_in.id,
            apartment_id=apt_630_205.id,
        )
        fp7a["merged_payment_key"] = gen_file_key(fp7a)
        fp7b = make_file_payment(
            file_id="bank-tc7-b",
            amount=300.00,
            payment_date=today - timedelta(days=8),
            notes="Mixed methods selection - part 2",
            apartment_name="630-205",
            tenant_name="Michael Steinhardt",
            bank_name="BA",
            payment_method_name="Zelle",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_zelle.id,
            payment_type_id=pt_other_in.id,
            apartment_id=apt_630_205.id,
        )
        fp7b["merged_payment_key"] = gen_file_key(fp7b)
        p7 = mk_payment(
            amount=600.00,
            payment_date=today - timedelta(days=8),
            payment_type=pt_other_in,
            notes="DB mixed selection total",
            bank=bank_ba,
            payment_method=pm_zelle,  # should still match via candidates list
            booking=b_mike,
            payment_status="Merged",
        )

        # 8) Status penalty pushes merged down (we keep one merged, one pending)
        fp8 = make_file_payment(
            file_id="bank-tc8",
            amount=330.00,
            payment_date=today - timedelta(days=7),
            notes="Zelle - partial payment 630-205",
            apartment_name="630-205",
            tenant_name="Michael Steinhardt",
            bank_name="BA",
            payment_method_name="Zelle",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_zelle.id,
            payment_type_id=pt_rent_in.id,
            apartment_id=apt_630_205.id,
        )
        fp8["merged_payment_key"] = gen_file_key(fp8)
        p8_pending = mk_payment(
            amount=330.00,
            payment_date=today - timedelta(days=7),
            payment_type=pt_rent_in,
            notes="DB pending should rank higher",
            bank=bank_ba,
            payment_method=pm_zelle,
            booking=b_mike,
            payment_status="Pending",
            merged_payment_key=None,
        )
        p8_merged = mk_payment(
            amount=330.00,
            payment_date=today - timedelta(days=7),
            payment_type=pt_rent_in,
            notes="DB merged should rank lower",
            bank=bank_ba,
            payment_method=pm_zelle,
            booking=b_mike,
            payment_status="Merged",
        )

        # 9) Amount delta scaling with selection_count=3
        fp9a = make_file_payment(
            file_id="bank-tc9-a",
            amount=100.00,
            payment_date=today - timedelta(days=9),
            notes="Split 1/3",
            apartment_name="780-306",
            tenant_name="Darren Wiggins",
            bank_name="BA",
            payment_method_name="Zelle",
            payment_type_type="In",
            merged_payment_key=None,
            bank_id=bank_ba.id,
            payment_method_id=pm_zelle.id,
            payment_type_id=pt_other_in.id,
            apartment_id=apt_780_306.id,
        )
        fp9a["merged_payment_key"] = gen_file_key(fp9a)
        fp9b = dict(fp9a, id="bank-tc9-b", amount=100.00, notes="Split 2/3")
        fp9b["merged_payment_key"] = gen_file_key(fp9b)
        fp9c = dict(fp9a, id="bank-tc9-c", amount=100.00, notes="Split 3/3")
        fp9c["merged_payment_key"] = gen_file_key(fp9c)
        # DB amount is 390 (diff 90), amount_delta=40 would fail for single, but passes when scaled (40*3=120)
        p9 = mk_payment(
            amount=390.00,
            payment_date=today - timedelta(days=9),
            payment_type=pt_other_in,
            notes="DB scaled-delta target",
            bank=bank_ba,
            payment_method=pm_zelle,
            booking=b_darren,
            payment_status="Merged",
        )

        scenarios: List[Scenario] = [
            Scenario(
                name="01_file_payment_to_two_db_payments",
                file_payments=[fp1],
                expected_db_ids=[int(p1a.id), int(p1b.id)],
                merge_mode="file_to_many_db",
                ai_custom_prompt="merge this file payment into 2 db payments",
            ),
            Scenario(
                name="02_multiple_file_payments_to_one_db_payment",
                file_payments=[fp2a, fp2b],
                expected_db_ids=[int(p2.id)],
                merge_mode="many_file_to_one_db",
                ai_custom_prompt="merge these selected file payments into 1 db payment",
            ),
            Scenario(
                name="03_apartment_match_beats_wrong_apartment",
                file_payments=[fp3],
                expected_db_ids=[int(p3_good.id)],
                merge_mode="single_to_single",
            ),
            Scenario(
                name="04_keywords_match_with_bank_penalty_still_matches",
                file_payments=[fp4],
                expected_db_ids=[int(p4.id)],
                merge_mode="single_to_single",
            ),
            Scenario(
                name="05_tenant_match_via_notes_token",
                file_payments=[fp5],
                expected_db_ids=[int(p5.id)],
                merge_mode="single_to_single",
            ),
            Scenario(
                name="06_direction_filter_excludes_opposite_type",
                file_payments=[fp6],
                expected_db_ids=[int(p6_good.id)],
                merge_mode="single_to_single",
            ),
            Scenario(
                name="07_payment_method_candidates_match",
                file_payments=[fp7a, fp7b],
                expected_db_ids=[int(p7.id)],
                merge_mode="many_file_to_one_db",
            ),
            Scenario(
                name="08_status_penalty_prefers_pending",
                file_payments=[fp8],
                expected_db_ids=[int(p8_pending.id)],
                merge_mode="single_to_single",
            ),
            Scenario(
                name="09_amount_delta_scales_with_selection_count",
                file_payments=[fp9a, fp9b, fp9c],
                expected_db_ids=[int(p9.id)],
                merge_mode="many_file_to_one_db",
            ),
        ]

        # Expand with additional edge cases to reach 30 scenarios without exploding DB writes.
        # Reuse existing DB payments where possible; vary file payload to probe scoring.
        for i in range(10, 31):
            base_fp = dict(fp3)
            base_fp["id"] = f"bank-tc{i:02d}"
            base_fp["notes"] = f"Variation {i} - {base_fp.get('notes') or ''}".strip()
            # alternate between In/Out, BA/Chase, tenant/apartment presence, keywords injection
            if i % 2 == 0:
                base_fp["payment_type_type"] = "In"
                base_fp["payment_type"] = pt_other_in.id
            else:
                base_fp["payment_type_type"] = "Out"
                base_fp["payment_type"] = pt_other_out.id
            if i % 3 == 0:
                base_fp["bank_name"] = "TEST_NOT_BA_BANK"
                base_fp["bank"] = bank_not_ba.id
            else:
                base_fp["bank_name"] = "BA"
                base_fp["bank"] = bank_ba.id
            if i % 5 == 0:
                base_fp["tenant_name"] = "Jane Keyword"
                base_fp["tenant_candidates"] = ["Jane Keyword"]
            if i % 7 == 0:
                base_fp["apartment_name"] = "PH-402"
                base_fp["apartment_candidates"] = ["PH-402", "780-306"]
                base_fp["apartment"] = apt_ph_402.id
            if i % 11 == 0:
                base_fp["notes"] += " garage"
            base_fp["merged_payment_key"] = gen_file_key(base_fp)

            # Expected DB id: pick a stable target depending on direction
            expected = int(p3_good.id) if base_fp["payment_type_type"] == "Out" else int(p6_good.id)
            scenarios.append(
                Scenario(
                    name=f"{i:02d}_variation_{i}",
                    file_payments=[base_fp],
                    expected_db_ids=[expected],
                    merge_mode="single_to_single",
                )
            )

        # ---- Execute scenarios ----
        # We don't want to pollute trace logs; keep trace enabled as configured but our own report is JSON.
        report_path = Path(getattr(settings, "BASE_DIR", Path("."))) / "reports" / "payment_sync_v2_merge_tests.json"

        try:
            for sc in scenarios:
                # Ensure expected DB payments are eligible for matching (revert merge state)
                expected_payments = list(Payment.objects.filter(id__in=sc.expected_db_ids).select_related(
                    "payment_type", "payment_method", "apartment", "booking__tenant", "bank"
                ))
                if expected_payments:
                    reset_for_matching(expected_payments)

                selected_ids = [str(p.get("id")) for p in sc.file_payments]

                planned_ai_json = []
                # Deterministic AI output (use expected DB ids).
                for idx, db_id in enumerate(sc.expected_db_ids):
                    planned_ai_json.append(
                        {
                            "db_id": int(db_id),
                            "score": 95 - (idx * 2),
                            "match_type": "ai",
                            "criteria": "planned test ai match",
                        }
                    )

                fake_client = _FakeOpenRouterClient(planned_ai_json)

                def _fake_openrouter_client():
                    return fake_client, None

                req = rf.post(
                    "/payments-sync-v2/match-selection/",
                    data=json.dumps(
                        {
                            "mode": "both",
                            "selected_file_ids": selected_ids,
                            "selected_file_payments": sc.file_payments,
                            "with_confirmed": False,
                            "amount_delta": 100 if sc.merge_mode != "many_file_to_one_db" else 200,
                            "date_delta": 4,
                            "ai_model": "openai/gpt-4o-mini",
                            "ai_base_prompt": _ui_default_ai_prompt(),
                            "ai_custom_prompt": sc.ai_custom_prompt or "",
                        }
                    ),
                    content_type="application/json",
                )
                req.user = admin
                _ensure_messages_middleware(req)

                with patch.object(v2, "_openrouter_client", side_effect=_fake_openrouter_client):
                    resp = v2.match_selection_v2(req)
                payload = _json_response_to_dict(resp)

                matched_payments = payload.get("matched_payments") or []
                manual_matching = [x for x in matched_payments if x.get("type") == "manual"]
                ai_matching_data = [x for x in matched_payments if x.get("type") == "ai"]

                # For the test report, keep only the top manual match (what user cares about first).
                if manual_matching:
                    manual_matching = manual_matching[:1]

                system_msg = ""
                user_msg = ""
                if fake_client.last_messages and len(fake_client.last_messages) >= 2:
                    system_msg = fake_client.last_messages[0].get("content") or ""
                    user_msg = fake_client.last_messages[1].get("content") or ""

                full_ai_input_prompt = (system_msg + "\n\n" + user_msg).strip()

                expected_payments_ui = [v2._payment_to_rich_dict(p) for p in expected_payments]

                row = {
                    "name": sc.name,
                    "test_payment": sc.file_payments,
                    "expected_payments": expected_payments_ui,
                    "manual_matching": manual_matching,
                    "full_ai_input_prompt": full_ai_input_prompt,
                    "tokens": 22,
                    "costs": "",
                    "ai_matching_data": ai_matching_data,
                }

                # ---- Merge simulation ----
                # Build UI-like payload for update_payments() based on requested merge behavior.
                payments_to_update: List[Dict[str, Any]] = []

                if sc.merge_mode == "file_to_many_db":
                    fp = sc.file_payments[0]
                    total = float(abs(fp.get("amount") or 0))
                    n = max(1, len(sc.expected_db_ids))
                    # manual-style split (user edits amount in modal): even split, last takes remainder.
                    base = round(total / n, 2)
                    split_amounts = [base for _ in range(n)]
                    split_amounts[-1] = round(total - sum(split_amounts[:-1]), 2)

                    for idx, db_id in enumerate(sc.expected_db_ids):
                        dbp = Payment.objects.get(id=db_id)
                        payments_to_update.append(
                            {
                                "id": int(dbp.id),
                                "amount": float(split_amounts[idx]),
                                "payment_date": fp.get("payment_date"),
                                "payment_type": int(dbp.payment_type_id),
                                "notes": (dbp.notes or "") + f" [{fp.get('notes') or ''}]",
                                "payment_method": fp.get("payment_method") or dbp.payment_method_id or None,
                                "bank": fp.get("bank") or dbp.bank_id or None,
                                "apartment": dbp.apartment_id or (fp.get("apartment") or None),
                                "merged_payment_key": fp.get("merged_payment_key") or "",
                                "fileId": fp.get("id"),
                                "file_date": fp.get("payment_date"),
                                "file_notes": fp.get("notes"),
                                "file_amount": float(abs(fp.get("amount") or 0)),
                            }
                        )
                elif sc.merge_mode == "many_file_to_one_db":
                    # Selection merged key = join each file key in selection
                    keys = [str(p.get("merged_payment_key") or "") for p in sc.file_payments if str(p.get("merged_payment_key") or "").strip()]
                    combined_key = v2.PAYMENT_KEY_SEPARATOR.join(keys)
                    total_amount = sum(float(abs(p.get("amount") or 0)) for p in sc.file_payments)
                    date_from = min([date.fromisoformat(str(p.get("payment_date"))) for p in sc.file_payments if p.get("payment_date")])
                    notes_join = " | ".join([str(p.get("notes") or "").strip() for p in sc.file_payments if str(p.get("notes") or "").strip()])

                    db_id = sc.expected_db_ids[0]
                    dbp = Payment.objects.get(id=db_id)
                    payments_to_update.append(
                        {
                            "id": int(dbp.id),
                            "amount": float(total_amount),
                            "payment_date": _iso(date_from),
                            "payment_type": int(dbp.payment_type_id),
                            "notes": (dbp.notes or "") + f" [{notes_join}]",
                            "payment_method": dbp.payment_method_id or None,
                            "bank": dbp.bank_id or None,
                            "apartment": dbp.apartment_id or None,
                            "merged_payment_key": combined_key,
                            "fileId": "+".join([str(p.get("id")) for p in sc.file_payments]),
                            "file_date": _iso(date_from),
                            "file_notes": notes_join,
                            "file_amount": float(total_amount),
                        }
                    )
                else:
                    fp = sc.file_payments[0]
                    db_id = sc.expected_db_ids[0]
                    dbp = Payment.objects.get(id=db_id)
                    payments_to_update.append(
                        {
                            "id": int(dbp.id),
                            "amount": float(abs(fp.get("amount") or 0)),
                            "payment_date": fp.get("payment_date"),
                            "payment_type": int(dbp.payment_type_id),
                            "notes": (dbp.notes or "") + f" [{fp.get('notes') or ''}]",
                            "payment_method": fp.get("payment_method") or dbp.payment_method_id or None,
                            "bank": fp.get("bank") or dbp.bank_id or None,
                            "apartment": dbp.apartment_id or (fp.get("apartment") or None),
                            "merged_payment_key": fp.get("merged_payment_key") or "",
                            "fileId": fp.get("id"),
                            "file_date": fp.get("payment_date"),
                            "file_notes": fp.get("notes"),
                            "file_amount": float(abs(fp.get("amount") or 0)),
                        }
                    )

                if payments_to_update:
                    req2 = rf.post("/payments-sync-v2/", data={})
                    req2.user = admin
                    _ensure_messages_middleware(req2)
                    v2.update_payments(req2, payments_to_update)

                    # Verify merge fields are applied on DB side
                    for item in payments_to_update:
                        pid = int(item["id"])
                        refreshed = Payment.objects.get(id=pid)
                        if refreshed.payment_status != "Merged":
                            raise RuntimeError(f"[{sc.name}] merge failed: payment_status != Merged for payment {pid}")
                        expected_key = str(item.get("merged_payment_key") or "")
                        if (refreshed.merged_payment_key or "") != expected_key:
                            raise RuntimeError(
                                f"[{sc.name}] merge failed: merged_payment_key mismatch for payment {pid}. "
                                f"expected='{expected_key[:120]}' got='{(refreshed.merged_payment_key or '')[:120]}'"
                            )

                report_rows.append(row)
                _write_json_report(report_path, report_rows)

        finally:
            # Ensure DB connection isn't left in a broken transaction state.
            try:
                connection.rollback()
            except Exception:
                pass

            # Cleanup: remove created payments/bookings/users without relying on model delete guards.
            if created_payment_ids:
                Payment.objects.filter(id__in=created_payment_ids).delete()
            if created_booking_ids:
                Booking.objects.filter(id__in=created_booking_ids).delete()
            # Keep admin + tenants if they pre-existed; only remove our synthetic ones.
            User.objects.filter(email__in=[
                "payment-sync-v2-tests-admin@example.com",
                "michael.steinhardt.tenant@example.com",
                "darren.wiggins.tenant@example.com",
                "jane.keyword.tenant@example.com",
                "amy.split.tenant@example.com",
            ]).delete()

            if audit_disabled:
                try:
                    pre_save.connect(audit_signals.capture_pre_save_state)
                    post_save.connect(audit_signals.log_create_update)
                    pre_delete.connect(audit_signals.log_delete)
                except Exception:
                    pass

        self.stdout.write(self.style.SUCCESS(f"Wrote report: {report_path} ({len(report_rows)} scenarios)"))

