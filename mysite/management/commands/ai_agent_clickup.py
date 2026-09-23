"""
ClickUp routing for the AI agent.

  python manage.py ai_agent_clickup                          status: token, mapped apartments
  python manage.py ai_agent_clickup --link Test_Apart2 --list-id 901329128753 [--channel-id 6-...-8]
  python manage.py ai_agent_clickup --create-channel Test_Apart2   create the List's chat channel through the API
  python manage.py ai_agent_clickup --test-message Test_Apart2     post a test message into the mapped channel
  python manage.py ai_agent_clickup --unlink Test_Apart2
"""
from django.core.management.base import BaseCommand, CommandError

from mysite.ai_agent import clickup


class Command(BaseCommand):
    help = "Map apartments to ClickUp channels / lists for the AI agent and test the connection"

    def add_arguments(self, parser):
        parser.add_argument('--link', metavar='APARTMENT')
        parser.add_argument('--list-id')
        parser.add_argument('--channel-id')
        parser.add_argument('--unlink', metavar='APARTMENT')
        parser.add_argument('--create-channel', metavar='APARTMENT')
        parser.add_argument('--test-message', metavar='APARTMENT')

    def _apartment(self, name):
        from mysite.models import Apartment
        apartment = Apartment.objects.filter(name=name).first()
        if not apartment:
            raise CommandError(f"Apartment '{name}' not found")
        return apartment

    def handle(self, *args, **options):
        from mysite.models import Apartment

        if options['link']:
            apartment = self._apartment(options['link'])
            if not (options['list_id'] or options['channel_id']):
                raise CommandError("--link needs --list-id and/or --channel-id")
            apartment.ai_clickup_list_id = options['list_id'] or apartment.ai_clickup_list_id
            apartment.ai_clickup_channel_id = options['channel_id'] or apartment.ai_clickup_channel_id
            apartment.ai_clickup_name = apartment.ai_clickup_name or apartment.name
            apartment.ai_clickup_active = True
            apartment.save(updated_by='ai_agent_clickup command')
        if options['unlink']:
            apartment = self._apartment(options['unlink'])
            apartment.ai_clickup_list_id = None
            apartment.ai_clickup_channel_id = None
            apartment.ai_clickup_name = None
            apartment.save(updated_by='ai_agent_clickup command')

        try:
            if options['create_channel']:
                apartment = self._apartment(options['create_channel'])
                if not apartment.ai_clickup_list_id:
                    raise CommandError(f"'{apartment.name}' has no list id yet: use --link {apartment.name} --list-id <id>")
                apartment.ai_clickup_channel_id = clickup.create_list_channel(apartment.ai_clickup_list_id)
                apartment.save(updated_by='ai_agent_clickup command')
                self.stdout.write(self.style.SUCCESS(f"Channel ready: {apartment.ai_clickup_channel_id}"))
            if options['test_message']:
                apartment = self._apartment(options['test_message'])
                if not apartment.ai_clickup_channel_id:
                    raise CommandError(f"'{apartment.name}' has no channel id yet: run --create-channel first")
                message_id = clickup.post_message(
                    apartment.ai_clickup_channel_id,
                    "✅ **Test message from the CRM AI agent (server side).**\n\nAlerts of this apartment's chats arrive here.",
                )
                self.stdout.write(self.style.SUCCESS(f"Message posted: {message_id}"))
        except clickup.ClickUpError as e:
            raise CommandError(str(e))

        self.stdout.write(f"CLICKUP_API_TOKEN: {'set' if clickup.is_configured() else 'NOT set (nothing is sent to ClickUp)'} | workspace {clickup.workspace_id()}")
        mapped = Apartment.objects.exclude(ai_clickup_list_id__isnull=True).exclude(ai_clickup_list_id='').order_by('name')
        for apartment in mapped:
            self.stdout.write(
                f"  {apartment.name}: list {apartment.ai_clickup_list_id or '-'} | channel {apartment.ai_clickup_channel_id or '- (not created yet)'}"
                f"{'' if apartment.ai_clickup_active else ' | INACTIVE'}"
            )
