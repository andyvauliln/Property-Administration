
from django.views.decorators.http import require_http_methods
import os
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from django.views.decorators.csrf import csrf_exempt
import re
from mysite.unified_logger import log_error, log_info, log_warning, logger
from django.http import JsonResponse
import time
import twilio
from django.utils import timezone

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
                
                if author not in [twilio_phone, 'ASSISTANT', manager_phone, manager_phone_2, manager_phone_3]:
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
            end_date__gte=cutoff_date
        ).order_by('-start_date').first()
        
        return booking
        
    except Exception as e:
        log_error(e, f"Error finding booking from phone {phone_number}", source='web')
        return None


def validate_phone_number(phone):
    """
    Validate and format phone number for Twilio
    Returns None if invalid, formatted phone if valid
    """
    if not phone:
        return None
   
    # Remove any whitespace
    phone = str(phone).strip()
   
    # If it's already in E.164 format, validate it
    if phone.startswith('+'):
        # Must be + followed by 1-15 digits
        if re.match(r'^\+[1-9]\d{1,14}$', phone):
            return phone
        else:
            return None
   
    # If it's a US number without +1, add it
    # Remove any non-digit characters first
    digits_only = re.sub(r'\D', '', phone)
   
    # If it's 10 digits, assume US number
    if len(digits_only) == 10:
        return f"+1{digits_only}"
   
    # If it's 11 digits and starts with 1, assume US number
    if len(digits_only) == 11 and digits_only.startswith('1'):
        return f"+{digits_only}"
   
    # Otherwise, return None as invalid
    return None



def create_conversation_with_participants(friendly_name, participants_config):
    """
    Create a conversation with multiple participants in a single API call
    
    Args:
        friendly_name (str): Name for the conversation
        participants_config (list): List of participant configurations
            Each participant can be:
            - {"phone": "+1234567890"} for phone number participants
            - {"identity": "ASSISTANT", "projected_address": "+1234567890"} for identity-based participants
    
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
        
        # Send message to conversation using ASSISTANT identity
        message_response = client.conversations.v1.conversations(conversation_sid).messages.create(
            author='ASSISTANT',
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
            author='ASSISTANT',
            body=formatted_message,
            direction='outbound'
        )
        
        return message_response.sid
        
    except Exception as e:
        log_error(e, f"Error forwarding message to conversation {conversation_sid}", source='twilio')
        raise


def delete_conversation(conversation_sid):
    """
    Delete a conversation
    
    Args:
        conversation_sid (str): Conversation SID to delete
    """
    try:
        # Ensure client is initialized
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()
            
        client.conversations.v1.conversations(conversation_sid).delete()
        log_info(f"Deleted conversation: {conversation_sid}", category='sms')
        
    except Exception as e:
        log_error(e, f"Error deleting conversation {conversation_sid}", source='twilio')
        raise



def _is_skippable_message(text):
    """Returns True for short acknowledgment messages that don't need AI processing."""
    if not text:
        return True
    text = text.strip()
    return len(text) <= 15 and '?' not in text


def _get_ai_client():
    """Initialize OpenRouter client."""
    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENROUTER_API_KEY', '')
        if not api_key:
            return None
        return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    except Exception as e:
        log_error(e, "Failed to initialize AI client", source='web')
        return None


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


def build_full_context(conversation_sid, apartment, booking):
    """
    Full context for AI: apartment notes + structured fields + booking (all fields) +
    parking + cleanings + payments + handyman + recent chat history.
    Both apartment and booking are guaranteed non-None when called.
    """
    from mysite.models import ParkingBooking, HandymanCalendar, Cleaning, Payment, TwilioMessage
    from datetime import date

    parts = []
    today = date.today()

    # Apartment notes / knowledge base
    if apartment.notes and apartment.notes.strip():
        parts.append(f"=== APARTMENT NOTES / KNOWLEDGE BASE ===\n{apartment.notes}")

    # Apartment structured fields
    parts.append(f"=== APARTMENT FIELDS DATA ===\n{_apartment_fields_context(apartment)}")

    # Booking — all fields except keywords/metadata
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
    if parking.exists():
        lines = [
            f"- Spot #{p.parking.number} ({p.parking.notes or ''}, building: {p.parking.building or 'N/A'})"
            for p in parking
        ]
        parts.append("=== PARKING ===\n" + "\n".join(lines))

    # Cleanings for this booking
    cleanings = Cleaning.objects.filter(booking=booking).order_by('date')[:5]
    if cleanings.exists():
        lines = [f"- {c.date}: {c.status}" for c in cleanings]
        parts.append("=== CLEANINGS ===\n" + "\n".join(lines))

    # Payments for this booking — all records with payment type
    payments = Payment.objects.filter(booking=booking).select_related('payment_type').order_by('-payment_date')
    if payments.exists():
        lines = [
            f"- {p.payment_date}: ${p.amount} ({p.payment_status})"
            f" | Type: {p.payment_type.name if p.payment_type else 'N/A'}"
            for p in payments
        ]
        parts.append("=== BOOKING PAYMENTS ===\n" + "\n".join(lines))

    # Handyman appointments for this tenant
    if tenant and tenant.phone:
        handyman = HandymanCalendar.objects.filter(
            tenant_phone=tenant.phone, date__gte=today
        ).order_by('date')[:3]
        if handyman.exists():
            lines = [f"- {h.date} {h.start_time}-{h.end_time}: {h.notes}" for h in handyman]
            parts.append("=== HANDYMAN APPOINTMENTS ===\n" + "\n".join(lines))

    # Recent chat history (last 10 messages)
    messages = TwilioMessage.objects.filter(
        conversation_sid=conversation_sid
    ).order_by('-message_timestamp')[:10]
    if messages.exists():
        history = [
            f"{'Customer' if m.direction == 'inbound' else 'Assistant'}: {m.body}"
            for m in reversed(list(messages))
        ]
        parts.append("=== RECENT CHAT HISTORY ===\n" + "\n".join(history))

    return "\n\n".join(parts)


def ai_answer_customer(conversation_sid, message_body, apartment, booking):
    """
    Customer path: AI answers the tenant's message using full context.
    Returns answer/clarifying-question string, or None if no relevant info.
    """
    try:
        ai_client = _get_ai_client()
        if not ai_client:
            log_warning("OPENROUTER_API_KEY not set, skipping AI response", category='sms')
            return None

        context = build_full_context(conversation_sid, apartment, booking)

        system_prompt = (
            "You are an AI assistant for a property management company. "
            "You help tenants in a group chat with questions about their apartment stay.\n\n"
            "For each tenant message, choose ONE of:\n"
            "1. ANSWER: You have enough info → give a concise, helpful answer (max 3 sentences).\n"
            "2. CLARIFY: You have relevant info about the topic but need one detail to answer precisely "
            "→ ask ONE short clarifying question.\n"
            "3. NO_ANSWER: You have no relevant info → respond ONLY with: NO_ANSWER\n\n"
            "RULES:\n"
            "- Never fabricate — only use facts from the context.\n"
            "- Be friendly and professional.\n"
            "- Answer in the same language as the tenant's message."
        )
        user_prompt = (
            f"Context:\n{context}\n\n"
            f"Tenant message: {message_body}\n\n"
            "Respond with an answer, a clarifying question, or NO_ANSWER."
        )

        response = ai_client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=300,
        )

        answer = response.choices[0].message.content.strip()
        if not answer or answer.upper() == "NO_ANSWER":
            log_info(f"AI: no answer for customer message in {conversation_sid}", category='sms')
            return None

        log_info(f"AI responded to customer in {conversation_sid}", category='sms')
        return answer

    except Exception as e:
        log_error(e, "Error in ai_answer_customer", source='web')
        return None


def ai_extract_knowledge(conversation_sid, message_body, apartment):
    """
    Manager path — two-step knowledge extraction:
    Step 1: Check if message has new OPERATIONAL info not already in structured fields (YES/NO).
    Step 2: If YES, AI merges info into existing notes and returns updated notes text.
    Silent — no chat reply.
    """
    try:
        ai_client = _get_ai_client()
        if not ai_client:
            return

        # Step 1: Check if message has valuable operational info
        fields_ctx = _apartment_fields_context(apartment)
        check_response = ai_client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    "You evaluate whether a manager's message contains OPERATIONAL knowledge "
                    "about an apartment that should be saved in the free-text notes.\n\n"
                    "The following information is ALREADY stored in structured database fields "
                    "and must NOT be flagged as new — do not save it to notes:\n"
                    f"{fields_ctx}\n\n"
                    "Only reply YES if the message contains new OPERATIONAL knowledge such as:\n"
                    "- WiFi network name or password\n"
                    "- Door/gate/lock access codes\n"
                    "- Specific parking instructions\n"
                    "- House rules (noise, pets, smoking, guests, etc.)\n"
                    "- Appliance instructions or quirks\n"
                    "- Local tips, nearby amenities\n"
                    "- Any other operational detail NOT covered by the fields above\n\n"
                    f"Manager message: {message_body}\n\n"
                    "Reply with YES or NO only."
                )
            }],
            temperature=0,
            max_tokens=5,
        )

        has_value = check_response.choices[0].message.content.strip().upper().startswith("YES")
        if not has_value:
            log_info(f"AI: manager message has no extractable knowledge", category='sms')
            return

        log_info(f"AI: extracting knowledge from manager message for apartment {apartment.id}", category='sms')

        # Step 2: Merge new operational info into existing notes
        update_response = ai_client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": (
                    f"Current apartment notes:\n{apartment.notes or '(empty)'}\n\n"
                    f"New information to add: {message_body}\n\n"
                    "Merge the new information into the notes. Keep them clear and organized.\n"
                    "Return ONLY the updated notes text, nothing else."
                )
            }],
            temperature=0.2,
            max_tokens=1000,
        )

        updated_notes = update_response.choices[0].message.content.strip()
        if updated_notes and updated_notes != (apartment.notes or ''):
            apartment.notes = updated_notes
            apartment.save()
            log_info(f"AI updated notes for apartment {apartment.id}", category='sms')

    except Exception as e:
        log_error(e, "Error in ai_extract_knowledge", source='web')


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
                    direction = 'inbound' if author not in [twilio_phone, 'ASSISTANT', manager_phone, manager_phone_2, manager_phone_3] else 'outbound'

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
                if os.environ.get('AI_ASSISTANT_ENABLED', 'true').lower() == 'true' and body and author and author != 'ASSISTANT':
                    try:
                        from mysite.models import TwilioConversation, Apartment, Booking
                        _conv = TwilioConversation.objects.filter(conversation_sid=conversation_sid).first()

                        if _conv and _conv.apartment_id and _conv.booking_id:
                            _apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=_conv.apartment_id)
                            _booking = Booking.objects.select_related('tenant').get(id=_conv.booking_id)
                            _is_customer = author not in [twilio_phone, manager_phone, manager_phone_2, manager_phone_3]

                            if _is_customer:
                                # Customer → skip short ack messages, then AI tries to answer/clarify
                                if not _is_skippable_message(body):
                                    _ai_resp = ai_answer_customer(conversation_sid, body, _apartment, _booking)
                                    if _ai_resp:
                                        send_messsage_by_sid(conversation_sid, 'ASSISTANT', _ai_resp, twilio_phone, None)
                            else:
                                # Manager → AI extracts knowledge silently
                                ai_extract_knowledge(conversation_sid, body, _apartment)

                    except Exception as e:
                        log_error(e, "Error in AI message routing", source='web')
                # --- end AI processing ---
                # Check if author is not twilio_phone and not manager_phone
                author_is_customer = (author != twilio_phone and author not in [manager_phone, manager_phone_2, manager_phone_3])
                
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
                        
                        # Save new group conversation with smart linking for customer
                        save_conversation_to_db(
                            conversation_sid=new_conversation_sid,
                            friendly_name=friendly_name,
                            author=author  # Pass customer phone for smart linking
                        )
                        
                        # Forward the message to the new group conversation
                        time.sleep(6)
                        if body:
                            forward_message_to_conversation(new_conversation_sid, author, body)
                        
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
        
        # Add assistant
        participants_config.append({
            "identity": "ASSISTANT",
            "projected_address": twilio_phone
        })
        
        conversation_sid = create_conversation_with_participants(friendly_name, participants_config)
        
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


def send_messsage_by_sid(conversation_sid, author, message, sender_phone, receiver_phone):
    try:
        # Ensure client is initialized
        global client
        if client is None:
            log_info("Twilio client not initialized, attempting to initialize...", category='sms')
            client = get_twilio_client()
            
        # Retry sending in case the conversation is still initializing
        max_attempts = 6
        delay_seconds = 0.5
        for attempt in range(1, max_attempts + 1):
            try:
                twilio_message = client.conversations.v1.conversations(
                    conversation_sid
                ).messages.create(
                    body=message,
                    author=author,
                )
                log_info(
                    f"Message sent via Twilio",
                    category='sms',
                    details={'message_sid': twilio_message.sid, 'conversation_sid': conversation_sid}
                )
                
                # Save outbound message to database
                save_message_to_db(
                    message_sid=twilio_message.sid,
                    conversation_sid=conversation_sid,
                    author=author,
                    body=message,
                    direction='outbound'
                )
                
                break
            except Exception as e:
                from twilio.base.exceptions import TwilioRestException
                is_initializing = (getattr(e, "status", None) == 409) or ("initializing" in str(e).lower())
                if is_initializing and attempt < max_attempts:
                    log_info(
                        f"Conversation {conversation_sid} is initializing; retrying in {delay_seconds}s (attempt {attempt}/{max_attempts})",
                        category='sms'
                    )
                    time.sleep(delay_seconds)
                    delay_seconds = min(delay_seconds * 2, 4.0)
                    continue
                else:
                    log_error(e, f"Error sending message via Twilio (attempt {attempt})", source='twilio')
                    raise
        
    except Exception as e:
        log_error(e, "Error sending message via Twilio", source='twilio')
        raise Exception(f"Error sending message via Twilio: {e}")


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
            f" {booking.tenant.full_name or 'Tenant'} Chat Apt: {booking.apartment.name}",
            validated_phone
        )
        
        if conversation_sid:
            log_info(f"Conversation created: {conversation_sid}", category='sms')
            send_messsage_by_sid(conversation_sid, "ASSISTANT", f"Hi, {booking.tenant.full_name or 'Dear guest'}, this is your contract for booking apartment {booking.apartment.name}. from {booking.start_date} to {booking.end_date}. Please sign it here: {contract_url}", twilio_phone_secondary, validated_phone)
        else:
            log_warning("Conversation wasn't created", category='sms')
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
            f" {booking.tenant.full_name or 'Tenant'} Chat Apt: {booking.apartment.name}",
            validated_phone
        )
        
        if conversation_sid:
            log_info(f"Conversation created: {conversation_sid}", category='sms')
            send_messsage_by_sid(conversation_sid, "ASSISTANT", f"Hi, {booking.tenant.full_name or 'Dear guest'}, This chat for renting apartment {booking.apartment.name}. from {booking.start_date} to {booking.end_date}.", twilio_phone_secondary, validated_phone)
        else:
            log_warning("Conversation wasn't created", category='sms')
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
