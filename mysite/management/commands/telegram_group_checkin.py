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


def my_cron_job(dry_run=False, stdout=None):
    next_day = date.today() + timedelta(days=1)
    chat_id = os.environ.get("TELEGRAM_GROUP_CHECKIN")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        if not dry_run:
            return
    bookings = Booking.objects.filter(
        start_date=next_day,
    ).exclude(status__in=['Blocked', 'Cancelled']).select_related('apartment', 'tenant').order_by('id')

    sent = 0
    for booking in bookings:
        start_str = format_date(booking.start_date)
        end_str = format_date(booking.end_date)
        apt_name = booking.apartment.name if booking.apartment else 'Unknown'
        tenant_name = booking.tenant.full_name if booking.tenant else 'Unknown'
        header = f"Start Booking: {start_str} - {end_str}, {apt_name}, {tenant_name}."
        message = f"CHECKIN TOMORROW: {header}\nBooking Details:"
        message += f"\n- Start Date: {booking.start_date}"
        message += f"\n- End Date: {booking.end_date}"
        message += f"\n- Apartment: {apt_name}"
        message += f"\n- Tenant: {tenant_name}"
        send_telegram_message(normalize_group_chat_id(chat_id), token, message, dry_run=dry_run, stdout=stdout)
        sent += 1
    if sent == 0:
        send_telegram_message(normalize_group_chat_id(chat_id), token, "No checkin notifications for tomorrow.", dry_run=dry_run, stdout=stdout)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily checkin notifications to Checkin Telegram group'

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print messages only, do not send")

    def execute_command(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write("DRY RUN - messages that would be sent to Checkin group:\n")
        else:
            self.stdout.write('Running telegram group checkin...')
        my_cron_job(dry_run=dry_run, stdout=self.stdout)
        self.stdout.write('Telegram group checkin completed' if not dry_run else 'Dry run done.')
