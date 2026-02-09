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


def build_cleaning_message(notification, prefix):
    message = f"{prefix}: {notification.notification_message}"
    if notification.cleaning:
        message += "\nCleaning Details:"
        message += f"\n- Date: {notification.cleaning.date}"
        if notification.cleaning.booking and notification.cleaning.booking.apartment:
            message += f"\n- Apartment: {notification.cleaning.booking.apartment.name}"
        elif getattr(notification.cleaning, 'apartment', None):
            message += f"\n- Apartment: {notification.cleaning.apartment.name}"
        if getattr(notification.cleaning, 'status', None):
            message += f"\n- Status: {notification.cleaning.status}"
        if getattr(notification.cleaning, 'cleaner', None):
            message += f"\n- Cleaner: {notification.cleaning.cleaner.full_name}"
        if getattr(notification.cleaning, 'tasks', None) and notification.cleaning.tasks:
            message += f"\n- Tasks: {notification.cleaning.tasks}"
        if getattr(notification.cleaning, 'notes', None) and notification.cleaning.notes:
            message += f"\n- Notes: {notification.cleaning.notes}"
        message += "\nForm Link: https://form.jotform.com/250414400218038"
    return message


def my_cron_job(dry_run=False, stdout=None):
    today = date.today()
    next_day = today + timedelta(days=1)
    chat_id = os.environ.get("TELEGRAM_GROUP_CLEANING")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        if not dry_run:
            return
    notifications = Notification.objects.filter(
        date__in=[today, next_day],
        send_in_telegram=True,
        cleaning__isnull=False,
    ).exclude(cleaning__booking__status='Blocked')

    sent = 0
    for notification in notifications:
        prefix = "TODAY" if notification.date == today else "TOMORROW"
        message = build_cleaning_message(notification, prefix)
        if notification.date == next_day and "Form Link:" in message:
            message = message.replace("https://form.jotform.com/250414400218038", "https://ro.am/join/fyy4jxbm-yr9qc7mo")
        send_telegram_message(normalize_group_chat_id(chat_id), token, message, dry_run=dry_run, stdout=stdout)
        sent += 1
    if sent == 0:
        send_telegram_message(normalize_group_chat_id(chat_id), token, "No cleaning notifications for today/tomorrow.", dry_run=dry_run, stdout=stdout)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily cleaning notifications to Cleaning Telegram group'

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print messages only, do not send")

    def execute_command(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write("DRY RUN - messages that would be sent to Cleaning group:\n")
        else:
            self.stdout.write('Running telegram group cleaning...')
        my_cron_job(dry_run=dry_run, stdout=self.stdout)
        self.stdout.write('Telegram group cleaning completed' if not dry_run else 'Dry run done.')
