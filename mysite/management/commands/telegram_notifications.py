

import requests
from datetime import timedelta, date
from mysite.models import Notification
import os
from django.core.management.base import BaseCommand


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day, send_in_telegram=True)

    telegram_chat_ids = os.environ["TELEGRAM_CHAT_ID"].split(",")
    telegram_token = os.environ["TELEGRAM_TOKEN"]
    

    for notification in notifications:
        message = f"{notification.notification_message}"
        
        # Add payment information if it exists
        if notification.payment:
            message += f"\nPayment Details:"
            message += f"\n- Amount: ${notification.payment.amount}"
            message += f"\n- Status: {notification.payment.payment_status}"
            message += f"\n- Type: {notification.payment.payment_type.name if notification.payment.payment_type else 'N/A'}"
            if notification.payment.notes:
                message += f"\n- Notes: {notification.payment.notes}"
        
        # Add booking information if it exists
        if notification.booking:
            message += f"\nBooking Details:"
            message += f"\n- Start Date: {notification.booking.start_date}"
            message += f"\n- End Date: {notification.booking.end_date}"
            if hasattr(notification.booking, 'apartment'):
                message += f"\n- Apartment: {notification.booking.apartment.name}"

            for chat_id in telegram_chat_ids:
                send_telegram_message(chat_id.strip(), telegram_token, message)


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
