from django.db.models import Max, Prefetch, Q

from mysite.models import TwilioConversation, TwilioMessage, User

try:
    from mysite.views.messaging import MANAGER_PHONE_NAMES, MANAGER_PHONES, TWILIO_ASSISTANT_PHONE
except Exception:
    MANAGER_PHONES = ("+15612205252", "+17282001917", "+15614603904", "+15618438867")
    MANAGER_PHONE_NAMES = {
        "+15618438867": "Janna",
    }
    TWILIO_ASSISTANT_PHONE = "+13153524379"


AI_AUTHORS = {"ASSISTANT", "Virtual Assistant", TWILIO_ASSISTANT_PHONE}


class GroupChatMarkdownExporter:
    def get_all_conversations(self):
        return self._load_conversations(TwilioConversation.objects.all())

    def get_list_conversations(self, search_query=""):
        queryset = self._chat_list_queryset(search_query)
        return self._load_conversations(queryset)

    def _chat_list_queryset(self, search_query=""):
        search_query = (search_query or "").strip()
        conversations_with_messages = TwilioConversation.objects.annotate(
            last_message_time=Max("messages__message_timestamp")
        ).filter(last_message_time__isnull=False)

        if search_query:
            conversations = TwilioConversation.objects.annotate(
                last_message_time=Max("messages__message_timestamp")
            )
            search_filters = (
                Q(booking__tenant__full_name__icontains=search_query)
                | Q(booking__tenant__phone__icontains=search_query)
                | Q(apartment__name__icontains=search_query)
                | Q(friendly_name__icontains=search_query)
                | Q(conversation_sid__icontains=search_query)
                | Q(messages__author__icontains=search_query)
            )
            if search_query.isdigit():
                search_filters |= Q(id=int(search_query))
            conversations = conversations.filter(search_filters).distinct()
        else:
            conversations = conversations_with_messages

        return conversations.order_by("-last_message_time")

    def _load_conversations(self, queryset):
        messages_prefetch = Prefetch(
            "messages",
            queryset=TwilioMessage.objects.order_by("message_timestamp", "id"),
            to_attr="export_messages",
        )
        conversations = list(
            queryset.select_related(
                "apartment",
                "booking",
                "booking__tenant",
                "booking__apartment",
            ).prefetch_related(messages_prefetch)
        )
        return sorted(conversations, key=self._conversation_sort_key)

    def _conversation_sort_key(self, conversation):
        apartment = self._conversation_apartment(conversation)
        apartment_name = apartment.name if apartment else ""
        booking_id = conversation.booking_id or 0
        first_message = conversation.export_messages[0] if conversation.export_messages else None
        first_timestamp = first_message.message_timestamp if first_message else conversation.created_at
        return (apartment_name.lower(), booking_id, first_timestamp, conversation.conversation_sid)

    def get_user_info(self, conversations):
        phones = set()
        for conversation in conversations:
            booking = conversation.booking
            if booking and booking.tenant and booking.tenant.phone:
                phones.add(booking.tenant.phone)
            for message in conversation.export_messages:
                if message.author:
                    phones.add(message.author)

        return {
            user.phone: {"name": user.full_name, "role": user.role}
            for user in User.objects.filter(phone__in=phones).only("phone", "full_name", "role")
            if user.phone
        }

    def render_markdown(self, conversations, search_query="", log_file=None):
        user_info = self.get_user_info(conversations)
        lines = []
        for index, conversation in enumerate(conversations):
            if index:
                lines.extend(["", "---", ""])
            lines.extend(self._render_conversation(conversation, user_info))
        return ("\n".join(lines).rstrip() + "\n") if lines else ""

    def _render_conversation(self, conversation, user_info):
        apartment = self._conversation_apartment(conversation)
        apartment_name = apartment.name if apartment else "Unknown"
        tenant_name = ""
        if conversation.booking and conversation.booking.tenant:
            tenant_name = self._short_name(conversation.booking.tenant.full_name)

        title = f"# {apartment_name}"
        if tenant_name:
            title += f" — {tenant_name}"
        if conversation.booking_id:
            title += f" (booking {conversation.booking_id})"

        lines = [title, ""]
        if conversation.export_messages:
            for message in conversation.export_messages:
                role = self._role_label(message.author, user_info)
                name = self._short_name(self._display_name(message.author, user_info))
                body = (message.body or "").replace('"', '\\"')
                lines.append(f'{role} [{name}]: "{body}"')
        else:
            lines.append("(no messages)")
        return lines

    def _role_label(self, author, user_info):
        if author in AI_AUTHORS:
            return "Assistant"
        if author in MANAGER_PHONES:
            return "Manager"
        info = user_info.get(author) or {}
        role = (info.get("role") or "").strip().lower()
        if role == "manager":
            return "Manager"
        if role == "admin":
            return "Manager"
        return "Tenant"

    def _display_name(self, phone, user_info):
        if phone in AI_AUTHORS:
            return "Assistant"
        info = user_info.get(phone) or {}
        return info.get("name") or MANAGER_PHONE_NAMES.get(phone) or "Unknown"

    def _short_name(self, full_name):
        name = (full_name or "").strip()
        if not name:
            return "Unknown"
        return name.split()[0]

    def _conversation_apartment(self, conversation):
        if conversation.apartment:
            return conversation.apartment
        if conversation.booking and conversation.booking.apartment:
            return conversation.booking.apartment
        return None
