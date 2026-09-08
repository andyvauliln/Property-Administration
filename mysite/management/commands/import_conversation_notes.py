import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from mysite.models import TwilioConversation, TwilioMessage

DEFAULT_JSON_PATH = "reports/conversation.json"


class Command(BaseCommand):
    help = (
        "Import notes from conversation.json into TwilioConversation and TwilioMessage. "
        "Only writes when the DB notes field is empty so UI edits are preserved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=DEFAULT_JSON_PATH,
            help=f"Replay JSON state file (default: {DEFAULT_JSON_PATH})",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without writing to the DB.",
        )

    def handle(self, *args, **options):
        state_path = self._resolve_path(options["file"])
        if not state_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {state_path}"))
            return

        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)

        conversations = state.get("conversations") or []
        dry_run = options["dry_run"]

        conv_updated = 0
        conv_skipped = 0
        conv_missing = 0
        msg_updated = 0
        msg_skipped = 0
        msg_unmatched = 0

        for record in conversations:
            sid = (record.get("conversation_sid") or "").strip()
            if not sid:
                continue

            conv = TwilioConversation.objects.filter(conversation_sid=sid).first()
            if not conv:
                conv_missing += 1
                continue

            conv_notes = (record.get("notes") or "").strip()
            if conv_notes:
                if not (conv.notes or "").strip():
                    if not dry_run:
                        conv.notes = conv_notes
                        conv.save(update_fields=["notes", "updated_at"])
                    conv_updated += 1
                else:
                    conv_skipped += 1

            for msg_record in record.get("messages") or []:
                notes = (msg_record.get("notes") or "").strip()
                if not notes:
                    continue

                sids = [s for s in (msg_record.get("message_sids") or []) if s]
                if not sids:
                    msg_unmatched += 1
                    continue

                messages = list(TwilioMessage.objects.filter(message_sid__in=sids))
                if not messages:
                    msg_unmatched += len(sids)
                    continue

                found_sids = {m.message_sid for m in messages}
                msg_unmatched += len([s for s in sids if s not in found_sids])

                ai = msg_record.get("ai") or {}
                json_has_ai = bool(
                    ai.get("answer")
                    or ai.get("no_answer")
                    or ai.get("why")
                    or ai.get("why_from_model")
                    or ai.get("why_analysis")
                )

                for message in messages:
                    use_ai = json_has_ai or bool(message.ai_response) or bool(message.ai_response_why)
                    field = "ai_notes" if use_ai else "notes"
                    if (getattr(message, field) or "").strip():
                        msg_skipped += 1
                        continue
                    if not dry_run:
                        setattr(message, field, notes)
                        message.save(update_fields=[field, "updated_at"])
                    msg_updated += 1

        prefix = "[dry-run] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Conversations updated: {conv_updated}, "
                f"skipped (already set): {conv_skipped}, missing: {conv_missing}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}Messages updated: {msg_updated}, "
                f"skipped (already set): {msg_skipped}, unmatched SIDs: {msg_unmatched}"
            )
        )

    def _resolve_path(self, path_str):
        path = Path(path_str)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        return path
