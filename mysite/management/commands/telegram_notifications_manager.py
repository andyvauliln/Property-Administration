

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

    telegram_manager_chat_id = os.environ["TELEGRAM_CHAT_ID_MANAGER"]
    telegram_token = os.environ["TELEGRAM_HANDY_MAN_BOT_TOKEN"]
    manager_phone = os.environ["MANAGER_PHONE3"]


    for notification in notifications:
        if notification.payment and (notification.payment.payment_status == "Completed" or notification.payment.payment_status == "Merged"):
            continue
        elif notification.booking and notification.booking.apartment and notification.booking.apartment.manager and notification.booking.apartment.manager.phone == manager_phone:
            message = f"{notification.notification_message}"
            send_telegram_message(telegram_manager_chat_id, telegram_token, message)
            continue
        elif notification.payment and notification.payment.booking and notification.payment.booking.apartment and notification.payment.booking.apartment.manager and notification.payment.booking.apartment.manager.phone == manager_phone:
            message = f"{notification.notification_message}"
            send_telegram_message(telegram_manager_chat_id, telegram_token, message)
            continue
        elif notification.cleaning and notification.cleaning.booking and notification.cleaning.booking.apartment and notification.cleaning.booking.apartment.manager and notification.cleaning.booking.apartment.manager.phone == manager_phone:
            message = f"{notification.notification_message}"
            send_telegram_message(telegram_manager_chat_id, telegram_token, message)
            continue

            


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
