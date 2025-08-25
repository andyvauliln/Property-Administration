from sys import int_info
from ..models import Booking, Chat, User
import json
from django.views.decorators.http import require_http_methods
import os
import requests
from django.http import HttpResponse, HttpResponseServerError
from twilio.twiml.messaging_response import MessagingResponse
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
from django.views.decorators.csrf import csrf_exempt
import re
import logging
from django.http import JsonResponse
import time
import twilio

# Debug: Check Twilio version at runtime


logger_sms = logging.getLogger('mysite.sms_webhooks')
account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
client = Client(account_sid, auth_token)


def print_info(message):
    print(message)
    logger_sms.debug(message)

print_info(f"=== TWILIO VERSION DEBUG 2 ===")
print_info(f"Twilio version: {twilio.__version__}")
print_info(f"Twilio path: {twilio.__file__}")
print_info(f"===========================")


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


# def get_customer_phone_from_webhook(author, messaging_binding_address):
#     """
#     Extract valid customer phone number from webhook data
#     """
#     # Try author field first
#     customer_phone = validate_phone_number(author)
#     if customer_phone:
#         print_info(f"Using author as customer phone: {customer_phone}")
#         return customer_phone
    
#     # Try messaging binding address as fallback
#     customer_phone = validate_phone_number(messaging_binding_address)
#     if customer_phone:
#         print_info(f"Using messaging binding address as customer phone: {customer_phone}")
#         return customer_phone
    
#     print_info(f"No valid customer phone found - Author: {author}, Messaging Address: {messaging_binding_address}")
#     return None


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
        formatted_message = f">>Customer: {author} - {message}"
        
        # Send message to conversation using ASSISTANT identity
        message_response = client.conversations.v1.conversations(conversation_sid).messages.create(
            author='ASSISTANT',
            body=formatted_message
        )
        
        print_info(f"Forwarded message to conversation {conversation_sid}: {formatted_message}")
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
        client.conversations.v1.conversations(conversation_sid).delete()
        print_info(f"Deleted conversation: {conversation_sid}")
        
    except Exception as e:
        print_info(f"Error deleting conversation {conversation_sid}: {e}")
        raise


@csrf_exempt
@require_http_methods(["POST", "GET"])
def conversation_created_webhook(request):
    print_info("**********CONVERSATION_CREATED_WEBHOOK **********")
    try:
        if request.method == 'POST':
            data = request.POST
            print_info(f"Data: {data}")
            event_type = data.get('EventType', None)
            webhook_sid = data.get('WebhookSid', None)
            conversation_sid = data.get('ConversationSid', None)
            author = data.get('Author', None)
            body = data.get('Body', None)
            messaging_binding_address = data.get('MessagingBinding.Address', None)
            messaging_binding_proxy_address = data.get('MessagingBinding.ProxyAddress', None)
            print_info(f"Body: {body}")
            print_info(f"WebhookSid: {webhook_sid}")
            print_info(f"ConversationSid: {conversation_sid}")
            print_info(f"EventType: {event_type}")
            print_info(f"Author: {author}")
            print_info(f"Messaging Binding Address: {messaging_binding_address}")
            print_info(f"Messaging Binding Proxy Address: {messaging_binding_proxy_address}")
            twilio_phone = "+13153524379"
            manager_phone = "+17282001917"
            
            
            if event_type == 'onMessageAdded':
                print_info(f"Event type is onMessageAdded: {event_type}")
                
                # Check if author is not twilio_phone and not manager_phone
                author_is_customer = (author != twilio_phone and author != manager_phone)
                
                if author_is_customer:
                    print_info(f"Author {author} is a customer, checking for existing group conversations")
                    
                    # Check if author exists in any group conversation (>2 participants)
                    author_in_group = check_author_in_group_conversations(author)
                    
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
                        
                        new_conversation_sid = create_conversation_with_participants(
                            f"Customer Support Group - {author}", 
                            participants_config
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
        print_info(f"Error in conversation_created_webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)



def print_participants(conversation_sid, label="Participants"):
    """Helper function to print all participants in a conversation"""
    try:
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
