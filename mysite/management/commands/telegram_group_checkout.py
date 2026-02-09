import requests
from datetime import timedelta, date
from mysite.models import Notification
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling


def normalize_group_chat_id(chat_id):
    s = (chat_id or "").strip()
    if not s or not s.lstrip("-").isdigit():
        return s
    if s.startswith("-100"):
        return s
    if s.startswith("-"):
        return "-100" + s[1:]
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
    chat_id = os.environ.get("TELEGRAM_GROUP_CHECKOUT")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        if not dry_run:
            return
    notifications = Notification.objects.filter(
        date=next_day,
        send_in_telegram=True,
        booking__isnull=False,
        message="End Booking",
    ).exclude(booking__status='Blocked')

    sent = 0
    for notification in notifications:
        message = f"CHECKOUT TOMORROW: {notification.notification_message}"
        if notification.booking:
            message += "\nBooking Details:"
            message += f"\n- Start Date: {notification.booking.start_date}"
            message += f"\n- End Date: {notification.booking.end_date}"
            if notification.booking.apartment:
                message += f"\n- Apartment: {notification.booking.apartment.name}"
            if notification.booking.tenant:
                message += f"\n- Tenant: {notification.booking.tenant.full_name}"
        send_telegram_message(normalize_group_chat_id(chat_id), token, message, dry_run=dry_run, stdout=stdout)
        sent += 1
    if sent == 0:
        send_telegram_message(normalize_group_chat_id(chat_id), token, "No checkout notifications for tomorrow.", dry_run=dry_run, stdout=stdout)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily checkout notifications to Checkout Telegram group'

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print messages only, do not send")

    def execute_command(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write("DRY RUN - messages that would be sent to Checkout group:\n")
        else:
            self.stdout.write('Running telegram group checkout...')
        my_cron_job(dry_run=dry_run, stdout=self.stdout)
        self.stdout.write('Telegram group checkout completed' if not dry_run else 'Dry run done.')
