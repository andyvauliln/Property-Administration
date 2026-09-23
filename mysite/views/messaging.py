
from django.views.decorators.http import require_http_methods
import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from django.views.decorators.csrf import csrf_exempt
import re
from mysite.unified_logger import log_error, log_info, log_warning, logger
from mysite.group_chat_logger import (
    log_message_received,
    log_ai_customer_start,
    log_ai_customer_skipped,
    log_ai_customer_full,
    log_ai_customer_no_answer,
    log_ai_customer_sent,
    log_ai_manager_start,
    log_ai_manager_check,
    log_ai_manager_merge,
    log_ai_manager_no_extract,
    log_ai_disabled,
    log_no_conv_link,
    log_new_group_created,
    log_message_forwarded,
    log_ai_error,
)
from django.http import JsonResponse
import json
import time
import twilio
from django.utils import timezone

KB_SUFFIX = "(+)"  # Virtual Assistant messages marked with (+) are processed for knowledge extraction
CLIENT_SUFFIX = "(+++)"  # Virtual Assistant messages marked with (+++) are treated as client (AI answers)
MANAGER_CHAT_SID = os.environ.get("MANAGER_CHAT_SID", "CH10c59b85e2ec4aad98e982916c495ea8")

TWILIO_ASSISTANT_PHONE = "+13153524379"
MANAGER_PHONES = ("+15612205252", "+17282001917", "+15614603904", "+15618438867")
MANAGER_PHONE_NAMES = {
    "+15618438867": "Janna",
}
RESERVED_PHONES = frozenset(MANAGER_PHONES + (TWILIO_ASSISTANT_PHONE,))

_manager_phones_cache = {'at': 0.0, 'phones': frozenset(MANAGER_PHONES)}


def get_manager_phones():
    """
    Phones that are STAFF, never a tenant: the hardcoded MANAGER_PHONES plus every active phone on
    /ai-staff/ (StaffMember). Adding a manager there needs no deploy. The hardcoded list stays as a
    safety net so a manager is never treated as a tenant; set STAFF_PHONES_FROM_TABLE_ONLY=true to
    drop it once the staff table is complete. Cached for 30 seconds, never raises.
    """
    now = time.monotonic()
    if now - _manager_phones_cache['at'] > 30:
        phones = set()
        try:
            from mysite.models import StaffMember
            for member in StaffMember.objects.filter(is_active=True):
                phones.update(member.phones)
        except Exception:
            phones = set()  # table not migrated yet / DB problem: fall back to the hardcoded list
        if not phones or os.environ.get('STAFF_PHONES_FROM_TABLE_ONLY', '').lower() != 'true':
            phones.update(MANAGER_PHONES)
        _manager_phones_cache.update(at=now, phones=frozenset(phones))
    return _manager_phones_cache['phones']


def _is_ai_assistant_globally_enabled():
    return os.environ.get('AI_ASSISTANT_ENABLED', 'true').lower() == 'true'


def _should_send_ai_to_group(apartment):
    return bool(apartment and apartment.ai_group_chat_enabled)


def _enqueue_for_ai_agent(conversation_sid, message_sid, body, **kwargs):
    """
    True when the Claude agent backend (AIManagement 'ai_backend' = claude_cli) queued the tenant
    message for the ai-agent worker; False means the legacy inline AI must handle it.
    """
    from mysite.ai_agent.service import enqueue_tenant_message
    return enqueue_tenant_message(conversation_sid, message_sid, body, **kwargs)


def _enqueue_staff_for_ai_agent(conversation_sid, message_sid, body, **kwargs):
    """Queues a manager's message for the Claude agent. Never replaces the knowledge-base extraction."""
    from mysite.ai_agent.service import enqueue_staff_message
    return enqueue_staff_message(conversation_sid, message_sid, body, **kwargs)


def _conversation_ai_group_chat_enabled(conversation_sid):
    try:
        from mysite.models import TwilioConversation
        conversation = (
            TwilioConversation.objects
            .select_related('apartment')
            .filter(conversation_sid=conversation_sid)
            .first()
        )
        return bool(conversation and conversation.apartment and conversation.apartment.ai_group_chat_enabled)
    except Exception:
        return False


def is_reserved_phone(phone):
    """True if phone matches a manager or the Twilio assistant number."""
    if not phone:
        return False
    from mysite.models import validate_and_format_phone
    formatted = validate_and_format_phone(phone)
    return formatted in RESERVED_PHONES or formatted in get_manager_phones()

# Unified logger throughout the app

# Initialize Twilio client with validation
def get_twilio_client():
    """Get or create Twilio client with proper credential validation"""
    try:
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        
        if not account_sid or not auth_token:
            raise ValueError("Twilio credentials are not set in environment variables")
        
        if account_sid.startswith("AC") and len(account_sid) == 34:
            # Valid account_sid format
            return Client(account_sid, auth_token)
        else:
            raise ValueError(f"Invalid Twilio Account SID format: {account_sid[:10]}...")
            
    except Exception as e:
        log_error(e, "Failed to initialize Twilio client", source='twilio')
        raise

# Initialize client at module level
try:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not account_sid or not auth_token:
        log_warning("Twilio credentials not found in environment variables", category='twilio')
        client = None
    else:
        client = Client(account_sid, auth_token)
except Exception as e:
    log_error(e, "Error initializing Twilio client", source='twilio')
    client = None



def save_conversation_to_db(conversation_sid, friendly_name, booking=None, apartment=None, author=None):
    """
    Save or get Twilio conversation in database with smart booking/apartment linking
    
    Args:
        conversation_sid (str): Twilio conversation SID
        friendly_name (str): Conversation friendly name
        booking (Booking): Optional booking object
        apartment (Apartment): Optional apartment object
        author (str): Optional message author for smart linking
    
    Returns:
        TwilioConversation: The conversation object
    """
    try:
        from mysite.models import TwilioConversation
        
        conversation, created = TwilioConversation.objects.get_or_create(
            conversation_sid=conversation_sid,
            defaults={
                'friendly_name': friendly_name,
                'booking': booking,
                'apartment': apartment
            }
        )
        
        if created:
            # Try to link booking/apartment for new customer conversations only
            if not booking and not apartment and author:
                # Only try to link if author is a customer (not system phones)
                twilio_phone = "+13153524379"
                manager_phone = "+15612205252"
                manager_phone_2 = "+17282001917"
                manager_phone_3 = "+15614603904"
                manager_phone_4 = "+15618438867"
                
                if author not in (twilio_phone, 'Virtual Assistant', 'ASSISTANT') and author not in get_manager_phones():
                    booking = get_booking_from_phone(author)
                    if booking:
                        conversation.booking = booking
                        conversation.apartment = booking.apartment
                        conversation.save()
                        log_info(
                            "Conversation linked to booking",
                            category='sms',
                            details={'conversation_sid': conversation_sid, 'booking_id': booking.id}
                        )
            
        return conversation
        
    except Exception as e:
        log_error(e, "Save Conversation to DB", source='web')
        return None


def update_conversation_booking_link(conversation_sid, phone_number):
    """
    Update an existing conversation with booking/apartment relationship
    Useful when booking is created after the conversation
    
    Args:
        conversation_sid (str): Twilio conversation SID
        phone_number (str): Phone number to find booking for
        
    Returns:
        bool: True if successfully updated, False otherwise
    """
    try:
        from mysite.models import TwilioConversation
        
        conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
        if not conversation:
            log_info(f"Conversation not found: {conversation_sid}", category='sms')
            return False
            
        # Skip if already linked
        if conversation.booking and conversation.apartment:
            log_info(f"Conversation already linked to booking: {conversation.booking}", category='sms')
            return True
            
        # Try to find booking
        booking = get_booking_from_phone(phone_number)
        if booking:
            conversation.booking = booking
            conversation.apartment = booking.apartment
            conversation.save()
            log_info(f"Updated conversation {conversation_sid} with booking: {booking} and apartment: {booking.apartment}", category='sms')
            return True
        else:
            log_info(f"No booking found for phone: {phone_number}", category='sms')
            return False
            
    except Exception as e:
        log_error(e, "Error updating conversation booking link", source='web')
        return False


def check_author_in_group_conversations_for_apartment(author_phone, apartment_id=None):
    """
    Check if the author exists in any group conversation for a specific apartment
    This helps handle multiple bookings for the same tenant
    
    Args:
        author_phone (str): Phone number to check
        apartment_id (int): Optional apartment ID to filter conversations
    
    Returns:
        bool: True if author exists in group conversations for this apartment
    """
    try:
        validated_phone = validate_phone_number(author_phone)
        if not validated_phone:
            log_info(f"Invalid phone number for group check: {author_phone}", category='sms')
            return False
            
        log_info(f"Checking if {validated_phone} exists in group conversations for apartment {apartment_id}", category='sms')
        
        # If apartment_id provided, check database first for more efficient lookup
        if apartment_id:
            from mysite.models import TwilioConversation
            
            # Check if there's already a conversation linked to this apartment with this tenant
            existing_conversation = TwilioConversation.objects.filter(
                apartment_id=apartment_id,
                messages__author=validated_phone
            ).first()
            
            if existing_conversation:
                log_info(f"Found existing conversation {existing_conversation.conversation_sid} for apartment {apartment_id}", category='sms')
                return True
        
        # Fallback to original Twilio API check for all group conversations
        return check_author_in_group_conversations(author_phone)
        
    except Exception as e:
        log_error(e, "Error in check_author_in_group_conversations_for_apartment", source='web')
        return check_author_in_group_conversations(author_phone)


def save_message_to_db(message_sid, conversation_sid, author, body, direction='inbound', 
                      webhook_sid=None, messaging_binding_address=None, 
                      messaging_binding_proxy_address=None):
    """
    Save Twilio message to database
    
    Args:
        message_sid (str): Twilio message SID
        conversation_sid (str): Twilio conversation SID
        author (str): Message author
        body (str): Message content
        direction (str): 'inbound' or 'outbound'
        webhook_sid (str): Optional webhook SID
        messaging_binding_address (str): Optional messaging binding address
        messaging_binding_proxy_address (str): Optional proxy address
    
    Returns:
        TwilioMessage: The message object or None if error
    """
    try:
        from mysite.models import TwilioConversation, TwilioMessage
        
        # Get or create conversation first
        conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
        if not conversation:
            # Create a basic conversation if it doesn't exist
            conversation = save_conversation_to_db(conversation_sid, f"Conversation {conversation_sid}")
            
        if conversation:
            message, created = TwilioMessage.objects.get_or_create(
                message_sid=message_sid,
                defaults={
                    'conversation': conversation,
                    'conversation_sid': conversation_sid,
                    'author': author,
                    'body': body,
                    'direction': direction,
                    'webhook_sid': webhook_sid,
                    'messaging_binding_address': messaging_binding_address,
                    'messaging_binding_proxy_address': messaging_binding_proxy_address,
                    'message_timestamp': timezone.now()
                }
            )
            
            if created:
                log_info(f"Saved message to DB: {message_sid} from {author}", category='sms')
            else:
                log_info(f"Message already exists in DB: {message_sid}", category='sms')
                
            return message
        else:
            log_info(f"Could not find or create conversation for message: {message_sid}", category='sms')
            return None
            
    except Exception as e:
        log_error(e, "Error saving message to DB", source='web')
        return None


def get_booking_from_phone(phone_number):
    """
    Try to find an active booking based on tenant phone number
    
    Args:
        phone_number (str): Phone number to search for
        
    Returns:
        Booking: Most recent booking or None
    """
    try:
        from mysite.models import Booking, User
        from datetime import date, timedelta
        
        # Validate and format phone number
        validated_phone = validate_phone_number(phone_number)
        if not validated_phone:
            return None
            
        # Find user with this phone number
        user = User.objects.filter(phone=validated_phone, role='Tenant').first()
        if not user:
            return None
            
        # Find most recent booking for this tenant (within last 90 days or future)
        cutoff_date = date.today() - timedelta(days=90)
        booking = Booking.objects.filter(
            tenant=user,
            end_date__gte=cutoff_date,
        ).exclude(status='Cancelled').order_by('-start_date').first()
        
        return booking
        
    except Exception as e:
        log_error(e, f"Error finding booking from phone {phone_number}", source='web')
        return None


def validate_phone_number(phone):
    """
    Validate and format phone number for Twilio.
    Returns None if invalid, formatted phone if valid.
    US numbers must be +1XXXXXXXXXX; fixes +5168490533 -> +15168490533.
    """
    if not phone:
        return None

    phone = str(phone).strip()
    digits_only = re.sub(r'\D', '', phone)

    # Numbers that already have + - handle before generic rules
    if phone.startswith('+'):
        if not re.match(r'^\+[1-9]\d{1,14}$', phone):
            return None
        # US missing country code 1: +5168490533 -> +15168490533 (assume US only)
        if len(digits_only) == 10 and digits_only[0] in '23456789':
            return f"+1{digits_only}"
        return phone

    # No + prefix: assume US if 10 or 11 digits
    if len(digits_only) == 10:
        return f"+1{digits_only}"
    if len(digits_only) == 11 and digits_only.startswith('1'):
        return f"+{digits_only}"

    return None



def _conversation_has_tenant_participant(conversation_sid, tenant_phone):
    """
    Verify that the tenant phone is a participant in the conversation.
    Used when reusing after 409 to avoid sending to wrong conversation
    (e.g. when 409 is due to manager/assistant reuse, not tenant).
    """
    validated = validate_phone_number(tenant_phone)
    if not validated:
        return False
    try:
        from mysite.models import TwilioMessage
        if TwilioMessage.objects.filter(conversation_sid=conversation_sid, author=validated).exists():
            return True
        global client
        if client is None:
            client = get_twilio_client()
        participants = client.conversations.v1.conversations(conversation_sid).participants.list()
        for p in participants:
            if hasattr(p, 'messaging_binding') and p.messaging_binding:
                addr = p.messaging_binding.get('address', '')
                if addr == validated:
                    return True
        return False
    except Exception as e:
        log_error(e, "Error checking tenant in conversation", source='twilio')
        return False


def create_conversation_with_participants(friendly_name, participants_config, tenant_phone_to_verify=None):
    """
    Create a conversation with multiple participants in a single API call
    
    Args:
        friendly_name (str): Name for the conversation
        participants_config (list): List of participant configurations
            Each participant can be:
            - {"phone": "+1234567890"} for phone number participants
            - {"identity": "ASSISTANT", "projected_address": "+1234567890"} for identity-based participants
        tenant_phone_to_verify (str): When 409 occurs, verify this tenant is in the reused conversation
            before returning. If not, re-raise to avoid sending to wrong conversation.
    
    Returns:
        str: conversation_sid of the created conversation
    """
    try:
        # Ensure client is initialized
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()
        
        participant_list = []
        
        for config in participants_config:
            # Validate phone numbers before creating JSON
            if "phone" in config:
                validated_phone = validate_phone_number(config["phone"])
                if not validated_phone:
                    log_info(f"Invalid phone number in config: {config}", category='sms')
                    continue
                config["phone"] = validated_phone
            
            if "projected_address" in config:
                validated_projected = validate_phone_number(config["projected_address"])
                if not validated_projected:
                    log_info(f"Invalid projected address in config: {config}", category='sms')
                    continue
                config["projected_address"] = validated_projected
            
            if "identity" in config and "projected_address" in config:
                # Identity-based participant with projected address
                participant_json = f'{{"identity": "{config["identity"]}", "messaging_binding": {{"projected_address": "{config["projected_address"]}"}}}}'
            elif "phone" in config:
                # Phone number participant
                participant_json = f'{{"messaging_binding": {{"address": "{config["phone"]}"}}}}'
            else:
                log_info(f"Invalid participant config: {config}", category='sms')
                continue
                
            participant_list.append(participant_json)
        
        log_info(f"Participant list: {participant_list}", category='sms')
        if not participant_list:
            raise ValueError("No valid participants provided")
        
        try:
            conversation_with_participant = client.conversations.v1.conversation_with_participants.create(
                friendly_name=friendly_name,
                participant=participant_list,
            )
            
            conversation_sid = conversation_with_participant.sid
            log_info(
                f'Created conversation "{friendly_name}" with {len(participant_list)} participants',
                category='sms',
                details={'conversation_sid': conversation_sid, 'participant_count': len(participant_list)}
            )
            
            return conversation_sid
            
        except TwilioRestException as e:
            # Handle duplicate conversation error (HTTP 409)
            if e.status == 409:
                # Extract existing conversation SID from error message
                # Pattern: "Conversation CH[32 alphanumeric chars]"
                match = re.search(r'Conversation (CH[a-z0-9]{32})', str(e))
                if match:
                    existing_sid = match.group(1)
                    if tenant_phone_to_verify:
                        if not _conversation_has_tenant_participant(existing_sid, tenant_phone_to_verify):
                            log_error(
                                Exception("409 reuse aborted: tenant not in conversation"),
                                "Wrong conversation reused after 409 - would send to different tenant",
                                source='twilio',
                                severity='high',
                                additional_info={
                                    'conversation_sid': existing_sid,
                                    'tenant_phone': tenant_phone_to_verify,
                                    'error': str(e)
                                }
                            )
                            raise
                    log_info(
                        f'Conversation already exists, reusing: {existing_sid}',
                        category='sms',
                        details={'conversation_sid': existing_sid, 'friendly_name': friendly_name}
                    )
                    return existing_sid
            # Re-raise if not a 409 or if we couldn't extract the SID
            raise
        
    except Exception as e:
        log_error(e, "Error creating conversation with participants", source='twilio')
        raise


def check_author_in_group_conversations(author_phone):
    """
    Check if the author exists in any conversation with more than 2 participants
    
    Args:
        author_phone (str): Phone number to check
    
    Returns:
        bool: True if author exists in group conversations (>2 participants)
    """
    try:
        validated_phone = validate_phone_number(author_phone)
        if not validated_phone:
            log_info(f"Invalid phone number for group check: {author_phone}", category='sms')
            return False
            
        log_info(f"Checking if {validated_phone} exists in group conversations", category='sms')
        
        # Ensure client is initialized
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()
        
        # Get all conversations
        conversations = client.conversations.v1.conversations.list()
        
        for conv in conversations:
            try:
                # Get participants for this conversation
                participants = client.conversations.v1.conversations(conv.sid).participants.list()
                participant_count = len(participants)
                
                log_info(f"Conversation {conv.sid} has {participant_count} participants", category='sms')
                
                # Check if this conversation has more than 2 participants
                if participant_count > 2:
                    # Check if our author is in this conversation
                    for participant in participants:
                        if hasattr(participant, 'messaging_binding') and participant.messaging_binding:
                            binding = participant.messaging_binding
                            participant_address = binding.get('address', '')
                            if participant_address == validated_phone:
                                log_info(f"Found author {validated_phone} in group conversation {conv.sid}", category='sms')
                                return True
                                
            except Exception as e:
                log_error(e, f"Error checking conversation {conv.sid}", source='twilio')
                continue
                
        log_info(f"Author {validated_phone} not found in any group conversations", category='sms')
        return False
        
    except Exception as e:
        log_error(e, "Error in check_author_in_group_conversations", source='twilio')
        return False


def forward_message_to_conversation(conversation_sid, author, message):
    """
    Forward a message to a specific conversation with formatted text
    
    Args:
        conversation_sid (str): Target conversation SID
        author (str): Original message author
        message (str): Original message content
    """
    try:
        # Ensure client is initialized
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()
            
        formatted_message = f">>Customer: {author} - {message}"
        
        # Send message to conversation using Virtual Assistant identity
        message_response = client.conversations.v1.conversations(conversation_sid).messages.create(
            author='Virtual Assistant',
            body=formatted_message
        )
        
        log_info(
            f"Forwarded message to conversation",
            category='sms',
            details={'conversation_sid': conversation_sid, 'message_sid': message_response.sid}
        )
        
        # Save outbound forwarded message to database
        save_message_to_db(
            message_sid=message_response.sid,
            conversation_sid=conversation_sid,
            author='Virtual Assistant',
            body=formatted_message,
            direction='outbound'
        )
        
        return message_response.sid
        
    except Exception as e:
        log_error(e, f"Error forwarding message to conversation {conversation_sid}", source='twilio')
        raise


def delete_conversation(conversation_sid):
    """
    Delete a conversation from Twilio (messages cascade).
    """
    try:
        global client
        if client is None:
            client = get_twilio_client()
        client.conversations.v1.conversations(conversation_sid).delete()
        log_info(f"Deleted conversation: {conversation_sid}", category='sms')
    except Exception as e:
        log_error(e, f"Error deleting conversation {conversation_sid}", source='twilio')
        raise


def delete_message(conversation_sid, message_sid):
    """
    Delete a single message from Twilio.
    """
    try:
        global client
        if client is None:
            client = get_twilio_client()
        client.conversations.v1.conversations(conversation_sid).messages(message_sid).delete()
        log_info(f"Deleted message: {message_sid}", category='sms')
    except Exception as e:
        log_error(e, f"Error deleting message {message_sid}", source='twilio')
        raise


def delete_all_messages(conversation_sid):
    """
    Delete all messages from Twilio for a conversation. Keeps the conversation.
    """
    try:
        global client
        if client is None:
            client = get_twilio_client()
        conv_resource = client.conversations.v1.conversations(conversation_sid)
        messages = list(conv_resource.messages.list())
        for msg in messages:
            try:
                conv_resource.messages(msg.sid).delete()
            except Exception as e:
                log_error(e, f"Error deleting message {msg.sid}", source='twilio')
        log_info(f"Deleted all messages from conversation: {conversation_sid}", category='sms')
    except Exception as e:
        log_error(e, f"Error deleting messages from {conversation_sid}", source='twilio')
        raise



def _is_skippable_message(text):
    """Returns True for short acknowledgment messages that don't need AI processing."""
    if not text:
        return True
    text = text.strip()
    return len(text) <= 3 and '?' not in text


def _extract_marked_body(text, marker):
    """
    Returns message content if marker is present as prefix or suffix.
    Examples:
      "(+) wifi issue" -> "wifi issue"
      "wifi issue (+)" -> "wifi issue"
    """
    if not text:
        return ""
    text = text.strip()
    if text.startswith(marker):
        return text[len(marker):].strip()
    if text.endswith(marker):
        return text[:-len(marker)].strip()
    return ""


def _get_ai_client():
    """Initialize OpenRouter client. Returns (client, error_reason)."""
    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENROUTER_API_KEY', '')
        if not api_key:
            return None, 'OPENROUTER_API_KEY is not set'
        return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key), None
    except Exception as e:
        log_error(e, "Failed to initialize AI client", source='web')
        return None, str(e)


DEFAULT_CHAT_MODEL = "openai/gpt-5.6-terra"
LEGACY_CHAT_MODEL = "openai/gpt-4o-mini"
RECOMMENDED_CHAT_MODELS = (
    ("openai/gpt-5.6-terra", "GPT-5.6 Terra (recommended for chat + KB)"),
    ("openai/gpt-5.6-luna", "GPT-5.6 Luna (fast, lower cost)"),
    ("openai/gpt-5.6-sol", "GPT-5.6 Sol (flagship)"),
    ("openai/gpt-5.5", "GPT-5.5"),
    ("openai/gpt-5.5-pro", "GPT-5.5 Pro (deep reasoning)"),
    ("openai/gpt-4o-mini", "GPT-4o Mini (legacy fallback)"),
)
DEPRECATED_CHAT_MODELS = frozenset({
    "openai/gpt-5.2-chat",
    "openai/gpt-5.2",
    "gpt-5.2-chat",
    "gpt-5.2",
})


def normalize_chat_model(model_slug, fallback=None):
    """Map deprecated/empty model slugs to the current default chat model."""
    fallback = fallback or DEFAULT_CHAT_MODEL
    slug = (model_slug or "").strip()
    if not slug or slug in DEPRECATED_CHAT_MODELS:
        return fallback
    return slug


def _get_db_model_raw():
    try:
        from mysite.models import AIManagement
        entry = AIManagement.objects.filter(prompt_key='ai_conversation_model').first()
        if entry and entry.content:
            return entry.content.strip()
    except Exception:
        pass
    return None


def resolve_chat_model(model_slug=None, fallback=None):
    """
    Resolve the chat model slug from AIManagement with deprecated slug auto-fix.
    Returns (resolved_model, replaced_from_or_none).
    """
    fallback = fallback or DEFAULT_CHAT_MODEL
    raw = model_slug if model_slug is not None else _get_db_model_raw()
    if not raw:
        return fallback, None
    resolved = normalize_chat_model(raw, fallback=fallback)
    if resolved != raw:
        return resolved, raw
    return resolved, None


def _get_db_model(fallback=None):
    """Return the AI model name set in AIManagement DB, or fallback."""
    fallback = fallback or DEFAULT_CHAT_MODEL
    resolved, _replaced = resolve_chat_model(fallback=fallback)
    return resolved


def _apartment_fields_context(apartment):
    """Returns all apartment structured fields as formatted text. Excludes notes, keywords, metadata."""
    owner_name = apartment.owner.full_name if apartment.owner else 'N/A'
    manager_names = ', '.join(m.full_name for m in apartment.managers.all()) or 'N/A'
    return (
        f"Name: {apartment.name}\n"
        f"Type: {apartment.apartment_type}\n"
        f"Status: {apartment.status}\n"
        f"Address: {apartment.address}\n"
        f"Building: {apartment.building_n}, Apt: {apartment.apartment_n or 'N/A'}\n"
        f"City: {apartment.city}, State: {apartment.state}, Zip: {apartment.zip_index}\n"
        f"Bedrooms: {apartment.bedrooms}, Bathrooms: {apartment.bathrooms}\n"
        f"Rating: {apartment.raiting}\n"
        f"Default price: ${apartment.default_price}\n"
        f"Web link: {apartment.web_link or 'N/A'}\n"
        f"Start date: {apartment.start_date or 'N/A'}, End date: {apartment.end_date or 'N/A'}\n"
        f"Owner: {owner_name}\n"
        f"Managers: {manager_names}"
    )


def build_full_context(conversation_sid, apartment, booking, history_before=None, include_history=True, now=None):
    """
    Full context for AI: apartment notes + structured fields + booking (all fields) +
    parking + cleanings + payments + handyman + recent chat history.
    Returns (context_str, context_sources) where context_sources describes what was included.
    Both apartment and booking are guaranteed non-None when called.

    history_before: optional TwilioMessage — include only chat messages strictly before this
    one (point-in-time replay). When omitted, uses the latest 10 messages in the conversation.
    include_history: False when the caller renders chat history itself (mysite.ai_agent.inputs).
    now: naive datetime to use as the current moment (replay / team timezone); defaults to server time.
    """
    from mysite.models import ParkingBooking, HandymanCalendar, Cleaning, Payment, TwilioMessage, AIManagement
    from datetime import date

    from datetime import datetime
    parts = []
    context_sources = {}
    now_label = "as given by caller" if now is not None else "local server time"
    now = now or datetime.now()
    today = now.date()
    parts.append(f"=== CURRENT DATE & TIME ===\n{now.strftime('%A, %B %d, %Y %H:%M')} ({now_label})")

    # Global knowledge base (only knowledge entries, not prompts)
    global_kb_entries = AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE
    )
    global_kb_texts = [entry.content for entry in global_kb_entries if entry.content and entry.content.strip()]
    context_sources["global_kb"] = bool(global_kb_texts)
    if global_kb_texts:
        parts.append(f"=== GLOBAL KNOWLEDGE BASE ===\n" + "\n\n".join(global_kb_texts))

    # Apartment knowledge base
    has_apt_kb = bool(apartment.knowledge_base and apartment.knowledge_base.strip())
    context_sources["apartment_kb"] = has_apt_kb
    if has_apt_kb:
        parts.append(f"=== APARTMENT KNOWLEDGE BASE ===\n{apartment.knowledge_base}")

    # Apartment structured fields (always)
    context_sources["apartment_fields"] = True
    parts.append(f"=== APARTMENT FIELDS DATA ===\n{_apartment_fields_context(apartment)}")

    # Booking — all fields except keywords/metadata (always)
    context_sources["booking"] = True
    tenant = booking.tenant
    car_info = ""
    if booking.is_rent_car:
        car_info = f", {booking.car_model} for {booking.car_rent_days} days at ${booking.car_price}"
    parts.append(
        f"=== CURRENT BOOKING ===\n"
        f"Tenant: {tenant.full_name if tenant else 'N/A'}\n"
        f"Tenant phone: {tenant.phone if tenant else 'N/A'}\n"
        f"Check-in: {booking.start_date}, Check-out: {booking.end_date}\n"
        f"Status: {booking.status}\n"
        f"Tenants count: {booking.tenants_n or 'N/A'}\n"
        f"Animals: {booking.animals or 'N/A'}\n"
        f"Visit purpose: {booking.visit_purpose or 'N/A'}\n"
        f"Source: {booking.source or 'N/A'}\n"
        f"Other tenants: {booking.other_tenants or 'N/A'}\n"
        f"Notes: {booking.notes or 'N/A'}\n"
        f"Contract status: {booking.contract_send_status}\n"
        f"Car rental: {'Yes' if booking.is_rent_car else 'No'}{car_info}"
    )

    # Parking booked for this booking
    parking = ParkingBooking.objects.filter(booking=booking).select_related('parking')
    context_sources["parking"] = parking.exists()
    if parking.exists():
        lines = [
            f"- Spot #{p.parking.number} ({p.parking.notes or ''}, building: {p.parking.building or 'N/A'})"
            for p in parking
        ]
        parts.append("=== PARKING ===\n" + "\n".join(lines))

    # Cleanings for this booking
    cleanings = Cleaning.objects.filter(booking=booking).order_by('date')[:5]
    context_sources["cleanings"] = cleanings.exists()
    if cleanings.exists():
        lines = [f"- {c.date}: {c.status}" for c in cleanings]
        parts.append("=== CLEANINGS ===\n" + "\n".join(lines))

    # Payments for this booking — all records with payment type
    payments = Payment.objects.filter(booking=booking).select_related('payment_type').order_by('-payment_date')
    context_sources["payments"] = payments.exists()
    if payments.exists():
        lines = [
            f"- {p.payment_date}: ${p.amount} ({p.payment_status})"
            f" | Type: {p.payment_type.name if p.payment_type else 'N/A'}"
            for p in payments
        ]
        parts.append("=== BOOKING PAYMENTS ===\n" + "\n".join(lines))

    # Handyman appointments for this tenant
    context_sources["handyman"] = False
    if tenant and tenant.phone:
        handyman = HandymanCalendar.objects.filter(
            tenant_phone=tenant.phone, date__gte=today
        ).order_by('date')[:3]
        if handyman.exists():
            context_sources["handyman"] = True
            lines = [f"- {h.date} {h.start_time}-{h.end_time}: {h.notes}" for h in handyman]
            parts.append("=== HANDYMAN APPOINTMENTS ===\n" + "\n".join(lines))

    # Recent chat history (up to 10 messages available at history_before, or latest 10)
    from django.db.models import Q

    history_qs = TwilioMessage.objects.filter(conversation_sid=conversation_sid)
    if history_before is not None:
        history_qs = history_qs.filter(
            Q(message_timestamp__lt=history_before.message_timestamp)
            | Q(message_timestamp=history_before.message_timestamp, id__lt=history_before.id)
        )
    messages = history_qs.order_by("-message_timestamp", "-id")[:10]
    context_sources["chat_history"] = messages.exists()
    context_sources["chat_history_point_in_time"] = history_before is not None
    if include_history and messages.exists():
        history = [
            f"[{m.message_timestamp.strftime('%Y-%m-%d %H:%M')}] {'Customer' if m.direction == 'inbound' else 'Assistant'}: {m.body}"
            for m in reversed(list(messages))
        ]
        parts.append("=== RECENT CHAT HISTORY ===\n" + "\n".join(history))

    return "\n\n".join(parts), context_sources


def _get_prompt(prompt_key, **placeholders):
    """Load prompt from AIManagement, format with placeholders, or return None for fallback."""
    content, _from_db = _get_prompt_with_source(prompt_key, **placeholders)
    return content


def _normalize_message_text(value):
    return " ".join(str(value or "").split()).strip().lower()


def is_assistant_system_notification(message):
    """
    True for automated ASSISTANT/chat-template messages, not conversational AI replies.
    Includes contract links, booking intros, contract-signed payment prompts, welcome msgs.
    """
    sid = getattr(message, "message_sid", None) or ""
    if sid.startswith("KB-UPDATE-"):
        return True
    body = (getattr(message, "body", None) or "").strip()
    if not body:
        return False
    normalized = _normalize_message_text(body)
    lower = body.lower()

    from mysite.models import AIManagement

    for entry in AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_SMS_TEMPLATE
    ).exclude(content__isnull=True).exclude(content=""):
        template = entry.content.strip()
        if not template:
            continue
        if "{" not in template:
            if _normalize_message_text(template) == normalized:
                return True
            continue
        static_prefix = template.split("{", 1)[0].strip().lower()
        if len(static_prefix) >= 20 and static_prefix in lower:
            return True

    markers = (
        "docuseal.com",
        "please sign it here",
        "please sign this contract",
        "this is your contract for booking",
        "this chat for booking property",
        "this is chat for booking apartment",
        "to continue with a booking, please sign",
        "how do you want to make payment for the hold deposit",
        "hold deposits may be paid",
        "credit card payments incur",
    )
    return any(marker in lower for marker in markers)


def _get_template(template_key, **placeholders):
    """Load SMS/chat template from AIManagement (entry_type=sms_template), format with placeholders, or return None."""
    from mysite.models import AIManagement
    entry = AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_SMS_TEMPLATE,
        prompt_key=template_key
    ).first()
    if entry and entry.content and entry.content.strip():
        try:
            return entry.content.format(**placeholders)
        except KeyError:
            log_warning(f"Template {template_key} has missing placeholders", category='sms')
            return None
    return None


def _get_prompt_with_source(prompt_key, **placeholders):
    """
    Load prompt from AIManagement. Returns (content, from_db).
    content: formatted prompt string or None for fallback.
    from_db: True if loaded from DB, False if fallback.
    """
    from mysite.models import AIManagement
    entry = AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_PROMPT,
        prompt_key=prompt_key
    ).first()
    if entry and entry.content and entry.content.strip():
        try:
            return entry.content.format(**placeholders), True
        except KeyError:
            log_warning(f"Prompt {prompt_key} has missing placeholders", category='sms')
            return None, False
    return None, False


def _update_message_ai_result(message_sid, **kwargs):
    """Update TwilioMessage AI metadata fields after processing."""
    if not message_sid:
        return
    if not kwargs:
        return
    try:
        from mysite.audit_bulk import audit_queryset_update
        from mysite.models import TwilioMessage
        from mysite.signals import get_current_user_info

        audit_queryset_update(
            TwilioMessage.objects.filter(message_sid=message_sid),
            changed_by=get_current_user_info(),
            **kwargs,
        )
    except Exception as e:
        log_error(e, "Error updating message AI metadata", source='web')


AI_ANSWER_RESPONSE_FORMAT = (
    "Always respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[ANSWER]\n"
    "<concise answer, one clarifying question, or NO_ANSWER only>\n"
    "[WHY]\n"
    "<1-2 sentences explaining why you chose this answer or NO_ANSWER, "
    "what context you used, or what information is missing>"
)


AI_ANSWER_SYSTEM_PROMPT = (
    "You are an AI assistant for a property management company in a group chat with the tenant and managers.\n\n"
    "Your job is to answer ONLY when you can give a factual, low-risk answer from context.\n"
    "You are NOT the property manager. You do NOT schedule meetings, confirm appointments, negotiate payments, "
    "or speak for managers.\n\n"
    "For each tenant message, choose ONE of:\n\n"
    "1. ANSWER - You have enough verified info in context for a short factual reply (max 3 sentences).\n"
    "   Examples: WiFi/password from KB, check-in/out dates from booking, apartment address, documented house rules.\n\n"
    "2. CLARIFY - ONLY for missing factual apartment details (NOT scheduling or payments).\n"
    "   Ask ONE short question. Never use CLARIFY for meeting time, place, or who will meet the tenant.\n\n"
    "3. NO_ANSWER - Use when ANY of these apply:\n"
    "   - Scheduling or logistics: meetups, times, places, hour-away updates, tomorrow works, availability, ETAs\n"
    "   - Payments handled in person: checks, deposits, down payment drop-off, who to pay, where to meet to pay\n"
    "   - Tenant is talking TO a manager by name (e.g. Hey Kevin) or updating managers on arrival\n"
    "   - Recent chat shows managers are actively handling this thread (manager message in last 5 messages)\n"
    "   - Tenant message is only acknowledgment: ok, thanks, Liked ..., great, emoji reactions\n"
    "   - Tenant asks manager to decide something (you tell me a time and where)\n"
    "   - You would need to invent time, place, person, phone, or agreement not explicitly in context\n"
    "   - Coordination between tenant and staff unless fully documented in KB\n\n"
    "RULES:\n"
    "- Never fabricate - only use facts from the context.\n"
    "- Never propose a meeting location or time unless a manager ALREADY stated it in RECENT CHAT HISTORY "
    "and the tenant only needs a brief confirmation repeat.\n"
    "- Do not say we will coordinate, someone will meet you, or I will let the team know - "
    "that implies a commitment managers must make.\n"
    "- Do not greet with Hello on every message if the conversation is already ongoing.\n"
    "- Be friendly and professional.\n"
    "- Answer in the same language as the tenant's message.\n"
    "- Put ONLY tenant-facing text in [ANSWER]. Put NO_ANSWER in [ANSWER] when staying silent.\n\n"
    f"{AI_ANSWER_RESPONSE_FORMAT}"
)


def _fallback_ai_answer_system_prompt():
    return AI_ANSWER_SYSTEM_PROMPT


def _fallback_ai_answer_user_prompt(context, message_body):
    return (
        f"Context:\n{context}\n\n"
        f"Tenant message: {message_body}\n\n"
        "Respond using the required [ANSWER] / [WHY] format from the system prompt."
    )


AI_ANSWER_USER_TEMPLATE = (
    "Context:\n{context}\n\n"
    "Tenant message: {message_body}\n\n"
    "Respond using the required [ANSWER] / [WHY] format from the system prompt."
)


def is_customer_message(message):
    """True for inbound tenant messages and UI client messages marked with (+++)."""
    body = (message.body or '').strip()
    if not body:
        return False
    if _extract_marked_body(body, CLIENT_SUFFIX):
        return True
    author = (message.author or '').strip()
    if author in ('ASSISTANT', 'Virtual Assistant', TWILIO_ASSISTANT_PHONE):
        return False
    if author in get_manager_phones():
        return False
    return message.direction == 'inbound'


def get_customer_message_body(message):
    body = (message.body or '').strip()
    extracted = _extract_marked_body(body, CLIENT_SUFFIX)
    return extracted or body


def generate_customer_ai_answer_for_message(
    message,
    conversation=None,
    apartment=None,
    booking=None,
    save=True,
):
    """
    Generate (or regenerate) AI tenant answer for one customer message.
    Uses production ai_answer_customer_detailed with point-in-time history.
    Saves to message.ai_response fields only — never sends to SMS.
    """
    from mysite.models import Apartment, Booking

    conversation = conversation or message.conversation
    if not conversation.apartment_id or not conversation.booking_id:
        return {'success': False, 'error': 'Conversation must be linked to an apartment and booking.'}
    if not is_customer_message(message):
        return {'success': False, 'error': 'Not a customer message.'}

    body = get_customer_message_body(message)
    if _is_skippable_message(body):
        return {
            'success': False,
            'error': 'Message too short for AI processing.',
            'skipped': True,
            'message_id': message.id,
        }

    if apartment is None:
        apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(
            id=conversation.apartment_id,
        )
    if booking is None:
        booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)

    result = ai_answer_customer_detailed(
        conversation.conversation_sid,
        body,
        apartment,
        booking,
        history_before=message,
    )
    if result.get('error'):
        return {
            'success': False,
            'error': result['error'],
            'message_id': message.id,
        }

    answer = result.get('answer')
    why = result.get('why')
    no_answer = bool(result.get('no_answer'))

    if save:
        message.ai_response = answer
        message.ai_response_why = why
        message.ai_sent_to_chat = False
        message.save(update_fields=['ai_response', 'ai_response_why', 'ai_sent_to_chat', 'updated_at'])

    return {
        'success': True,
        'message_id': message.id,
        'ai_response': answer,
        'ai_response_why': why,
        'ai_sent_to_chat': False,
        'no_answer': no_answer,
        'message_timestamp': message.message_timestamp.isoformat() if message.message_timestamp else None,
    }


def generate_all_customer_ai_answers(conversation_sid):
    from mysite.models import TwilioConversation, Apartment, Booking

    conversation = TwilioConversation.objects.select_related(
        'apartment', 'booking', 'booking__tenant',
    ).filter(conversation_sid=conversation_sid).first()
    if not conversation:
        return {'success': False, 'error': 'Conversation not found.'}
    if not conversation.apartment_id or not conversation.booking_id:
        return {'success': False, 'error': 'Conversation must be linked to an apartment and booking.'}

    apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(
        id=conversation.apartment_id,
    )
    booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)
    messages = list(conversation.messages.order_by('message_timestamp', 'id'))

    results = []
    processed = 0
    skipped = 0
    errors = []
    for msg in messages:
        if not is_customer_message(msg):
            continue
        item = generate_customer_ai_answer_for_message(
            msg, conversation=conversation, apartment=apartment, booking=booking, save=True,
        )
        results.append(item)
        if item.get('skipped'):
            skipped += 1
        elif item.get('success'):
            processed += 1
        elif item.get('error'):
            errors.append(f"#{msg.id}: {item['error']}")

    return {
        'success': True,
        'processed': processed,
        'skipped': skipped,
        'total_customer_messages': len([m for m in messages if is_customer_message(m)]),
        'results': results,
        'errors': errors[:20],
    }


AI_EXTRACT_CHECK_TEMPLATE = (
    "You evaluate whether a manager's message contains REUSABLE OPERATIONAL knowledge "
    "about an apartment that should be saved in free-text notes for FUTURE tenants.\n\n"
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be flagged as new — do not save it to notes:\n"
    "{fields_ctx}\n\n"
    "Recent chat history before this message (read-only context to understand the manager message — "
    "do NOT extract knowledge from history, only from the manager message below):\n"
    "{chat_history}\n\n"
    "IMPORTANT: Extract knowledge ONLY from the manager message at the end. "
    "Ignore chat history, tenant messages, and any automated system notifications.\n\n"
    "RULES:\n"
    "- YES if the message states a standing procedure or fact that would still be true for the next tenant "
    "(how to access the property, how parking works, house rules, WiFi/credentials, how something operates, "
    "stable local tips). Save it even when it was said while handling a current request.\n"
    "- WiFi names/passwords and access codes MUST be saved; do not skip them as too sensitive.\n"
    "- NO only if the message is solely this-stay coordination: a specific time/ETA, meetup, payment drop-off, "
    "offering to bring or deliver an item now, a personal contact for one meeting, or an acknowledgment "
    "with no lasting procedure.\n"
    "- A current request can still contain a standing rule. If the manager explains how the property works, "
    "that is YES. If they only arrange a one-off action, that is NO.\n\n"
    "Manager message to evaluate: {message_body}\n\n"
    "Reply using EXACTLY this format (keep the markers on their own lines):\n"
    "[DECISION]\n"
    "YES or NO\n"
    "[SUGGESTED]\n"
    "<concise standing facts to save for future tenants — only if YES; otherwise \"none\">"
)

AI_EXTRACT_MERGE_TEMPLATE = (
    "Current apartment knowledge base:\n{knowledge_base}\n\n"
    "Manager message (ONLY source for new facts — do not use anything else):\n{message_body}\n\n"
    "Our system detected the following possible reusable knowledge from this manager message:\n"
    "{suggested_knowledge}\n\n"
    "Merge ONLY facts from the manager message / suggested knowledge above. "
    "Do not add facts from chat history or automated notifications. "
    "Merge into the knowledge base only if not already covered. "
    "Do not duplicate existing facts. Keep the result clear and organized.\n\n"
    "If nothing new to add, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)

AI_EXTRACT_GLOBAL_CHECK_TEMPLATE = (
    "You evaluate whether a manager's message contains REUSABLE GLOBAL guidance for how the "
    "Virtual Assistant should communicate across all properties and bookings.\n\n"
    "Recent chat history before this message (read-only context to understand the manager message — "
    "do NOT extract knowledge from history, only from the manager message below):\n"
    "{chat_history}\n\n"
    "IMPORTANT: Extract knowledge ONLY from the manager message at the end. "
    "Ignore chat history, tenant messages, and any automated system notifications.\n\n"
    "Global KB is NOT for apartment or booking facts. Reply YES only for guidance that applies everywhere:\n"
    "- How to answer certain question types (patterns: what to say, what not to say, when to use NO_ANSWER)\n"
    "- Communication style (tone, greetings policy, language, professionalism)\n"
    "- Company-wide policies not tied to one unit (accepted payment methods, deposit/hold rules, company procedures)\n"
    "- Reusable response patterns for common tenant situations across properties\n\n"
    "Reply NO — these belong in apartment KB or nowhere:\n"
    "- This apartment's WiFi, door/gate codes, parking for one unit, appliances, address, unit house rules\n"
    "- This booking's dates, tenant name, prices, meetup times, ETAs, one-off coordination\n"
    "- Greetings, acknowledgments, or content with no reusable assistant guidance\n\n"
    "If a message mixes apartment-specific facts with global guidance, reply YES only when there is "
    "clear global assistant guidance — put only that in [SUGGESTED], not apartment details.\n\n"
    "Manager message to evaluate: {message_body}\n\n"
    "Reply using EXACTLY this format (keep the markers on their own lines):\n"
    "[DECISION]\n"
    "YES or NO\n"
    "[SUGGESTED]\n"
    "<concise global assistant guidance (how to answer/style/policy) — only if YES; otherwise \"none\">"
)

AI_EXTRACT_GLOBAL_MERGE_TEMPLATE = (
    "Current global knowledge base:\n{knowledge_base}\n\n"
    "Manager message (ONLY source for new facts — do not use anything else):\n{message_body}\n\n"
    "Our system detected the following possible global assistant guidance from this manager message:\n"
    "{suggested_knowledge}\n\n"
    "Global KB stores ONLY company-wide guidance: how to answer question types, communication style, "
    "and policies that are NOT tied to one apartment or booking.\n\n"
    "Merge ONLY that global guidance from the manager message / suggested knowledge above. "
    "Do NOT add apartment-specific facts (WiFi, codes, unit details, parking for one property) "
    "or booking-specific facts (dates, tenant names, meetups, ETAs). "
    "Do not add facts from chat history or automated notifications. "
    "Merge only if not already covered. Do not duplicate. Keep clear and organized.\n\n"
    "If nothing new to add, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated global knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)


def manager_message_has_operational_kb_hints(message_body):
    """Detect obvious operational KB content the extract check should not skip."""
    text = (message_body or "").lower()
    if not text:
        return False
    patterns = (
        r"wi-?fi",
        r"\bssid\b",
        r"network\s*name",
        r"door\s*code",
        r"gate\s*code",
        r"lock\s*code",
        r"access\s*code",
        r"entry\s*code",
        r"\bparking\b",
        r"\bgarage\b",
        r"house\s*rule",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def build_ai_extract_check_prompt(fields_ctx, message_body, chat_history='(none)'):
    return AI_EXTRACT_CHECK_TEMPLATE.format(
        fields_ctx=fields_ctx,
        message_body=message_body,
        chat_history=chat_history,
    )


def build_ai_extract_merge_prompt(knowledge_base, message_body, suggested_knowledge=''):
    return AI_EXTRACT_MERGE_TEMPLATE.format(
        knowledge_base=knowledge_base,
        message_body=message_body,
        suggested_knowledge=suggested_knowledge or message_body,
    )


def build_ai_extract_global_check_prompt(message_body, chat_history='(none)'):
    return AI_EXTRACT_GLOBAL_CHECK_TEMPLATE.format(
        message_body=message_body,
        chat_history=chat_history,
    )


def build_ai_extract_global_merge_prompt(knowledge_base, message_body, suggested_knowledge=''):
    return AI_EXTRACT_GLOBAL_MERGE_TEMPLATE.format(
        knowledge_base=knowledge_base,
        message_body=message_body,
        suggested_knowledge=suggested_knowledge or message_body,
    )


KB_EXTRACT_APARTMENT_CHECK_KEY = 'ai_extract_check'
KB_EXTRACT_APARTMENT_MERGE_KEY = 'ai_extract_merge'
KB_EXTRACT_GLOBAL_CHECK_KEY = 'ai_extract_global_check'
KB_EXTRACT_GLOBAL_MERGE_KEY = 'ai_extract_global_merge'
KB_EXTRACT_PROMPT_KEYS = (
    KB_EXTRACT_APARTMENT_CHECK_KEY,
    KB_EXTRACT_APARTMENT_MERGE_KEY,
    KB_EXTRACT_GLOBAL_CHECK_KEY,
    KB_EXTRACT_GLOBAL_MERGE_KEY,
)
# Backward-compatible aliases used by chat modal prompt editor URLs
KB_GENERATE_APARTMENT_PROMPT_KEY = KB_EXTRACT_APARTMENT_MERGE_KEY
KB_GENERATE_GLOBAL_PROMPT_KEY = KB_EXTRACT_GLOBAL_MERGE_KEY
KB_GENERATE_PROMPT_KEYS = KB_EXTRACT_PROMPT_KEYS

AI_ANSWER_SYSTEM_KEY = 'ai_answer_system'
AI_ANSWER_USER_KEY = 'ai_answer_user'
AI_ANSWER_RULE_GENERATE_KEY = 'ai_answer_rule_generate'
AI_KB_RULE_GENERATE_KEY = 'ai_kb_rule_generate'
AI_ANSWER_PROMPT_KEYS = (AI_ANSWER_SYSTEM_KEY, AI_ANSWER_USER_KEY)
AI_ANSWER_RULE_PROMPT_KEYS = (AI_ANSWER_RULE_GENERATE_KEY,)
AI_KB_RULE_PROMPT_KEYS = (AI_KB_RULE_GENERATE_KEY,)
CHAT_EDITABLE_PROMPT_KEYS = (
    KB_EXTRACT_PROMPT_KEYS + AI_ANSWER_PROMPT_KEYS + AI_ANSWER_RULE_PROMPT_KEYS + AI_KB_RULE_PROMPT_KEYS
)

AI_ANSWER_RULE_GENERATE_TEMPLATE = (
    "You write one concise standing rule for a property-management AI assistant system prompt.\n"
    "The rule should help the assistant handle similar tenant messages correctly in the future.\n\n"
    "Tenant message:\n{client_message}\n\n"
    "Correct answer the assistant should give:\n{correct_answer}\n\n"
    "{ai_response_section}"
    "Write ONE bullet rule (starting with '- ') that is reusable for similar situations.\n"
    "Encode the decision or policy — do not paste the full answer text.\n"
    "Return only the rule line, nothing else."
)

AI_KB_RULE_GENERATE_TEMPLATE = (
    "You write one concise standing rule for a property-management KNOWLEDGE BASE extraction check prompt.\n"
    "The rule teaches when to reply YES (save to KB) or NO (skip) during automated KB analysis.\n"
    "Target: {scope_label}\n\n"
    "Manager message:\n{manager_message}\n\n"
    "User guidance — what should be added to or excluded from the knowledge base:\n{guidance}\n\n"
    "{kb_context_section}"
    "Rule intent: {rule_intent_label}\n\n"
    "Write ONE bullet rule (starting with '- ') for the CHECK prompt RULES section.\n"
    "For exclude rules, phrase when to reply NO or what must never be saved.\n"
    "For include rules, phrase when to reply YES or what must always be saved.\n"
    "Do not paste the full manager message — encode a reusable extraction policy.\n"
    "Return only the rule line, nothing else."
)

KB_RULE_SCOPE_LABELS = {
    'apartment': 'Apartment knowledge base (property-specific facts)',
    'global': 'Global knowledge base (company-wide assistant guidance, not unit-specific)',
}

KB_RULE_INTENT_LABELS = {
    'include': 'INCLUDE — manager messages like this should be saved to the knowledge base',
    'exclude': 'EXCLUDE — manager messages like this must NOT be saved to the knowledge base',
}

_PROMPT_FALLBACKS = {
    KB_EXTRACT_APARTMENT_CHECK_KEY: AI_EXTRACT_CHECK_TEMPLATE,
    KB_EXTRACT_APARTMENT_MERGE_KEY: AI_EXTRACT_MERGE_TEMPLATE,
    KB_EXTRACT_GLOBAL_CHECK_KEY: AI_EXTRACT_GLOBAL_CHECK_TEMPLATE,
    KB_EXTRACT_GLOBAL_MERGE_KEY: AI_EXTRACT_GLOBAL_MERGE_TEMPLATE,
    AI_ANSWER_SYSTEM_KEY: AI_ANSWER_SYSTEM_PROMPT,
    AI_ANSWER_USER_KEY: AI_ANSWER_USER_TEMPLATE,
    AI_ANSWER_RULE_GENERATE_KEY: AI_ANSWER_RULE_GENERATE_TEMPLATE,
    AI_KB_RULE_GENERATE_KEY: AI_KB_RULE_GENERATE_TEMPLATE,
}

_PROMPT_DEFAULT_NAMES = {
    KB_EXTRACT_APARTMENT_CHECK_KEY: 'AI Extract Check',
    KB_EXTRACT_APARTMENT_MERGE_KEY: 'AI Extract Merge',
    KB_EXTRACT_GLOBAL_CHECK_KEY: 'AI Extract Global Check',
    KB_EXTRACT_GLOBAL_MERGE_KEY: 'AI Extract Global Merge',
    AI_ANSWER_SYSTEM_KEY: 'AI Answer System',
    AI_ANSWER_USER_KEY: 'AI Answer User',
    AI_ANSWER_RULE_GENERATE_KEY: 'AI Answer Rule Generate',
    AI_KB_RULE_GENERATE_KEY: 'AI KB Rule Generate',
}

_PROMPT_DEFAULT_DESCRIPTIONS = {
    KB_EXTRACT_APARTMENT_CHECK_KEY: 'Apartment KB check. Placeholders: {fields_ctx}, {chat_history}, {message_body}',
    KB_EXTRACT_APARTMENT_MERGE_KEY: 'Apartment KB merge. Placeholders: {knowledge_base}, {message_body}, {suggested_knowledge}',
    KB_EXTRACT_GLOBAL_CHECK_KEY: 'Global KB check. Placeholders: {chat_history}, {message_body}. YES only for global answer/style guidance, not apartment or booking facts.',
    KB_EXTRACT_GLOBAL_MERGE_KEY: 'Global KB merge. Placeholders: {knowledge_base}, {message_body}, {suggested_knowledge}. Merge only global assistant guidance.',
    AI_ANSWER_SYSTEM_KEY: 'System prompt for tenant AI answers. No placeholders.',
    AI_ANSWER_USER_KEY: 'User prompt for tenant AI answers. Placeholders: {context}, {message_body}',
    AI_ANSWER_RULE_GENERATE_KEY: (
        'Generate one rule for ai_answer_system. Placeholders: {client_message}, {correct_answer}, {ai_response_section}'
    ),
    AI_KB_RULE_GENERATE_KEY: (
        'Generate KB extract check rule. Placeholders: {scope_label}, {manager_message}, {guidance}, '
        '{kb_context_section}, {rule_intent_label}'
    ),
}


def get_ai_prompt_template(prompt_key):
    """Return raw prompt template from DB, or built-in fallback for known KB extract keys."""
    from mysite.models import AIManagement

    entry = AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_PROMPT,
        prompt_key=prompt_key,
    ).first()
    if entry and entry.content and entry.content.strip():
        return entry.content.strip(), True, entry.description or ''
    if prompt_key in _PROMPT_FALLBACKS:
        return _PROMPT_FALLBACKS[prompt_key], False, _PROMPT_DEFAULT_DESCRIPTIONS.get(prompt_key, '')
    return None, False, ''


def save_ai_prompt_template(prompt_key, content, description=None):
    from mysite.models import AIManagement

    if prompt_key not in CHAT_EDITABLE_PROMPT_KEYS:
        raise ValueError(f'Unsupported prompt key: {prompt_key}')
    entry, _created = AIManagement.objects.update_or_create(
        prompt_key=prompt_key,
        defaults={
            'name': _PROMPT_DEFAULT_NAMES[prompt_key],
            'content': (content or '').strip(),
            'entry_type': AIManagement.ENTRY_TYPE_PROMPT,
            'description': description if description is not None else _PROMPT_DEFAULT_DESCRIPTIONS[prompt_key],
        },
    )
    return entry


def sync_kb_extract_prompts_to_db():
    """Force-sync all four KB extract prompts from built-in templates into AIManagement."""
    from mysite.models import AIManagement

    for prompt_key in KB_EXTRACT_PROMPT_KEYS:
        content = _PROMPT_FALLBACKS[prompt_key]
        AIManagement.objects.update_or_create(
            prompt_key=prompt_key,
            defaults={
                'name': _PROMPT_DEFAULT_NAMES[prompt_key],
                'content': content,
                'entry_type': AIManagement.ENTRY_TYPE_PROMPT,
                'description': _PROMPT_DEFAULT_DESCRIPTIONS[prompt_key],
            },
        )


def _format_ai_response_section(ai_response):
    ai_response = (ai_response or '').strip()
    if not ai_response:
        return ''
    return f'Previous AI answer (for reference):\n{ai_response}\n\n'


def generate_answer_rule_text(client_message, correct_answer, ai_response=None, generate_prompt=None):
    """Use AI to draft one system-prompt rule from a tenant message and correct answer."""
    client_message = (client_message or '').strip()
    correct_answer = (correct_answer or '').strip()
    if not client_message:
        return {'success': False, 'error': 'Client message is required.'}
    if not correct_answer:
        return {'success': False, 'error': 'Correct answer is required.'}

    ai_response_section = _format_ai_response_section(ai_response)
    if generate_prompt and generate_prompt.strip():
        try:
            prompt_content = generate_prompt.format(
                client_message=client_message,
                correct_answer=correct_answer,
                ai_response_section=ai_response_section,
            )
        except KeyError as exc:
            return {'success': False, 'error': f'Generate prompt missing placeholder: {exc}'}
    else:
        formatted, from_db = _get_prompt_with_source(
            AI_ANSWER_RULE_GENERATE_KEY,
            client_message=client_message,
            correct_answer=correct_answer,
            ai_response_section=ai_response_section,
        )
        if formatted:
            prompt_content = formatted
        else:
            prompt_content = AI_ANSWER_RULE_GENERATE_TEMPLATE.format(
                client_message=client_message,
                correct_answer=correct_answer,
                ai_response_section=ai_response_section,
            )

    ai_client, ai_error = _get_ai_client()
    if not ai_client:
        return {'success': False, 'error': f'AI client unavailable: {ai_error}'}

    model = _get_db_model()
    try:
        response = ai_client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt_content}],
            temperature=0.2,
            max_tokens=250,
        )
        rule = _safe_completion_content(response).strip()
        if not rule:
            return {'success': False, 'error': 'AI returned empty rule.'}
        if not rule.startswith('-'):
            rule = f'- {rule.lstrip("- ").strip()}'
        return {'success': True, 'rule': rule}
    except Exception as exc:
        log_error(exc, 'generate_answer_rule_text failed', source='web')
        return {'success': False, 'error': str(exc)}


def append_rule_to_ai_answer_system(rule_text):
    """Append one bullet rule to ai_answer_system prompt in DB."""
    rule_text = (rule_text or '').strip()
    if not rule_text:
        raise ValueError('Rule is empty.')
    if not rule_text.startswith('-'):
        rule_text = f'- {rule_text}'

    content, _, _ = get_ai_prompt_template(AI_ANSWER_SYSTEM_KEY)
    if not content:
        content = AI_ANSWER_SYSTEM_PROMPT

    marker = 'Always respond using EXACTLY this format'
    if marker in content:
        head, tail = content.split(marker, 1)
        updated = head.rstrip() + f'\n{rule_text}\n\n' + marker + tail
    else:
        updated = content.rstrip() + f'\n{rule_text}\n'

    entry = save_ai_prompt_template(AI_ANSWER_SYSTEM_KEY, updated)
    return entry.content or updated


def get_answer_rule_message_body(message):
    """Tenant message text for the answer Add Rule modal."""
    return get_customer_message_body(message)


def is_answer_rule_eligible_message(message):
    """Only customer messages with AI answers use the answer rule flow."""
    return is_customer_message(message)


def get_kb_manager_message_body(message):
    """Manager message text for the KB Add Rule modal."""
    body = _extract_marked_body(message.body, KB_SUFFIX) or message.body
    return (body or '').strip()


def is_kb_rule_eligible_message(message):
    """Only manager KB source messages use the KB rule flow."""
    return should_run_kb_extraction_for_message(message)


def get_answer_rule_modal_context(message):
    """Context for the Add Rule modal on a customer message (tenant AI answers)."""
    if not is_answer_rule_eligible_message(message):
        return None
    generate_prompt, _, generate_description = get_ai_prompt_template(AI_ANSWER_RULE_GENERATE_KEY)
    if not generate_prompt:
        generate_prompt = AI_ANSWER_RULE_GENERATE_TEMPLATE
    return {
        'message_id': message.id,
        'client_message': get_answer_rule_message_body(message),
        'ai_response': message.ai_response or '',
        'ai_response_why': message.ai_response_why or '',
        'generate_prompt': generate_prompt,
        'generate_prompt_description': generate_description,
    }


def _normalize_kb_rule_scope(scope):
    scope = (scope or 'apartment').strip().lower()
    return scope if scope in KB_RULE_SCOPE_LABELS else 'apartment'


def _normalize_kb_rule_intent(rule_intent):
    rule_intent = (rule_intent or 'include').strip().lower()
    return rule_intent if rule_intent in KB_RULE_INTENT_LABELS else 'include'


def _format_kb_rule_context_section(kb_changes):
    kb_changes = (kb_changes or '').strip()
    if not kb_changes:
        return ''
    return f'Previous KB analysis note:\n{kb_changes}\n\n'


def get_kb_rule_modal_context(message):
    """Context for the Add KB Rule modal on a manager message."""
    if not is_kb_rule_eligible_message(message):
        return None
    generate_prompt, _, generate_description = get_ai_prompt_template(AI_KB_RULE_GENERATE_KEY)
    if not generate_prompt:
        generate_prompt = AI_KB_RULE_GENERATE_TEMPLATE
    return {
        'message_id': message.id,
        'manager_message': get_kb_manager_message_body(message),
        'kb_changes': message.ai_kb_changes or '',
        'generate_prompt': generate_prompt,
        'generate_prompt_description': generate_description,
        'default_scope': 'apartment',
        'default_rule_intent': 'include',
    }


def generate_kb_rule_text(
    manager_message,
    guidance,
    scope='apartment',
    rule_intent='include',
    kb_changes=None,
    generate_prompt=None,
):
    """Use AI to draft one KB extract check rule from a manager message and user guidance."""
    manager_message = (manager_message or '').strip()
    guidance = (guidance or '').strip()
    scope = _normalize_kb_rule_scope(scope)
    rule_intent = _normalize_kb_rule_intent(rule_intent)
    if not manager_message:
        return {'success': False, 'error': 'Manager message is required.'}
    if not guidance:
        return {'success': False, 'error': 'Guidance is required.'}

    scope_label = KB_RULE_SCOPE_LABELS[scope]
    rule_intent_label = KB_RULE_INTENT_LABELS[rule_intent]
    kb_context_section = _format_kb_rule_context_section(kb_changes)

    if generate_prompt and generate_prompt.strip():
        try:
            prompt_content = generate_prompt.format(
                scope_label=scope_label,
                manager_message=manager_message,
                guidance=guidance,
                kb_context_section=kb_context_section,
                rule_intent_label=rule_intent_label,
            )
        except KeyError as exc:
            return {'success': False, 'error': f'Generate prompt missing placeholder: {exc}'}
    else:
        formatted, _from_db = _get_prompt_with_source(
            AI_KB_RULE_GENERATE_KEY,
            scope_label=scope_label,
            manager_message=manager_message,
            guidance=guidance,
            kb_context_section=kb_context_section,
            rule_intent_label=rule_intent_label,
        )
        if formatted:
            prompt_content = formatted
        else:
            prompt_content = AI_KB_RULE_GENERATE_TEMPLATE.format(
                scope_label=scope_label,
                manager_message=manager_message,
                guidance=guidance,
                kb_context_section=kb_context_section,
                rule_intent_label=rule_intent_label,
            )

    ai_client, ai_error = _get_ai_client()
    if not ai_client:
        return {'success': False, 'error': f'AI client unavailable: {ai_error}'}

    model = _get_db_model()
    try:
        response = ai_client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt_content}],
            temperature=0.2,
            max_tokens=250,
        )
        rule = _safe_completion_content(response).strip()
        if not rule:
            return {'success': False, 'error': 'AI returned empty rule.'}
        if not rule.startswith('-'):
            rule = f'- {rule.lstrip("- ").strip()}'
        return {'success': True, 'rule': rule, 'scope': scope, 'rule_intent': rule_intent}
    except Exception as exc:
        log_error(exc, 'generate_kb_rule_text failed', source='web')
        return {'success': False, 'error': str(exc)}


def append_rule_to_kb_extract_check(rule_text, scope='apartment'):
    """Append one bullet rule to apartment or global KB check prompt in DB."""
    rule_text = (rule_text or '').strip()
    if not rule_text:
        raise ValueError('Rule is empty.')
    if not rule_text.startswith('-'):
        rule_text = f'- {rule_text}'

    scope = _normalize_kb_rule_scope(scope)
    if scope == 'global':
        prompt_key = KB_EXTRACT_GLOBAL_CHECK_KEY
        fallback = AI_EXTRACT_GLOBAL_CHECK_TEMPLATE
    else:
        prompt_key = KB_EXTRACT_APARTMENT_CHECK_KEY
        fallback = AI_EXTRACT_CHECK_TEMPLATE

    content, _, _ = get_ai_prompt_template(prompt_key)
    if not content:
        content = fallback

    marker = 'Manager message to evaluate:'
    if marker in content:
        head, tail = content.split(marker, 1)
        updated = head.rstrip() + f'\n{rule_text}\n\n' + marker + tail
    else:
        updated = content.rstrip() + f'\n{rule_text}\n'

    entry = save_ai_prompt_template(prompt_key, updated)
    return entry.content or updated, prompt_key


def _build_kb_author_labels(conversation):
    from mysite.models import User

    labels = {}
    tenant_phone = None
    tenant_name = None
    if conversation.booking and conversation.booking.tenant:
        tenant_phone = (conversation.booking.tenant.phone or '').strip()
        tenant_name = (conversation.booking.tenant.full_name or '').strip()

    phones = set(get_manager_phones())
    if tenant_phone:
        phones.add(tenant_phone)
    for author in conversation.messages.values_list('author', flat=True).distinct():
        author = (author or '').strip()
        if author.startswith('+'):
            phones.add(author)

    users_by_phone = {}
    if phones:
        for user in User.objects.filter(phone__in=phones).only('phone', 'full_name'):
            users_by_phone[(user.phone or '').strip()] = user

    def label_for(author):
        author = (author or '').strip()
        if author in ('ASSISTANT', 'Virtual Assistant'):
            return f'{TWILIO_ASSISTANT_PHONE} (Assistant)'
        if author in get_manager_phones():
            user = users_by_phone.get(author)
            name = (user.full_name or '').strip() if user and user.full_name else MANAGER_PHONE_NAMES.get(author, 'Manager')
            return f'{author} ({name})'
        if tenant_phone and author == tenant_phone:
            name = tenant_name or 'Customer'
            return f'{author} ({name})'
        if author.startswith('+'):
            user = users_by_phone.get(author)
            name = (user.full_name or '').strip() if user and user.full_name else 'Unknown'
            return f'{author} ({name})'
        return author or 'Unknown'

    for author in conversation.messages.values_list('author', flat=True).distinct():
        author = (author or '').strip()
        if author:
            labels[author] = label_for(author)
    for key in tuple(get_manager_phones()) + ('ASSISTANT', 'Virtual Assistant'):
        labels[key] = label_for(key)
    return labels


def _format_chat_history_for_kb(conversation, history_before=None):
    """Author-labeled chat lines strictly before history_before; excludes notifications."""
    from django.db.models import Q
    from mysite.models import TwilioMessage

    qs = TwilioMessage.objects.filter(conversation=conversation)
    if history_before is not None:
        qs = qs.filter(
            Q(message_timestamp__lt=history_before.message_timestamp)
            | Q(message_timestamp=history_before.message_timestamp, id__lt=history_before.id)
        )
    messages = qs.order_by('message_timestamp', 'id')
    author_labels = _build_kb_author_labels(conversation)
    lines = []
    for msg in messages:
        if is_assistant_system_notification(msg):
            continue
        if (getattr(msg, 'message_sid', None) or '').startswith('KB-UPDATE-'):
            continue
        author_raw = (msg.author or '').strip()
        if author_raw in ('ASSISTANT', 'Virtual Assistant', TWILIO_ASSISTANT_PHONE):
            continue
        body = (msg.body or '').strip()
        if not body:
            continue
        author = author_labels.get((msg.author or '').strip(), (msg.author or '').strip() or 'Unknown')
        timestamp = msg.message_timestamp.strftime('%Y-%m-%d %H:%M') if msg.message_timestamp else ''
        lines.append(f'[{timestamp}] {author}: {body}')
    return '\n'.join(lines) if lines else '(none)'


def _parse_kb_check(raw_check):
    """Parse structured check output into (has_value, suggested_knowledge)."""
    raw = (raw_check or '').strip()
    if not raw:
        return False, None

    if re.search(r'\[DECISION\]', raw, re.IGNORECASE):
        rest = re.split(r'\[DECISION\]', raw, flags=re.IGNORECASE, maxsplit=1)[1]
        suggested = None
        if re.search(r'\[SUGGESTED\]', rest, re.IGNORECASE):
            decision_part, suggested_part = re.split(
                r'\[SUGGESTED\]', rest, flags=re.IGNORECASE, maxsplit=1
            )
            decision = decision_part.strip()
            suggested = suggested_part.strip()
        else:
            decision = rest.strip()
        has_value = decision.upper().startswith('YES')
        if suggested and suggested.lower() in {'none', 'n/a', '-', 'no'}:
            suggested = None
        return has_value, suggested or None

    first_line = raw.splitlines()[0].strip().upper()
    if first_line.startswith('YES'):
        trailing = '\n'.join(raw.splitlines()[1:]).strip()
        return True, trailing or None
    if first_line.startswith('NO'):
        return False, None
    return False, None


def _resolve_suggested_knowledge(suggested, message_body):
    suggested = (suggested or '').strip()
    if suggested and suggested.lower() not in {'none', 'n/a', '-'}:
        return suggested
    return (message_body or '').strip()


def _run_kb_extract_check(conversation_sid, prompt_key, build_fallback, has_hint_fn, message_body, **prompt_kwargs):
    ai_client, ai_error = _get_ai_client()
    if not ai_client:
        raise RuntimeError(f'AI client unavailable: {ai_error}')

    format_kwargs = dict(prompt_kwargs, message_body=message_body)
    check_content, check_from_db = _get_prompt_with_source(prompt_key, **format_kwargs)
    if not check_content:
        check_content = build_fallback(**format_kwargs)
        check_from_db = False
    model = _get_db_model()
    check_response = ai_client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': check_content}],
        temperature=0,
        max_tokens=400,
    )
    check_text = _safe_completion_content(check_response)
    has_value, suggested = _parse_kb_check(check_text)
    if not has_value and has_hint_fn and has_hint_fn(message_body):
        has_value = True
    log_ai_manager_check(
        conversation_sid, check_content, model, has_value,
        prompt_source=f'DB:{prompt_key}' if check_from_db else 'fallback',
    )
    return has_value, suggested


def _run_kb_extract_merge(conversation_sid, prompt_key, build_fallback, original_kb, message_body, **prompt_kwargs):
    ai_client, ai_error = _get_ai_client()
    if not ai_client:
        return {
            'kb': original_kb,
            'changes': None,
            'saved': False,
            'error': f'AI client unavailable: {ai_error}',
        }

    kb_content = original_kb or '(empty)'
    suggested_knowledge = _resolve_suggested_knowledge(
        prompt_kwargs.get('suggested_knowledge'),
        message_body,
    )
    merge_content, merge_from_db = _get_prompt_with_source(
        prompt_key,
        knowledge_base=kb_content,
        message_body=message_body,
        suggested_knowledge=suggested_knowledge,
    )
    if not merge_content:
        merge_content = build_fallback(
            knowledge_base=kb_content,
            message_body=message_body,
            suggested_knowledge=suggested_knowledge,
        )
        merge_from_db = False
    model = _get_db_model()
    update_response = ai_client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': merge_content}],
        temperature=0.2,
        max_tokens=2000,
    )
    raw_merge = _safe_completion_content(update_response)
    if not raw_merge:
        return {
            'kb': original_kb,
            'changes': None,
            'saved': False,
            'error': 'AI merge returned empty content',
        }

    updated_kb, changes = _parse_kb_merge(raw_merge)
    saved = bool(updated_kb) and updated_kb != (original_kb or '')
    if _kb_merge_has_no_reusable_knowledge(updated_kb, kb_content, changes):
        saved = False
    return {
        'kb': updated_kb if saved else (original_kb or ''),
        'changes': changes,
        'saved': saved,
        'error': None,
        'merge_content': merge_content,
        'model': model,
        'prompt_source': f'DB:{prompt_key}' if merge_from_db else 'fallback',
    }


def extract_knowledge_from_manager_message(
    conversation_sid,
    message_body,
    apartment,
    conversation=None,
    history_before=None,
    save=True,
    apartment_kb_draft=None,
    global_kb_draft=None,
):
    """
    Unified KB extraction for live chat and modal replay.
    Uses ai_extract_check/merge (apartment) and ai_extract_global_check/merge (global).
    Chat history = messages strictly before history_before, excluding notifications.
    """
    from mysite.models import TwilioConversation

    if conversation is None:
        conversation = TwilioConversation.objects.select_related(
            'booking', 'booking__tenant',
        ).filter(conversation_sid=conversation_sid).first()

    chat_history = _format_chat_history_for_kb(conversation, history_before) if conversation else '(none)'
    apartment_kb = apartment_kb_draft if apartment_kb_draft is not None else (apartment.knowledge_base or '')
    global_kb = global_kb_draft if global_kb_draft is not None else get_global_knowledge_base_text()

    result = {
        'apartment_kb': apartment_kb,
        'global_kb': global_kb,
        'apartment_saved': False,
        'global_saved': False,
        'apartment_changes': None,
        'global_changes': None,
        'why': [],
        'error': None,
    }

    try:
        ai_client, ai_client_error = _get_ai_client()
        if not ai_client:
            result['error'] = ai_client_error
            return result

        fields_ctx = _apartment_fields_context(apartment)
        apt_has_value, apt_suggested = _run_kb_extract_check(
            conversation_sid,
            KB_EXTRACT_APARTMENT_CHECK_KEY,
            lambda **kw: build_ai_extract_check_prompt(fields_ctx, message_body, chat_history),
            manager_message_has_operational_kb_hints,
            message_body,
            fields_ctx=fields_ctx,
            chat_history=chat_history,
        )
        if apt_has_value:
            apt_suggested_knowledge = _resolve_suggested_knowledge(apt_suggested, message_body)
            apt_merge = _run_kb_extract_merge(
                conversation_sid,
                KB_EXTRACT_APARTMENT_MERGE_KEY,
                lambda knowledge_base, message_body, suggested_knowledge, **kw: build_ai_extract_merge_prompt(
                    knowledge_base, message_body, suggested_knowledge
                ),
                apartment_kb,
                message_body,
                suggested_knowledge=apt_suggested_knowledge,
            )
            if apt_merge.get('error'):
                result['why'].append(f'Apartment: {apt_merge["error"]}')
            elif apt_merge.get('saved'):
                result['apartment_kb'] = apt_merge['kb']
                result['apartment_changes'] = apt_merge.get('changes')
                if save:
                    apartment.knowledge_base = apt_merge['kb']
                    apartment.save(update_fields=['knowledge_base', 'updated_at'])
                    result['apartment_saved'] = True
                    try:
                        from uuid import uuid4
                        changes_summary = apt_merge.get('changes')
                        notification = (
                            f'📚 Knowledge base updated: {changes_summary}'
                            if changes_summary else '📚 Knowledge base updated.'
                        )
                        save_message_to_db(
                            message_sid=f'KB-UPDATE-{uuid4().hex}',
                            conversation_sid=conversation_sid,
                            author='Virtual Assistant',
                            body=notification,
                            direction='outbound',
                        )
                    except Exception as notify_err:
                        log_error(notify_err, 'Failed to save KB update notification to DB', source='web')
                log_ai_manager_merge(
                    conversation_sid=conversation_sid,
                    apartment_id=apartment.id,
                    knowledge_base_before=apartment_kb or '(empty)',
                    message_body=message_body,
                    merge_content=apt_merge.get('merge_content', ''),
                    model=apt_merge.get('model', _get_db_model()),
                    updated_notes=apt_merge.get('kb'),
                    saved=True,
                    prompt_source=apt_merge.get('prompt_source', 'fallback'),
                )
            elif apt_merge.get('changes'):
                result['why'].append(f'Apartment: {apt_merge["changes"]}')
        else:
            log_info(f'AI: apartment KB check returned NO for {conversation_sid}', category='sms')

        glob_has_value, glob_suggested = _run_kb_extract_check(
            conversation_sid,
            KB_EXTRACT_GLOBAL_CHECK_KEY,
            lambda **kw: build_ai_extract_global_check_prompt(message_body, chat_history),
            None,
            message_body,
            chat_history=chat_history,
        )
        if glob_has_value:
            glob_suggested_knowledge = _resolve_suggested_knowledge(glob_suggested, message_body)
            glob_merge = _run_kb_extract_merge(
                conversation_sid,
                KB_EXTRACT_GLOBAL_MERGE_KEY,
                lambda knowledge_base, message_body, suggested_knowledge, **kw: build_ai_extract_global_merge_prompt(
                    knowledge_base, message_body, suggested_knowledge
                ),
                global_kb,
                message_body,
                suggested_knowledge=glob_suggested_knowledge,
            )
            if glob_merge.get('error'):
                result['why'].append(f'Global: {glob_merge["error"]}')
            elif glob_merge.get('saved'):
                result['global_kb'] = glob_merge['kb']
                result['global_changes'] = glob_merge.get('changes')
                if save:
                    save_global_knowledge_base_text(glob_merge['kb'], conversation_sid)
                    result['global_saved'] = True
            elif glob_merge.get('changes'):
                result['why'].append(f'Global: {glob_merge["changes"]}')

        return result
    except Exception as exc:
        log_error(exc, 'extract_knowledge_from_manager_message failed', source='web')
        log_ai_error(conversation_sid, 'extract_knowledge_from_manager_message', str(exc))
        result['error'] = str(exc)
        return result


def collect_manager_messages_for_kb(messages):
    eligible = []
    for msg in messages:
        if not should_run_kb_extraction_for_message(msg):
            continue
        body = _extract_marked_body(msg.body, KB_SUFFIX) or msg.body
        if _is_skippable_message(body):
            continue
        eligible.append((msg, body.strip()))
    return eligible


def _parse_ai_customer_response(raw_text):
    """
    Parse structured AI customer output.
    Returns (answer, why, no_answer).
    Backward compatible with legacy plain-text responses.
    """
    raw = (raw_text or "").strip()
    if raw.startswith("Virtual Assistant:"):
        raw = raw[len("Virtual Assistant:"):].strip()

    answer = raw
    why = None
    if "[ANSWER]" in raw and "[WHY]" in raw:
        answer_part = raw.split("[ANSWER]", 1)[1]
        if "[WHY]" in answer_part:
            answer_text, why_text = answer_part.split("[WHY]", 1)
            answer = answer_text.strip()
            why = why_text.strip() or None

    if not answer or answer.upper() == "NO_ANSWER":
        return None, why, True
    return answer, why, False


def _persist_customer_ai_result(message_sid, result, sent_to_chat=None):
    """Store tenant AI answer metadata on the triggering message."""
    if not message_sid or not result:
        return
    kwargs = {}
    if result.get("answer"):
        kwargs["ai_response"] = result["answer"]
    if result.get("why"):
        kwargs["ai_response_why"] = result["why"]
    if sent_to_chat is not None:
        kwargs["ai_sent_to_chat"] = sent_to_chat
    if kwargs:
        _update_message_ai_result(message_sid, **kwargs)


def _safe_completion_content(response):
    """Return stripped completion text, or empty string if the model returned nothing."""
    if not response or not getattr(response, "choices", None):
        return ""
    content = response.choices[0].message.content
    return (content or "").strip()


def ai_answer_customer_detailed(conversation_sid, message_body, apartment, booking, history_before=None):
    """
    Customer path with explicit status for tooling/replay.
    Returns dict: answer, why, no_answer, error, model, raw_response.

    history_before: optional TwilioMessage for point-in-time chat history (replay).
    """
    model = _get_db_model()
    try:
        ai_client, ai_client_error = _get_ai_client()
        if not ai_client:
            return {
                "answer": None,
                "why": None,
                "no_answer": False,
                "error": ai_client_error,
                "model": model,
                "raw_response": None,
            }

        context, context_sources = build_full_context(
            conversation_sid, apartment, booking, history_before=history_before
        )

        system_prompt, system_from_db = _get_prompt_with_source('ai_answer_system')
        if not system_prompt:
            system_prompt = _fallback_ai_answer_system_prompt()
            system_from_db = False

        user_prompt, user_from_db = _get_prompt_with_source('ai_answer_user', context=context, message_body=message_body)
        if not user_prompt:
            user_prompt = _fallback_ai_answer_user_prompt(context, message_body)
            user_from_db = False

        temperature = 0.3
        max_tokens = 450
        response = ai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        raw_response = _safe_completion_content(response)
        if not raw_response:
            return {
                "answer": None,
                "why": None,
                "no_answer": False,
                "error": "AI returned empty content",
                "model": model,
                "raw_response": None,
            }
        answer, why, no_answer = _parse_ai_customer_response(raw_response)
        usage = getattr(response, 'usage', None)

        log_ai_customer_full(
            conversation_sid=conversation_sid,
            message_body=message_body,
            context=context,
            context_sources=context_sources,
            system_prompt=system_prompt,
            system_prompt_source="DB:ai_answer_system" if system_from_db else "fallback",
            user_prompt=user_prompt,
            user_prompt_source="DB:ai_answer_user" if user_from_db else "fallback",
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            answer=answer or raw_response or "(empty)",
            usage=usage,
        )

        if no_answer:
            log_ai_customer_no_answer(conversation_sid, message_body, answer or raw_response)
            log_info(f"AI: no answer for customer message in {conversation_sid}", category='sms')
            return {
                "answer": None,
                "why": why,
                "no_answer": True,
                "error": None,
                "model": model,
                "raw_response": raw_response,
            }

        log_info(f"AI responded to customer in {conversation_sid}", category='sms')
        return {
            "answer": answer,
            "why": why,
            "no_answer": False,
            "error": None,
            "model": model,
            "raw_response": raw_response,
        }

    except Exception as e:
        log_ai_error(conversation_sid, "ai_answer_customer", str(e))
        log_error(e, "Error in ai_answer_customer", source='web')
        return {
            "answer": None,
            "why": None,
            "no_answer": False,
            "error": str(e),
            "model": model,
            "raw_response": None,
        }


def ai_answer_customer(conversation_sid, message_body, apartment, booking):
    """
    Customer path: AI answers the tenant's message using full context.
    Returns answer/clarifying-question string, or None if no relevant info.
    """
    result = ai_answer_customer_detailed(conversation_sid, message_body, apartment, booking)
    if result.get("error"):
        return None
    return result.get("answer")


def ai_extract_knowledge(conversation_sid, message_body, apartment, conversation=None, history_before=None):
    """
    Live manager-message KB extraction (apartment + global).
    Delegates to extract_knowledge_from_manager_message with save=True.
    """
    try:
        result = extract_knowledge_from_manager_message(
            conversation_sid,
            message_body,
            apartment,
            conversation=conversation,
            history_before=history_before,
            save=True,
        )
        if result.get('error') and not result.get('apartment_saved') and not result.get('global_saved'):
            log_warning(f"AI knowledge extract skipped: {result['error']}", category='sms')
            return False, None

        saved = bool(result.get('apartment_saved') or result.get('global_saved'))
        changes_parts = []
        if result.get('apartment_changes'):
            changes_parts.append(result['apartment_changes'])
        if result.get('global_changes'):
            changes_parts.append(f"Global: {result['global_changes']}")
        changes = '\n'.join(changes_parts) if changes_parts else None
        return saved, changes if saved else None
    except Exception as e:
        log_ai_error(conversation_sid, "ai_extract_knowledge", str(e))
        log_error(e, "Error in ai_extract_knowledge", source='web')
        return False, None


def get_global_knowledge_base_text():
    from mysite.models import AIManagement

    entries = AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE,
    ).order_by('id')
    parts = [entry.content.strip() for entry in entries if entry.content and entry.content.strip()]
    return '\n\n'.join(parts)


def save_global_knowledge_base_text(text, conversation_sid=None):
    from mysite.models import AIManagement

    text = (text or '').strip()
    AIManagement.objects.filter(entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE).delete()
    if not text:
        return None
    description = f'Updated from chat {conversation_sid}' if conversation_sid else 'Updated from chat'
    return AIManagement.objects.create(
        name='Global Knowledge Base',
        content=text,
        entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE,
        prompt_key='global_knowledge_base',
        description=description,
    )


def _parse_kb_merge(raw_merge):
    if '[UPDATED KB]' in raw_merge and '[CHANGES]' in raw_merge:
        kb_part = raw_merge.split('[UPDATED KB]', 1)[1]
        updated_kb, changes = kb_part.split('[CHANGES]', 1)
        return updated_kb.strip(), changes.strip()
    return raw_merge.strip(), None


def _kb_merge_has_no_reusable_knowledge(updated_kb, original_kb, changes):
    if changes and changes.strip().lower().startswith('no reusable knowledge'):
        return True
    original = (original_kb or '').strip()
    updated = (updated_kb or '').strip()
    return bool(original) and updated == original


def _parse_json_object(raw):
    if not raw:
        return None
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _short_ai_explain(prompt, max_tokens=180, model=None):
    ai_client, ai_error = _get_ai_client()
    if not ai_client:
        return f'AI explanation unavailable: {ai_error}'
    model = model or _get_db_model()
    models_to_try = [model]
    if model != LEGACY_CHAT_MODEL:
        models_to_try.append(LEGACY_CHAT_MODEL)
    last_error = None
    for model_name in models_to_try:
        try:
            response = ai_client.chat.completions.create(
                model=model_name,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0,
                max_tokens=max_tokens,
            )
            return _safe_completion_content(response) or f'AI explanation failed: empty response'
        except Exception as exc:
            last_error = str(exc)
            if '404' not in last_error and 'No endpoints found' not in last_error:
                break
    return f'AI explanation failed: {last_error}'


def _classify_kb_scope(message_body, updated_kb, changes, model=None):
    prompt = (
        'You analyze property-management knowledge extracted from a group chat.\n'
        'Return JSON only with keys: global, reusable, why.\n'
        'global: reusable company-wide policy text, or empty string if apartment-specific.\n'
        'reusable: true if it is a standing property procedure or fact future tenants still need '
        '(access, parking how-it-works, house rules, WiFi/credentials, appliance operation); '
        'false only for this-stay coordination (times/ETAs, meetups, delivering an item now, '
        'personal contacts for one meeting). A current request can still contain a standing rule.\n'
        'Credentials such as WiFi passwords are reusable=true.\n\n'
        f'Manager message:\n{message_body}\n\n'
        f'Apartment knowledge base draft:\n{updated_kb}\n\n'
        f'Changes summary:\n{changes or "none"}'
    )
    raw = _short_ai_explain(prompt, max_tokens=300, model=model)
    parsed = _parse_json_object(raw)
    if parsed:
        reusable = parsed.get('reusable')
        if isinstance(reusable, str):
            reusable = reusable.strip().lower() in {'true', 'yes', '1'}
        elif reusable is None:
            reusable = True
        return {
            'global': parsed.get('global') or None,
            'reusable': reusable,
            'why': parsed.get('why') or None,
        }
    return {
        'global': None,
        'reusable': True,
        'why': raw,
    }


def _is_manager_kb_candidate(author, body, direction=None):
    """True for manager-authored messages or VA messages explicitly marked with (+)."""
    author = (author or '').strip()
    body = (body or '').strip()
    if author in get_manager_phones():
        return True
    if KB_SUFFIX in body:
        return True
    return False


def should_run_kb_extraction_for_message(message):
    """KB extraction runs only on manager messages, not automated notifications."""
    if message is None:
        return False
    sid = getattr(message, 'message_sid', None) or ''
    if sid.startswith('KB-UPDATE-'):
        return False
    if is_assistant_system_notification(message):
        return False
    return _is_manager_kb_candidate(message.author, message.body, message.direction)


def manager_kb_preview(conversation_sid, message_body, apartment, kb_draft=None):
    """
    Preview apartment/global KB updates from one manager message without saving.
    Returns dict with has_value, apartment, global, why, changes.
    """
    model = _get_db_model()
    ai_client, ai_error = _get_ai_client()
    if not ai_client:
        return {
            'has_value': False,
            'apartment': kb_draft,
            'global': None,
            'why': f'AI client unavailable: {ai_error}',
            'changes': None,
        }

    kb_content = kb_draft if kb_draft is not None else (apartment.knowledge_base or '(empty)')

    try:
        fields_ctx = _apartment_fields_context(apartment)
        check_content, _check_from_db = _get_prompt_with_source(
            'ai_extract_check', fields_ctx=fields_ctx, message_body=message_body
        )
        if not check_content:
            check_content = build_ai_extract_check_prompt(fields_ctx, message_body)

        check_response = ai_client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': check_content}],
            temperature=0,
            max_tokens=400,
        )
        check_text = _safe_completion_content(check_response)
        if not check_text:
            return {
                'has_value': False,
                'apartment': kb_draft,
                'global': None,
                'why': 'AI KB check returned empty content',
                'changes': None,
            }

        has_value, suggested = _parse_kb_check(check_text)
        if not has_value and manager_message_has_operational_kb_hints(message_body):
            has_value = True
        if not has_value:
            why = _short_ai_explain(
                'Explain in one sentence why this manager message should not add knowledge base information:\n'
                f'{message_body}',
                model=model,
            )
            return {'has_value': False, 'apartment': kb_draft, 'global': None, 'why': why, 'changes': None}

        suggested_knowledge = _resolve_suggested_knowledge(suggested, message_body)
        merge_content, _merge_from_db = _get_prompt_with_source(
            'ai_extract_merge',
            knowledge_base=kb_content,
            message_body=message_body,
            suggested_knowledge=suggested_knowledge,
        )
        if not merge_content:
            merge_content = build_ai_extract_merge_prompt(
                kb_content, message_body, suggested_knowledge
            )

        update_response = ai_client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': merge_content}],
            temperature=0.2,
            max_tokens=1200,
        )
        raw_merge = _safe_completion_content(update_response)
        if not raw_merge:
            return {
                'has_value': False,
                'apartment': kb_draft,
                'global': None,
                'why': 'AI KB merge returned empty content',
                'changes': None,
            }

        updated_kb, changes = _parse_kb_merge(raw_merge)
        if _kb_merge_has_no_reusable_knowledge(updated_kb, kb_content, changes):
            why = _short_ai_explain(
                'Explain in one sentence why this manager message should not add knowledge base information:\n'
                f'{message_body}',
                model=model,
            )
            return {'has_value': False, 'apartment': kb_draft, 'global': None, 'why': why, 'changes': None}

        classification = _classify_kb_scope(message_body, updated_kb, changes, model=model)
        if classification.get('reusable') is False and not manager_message_has_operational_kb_hints(message_body):
            return {
                'has_value': False,
                'apartment': kb_draft,
                'global': None,
                'why': classification.get('why') or 'One-time coordination, not reusable knowledge.',
                'changes': None,
            }

        return {
            'has_value': True,
            'apartment': updated_kb,
            'global': classification.get('global'),
            'why': classification.get('why') or changes,
            'changes': changes,
        }
    except Exception as exc:
        log_error(exc, 'manager_kb_preview failed', source='web')
        return {
            'has_value': False,
            'apartment': kb_draft,
            'global': None,
            'why': f'AI KB preview failed: {exc}',
            'changes': None,
        }


def _friendly_kb_error(error):
    error = (error or '').strip()
    if not error:
        return 'Could not analyze this message.'
    if 'multiple values for argument' in error:
        return 'Could not analyze this message (internal error).'
    if error.startswith('AI client unavailable'):
        return error
    if error.startswith('AI merge returned empty'):
        return 'AI returned no merge result for this message.'
    return error


def _format_kb_generate_message_line(msg, body, prev_apartment_kb, prev_global_kb, result):
    """Build one human-readable block for KB modal generate output (full message text)."""
    ts = msg.message_timestamp.strftime('%b %d %H:%M') if msg.message_timestamp else 'Message'
    message_text = (body or '').strip()
    header = f'{ts}\n{message_text}' if message_text else ts

    def with_status(status):
        return f'{header}\n→ {status}'

    if result.get('error'):
        return with_status(f'skipped — {_friendly_kb_error(result["error"])}')

    apartment_kb = result.get('apartment_kb', prev_apartment_kb) or ''
    global_kb = result.get('global_kb', prev_global_kb) or ''
    apartment_changed = (apartment_kb or '').strip() != (prev_apartment_kb or '').strip()
    global_changed = (global_kb or '').strip() != (prev_global_kb or '').strip()

    parts = []
    if apartment_changed:
        detail = (result.get('apartment_changes') or 'updated').strip()
        if detail.lower().startswith('no reusable knowledge'):
            detail = 'updated'
        parts.append(f'Apartment KB: {detail}')
    if global_changed:
        detail = (result.get('global_changes') or 'updated').strip()
        if detail.lower().startswith('no reusable knowledge'):
            detail = 'updated'
        parts.append(f'Global KB: {detail}')

    if parts:
        return with_status('; '.join(parts))

    for note in result.get('why') or []:
        note = (note or '').strip()
        if note.lower().startswith('no reusable knowledge'):
            return with_status('no reusable knowledge found.')
        if note.lower().startswith('apartment:'):
            return with_status(note)
        if note.lower().startswith('global:'):
            return with_status(note)

    return with_status('no reusable knowledge found.')


def _kb_generate_stats(prev_apartment_kb, prev_global_kb, apartment_draft, global_draft, result):
    stats = {
        'apartment_changed': False,
        'global_changed': False,
        'skipped': bool(result.get('error')),
        'no_knowledge': False,
    }
    if result.get('error'):
        return stats
    apt_changed = (apartment_draft or '').strip() != (prev_apartment_kb or '').strip()
    glob_changed = (global_draft or '').strip() != (prev_global_kb or '').strip()
    stats['apartment_changed'] = apt_changed
    stats['global_changed'] = glob_changed
    stats['no_knowledge'] = not apt_changed and not glob_changed
    return stats


def _kb_generate_summary(analyzed, apartment_updates, global_updates, no_knowledge, skipped):
    if analyzed == 0:
        return 'No manager messages found to analyze.'
    summary = f'Analyzed {analyzed} manager message{"s" if analyzed != 1 else ""}.'
    summary_parts = []
    if apartment_updates:
        summary_parts.append(f'{apartment_updates} added to apartment KB')
    if global_updates:
        summary_parts.append(f'{global_updates} added to global KB')
    if no_knowledge:
        summary_parts.append(f'{no_knowledge} with no reusable knowledge')
    if skipped:
        summary_parts.append(f'{skipped} skipped due to errors')
    if summary_parts:
        summary += ' ' + ', '.join(summary_parts) + '.'
    else:
        summary += ' No reusable knowledge found.'
    summary += ' Review suggestions below, then Save KB.'
    return summary


def list_conversation_kb_eligible(conversation_sid):
    """Return manager message ids eligible for KB extraction (no AI calls)."""
    from mysite.models import TwilioConversation, TwilioMessage

    conversation = TwilioConversation.objects.select_related('apartment').filter(
        conversation_sid=conversation_sid,
    ).first()
    if not conversation:
        return {'success': False, 'error': 'Conversation not found.'}
    if not conversation.apartment_id:
        return {'success': False, 'error': 'Conversation must be linked to an apartment.'}

    messages = list(
        TwilioMessage.objects.filter(conversation=conversation).order_by('message_timestamp', 'id')
    )
    eligible = collect_manager_messages_for_kb(messages)
    return {
        'success': True,
        'message_ids': [msg.id for msg, _body in eligible],
        'messages_analyzed': len(eligible),
        'total_messages': len(messages),
        'apartment_kb': conversation.apartment.knowledge_base or '',
        'global_kb': get_global_knowledge_base_text() or '',
    }


def generate_conversation_kb_step(conversation_sid, message_id, apartment_kb_draft=None, global_kb_draft=None):
    """Run KB extraction for one manager message (used by modal step-by-step generate)."""
    from mysite.models import TwilioConversation, TwilioMessage

    conversation = TwilioConversation.objects.select_related(
        'apartment', 'booking', 'booking__tenant',
    ).filter(
        conversation_sid=conversation_sid,
    ).first()
    if not conversation:
        return {'success': False, 'error': 'Conversation not found.'}
    if not conversation.apartment_id:
        return {'success': False, 'error': 'Conversation must be linked to an apartment.'}

    message = TwilioMessage.objects.filter(id=message_id, conversation=conversation).first()
    if not message:
        return {'success': False, 'error': 'Message not found.'}

    if not should_run_kb_extraction_for_message(message):
        return {'success': False, 'error': 'Only manager messages can be used for knowledge base extraction.'}

    body = _extract_marked_body(message.body, KB_SUFFIX) or message.body
    if _is_skippable_message(body):
        return {'success': False, 'error': 'Message is too short to extract knowledge from.'}
    body = (body or '').strip()

    apartment_draft = apartment_kb_draft if apartment_kb_draft is not None else (conversation.apartment.knowledge_base or '')
    global_draft = global_kb_draft if global_kb_draft is not None else (get_global_knowledge_base_text() or '')

    prev_apartment_kb = apartment_draft
    prev_global_kb = global_draft
    result = extract_knowledge_from_manager_message(
        conversation_sid,
        body,
        conversation.apartment,
        conversation=conversation,
        history_before=message,
        save=False,
        apartment_kb_draft=apartment_draft,
        global_kb_draft=global_draft,
    )
    apartment_draft = result.get('apartment_kb', apartment_draft)
    global_draft = result.get('global_kb', global_draft)
    line = _format_kb_generate_message_line(
        message, body, prev_apartment_kb, prev_global_kb, result
    )
    stats = _kb_generate_stats(
        prev_apartment_kb, prev_global_kb, apartment_draft, global_draft, result
    )
    return {
        'success': True,
        'apartment_kb': apartment_draft,
        'global_kb': global_draft,
        'line': line,
        **stats,
    }


def generate_conversation_kb_suggestions(conversation_sid):
    from mysite.models import TwilioConversation, TwilioMessage

    conversation = TwilioConversation.objects.select_related(
        'apartment', 'booking', 'booking__tenant',
    ).filter(
        conversation_sid=conversation_sid,
    ).first()
    if not conversation:
        return {'success': False, 'error': 'Conversation not found.'}
    if not conversation.apartment_id:
        return {'success': False, 'error': 'Conversation must be linked to an apartment.'}

    messages = list(
        TwilioMessage.objects.filter(conversation=conversation).order_by('message_timestamp', 'id')
    )
    eligible = collect_manager_messages_for_kb(messages)
    apartment_draft = conversation.apartment.knowledge_base or ''
    global_draft = get_global_knowledge_base_text() or ''
    why_lines = []
    apartment_updates = 0
    global_updates = 0
    skipped = 0
    no_knowledge = 0

    for msg, body in eligible:
        prev_apartment_kb = apartment_draft
        prev_global_kb = global_draft
        result = extract_knowledge_from_manager_message(
            conversation_sid,
            body,
            conversation.apartment,
            conversation=conversation,
            history_before=msg,
            save=False,
            apartment_kb_draft=apartment_draft,
            global_kb_draft=global_draft,
        )
        apartment_draft = result.get('apartment_kb', apartment_draft)
        global_draft = result.get('global_kb', global_draft)
        line = _format_kb_generate_message_line(
            msg, body, prev_apartment_kb, prev_global_kb, result
        )
        why_lines.append(line)

        if result.get('error'):
            skipped += 1
        else:
            apt_changed = (apartment_draft or '').strip() != (prev_apartment_kb or '').strip()
            glob_changed = (global_draft or '').strip() != (prev_global_kb or '').strip()
            if apt_changed:
                apartment_updates += 1
            if glob_changed:
                global_updates += 1
            if not apt_changed and not glob_changed:
                no_knowledge += 1

    analyzed = len(eligible)
    summary = _kb_generate_summary(analyzed, apartment_updates, global_updates, no_knowledge, skipped)

    return {
        'success': True,
        'apartment_kb': apartment_draft,
        'global_kb': global_draft,
        'summary': summary,
        'why': '\n'.join(why_lines[:40]),
        'messages_analyzed': analyzed,
        'total_messages': len(messages),
    }


@csrf_exempt
@require_http_methods(["POST", "GET"])
def twilio_webhook(request):
    log_info("**********CONVERSATION_CREATED_WEBHOOK **********", category='sms')
    try:
        if request.method == 'POST':
            data = request.POST
            event_type = data.get('EventType', None)
            webhook_sid = data.get('WebhookSid', None)
            message_sid = data.get('MessageSid', None)  # Extract actual MessageSid from webhook
            conversation_sid = data.get('ConversationSid', None)
            author = data.get('Author', None)
            body = data.get('Body', None)
            messaging_binding_address = data.get('MessagingBinding.Address', None)
            messaging_binding_proxy_address = data.get('MessagingBinding.ProxyAddress', None)
            
            log_info(
                "Webhook received",
                category='sms',
                details={
                    'event_type': event_type,
                    'message_sid': message_sid,
                    'webhook_sid': webhook_sid,
                    'conversation_sid': conversation_sid,
                    'author': author,
                    'body': body,
                    'messaging_binding_address': messaging_binding_address,
                    'messaging_binding_proxy_address': messaging_binding_proxy_address
                }
            )
            twilio_phone = "+13153524379"
            manager_phone = "+15612205252"
            manager_phone_2 = "+17282001917"
            manager_phone_3 = "+15614603904"
            manager_phone_4 = "+15618438867"
            
            
            if event_type == 'onMessageAdded':
                log_info(f"Event type is onMessageAdded: {event_type}", category='sms')
                
                # Ensure conversation exists in database (do NOT gate this on Body presence).
                # Some Twilio Conversation events can have an empty Body (e.g. media/system messages),
                # and we still want the conversation row created.
                if conversation_sid:
                    save_conversation_to_db(
                        conversation_sid=conversation_sid,
                        friendly_name=f"Conversation {conversation_sid}",
                        author=author  # Pass author for smart linking logic
                    )

                # Save message to database (Body can be empty; MessageSid is the true unique identifier).
                if message_sid:
                    body_to_save = body or ''

                    # Determine direction based on author
                    direction = 'inbound' if author not in (twilio_phone, 'Virtual Assistant', 'ASSISTANT') and author not in get_manager_phones() else 'outbound'

                    log_message_received(
                        conversation_sid=conversation_sid or '',
                        author=author or '',
                        body=body_to_save,
                        event_type=event_type or '',
                        message_sid=message_sid,
                        direction=direction,
                    )

                    save_message_to_db(
                        message_sid=message_sid,  # Use the actual MessageSid from Twilio
                        conversation_sid=conversation_sid,
                        author=author,
                        body=body_to_save,
                        direction=direction,
                        webhook_sid=webhook_sid,
                        messaging_binding_address=messaging_binding_address,
                        messaging_binding_proxy_address=messaging_binding_proxy_address
                    )
                else:
                    log_warning("Received onMessageAdded without MessageSid, skipping message save to DB", category='sms')

                # --- AI processing ---
                if not (body and author):
                    pass
                elif author in ('ASSISTANT', 'Virtual Assistant'):
                    body_stripped = body.strip()
                    body_for_customer = _extract_marked_body(body_stripped, CLIENT_SUFFIX)
                    if body_for_customer:
                        try:
                            from mysite.models import TwilioConversation, Apartment, Booking
                            _conv = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
                            if _conv and _conv.apartment_id and _conv.booking_id:
                                _apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=_conv.apartment_id)
                                _booking = Booking.objects.select_related('tenant').get(id=_conv.booking_id)
                                if _enqueue_for_ai_agent(conversation_sid, message_sid, body_for_customer):
                                    pass  # answered asynchronously by the ai-agent worker
                                elif not _is_skippable_message(body_for_customer):
                                    log_ai_customer_start(conversation_sid, author, body_for_customer, _conv.apartment_id, _conv.booking_id)
                                    _ai_result = ai_answer_customer_detailed(conversation_sid, body_for_customer, _apartment, _booking)
                                    _ai_resp = _ai_result.get("answer")
                                    if _ai_resp:
                                        if _should_send_ai_to_group(_apartment):
                                            try:
                                                send_tenant_sms_gated(conversation_sid, 'Virtual Assistant', _ai_resp, twilio_phone, None)
                                            except Exception:
                                                _notify_manager_chat_delivery_failed(
                                                    _booking.tenant.full_name or "N/A",
                                                    _booking.tenant.phone or "N/A",
                                                    _ai_resp,
                                                    conversation_sid,
                                                )
                                                raise
                                            log_ai_customer_sent(conversation_sid, _ai_resp)
                                            _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=True)
                                        else:
                                            log_ai_disabled(conversation_sid or '', author or '', body_for_customer or '')
                                            _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=False)
                                            log_ai_customer_sent(conversation_sid, _ai_resp)
                                    elif _ai_result.get("why") or _ai_result.get("no_answer"):
                                        _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=False)
                        except Exception as e:
                            log_ai_error(conversation_sid or '', "AI message routing", str(e))
                            log_error(e, "Error in AI message routing (ASSISTANT client)", source='web')
                    else:
                        body_for_extract = _extract_marked_body(body_stripped, KB_SUFFIX)
                        if body_for_extract:
                            try:
                                from mysite.models import TwilioConversation, Apartment
                                _conv = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
                                if _conv and _conv.apartment_id and _conv.booking_id:
                                    _apartment = Apartment.objects.get(id=_conv.apartment_id)
                                    log_ai_manager_start(conversation_sid, author, body_for_extract, _conv.apartment_id)
                                    from mysite.models import TwilioMessage
                                    _history_msg = TwilioMessage.objects.filter(message_sid=message_sid).first() if message_sid else None
                                    ai_extract_knowledge(
                                        conversation_sid, body_for_extract, _apartment,
                                        conversation=_conv, history_before=_history_msg,
                                    )
                            except Exception as e:
                                log_ai_error(conversation_sid or '', "AI message routing", str(e))
                                log_error(e, "Error in AI message routing (ASSISTANT)", source='web')
                elif not _is_ai_assistant_globally_enabled() and not _conversation_ai_group_chat_enabled(conversation_sid):
                    # Test mode: run AI processing but do NOT send responses to chat
                    log_ai_disabled(conversation_sid or '', author or '', body or '')
                    try:
                        from mysite.models import TwilioConversation, Apartment, Booking
                        _conv = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()

                        if _conv and _conv.apartment_id and _conv.booking_id:
                            _apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=_conv.apartment_id)
                            _booking = Booking.objects.select_related('tenant').get(id=_conv.booking_id)
                            _is_customer = author not in (twilio_phone, 'ASSISTANT', 'Virtual Assistant') and author not in get_manager_phones()

                            if _is_customer and _enqueue_for_ai_agent(conversation_sid, message_sid, body):
                                pass  # answered asynchronously by the ai-agent worker
                            elif _is_customer:
                                if _is_skippable_message(body):
                                    log_ai_customer_skipped(conversation_sid, body)
                                else:
                                    log_ai_customer_start(conversation_sid, author, body, _conv.apartment_id, _conv.booking_id)
                                    _ai_result = ai_answer_customer_detailed(conversation_sid, body, _apartment, _booking)
                                    _ai_resp = _ai_result.get("answer")
                                    if _ai_resp:
                                        log_ai_customer_sent(conversation_sid, _ai_resp)
                                        _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=False)
                                    elif _ai_result.get("why") or _ai_result.get("no_answer"):
                                        _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=False)
                            else:
                                from mysite.models import TwilioMessage
                                _history_msg = TwilioMessage.objects.filter(message_sid=message_sid).first() if message_sid else None
                                if author in get_manager_phones():
                                    # Claude agent: a manager's message updates issues / follow-ups (no-op on the legacy backend)
                                    _enqueue_staff_for_ai_agent(conversation_sid, message_sid, body)
                                if _history_msg and should_run_kb_extraction_for_message(_history_msg):
                                    log_ai_manager_start(conversation_sid, author, body, _conv.apartment_id)
                                    _kb_saved, _kb_new = ai_extract_knowledge(
                                        conversation_sid, body, _apartment,
                                        conversation=_conv, history_before=_history_msg,
                                    )
                                    _update_message_ai_result(
                                        message_sid,
                                        ai_kb_updated=_kb_saved,
                                        ai_kb_changes=_kb_new,
                                    )
                        else:
                            log_no_conv_link(conversation_sid or '', author or '', body or '')
                    except Exception as e:
                        log_ai_error(conversation_sid or '', "AI message routing (test mode)", str(e))
                        log_error(e, "Error in AI message routing (test mode)", source='web')
                else:
                    try:
                        from mysite.models import TwilioConversation, Apartment, Booking
                        _conv = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()

                        if _conv and _conv.apartment_id and _conv.booking_id:
                            _apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=_conv.apartment_id)
                            _booking = Booking.objects.select_related('tenant').get(id=_conv.booking_id)
                            _is_customer = author not in (twilio_phone, 'ASSISTANT', 'Virtual Assistant') and author not in get_manager_phones()

                            if _is_customer and _enqueue_for_ai_agent(conversation_sid, message_sid, body):
                                pass  # answered asynchronously by the ai-agent worker
                            elif _is_customer:
                                # Customer → skip short ack messages, then AI tries to answer/clarify
                                if _is_skippable_message(body):
                                    log_ai_customer_skipped(conversation_sid, body)
                                else:
                                    log_ai_customer_start(conversation_sid, author, body, _conv.apartment_id, _conv.booking_id)
                                    _ai_result = ai_answer_customer_detailed(conversation_sid, body, _apartment, _booking)
                                    _ai_resp = _ai_result.get("answer")
                                    if _ai_resp:
                                        if _should_send_ai_to_group(_apartment):
                                            try:
                                                send_tenant_sms_gated(conversation_sid, 'Virtual Assistant', _ai_resp, twilio_phone, None)
                                            except Exception:
                                                _notify_manager_chat_delivery_failed(
                                                    _booking.tenant.full_name or "N/A",
                                                    _booking.tenant.phone or "N/A",
                                                    _ai_resp,
                                                    conversation_sid,
                                                )
                                                raise
                                            log_ai_customer_sent(conversation_sid, _ai_resp)
                                            _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=True)
                                        else:
                                            log_ai_disabled(conversation_sid or '', author or '', body or '')
                                            log_ai_customer_sent(conversation_sid, _ai_resp)
                                            _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=False)
                                    elif _ai_result.get("why") or _ai_result.get("no_answer"):
                                        _persist_customer_ai_result(message_sid, _ai_result, sent_to_chat=False)
                            else:
                                from mysite.models import TwilioMessage
                                _history_msg = TwilioMessage.objects.filter(message_sid=message_sid).first() if message_sid else None
                                if author in get_manager_phones():
                                    # Claude agent: a manager's message updates issues / follow-ups (no-op on the legacy backend)
                                    _enqueue_staff_for_ai_agent(conversation_sid, message_sid, body)
                                if _history_msg and should_run_kb_extraction_for_message(_history_msg):
                                    log_ai_manager_start(conversation_sid, author, body, _conv.apartment_id)
                                    _kb_saved, _kb_new = ai_extract_knowledge(
                                        conversation_sid, body, _apartment,
                                        conversation=_conv, history_before=_history_msg,
                                    )
                                    _update_message_ai_result(
                                        message_sid,
                                        ai_kb_updated=_kb_saved,
                                        ai_kb_changes=_kb_new,
                                    )
                        else:
                            log_no_conv_link(conversation_sid or '', author or '', body or '')
                    except Exception as e:
                        log_ai_error(conversation_sid or '', "AI message routing", str(e))
                        log_error(e, "Error in AI message routing", source='web')
                # --- end AI processing ---
                # Check if author is not twilio_phone and not manager_phone
                author_is_customer = author not in (twilio_phone, 'ASSISTANT', 'Virtual Assistant') and author not in get_manager_phones()
                
                if author_is_customer:
                    log_info(f"Author {author} is a customer, checking for existing group conversations", category='sms')
                    
                    # Get customer's current booking context to determine if we need a new conversation
                    customer_booking = get_booking_from_phone(author)
                    apartment_id = customer_booking.apartment.id if customer_booking and customer_booking.apartment else None
                    
                    # Check if author exists in group conversation for this specific apartment
                    author_in_group = check_author_in_group_conversations_for_apartment(author, apartment_id)
                    
                    if not author_in_group:
                        log_info(
                            f"Author {author} not found in group conversations, creating new group conversation and forwarding message",
                            category='sms'
                        )
                        
                        # Create new group conversation with all participants
                        participants_config = []
                        
                        # Add customer
                        participants_config.append({
                            "phone": author
                        })
                        
                        # Add manager  
                        participants_config.append({
                            "phone": manager_phone
                        })

                        # Add manager 2
                        participants_config.append({
                            "phone": manager_phone_2
                        })

                        # Add manager 3
                        participants_config.append({
                            "phone": manager_phone_3
                        })

                        # Add manager 4
                        participants_config.append({
                            "phone": manager_phone_4
                        })
                        
                        # Add assistant
                        participants_config.append({
                            "identity": "ASSISTANT",
                            "projected_address": twilio_phone
                        })
                        
                        log_info(
                            f"Creating new group conversation",
                            category='sms',
                            details={'participants_config': participants_config}
                        )
                        
                        friendly_name = f"Customer Support Group - {author}"
                        new_conversation_sid = create_conversation_with_participants(
                            friendly_name, 
                            participants_config
                        )

                        log_new_group_created(
                            conversation_sid=conversation_sid,
                            author=author,
                            new_conversation_sid=new_conversation_sid,
                            participants=[p.get("phone", p.get("identity", "")) for p in participants_config],
                        )

                        # Save new group conversation with smart linking for customer
                        save_conversation_to_db(
                            conversation_sid=new_conversation_sid,
                            friendly_name=friendly_name,
                            author=author  # Pass customer phone for smart linking
                        )
                        
                        # Forward the message to the new group conversation
                        time.sleep(6)
                        if body:
                            log_message_forwarded(
                                conversation_sid=conversation_sid,
                                author=author,
                                body=body,
                                target_conversation_sid=new_conversation_sid,
                            )
                            forward_message_to_conversation(new_conversation_sid, author, body)
                            _update_message_ai_result(
                                message_sid,
                                forwarded_to_group_sid=new_conversation_sid,
                            )
                        
                        # Delete the old conversation (the one that triggered this webhook)
                        if conversation_sid:
                            try:
                                delete_conversation(conversation_sid)
                                log_info(f"Deleted old conversation: {conversation_sid}", category='sms')
                            except Exception as e:
                                log_warning(f"Could not delete old conversation {conversation_sid}", category='sms', details={'error': str(e)})
                        
                        # Print final participant list
                        print_participants(new_conversation_sid, "New Group Conversation Created")
                        return JsonResponse({'status': 'success', 'new_conversation_sid': new_conversation_sid}, status=200)
                
                # Print final participant list
                print_participants(conversation_sid, "Conversation Created with All Participants")
                return JsonResponse({'status': 'success', 'conversation_sid': conversation_sid}, status=200)
            

        return JsonResponse({'status': 'success'}, status=200)
    except Exception as e:
        log_error(e, "Error in twilio_webhook", source='web')
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)



def create_conversation_config(friendly_name, tenant_phone):
    """
    Create a conversation with all participants in a single API call
    
    Args:
        friendly_name (str): Name for the conversation
        tenant_phone (str): Tenant's phone number
    
    Returns:
        str: conversation_sid of the created conversation
    """
    try:
         # Create new group conversation with all participants
        participants_config = []
        twilio_phone = "+13153524379"
        manager_phone = "+15612205252"
        manager_phone_2 = "+17282001917"
        manager_phone_3 = "+15614603904"
        manager_phone_4 = "+15618438867"
        
        # Add customer
        participants_config.append({
            "phone": tenant_phone
        })
        
        # Add manager  
        participants_config.append({
            "phone": manager_phone
        })

        # Add manager 2
        participants_config.append({
            "phone": manager_phone_2
        })

        # Add manager 3
        participants_config.append({
            "phone": manager_phone_3
        })

        # Add manager 4
        participants_config.append({
            "phone": manager_phone_4
        })
        
        # Add assistant
        participants_config.append({
            "identity": "Virtual Assistant",
            "projected_address": twilio_phone
        })
        
        conversation_sid = create_conversation_with_participants(
            friendly_name, participants_config, tenant_phone_to_verify=tenant_phone
        )
        
        # Try to find booking from tenant phone for database relationship
        booking = get_booking_from_phone(tenant_phone)
        apartment = booking.apartment if booking else None
        
        # Save conversation to database with booking/apartment relationship
        save_conversation_to_db(conversation_sid, friendly_name, booking=booking, apartment=apartment)
        
        log_info(
            f'Created conversation "{friendly_name}"',
            category='sms',
            details={'conversation_sid': conversation_sid, 'participant_count': len(participants_config)}
        )
        return conversation_sid
        
    except Exception as e:
        log_error(e, "Error creating conversation with participants", source='twilio')
        raise


def _fallback_author_for_50513(author):
    """Return alternate author for Twilio 50513 (author not among group MMS participants)."""
    if author == 'Virtual Assistant':
        return 'ASSISTANT'
    if author == 'ASSISTANT':
        return 'Virtual Assistant'
    return None


def send_messsage_by_sid(conversation_sid, author, message, sender_phone, receiver_phone):
    try:
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()

        authors_to_try = [author]
        fallback = _fallback_author_for_50513(author)
        if fallback:
            authors_to_try.append(fallback)

        max_attempts = 10
        delay_seconds = 0.5
        last_error = None
        for attempt in range(1, max_attempts + 1):
            for try_author in authors_to_try:
                try:
                    twilio_message = client.conversations.v1.conversations(
                        conversation_sid
                    ).messages.create(
                        body=message,
                        author=try_author,
                    )
                    log_info(
                        f"Message sent via Twilio",
                        category='sms',
                        details={'message_sid': twilio_message.sid, 'conversation_sid': conversation_sid}
                    )
                    save_message_to_db(
                        message_sid=twilio_message.sid,
                        conversation_sid=conversation_sid,
                        author=try_author,
                        body=message,
                        direction='outbound'
                    )
                    return
                except Exception as e:
                    from twilio.base.exceptions import TwilioRestException
                    is_50513 = (
                        isinstance(e, TwilioRestException)
                        and (getattr(e, "code", None) == 50513 or "Message author should be among group MMS participants" in str(e))
                    )
                    if is_50513 and try_author != authors_to_try[-1]:
                        log_info(
                            f"Retrying with fallback author (50513) for conversation {conversation_sid}",
                            category='sms'
                        )
                        continue
                    last_error = e
                    is_initializing = (getattr(e, "status", None) == 409) or ("initializing" in str(e).lower())
                    if is_initializing and attempt < max_attempts:
                        log_info(
                            f"Conversation {conversation_sid} is initializing; retrying in {delay_seconds}s (attempt {attempt}/{max_attempts})",
                            category='sms'
                        )
                        time.sleep(delay_seconds)
                        delay_seconds = min(delay_seconds * 2, 8.0)
                        break
                    log_error(e, f"Error sending message via Twilio (attempt {attempt})", source='twilio')
                    raise Exception(f"Error sending message via Twilio: {e}")
        
        if last_error:
            raise Exception(f"Error sending message via Twilio: {last_error}")
        
    except Exception as e:
        if "Error sending message via Twilio" not in str(e):
            log_error(e, "Error sending message via Twilio", source='twilio')
        raise Exception(f"Error sending message via Twilio: {e}")


def send_tenant_sms_gated(conversation_sid, author, message, sender_phone, receiver_phone):
    """
    Same as send_messsage_by_sid, but only for tenant-facing sends (AI answers, welcome/contract-link
    messages): outside the 08:00-21:00 Florida notification window it holds the message in
    PendingOutboundMessage instead of sending, and flush_pending_sms delivers it once the window opens.
    Staff-initiated sends (manual chat replies, ClickUp/Telegram alerts) should keep calling
    send_messsage_by_sid directly - they are not gated. Returns True if sent now, False if held.
    """
    from mysite.ai_agent import config
    if config.is_within_notification_window():
        send_messsage_by_sid(conversation_sid, author, message, sender_phone, receiver_phone)
        return True
    from mysite.models import PendingOutboundMessage
    PendingOutboundMessage.objects.create(
        conversation_sid=conversation_sid, author=author, body=message,
        sender_phone=sender_phone, receiver_phone=receiver_phone,
        send_after=config.next_notification_window_start(),
    )
    log_info(
        f"SMS held for the notification window (08:00-21:00 Florida time), will send at the next window open",
        category='sms', details={'conversation_sid': conversation_sid},
    )
    return False


def _notify_manager_chat_delivery_failed(tenant_name, tenant_phone, message, conversation_sid=None):
    """Send delivery failure alert to manager chat. Swallows errors to avoid masking the original failure."""
    if not MANAGER_CHAT_SID:
        return
    try:
        chat_id = conversation_sid or "not created"
        msg_str = str(message or "")[:200]
        if len(str(message or "")) > 200:
            msg_str += "..."
        alert = (
            f"Message wasn't delivered. Tenant: {tenant_name}, Phone: {tenant_phone}, "
            f"Chat ID: {chat_id}. Message: {msg_str}"
        )
        twilio_phone_secondary = os.environ.get("TWILIO_PHONE_SECONDARY")
        send_messsage_by_sid(
            MANAGER_CHAT_SID,
            "Virtual Assistant",
            alert,
            twilio_phone_secondary,
            None,
        )
    except Exception:
        log_warning("Failed to notify manager chat of delivery failure", category='sms')


def _group_chat_name(booking):
    """Twilio conversation friendly_name for a booking's tenant group chat."""
    return f"{booking.apartment.name} {booking.tenant.full_name or 'Tenant'} Rental"


def sendContractToTwilio(booking, contract_url):
    try:
        twilio_phone_secondary = os.environ.get("TWILIO_PHONE_SECONDARY")
        
        # Log tenant phone for debugging
        log_info(
            f"Attempting to send contract to tenant",
            category='sms',
            details={'booking_id': booking.id, 'tenant_phone': booking.tenant.phone}
        )
        
        # Validate tenant phone
        if not booking.tenant.phone:
            error_msg = f"Tenant phone is empty/None for booking {booking.id}"
            log_error(Exception(error_msg), error_msg, source='web')
            raise Exception(error_msg)
        
        validated_phone = validate_phone_number(booking.tenant.phone)
        if not validated_phone:
            error_msg = f"Invalid tenant phone format: '{booking.tenant.phone}' for booking {booking.id}, tenant: {booking.tenant.full_name}"
            log_error(Exception(error_msg), error_msg, source='web')
            raise Exception(f"Invalid tenant phone format: '{booking.tenant.phone}' for booking {booking.id}")
        
        log_info(f"Validated tenant phone: {validated_phone}", category='sms')
        
        conversation_sid = create_conversation_config(
            _group_chat_name(booking),
            validated_phone
        )
        
        if conversation_sid:
            log_info(f"Conversation created: {conversation_sid}", category='sms')
            tenant_name = booking.tenant.full_name or 'Dear guest'
            
            # Try to get template from AIManagement (sms_template)
            message = _get_template(
                'contract_message_template',
                tenant_name=tenant_name,
                apartment_name=booking.apartment.name,
                start_date=booking.start_date,
                end_date=booking.end_date,
                contract_url=contract_url
            )

            # Fallback to hardcoded template if DB template is missing or invalid
            if not message:
                message = (
                    f"Hi {tenant_name}, I am Sophia, a virtual assistant helping managers and guests with a booking for apartment {booking.apartment.name} from {booking.start_date} to {booking.end_date}. "
                    f"If I do not respond, our managers in this chat will contact you as soon as possible. "
                    f"To continue with a booking, please sign this contract: {contract_url}"
                )

            try:
                send_tenant_sms_gated(conversation_sid, "Virtual Assistant", message, twilio_phone_secondary, validated_phone)
            except Exception:
                _notify_manager_chat_delivery_failed(
                    booking.tenant.full_name or "N/A",
                    validated_phone,
                    message,
                    conversation_sid,
                )
                raise
        else:
            log_warning("Conversation wasn't created", category='sms')
            _notify_manager_chat_delivery_failed(
                booking.tenant.full_name or "N/A",
                validated_phone,
                "contract link",
                None,
            )
    except Exception as e:
        from twilio.base.exceptions import TwilioException
        log_error(e, "Error sending contract message", source='twilio')
        raise Exception(f"Error sending contract message: {e}")


def sendWelcomeMessageToTwilio(booking):
    try:
        twilio_phone_secondary = os.environ.get("TWILIO_PHONE_SECONDARY")
        
        # Log tenant phone for debugging
        log_info(
            f"Attempting to send welcome message to tenant",
            category='sms',
            details={'booking_id': booking.id, 'tenant_phone': booking.tenant.phone}
        )
        
        # Validate tenant phone
        if not booking.tenant.phone:
            error_msg = f"Tenant phone is empty/None for booking {booking.id}"
            log_error(Exception(error_msg), error_msg, source='web')
            raise Exception(error_msg)
        
        validated_phone = validate_phone_number(booking.tenant.phone)
        if not validated_phone:
            error_msg = f"Invalid tenant phone format: '{booking.tenant.phone}' for booking {booking.id}, tenant: {booking.tenant.full_name}"
            log_error(Exception(error_msg), error_msg, source='web')
            raise Exception(f"Invalid tenant phone format: '{booking.tenant.phone}' for booking {booking.id}")
        
        log_info(f"Validated tenant phone: {validated_phone}", category='sms')
        
        conversation_sid = create_conversation_config(
            _group_chat_name(booking),
            validated_phone
        )
        
        if conversation_sid:
            log_info(f"Conversation created: {conversation_sid}", category='sms')
            tenant_name = booking.tenant.full_name or 'Dear guest'
            
            # Try to get template from AIManagement (sms_template)
            message = _get_template(
                'welcome_message_template',
                tenant_name=tenant_name,
                apartment_name=booking.apartment.name,
                start_date=booking.start_date,
                end_date=booking.end_date
            )

            # Fallback to hardcoded template if DB template is missing or invalid
            if not message:
                message = (
                    f"Hi {tenant_name}, I am Sophia, a virtual assistant helping managers and guests with a booking for apartment {booking.apartment.name} from {booking.start_date} to {booking.end_date}. "
                    f"If I do not respond, our managers in this chat will contact you as soon as possible."
                )

            try:
                send_tenant_sms_gated(conversation_sid, "Virtual Assistant", message, twilio_phone_secondary, validated_phone)
            except Exception:
                _notify_manager_chat_delivery_failed(
                    booking.tenant.full_name or "N/A",
                    validated_phone,
                    message,
                    conversation_sid,
                )
                raise
        else:
            log_warning("Conversation wasn't created", category='sms')
            _notify_manager_chat_delivery_failed(
                booking.tenant.full_name or "N/A",
                validated_phone,
                "welcome message",
                None,
            )
    except Exception as e:
        from twilio.base.exceptions import TwilioException
        log_error(e, "Error sending welcome message", source='twilio')
        raise Exception(f"Error sending welcome message: {e}")


def print_participants(conversation_sid, label="Participants"):
    """Helper function to print all participants in a conversation"""
    try:
        # Ensure client is initialized
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()
            
        participants = client.conversations.v1.conversations(conversation_sid).participants.list()
        
        # Build participant details
        participant_details = []
        for i, p in enumerate(participants, 1):
            participant_info = {
                'number': i,
                'sid': p.sid,
                'identity': getattr(p, 'identity', 'None'),
                'date_created': str(getattr(p, 'date_created', 'None'))
            }
            
            if hasattr(p, 'messaging_binding') and p.messaging_binding:
                binding = p.messaging_binding
                participant_info['messaging_binding'] = {
                    'address': binding.get('address', 'None'),
                    'projected_address': binding.get('projected_address', 'None'),
                    'type': binding.get('type', 'None')
                }
            else:
                participant_info['messaging_binding'] = None
                
            participant_details.append(participant_info)
        
        log_info(
            f"{label} for Conversation",
            category='sms',
            details={
                'conversation_sid': conversation_sid,
                'total_participants': len(participants),
                'participants': participant_details
            }
        )
        
    except Exception as e:
        log_error(e, f"Error printing participants for {conversation_sid}", source='twilio')
