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
account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
manager_phone = os.environ["MANAGER_PHONE"]
manager_phone2 = os.environ["MANAGER_PHONE2"]
twilio_phone_secondary = os.environ["TWILIO_PHONE_SECONDARY"]
client = Client(account_sid, auth_token)


def print_info(message):
    print(message)
    logger_sms.debug(message)

print_info(f"MANAGER_PHONE: {manager_phone}")
print_info(f"MANAGER_PHONE2: {manager_phone2}")
print_info(f"TWILIO_PHONE_SECONDARY: {twilio_phone_secondary}")

# TWILIO WEBHOOK [Events types: onMessageAdded]
@csrf_exempt
@require_http_methods(["POST", "GET"])
def conversation_webhook(request):
    print_info("WEBHOOK_EVENT")
    try:
        if request.method == 'POST':
            data = request.POST
            event_type = data.get('EventType')
            webhook_sid = data.get('WebhookSid', None)
            conversation_sid = data.get('ConversationSid')
            print_info(f"Data: {data}")
            print_info(f"ConversationSid: {conversation_sid}")
            print_info(f"EventType: {event_type}")

            if event_type == 'onMessageAdded' and webhook_sid:
                is_all_participants_added = is_participants_added(conversation_sid)
                if not is_all_participants_added:
                    add_participants(conversation_sid)
                return JsonResponse({'status': 'success'})

        return JsonResponse({'status': 'success'}, status=200)
    except Exception as e:
        print_info(f"Error in conversation_webhook: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

#http://68.183.124.79/conversation-webhook/
def is_participants_added(conversation_sid):
    # Assuming you have a way to interact with Twilio's API to get the number of participants
    try:
        print_info(f"Checking participants for conversation: {conversation_sid}")
        participants = client.conversations.conversations(conversation_sid).participants.list()
        print_info(f"Participants: {participants} Length: {len(participants)}, More than 1: {len(participants) >= 1}")
        return len(participants) > 1
    except TwilioException as e:
        print_info(f"Error checking participants: {e}")
        return False

def add_participants(conversation_sid):
    try:
        # Add projected address on Twilio Secondary number with a name GPT Bot
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(
            identity="GPT BOT",
            messaging_binding_projected_address=twilio_phone_secondary,
        )
        print_info(f"GPT Bot added: {participant.sid}")
        time.sleep(2)
        # Add manager 1
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(messaging_binding_address=manager_phone)
        time.sleep(2)
        print_info(f"Manager 1 added: {participant.sid}")
        # Add manager 2
        participant = client.conversations.v1.conversations(
            conversation_sid
        ).participants.create(messaging_binding_address=manager_phone2)
        print_info(f"Manager 2 added: {participant.sid}")
        time.sleep(2)
        # Send welcome message
        send_messsage(conversation_sid,  "GPT BOT", "Hi Welcome to the chat, you can ask here any questions about the property and our managers will help you with everything!", twilio_phone_secondary, "+1244214141444")
    except TwilioException as e:
        print_info(f"Error adding participants: {e}")

def send_messsage(conversation_sid, author, message, sender_phone, receiver_phone):
    try:
        # Send message to the group chat in Twilio
        # chat = create_db_message(sender_phone, receiver_phone, message, sender_type='GPT Bot', message_type='WELCOME')
        message = client.conversations.v1.conversations(
            conversation_sid
        ).messages.create(
            body=message,
            author=author,
        )
        print_info(f"\nMessage sent: {message.sid}, \n Message: {message.body}")

    except TwilioException as e:
        print_info(f"Error sending welcome message: {e}")
        # chat.message_status = "FAILED"
        # chat.save()


def create_db_message(sender_phone, receiver_phone, message, booking=None, context=None, sender_type='GPT BOT', message_type='NO_NEED_ACTION', message_status="SENDED"):
    chat = Chat.objects.create(
        booking=booking,
        sender_phone=sender_phone,
        receiver_phone=receiver_phone,
        message=message,
        context=context,
        sender_type=sender_type,
        message_type=message_type,
        message_status=message_status,
    )
    chat.save()
    print_info(
        f"\n Message Saved to DB. Sender: {chat.sender_phone} Receiver: {chat.receiver_phone}. Message Status: {message_status}, Message Type: {message_type} Context: {context}  Sender Type: {sender_type} \n{message}\n")
    return chat
