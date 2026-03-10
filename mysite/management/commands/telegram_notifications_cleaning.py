import requests
from datetime import timedelta, date
from mysite.models import Cleaning
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling
from django.db.models import Q


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def _build_cleaning_message(cleaning, prefix):
    message = f"{prefix}: Cleaning: {cleaning.date.strftime('%B %d %Y')} by "
    message += f"{cleaning.cleaner.full_name} " if cleaning.cleaner else "No cleaner assigned "
    apt_name = ""
    if cleaning.booking and cleaning.booking.apartment:
        apt_name = cleaning.booking.apartment.name
    elif cleaning.apartment:
        apt_name = cleaning.apartment.name
    message += f"{apt_name} [{cleaning.status}]\nCleaning Details:"
    message += f"\n- Date: {cleaning.date}"
    message += f"\n- Apartment: {apt_name or 'N/A'}"
    message += f"\n- Status: {cleaning.status}"
    message += f"\n- Cleaner: {cleaning.cleaner.full_name}" if cleaning.cleaner else "\n- Cleaner: N/A"
    if cleaning.tasks:
        message += f"\n- Tasks: {cleaning.tasks}"
    if cleaning.notes:
        message += f"\n- Notes: {cleaning.notes}"
    return message


def my_cron_job():
    today = date.today()
    next_day = today + timedelta(days=1)
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    cleanings = Cleaning.objects.filter(
        date__in=[today, next_day],
    ).filter(
        Q(booking__isnull=True) | ~Q(booking__status='Blocked'),
    ).exclude(cleaner__isnull=True).select_related(
        'cleaner', 'booking', 'booking__apartment', 'apartment'
    ).order_by('date', 'id')

    for cleaning in cleanings:
        if not cleaning.cleaner or not cleaning.cleaner.telegram_chat_id:
            continue
        prefix = "TODAY" if cleaning.date == today else "TOMORROW"
        message = _build_cleaning_message(cleaning, prefix)
        form_url = "https://ro.am/join/fyy4jxbm-yr9qc7mo" if prefix == "TOMORROW" else "https://form.jotform.com/250414400218038"
        message += f"\nForm Link: {form_url}"
        send_telegram_message(cleaning.cleaner.telegram_chat_id.strip(), telegram_token, message)


class Command(BaseCommandWithErrorHandling):
    help = 'Run telegram notification task for cleaners'

    def execute_command(self, *args, **options):
        self.stdout.write('Running telegram notification task for cleaners...')
        my_cron_job()
        self.stdout.write('Telegram notification task for cleaners completed')
