

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
        if notification.payment and (notification.payment.payment_status == "Completed" or notification.payment.payment_status == "Merged"):
            continue
        else:
            message = f"Tomorrow events: {notification.notification_message}"

            for chat_id in telegram_chat_ids:
                send_telegram_message(chat_id.strip(), telegram_token, message)


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
