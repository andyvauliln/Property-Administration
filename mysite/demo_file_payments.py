"""
20 hardcoded file payment demo cases for Payment Sync V2.
Used when ?demo=1 is loaded. Dates are relative to today (date_offset_days = days ago).
"""

from datetime import date, timedelta
from typing import Any, Dict, List


def _fp(
    file_id: str,
    amount: float,
    date_offset_days: int,
    notes: str,
    *,
    apartment_candidates: List[str] = None,
    tenant_candidates: List[str] = None,
    bank_name: str = "BA",
    payment_method_name: str = "Wire",
    payment_type_name: str = "Rent",
    payment_type_type: str = "In",
) -> Dict[str, Any]:
    return {
        "id": file_id,
        "amount": float(amount),
        "date_offset_days": date_offset_days,
        "notes": notes,
        "apartment_candidates": apartment_candidates or [],
        "tenant_candidates": tenant_candidates or [],
        "bank_name": bank_name,
        "payment_method_name": payment_method_name,
        "payment_type_name": payment_type_name,
        "payment_type_type": payment_type_type,
    }


def get_demo_file_payments_raw() -> List[Dict[str, Any]]:
    """Return 20 hardcoded demo file payment definitions (before ID resolution)."""
    return [
        _fp("bank-tc1", 3300.00, 2, "[Test 1] Exact match: $3300 Wire 630-205 Rent In",
            apartment_candidates=["630-205"], tenant_candidates=["Michael Steinhardt"]),
        _fp("bank-tc2", 3300.00, 5, "[Test 2] 1 file -> 2 DB split: $3300 ($1650+$1650)",
            apartment_candidates=["630-205"], tenant_candidates=["Michael Steinhardt"]),
        _fp("bank-tc3a", 1200.00, 4, "[Test 3-A] Multi file->1 DB: $1200 part of $2100",
            apartment_candidates=["780-306"], tenant_candidates=["Darren Wiggins"],
            payment_method_name="Zelle"),
        _fp("bank-tc3b", 900.00, 4, "[Test 3-B] Multi file->1 DB: $900 part of $2100",
            apartment_candidates=["780-306"], tenant_candidates=["Darren Wiggins"],
            payment_method_name="Zelle"),
        _fp("bank-tc4", 500.00, 1, "[Test 4] Apartment match: $500 Out 780-306",
            apartment_candidates=["780-306"], payment_type_name="Other", payment_type_type="Out"),
        _fp("bank-tc5", 178.85, 3, "[Test 5] Keyword match: $178.85 PH-402 garage",
            apartment_candidates=["PH-402"], tenant_candidates=["Jane Keyword"],
            bank_name="Chase", payment_method_name="Check",
            payment_type_name="Other", payment_type_type="Out"),
        _fp("bank-tc6", 1250.00, 15, "[Test 6] Tenant token: $1250 ACH 450-110 Amy deposit",
            apartment_candidates=["450-110"], tenant_candidates=[],
            payment_method_name="ACH", payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc7", 990.00, 6, "[Test 7] Direction filter: $990 In Wire refund",
            bank_name="BA", payment_method_name="Wire",
            payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc8", 330.00, 7, "[Test 8] Status preference: $330 Zelle 630-205",
            apartment_candidates=["630-205"], tenant_candidates=["Michael Steinhardt"],
            payment_method_name="Zelle"),
        _fp("bank-tc9", 750.00, 7, "[Test 9] Date off: $750 Wire 780-306 (file +5 days vs DB)",
            apartment_candidates=["780-306"], tenant_candidates=["Darren Wiggins"]),
        _fp("bank-tc10", 3295.00, 2, "[Test 10] Amount Diff: $3295 file vs $3300 DB",
            apartment_candidates=["630-205"], tenant_candidates=["Michael Steinhardt"]),
        _fp("bank-tc11", 1000.00, 0, "[Test 11] Ambiguity: $1000 - date vs amount trade-off",
            apartment_candidates=["780-306"], tenant_candidates=["Darren Wiggins"],
            payment_method_name="Zelle"),
        _fp("bank-tc12", 200.00, 5, "garage maintenance payment",
            apartment_candidates=["PH-402"], bank_name="Chase", payment_method_name="Check",
            payment_type_name="Other", payment_type_type="Out"),
        _fp("bank-tc13", 450.00, 8, "[Test 13] Bank Match: file from BA",
            apartment_candidates=["630-205"], bank_name="BA",
            payment_method_name="ACH", payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc14", 1500.00, 2, "[Test 14] Multiple Bookings: $1500 for Michael",
            apartment_candidates=["630-205"], tenant_candidates=["Michael Steinhardt"]),
        _fp("bank-tc15", 888.00, 5, "[Test 15] Apt Ambiguity: $888 (no apt in file)",
            apartment_candidates=[], tenant_candidates=[], payment_method_name="Zelle",
            payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc16", 0.01, 1, "[Test 16] Edge Case: Penny payment",
            payment_method_name="ACH", payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc17", 1234.00, 80, "[Test 17] Very Old: 80 days ago (window edge)",
            apartment_candidates=["630-205"], tenant_candidates=["Michael Steinhardt"]),
        _fp("bank-tc18", 600.00, 3, "Refund for service",
            payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc19", 555.00, 4, "[Test 19] Method Mismatch: file Wire, DB Zelle",
            payment_method_name="Wire", payment_type_name="Other", payment_type_type="In"),
        _fp("bank-tc20", 444.00, 2, "[Test 20] Apt Variation: file '630 205' vs DB '630-205'",
            apartment_candidates=["630 205"], payment_type_name="Other", payment_type_type="In"),
    ]
