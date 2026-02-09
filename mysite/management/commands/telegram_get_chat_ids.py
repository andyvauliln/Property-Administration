import requests
import os
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Print chat_id for each chat this bot sees (from recent getUpdates). Add bot to group, then send a message in group, then run this."

    def handle(self, *args, **options):
        token = os.environ.get("TELEGRAM_TOKEN")
        if not token:
            self.stdout.write(self.style.ERROR("TELEGRAM_TOKEN not set"))
            return
        url = f"https://api.telegram.org/bot{token}/getUpdates?limit=100"
        resp = requests.get(url)
        try:
            data = resp.json()
        except Exception:
            self.stdout.write(self.style.ERROR(resp.text))
            return
        if not data.get("ok"):
            self.stdout.write(self.style.ERROR(data.get("description", resp.text)))
            return
        seen = {}
        for u in data.get("result", []):
            msg = u.get("message") or u.get("channel_post") or {}
            chat = msg.get("chat") or {}
            cid = chat.get("id")
            if cid is None:
                continue
            if cid in seen:
                continue
            seen[cid] = {
                "title": chat.get("title") or chat.get("first_name") or ("id " + str(cid)),
                "type": chat.get("type", ""),
            }
        if not seen:
            self.stdout.write(
                "No chats in updates. Add the bot to a group and send any message in that group, then run this again."
            )
            return
        for cid, info in sorted(seen.items()):
            self.stdout.write(f"  chat_id={cid}  type={info['type']}  title={info['title']!r}")
