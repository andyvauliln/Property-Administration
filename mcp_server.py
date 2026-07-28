"""
MCP server for Property Management — streamable HTTP at /mcp with OAuth.
Run:  python mcp_server.py
Claude Desktop: add connector URL https://<tunnel>/mcp (OAuth login required).

All write operations are tagged with updated_by="Claude MCP Agent" so every
change made through this server is traceable in the last_updated_by column.
"""

import os
import sys
import json
import decimal
from datetime import date, timedelta
from calendar import monthrange

# Bootstrap Django before any model imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
# FastMCP runs handlers inside asyncio; this tells Django not to block sync ORM
# calls from async contexts. Safe here since all tools are read-only.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import django
django.setup()

from django.db.models import Q

from mysite.models import (
    Apartment, Booking, Payment, Cleaning,
    Parking, ParkingBooking, CalendarNote, ApartmentPrice,
    User,
)

from mcp_oauth import create_mcp_server, get_public_base_url

mcp = create_mcp_server("Property Management", host="0.0.0.0", port=8001)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


def _dumps(obj) -> str:
    return json.dumps(obj, cls=_Encoder, ensure_ascii=False, indent=2)


def _month_range(year: int, month: int):
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return start, end


def _resolve_period(period: str):
    today = date.today()
    if period == "previous":
        first = date(today.year, today.month, 1) - timedelta(days=1)
        return first.year, first.month
    if period == "next":
        last = date(today.year, today.month, monthrange(today.year, today.month)[1])
        nxt = last + timedelta(days=1)
        return nxt.year, nxt.month
    return today.year, today.month


def _booking_to_dict(b: Booking) -> dict:
    tenant = b.tenant
    return {
        "id": b.id,
        "status": b.status,
        "start_date": b.start_date,
        "end_date": b.end_date,
        "nights": (b.end_date - b.start_date).days,
        "tenant": {
            "id": tenant.id if tenant else None,
            "full_name": tenant.full_name if tenant else None,
            "email": tenant.email if tenant else None,
            "phone": tenant.phone if tenant else None,
        },
        "apartment": {
            "id": b.apartment.id if b.apartment else None,
            "name": str(b.apartment) if b.apartment else None,
        },
        "notes": b.notes,
        "other_tenants": b.other_tenants,
        "source": b.source,
        "contract_send_status": b.contract_send_status,
        "contract_url": b.contract_url,
    }


def _payment_to_dict(p: Payment) -> dict:
    apt = p.apartment or (p.booking.apartment if p.booking else None)
    tenant = p.booking.tenant if p.booking else None
    return {
        "id": p.id,
        "payment_date": p.payment_date,
        "amount": p.amount,
        "direction": p.payment_type.type if p.payment_type else None,
        "payment_type_name": p.payment_type.name if p.payment_type else None,
        "payment_type_category": p.payment_type.category if p.payment_type else None,
        "payment_status": p.payment_status,
        "payment_method": str(p.payment_method) if p.payment_method else None,
        "bank": str(p.bank) if p.bank else None,
        "apartment": str(apt) if apt else None,
        "tenant": tenant.full_name if tenant else None,
        "notes": p.notes,
        "tenant_notes": p.tenant_notes,
        "booking_id": p.booking_id,
    }


def _apt_summary(apt: Apartment) -> dict:
    return {
        "id": apt.id,
        "name": apt.name,
        "building_n": apt.building_n,
        "apartment_n": apt.apartment_n,
        "address": f"{apt.building_n} {apt.street}, apt {apt.apartment_n}, {apt.city}, {apt.state}",
        "bedrooms": apt.bedrooms,
        "bathrooms": apt.bathrooms,
        "type": apt.apartment_type,
        "status": apt.status,
        "default_price": apt.default_price,
        "rating": apt.raiting,
    }


def _resolve_apartment(query: str) -> "list[Apartment]":
    """
    Flexible apartment lookup.
    Accepts: apartment name, "building-apt" notation (e.g. "780-110"),
    building number alone, or any substring of the name.
    Returns a list (may be empty or contain multiple candidates).
    """
    query = query.strip()

    # Exact name match
    exact = list(Apartment.objects.filter(name__iexact=query))
    if exact:
        return exact

    # "780-110" → building_n=780, apartment_n=110
    if "-" in query:
        parts = query.split("-", 1)
        building, apt_n = parts[0].strip(), parts[1].strip()
        by_num = list(
            Apartment.objects.filter(
                building_n__icontains=building,
                apartment_n__icontains=apt_n,
            )
        )
        if by_num:
            return by_num

    # Name substring match
    by_name = list(Apartment.objects.filter(name__icontains=query))
    if by_name:
        return by_name

    # Building number match (returns all units in that building)
    by_building = list(Apartment.objects.filter(building_n__icontains=query))
    return by_building


def _ambiguous(matches: "list[Apartment]") -> str:
    return _dumps({
        "error": "Multiple apartments matched. Please be more specific.",
        "matches": [_apt_summary(a) for a in matches],
    })


# ---------------------------------------------------------------------------
# Tool 1 (NEW): find_apartments — search / discover apartments
# ---------------------------------------------------------------------------

@mcp.tool()
def find_apartments(query: str) -> str:
    """
    Search for apartments by name, "building-apt" notation (e.g. "780-110"),
    building number, or any substring of the apartment name.
    Use this to discover apartment IDs before calling other tools.

    Args:
        query: Search string, e.g. "780-110", "630", "Main St"
    """
    matches = _resolve_apartment(query)
    if not matches:
        return _dumps({"error": "No apartments found", "query": query})
    return _dumps({"count": len(matches), "apartments": [_apt_summary(a) for a in matches]})


# ---------------------------------------------------------------------------
# Tool 2 (NEW): get_apartment_status — occupancy / check-in / check-out
# ---------------------------------------------------------------------------

@mcp.tool()
def get_apartment_status(apartment_name: str) -> str:
    """
    Answer questions like:
    - Is 780-110 occupied?
    - Who is staying in 630-213?
    - When does the current guest check out?
    - When is the next check-in?

    Args:
        apartment_name: Name or "building-apt" notation, e.g. "780-110"
    """
    matches = _resolve_apartment(apartment_name)
    if not matches:
        return _dumps({"error": "Apartment not found", "query": apartment_name})
    if len(matches) > 1:
        return _ambiguous(matches)

    apt = matches[0]
    today = date.today()

    active = (
        Booking.objects
        .filter(apartment=apt, start_date__lte=today, end_date__gte=today)
        .exclude(status="Cancelled")
        .select_related("tenant")
        .first()
    )

    next_booking = (
        Booking.objects
        .filter(apartment=apt, start_date__gt=today)
        .exclude(status="Cancelled")
        .select_related("tenant")
        .order_by("start_date")
        .first()
    )

    result = {
        "apartment": _apt_summary(apt),
        "today": today,
        "is_occupied": active is not None,
    }

    if active:
        tenant = active.tenant
        result["current_booking"] = {
            "id": active.id,
            "status": active.status,
            "check_in": active.start_date,
            "check_out": active.end_date,
            "nights_remaining": (active.end_date - today).days,
            "tenant": {
                "full_name": tenant.full_name if tenant else None,
                "phone": tenant.phone if tenant else None,
                "email": tenant.email if tenant else None,
            },
            "other_tenants": active.other_tenants,
            "notes": active.notes,
        }
    else:
        result["current_booking"] = None

    if next_booking:
        result["next_booking"] = {
            "id": next_booking.id,
            "status": next_booking.status,
            "check_in": next_booking.start_date,
            "check_out": next_booking.end_date,
            "days_until_checkin": (next_booking.start_date - today).days,
            "tenant": {
                "full_name": next_booking.tenant.full_name if next_booking.tenant else None,
                "phone": next_booking.tenant.phone if next_booking.tenant else None,
            },
        }
    else:
        result["next_booking"] = None

    return _dumps(result)


# ---------------------------------------------------------------------------
# Tool 3 (NEW): get_apartment_parking — parking assigned to an apartment
# ---------------------------------------------------------------------------

@mcp.tool()
def get_apartment_parking(apartment_name: str) -> str:
    """
    Return current and upcoming parking bookings for a given apartment.
    Answers: "What is the parking for 630-222?"

    Args:
        apartment_name: Name or "building-apt" notation, e.g. "630-222"
    """
    matches = _resolve_apartment(apartment_name)
    if not matches:
        return _dumps({"error": "Apartment not found", "query": apartment_name})
    if len(matches) > 1:
        return _ambiguous(matches)

    apt = matches[0]
    today = date.today()
    future_cutoff = today + timedelta(days=90)

    parking_bookings = (
        ParkingBooking.objects
        .filter(apartment=apt, end_date__gte=today, start_date__lte=future_cutoff)
        .select_related("parking", "booking__tenant")
        .order_by("start_date")
    )

    rows = []
    for pb in parking_bookings:
        tenant = pb.booking.tenant if pb.booking else None
        rows.append({
            "id": pb.id,
            "parking_number": pb.parking.number if pb.parking else None,
            "building": pb.parking.building if pb.parking else None,
            "status": pb.status,
            "start_date": pb.start_date,
            "end_date": pb.end_date,
            "tenant": tenant.full_name if tenant else None,
            "notes": pb.notes,
        })

    return _dumps({
        "apartment": _apt_summary(apt),
        "today": today,
        "parking_bookings": rows,
    })


# ---------------------------------------------------------------------------
# Tool 4 (NEW): get_upcoming_payments — next payments for an apartment
# ---------------------------------------------------------------------------

@mcp.tool()
def get_upcoming_payments(apartment_name: str, days_ahead: int = 60) -> str:
    """
    Return upcoming and recent pending payments for a given apartment.
    Answers: "When is the next payment for 630-210? How much?"

    Args:
        apartment_name: Name or "building-apt" notation, e.g. "630-210"
        days_ahead: How many days forward to look (default 60)
    """
    matches = _resolve_apartment(apartment_name)
    if not matches:
        return _dumps({"error": "Apartment not found", "query": apartment_name})
    if len(matches) > 1:
        return _ambiguous(matches)

    apt = matches[0]
    today = date.today()
    end = today + timedelta(days=days_ahead)

    payments = (
        Payment.objects
        .filter(
            Q(booking__apartment=apt) | Q(apartment=apt),
            payment_date__range=(today, end),
        )
        .filter(Q(booking__isnull=True) | ~Q(booking__status="Cancelled"))
        .select_related("payment_type", "payment_method", "booking__tenant")
        .order_by("payment_date")
    )

    # Also fetch the most recent completed/pending payments (last 30 days) for context
    recent = (
        Payment.objects
        .filter(
            Q(booking__apartment=apt) | Q(apartment=apt),
            payment_date__range=(today - timedelta(days=30), today - timedelta(days=1)),
        )
        .filter(Q(booking__isnull=True) | ~Q(booking__status="Cancelled"))
        .select_related("payment_type", "payment_method", "booking__tenant")
        .order_by("-payment_date")[:5]
    )

    return _dumps({
        "apartment": _apt_summary(apt),
        "today": today,
        "upcoming_payments": [_payment_to_dict(p) for p in payments],
        "recent_payments_last_30_days": [_payment_to_dict(p) for p in recent],
    })


# ---------------------------------------------------------------------------
# Tool 5 (NEW): search_tenants — find tenants by name, phone, or email
# ---------------------------------------------------------------------------

@mcp.tool()
def search_tenants(query: str) -> str:
    """
    Search for tenants by full name, email, or phone number.
    Returns matching tenants and their booking history.

    Args:
        query: Tenant name, email address, or phone number (partial match works)
    """
    users = (
        User.objects
        .filter(role="Tenant")
        .filter(
            Q(full_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )
        .prefetch_related("bookings__apartment")
        [:20]
    )

    results = []
    for u in users:
        bookings = list(u.bookings.exclude(status="Cancelled").order_by("-start_date")[:10])
        results.append({
            "id": u.id,
            "full_name": u.full_name,
            "email": u.email,
            "phone": u.phone,
            "telegram_chat_id": u.telegram_chat_id,
            "notes": u.notes,
            "booking_count": len(bookings),
            "bookings": [
                {
                    "id": b.id,
                    "apartment": str(b.apartment) if b.apartment else None,
                    "start_date": b.start_date,
                    "end_date": b.end_date,
                    "status": b.status,
                    "source": b.source,
                }
                for b in bookings
            ],
        })

    return _dumps({"query": query, "count": len(results), "tenants": results})


# ---------------------------------------------------------------------------
# Tool 6 (NEW): get_availability — find free apartments from a date
# ---------------------------------------------------------------------------

@mcp.tool()
def get_availability(
    start_date: str,
    end_date: str = "",
    bedrooms: int = 0,
    apartment_type: str = "",
) -> str:
    """
    Find apartments that are available for a given date range.
    Answers: "Any one-bedroom apartments available starting June 15?"

    Args:
        start_date: ISO date string, e.g. "2026-06-15"
        end_date: ISO date string (optional — defaults to start_date + 1 day for single-night check)
        bedrooms: Filter by number of bedrooms (0 = any)
        apartment_type: "In Management" or "In Ownership" (empty = any)
    """
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        return _dumps({"error": f"Invalid start_date format: {start_date}. Use YYYY-MM-DD."})

    end = date.fromisoformat(end_date) if end_date else start + timedelta(days=1)

    qs = Apartment.objects.exclude(status="Unavailable")
    if bedrooms:
        qs = qs.filter(bedrooms=bedrooms)
    if apartment_type:
        qs = qs.filter(apartment_type__icontains=apartment_type)

    overlapping_apt_ids = set(
        Booking.objects
        .filter(start_date__lte=end, end_date__gte=start)
        .exclude(status="Cancelled")
        .values_list("apartment_id", flat=True)
    )

    available = [apt for apt in qs if apt.id not in overlapping_apt_ids]

    return _dumps({
        "query": {
            "start_date": start,
            "end_date": end,
            "bedrooms_filter": bedrooms or "any",
            "type_filter": apartment_type or "any",
        },
        "available_count": len(available),
        "apartments": [_apt_summary(a) for a in available],
    })


# ---------------------------------------------------------------------------
# Tool 7 (NEW): get_occupancy_stats — occupancy rate by month / bedroom count
# ---------------------------------------------------------------------------

@mcp.tool()
def get_occupancy_stats(
    year: int = 0,
    month: int = 0,
    bedrooms: int = 0,
) -> str:
    """
    Return occupancy rates, booked vs available days, and revenue for a month.
    Answers: "What is the occupancy rate for May?" or "How occupied are 1-bedrooms?"

    Args:
        year: Year (defaults to current year)
        month: Month 1–12 (defaults to current month)
        bedrooms: Filter by bedroom count (0 = all sizes, returns breakdown by size)
    """
    today = date.today()
    year = year or today.year
    month = month or today.month
    start, end = _month_range(year, month)
    _, days_in_month = monthrange(year, month)

    qs = Apartment.objects.exclude(status="Unavailable")
    if bedrooms:
        qs = qs.filter(bedrooms=bedrooms)

    apartments = list(qs)
    apt_ids = [a.id for a in apartments]

    bookings = (
        Booking.objects
        .filter(apartment_id__in=apt_ids, start_date__lte=end, end_date__gte=start)
        .exclude(status="Cancelled")
    )

    # Map apartment_id → set of occupied days in the month
    occupied_days: dict[int, set] = {a.id: set() for a in apartments}
    for b in bookings:
        b_start = max(b.start_date, start)
        b_end = min(b.end_date, end)
        cur = b_start
        while cur <= b_end:
            occupied_days[b.apartment_id].add(cur)
            cur += timedelta(days=1)

    # Breakdown by bedroom count
    breakdown: dict[int, dict] = {}
    for apt in apartments:
        br = apt.bedrooms
        if br not in breakdown:
            breakdown[br] = {"apartments": 0, "total_days": 0, "booked_days": 0}
        booked = len(occupied_days[apt.id])
        breakdown[br]["apartments"] += 1
        breakdown[br]["total_days"] += days_in_month
        breakdown[br]["booked_days"] += booked

    br_stats = []
    for br, d in sorted(breakdown.items()):
        occ = round(d["booked_days"] / d["total_days"] * 100, 1) if d["total_days"] else 0
        br_stats.append({
            "bedrooms": br,
            "apartments": d["apartments"],
            "total_days": d["total_days"],
            "booked_days": d["booked_days"],
            "available_days": d["total_days"] - d["booked_days"],
            "occupancy_pct": occ,
        })

    total_days = sum(d["total_days"] for d in breakdown.values())
    total_booked = sum(d["booked_days"] for d in breakdown.values())
    overall_occ = round(total_booked / total_days * 100, 1) if total_days else 0

    return _dumps({
        "period": {"year": year, "month": month, "start": start, "end": end},
        "total_apartments": len(apartments),
        "overall_occupancy_pct": overall_occ,
        "total_booked_days": total_booked,
        "total_available_days": total_days - total_booked,
        "by_bedroom_count": br_stats,
    })


# ---------------------------------------------------------------------------
# Tool 8: get_apartment_calendar
# ---------------------------------------------------------------------------

@mcp.tool()
def get_apartment_calendar(
    apartment_id: int,
    year: int = 0,
    month: int = 0,
) -> str:
    """
    Return all bookings, cleanings, and payments for one apartment by ID.
    Use find_apartments() first to get the apartment ID from a name.

    Args:
        apartment_id: Apartment primary key
        year: Calendar year (defaults to current year)
        month: Month 1–12. 0 means full year.
    """
    today = date.today()
    year = year or today.year
    start = date(year, month, 1) if month else date(year, 1, 1)
    end = date(year, month, monthrange(year, month)[1]) if month else date(year, 12, 31)

    try:
        apt = Apartment.objects.get(id=apartment_id)
    except Apartment.DoesNotExist:
        return _dumps({"error": f"Apartment {apartment_id} not found"})

    bookings = (
        Booking.objects
        .filter(apartment=apt, start_date__lte=end, end_date__gte=start)
        .exclude(status="Cancelled")
        .select_related("tenant")
    )

    cleanings = (
        Cleaning.objects
        .filter(date__range=(start, end), booking__apartment=apt)
        .exclude(booking__status="Cancelled")
        .select_related("cleaner", "booking")
    )

    payments = (
        Payment.objects
        .filter(
            Q(booking__apartment=apt) | Q(apartment=apt),
            payment_date__range=(start, end),
        )
        .filter(Q(booking__isnull=True) | ~Q(booking__status="Cancelled"))
        .select_related("payment_type", "payment_method", "bank", "booking__tenant")
    )

    current_price = (
        ApartmentPrice.objects
        .filter(apartment=apt, effective_date__lte=today)
        .order_by("-effective_date")
        .values_list("price", flat=True)
        .first()
    ) or apt.default_price

    return _dumps({
        "apartment": {**_apt_summary(apt), "current_price": current_price},
        "period": {"year": year, "month": month or "full-year", "start": start, "end": end},
        "bookings": [_booking_to_dict(b) for b in bookings],
        "cleanings": [
            {
                "id": c.id,
                "date": c.date,
                "status": c.status,
                "cleaner": c.cleaner.full_name if c.cleaner else None,
                "booking_id": c.booking_id,
                "tasks": c.tasks,
                "notes": c.notes,
            }
            for c in cleanings
        ],
        "payments": [_payment_to_dict(p) for p in payments],
    })


# ---------------------------------------------------------------------------
# Tool 10: get_payment_report (existing, unchanged)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_payment_report(period: str = "current") -> str:
    """
    Return all payments for a month with income/expense/profit summary.

    Args:
        period: "current", "previous", or "next" (relative to today)
    """
    year, month = _resolve_period(period)
    start, end = _month_range(year, month)

    payments = (
        Payment.objects
        .filter(payment_date__range=(start, end))
        .select_related(
            "payment_type", "payment_method", "bank",
            "booking__apartment", "booking__tenant", "apartment",
        )
        .order_by("payment_date")
    )

    rows = [_payment_to_dict(p) for p in payments]

    income = sum(float(p["amount"]) for p in rows if p["direction"] == "In" and p["payment_status"] == "Expected")
    expense = sum(float(p["amount"]) for p in rows if p["direction"] == "Out" and p["payment_status"] == "Expected")
    pending_in = sum(float(p["amount"]) for p in rows if p["direction"] == "In" and p["payment_status"] == "Pending")
    pending_out = sum(float(p["amount"]) for p in rows if p["direction"] == "Out" and p["payment_status"] == "Pending")

    return _dumps({
        "period": {"year": year, "month": month, "start": start, "end": end},
        "summary": {
            "total_income": income,
            "total_expense": expense,
            "net_profit": income - expense,
            "pending_income": pending_in,
            "pending_expense": pending_out,
            "total_payments": len(rows),
        },
        "payments": rows,
    })


# ---------------------------------------------------------------------------
# Tool 11: get_booking_availability (existing, unchanged)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_booking_availability(year: int = 0, month: int = 0) -> str:
    """
    Return a 3-month availability overview for all active apartments.

    Args:
        year: Start year (defaults to current)
        month: Start month 1–12 (defaults to current). Shows month + next 2 months.
    """
    today = date.today()
    year = year or today.year
    month = month or today.month

    start = date(year, month, 1)
    m3_year = year + (month + 2 - 1) // 12
    m3_month = (month + 2 - 1) % 12 + 1
    end = date(m3_year, m3_month, monthrange(m3_year, m3_month)[1])

    apartments = (
        Apartment.objects
        .exclude(status="Unavailable")
        .order_by("name")
    )

    bookings = (
        Booking.objects
        .filter(start_date__lte=end, end_date__gte=start)
        .exclude(status="Cancelled")
        .select_related("tenant", "apartment")
    )
    bookings_by_apt: dict[int, list] = {}
    for b in bookings:
        if b.apartment_id:
            bookings_by_apt.setdefault(b.apartment_id, []).append(b)

    calendar_notes = CalendarNote.objects.filter(start_date__lte=end, end_date__gte=start)

    result_apartments = []
    for apt in apartments:
        apt_bookings = bookings_by_apt.get(apt.id, [])

        current_price = (
            ApartmentPrice.objects
            .filter(apartment=apt, effective_date__lte=today)
            .order_by("-effective_date")
            .values_list("price", flat=True)
            .first()
        ) or apt.default_price

        months_data = []
        cur = start
        while cur <= end:
            _, days_in_month = monthrange(cur.year, cur.month)

            day_statuses = []
            revenue = 0.0
            for day_n in range(1, days_in_month + 1):
                d = date(cur.year, cur.month, day_n)
                day_bookings = [b for b in apt_bookings if b.start_date <= d <= b.end_date]
                if day_bookings:
                    b = day_bookings[0]
                    day_statuses.append({
                        "date": d,
                        "status": b.status,
                        "booking_id": b.id,
                        "tenant": b.tenant.full_name if b.tenant else None,
                    })
                    if b.status == "Confirmed":
                        revenue += float(current_price or 0)
                else:
                    day_statuses.append({"date": d, "status": "Available"})

            max_revenue = days_in_month * float(current_price or 0)
            booked_days = sum(1 for d in day_statuses if d["status"] != "Available")
            occupancy = round(booked_days / days_in_month * 100, 1) if days_in_month else 0

            months_data.append({
                "year": cur.year,
                "month": cur.month,
                "days": day_statuses,
                "revenue": revenue,
                "max_revenue": max_revenue,
                "occupancy_pct": occupancy,
                "booked_days": booked_days,
                "available_days": days_in_month - booked_days,
            })

            cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)

        notes = [
            {"note": cn.note, "start": cn.start_date, "end": cn.end_date}
            for cn in calendar_notes
            if cn.apartment_id is None or cn.apartment_id == apt.id
        ]

        result_apartments.append({
            "id": apt.id,
            "name": apt.name,
            "type": apt.apartment_type,
            "bedrooms": apt.bedrooms,
            "rating": apt.raiting,
            "current_price": current_price,
            "months": months_data,
            "calendar_notes": notes,
        })

    return _dumps({"period": {"start": start, "end": end}, "apartments": result_apartments})


# ---------------------------------------------------------------------------
# Tool 12: get_parking_calendar (existing, unchanged)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_parking_calendar(year: int = 0, month: int = 0) -> str:
    """
    Return a parking availability calendar for all parking spots (3-month window).

    Args:
        year: Year (defaults to current)
        month: Month 1–12 (defaults to current). Shows month + next 2 months.
    """
    today = date.today()
    year = year or today.year
    month = month or today.month

    start = date(year, month, 1)
    m3_year = year + (month + 2 - 1) // 12
    m3_month = (month + 2 - 1) % 12 + 1
    end = date(m3_year, m3_month, monthrange(m3_year, m3_month)[1])

    parking_spots = Parking.objects.all().order_by("building", "number")

    parking_bookings = (
        ParkingBooking.objects
        .filter(start_date__lte=end, end_date__gte=start)
        .select_related("parking", "apartment", "booking__tenant")
    )
    bookings_by_parking: dict[int, list] = {}
    for pb in parking_bookings:
        if pb.parking_id:
            bookings_by_parking.setdefault(pb.parking_id, []).append(pb)

    result_spots = []
    for spot in parking_spots:
        spot_bookings = bookings_by_parking.get(spot.id, [])

        months_data = []
        cur = start
        while cur <= end:
            _, days_in_month = monthrange(cur.year, cur.month)
            booked_days = 0
            day_statuses = []

            for day_n in range(1, days_in_month + 1):
                d = date(cur.year, cur.month, day_n)
                day_pbs = [
                    pb for pb in spot_bookings
                    if pb.start_date and pb.end_date and pb.start_date <= d <= pb.end_date
                ]
                if day_pbs:
                    pb = day_pbs[0]
                    booked_days += 1
                    tenant = pb.booking.tenant.full_name if pb.booking and pb.booking.tenant else None
                    day_statuses.append({
                        "date": d,
                        "status": pb.status,
                        "tenant": tenant,
                        "apartment": str(pb.apartment) if pb.apartment else None,
                        "booking_id": pb.booking_id,
                        "parking_booking_id": pb.id,
                        "notes": pb.notes,
                    })
                else:
                    day_statuses.append({"date": d, "status": "Available"})

            occupancy = round(booked_days / days_in_month * 100, 1) if days_in_month else 0
            months_data.append({
                "year": cur.year,
                "month": cur.month,
                "days": day_statuses,
                "booked_days": booked_days,
                "occupancy_pct": occupancy,
            })

            cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)

        result_spots.append({
            "id": spot.id,
            "number": spot.number,
            "building": spot.building,
            "associated_room": spot.associated_room,
            "notes": spot.notes,
            "months": months_data,
        })

    return _dumps({"period": {"start": start, "end": end}, "parking_spots": result_spots})


# ---------------------------------------------------------------------------
# Tool 13: get_booking_details (existing, unchanged)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_booking_details(booking_id: int) -> str:
    """
    Return full details for one booking — tenant, apartment, payments, cleanings, parking.

    Args:
        booking_id: Booking primary key
    """
    try:
        b = (
            Booking.objects
            .select_related("tenant", "apartment__owner")
            .prefetch_related(
                "payments__payment_type",
                "payments__payment_method",
                "payments__bank",
                "cleanings__cleaner",
                "parking_bookings__parking",
                "parking_bookings__apartment",
            )
            .get(id=booking_id)
        )
    except Booking.DoesNotExist:
        return _dumps({"error": f"Booking {booking_id} not found"})

    apt = b.apartment
    tenant = b.tenant
    payments = [_payment_to_dict(p) for p in b.payments.all()]
    total_paid = sum(float(p["amount"]) for p in payments if p["direction"] == "In" and p["payment_status"] == "Expected")

    return _dumps({
        "id": b.id,
        "status": b.status,
        "start_date": b.start_date,
        "end_date": b.end_date,
        "nights": (b.end_date - b.start_date).days,
        "source": b.source,
        "contract_send_status": b.contract_send_status,
        "contract_url": b.contract_url,
        "notes": b.notes,
        "other_tenants": b.other_tenants,
        "tenants_n": b.tenants_n,
        "animals": b.animals,
        "visit_purpose": b.visit_purpose,
        "tenant": {
            "id": tenant.id if tenant else None,
            "full_name": tenant.full_name if tenant else None,
            "email": tenant.email if tenant else None,
            "phone": tenant.phone if tenant else None,
        },
        "apartment": {
            "id": apt.id if apt else None,
            "name": str(apt) if apt else None,
            "address": (
                f"{apt.building_n} {apt.street}, apt {apt.apartment_n}, {apt.city}, {apt.state}"
                if apt else None
            ),
            "bedrooms": apt.bedrooms if apt else None,
            "bathrooms": apt.bathrooms if apt else None,
            "type": apt.apartment_type if apt else None,
            "default_price": apt.default_price if apt else None,
        },
        "payments": payments,
        "payment_summary": {"total_paid": total_paid, "total_payments": len(payments)},
        "cleanings": [
            {
                "id": c.id,
                "date": c.date,
                "status": c.status,
                "cleaner": c.cleaner.full_name if c.cleaner else None,
                "tasks": c.tasks,
                "notes": c.notes,
            }
            for c in b.cleanings.all()
        ],
        "parking_bookings": [
            {
                "id": pb.id,
                "status": pb.status,
                "start_date": pb.start_date,
                "end_date": pb.end_date,
                "parking_number": pb.parking.number if pb.parking else None,
                "building": pb.parking.building if pb.parking else None,
                "notes": pb.notes,
            }
            for pb in b.parking_bookings.all()
        ],
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    public = get_public_base_url()
    print("Starting Property Management MCP server on http://0.0.0.0:8001")
    print(f"MCP endpoint (public): {public}/mcp")
    print(f"OAuth login: {public}/login")
    mcp.run(transport="streamable-http")
