import requests
from datetime import timedelta, date
from mysite.models import Notification
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


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


def my_cron_job():
    today = date.today()
    next_day = today + timedelta(days=1)
    chat_id = os.environ.get("TELEGRAM_GROUP_CLEANING")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        return

    notifications = Notification.objects.filter(
        date__in=[today, next_day],
        send_in_telegram=True,
        cleaning__isnull=False,
    ).exclude(cleaning__booking__status='Blocked')

    for notification in notifications:
        prefix = "TODAY" if notification.date == today else "TOMORROW"
        message = build_cleaning_message(notification, prefix)
        if notification.date == next_day and "Form Link:" in message:
            message = message.replace("https://form.jotform.com/250414400218038", "https://ro.am/join/fyy4jxbm-yr9qc7mo")
        send_telegram_message(chat_id.strip(), token, message)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily cleaning notifications to Cleaning Telegram group'

    def execute_command(self, *args, **options):
        self.stdout.write('Running telegram group cleaning...')
        my_cron_job()
        self.stdout.write('Telegram group cleaning completed')
