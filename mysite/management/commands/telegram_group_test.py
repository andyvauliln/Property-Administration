import requests
import os
from django.core.management.base import BaseCommand


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    resp = requests.get(base_url)
    return resp.status_code == 200


GROUPS = [
    ("Cleaning", "TELEGRAM_GROUP_CLEANING"),
    ("Checkout", "TELEGRAM_GROUP_CHECKOUT"),
    ("Checkin", "TELEGRAM_GROUP_CHECKIN"),
    ("Payment", "TELEGRAM_GROUP_PAYMENT"),
]


class Command(BaseCommand):
    help = 'Send a test message to all 4 Telegram notification groups'

    def handle(self, *args, **options):
        token = os.environ.get("TELEGRAM_TOKEN")
        if not token:
            self.stdout.write(self.style.ERROR('TELEGRAM_TOKEN not set'))
            return

        for group_name, env_key in GROUPS:
            chat_id = os.environ.get(env_key)
            if not chat_id:
                self.stdout.write(self.style.WARNING(f'Skipped {group_name}: {env_key} not set'))
                continue
            message = f"TEST: This is the {group_name} notification group. ID: {chat_id}"
            ok = send_telegram_message(chat_id.strip(), token, message)
            if ok:
                self.stdout.write(self.style.SUCCESS(f'Sent test to {group_name} ({chat_id})'))
            else:
                self.stdout.write(self.style.ERROR(f'Failed to send to {group_name} ({chat_id})'))
