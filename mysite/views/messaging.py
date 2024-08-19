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

logger_sms = logging.getLogger('mysite.sms_webhooks')
twilio_phone = os.environ["TWILIO_PHONE"]
account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
manager_phone = os.environ["TWILIO_MANAGER_PHONE"]
manager_phone2 = os.environ["TWILIO_MANAGER_PHONE2"]
client = Client(account_sid, auth_token)


def print_info(message):
    print(message)
    logger_sms.debug(message)

# event types onConversationAdd onMessageAdd


@csrf_exempt
@require_http_methods(["POST", "GET"])
def conversation_webhook(request):
    print_info("WEBHOOK_EVENT")
    if request.method == 'POST':
        data = request.POST
        event_type = data.get('EventType')
        print_info(f"event_type: {event_type}")
        print_info(f"ConversationSid: {data.get('ConversationSid')}")

        if event_type == 'onConversationAdd':
            conversation_sid = data.get('ConversationSid')

           # Add managers to the conversation by phone number
            # Replace with actual phone numbers
            managers = [manager_phone, manager_phone2]
            for manager in managers:
                client.conversations.conversations(conversation_sid).participants.create(
                    messaging_binding_address=manager
                )

            return JsonResponse({'status': 'success'})

        elif event_type == 'onMessageAdded':
            conversation_sid = data.get('ConversationSid')
            message_body = data.get('Body')
            author = data.get('Author')

            print_info(
                f"onMessageAdded: {conversation_sid} {message_body} {author}")

            return JsonResponse({'status': 'success'})

    return JsonResponse({'status': 'failed'}, status=400)


def create_db_message(sender_phone, receiver_phone, message, booking=None, context=None, sender_type='MANAGER', message_type='NO_NEED_ACTION', message_status="SENDED"):
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
