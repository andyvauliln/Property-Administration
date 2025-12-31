"""
Payment Sync V2 - matching tests (manual + AI) using REAL DB payments.

Run:
  python commands/test_payment_sync_v2_matching.py

Optional:
  RUN_AI=1 python commands/test_payment_sync_v2_matching.py

Output:
  - prints JSON report to stdout
  - writes logs/payment_sync_v2_matching_test_results.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


def _iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


@dataclass
class TestCase:
    name: str
    description: str
    selected_file_payments: List[Dict[str, Any]]
    amount_delta: int
    with_confirmed: bool
    expected_db_ids: List[int]
    ai_model: str = "openai/gpt-4o"
    ai_base_prompt: str = (
        "Match the selected bank payments to the best DB payments.\n"
        "Return ONLY JSON array of {db_id, score, match_type, criteria}.\n"
        "If the selection likely corresponds to multiple DB payments, return multiple objects (2-3 max)."
    )
    ai_custom_prompt: str = ""


def _django_setup():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
    try:
        import django  # type: ignore
    except Exception as e:
        raise RuntimeError(f"django_import_failed: {e}") from e

    django.setup()


def _pick_real_payments():
    from mysite.models import Payment  # type: ignore

    qs = (
        Payment.objects.select_related(
            "payment_type",
            "payment_method",
            "apartment",
            "booking__tenant",
            "bank",
        )
        .exclude(payment_type=None)
        .exclude(payment_date=None)
        .order_by("-payment_date")[:250]
    )
    return list(qs)


def _payment_to_file_payment_dict(p, file_id: str, amount: Optional[float] = None, payment_date: Optional[date] = None, notes: Optional[str] = None):
    # Keep only fields used by composite builder + useful context.
    apt_name = None
    if hasattr(p, "apartmentName") and getattr(p, "apartmentName", None):
        apt_name = p.apartmentName
    elif getattr(p, "apartment", None):
        apt_name = p.apartment.name

    method_name = p.payment_method.name if getattr(p, "payment_method", None) else None
    bank_name = p.bank.name if getattr(p, "bank", None) else None
    direction = p.payment_type.type if getattr(p, "payment_type", None) else None

    return {
        "id": file_id,
        "amount": float(amount if amount is not None else (p.amount or 0)),
        "payment_date": _iso(payment_date if payment_date is not None else p.payment_date),
        "notes": notes if notes is not None else (p.notes or ""),
        "apartment_name": apt_name,
        "payment_method_name": method_name,
        "bank_name": bank_name,
        "payment_type_type": direction,
    }


def _build_testcases(real_payments) -> List[TestCase]:
    # Choose a couple of anchor payments (prefer ones with apartment + method + bank present).
    def good(p):
        apt = (getattr(p, "apartmentName", None) or (p.apartment.name if getattr(p, "apartment", None) else None))
        return bool(apt) and bool(getattr(p, "payment_type", None)) and bool(getattr(p, "payment_type", None).type)

    anchors = [p for p in real_payments if good(p)]
    if len(anchors) < 3:
        anchors = real_payments[:3]

    p1 = anchors[0]
    p2 = anchors[1] if len(anchors) > 1 else anchors[0]
    p3 = anchors[2] if len(anchors) > 2 else anchors[0]

    cases: List[TestCase] = []

    # 1) Single file -> single DB (should succeed for manual + AI)
    cases.append(
        TestCase(
            name="single_file_to_single_db_exact",
            description="Bank payment equals a real DB payment (amount/date/apt/type).",
            selected_file_payments=[_payment_to_file_payment_dict(p1, "bank-tc1")],
            amount_delta=150,
            with_confirmed=False,
            expected_db_ids=[int(p1.id)],
        )
    )

    # 2) Multiple file payments -> one DB payment (split amount)
    amt = float(p2.amount or 0)
    if amt <= 1:
        amt = 1000.0
    a1 = round(amt * 0.6, 2)
    a2 = round(amt - a1, 2)
    d0 = p2.payment_date
    cases.append(
        TestCase(
            name="multi_file_to_one_db_split_amount",
            description="Two bank rows sum to one DB payment (selection_count=2).",
            selected_file_payments=[
                _payment_to_file_payment_dict(p2, "bank-tc2-1", amount=a1, payment_date=d0),
                _payment_to_file_payment_dict(p2, "bank-tc2-2", amount=a2, payment_date=d0 + timedelta(days=1)),
            ],
            amount_delta=200,
            with_confirmed=False,
            expected_db_ids=[int(p2.id)],
        )
    )

    # 3) Single file wrong apartment but same amount/date (should usually fail apt boost; useful for checking)
    bad_notes = (p3.notes or "") + " WRONG_APT"
    fp4 = _payment_to_file_payment_dict(p3, "bank-tc4", notes=bad_notes)
    fp4["apartment_name"] = "ZZZ-999"  # force mismatch
    cases.append(
        TestCase(
            name="single_file_wrong_apartment",
            description="Same amount/date but apartment is wrong.",
            selected_file_payments=[fp4],
            amount_delta=150,
            with_confirmed=False,
            expected_db_ids=[],  # expected no strong match
        )
    )

    return cases


def _match_one_case(tc: TestCase, *, run_ai: bool) -> Dict[str, Any]:
    from mysite.models import Payment  # type: ignore
    from mysite.views import payment_sync_v2 as v2  # type: ignore

    composite = v2._build_composite_from_selected_file_payments(tc.selected_file_payments)

    # Determine db window exactly like match_selection_v2
    date_from = composite["date_from"]
    date_to = composite["date_to"]
    db_from = date_from - timedelta(days=30)
    db_to = date_to + timedelta(days=30)

    db_qs = (
        Payment.objects.filter(payment_date__range=(db_from, db_to))
        .select_related("payment_type", "payment_method", "apartment", "booking__tenant", "bank")
    )
    if not tc.with_confirmed:
        db_qs = db_qs.filter(payment_status="Pending")

    db_candidates = list(db_qs)

    # Manual scores (top 15)
    manual = []
    for p in db_candidates:
        scored = v2._manual_score_db_payment(p, composite, tc.amount_delta)
        if not scored:
            continue
        score, match_type, criteria = scored
        if score <= 0:
            continue
        manual.append(
            {
                "db_id": int(p.id),
                "score": float(score),
                "match_type": match_type,
                "criteria": criteria,
                "db_payment": v2._payment_to_rich_dict(p),
            }
        )
    manual.sort(key=lambda x: x["score"], reverse=True)
    manual_top = manual[:15]

    # AI scores
    ai = {"status": "skipped", "error": None, "raw": None, "candidates_sent": None, "results": []}
    if run_ai:
        top100 = v2._ai_prefilter_top100(db_qs, composite, tc.amount_delta)
        candidates_sent = [v2._payment_to_rich_dict(p) for p in top100]
        ai_json, ai_err = v2._ai_match_with_openrouter(
            model=tc.ai_model,
            base_prompt=tc.ai_base_prompt,
            custom_prompt=tc.ai_custom_prompt,
            composite=composite,
            candidate_payments=top100,
            request=None,
        )
        ai["raw"] = {"ai_json": ai_json, "ai_error": ai_err}
        ai["candidates_sent"] = candidates_sent
        if ai_err:
            ai["status"] = "error"
            ai["error"] = ai_err
        else:
            ai["status"] = "ok"
            # map to candidate objects
            by_id = {p.id: p for p in top100}
            out = []
            for item in ai_json or []:
                try:
                    db_id = int(item.get("db_id"))
                except Exception:
                    continue
                p = by_id.get(db_id)
                if not p:
                    continue
                out.append(
                    {
                        "db_id": db_id,
                        "score": float(_safe_float(item.get("score"), 0.0)),
                        "match_type": str(item.get("match_type") or "ai"),
                        "criteria": str(item.get("criteria") or ""),
                        "db_payment": v2._payment_to_rich_dict(p),
                    }
                )
            out.sort(key=lambda x: x["score"], reverse=True)
            ai["results"] = out[:15]

    # Success checks
    expected = set(tc.expected_db_ids or [])
    manual_ids = [x["db_id"] for x in manual_top]
    ai_ids = [x["db_id"] for x in ai.get("results") or []]

    def success_for(ids: Sequence[int]) -> bool:
        if not expected:
            # For negative tests: consider success if top1 isn't "confident"
            return True
        return expected.issubset(set(ids[:10]))

    result = {
        "name": tc.name,
        "description": tc.description,
        "settings": {"amount_delta": tc.amount_delta, "with_confirmed": tc.with_confirmed},
        "expected_db_ids": tc.expected_db_ids,
        "input": {"selected_file_payments": tc.selected_file_payments, "composite": {**composite, "date_from": _iso(composite.get("date_from")), "date_to": _iso(composite.get("date_to"))}},
        "db_window": {"db_from": _iso(db_from), "db_to": _iso(db_to), "db_candidates_count": len(db_candidates)},
        "db_candidates_preview": [v2._payment_to_rich_dict(p) for p in db_candidates[:25]],
        "manual": {"top": manual_top, "success": success_for(manual_ids), "top_ids": manual_ids[:10]},
        "ai": {"status": ai["status"], "error": ai["error"], "top": ai.get("results"), "success": success_for(ai_ids), "top_ids": ai_ids[:10], "raw": ai.get("raw"), "candidates_sent_preview": (ai.get("candidates_sent") or [])[:25]},
    }
    return result


def main():
    try:
        _django_setup()
    except Exception as e:
        print(
            json.dumps(
                {
                    "error": "cannot_run_tests",
                    "reason": str(e),
                    "hint": "Run inside your project venv where Django is installed.",
                },
                indent=2,
            )
        )
        return
    run_ai = str(os.getenv("RUN_AI", "")).strip().lower() in ("1", "true", "yes", "on")

    real_payments = _pick_real_payments()
    if not real_payments:
        print(json.dumps({"error": "No payments found in DB"}, indent=2))
        return

    cases = _build_testcases(real_payments)
    out = {
        "run_ai": run_ai,
        "cases_count": len(cases),
        "cases": [],
    }

    for tc in cases:
        out["cases"].append(_match_one_case(tc, run_ai=run_ai))

    report_json = json.dumps(out, indent=2, ensure_ascii=False, default=str)
    print(report_json)

    Path("logs").mkdir(parents=True, exist_ok=True)
    Path("logs/payment_sync_v2_matching_test_results.json").write_text(report_json, encoding="utf-8")


if __name__ == "__main__":
    main()

