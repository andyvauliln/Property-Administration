
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
                
                if author not in [twilio_phone, 'ASSISTANT', manager_phone]:
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
            
            
            if event_type == 'onMessageAdded':
                log_info(f"Event type is onMessageAdded: {event_type}", category='sms')
                
                # Save incoming message to database
                if body and message_sid:  # Only save if there's actual message content and message_sid
                    # Determine direction based on author
                    direction = 'inbound' if author not in [twilio_phone, 'ASSISTANT', manager_phone] else 'outbound'
                    
                    # Ensure conversation exists in database with smart linking
                    if conversation_sid:
                        save_conversation_to_db(
                            conversation_sid=conversation_sid,
                            friendly_name=f"Conversation {conversation_sid}",
                            author=author  # Pass author for smart linking logic
                        )
                    
                    save_message_to_db(
                        message_sid=message_sid,  # Use the actual MessageSid from Twilio
                        conversation_sid=conversation_sid,
                        author=author,
                        body=body,
                        direction=direction,
                        webhook_sid=webhook_sid,
                        messaging_binding_address=messaging_binding_address,
                        messaging_binding_proxy_address=messaging_binding_proxy_address
                    )
                elif body and not message_sid:
                    log_warning("Received message without MessageSid, skipping save to DB", category='sms')
                
                # Check if author is not twilio_phone and not manager_phone
                author_is_customer = (author != twilio_phone and author != manager_phone)
                
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
            
            
    #         if event_type == 'onParticipantAdded':
    #             print_participants(conversation_sid, "WEBHOOK ON PARTICIPANT ADDED")
                
    #             return JsonResponse({'status': 'success'}, status=200)
    #         if event_type == 'onConversationUpdated':
    #             print_info(f"Event type is onConversationUpdated: {event_type}")
    #             return JsonResponse({'status': 'success'}, status=200)

           

    #         if event_type == 'onConversationAdded' and conversation_sid:
    #             print_info(f"Event type is onConversationAdded: {event_type}")

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
        
        # Add customer
        participants_config.append({
            "phone": tenant_phone
        })
        
        # Add manager  
        participants_config.append({
            "phone": manager_phone
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
