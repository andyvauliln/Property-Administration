import requests
from django.core.management import BaseCommand
from django.urls import reverse
import os

class Command(BaseCommand):
    help = "Set new telegram webhook"

    def handle(self, *args, **options):
  
        webhook_url = "http://68.183.124.79/telegram_webhook/"

        self.stdout.write(f"Checking current webhook info...\n")
        current_webhook_info = self.check_current_webhook(os.environ['TELEGRAM_TOKEN'])

        if current_webhook_info and current_webhook_info.get('url') == webhook_url:
            self.stdout.write(self.style.SUCCESS(f"Webhook is already set to {webhook_url}. No changes needed."))
        else:
            self.stdout.write(f"Setting new webhook url {webhook_url}...\n")
            set_webhook_result = self.set_telegram_webhook(os.environ['TELEGRAM_TOKEN'], webhook_url)

            if set_webhook_result.get('ok'):
                self.stdout.write(self.style.SUCCESS(f"Webhook set successfully: {webhook_url}"))
            else:
                error_message = set_webhook_result.get('description', 'Unknown error')
                self.stdout.write(self.style.ERROR(f"Failed to set webhook. Error: {error_message}"))




    def check_current_webhook(self, bot_token):
        get_webhook_info_api = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
        response = requests.get(get_webhook_info_api)
        if response.status_code == 200:
            return response.json().get('result')
        else:
            self.stdout.write(self.style.WARNING(f"Failed to retrieve current webhook info. Status code: {response.status_code}"))
            return None

    def set_telegram_webhook(self, bot_token, webhook_url):
        set_webhook_api = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        return requests.post(set_webhook_api, data={"url": webhook_url}).json()