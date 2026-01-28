import os
from django.core.management.base import BaseCommand
from twilio.rest import Client


class Command(BaseCommand):
    help = "Inspect a Twilio conversation and participants"

    def add_arguments(self, parser):
        parser.add_argument(
            "--conversation-sid",
            required=True,
            type=str,
            help="Conversation SID to inspect"
        )
        parser.add_argument(
            "--messages-limit",
            type=int,
            default=5,
            help="Number of latest messages to show"
        )

    def handle(self, *args, **options):
        conversation_sid = options["conversation_sid"]
        messages_limit = max(options["messages_limit"], 0)

        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

        if not account_sid or not auth_token:
            self.stdout.write(self.style.ERROR("Twilio credentials not set"))
            return

        client = Client(account_sid, auth_token)

        try:
            conversation = client.conversations.v1.conversations(conversation_sid).fetch()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Failed to fetch conversation: {exc}"))
            return

        self.stdout.write(self.style.SUCCESS("=== Conversation ==="))
        self.stdout.write(f"SID: {conversation.sid}")
        self.stdout.write(f"Friendly name: {conversation.friendly_name}")
        self.stdout.write(f"State: {conversation.state}")
        self.stdout.write(f"Date created: {conversation.date_created}")
        self.stdout.write(f"Date updated: {conversation.date_updated}")
        self.stdout.write(f"Messaging service SID: {conversation.messaging_service_sid}")
        self.stdout.write(f"Unique name: {conversation.unique_name}")

        self.stdout.write(self.style.SUCCESS("\n=== Participants ==="))
        try:
            participants = client.conversations.v1.conversations(
                conversation_sid
            ).participants.list()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Failed to fetch participants: {exc}"))
            participants = []

        if not participants:
            self.stdout.write("No participants found")
        else:
            for participant in participants:
                self.stdout.write(f"\nParticipant SID: {participant.sid}")
                self.stdout.write(f"Identity: {getattr(participant, 'identity', None)}")
                binding = getattr(participant, "messaging_binding", None)
                if binding:
                    self.stdout.write(f"Binding address: {binding.get('address')}")
                    self.stdout.write(f"Binding projected address: {binding.get('projected_address')}")
                    self.stdout.write(f"Binding type: {binding.get('type')}")
                else:
                    self.stdout.write("Binding: None")

        if messages_limit > 0:
            self.stdout.write(self.style.SUCCESS("\n=== Latest Messages ==="))
            try:
                messages = client.conversations.v1.conversations(
                    conversation_sid
                ).messages.list(limit=messages_limit)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Failed to fetch messages: {exc}"))
                return

            if not messages:
                self.stdout.write("No messages found")
            else:
                for message in messages:
                    self.stdout.write(f"\nMessage SID: {message.sid}")
                    self.stdout.write(f"Author: {message.author}")
                    self.stdout.write(f"Date created: {message.date_created}")
                    body_preview = message.body or ""
                    self.stdout.write(f"Body: {body_preview}")
