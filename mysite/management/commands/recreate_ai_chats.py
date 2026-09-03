import json
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Prefetch
from django.utils import timezone
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mysite.replay_kb_cleanup import cleanup_knowledge_bases, reset_knowledge_for_conversation
from mysite.models import AIManagement, TwilioConversation, TwilioMessage, User
from mysite.views.messaging import (
    DEFAULT_CHAT_MODEL,
    LEGACY_CHAT_MODEL,
    MANAGER_PHONES,
    TWILIO_ASSISTANT_PHONE,
    _apartment_fields_context,
    _get_ai_client,
    _get_prompt_with_source,
    _is_skippable_message,
    _safe_completion_content,
    ai_answer_customer_detailed,
    build_ai_extract_check_prompt,
    build_ai_extract_merge_prompt,
    delete_conversation as twilio_delete_conversation,
    delete_message as twilio_delete_message,
    is_assistant_system_notification,
    manager_message_has_operational_kb_hints,
    resolve_chat_model,
)


AI_AUTHORS = {"ASSISTANT", "Virtual Assistant", TWILIO_ASSISTANT_PHONE}
STAR_SEPARATOR = "*" * 76
LINE_SEPARATOR = "=" * 76
DEFAULT_JSON_PATH = "reports/conversation.json"


class QuitReplay(Exception):
    pass


class Command(BaseCommand):
    help = "Interactively replay Twilio group chats, recreate AI answers, and save analysis to conversation.json."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=DEFAULT_JSON_PATH,
            help=f"JSON state file (default: {DEFAULT_JSON_PATH})",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Start replay from a clean JSON state. "
                "With --conversation, reset only that conversation (JSON progress, its AI cleanup, "
                "and that apartment KB). Without --conversation, reset the whole replay file and all apartment KBs."
            ),
        )
        parser.add_argument(
            "--conversation",
            help="Only process this Twilio conversation SID.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of conversations to process in this run.",
        )
        parser.add_argument(
            "--no-delete",
            action="store_true",
            help="Dry-run delete mode: do not delete or update DB/Twilio records.",
        )
        parser.add_argument(
            "--db-only-delete",
            action="store_true",
            help="Delete/update local DB only; do not delete anything from Twilio.",
        )

    def handle(self, *args, **options):
        self.console = Console()
        self.options = options
        self.model, self.model_replaced_from = resolve_chat_model()
        self.state_path = self._resolve_path(options["file"])
        if options["reset"] and options.get("conversation"):
            self.state = self._load_state()
            self._reset_single_conversation(options["conversation"])
        elif options["reset"]:
            self.state = self._initial_state()
        else:
            self.state = self._load_state()
        self.user_names = {}

        conversations = self._get_conversations(options.get("conversation"))
        if options.get("limit"):
            conversations = conversations[: options["limit"]]
        self.user_names = self._get_user_names(conversations)

        self._print_start(conversations)
        self._run_kb_cleanup_if_needed()
        try:
            for position, conversation in enumerate(conversations, start=1):
                if self._conversation_completed(conversation):
                    self.console.print(
                        f"[dim]Skipping completed conversation {conversation.conversation_sid}[/dim]"
                    )
                    continue
                self._process_conversation(conversation, position, len(conversations))
        except QuitReplay:
            self._save_state()
            self.console.print("[yellow]Quitting. Progress saved to JSON.[/yellow]")

        self._save_state()
        self.console.print(self.style.SUCCESS(f"Replay state saved to {self.state_path}"))

    # ---------------------------------------------------------------------
    # State and loading
    # ---------------------------------------------------------------------
    def _run_kb_cleanup_if_needed(self):
        if self.options.get("reset") and self.options.get("conversation"):
            return
        if self.state.get("kb_cleanup_done"):
            self.console.print("[dim]KB cleanup already done for this replay state; skipping.[/dim]")
            return

        stats = cleanup_knowledge_bases(self.state, dry_run=self.options.get("no_delete"))
        self.console.print(
            "[dim]KB cleanup: "
            f"apartments_cleared={stats['apartments_cleared']} "
            f"apartments_restored={stats['apartments_restored']} "
            f"global_deleted={stats['global_deleted']} "
            f"global_kept={stats['global_kept']} "
            f"mode={stats['mode']}[/dim]"
        )
        if self.options.get("no_delete"):
            return

        self.state["kb_cleanup_done"] = True
        self.state["kb_cleanup"] = {
            "mode": stats["mode"],
            "preserved_apartment_ids": stats["preserved_apartment_ids"],
            "apartments_cleared": stats["apartments_cleared"],
            "apartments_restored": stats["apartments_restored"],
            "global_kept": stats["global_kept"],
            "global_deleted": stats["global_deleted"],
            "global_created": stats["global_created"],
            "ran_at": timezone.now().isoformat(),
        }
        self._save_state()

    def _reset_single_conversation(self, conversation_sid):
        conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
        if not conversation:
            raise CommandError(f"Conversation {conversation_sid} was not found.")

        apartment = self._conversation_apartment(conversation)
        apartment_id = apartment.id if apartment else None
        dry_run = self.options.get("no_delete")

        kept = [
            record
            for record in self.state.get("conversations", [])
            if record.get("conversation_sid") != conversation_sid
        ]
        self.state["conversations"] = kept
        last_processed = self.state.get("last_processed") or {}
        if last_processed.get("conversation_sid") == conversation_sid:
            self.state["last_processed"] = None

        kb_stats = reset_knowledge_for_conversation(
            self.state,
            conversation_sid,
            apartment_id,
            dry_run=dry_run,
        )
        self._save_state()
        self.console.print(
            "[yellow]"
            f"Restarting {conversation_sid} from clean "
            f"(apartment_kb={kb_stats['apartment_action']}, "
            f"global_deleted={kb_stats['global_deleted']}, mode={kb_stats['mode']})"
            "[/yellow]"
        )

    def _resolve_path(self, raw_path):
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        return path

    def _initial_state(self):
        return {
            "version": 1,
            "model": self.model,
            "created_at": timezone.now().isoformat(),
            "updated_at": timezone.now().isoformat(),
            "last_processed": None,
            "kb_cleanup_done": False,
            "kb_cleanup": {},
            "conversations": [],
        }

    def _load_state(self):
        if not self.state_path.exists():
            return self._initial_state()
        try:
            with self.state_path.open("r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except (OSError, json.JSONDecodeError):
            self.console.print(
                f"[yellow]Could not read {self.state_path}; starting with a fresh state.[/yellow]"
            )
            return self._initial_state()

        state.setdefault("version", 1)
        state.setdefault("model", self.model)
        state.setdefault("last_processed", None)
        state.setdefault("kb_cleanup_done", False)
        state.setdefault("kb_cleanup", {})
        state.setdefault("conversations", [])
        return state

    def _save_state(self):
        self.state["model"] = self.model
        self.state["updated_at"] = timezone.now().isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as tmp_file:
            json.dump(self.state, tmp_file, ensure_ascii=False, indent=2)
            tmp_file.write("\n")
        os.replace(tmp_path, self.state_path)

    def _get_or_create_conversation_record(self, conversation):
        for record in self.state["conversations"]:
            if record.get("conversation_sid") == conversation.conversation_sid:
                record.setdefault("messages", [])
                return record

        apartment = self._conversation_apartment(conversation)
        record = {
            "conversation_sid": conversation.conversation_sid,
            "friendly_name": conversation.friendly_name,
            "apartment_id": apartment.id if apartment else None,
            "apartment_name": apartment.name if apartment else None,
            "booking_id": conversation.booking_id,
            "participants": self._participants(conversation),
            "processed_at": None,
            "completed": False,
            "cleanup_done": False,
            "cleanup": {},
            "tags": [],
            "notes": "",
            "deleted": False,
            "messages": [],
        }
        self.state["conversations"].append(record)
        return record

    def _conversation_completed(self, conversation):
        if self.options.get("reset") and not self.options.get("conversation"):
            return False
        for record in self.state.get("conversations", []):
            if record.get("conversation_sid") == conversation.conversation_sid:
                return bool(record.get("completed"))
        return False

    # ---------------------------------------------------------------------
    # Conversation discovery
    # ---------------------------------------------------------------------
    def _get_conversations(self, conversation_sid=None):
        messages_prefetch = Prefetch(
            "messages",
            queryset=TwilioMessage.objects.order_by("message_timestamp", "id"),
            to_attr="replay_messages",
        )
        queryset = (
            TwilioConversation.objects.select_related(
                "apartment",
                "booking",
                "booking__tenant",
                "booking__apartment",
            )
            .prefetch_related(messages_prefetch)
            .order_by("apartment__name", "booking_id", "conversation_sid")
        )
        if conversation_sid:
            queryset = queryset.filter(conversation_sid=conversation_sid)
        conversations = list(queryset)
        return sorted(conversations, key=self._conversation_sort_key)

    def _conversation_sort_key(self, conversation):
        apartment = self._conversation_apartment(conversation)
        apartment_name = apartment.name if apartment else ""
        booking_id = conversation.booking_id or 0
        first_message = conversation.replay_messages[0] if conversation.replay_messages else None
        first_timestamp = first_message.message_timestamp if first_message else conversation.created_at
        return (apartment_name.lower(), booking_id, first_timestamp, conversation.conversation_sid)

    def _conversation_apartment(self, conversation):
        if conversation.apartment:
            return conversation.apartment
        if conversation.booking and conversation.booking.apartment:
            return conversation.booking.apartment
        return None

    def _get_user_names(self, conversations):
        phones = set()
        for conversation in conversations:
            if conversation.booking and conversation.booking.tenant and conversation.booking.tenant.phone:
                phones.add(conversation.booking.tenant.phone)
            for message in conversation.replay_messages:
                if message.author:
                    phones.add(message.author)
        return {
            user.phone: user.full_name
            for user in User.objects.filter(phone__in=phones).only("phone", "full_name")
            if user.phone
        }

    def _participants(self, conversation):
        participants = {}
        if conversation.booking and conversation.booking.tenant and conversation.booking.tenant.phone:
            phone = conversation.booking.tenant.phone
            participants[phone] = {
                "phone_or_identity": phone,
                "name": conversation.booking.tenant.full_name or "Unknown",
                "role": "tenant",
            }
        for message in conversation.replay_messages:
            if not message.author or message.author in participants:
                continue
            participants[message.author] = {
                "phone_or_identity": message.author,
                "name": self._display_name(message.author),
                "role": self._message_role(message.author),
            }
        return list(participants.values())

    # ---------------------------------------------------------------------
    # Rendering and input
    # ---------------------------------------------------------------------
    def _print_start(self, conversations):
        processed = sum(
            1 for item in self.state.get("conversations", []) if item.get("completed")
        )
        mode = "DRY RUN (--no-delete)" if self.options.get("no_delete") else "LIVE"
        if self.options.get("db_only_delete"):
            mode = "DB ONLY DELETE"
        self.console.print(Text(STAR_SEPARATOR, style="cyan"))
        self.console.print("[bold cyan]AI CHAT REPLAY[/bold cyan]")
        self.console.print(
            f"Model: [bold]{self.model}[/bold]   Conversations in scope: {len(conversations)}   Completed in JSON: {processed}"
        )
        if self.model_replaced_from:
            self.console.print(
                f"[yellow]Replaced deprecated model '{self.model_replaced_from}' with '{self.model}'.[/yellow]"
            )
        self.console.print(f"State file: [bold]{self.state_path}[/bold]")
        self.console.print(f"Mode: [bold]{mode}[/bold]")
        self.console.print("Type [bold]q[/bold] to quit safely, [bold]s[/bold] to skip a message.")
        self.console.print(Text(STAR_SEPARATOR, style="cyan"))

    def _prompt(self, label, default=""):
        try:
            value = self.console.input(f"{label} ")
        except EOFError:
            raise QuitReplay()
        value = value.strip()
        if value.lower() == "q":
            raise QuitReplay()
        return value if value else default

    def _prompt_tags_notes(self, subject="message"):
        subject_label = subject.capitalize()
        self.console.print(f"[dim]Entering tags/notes for current {subject}.[/dim]")
        raw_tags = self._prompt(
            f"[bold]{subject_label} tags[/bold] (valid, problems, to_delete, to_check, to_save) [Enter=none]:",
            "",
        )
        tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
        notes = self._prompt(f"[bold]{subject_label} notes[/bold] [Enter=none]:", "")
        return tags, notes

    def _confirm(self, label, default=False):
        suffix = "Y/n" if default else "y/N"
        value = self._prompt(f"{label} [{suffix}]:", "")
        if not value:
            return default
        return value.lower() in {"y", "yes"}

    def _print_panel(self, title, body, style):
        self.console.print(Panel(body or "(empty)", title=title, border_style=style))

    # ---------------------------------------------------------------------
    # Conversation workflow
    # ---------------------------------------------------------------------
    def _process_conversation(self, conversation, position, total):
        record = self._get_or_create_conversation_record(conversation)
        self._print_conversation_header(conversation, position, total)

        if not record.get("cleanup_done"):
            cleanup = self._cleanup_ai_answers(conversation)
            record["cleanup"] = cleanup
            record["cleanup_done"] = True
            self._save_state()
            self.console.print(
                f"[dim]Cleanup: deleted={cleanup['deleted_messages']} cleared={cleanup['cleared_messages']} mode={cleanup['mode']}[/dim]"
            )
        else:
            self.console.print("[dim]Cleanup already done for this conversation; resuming.[/dim]")

        messages = self._fresh_messages(conversation)
        units = self._build_units(messages)
        processed_keys = {item.get("key") for item in record.get("messages", [])}

        for index, unit in enumerate(units, start=1):
            key = self._unit_key(unit)
            if key in processed_keys:
                continue
            message_record = self._process_unit(conversation, unit, index, len(units))
            record["messages"].append(message_record)
            self.state["last_processed"] = {
                "conversation_sid": conversation.conversation_sid,
                "index": index,
                "updated_at": timezone.now().isoformat(),
            }
            self._save_state()

        self.console.print(Text("-" * 76, style="cyan"))
        self.console.print(
            "[bold cyan]Conversation review[/bold cyan] - these tags and notes apply to the whole conversation, not the last message."
        )
        tags, notes = self._prompt_tags_notes(subject="conversation")
        record["tags"] = tags
        record["notes"] = notes
        if "to_delete" in tags:
            record["deleted"] = self._delete_conversation(conversation)
        record["processed_at"] = timezone.now().isoformat()
        record["completed"] = True
        self._save_state()

    def _print_conversation_header(self, conversation, position, total):
        apartment = self._conversation_apartment(conversation)
        participants = ", ".join(
            f"{p['name']} ({p['phone_or_identity']}, {p['role']})"
            for p in self._participants(conversation)
        ) or "none"
        header = (
            f"{conversation.conversation_sid} {position}/{total}\n"
            f"{conversation.friendly_name}\n"
            f"Apartment: {apartment.name if apartment else 'none'}   Booking: {conversation.booking_id or 'none'}\n"
            f"Participants: {participants}"
        )
        self.console.print(Text(STAR_SEPARATOR, style="cyan"))
        self._print_panel("CONVERSATION", header, "cyan")
        if not apartment or not conversation.booking:
            self.console.print(
                "[yellow]This conversation is missing apartment or booking; tenant AI replay will be skipped.[/yellow]"
            )

    def _fresh_messages(self, conversation):
        return list(
            TwilioMessage.objects.filter(conversation=conversation).order_by(
                "message_timestamp", "id"
            )
        )

    # ---------------------------------------------------------------------
    # Cleanup and deletes
    # ---------------------------------------------------------------------
    def _cleanup_ai_answers(self, conversation):
        mode = "no-delete" if self.options.get("no_delete") else "live"
        if self.options.get("db_only_delete"):
            mode = "db-only"

        messages = list(TwilioMessage.objects.filter(conversation=conversation))
        answer_messages = [
            message
            for message in messages
            if self._should_delete_ai_message(message)
        ]
        clear_qs = TwilioMessage.objects.filter(conversation=conversation).filter(
            models_ai_filter()
        )

        if self.options.get("no_delete"):
            return {
                "mode": mode,
                "deleted_messages": 0,
                "would_delete_messages": len(answer_messages),
                "cleared_messages": 0,
                "would_clear_messages": clear_qs.count(),
            }

        deleted = 0
        for message in answer_messages:
            twilio_deleted = True
            if not self.options.get("db_only_delete") and not self._is_local_message(message):
                try:
                    twilio_delete_message(conversation.conversation_sid, message.message_sid)
                except Exception as exc:
                    twilio_deleted = False
                    self.console.print(
                        f"[red]Twilio delete failed for {message.message_sid}: {exc}[/red]"
                    )
            if twilio_deleted or self.options.get("db_only_delete") or self._is_local_message(message):
                message.delete()
                deleted += 1

        cleared = clear_qs.update(
            ai_response=None,
            ai_response_why=None,
            ai_sent_to_chat=None,
            ai_kb_updated=None,
            ai_kb_changes=None,
        )
        return {
            "mode": mode,
            "deleted_messages": deleted,
            "would_delete_messages": len(answer_messages),
            "cleared_messages": cleared,
        }

    def _should_delete_ai_message(self, message):
        """Delete old AI chat answers and KB-UPDATE rows; keep contract/booking notifications."""
        if message.message_sid and message.message_sid.startswith("KB-UPDATE-"):
            return True
        if message.author not in AI_AUTHORS:
            return False
        if self._is_system_notification_message(message):
            return False
        if self._normalize_text(message.body):
            return True
        return str(message.body or "").startswith("\U0001f4da Knowledge base updated")

    def _delete_message_unit(self, conversation, unit):
        if self.options.get("no_delete"):
            self.console.print("[yellow]--no-delete is active; message was not deleted.[/yellow]")
            return False
        deleted = False
        for message in unit["messages"]:
            twilio_deleted = True
            if not self.options.get("db_only_delete") and not self._is_local_message(message):
                try:
                    twilio_delete_message(conversation.conversation_sid, message.message_sid)
                except Exception as exc:
                    twilio_deleted = False
                    self.console.print(
                        f"[red]Twilio delete failed for {message.message_sid}: {exc}[/red]"
                    )
            if twilio_deleted or self.options.get("db_only_delete") or self._is_local_message(message):
                message.delete()
                deleted = True
        return deleted

    def _delete_conversation(self, conversation):
        if self.options.get("no_delete"):
            self.console.print("[yellow]--no-delete is active; conversation was not deleted.[/yellow]")
            return False
        if not self.options.get("db_only_delete"):
            twilio_delete_conversation(conversation.conversation_sid)
        conversation.delete()
        return True

    def _is_local_message(self, message):
        return bool(message.message_sid and message.message_sid.startswith("KB-UPDATE-"))

    # ---------------------------------------------------------------------
    # Unit classification
    # ---------------------------------------------------------------------
    def _build_units(self, messages):
        units = []
        index = 0
        while index < len(messages):
            message = messages[index]
            role = self._message_role(message.author)
            if role == "manager":
                grouped = [message]
                index += 1
                while index < len(messages) and self._message_role(messages[index].author) == "manager":
                    grouped.append(messages[index])
                    index += 1
                units.append({"type": "manager", "messages": grouped})
                continue
            if role == "ai":
                unit_type = (
                    "notification"
                    if self._is_system_notification_message(message)
                    else "ai_answer"
                )
                units.append({"type": unit_type, "messages": [message]})
                index += 1
                continue
            units.append({"type": "tenant", "messages": [message]})
            index += 1
        return units

    def _is_system_notification_message(self, message):
        return is_assistant_system_notification(message)

    def _message_role(self, author):
        if author in AI_AUTHORS:
            return "ai"
        if author in MANAGER_PHONES:
            return "manager"
        return "tenant"

    def _display_name(self, author):
        if author in AI_AUTHORS:
            return "Virtual Assistant"
        return self.user_names.get(author) or "Unknown"

    def _unit_key(self, unit):
        return "|".join(str(message.message_sid) for message in unit["messages"])

    def _unit_body(self, unit):
        return "\n".join(message.body or "" for message in unit["messages"])

    # ---------------------------------------------------------------------
    # Unit processors
    # ---------------------------------------------------------------------
    def _process_unit(self, conversation, unit, index, total):
        body = self._unit_body(unit)
        first_message = unit["messages"][0]
        title = f"{unit['type'].upper()} Message {index}/{total}"
        if unit["type"] == "manager" and len(unit["messages"]) > 1:
            title += f" ({len(unit['messages'])} messages merged)"
        self.console.print(Text(LINE_SEPARATOR, style=self._unit_style(unit["type"])))
        self._print_panel(title, body, self._unit_style(unit["type"]))

        action = self._prompt("[bold]Action[/bold] [Enter=process, s=skip, q=quit]:", "")
        if action.lower() == "s":
            tags, notes = self._prompt_tags_notes()
            return self._message_json(unit, index, tags, notes, skipped=True)

        ai_data = {
            "answer": None,
            "no_answer": False,
            "why": None,
            "model": self.model,
            "saved_to_db": False,
        }
        kb_data = {
            "apartment": None,
            "global": None,
            "why": None,
            "saved_apartment": False,
            "saved_global": False,
        }

        if unit["type"] == "tenant":
            ai_data = self._process_tenant(conversation, first_message, body)
        elif unit["type"] == "manager":
            kb_data = self._process_manager(conversation, body)
        elif unit["type"] == "notification":
            self.console.print("[dim]System notification only; no AI processing.[/dim]")
        else:
            self.console.print(
                "[dim]Existing AI answer already sent in chat; no re-processing. "
                "Use tag to_delete to remove it.[/dim]"
            )

        tags, notes = self._prompt_tags_notes()
        deleted = False
        if "to_delete" in tags:
            deleted = self._delete_message_unit(conversation, unit)

        return self._message_json(
            unit,
            index,
            tags,
            notes,
            deleted=deleted,
            ai_data=ai_data,
            kb_data=kb_data,
        )

    def _process_tenant(self, conversation, message, body):
        data = {
            "answer": None,
            "no_answer": False,
            "error": None,
            "why_from_model": None,
            "why_analysis": None,
            "why": None,
            "model": self.model,
            "saved_to_db": False,
        }
        apartment = self._conversation_apartment(conversation)
        booking = conversation.booking
        if not apartment or not booking:
            data["why"] = "Skipped: conversation is missing apartment or booking."
            self.console.print(f"[yellow]{data['why']}[/yellow]")
            return data
        if _is_skippable_message(body):
            data["why"] = "Skipped by production short-message filter."
            self.console.print(f"[dim]{data['why']}[/dim]")
            return data

        self.console.print("[dim]Calling production ai_answer_customer (point-in-time history)...[/dim]")
        result = ai_answer_customer_detailed(
            conversation.conversation_sid,
            body,
            apartment,
            booking,
            history_before=message,
        )
        data["model"] = result.get("model") or self.model
        data["error"] = result.get("error")
        data["no_answer"] = bool(result.get("no_answer"))
        data["why_from_model"] = result.get("why")

        if data["error"]:
            data["answer"] = "API_ERROR"
            data["why"] = data["error"]
            data["why_analysis"] = data["error"]
            self._print_panel("AI Answer", data["answer"], "red")
            self.console.print(f"[red]why (analysis): {data['why_analysis']}[/red]")
            return data

        answer = result.get("answer")
        data["answer"] = answer or "NO_ANSWER"
        data["why_analysis"] = self._explain_tenant_answer(conversation, body, answer)
        data["why"] = data["why_analysis"]
        self._print_panel("AI Answer", data["answer"], "magenta")
        if data["why_from_model"]:
            self.console.print(f"[dim]why (from model): {data['why_from_model']}[/dim]")
        if data["why_analysis"]:
            self.console.print(f"[dim]why (analysis): {data['why_analysis']}[/dim]")

        if answer and self._confirm("Save AI answer to db? (not sent to chat)", default=False):
            if self.options.get("no_delete"):
                self.console.print("[yellow]--no-delete is active; answer was not saved.[/yellow]")
            else:
                message.ai_response = answer
                message.ai_response_why = data["why_from_model"]
                message.ai_sent_to_chat = False
                message.save(update_fields=['ai_response', 'ai_response_why', 'ai_sent_to_chat', 'updated_at'])
                data["saved_to_db"] = True
                self.console.print("[green]Saved ai_response with ai_sent_to_chat=False.[/green]")
        elif data["why_from_model"] and not self.options.get("no_delete"):
            message.ai_response_why = data["why_from_model"]
            message.save(update_fields=['ai_response_why', 'updated_at'])
        return data

    def _process_manager(self, conversation, body):
        data = {
            "apartment": None,
            "global": None,
            "why": None,
            "saved_apartment": False,
            "saved_global": False,
        }
        apartment = self._conversation_apartment(conversation)
        if not apartment:
            data["why"] = "Skipped: conversation is missing apartment context."
            self.console.print(f"[yellow]{data['why']}[/yellow]")
            return data

        result = self._manager_kb_preview(conversation.conversation_sid, body, apartment)
        data.update(result)
        if not result.get("has_value"):
            self.console.print("[dim]KNOWLEDGE BASE: nothing to add.[/dim]")
            if data.get("why"):
                self.console.print(f"[dim]why: {data['why']}[/dim]")
            return data

        kb_text = (
            f"apartment:\n{data.get('apartment') or 'none'}\n\n"
            f"global:\n{data.get('global') or 'none'}\n\n"
            f"why:\n{data.get('why') or 'none'}"
        )
        self._print_panel("KNOWLEDGE BASE", kb_text, "cyan")

        if data.get("apartment") and self._confirm(f"Save apartment KB for {apartment.name}?", False):
            if self.options.get("no_delete"):
                self.console.print("[yellow]--no-delete is active; apartment KB was not saved.[/yellow]")
            else:
                apartment.knowledge_base = data["apartment"]
                apartment.save()
                data["saved_apartment"] = True
                self.console.print("[green]Apartment knowledge base saved.[/green]")

        if data.get("global") and self._confirm("Save global KB entry?", False):
            if self.options.get("no_delete"):
                self.console.print("[yellow]--no-delete is active; global KB was not saved.[/yellow]")
            else:
                AIManagement.objects.create(
                    name=f"Chat replay knowledge {timezone.now().strftime('%Y-%m-%d %H:%M')}",
                    content=data["global"],
                    entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE,
                    description=f"Saved from chat replay {conversation.conversation_sid}",
                )
                data["saved_global"] = True
                self.console.print("[green]Global knowledge entry saved.[/green]")
        return data

    # ---------------------------------------------------------------------
    # AI helper calls
    # ---------------------------------------------------------------------
    def _manager_kb_preview(self, conversation_sid, message_body, apartment):
        ai_client, ai_error = _get_ai_client()
        if not ai_client:
            return {
                "has_value": False,
                "apartment": None,
                "global": None,
                "why": f"AI client unavailable: {ai_error}",
            }

        try:
            fields_ctx = _apartment_fields_context(apartment)
            check_content, _check_from_db = _get_prompt_with_source(
                "ai_extract_check", fields_ctx=fields_ctx, message_body=message_body
            )
            if not check_content:
                check_content = build_ai_extract_check_prompt(fields_ctx, message_body)

            check_response = ai_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": check_content}],
                temperature=0,
                max_tokens=5,
            )
            check_text = _safe_completion_content(check_response)
            if not check_text:
                return {
                    "has_value": False,
                    "apartment": None,
                    "global": None,
                    "why": "AI KB check returned empty content",
                }
            has_value = check_text.upper().startswith("YES")
            if not has_value and manager_message_has_operational_kb_hints(message_body):
                has_value = True
            if not has_value:
                why = self._short_ai_explain(
                    "Explain in one sentence why this manager message should not add knowledge base information:\n"
                    f"{message_body}"
                )
                return {"has_value": False, "apartment": None, "global": None, "why": why}

            kb_content = apartment.knowledge_base or "(empty)"
            merge_content, _merge_from_db = _get_prompt_with_source(
                "ai_extract_merge", knowledge_base=kb_content, message_body=message_body
            )
            if not merge_content:
                merge_content = build_ai_extract_merge_prompt(kb_content, message_body)
            update_response = ai_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": merge_content}],
                temperature=0.2,
                max_tokens=1200,
            )
            raw_merge = _safe_completion_content(update_response)
            if not raw_merge:
                return {
                    "has_value": False,
                    "apartment": None,
                    "global": None,
                    "why": "AI KB merge returned empty content",
                }
            updated_kb, changes = self._parse_kb_merge(raw_merge)
            if self._kb_merge_has_no_reusable_knowledge(updated_kb, kb_content, changes):
                why = self._short_ai_explain(
                    "Explain in one sentence why this manager message should not add knowledge base information:\n"
                    f"{message_body}"
                )
                return {"has_value": False, "apartment": None, "global": None, "why": why}
            classification = self._classify_kb_scope(message_body, updated_kb, changes)
            if classification.get("reusable") is False and not manager_message_has_operational_kb_hints(message_body):
                return {
                    "has_value": False,
                    "apartment": None,
                    "global": None,
                    "why": classification.get("why") or "One-time coordination, not reusable knowledge.",
                }
            return {
                "has_value": True,
                "apartment": updated_kb,
                "global": classification.get("global"),
                "why": classification.get("why") or changes,
                "changes": changes,
            }
        except Exception as exc:
            return {
                "has_value": False,
                "apartment": None,
                "global": None,
                "why": f"AI KB preview failed: {exc}",
            }

    def _parse_kb_merge(self, raw_merge):
        if "[UPDATED KB]" in raw_merge and "[CHANGES]" in raw_merge:
            kb_part = raw_merge.split("[UPDATED KB]", 1)[1]
            updated_kb, changes = kb_part.split("[CHANGES]", 1)
            return updated_kb.strip(), changes.strip()
        return raw_merge.strip(), None

    def _kb_merge_has_no_reusable_knowledge(self, updated_kb, original_kb, changes):
        if changes and changes.strip().lower().startswith("no reusable knowledge"):
            return True
        original = (original_kb or "").strip()
        updated = (updated_kb or "").strip()
        return bool(original) and updated == original

    def _classify_kb_scope(self, message_body, updated_kb, changes):
        prompt = (
            "You analyze property-management knowledge extracted from a group chat.\n"
            "Return JSON only with keys: global, reusable, why.\n"
            "global: reusable company-wide policy text, or empty string if apartment-specific.\n"
            "reusable: true if it is a standing property procedure or fact future tenants still need "
            "(access, parking how-it-works, house rules, WiFi/credentials, appliance operation); "
            "false only for this-stay coordination (times/ETAs, meetups, delivering an item now, "
            "personal contacts for one meeting). A standing rule said while handling a current request "
            "is still reusable=true. Credentials are reusable=true.\n"
            "Credentials such as WiFi passwords are reusable=true — sensitivity is NOT a reason to skip.\n\n"
            f"Manager message:\n{message_body}\n\n"
            f"Apartment knowledge base draft:\n{updated_kb}\n\n"
            f"Changes summary:\n{changes or 'none'}"
        )
        raw = self._short_ai_explain(prompt, max_tokens=300)
        parsed = self._parse_json_object(raw)
        if parsed:
            reusable = parsed.get("reusable")
            if isinstance(reusable, str):
                reusable = reusable.strip().lower() in {"true", "yes", "1"}
            elif reusable is None:
                reusable = True
            return {
                "global": parsed.get("global") or None,
                "reusable": reusable,
                "why": parsed.get("why") or None,
            }
        return {
            "global": None,
            "reusable": True,
            "why": raw,
        }

    def _explain_tenant_answer(self, conversation, message_body, answer):
        prompt = (
            "In one or two concise sentences, explain why the AI assistant gave this answer "
            "or why it returned NO_ANSWER. Mention missing context if relevant.\n\n"
            f"Conversation: {conversation.conversation_sid}\n"
            f"Tenant message: {message_body}\n"
            f"AI answer: {answer or 'NO_ANSWER'}"
        )
        return self._short_ai_explain(prompt)

    def _short_ai_explain(self, prompt, max_tokens=180):
        ai_client, ai_error = _get_ai_client()
        if not ai_client:
            return f"AI explanation unavailable: {ai_error}"
        models_to_try = [self.model]
        if self.model != LEGACY_CHAT_MODEL:
            models_to_try.append(LEGACY_CHAT_MODEL)
        last_error = None
        for model_name in models_to_try:
            try:
                response = ai_client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content.strip()
            except Exception as exc:
                last_error = str(exc)
                if "404" not in last_error and "No endpoints found" not in last_error:
                    break
        return f"AI explanation failed: {last_error}"

    def _parse_json_object(self, raw):
        if not raw:
            return None
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    # ---------------------------------------------------------------------
    # JSON shape helpers
    # ---------------------------------------------------------------------
    def _message_json(
        self,
        unit,
        index,
        tags,
        notes,
        deleted=False,
        skipped=False,
        ai_data=None,
        kb_data=None,
    ):
        first = unit["messages"][0]
        return {
            "key": self._unit_key(unit),
            "index": index,
            "type": unit["type"],
            "message_sids": [message.message_sid for message in unit["messages"]],
            "author": first.author,
            "author_name": self._display_name(first.author),
            "body": self._unit_body(unit),
            "timestamp": first.message_timestamp.isoformat(),
            "tags": tags,
            "notes": notes,
            "deleted": deleted,
            "skipped": skipped,
            "ai": ai_data
            or {
                "answer": None,
                "no_answer": False,
                "error": None,
                "why_from_model": None,
                "why_analysis": None,
                "why": None,
                "model": self.model,
                "saved_to_db": False,
            },
            "kb": kb_data
            or {
                "apartment": None,
                "global": None,
                "why": None,
                "saved_apartment": False,
                "saved_global": False,
            },
        }

    def _unit_style(self, unit_type):
        return {
            "notification": "yellow",
            "ai_answer": "magenta",
            "manager": "blue",
            "tenant": "green",
        }.get(unit_type, "white")

    def _has_text(self, value):
        return bool(value and str(value).strip())

    def _normalize_text(self, value):
        return " ".join(str(value or "").split())


def models_ai_filter():
    from django.db.models import Q

    return Q(ai_response__isnull=False) | Q(ai_response_why__isnull=False) | Q(ai_sent_to_chat__isnull=False) | Q(
        ai_kb_updated__isnull=False
    ) | Q(ai_kb_changes__isnull=False)
