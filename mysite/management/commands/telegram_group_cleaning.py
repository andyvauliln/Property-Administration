import requests
from datetime import timedelta, date
from django.db.models import Q
from mysite.models import Cleaning, format_date
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


def build_cleaning_message(cleaning, prefix):
    date_str = format_date(cleaning.date)
    cleaner_name = cleaning.cleaner.full_name if cleaning.cleaner else "No cleaner assigned"
    apt_name = ""
    if cleaning.booking and cleaning.booking.apartment:
        apt_name = cleaning.booking.apartment.name
    elif cleaning.apartment:
        apt_name = cleaning.apartment.name
    header = f"Cleaning: {date_str} by {cleaner_name} {apt_name} [{cleaning.status}]"
    message = f"{prefix}: {header}\nCleaning Details:"
    message += f"\n- Date: {cleaning.date}"
    message += f"\n- Apartment: {apt_name or 'N/A'}"
    message += f"\n- Status: {cleaning.status}"
    message += f"\n- Cleaner: {cleaner_name}"
    if cleaning.tasks:
        message += f"\n- Tasks: {cleaning.tasks}"
    if cleaning.notes:
        message += f"\n- Notes: {cleaning.notes}"
    form_url = "https://ro.am/join/fyy4jxbm-yr9qc7mo" if prefix == "TOMORROW" else "https://form.jotform.com/250414400218038"
    message += f"\nForm Link: {form_url}"
    return message


def my_cron_job(dry_run=False, stdout=None):
    today = date.today()
    next_day = today + timedelta(days=1)
    chat_id = os.environ.get("TELEGRAM_GROUP_CLEANING")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        if not dry_run:
            return
    cleanings = Cleaning.objects.filter(
        date__in=[today, next_day],
    ).filter(
        Q(booking__isnull=True) | ~Q(booking__status='Blocked'),
    ).select_related(
        'cleaner', 'booking', 'booking__apartment', 'apartment'
    ).order_by('date', 'id')

    sent = 0
    for cleaning in cleanings:
        prefix = "TODAY" if cleaning.date == today else "TOMORROW"
        message = build_cleaning_message(cleaning, prefix)
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
