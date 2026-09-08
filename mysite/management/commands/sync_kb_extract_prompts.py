from django.core.management.base import BaseCommand

from mysite.views.messaging import KB_EXTRACT_PROMPT_KEYS, sync_kb_extract_prompts_to_db


class Command(BaseCommand):
    help = 'Sync KB extract check/merge prompts in AIManagement from built-in templates.'

    def handle(self, *args, **options):
        sync_kb_extract_prompts_to_db()
        for prompt_key in KB_EXTRACT_PROMPT_KEYS:
            self.stdout.write(self.style.SUCCESS(f'Updated {prompt_key}'))
        self.stdout.write(self.style.SUCCESS('KB extract prompts synced.'))
