"""
Report: bookings whose end_date was decreased (from AuditLog), last calendar month,
and what happened to linked cleanings (audit trail + current DB).

Note: Booking end-date sync uses audit_queryset_update on Cleaning (mysite.audit_bulk).
"""
import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from mysite.models import AuditLog, Booking, Cleaning


def _parse_date(val):
    if val is None:
        return None
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    return None


def _booking_id_from_cleaning_values(values):
    if not values:
        return None
    b = values.get("booking")
    if isinstance(b, dict):
        return b.get("id")
    return None


def _prev_calendar_month_bounds(now=None):
    now = now or timezone.localtime()
    y, m = now.year, now.month
    if m == 1:
        y, m = y - 1, 12
    else:
        m -= 1
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime(y, m, 1, 0, 0, 0, 0), tz)
    last_d = calendar.monthrange(y, m)[1]
    end = timezone.make_aware(datetime(y, m, last_d, 23, 59, 59, 999999), tz)
    return start, end, y, m


class Command(BaseCommand):
    help = "Report bookings with decreased end_date last month and cleaning outcomes"

    def handle(self, *args, **options):
        start, end, y, m = _prev_calendar_month_bounds()
        self.stdout.write(
            f"Period: {start.date()} .. {end.date()} (calendar month {y}-{m:02d})\n"
        )
        self.stdout.write(
            "Booking end_date decreases from AuditLog; cleaning via AuditLog + live DB.\n"
            "Cleaning date moves from booking end_date sync are logged via audit_queryset_update.\n"
            + ("=" * 80)
            + "\n"
        )

        logs = (
            AuditLog.objects.filter(
                model_name="Booking",
                action="update",
                timestamp__gte=start,
                timestamp__lte=end,
            )
            .order_by("timestamp", "id")
        )

        hits = []
        for log in logs:
            cf = log.changed_fields or []
            if "end_date" not in cf:
                continue
            ov = log.old_values or {}
            nv = log.new_values or {}
            old_e = _parse_date(ov.get("end_date"))
            new_e = _parse_date(nv.get("end_date"))
            if old_e is None or new_e is None:
                continue
            if new_e >= old_e:
                continue
            hits.append(
                {
                    "log": log,
                    "booking_id": int(log.object_id),
                    "old_end": old_e,
                    "new_end": new_e,
                }
            )

        if not hits:
            self.stdout.write("No booking updates with decreased end_date in this period.\n")
            return

        self.stdout.write(f"Found {len(hits)} booking audit event(s) with end_date decreased.\n\n")

        for i, h in enumerate(hits, 1):
            log = h["log"]
            bid = h["booking_id"]
            self.stdout.write(
                f"--- [{i}] Booking id={bid} at {log.timestamp.isoformat()} ---\n"
                f"    end_date: {h['old_end']} -> {h['new_end']} (shortened by {(h['old_end'] - h['new_end']).days} days)\n"
                f"    changed_by: {log.changed_by}\n"
            )

            try:
                b = Booking.objects.select_related("apartment", "tenant").get(pk=bid)
                apt = b.apartment.name if b.apartment else "?"
                ten = b.tenant.full_name if b.tenant else "?"
                self.stdout.write(
                    f"    current booking: apartment={apt!r} tenant={ten!r} status={b.status!r} end_date={b.end_date}\n"
                )
            except Booking.DoesNotExist:
                self.stdout.write("    current booking: NOT IN DB (deleted / missing)\n")
                b = None

            change_ts = log.timestamp
            cleaning_ids = set(
                Cleaning.objects.filter(booking_id=bid).values_list("id", flat=True)
            )

            for dlog in AuditLog.objects.filter(
                model_name="Cleaning",
                action="delete",
                timestamp__gte=change_ts,
            ).order_by("timestamp", "id"):
                ov = dlog.old_values or {}
                if _booking_id_from_cleaning_values(ov) == bid:
                    cleaning_ids.add(int(dlog.object_id))

            for ulog in AuditLog.objects.filter(
                model_name="Cleaning",
                action="update",
                timestamp__gte=change_ts,
            ).order_by("timestamp", "id"):
                ov = ulog.old_values or {}
                nv = ulog.new_values or {}
                if _booking_id_from_cleaning_values(ov) == bid or _booking_id_from_cleaning_values(
                    nv
                ) == bid:
                    cleaning_ids.add(int(ulog.object_id))

            if not cleaning_ids:
                self.stdout.write(
                    "    cleanings: none linked now and none found in audit (delete/update with booking FK) after event.\n"
                )
            else:
                self.stdout.write(
                    f"    cleaning row id(s) tied to this booking (now + audit): {sorted(cleaning_ids)}\n"
                )

            for cid in sorted(cleaning_ids):
                exists = Cleaning.objects.filter(pk=cid).first()
                self.stdout.write(f"    Cleaning #{cid}:\n")
                if exists:
                    self.stdout.write(
                        f"      DB: date={exists.date} booking_id={exists.booking_id} status={exists.status!r}\n"
                        f"      vs expected date after shorten: {h['new_end']} -> "
                        f"{'OK' if exists.date == h['new_end'] else 'MISMATCH'}\n"
                    )
                else:
                    self.stdout.write("      DB: row does not exist (deleted)\n")

                evs = AuditLog.objects.filter(
                    model_name="Cleaning",
                    object_id=str(cid),
                    timestamp__gte=change_ts,
                ).order_by("timestamp", "id")
                rows = list(evs)
                if not rows:
                    self.stdout.write(
                        "      AuditLog after booking change: (no rows)\n"
                    )
                else:
                    self.stdout.write(
                        f"      AuditLog after booking change ({len(rows)} row(s)):\n"
                    )
                    for ev in rows:
                        self.stdout.write(
                            f"        {ev.timestamp.isoformat()} {ev.action} by {ev.changed_by}\n"
                        )
                        if ev.action == "delete":
                            self.stdout.write(f"          old_values keys: {list((ev.old_values or {}).keys())}\n")
                        elif ev.action == "update":
                            self.stdout.write(
                                f"          changed_fields: {ev.changed_fields}\n"
                            )
                            if ev.old_values and ev.new_values:
                                if "date" in (ev.changed_fields or []):
                                    self.stdout.write(
                                        f"          date: {ev.old_values.get('date')} -> {ev.new_values.get('date')}\n"
                                    )
                                if "booking" in (ev.changed_fields or []):
                                    self.stdout.write(
                                        f"          booking: {ev.old_values.get('booking')} -> {ev.new_values.get('booking')}\n"
                                    )
                        elif ev.action == "create":
                            self.stdout.write(
                                f"          new_values keys: {list((ev.new_values or {}).keys())}\n"
                            )
            self.stdout.write("")

        self.stdout.write("Done.\n")
