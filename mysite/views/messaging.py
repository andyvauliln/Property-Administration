
from django.views.decorators.http import require_http_methods
import os
from twilio.rest import Client
from django.views.decorators.csrf import csrf_exempt
import re
import logging
from django.http import JsonResponse
import time
import twilio
from django.utils import timezone

# Debug: Check Twilio version at runtime

def print_info(message):
    print(message)
    logger_sms.debug(message)

logger_sms = logging.getLogger('mysite.sms_webhooks')

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
        logger_sms.error(f"Failed to initialize Twilio client: {e}")
        raise

# Initialize client at module level
try:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not account_sid or not auth_token:
        logger_sms.warning("Twilio credentials not found in environment variables")
        client = None
    else:
        client = Client(account_sid, auth_token)
except Exception as e:
    logger_sms.error(f"Error initializing Twilio client: {e}")
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
            print_info(f"Saved new conversation to DB: {conversation_sid}")
            
            # Try to link booking/apartment for new customer conversations only
            if not booking and not apartment and author:
                # Only try to link if author is a customer (not system phones)
                twilio_phone = "+13153524379"
                manager_phone = "+15612205252"
                
                if author not in [twilio_phone, 'ASSISTANT', manager_phone]:
                    print_info(f"Attempting to link conversation to booking for customer: {author}")
                    booking = get_booking_from_phone(author)
                    if booking:
                        conversation.booking = booking
                        conversation.apartment = booking.apartment
                        conversation.save()
                        print_info(f"Linked conversation to booking: {booking} and apartment: {booking.apartment}")
                    else:
                        print_info(f"No booking found for customer: {author} - conversation can be linked later")
                else:
                    print_info(f"Author {author} is system user - skipping booking lookup")
        else:
            print_info(f"Found existing conversation in DB: {conversation_sid}")
            
        return conversation
        
    except Exception as e:
        print_info(f"Error saving conversation to DB: {e}")
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
            print_info(f"Conversation not found: {conversation_sid}")
            return False
            
        # Skip if already linked
        if conversation.booking and conversation.apartment:
            print_info(f"Conversation already linked to booking: {conversation.booking}")
            return True
            
        # Try to find booking
        booking = get_booking_from_phone(phone_number)
        if booking:
            conversation.booking = booking
            conversation.apartment = booking.apartment
            conversation.save()
            print_info(f"Updated conversation {conversation_sid} with booking: {booking} and apartment: {booking.apartment}")
            return True
        else:
            print_info(f"No booking found for phone: {phone_number}")
            return False
            
    except Exception as e:
        print_info(f"Error updating conversation booking link: {e}")
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
            print_info(f"Invalid phone number for group check: {author_phone}")
            return False
            
        print_info(f"Checking if {validated_phone} exists in group conversations for apartment {apartment_id}")
        
        # If apartment_id provided, check database first for more efficient lookup
        if apartment_id:
            from mysite.models import TwilioConversation
            
            # Check if there's already a conversation linked to this apartment with this tenant
            existing_conversation = TwilioConversation.objects.filter(
                apartment_id=apartment_id,
                messages__author=validated_phone
            ).first()
            
            if existing_conversation:
                print_info(f"Found existing conversation {existing_conversation.conversation_sid} for apartment {apartment_id}")
                return True
        
        # Fallback to original Twilio API check for all group conversations
        return check_author_in_group_conversations(author_phone)
        
    except Exception as e:
        print_info(f"Error in check_author_in_group_conversations_for_apartment: {e}")
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
                print_info(f"Saved message to DB: {message_sid} from {author}")
            else:
                print_info(f"Message already exists in DB: {message_sid}")
                
            return message
        else:
            print_info(f"Could not find or create conversation for message: {message_sid}")
            return None
            
    except Exception as e:
        print_info(f"Error saving message to DB: {e}")
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
        print_info(f"Error finding booking from phone {phone_number}: {e}")
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
            print_info("Twilio client not initialized, attempting to initialize...")
            client = get_twilio_client()
        
        participant_list = []
        
        for config in participants_config:
            # Validate phone numbers before creating JSON
            if "phone" in config:
                validated_phone = validate_phone_number(config["phone"])
                if not validated_phone:
                    print_info(f"Invalid phone number in config: {config}")
                    continue
                config["phone"] = validated_phone
            
            if "projected_address" in config:
                validated_projected = validate_phone_number(config["projected_address"])
                if not validated_projected:
                    print_info(f"Invalid projected address in config: {config}")
                    continue
                config["projected_address"] = validated_projected
            
            if "identity" in config and "projected_address" in config:
                # Identity-based participant with projected address
                participant_json = f'{{"identity": "{config["identity"]}", "messaging_binding": {{"projected_address": "{config["projected_address"]}"}}}}'
            elif "phone" in config:
                # Phone number participant
                participant_json = f'{{"messaging_binding": {{"address": "{config["phone"]}"}}}}'
            else:
                print_info(f"Invalid participant config: {config}")
                continue
                
            participant_list.append(participant_json)
        
        print_info(f"Participant list: {participant_list}")
        if not participant_list:
            raise ValueError("No valid participants provided")
        
        conversation_with_participant = client.conversations.v1.conversation_with_participants.create(
            friendly_name=friendly_name,
            participant=participant_list,
        )
        
        conversation_sid = conversation_with_participant.sid
        print_info(f'Created conversation "{friendly_name}" with {len(participant_list)} participants: {conversation_sid}')
        
        return conversation_sid
        
    except Exception as e:
        print_info(f"Error creating conversation with participants: {e}")
        # Log more details about the error for debugging
        import traceback
        print_info(f"Full traceback: {traceback.format_exc()}")
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
            print_info(f"Invalid phone number for group check: {author_phone}")
            return False
            
        print_info(f"Checking if {validated_phone} exists in group conversations")
        
        # Ensure client is initialized
        global client
        if client is None:
            print_info("Twilio client not initialized, attempting to initialize...")
            client = get_twilio_client()
        
        # Get all conversations
        conversations = client.conversations.v1.conversations.list()
        
        for conv in conversations:
            try:
                # Get participants for this conversation
                participants = client.conversations.v1.conversations(conv.sid).participants.list()
                participant_count = len(participants)
                
                print_info(f"Conversation {conv.sid} has {participant_count} participants")
                
                # Check if this conversation has more than 2 participants
                if participant_count > 2:
                    # Check if our author is in this conversation
                    for participant in participants:
                        if hasattr(participant, 'messaging_binding') and participant.messaging_binding:
                            binding = participant.messaging_binding
                            participant_address = binding.get('address', '')
                            if participant_address == validated_phone:
                                print_info(f"Found author {validated_phone} in group conversation {conv.sid}")
                                return True
                                
            except Exception as e:
                print_info(f"Error checking conversation {conv.sid}: {e}")
                continue
                
        print_info(f"Author {validated_phone} not found in any group conversations")
        return False
        
    except Exception as e:
        print_info(f"Error in check_author_in_group_conversations: {e}")
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
            print_info("Twilio client not initialized, attempting to initialize...")
            client = get_twilio_client()
            
        formatted_message = f">>Customer: {author} - {message}"
        
        # Send message to conversation using ASSISTANT identity
        message_response = client.conversations.v1.conversations(conversation_sid).messages.create(
            author='ASSISTANT',
            body=formatted_message
        )
        
        print_info(f"Forwarded message to conversation {conversation_sid}: {formatted_message}")
        
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
        print_info(f"Error forwarding message to conversation {conversation_sid}: {e}")
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
            print_info("Twilio client not initialized, attempting to initialize...")
            client = get_twilio_client()
            
        client.conversations.v1.conversations(conversation_sid).delete()
        print_info(f"Deleted conversation: {conversation_sid}")
        
    except Exception as e:
        print_info(f"Error deleting conversation {conversation_sid}: {e}")
        raise


@csrf_exempt
@require_http_methods(["POST", "GET"])
def twilio_webhook(request):
    print_info("**********CONVERSATION_CREATED_WEBHOOK **********")
    try:
        if request.method == 'POST':
            data = request.POST
            print_info(f"Data: {data}")
            event_type = data.get('EventType', None)
            webhook_sid = data.get('WebhookSid', None)
            message_sid = data.get('MessageSid', None)  # Extract actual MessageSid from webhook
            conversation_sid = data.get('ConversationSid', None)
            author = data.get('Author', None)
            body = data.get('Body', None)
            messaging_binding_address = data.get('MessagingBinding.Address', None)
            messaging_binding_proxy_address = data.get('MessagingBinding.ProxyAddress', None)
            print_info(f"Body: {body}")
            print_info(f"MessageSid: {message_sid}")
            print_info(f"WebhookSid: {webhook_sid}")
            print_info(f"ConversationSid: {conversation_sid}")
            print_info(f"EventType: {event_type}")
            print_info(f"Author: {author}")
            print_info(f"Messaging Binding Address: {messaging_binding_address}")
            print_info(f"Messaging Binding Proxy Address: {messaging_binding_proxy_address}")
            twilio_phone = "+13153524379"
            manager_phone = "+15612205252"
            
            
            if event_type == 'onMessageAdded':
                print_info(f"Event type is onMessageAdded: {event_type}")
                
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
                    print_info(f"Warning: Received message without MessageSid, skipping save to DB")
                
                # Check if author is not twilio_phone and not manager_phone
                author_is_customer = (author != twilio_phone and author != manager_phone)
                
                if author_is_customer:
                    print_info(f"Author {author} is a customer, checking for existing group conversations")
                    
                    # Get customer's current booking context to determine if we need a new conversation
                    customer_booking = get_booking_from_phone(author)
                    apartment_id = customer_booking.apartment.id if customer_booking and customer_booking.apartment else None
                    
                    # Check if author exists in group conversation for this specific apartment
                    author_in_group = check_author_in_group_conversations_for_apartment(author, apartment_id)
                    
                    if not author_in_group:
                        print_info(f"Author {author} not found in group conversations, creating new group conversation and forwarding message")
                        
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
                        
                        print_info(f"Creating new group conversation with participants: {participants_config}")
                        
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
                                print_info(f"Deleted old conversation: {conversation_sid}")
                            except Exception as e:
                                print_info(f"Warning: Could not delete old conversation {conversation_sid}: {e}")
                        
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
        print_info(f"Error in twilio_webhook: {e}")
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
        
        print_info(f'Created conversation "{friendly_name}" with {len(participants_config)} participants: {conversation_sid}')
        return conversation_sid
        
    except Exception as e:
        print_info(f"Error creating conversation with participants: {e}")
        raise


def send_messsage_by_sid(conversation_sid, author, message, sender_phone, receiver_phone):
    try:
        # Ensure client is initialized
        global client
        if client is None:
            print_info("Twilio client not initialized, attempting to initialize...")
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
                print_info(f"Message sent via Twilio: {twilio_message.sid}")
                
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
                    print_info(
                        f"Conversation {conversation_sid} is initializing; retrying in {delay_seconds}s (attempt {attempt}/{max_attempts})"
                    )
                    time.sleep(delay_seconds)
                    delay_seconds = min(delay_seconds * 2, 4.0)
                    continue
                else:
                    print_info(f"Error sending message via Twilio (attempt {attempt}): {e}")
                    raise
        
    except Exception as e:
        print_info(f"Error sending message via Twilio: {e}")
        raise Exception(f"Error sending message via Twilio: {e}")


def sendContractToTwilio(booking, contract_url):
    try:
        twilio_phone_secondary = os.environ.get("TWILIO_PHONE_SECONDARY")
        
        # Log tenant phone for debugging
        print_info(f"Attempting to send contract to tenant phone: {booking.tenant.phone}")
        
        # Validate tenant phone
        if not booking.tenant.phone:
            print_info(f"ERROR: Tenant phone is empty/None for booking {booking.id}")
            raise Exception(f"Tenant phone is empty/None for booking {booking.id}")
        
        validated_phone = validate_phone_number(booking.tenant.phone)
        if not validated_phone:
            print_info(f"ERROR: Invalid tenant phone format: '{booking.tenant.phone}' (Type: {type(booking.tenant.phone).__name__}) for booking {booking.id}, tenant: {booking.tenant.full_name}")
            raise Exception(f"Invalid tenant phone format: '{booking.tenant.phone}' for booking {booking.id}")
        
        print_info(f"Validated tenant phone: {validated_phone}")
        
        conversation_sid = create_conversation_config(
            f" {booking.tenant.full_name or 'Tenant'} Chat Apt: {booking.apartment.name}",
            validated_phone
        )
        
        if conversation_sid:
            print_info(f"Conversation created: {conversation_sid}")
            send_messsage_by_sid(conversation_sid, "ASSISTANT", f"Hi, {booking.tenant.full_name or 'Dear guest'}, this is your contract for booking apartment {booking.apartment.name}. from {booking.start_date} to {booking.end_date}. Please sign it here: {contract_url}", twilio_phone_secondary, validated_phone)
        else:
            print_info("Conversation wasn't created")
    except Exception as e:
        from twilio.base.exceptions import TwilioException
        print_info(f"Error sending contract message: {e}")
        raise Exception(f"Error sending contract message: {e}")


def sendWelcomeMessageToTwilio(booking):
    try:
        twilio_phone_secondary = os.environ.get("TWILIO_PHONE_SECONDARY")
        
        # Log tenant phone for debugging
        print_info(f"Attempting to send welcome message to tenant phone: {booking.tenant.phone}")
        
        # Validate tenant phone
        if not booking.tenant.phone:
            print_info(f"ERROR: Tenant phone is empty/None for booking {booking.id}")
            raise Exception(f"Tenant phone is empty/None for booking {booking.id}")
        
        validated_phone = validate_phone_number(booking.tenant.phone)
        if not validated_phone:
            print_info(f"ERROR: Invalid tenant phone format: '{booking.tenant.phone}' (Type: {type(booking.tenant.phone).__name__}) for booking {booking.id}, tenant: {booking.tenant.full_name}")
            raise Exception(f"Invalid tenant phone format: '{booking.tenant.phone}' for booking {booking.id}")
        
        print_info(f"Validated tenant phone: {validated_phone}")
        
        conversation_sid = create_conversation_config(
            f" {booking.tenant.full_name or 'Tenant'} Chat Apt: {booking.apartment.name}",
            validated_phone
        )
        
        if conversation_sid:
            print_info(f"Conversation created: {conversation_sid}")
            send_messsage_by_sid(conversation_sid, "ASSISTANT", f"Hi, {booking.tenant.full_name or 'Dear guest'}, This chat for renting apartment {booking.apartment.name}. from {booking.start_date} to {booking.end_date}.", twilio_phone_secondary, validated_phone)
        else:
            print_info("Conversation wasn't created")
    except Exception as e:
        from twilio.base.exceptions import TwilioException
        print_info(f"Error sending welcome message: {e}")
        raise Exception(f"Error sending welcome message: {e}")


def print_participants(conversation_sid, label="Participants"):
    """Helper function to print all participants in a conversation"""
    try:
        # Ensure client is initialized
        global client
        if client is None:
            print_info("Twilio client not initialized, attempting to initialize...")
            client = get_twilio_client()
            
        participants = client.conversations.v1.conversations(conversation_sid).participants.list()
        print_info(f"\n========== {label} for Conversation {conversation_sid} ==========")
        print_info(f"Total participants: {len(participants)}")
        
        for i, p in enumerate(participants, 1):
            print_info(f"Participant {i}:")
            print_info(f"  - SID: {p.sid}")
            print_info(f"  - Identity: {getattr(p, 'identity', 'None')}")
            print_info(f"  - Date Created: {getattr(p, 'date_created', 'None')}")
            
            if hasattr(p, 'messaging_binding') and p.messaging_binding:
                binding = p.messaging_binding
                print_info(f"  - Messaging Binding:")
                print_info(f"    - Address: {binding.get('address', 'None')}")
                print_info(f"    - Projected Address: {binding.get('projected_address', 'None')}")
                print_info(f"    - Type: {binding.get('type', 'None')}")
            else:
                print_info(f"  - Messaging Binding: None")
        print_info(f"========== End {label} ==========\n")
        
    except Exception as e:
        print_info(f"Error printing participants: {e}")
