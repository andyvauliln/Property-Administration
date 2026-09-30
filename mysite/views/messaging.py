
from django.views.decorators.http import require_http_methods
import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from django.views.decorators.csrf import csrf_exempt
import re
from mysite.unified_logger import log_error, log_info, log_warning, logger
from mysite.group_chat_logger import (
    log_message_received,
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

KB_SUFFIX = "(+)"  # older Virtual Assistant messages marked with (+) as manager knowledge (still recognised)
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



PHOTO_PLACEHOLDER = '[photo]'


def _store_webhook_media(message, post):
    """Downloads the photos of a webhook message. Never raises: a media failure must not lose the message."""
    if message is None or not post.get('Media'):
        return []
    try:
        from mysite.twilio_media import parse_webhook_media, store_message_media
        chat_service_sid, items = parse_webhook_media(post)
        return store_message_media(message, chat_service_sid, items) if items else []
    except Exception as e:
        log_error(e, "Error storing webhook media", source='twilio')
        return []


def _route_to_ai_agent(conversation_sid, message_sid, author, body):
    """
    Queues a stored chat message for the Claude agent: tenant messages (to answer) and manager messages (to
    update issues, follow-ups and the knowledge base). The agent decides per apartment whether it may send.
    """
    from mysite.models import TwilioConversation

    if author in ('ASSISTANT', 'Virtual Assistant'):
        # The assistant's own messages; "(+++)" marks a chat-page test message sent as the tenant
        body = _extract_marked_body((body or '').strip(), CLIENT_SUFFIX)
        if not body:
            return
        is_tenant = True
    elif author == TWILIO_ASSISTANT_PHONE:
        return
    else:
        is_tenant = author not in get_manager_phones()
    conv = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
    if not (conv and conv.apartment_id and conv.booking_id):
        log_no_conv_link(conversation_sid or '', author or '', body or '')
        return
    if not is_tenant:
        _enqueue_staff_for_ai_agent(conversation_sid, message_sid, body)
        return
    if not _enqueue_for_ai_agent(conversation_sid, message_sid, body):
        # There is no fallback AI any more: tell the team so a person answers
        from mysite.ai_agent.notify import notify_ai_chat
        log_ai_error(conversation_sid or '', "AI agent queue", "tenant message could not be queued")
        notify_ai_chat(f"⚠️ A tenant message could not be queued for the AI agent - please answer it yourself.\n"
                       f"Chat {conversation_sid}: {body[:300]}")


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
    """Client for the chat-page AI helpers (the Claude one-shot client). Returns (client, error_reason)."""
    from mysite.ai_agent.oneshot import ClaudeTextClient
    return ClaudeTextClient(), None


def _get_db_model(fallback=None):
    """Model of the chat-page AI helpers: the Claude one-shot model."""
    from mysite.ai_agent import config as ai_agent_config
    return ai_agent_config.oneshot_model()


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
    global_kb_text = get_global_knowledge_base_text()
    global_kb_texts = [global_kb_text] if global_kb_text else []
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


def _drop_empty_greeting_name(message):
    """Tidy a greeting whose name placeholder rendered empty: 'Hi , ...' -> 'Hi, ...'."""
    return re.sub(r'\b(Hi|Hello|Hey|Dear)\s+([,!.])', r'\1\2', message)


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
    from mysite.ai_agent import prompt_library
    from mysite.models import AIManagement
    if prompt_key in prompt_library.BY_KEY:
        content = prompt_library.raw(prompt_key)
    else:
        entry = AIManagement.objects.filter(entry_type=AIManagement.ENTRY_TYPE_PROMPT, prompt_key=prompt_key).first()
        content = (entry.content or '').strip() if entry else ''
    if content:
        try:
            return content.format(**placeholders), True
        except (KeyError, IndexError, ValueError):
            log_warning(f"Prompt {prompt_key} has missing placeholders", category='sms')
            return None, False
    return None, False


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


REGENERATE_ALL_MAX = int(os.environ.get('AI_AGENT_REGENERATE_ALL_MAX', 30))


def start_agent_regenerate(conversation_sid, message=None):
    """
    Claude backend: queue "Generate AI" (one message) or "Generate all" (every tenant message, newest
    REGENERATE_ALL_MAX) for the agent in a background thread (mysite.ai_agent.regenerate). Never sends anything.
    """
    from mysite.ai_agent import regenerate
    from mysite.models import TwilioConversation

    conversation = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()
    if not conversation:
        return {'success': False, 'error': 'Conversation not found.'}
    if not conversation.apartment_id or not conversation.booking_id:
        return {'success': False, 'error': 'Conversation must be linked to an apartment and booking.'}
    if message is not None and not is_customer_message(message):
        return {'success': False, 'error': 'Not a customer message.'}

    candidates = [message] if message is not None else [
        m for m in conversation.messages.order_by('message_timestamp', 'id') if is_customer_message(m)
    ]
    eligible = [m for m in candidates if not _is_skippable_message(get_customer_message_body(m))]
    if not eligible:
        return {'success': False, 'error': 'Message too short for AI processing.', 'skipped': True,
                'message_id': getattr(message, 'id', None)}
    limited = eligible[-REGENERATE_ALL_MAX:]
    run_ids = regenerate.start(limited)
    return {
        'success': True,
        'queued': True,
        'run_ids': run_ids,
        'message_id': getattr(message, 'id', None),
        'total_customer_messages': len(candidates),
        'skipped': len(candidates) - len(eligible),
        'not_queued_over_limit': len(eligible) - len(limited),
    }


# Backward-compatible aliases used by chat modal prompt editor URLs
AI_ANSWER_RULE_GENERATE_KEY = 'ai_answer_rule_generate'
AI_KB_RULE_GENERATE_KEY = 'ai_kb_rule_generate'
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
    "You write one concise standing rule for the knowledge-base rules of a property-management AI assistant.\n"
    "The rule tells the assistant what it must (or must never) write into the knowledge base when staff or tenants "
    "say something like this.\n"
    "Target: {scope_label}\n\n"
    "Manager message:\n{manager_message}\n\n"
    "User guidance — what should be added to or excluded from the knowledge base:\n{guidance}\n\n"
    "{kb_context_section}"
    "Rule intent: {rule_intent_label}\n\n"
    "Write ONE bullet rule (starting with '- ').\n"
    "For exclude rules, phrase what must never be saved; for include rules, what must always be saved.\n"
    "Do not paste the full manager message — encode a reusable policy.\n"
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

# Names, defaults and descriptions of every prompt: mysite/ai_agent/prompt_library.py (the live text is in AIManagement)


def get_ai_prompt_template(prompt_key):
    """Returns (raw template, from_db, description) of a registry prompt; a missing row is created from its default."""
    from mysite.ai_agent import prompt_library

    if prompt_key not in prompt_library.BY_KEY:
        return None, False, ''
    spec = prompt_library.spec(prompt_key)
    return prompt_library.raw(prompt_key), True, prompt_library.description_of(spec)


def save_ai_prompt_template(prompt_key, content, description=None):
    """Saves a chat-editable prompt. The description always comes from the prompt registry."""
    from mysite.ai_agent import prompt_library

    if prompt_key not in prompt_library.BY_KEY:
        raise ValueError(f'Unsupported prompt key: {prompt_key}')
    return prompt_library.save(prompt_key, content)


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


def _bullet(rule_text):
    rule_text = (rule_text or '').strip()
    if not rule_text:
        raise ValueError('Rule is empty.')
    return rule_text if rule_text.startswith('-') else f'- {rule_text}'


def save_answer_rule_as_lesson(rule_text, apartment=None, author=None, conversation_sid=None):
    """
    A "Teach AI answer" rule becomes a line of the 'ai_agent_answer_lessons' prompt, which the agent's system
    prompt includes as ANSWER_LESSONS. apartment=None -> company-wide. Returns a short detail.
    """
    from mysite.ai_agent import knowledge, prompt_library

    rule = _bullet(rule_text).lstrip('- ').strip()
    key = knowledge.normalize_key(' '.join(rule.split()[:6])) or 'answer_lesson'
    return prompt_library.upsert_lesson(apartment, key, rule)


def append_rule_to_agent_kb_rules(rule_text, scope='apartment'):
    """
    Claude backend: an "Add KB rule" bullet goes to AIManagement 'ai_agent_kb_rules', which the agent's system
    prompt includes as KB RULES (the legacy KB extract prompts are not used by the agent). Returns the new content.
    """
    from mysite.ai_agent import config as ai_agent_config, prompt_library

    scope = _normalize_kb_rule_scope(scope)
    label = 'apartment KB' if scope == 'apartment' else 'company-wide KB'
    rule = _bullet(rule_text)
    return prompt_library.append_rule(ai_agent_config.AI_AGENT_KB_RULES_KEY, f"- [{label}] {rule.lstrip('- ').strip()}")


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


GLOBAL_KB_KEY = 'global_knowledge_base'


def get_global_knowledge_base_text():
    from mysite.models import AIManagement

    # Only its own row: other 'knowledge'-type rows (e.g. old settings) are not knowledge base text
    entry = AIManagement.objects.filter(prompt_key=GLOBAL_KB_KEY).first()
    return (entry.content or '').strip() if entry else ''


def save_global_knowledge_base_text(text, conversation_sid=None):
    from mysite.models import AIManagement

    text = (text or '').strip()
    # Never delete other rows here: this used to wipe every 'knowledge'-type row, including the
    # ai_clickup_writes switch (2026-09-28)
    if not text:
        AIManagement.objects.filter(prompt_key=GLOBAL_KB_KEY).delete()
        return None
    description = f'Updated from chat {conversation_sid}' if conversation_sid else 'Updated from AI Management'
    entry, _ = AIManagement.objects.update_or_create(
        prompt_key=GLOBAL_KB_KEY,
        defaults={'name': 'Global Knowledge Base', 'content': text,
                  'entry_type': AIManagement.ENTRY_TYPE_KNOWLEDGE, 'description': description},
    )
    return entry


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

                    saved_message = save_message_to_db(
                        message_sid=message_sid,  # Use the actual MessageSid from Twilio
                        conversation_sid=conversation_sid,
                        author=author,
                        body=body_to_save,
                        direction=direction,
                        webhook_sid=webhook_sid,
                        messaging_binding_address=messaging_binding_address,
                        messaging_binding_proxy_address=messaging_binding_proxy_address
                    )
                    media_items = _store_webhook_media(saved_message, data)
                else:
                    media_items = []
                    log_warning("Received onMessageAdded without MessageSid, skipping message save to DB", category='sms')

                # --- AI: the Claude agent (ai-agent worker) handles every chat message ---
                # Photos: the agent sees the images themselves, so a photo-only (or "ok" + photo) message
                # still reaches it
                ai_body = f"{(body or '').strip()} {PHOTO_PLACEHOLDER}".strip() if media_items else body
                if ai_body and author and message_sid:
                    _route_to_ai_agent(conversation_sid, message_sid, author, ai_body)
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



def find_reusable_group_conversation(booking, tenant_phone):
    """
    Return the conversation_sid of this tenant's existing group chat for the same booking or apartment,
    or None. Without this every contract re-send / welcome message opened a new chat: Twilio's 409 dedupe
    only fires for an identical participant set, and the staff list changes over time (see
    data/tenant_multiple_conversations_report.md). A candidate is reused only if Twilio confirms it is not
    closed, the tenant is still a participant, and the assistant identity is in it (so our sends are accepted;
    Twilio-made regroups/1:1 threads have no identity and are skipped). Any error -> None (caller creates).
    """
    try:
        from django.db.models import F, Max, Q
        from mysite.models import TwilioConversation
        validated = validate_phone_number(tenant_phone)
        if not validated or not booking:
            return None
        scope = Q(booking_id=booking.id)
        if booking.apartment_id:
            scope |= Q(apartment_id=booking.apartment_id)
        tenant_in = (Q(messages__author=validated) | Q(messages__messaging_binding_address=validated)
                     | Q(booking__tenant__phone=validated))
        # The chat the tenant wrote in last comes first (same "main chat" rule as conversation_groups)
        candidates = (TwilioConversation.objects.filter(scope).filter(tenant_in).distinct()
                      .annotate(last_msg=Max('messages__message_timestamp'),
                                tenant_last=Max('messages__message_timestamp', filter=Q(messages__author=validated)))
                      .order_by(F('tenant_last').desc(nulls_last=True), F('last_msg').desc(nulls_last=True),
                                '-created_at')[:5])
        global client
        if client is None:
            client = get_twilio_client()
        for conv in candidates:
            try:
                resource = client.conversations.v1.conversations(conv.conversation_sid)
                if resource.fetch().state == 'closed':
                    continue
                participants = resource.participants.list()
                addresses = {(p.messaging_binding or {}).get('address') for p in participants}
                identities = {p.identity for p in participants}
                if validated in addresses and identities & {'Virtual Assistant', 'ASSISTANT'}:
                    return conv.conversation_sid
            except Exception:
                continue  # deleted in Twilio or API hiccup: try the next one
        return None
    except Exception as e:
        log_error(e, "Find reusable group conversation", source='twilio')
        return None


def create_conversation_config(friendly_name, tenant_phone, booking=None):
    """
    Create a conversation with all participants in a single API call

    Args:
        friendly_name (str): Name for the conversation
        tenant_phone (str): Tenant's phone number
        booking (Booking): Optional; when given, the tenant's existing group chat for this booking or
            apartment is reused instead of creating a new one

    Returns:
        str: conversation_sid of the created conversation
    """
    try:
        if booking is not None:
            existing_sid = find_reusable_group_conversation(booking, tenant_phone)
            if existing_sid:
                log_info(
                    f'Reusing existing group chat for "{friendly_name}"',
                    category='sms',
                    details={'conversation_sid': existing_sid, 'booking_id': booking.id}
                )
                return existing_sid

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
    return f"{booking.apartment.name} {booking.tenant.greeting_name or 'N/A'} Rental"


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
            validated_phone,
            booking=booking,
        )
        
        if conversation_sid:
            log_info(f"Conversation created: {conversation_sid}", category='sms')
            tenant_name = booking.tenant.greeting_name
            
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

            if not tenant_name:
                message = _drop_empty_greeting_name(message)

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
            validated_phone,
            booking=booking,
        )
        
        if conversation_sid:
            log_info(f"Conversation created: {conversation_sid}", category='sms')
            tenant_name = booking.tenant.greeting_name
            
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

            if not tenant_name:
                message = _drop_empty_greeting_name(message)

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
