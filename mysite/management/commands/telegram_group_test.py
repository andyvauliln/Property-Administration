import requests
import os
from django.core.management.base import BaseCommand


def normalize_group_chat_id(chat_id):
    """Bot API supergroup ids use -100 prefix. Convert -5106660832 -> -1005106660832."""
    s = (chat_id or "").strip()
    if not s or not s.lstrip("-").isdigit():
        return s
    if s.startswith("-100"):
        return s
    if s.startswith("-"):
        return "-100" + s[1:]
    return s


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    resp = requests.get(base_url)
    try:
        data = resp.json()
        if not data.get("ok"):
            return False, data.get("description", resp.text)
    except Exception:
        return False, resp.text
    return True, None


GROUPS = [
    ("Cleaning", "TELEGRAM_GROUP_CLEANING"),
    ("Checkout", "TELEGRAM_GROUP_CHECKOUT"),
    ("Checkin", "TELEGRAM_GROUP_CHECKIN"),
    ("Payment", "TELEGRAM_GROUP_PAYMENT"),
]


def strip_100_prefix(chat_id):
    """Reverse of normalize: -1005106660832 -> -5106660832."""
    s = (chat_id or "").strip()
    if s.startswith("-100") and len(s) > 3 and s[4:].lstrip("0").isdigit():
        return "-" + s[4:]
    return s


class Command(BaseCommand):
    help = 'Send a test message to all 4 Telegram notification groups'

    def add_arguments(self, parser):
        parser.add_argument("--verbose", action="store_true", help="Show each chat_id attempt")

    def handle(self, *args, **options):
        token = os.environ.get("TELEGRAM_TOKEN")
        if not token:
            self.stdout.write(self.style.ERROR('TELEGRAM_TOKEN not set'))
            return
        verbose = options.get("verbose", False)

        for group_name, env_key in GROUPS:
            chat_id_raw = os.environ.get(env_key)
            if not chat_id_raw:
                self.stdout.write(self.style.WARNING(f'Skipped {group_name}: {env_key} not set'))
                continue
            chat_id = chat_id_raw.strip()
            message = f"TEST: This is the {group_name} notification group. ID: {chat_id}"
            ids_to_try = [chat_id]
            alt = normalize_group_chat_id(chat_id)
            if alt != chat_id:
                ids_to_try.append(alt)
            alt2 = strip_100_prefix(chat_id)
            if alt2 != chat_id and alt2 not in ids_to_try:
                ids_to_try.append(alt2)
            ok, err, used_id = False, None, None
            for try_id in ids_to_try:
                ok, err = send_telegram_message(try_id, token, message)
                if verbose:
                    self.stdout.write(f"  {group_name}: try chat_id={try_id} -> {'OK' if ok else err}")
                if ok:
                    used_id = try_id
                    break
            if ok:
                self.stdout.write(self.style.SUCCESS(f'Sent test to {group_name} ({used_id})'))
            else:
                self.stdout.write(self.style.ERROR(f'Failed {group_name} (raw={chat_id!r}): {err}'))
