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
                    identity="realEstateAgent",
                    messaging_binding_projected_address=twilio_phone_secondary,
                )
                print_info(f'Added real estate agent: {participant.sid}')
                print_participants(conversation_sid, "After Adding Real Estate Agent")
                time.sleep(1)

                # Step 3: Add the First Homebuyer
                participant = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.create(messaging_binding_address=farid_secondary)
                print_info(f'Added first farid number: {participant.sid}')
                print_participants(conversation_sid, "After Adding First Farid Number")
                time.sleep(1)

                # Step 4: Send a 1:1 Message
                message = client.conversations.v1.conversations(
                    conversation_sid
                ).messages.create(
                    body="TEST:Hi there. What did you think of the listing I sent?",
                    author="realEstateAgent",
                )
                print_info(f'Sent message: {message.sid}')
                time.sleep(1)

                # Step 5: Add the Second Homebuyer
                participant = client.conversations.v1.conversations(
                    conversation_sid
                ).participants.create(messaging_binding_address=farid)
                print_info(f'Added second farid number: {participant.sid}')
                print_participants(conversation_sid, "After Adding Second Farid Number - Final Group")
                time.sleep(1)
                
            return JsonResponse({'status': 'success'})

        return JsonResponse({'status': 'success'}, status=200)
    except Exception as e:
        print_info(f"Error in conversation_webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

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
            print_info(f"Body: {data.get('Body', None)}")
            print_info(f"WebhookSid: {webhook_sid}")
            print_info(f"ConversationSid: {conversation_sid}")
            print_info(f"EventType: {event_type}")
            
            if not conversation_sid:
                conversation = client.conversations.v1.conversations.create(
                    friendly_name="TEST_GROUP_CONVERSATION2"
                )
                conversation_sid = conversation.sid
                print_info(f'Created conversation: {conversation_sid}')
                time.sleep(1)
            # Only handle onMessageAdded events
            if event_type != 'onMessageAdded':
                print_info(f'Event type is not onMessageAdded: {event_type}')
                return JsonResponse({'status': 'success'})
            
            time.sleep(3)
            twilio_phone_secondary = "+13153524379"
            farid = "+15614603904"
            farid_secondary = "+17282001917"

            # Update conversation name
            conversation = client.conversations.v1.conversations(conversation_sid).update(
                friendly_name="Real Estate Support Chat"
            )
            print_info(f'Updated conversation name: {conversation.friendly_name}')

            # Get current participants to avoid duplicates
            participants = client.conversations.v1.conversations(conversation_sid).participants.list()
            print_participants(conversation_sid, "Initial Participants in Auto-Created Conversation")
            
            existing_addresses = []
            for p in participants:
                if p.messaging_binding and p.messaging_binding.get('address'):
                    existing_addresses.append(p.messaging_binding.get('address'))
            
            print_info(f'Existing participant addresses: {existing_addresses}')

            # Step 1: Add GPT Bot with projected address (if not already added)
            bot_participant = None
            try:
                # Check if bot is already added by looking for identity
                for p in participants:
                    if hasattr(p, 'identity') and p.identity == "ASSISTANT":
                        bot_participant = p
                        break
                
                if not bot_participant:
                    bot_participant = client.conversations.v1.conversations(
                        conversation_sid
                    ).participants.create(
                        identity="ASSISTANT",
                        messaging_binding_projected_address=twilio_phone_secondary,
                    )
                    print_info(f'Added GPT Bot participant: {bot_participant.sid}')
                    print_participants(conversation_sid, "After Adding ASSISTANT Bot")
                    time.sleep(2)
                else:
                    print_info(f'GPT Bot already exists: {bot_participant.sid}')
            except Exception as e:
                print_info(f'Error adding GPT Bot: {e}')

            # Step 2: Add Farid if not already present
            if farid_secondary not in existing_addresses:
                try:
                    farid_participant = client.conversations.v1.conversations(
                        conversation_sid
                    ).participants.create(messaging_binding_address=farid_secondary)
                    print_info(f'Added Farid to conversation: {farid_participant.sid}')
                    print_participants(conversation_sid, "After Adding Farid - Final Setup")
                    time.sleep(2)
                except Exception as e:
                    print_info(f'Error adding Farid: {e}')
            else:
                print_info(f'Farid already in conversation')
                print_participants(conversation_sid, "Farid Already Present - Current Setup")
            
            # Step 3: Send welcome message using GPT_BOT identity (not participant SID)
            try:
                welcome_message = "Welcome to Real Estate Support! How can I help you today?"
                
                message = client.conversations.v1.conversations(conversation_sid).messages.create(
                    body=welcome_message,
                    author="ASSISTANT"  # Use identity, not participant SID
                )
                print_info(f'Welcome message sent from ASSISTANT: {message.sid}')
                
            except Exception as e:
                print_info(f'Error sending welcome message with GPT_BOT: {e}')
                
                # Fallback: send without specific author
                try:
                    message = client.conversations.v1.conversations(conversation_sid).messages.create(
                        body="ERROR TRY WITH A JUST BODY"
                    )
                    print_info(f'Welcome message sent (no specific author): {message.sid}')
                except Exception as e2:
                    print_info(f'Error sending fallback message: {e2}')
            
            return JsonResponse({'status': 'success'})

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
