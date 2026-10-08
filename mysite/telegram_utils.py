"""Shared Telegram helpers for the daily notification management commands."""
import requests


def normalize_group_chat_id(chat_id):
    """Return the chat id as a stripped string ('' for None)."""
    s = (chat_id or "").strip()
    if not s or not s.lstrip("-").isdigit():
        return s
    return s


def send_telegram_message(chat_id, token, message, dry_run=False, stdout=None):
    """Send a plain-text message with the Bot API (GET sendMessage).

    With dry_run and a stdout, write the message to stdout instead of sending it.
    """
    if dry_run and stdout:
        stdout.write(message)
        stdout.write("\n" + "-" * 40 + "\n")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.get(url, params={"chat_id": chat_id, "text": message})
