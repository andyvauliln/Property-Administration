import requests
from datetime import timedelta, date
from mysite.models import Notification
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    chat_id = os.environ.get("TELEGRAM_GROUP_CHECKIN")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        return

    notifications = Notification.objects.filter(
        date=next_day,
        send_in_telegram=True,
        booking__isnull=False,
        message="Start Booking",
    ).exclude(booking__status='Blocked')

    for notification in notifications:
        message = f"CHECKIN TOMORROW: {notification.notification_message}"
        if notification.booking:
            message += "\nBooking Details:"
            message += f"\n- Start Date: {notification.booking.start_date}"
            message += f"\n- End Date: {notification.booking.end_date}"
            if notification.booking.apartment:
                message += f"\n- Apartment: {notification.booking.apartment.name}"
            if notification.booking.tenant:
                message += f"\n- Tenant: {notification.booking.tenant.full_name}"
        send_telegram_message(chat_id.strip(), token, message)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily checkin notifications to Checkin Telegram group'

    def execute_command(self, *args, **options):
        self.stdout.write('Running telegram group checkin...')
        my_cron_job()
        self.stdout.write('Telegram group checkin completed')
