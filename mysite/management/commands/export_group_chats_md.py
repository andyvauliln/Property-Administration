import os
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Prefetch
from django.utils import timezone

from mysite.models import TwilioConversation, TwilioMessage, User

try:
    from mysite.views.messaging import MANAGER_PHONES, TWILIO_ASSISTANT_PHONE
except Exception:
    MANAGER_PHONES = ("+15612205252", "+17282001917", "+15614603904")
    TWILIO_ASSISTANT_PHONE = "+13153524379"


AI_AUTHORS = {"ASSISTANT", "Virtual Assistant", TWILIO_ASSISTANT_PHONE}
STAR_SEPARATOR = "*" * 30
HASH_SEPARATOR = "#" * 30


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
        log_file = options["log_file"]

        context_index = self._parse_context_log(log_file)
        conversations = self._get_conversations()
        user_names = self._get_user_names(conversations)
        lines = self._render_document(conversations, user_names, context_index)

        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as export_file:
            export_file.write("\n".join(lines).rstrip() + "\n")

        message_count = sum(len(list(conversation.export_messages)) for conversation in conversations)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote group chat export to {output_path} "
                f"(conversations={len(conversations)}, messages={message_count})"
            )
        )

    def _get_conversations(self):
        messages_prefetch = Prefetch(
            "messages",
            queryset=TwilioMessage.objects.order_by("message_timestamp", "id"),
            to_attr="export_messages",
        )
        conversations = list(
            TwilioConversation.objects.select_related(
                "apartment",
                "booking",
                "booking__tenant",
                "booking__apartment",
            )
            .prefetch_related(messages_prefetch)
            .order_by("apartment__name", "booking_id", "conversation_sid")
        )
        return sorted(conversations, key=self._conversation_sort_key)

    def _conversation_sort_key(self, conversation):
        apartment = self._conversation_apartment(conversation)
        apartment_name = apartment.name if apartment else ""
        booking_id = conversation.booking_id or 0
        first_message = conversation.export_messages[0] if conversation.export_messages else None
        first_timestamp = first_message.message_timestamp if first_message else conversation.created_at
        return (apartment_name.lower(), booking_id, first_timestamp, conversation.conversation_sid)

    def _get_user_names(self, conversations):
        phones = set()
        for conversation in conversations:
            booking = conversation.booking
            if booking and booking.tenant and booking.tenant.phone:
                phones.add(booking.tenant.phone)
            for message in conversation.export_messages:
                if message.author:
                    phones.add(message.author)

        return {
            user.phone: user.full_name
            for user in User.objects.filter(phone__in=phones).only("phone", "full_name")
            if user.phone
        }

    def _render_document(self, conversations, user_names, context_index):
        lines = self._render_statistics(conversations, context_index)
        current_group = None

        for conversation in conversations:
            apartment = self._conversation_apartment(conversation)
            group_key = (apartment.id if apartment else None, conversation.booking_id)
            if current_group != group_key:
                if lines:
                    lines.extend([HASH_SEPARATOR])
                self._append_group_header(lines, conversation)
                current_group = group_key
            else:
                lines.append(STAR_SEPARATOR)

            lines.extend([f"**ConversationId:** `{conversation.conversation_sid}`", ""])
            self._append_conversation_messages(lines, conversation, user_names, context_index)

        return lines

    def _render_statistics(self, conversations, context_index):
        total_messages = sum(len(conversation.export_messages) for conversation in conversations)
        group_keys = set()
        conversations_with_messages = 0

        for conversation in conversations:
            apartment = self._conversation_apartment(conversation)
            group_keys.add((apartment.id if apartment else None, conversation.booking_id))
            if conversation.export_messages:
                conversations_with_messages += 1

        total_context_entries = sum(len(entries) for entries in context_index.values())

        return [
            "# Group Chat Export for AI Prompt Review",
            "",
            "## Statistics",
            f"- **Generated at:** `{timezone.now().isoformat()}`",
            "- **Rewrite behavior:** this file is overwritten on each command run",
            f"- **Total conversations:** `{len(conversations)}`",
            f"- **Total messages:** `{total_messages}`",
            f"- **Apartment/booking groups:** `{len(group_keys)}`",
            f"- **Conversations with messages:** `{conversations_with_messages}`",
            f"- **Conversations without messages:** `{len(conversations) - conversations_with_messages}`",
            f"- **AI context log entries available:** `{total_context_entries}`",
            "",
            "## Conversations",
            "",
        ]

    def _append_group_header(self, lines, conversation):
        apartment = self._conversation_apartment(conversation)
        apartment_name = apartment.name if apartment else "Unknown"
        booking_id = conversation.booking_id or "N/A"
        lines.extend(
            [
                f"**Apartment:** `{apartment_name}`",
                f"**Booking:** `{booking_id}`",
                STAR_SEPARATOR,
            ]
        )

    def _append_conversation_messages(self, lines, conversation, user_names, context_index):
        pending_ai_response_texts = []
        last_customer_body = None

        for message in conversation.export_messages:
            role = self._message_role(message.author)

            if role == "customer":
                last_customer_body = message.body
                self._append_customer_block(lines, message, user_names)
                if self._has_text(message.ai_response):
                    self._append_ai_block(
                        lines,
                        message.ai_response,
                        self._sent_label(message.ai_sent_to_chat),
                        message.ai_kb_changes,
                    )
                    self._append_context_block(lines, conversation.conversation_sid, message.body, context_index)
                    pending_ai_response_texts.append(self._normalize_text(message.ai_response))
                continue

            if role == "ai":
                normalized_body = self._normalize_text(message.body)
                if normalized_body and normalized_body in pending_ai_response_texts:
                    pending_ai_response_texts.remove(normalized_body)
                    continue
                self._append_ai_block(lines, message.body, "yes", message.ai_kb_changes)
                self._append_context_block(lines, conversation.conversation_sid, last_customer_body, context_index)
                continue

            if role == "manager":
                self._append_manager_block(lines, message, user_names)

    def _append_customer_block(self, lines, message, user_names):
        name = self._display_name(message.author, user_names)
        lines.append(f"**Customer Message** (`{message.author or 'N/A'}`) (`{name}`):")
        self._append_field(lines, "- **Message:** ", message.body)
        self._append_field(lines, "- **Added to knowledge base:** ", self._kb_text(message))

    def _append_ai_block(self, lines, message_text, sent, kb_changes):
        lines.append("**AI Answer Message:**")
        self._append_field(lines, "- **Message:** ", message_text or "none")
        lines.append(f"- **Sent:** `{sent}`")
        self._append_field(lines, "- **Added to knowledge base:** ", kb_changes or "none")

    def _append_context_block(self, lines, conversation_sid, customer_message, context_index):
        context_type, context = self._pop_context(context_index, conversation_sid, customer_message)
        lines.append("**Context:**")
        lines.append(f"- **Type:** `{context_type}`")
        self._append_field(lines, "- **Context:** ", context or "none")

    def _append_manager_block(self, lines, message, user_names):
        name = self._display_name(message.author, user_names)
        lines.append(f"**Manager Message** (`{message.author or 'N/A'}`) (`{name}`):")
        self._append_field(lines, "- **Message:** ", message.body)
        self._append_field(lines, "- **Added to knowledge base:** ", self._kb_text(message))

    def _append_field(self, lines, prefix, value):
        value = str(value or "none")
        value_lines = value.splitlines() or ["none"]
        lines.append(f"{prefix}{value_lines[0]}")
        for continuation in value_lines[1:]:
            lines.append(continuation)

    def _message_role(self, author):
        if author in AI_AUTHORS:
            return "ai"
        if author in MANAGER_PHONES:
            return "manager"
        return "customer"

    def _display_name(self, phone, user_names):
        if phone in AI_AUTHORS:
            return "Virtual Assistant"
        return user_names.get(phone) or "Unknown"

    def _conversation_apartment(self, conversation):
        if conversation.apartment:
            return conversation.apartment
        if conversation.booking and conversation.booking.apartment:
            return conversation.booking.apartment
        return None

    def _kb_text(self, message):
        if self._has_text(message.ai_kb_changes):
            return message.ai_kb_changes
        return "none"

    def _sent_label(self, sent_value):
        return "yes" if sent_value is True else "no"

    def _has_text(self, value):
        return bool(value and str(value).strip())

    def _normalize_text(self, value):
        return " ".join(str(value or "").split())

    def _parse_context_log(self, log_file):
        log_path = Path(log_file)
        if not log_path.exists():
            return defaultdict(list)

        try:
            raw_log = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return defaultdict(list)

        pattern = re.compile(
            r"AI CUSTOMER ANSWER \|.*?"
            r"Conversation:\s*(?P<conversation_sid>\S+).*?"
            r"=+\nTENANT MESSAGE\n-+\n(?P<tenant_message>.*?)\n\s*=+\n"
            r"CONTEXT \(sent to AI\)\n-+\n(?P<context>.*?)\n\s*=+\nSYSTEM PROMPT",
            re.DOTALL,
        )

        context_index = defaultdict(list)
        for match in pattern.finditer(raw_log):
            conversation_sid = match.group("conversation_sid").strip()
            context_index[conversation_sid].append(
                {
                    "tenant_message": match.group("tenant_message").strip(),
                    "context": match.group("context").strip(),
                    "used": False,
                }
            )
        return context_index

    def _pop_context(self, context_index, conversation_sid, customer_message):
        if not self._has_text(customer_message):
            return "no_context_in_logs", "none"

        entries = context_index.get(conversation_sid) or []
        entry = self._find_context_entry(entries, customer_message)
        if not entry:
            return "no_context_in_logs", "none"

        entry["used"] = True
        context = entry.get("context") or ""
        if not context or context == "(none)":
            return "empty", "none"
        return "exist", context

    def _find_context_entry(self, entries, customer_message):
        normalized_message = self._normalize_text(customer_message)
        for entry in entries:
            if entry["used"]:
                continue
            normalized_entry = self._normalize_text(entry.get("tenant_message"))
            if normalized_message and normalized_entry == normalized_message:
                return entry

        for entry in entries:
            if not entry["used"]:
                return entry

        return None
