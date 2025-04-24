import requests
from datetime import timedelta, date
from mysite.models import Notification, Payment
import os
from django.core.management.base import BaseCommand
from django.db.models import Q


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)



def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day, send_in_telegram=True, cleaning__isnull=False)
    print(f"Found {len(notifications)} notifications for {next_day}")
    telegram_token = os.environ["TELEGRAM_TOKEN"]
    
   
    for notification in notifications:
        message = f"{notification.notification_message}"
        
        if notification.cleaning and notification.cleaning.cleaner:
            cliner_chat_id = notification.cleaning.cleaner.telegram_chat_id
            message += f"\nCleaning Details:"
            message += f"\n- Date: {notification.cleaning.date}"
            if hasattr(notification.cleaning, 'booking') and notification.cleaning.booking and notification.cleaning.booking.apartment:
                message += f"\n- Apartment: {notification.cleaning.booking.apartment.name}"
            if hasattr(notification.cleaning, 'apartment') and notification.cleaning.apartment:
                message += f"\n- Apartment: {notification.cleaning.apartment.name}"
            if hasattr(notification.cleaning, 'status'):
                message += f"\n- Status: {notification.cleaning.status}"
            if hasattr(notification.cleaning, 'cleaner'):
                message += f"\n- Cleaner: {notification.cleaning.cleaner.full_name}"

            send_telegram_message(cliner_chat_id.strip(), telegram_token, message)


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
