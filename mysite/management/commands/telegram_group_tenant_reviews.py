import requests
from datetime import timedelta, date
from mysite.models import Booking, format_date
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling


def normalize_group_chat_id(chat_id):
    s = (chat_id or "").strip()
    if not s or not s.lstrip("-").isdigit():
        return s
    return s


def send_telegram_message(chat_id, token, message, dry_run=False, stdout=None):
    if dry_run and stdout:
        stdout.write(message)
        stdout.write("\n" + "-" * 40 + "\n")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.get(url, params={"chat_id": chat_id, "text": message})


def build_tenant_review_message(booking):
    start_str = format_date(booking.start_date)
    end_str = format_date(booking.end_date)
    apt_name = booking.apartment.name if booking.apartment else "N/A"
    tenant_name = booking.tenant.full_name if booking.tenant else "N/A"
    dates_line = f"{start_str} - {end_str}"
    lines = [
        "Google Review needed from",
        tenant_name,
        apt_name,
        dates_line,
    ]
    return "\n".join(lines)


def my_cron_job(dry_run=False, stdout=None):
    today = date.today()
    review_day_end_date = today - timedelta(days=2)
    chat_id = os.environ.get("TELEGRAM_GROUP_TENANT_REVIEWS")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        if not dry_run:
            return
    bookings = Booking.objects.filter(
        end_date=review_day_end_date,
    ).exclude(status="Blocked").select_related("apartment", "tenant").order_by("id")

    sent = 0
    for booking in bookings:
        message = build_tenant_review_message(booking)
        send_telegram_message(
            normalize_group_chat_id(chat_id),
            token,
            message,
            dry_run=dry_run,
            stdout=stdout,
        )
        sent += 1
    if sent == 0:
        send_telegram_message(
            normalize_group_chat_id(chat_id),
            token,
            "No Google Review reminders for today (no bookings ended 2 days ago).",
            dry_run=dry_run,
            stdout=stdout,
        )


class Command(BaseCommandWithErrorHandling):
    help = "Daily reminders (2 days after booking end) to PM Tenant Reviews Telegram group"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print messages only, do not send",
        )

    def execute_command(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write(
                "DRY RUN - messages that would be sent to Tenant Reviews group:\n"
            )
        else:
            self.stdout.write("Running telegram group tenant reviews...")
        my_cron_job(dry_run=dry_run, stdout=self.stdout)
        self.stdout.write(
            "Telegram group tenant reviews completed"
            if not dry_run
            else "Dry run done."
        )
