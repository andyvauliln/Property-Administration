from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _django_setup():
    # When running as "python commands/...", ensure project root is on sys.path
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
    import django  # type: ignore

    django.setup()


def _payment_to_dict(p) -> Dict[str, Any]:
    apt = getattr(p, "apartment", None)
    booking = getattr(p, "booking", None)
    booking_apt = getattr(booking, "apartment", None) if booking else None
    tenant = getattr(booking, "tenant", None) if booking else None
    pt = getattr(p, "payment_type", None)
    pm = getattr(p, "payment_method", None)
    bank = getattr(p, "bank", None)

    return {
        "id": p.id,
        "payment_date": _iso(getattr(p, "payment_date", None)),
        "amount": float(getattr(p, "amount", 0) or 0),
        "payment_status": getattr(p, "payment_status", None),
        "invoice_url": getattr(p, "invoice_url", None),
        "notes": getattr(p, "notes", None),
        "tenant_notes": getattr(p, "tenant_notes", None),
        "merged_payment_key": getattr(p, "merged_payment_key", None),
        "payment_type": (
            {
                "id": pt.id,
                "name": getattr(pt, "name", None),
                "type": getattr(pt, "type", None),
                "category": getattr(pt, "category", None),
                "balance_sheet_name": getattr(pt, "balance_sheet_name", None),
            }
            if pt
            else None
        ),
        "payment_method": ({"id": pm.id, "name": getattr(pm, "name", None)} if pm else None),
        "bank": ({"id": bank.id, "name": getattr(bank, "name", None)} if bank else None),
        "apartment": ({"id": apt.id, "name": getattr(apt, "name", None)} if apt else None),
        "booking": (
            {
                "id": booking.id,
                "status": getattr(booking, "status", None),
                "start_date": _iso(getattr(booking, "start_date", None)),
                "end_date": _iso(getattr(booking, "end_date", None)),
            }
            if booking
            else None
        ),
        "booking_apartment": (
            {"id": booking_apt.id, "name": getattr(booking_apt, "name", None)} if booking_apt else None
        ),
        "tenant": (
            {
                "id": tenant.id,
                "full_name": getattr(tenant, "full_name", None),
                "phone": getattr(tenant, "phone", None),
                "email": getattr(tenant, "email", None),
            }
            if tenant
            else None
        ),
    }


def export(output_path: str, limit: Optional[int] = None) -> Dict[str, Any]:
    from django.db.models import F  # type: ignore
    from mysite.models import Payment  # type: ignore

    qs = (
        Payment.objects.filter(apartment__isnull=False, booking__isnull=False)
        .exclude(apartment=F("booking__apartment"))
        .select_related(
            "payment_type",
            "payment_method",
            "bank",
            "apartment",
            "booking__apartment",
            "booking__tenant",
        )
        .order_by("-payment_date", "-id")
    )

    if limit is not None:
        qs = qs[:limit]

    payments = list(qs)
    last_mismatch_payment_date = payments[0].payment_date.isoformat() if payments else None

    payload = {
        "summary": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(payments),
            "last_mismatch_payment_date": last_mismatch_payment_date,
        },
        "payments": [_payment_to_dict(p) for p in payments],
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    output_path = sys.argv[1] if len(sys.argv) > 1 else "logs/payment_apartment_mismatches.json"
    limit = None
    if len(sys.argv) > 2:
        try:
            limit = int(sys.argv[2])
        except Exception:
            limit = None

    _django_setup()
    payload = export(output_path=output_path, limit=limit)

    summary = payload.get("summary") or {}
    print(f"wrote: {output_path}")
    print(f"count: {summary.get('count')}")
    print(f"last_mismatch_payment_date: {summary.get('last_mismatch_payment_date')}")


if __name__ == "__main__":
    main()

