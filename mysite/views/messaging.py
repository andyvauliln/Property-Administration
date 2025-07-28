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

logger_sms = logging.getLogger('mysite.sms_webhooks')
# twilio_phone = os.environ["TWILIO_PHONE"]
#http://68.183.124.79/conversation-webhook/
account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
# manager_phone = os.environ["MANAGER_PHONE"]
# manager_phone2 = os.environ["MANAGER_PHONE2"]
# twilio_phone_secondary = os.environ["TWILIO_PHONE_SECONDARY"]
# farid_secondary = "+17282001917"
client = Client(account_sid, auth_token)


def print_info(message):
    print(message)
    logger_sms.debug(message)

def get_sender_name_from_phone(phone_number):
    """Map phone numbers to display names"""
    phone_mapping = {
        "+13153524379": "ASSISTANT",
        "+15614603904": "FARID", 
        "+17282001917": "FARID",
        "+12403584373": "TWILIO",
        "+13055400481": "CUSTOMER",
        # Add your manager phones
        os.environ.get("MANAGER_PHONE", ""): "MANAGER1",
        os.environ.get("MANAGER_PHONE2", ""): "MANAGER2",
    }
    return phone_mapping.get(phone_number, "UNKNOWN")

def get_sender_name_from_author_and_participant(author, participant_phone, conversation_sid):
    """Get sender name from author identity or phone number"""
    if author:
        # If author is set (like "ASSISTANT"), use that
        if author in ["ASSISTANT", "GPT BOT", "realEstateAgent"]:
            return "ASSISTANT"
        elif author in ["FARID", "CUSTOMER"]:
            return author
        else:
            return author
    
    # If no author, try to identify by phone number
    if participant_phone:
        return get_sender_name_from_phone(participant_phone)
    
    return "UNKNOWN"

# NEW PRE-EVENT WEBHOOK [Events types: onMessageAdd - PRE-HOOK]
@csrf_exempt
@require_http_methods(["POST", "GET"])
def conversation_pre_webhook(request):
    print_info("**********MESSAGE_PRE_WEBHOOK **********")
    try:
        if request.method == 'POST':
            data = request.POST
            event_type = data.get('EventType')
            conversation_sid = data.get('ConversationSid')
            author = data.get('Author', None)
            body = data.get('Body', None)
            participant_sid = data.get('ParticipantSid', None)
            
            print_info(f"Pre-webhook Data: {data}")
            print_info(f"EventType: {event_type}")
            print_info(f"Author: {author}")
            print_info(f"Body: {body}")
            print_info(f"ConversationSid: {conversation_sid}")
            print_info(f"ParticipantSid: {participant_sid}")
            
            # Handle pre-message event
            if event_type == 'onMessageAdd':
                if body and not body.startswith(('ASSISTANT:', 'FARID:', 'CUSTOMER:', 'MANAGER1:', 'MANAGER2:', 'UNKNOWN:')):
                    # Get participant info to determine sender
                    participant_phone = None
                    if participant_sid and conversation_sid:
                        try:
                            participant = client.conversations.v1.conversations(
                                conversation_sid
                            ).participants(participant_sid).fetch()
                            
                            if hasattr(participant, 'messaging_binding') and participant.messaging_binding:
                                participant_phone = participant.messaging_binding.get('address')
                                print_info(f"Participant phone: {participant_phone}")
                        except Exception as e:
                            print_info(f"Error fetching participant: {e}")
                    
                    # Determine sender name
                    sender_name = get_sender_name_from_author_and_participant(author, participant_phone, conversation_sid)
                    
                    # Add prefix to message
                    modified_body = f"{sender_name}: {body}"
                    print_info(f"Modified message: {modified_body}")
                    
                    # Return the modified message
                    return JsonResponse({
                        'body': modified_body,
                        'status': 'success'
                    })
                
                # If message already has prefix, don't modify
                print_info("Message already has prefix or no body - not modifying")
                return JsonResponse({'status': 'success'})
            
        return JsonResponse({'status': 'success'}, status=200)
    except Exception as e:
        print_info(f"Error in conversation_pre_webhook: {e}")
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

# TWILIO WEBHOOK [Events types: onMessageAdded]
@csrf_exempt
@require_http_methods(["POST", "GET"])
def conversation_webhook(request):
    print_info("**********MESSAGE_ADDED_WEBHOOK **********")
    try:
        if request.method == 'POST':
            data = request.POST
            event_type = data.get('EventType')
            webhook_sid = data.get('WebhookSid', None)
            conversation_sid = data.get('ConversationSid')
            print_info(f"Data: {data}")
            print_info(f"Body: {data.get('Body', None)}")
            print_info(f"WebhookSid: {webhook_sid}")
            print_info(f"ConversationSid: {conversation_sid}")
            print_info(f"EventType: {event_type}")
            twilio_phone_secondary = "+13153524379"
            farid = "+15614603904"
            farid_secondary = "+17282001917"
            twilio_phone_additional = "+12403584373"

            

            # This webhook should handle regular message events, not conversation creation
            # The conversation_created_webhook handles initial setup when conversations are auto-created
            if event_type == 'onMessageAdded' and webhook_sid:
                # Handle regular message processing here if needed
                # For now, just log and return success
                print_info("Regular message event processed")
                pass
            if event_type == 'onMessageAdded' and  not conversation_sid:
                conversation = client.conversations.v1.conversations.create(
                    friendly_name="TEST_GROUP_CONVERSATION"
                )
                conversation_sid = conversation.sid
                print_info(f'Created conversation: {conversation_sid}')
                print_participants(conversation_sid, "After Creating Conversation")
                time.sleep(1)

                # Step 2: Add the Real Estate Agent
                participant = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.create(
                    identity="ASSISTANT",
                    messaging_binding_projected_address=twilio_phone_secondary,
                )
                print_info(f'Added real estate agent: {participant.sid}')
                print_participants(conversation_sid, "After Adding Real Estate Agent")
                time.sleep(1)

                # Step 3: Add the First Homebuyer
                participant = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.create(messaging_binding_address=twilio_phone_additional)
                print_info(f'Added first farid number: {participant.sid}')
                print_participants(conversation_sid, "After Adding Twilio Phone Number")
                time.sleep(1)

                # Step 4: Send a 1:1 Message
                message = client.conversations.v1.conversations(
                    conversation_sid
                ).messages.create(
                    body="TEST:Hi there. What did you think of the listing I sent?",
                    author="ASSISTANT",
                )
                print_info(f'Sent message: {message.sid}')
                time.sleep(1)

                # Step 5: Add the Second Homebuyer
                participant = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.create(messaging_binding_address=farid_secondary)
                print_info(f'Added second farid number: {participant.sid}')
                print_participants(conversation_sid, "After Adding Second Farid Number - Final Group")
                time.sleep(1)
                # Step 5: Add the Second Homebuyer
                participant = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.create(messaging_binding_address=farid)
                print_info(f'Added second farid number: {participant.sid}')
                print_participants(conversation_sid, "After Adding Second Farid Number - Final Group")
                time.sleep(1)

                # Step 6: Send a 1:1 Message
                message = client.conversations.v1.conversations(
                    conversation_sid
                ).messages.create(
                    body="TEST2:Hi there 2. What did you think of the listing I sent?",
                    author="ASSISTANT",
                )
                print_info(f'Sent message: {message.sid}')
                time.sleep(1)
                
            return JsonResponse({'status': 'success'})

        return JsonResponse({'status': 'success'}, status=200)
    except Exception as e:
        print_info(f"Error in conversation_webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

def find_existing_conversation_with_participants(customer_phone, manager_phone):
    """Find if customer and manager already have a conversation together"""
    try:
        conversations = client.conversations.v1.conversations.list(limit=50)
        
        for conv in conversations:
            participants = client.conversations.v1.conversations(conv.sid).participants.list()
            
            has_customer = False
            has_manager = False
            
            for p in participants:
                if p.messaging_binding:
                    address = p.messaging_binding.get('address')
                    projected_address = p.messaging_binding.get('projected_address')
                    
                    if address == customer_phone or projected_address == customer_phone:
                        has_customer = True
                    if address == manager_phone or projected_address == manager_phone:
                        has_manager = True
            
            if has_customer and has_manager:
                return conv.sid
                
        return None
    except Exception as e:
        print_info(f"Error checking existing conversations: {e}")
        return None

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
            twilio_phone_secondary = "+13153524379"
            farid_secondary = "+17282001917"
            
            
            if event_type == 'onMessageAdded' and conversation_sid:
                print_info(f"Event type is onMessageAdded: {event_type}")
                time.sleep(5)
                try:
                     # Add customer phone to conversation attributes to make it unique
                    conversation_attributes = {
                        "customer_phone": author,
                        "created_timestamp": str(int(time.time())),
                        "conversation_type": "customer_support"
                    }
                    
                    client.conversations.v1.conversations(conversation_sid).update(
                        friendly_name="TEST_GROUP_CONVERSATION123",
                        attributes=json.dumps(conversation_attributes)
                    )
                    print_info(f"Updated conversation attributes: {conversation_attributes}")
                except Exception as e:
                     print_info(f"Error updating conversation attributes: {e}")
                
                existing_participants = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.list()
                time.sleep(3)

                print_info(f"Existing participants {len(existing_participants)}: {existing_participants}")
                
               
                
                customer_exists = any(
                    participant.messaging_binding and 
                    participant.messaging_binding.get('address') == author
                    for participant in existing_participants
                )
                if not customer_exists and author:
                    print_info(f"Adding customer: {author}")
                    try:
                        participant = client.conversations.v1.conversations(
                            conversation_sid
                        ).participants.create(messaging_binding_address=author)
                        print_info(f'Added customer: {participant.sid}')
                    except Exception as e:
                        print_info(f"Error adding customer: {e} {author, event_type, conversation_sid, messaging_binding_address, messaging_binding_proxy_address}")
                else:
                    print_info('Customer already exists in the conversation.')
            
                existing_conversation_sid = find_existing_conversation_with_participants(author, farid_secondary)
                if existing_conversation_sid:
                    print_info(f"Existing conversation found: {existing_conversation_sid}")
                    return JsonResponse({'status': 'success'}, status=200)


                farid_secondary_exists = any(
                    participant.messaging_binding and 
                    participant.messaging_binding.get('address') == farid_secondary
                    for participant in existing_participants
                )

                if (not farid_secondary_exists):
                    print_info(f"Adding second farid number: {farid_secondary}")
                    try:
                        participant = client.conversations.v1.conversations(
                            conversation_sid
                        ).participants.create(messaging_binding_address=farid_secondary, identity="MANAGER")
                        print_info(f'Added second farid number: {participant.sid}')
                        print_participants(conversation_sid, "After Adding Second Farid Number - Final Group")
                        time.sleep(1)
                    except Exception as e:
                        print_info(f"Error adding second farid number: {e} {author, event_type, conversation_sid, messaging_binding_address, messaging_binding_proxy_address}")
                else:
                    print_info('Second Farid number already exists in the conversation.')

                exist_assistant = any(
                    participant.identity == "ASSISTANT3"
                    for participant in existing_participants
                )
                if not exist_assistant:
                    assistant_participant = client.conversations.v1.conversations(
                        conversation_sid
                    ).participants.create(
                        identity="ASSISTANT3",
                        messaging_binding_projected_address=twilio_phone_secondary,
                    )
                    print_info(f'Added ASSISTANT: {assistant_participant.sid}')
                    print_participants(conversation_sid, "After Adding Real Estate Agent")
                    
                return JsonResponse({'status': 'success'}, status=200)
            
            if event_type == 'onParticipantAdded':
                print_participants(conversation_sid, "WEBHOOK ON PARTICIPANT ADDED")
                
                return JsonResponse({'status': 'success'}, status=200)
            if event_type == 'onConversationUpdated':
                print_info(f"Event type is onConversationUpdated: {event_type}")
                return JsonResponse({'status': 'success'}, status=200)

           

            if event_type == 'onConversationAdded' and conversation_sid:
                print_info(f"Event type is onConversationAdded: {event_type}")

        return JsonResponse({'status': 'success'}, status=200)
    except Exception as e:
        print_info(f"Error in conversation_created_webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)



# def create_db_message(sender_phone, receiver_phone, message, booking=None, context=None, sender_type='GPT BOT', message_type='NO_NEED_ACTION', message_status="SENDED"):
#     chat = Chat.objects.create(
#         booking=booking,
#         sender_phone=sender_phone,
#         receiver_phone=receiver_phone,
#         message=message,
#         context=context,
#         sender_type=sender_type,
#         message_type=message_type,
#         message_status=message_status,
#     )
#     chat.save()
#     print_info(
#         f"\n Message Saved to DB. Sender: {chat.sender_phone} Receiver: {chat.receiver_phone}. Message Status: {message_status}, Message Type: {message_type} Context: {context}  Sender Type: {sender_type} \n{message}\n")
#     return chat
