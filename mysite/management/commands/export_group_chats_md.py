import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from mysite.group_chat_md_export import GroupChatMarkdownExporter


class Command(BaseCommand):
    help = "Export Twilio group chats to Markdown for AI prompt review."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            default="reports/group_chats_for_ai_review.md",
            help="Path to write the Markdown export (default: reports/group_chats_for_ai_review.md)",
        )
        parser.add_argument(
            "--log-file",
            type=str,
            default=str(Path(settings.BASE_DIR) / "logs" / "group_chat_log.log"),
            help="Path to group_chat_log.log for AI context snapshots.",
        )

    def handle(self, *args, **options):
        output_path = options["output"]
        exporter = GroupChatMarkdownExporter()
        conversations = exporter.get_all_conversations()
        markdown = exporter.render_markdown(conversations)

        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as export_file:
            export_file.write(markdown)

        message_count = sum(len(list(conversation.export_messages)) for conversation in conversations)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote group chat export to {output_path} "
                f"(conversations={len(conversations)}, messages={message_count})"
            )
        )
