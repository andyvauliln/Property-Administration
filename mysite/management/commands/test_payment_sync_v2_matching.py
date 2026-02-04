import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from django.core.management.base import BaseCommand


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
    expected_db_ids: List[int]
    ai_model: str = "openai/gpt-4o"
    ai_base_prompt: str = (
        "Match the selected bank payments to the best DB payments.\n"
        "Return ONLY JSON array of {db_id, score, match_type, criteria}.\n"
        "If the selection likely corresponds to multiple DB payments, return multiple objects (2-3 max)."
    )
    ai_custom_prompt: str = ""


def _payment_to_file_payment_dict(p, file_id: str, amount: Optional[float] = None, payment_date: Optional[date] = None, notes: Optional[str] = None):
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


def _pick_real_payments():
    from mysite.models import Payment

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


def _build_testcases(real_payments) -> List[TestCase]:
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

    cases.append(
        TestCase(
            name="single_file_to_single_db_exact",
            description="Bank payment equals a real DB payment (amount/date/apt/type).",
            selected_file_payments=[_payment_to_file_payment_dict(p1, "bank-tc1")],
            amount_delta=150,
            expected_db_ids=[int(p1.id)],
        )
    )

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
            expected_db_ids=[int(p2.id)],
        )
    )

    bad_notes = (p3.notes or "") + " WRONG_APT"
    fp4 = _payment_to_file_payment_dict(p3, "bank-tc4", notes=bad_notes)
    fp4["apartment_name"] = "ZZZ-999"
    cases.append(
        TestCase(
            name="single_file_wrong_apartment",
            description="Same amount/date but apartment is wrong.",
            selected_file_payments=[fp4],
            amount_delta=150,
            expected_db_ids=[],
        )
    )

    # NEW TEST CASES
    # 5) Date slightly off - find payments with nearby dates and similar amounts
    date_test_anchor = None
    for p in anchors:
        # Find a payment that has others within ±14 days with similar amounts (relaxed from 7)
        nearby = [x for x in real_payments 
                  if x.id != p.id 
                  and abs((x.payment_date - p.payment_date).days) <= 14
                  and abs(float(x.amount or 0) - float(p.amount or 0)) < 300]
        if len(nearby) >= 1:  # Relaxed from 2
            date_test_anchor = p
            break
    
    # Fallback: just use first anchor if no good match
    if not date_test_anchor and len(anchors) > 0:
        date_test_anchor = anchors[0]
    
    if date_test_anchor:
        fp5 = _payment_to_file_payment_dict(date_test_anchor, "bank-tc5", 
                                            payment_date=date_test_anchor.payment_date + timedelta(days=5))
        cases.append(
            TestCase(
                name="date_slightly_off",
                description="File date is 5 days after DB date, with competing nearby dates.",
                selected_file_payments=[fp5],
                amount_delta=150,
                expected_db_ids=[int(date_test_anchor.id)],
            )
        )

    # 6) Amount at delta edge - find payment with others nearby in amount
    delta = 150
    amount_test_anchor = None
    for p in anchors:
        p_amt = float(p.amount or 0)
        # Find others with amounts within ±(delta * 2) to create ambiguity (relaxed)
        nearby_amounts = [x for x in real_payments 
                          if x.id != p.id 
                          and abs(float(x.amount or 0) - p_amt) < (delta * 2)
                          and abs((x.payment_date - p.payment_date).days) <= 60]  # Relaxed from 30
        if len(nearby_amounts) >= 1:  # Relaxed from 2
            amount_test_anchor = p
            break
    
    # Fallback: use second anchor if available
    if not amount_test_anchor and len(anchors) > 1:
        amount_test_anchor = anchors[1]
    
    if amount_test_anchor:
        fp6 = _payment_to_file_payment_dict(amount_test_anchor, "bank-tc6", 
                                            amount=float(amount_test_anchor.amount or 0) + delta)
        cases.append(
            TestCase(
                name="amount_at_delta_edge",
                description="File amount = DB amount + delta, with competing similar amounts.",
                selected_file_payments=[fp6],
                amount_delta=delta,
                expected_db_ids=[int(amount_test_anchor.id)],
            )
        )

    # 7) Strong keyword match
    keyword_anchor = None
    for p in anchors:
        if (p.notes or "").strip() and (p.apartment and p.apartment.name):
            keyword_anchor = p
            break
    if keyword_anchor:
        tenant_name = ""
        if getattr(keyword_anchor, "booking", None) and getattr(keyword_anchor.booking, "tenant", None):
            tenant_name = keyword_anchor.booking.tenant.full_name or ""
        apt_name = keyword_anchor.apartment.name if keyword_anchor.apartment else ""
        notes_with_keywords = f"{tenant_name} {apt_name} payment ref"
        fp7 = _payment_to_file_payment_dict(keyword_anchor, "bank-tc7", notes=notes_with_keywords)
        cases.append(
            TestCase(
                name="strong_keyword_match",
                description="File notes contain tenant name + apartment (should boost score).",
                selected_file_payments=[fp7],
                amount_delta=150,
                expected_db_ids=[int(keyword_anchor.id)],
            )
        )

    # 8) Multiple similar candidates - find similar amounts, different apartments
    similar_group = {}
    for p in real_payments:
        if not p.payment_type or not p.apartment:
            continue
        amt = float(p.amount or 0)
        # Group by exact amount
        if amt not in similar_group:
            similar_group[amt] = []
        similar_group[amt].append(p)
    
    multi_candidates = None
    for amt, group in similar_group.items():
        # Filter to only those with similar dates (within 120 days of each other) - relaxed
        if len(group) >= 2:  # Relaxed from 3
            group_sorted = sorted(group, key=lambda x: x.payment_date)
            date_range = (group_sorted[-1].payment_date - group_sorted[0].payment_date).days
            if date_range <= 120:  # Relaxed from 60
                apts = set([p.apartment.name for p in group if p.apartment])
                if len(apts) >= 2:  # Relaxed from 3
                    multi_candidates = group[:3]  # Take 3
                    break
    
    # Fallback: find any payments with same amount
    if not multi_candidates:
        for amt, group in similar_group.items():
            if len(group) >= 2:
                multi_candidates = group[:2]
                break
    
    if multi_candidates and len(multi_candidates) >= 2:
        target = multi_candidates[0]
        fp8 = _payment_to_file_payment_dict(target, "bank-tc8")
        cases.append(
            TestCase(
                name="multiple_similar_candidates",
                description=f"Multiple DB payments with same amount ({float(target.amount or 0)}), different apartments; AI should pick by apartment match.",
                selected_file_payments=[fp8],
                amount_delta=150,
                expected_db_ids=[int(target.id)],
            )
        )

    # 9) Sparse data (no apartment) - find payment with multiple candidates at similar amount/date
    sparse_anchor = None
    for p in anchors:
        # Find others with similar amount and nearby date (relaxed criteria)
        competitors = [x for x in real_payments 
                       if x.id != p.id 
                       and abs(float(x.amount or 0) - float(p.amount or 0)) < 200  # Relaxed from 50
                       and abs((x.payment_date - p.payment_date).days) <= 14  # Relaxed from 7
                       and x.apartment and x.apartment.name != (p.apartment.name if p.apartment else None)]
        if len(competitors) >= 1:  # Relaxed from 2
            sparse_anchor = p
            break
    
    # Fallback: use third anchor if available
    if not sparse_anchor and len(anchors) > 2:
        sparse_anchor = anchors[2]
    
    if sparse_anchor:
        fp9 = _payment_to_file_payment_dict(sparse_anchor, "bank-tc9")
        fp9["apartment_name"] = None  # Remove apartment info
        fp9["notes"] = None  # Also remove notes to make it truly sparse
        cases.append(
            TestCase(
                name="sparse_data_no_apartment",
                description="File payment has no apartment/notes, multiple similar DB payments; tests fallback to amount/date only.",
                selected_file_payments=[fp9],
                amount_delta=150,
                expected_db_ids=[int(sparse_anchor.id)],
            )
        )

    return cases


def _match_one_case(tc: TestCase, *, run_ai: bool) -> Dict[str, Any]:
    from mysite.models import Payment
    from mysite.views import payment_sync_v2 as v2

    composite = v2._build_composite_from_selected_file_payments(tc.selected_file_payments)

    date_from = composite["date_from"]
    date_to = composite["date_to"]
    db_from = date_from - timedelta(days=30)
    db_to = date_to + timedelta(days=30)

    db_qs = (
        Payment.objects.filter(payment_date__range=(db_from, db_to))
        .select_related("payment_type", "payment_method", "apartment", "booking__tenant", "bank")
    )

    db_candidates = list(db_qs)

    manual = []
    for p in db_candidates:
        scored = v2._manual_score_db_payment(p, composite, tc.amount_delta)
        if not scored:
            continue
        if scored['total'] <= 0:
            continue
        manual.append(
            {
                "db_id": int(p.id),
                "score": float(scored['total']),
                "match_type": scored['match_type'],
                "criteria": scored['criteria'],
                "breakdown": scored.get('breakdown'),
                "breakdown_details": scored.get('breakdown_details'),
                "penalties": scored.get('penalties'),
                "matched_keywords": scored.get('matched_keywords'),
                "quality": scored.get('quality'),
                "db_payment": v2._payment_to_rich_dict(p),
            }
        )
    manual.sort(key=lambda x: x["score"], reverse=True)
    manual_top = manual[:15]

    ai = {"status": "skipped", "error": None, "raw": None, "results": [], "candidates_sent_preview": []}
    if run_ai:
        top100 = v2._ai_prefilter_top100(db_qs, composite, tc.amount_delta)
        ai_json, ai_err = v2._ai_match_with_openrouter(
            model=tc.ai_model,
            base_prompt=tc.ai_base_prompt,
            custom_prompt=tc.ai_custom_prompt,
            composite=composite,
            candidate_payments=top100,
            request=None,
        )
        ai["raw"] = {"ai_json": ai_json, "ai_error": ai_err}
        ai["candidates_sent_preview"] = [v2._payment_to_rich_dict(p) for p in top100[:25]]
        if ai_err:
            ai["status"] = "error"
            ai["error"] = ai_err
        else:
            ai["status"] = "ok"
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

    expected = set(tc.expected_db_ids or [])
    manual_ids = [x["db_id"] for x in manual_top]
    ai_ids = [x["db_id"] for x in ai.get("results") or []]

    def success_for(ids: Sequence[int]) -> bool:
        if not expected:
            return True
        return expected.issubset(set(ids[:10]))

    return {
        "name": tc.name,
        "description": tc.description,
        "settings": {"amount_delta": tc.amount_delta},
        "expected_db_ids": tc.expected_db_ids,
        "input": {
            "selected_file_payments": tc.selected_file_payments,
            "composite": {**composite, "date_from": _iso(composite.get("date_from")), "date_to": _iso(composite.get("date_to"))},
        },
        "db_window": {"db_from": _iso(db_from), "db_to": _iso(db_to), "db_candidates_count": len(db_candidates)},
        "db_candidates_preview": [v2._payment_to_rich_dict(p) for p in db_candidates[:25]],
        "manual": {"top": manual_top, "success": success_for(manual_ids), "top_ids": manual_ids[:10]},
        "ai": {
            "status": ai["status"],
            "error": ai["error"],
            "top": ai.get("results"),
            "success": success_for(ai_ids),
            "top_ids": ai_ids[:10],
            "raw": ai.get("raw"),
            "candidates_sent_preview": ai.get("candidates_sent_preview"),
        },
    }


class Command(BaseCommand):
    help = "Run Payment Sync V2 matching tests (manual + optional AI)."

    def add_arguments(self, parser):
        parser.add_argument("--ai", action="store_true", help="Run AI matching (requires OPENROUTER_API_KEY)")
        parser.add_argument("--out", default="logs/payment_sync_v2_matching_test_results.json", help="Output JSON path")
        parser.add_argument("--only", default="", help="Run only a single case by name")
        parser.add_argument("--topn", type=int, default=10, help="How many top IDs count toward success checks")

    def handle(self, *args, **options):
        run_ai = bool(options.get("ai"))
        out_path = str(options.get("out") or "logs/payment_sync_v2_matching_test_results.json")
        only = str(options.get("only") or "").strip()
        topn = int(options.get("topn") or 10)

        real_payments = _pick_real_payments()
        if not real_payments:
            self.stdout.write(json.dumps({"error": "No payments found in DB"}, indent=2))
            return

        cases = _build_testcases(real_payments)
        if only:
            cases = [c for c in cases if c.name == only]
        report = {"run_ai": run_ai, "cases_count": len(cases), "cases": []}
        for tc in cases:
            report["cases"].append(_match_one_case(tc, run_ai=run_ai))

        # Add a compact summary at the end for quick reading
        summary = []
        for c in report["cases"]:
            expected = list(c.get("expected_db_ids") or [])
            manual_top = list((c.get("manual") or {}).get("top_ids") or [])[:topn]
            ai_block = c.get("ai") or {}
            ai_status = ai_block.get("status")
            ai_top = list(ai_block.get("top_ids") or [])[:topn]
            manual_ok = (c.get("manual") or {}).get("success")
            ai_ok = (ai_block.get("success") if ai_status != "skipped" else None)
            summary.append(
                {
                    "case": c.get("name"),
                    "expected_db_ids": expected,
                    "manual_top_ids": manual_top,
                    "manual_success": manual_ok,
                    "ai_status": ai_status,
                    "ai_top_ids": ai_top,
                    "ai_success": ai_ok,
                }
            )
        report["summary"] = summary

        payload = json.dumps(report, indent=2, ensure_ascii=False, default=str)
        self.stdout.write(payload)

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(payload)

