from ..models import Booking, Chat
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

current_customer_phone = None

logger_sms = logging.getLogger('mysite.sms_webhooks')


def print_info(message):
    print(message)
    logger_sms.debug(message)

@csrf_exempt
@require_http_methods(["POST"])
def forward_message(request):
    global current_customer_phone
    from_phone = request.POST.get('From', None)
    incoming_message = request.POST.get('Body', None)
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]
    manager_phone2 = os.environ["TWILIO_MANAGER_PHONE2"]

    print_info("***************SENDING SMS***************\n")
    print_info(f"Message came from {from_phone} \n")
    print_info(f"Current Twilio Phone {twilio_phone} \n")
    print_info(f"Manager Phone {manager_phone} \n")
    print_info(f"Manager Phone {manager_phone2} \n")
    print_info(f"Incoming Message {incoming_message} \n")

    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])

    if (from_phone == manager_phone or from_phone == manager_phone2):  # message came from Manager
        if is_phone_number(incoming_message):
            print_info(f"Message Contains Phone")
            recipient, message = get_phone_number_from_message(incoming_message)
            print_info(f"Parsed Phone and message :{recipient} /n Message: {message}")
            booking = getBookingByPhone(recipient)
            db_message = create_db_message(twilio_phone, recipient, message, booking)

            return send_sms(message, recipient, db_message)

        elif current_customer_phone is None:
            message = 'Current customer has not been set. Pls provide phone number before message in format: +13525413455 message'
            db_message1 = create_db_message(twilio_phone, manager_phone, message, None, None, "SYSTEM")
            db_message2 = create_db_message(twilio_phone, manager_phone2, message, None, None, "SYSTEM")
            send_sms(message, manager_phone, db_message1)
            return send_sms(message, manager_phone2, db_message2)
        else:
            booking = getBookingByPhone(current_customer_phone)
            db_message = create_db_message(twilio_phone, current_customer_phone, message, booking)
            return send_sms(incoming_message, current_customer_phone, db_message)

    else:  # message came from a User
        current_customer_phone = from_phone
        booking = getBookingByPhone(from_phone)

        if booking:
            conversation = create_conversation(client, "Booking Conversation")
            add_participant(client, conversation.sid, "Chat", manager_phone)
            add_participant(client, conversation.sid, "SMS", manager_phone2)
            add_participant(client, conversation.sid, "SMS", from_phone)

            booking_info_message = f'''
                From: {booking.tenant.full_name}({current_customer_phone})
                Booking: {booking.start_date} - {booking.end_date}. [{booking.apartment.name}].
                \n {incoming_message}
            '''
            db_message1 = create_db_message(twilio_phone, manager_phone, booking_info_message, booking, None, "USER")
            db_message2 = create_db_message(twilio_phone, manager_phone2, booking_info_message, booking, None, "USER")
            send_sms(booking_info_message, manager_phone, db_message1)
            return send_sms(booking_info_message, manager_phone2, db_message2)
        else:
            message = f'{from_phone} {incoming_message}'
            db_message1 = create_db_message(twilio_phone, manager_phone, message, None, None, "USER")
            db_message2 = create_db_message(twilio_phone, manager_phone2, message, None, None, "USER")
            send_sms(message, manager_phone, db_message1)
            return send_sms(message, manager_phone2, db_message2)

def create_conversation(client, friendly_name):
    conversation = client.conversations.v1.conversations.create(friendly_name=friendly_name)
    print_info(f"Created conversation with SID: {conversation.sid}")
    return conversation

def add_participant(client, conversation_sid, participant_type, phone_number):
    if participant_type == "Chat":
        client.conversations.v1.conversations(conversation_sid).participants.create(
            identity=phone_number,
            messaging_binding_projected_address=os.environ["TWILIO_PHONE"]
        )
    elif participant_type == "SMS":
        client.conversations.v1.conversations(conversation_sid).participants.create(
            messaging_binding_address=phone_number
        )
    print_info(f"Added {participant_type} participant with phone number: {phone_number}")


def send_sms(message, recipient, db_message: Chat, count=0):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]
    manager_phone2 = os.environ["TWILIO_MANAGER_PHONE2"]

    client = Client(account_sid, auth_token)

    try:
        twilio_message = client.messages.create(
            from_=twilio_phone,
            to=recipient,
            body=message
        )

        print_info(
            f'SMS sent from {twilio_phone} to {recipient} \n{message}')
        return HttpResponse("Success", status=200)

    except TwilioException as e:
        context = f'Error sending SMS notification to {recipient}. \n{message} \n Error: {str(e)}, '
        print_info(context)
        if count == 0:
            print_info(
                f"Try send message one more time to {recipient} \n {message}")
            send_sms(manager_phone, context, db_message, 1)
            return send_sms(manager_phone2, context, db_message, 1)
        else:
            print_info(
                f"SMS can't be sent to {recipient} \n {message} after {count} attempt")
            db_message.message_status = "ERROR"
            db_message.context = context
            db_message.save()
            return HttpResponseServerError(f"Error: {str(e)}")


def getBookingByPhone(phone):
    booking = Booking.objects.filter(
        tenant__phone=phone).order_by('-created_at').first()
    if (booking):
        print_info(
            f'Found last booking {booking.id} {booking.created_at} for phone {phone} ')
    return booking


def get_phone_number_from_message(incoming_message: str):
    # Define a regular expression pattern to capture the phone number and the rest of the message
    pattern = r'\+(\d+)\s*(.*)'

    # Use re.match to find the match at the beginning of the string
    match = re.match(pattern, incoming_message.strip())

    # Check if there is a match
    if match:
        # Extract the phone number and message
        phone_number = match.group(1)
        # Remove leading and trailing whitespaces
        message = match.group(2).strip()

        return phone_number, message
    else:
        # Return None if there is no match
        return None, incoming_message


def is_phone_number(text):
    # if the text starts with a phone number
    return re.match(r'^\+\d+', text) is not None


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

