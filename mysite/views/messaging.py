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

# @csrf_exempt


@require_http_methods(["POST"])
def forward_message2(request):
    print_info("FORWARD MESSAGE FROM SECOND NUMBER!!!")
    from_phone = request.POST.get('From', None)
    print_info(from_phone)
    print_info(request)
    return HttpResponse("Success", status=200)


@csrf_exempt
@require_http_methods(["POST"])
def forward_message(request):
    global current_customer_phone
    from_phone = request.POST.get('From', None)
    incoming_message = request.POST.get('Body', None)
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]

    print_info("***************SENDING SMS***************\n")
    print_info(f"Message came from {from_phone} \n")
    print_info(f"Current Twilio Phone {twilio_phone} \n")
    print_info(f"Manager Phone {manager_phone} \n")
    print_info(f"Incoming Message {incoming_message} \n")

    if (from_phone == manager_phone):  # message came from Manager
        if is_phone_number(incoming_message):
            print_info(f"Message Contains Phone")
            recipient, message = get_phone_number_from_message(
                incoming_message)
            print_info(
                f"Parsed Phone and message :{recipient} /n Message: {message}")
            booking = getBookingByPhone(recipient)
            db_message = create_db_message(
                twilio_phone, recipient, message, booking)

            return send_sms(message, recipient, db_message)

        elif current_customer_phone == None:
            message = 'Current customer has not been set. Pls provide phone number before message in format: +13525413455 message'
            db_message = create_db_message(
                twilio_phone, manager_phone, message, None, None, "SYSTEM")
            return send_sms(message, manager_phone, db_message)
        else:
            booking = getBookingByPhone(current_customer_phone)
            db_message = create_db_message(
                twilio_phone, current_customer_phone, message, booking)
            return send_sms(incoming_message, current_customer_phone, db_message)

    else:  # message came from a User
        current_customer_phone = from_phone
        booking = getBookingByPhone(from_phone)

        if (booking):
            booking_info_message = f'''
                From: {booking.tenant.full_name}({current_customer_phone})
                Booking: {booking.start_date} - { booking.end_date}. [{booking.apartment.name}].
                \n {incoming_message}
            '''
            db_message = create_db_message(
                twilio_phone, manager_phone, booking_info_message, booking, None, "USER")
            return send_sms(booking_info_message, manager_phone, db_message)
        else:
            message = f'{from_phone} {incoming_message}'
            db_message = create_db_message(
                twilio_phone, manager_phone, message, None, None, "USER")
            return send_sms(message, manager_phone, db_message)


def send_sms(message, recipient, db_message: Chat, count=0):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]

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
        if (count == 0):
            print_info(
                f"Try send message one more time to {recipient} \n {message}")
            return send_sms(manager_phone, context, db_message, 1)
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

# @require_http_methods(["POST"])
# def forward_message(request):
#     global current_customer_phone
#     from_phone = request.POST.get('From', None)
#     incoming_message = request.POST.get('Body', None)
#     manager_phone = os.environ["TWILIO_PHONE"]

#     if (from_phone == manager_phone):  # message came from manager
#         # TODO: Need take only at the beginging
#         if is_phone_number(incoming_message):
#             phone = incoming_message.split(' ')[1]
#             message = incoming_message.split(' ')[0]
#             print_info(phone, message)
#             if message == "/history":
#                 history = getUserMessages(from_phone)
#                 reply(history, manager_phone)
#             if message == "/gpt":
#                 history = applyGptAnswere(from_phone)
#                 reply(history, manager_phone)
#             return reply(message, phone)

#         elif current_customer_phone == None:
#             return reply('Current customer has not been set. Pls provide phone number', manager_phone)
#         else:
#             return reply(incoming_message, current_customer_phone)
#     else:  # message came from a customer
#         current_customer_phone = from_phone
#         booking = Booking.objects.filter(
#             tenant__phone=from_phone).order_by('-created_at').first()

#         if (booking):
#             booking_info_message = f'''
#                 {booking.tenant.full_name}({current_customer_phone})
#                 Booking: {booking.start_date} -{ booking.end_date}. [{booking.apartment.name}].
#                 {incoming_message}
#             '''
#             reply(booking_info_message, manager_phone)
#         else:
#             reply(f'{from_phone} : {incoming_message}', manager_phone)

#         gpt_answer = getGptAnswere(booking, incoming_message)
#         if gpt_answer:
#             reply(f'GPT : {incoming_message}', manager_phone)


# @require_http_methods(["POST"])
# def forward_telegram(request):
#     if request.method == 'POST':
#         # Parse the incoming JSON payload
#         data = json.loads(request.body.decode('utf-8'))

#         # Check if the payload is a message
#         if 'message' in data:
#             chat_id = data['message']['chat']['id']
#             text = data['message']['text']

#             # Process the incoming message and generate a response
#             response_text = process_message(text)

#             # Send the response back to the Telegram API
#             send_telegram_message(chat_id, response_text)

#     return HttpResponse()


# def applyGptAnswere(from_phone):
#     return ""


# def getUserMessages(from_phone):
#     return ""


# def getGptAnswere(booking, message):

#     gpt_system_message = "You are helpfull assistan in real estate menagment. Your goal is asnwere users questions and help them during the stay"
#     gpt_instructions = ""
#     questions_type = define_qustion_type(message)
#     if (questions_type == "db_related"):
#         sendToGpt(getDatabaseContext(), message)
#     elif (questions_type == "knowledge_base"):
#         return sendToAssistant(message)
#     else:
#         return ""


# def sendToAssistant(phone, message):
#     return ""


# def sendToGpt(context, message):
#     return ""


# def getDatabaseContext(booking):
#     booking_info = f'''
#         Tenant Details:

#         Name: {booking.tenant.full_name}.
#         Phone: {booking.tenant.phone}.
#         Email: {booking.tenant.email}.
#         Tenants Amount: {booking.tenants_n}.
#         Other Tenants: {booking.other_tenants}.
#         Notes: {booking.tenant.notes}.

#         Booking Details:
#         Start Date: {booking.start_date} (m/d/y).
#         End Date: {booking.end_date} (m/d/y).
#         Status: {booking.status}.
#         Source: {booking.source}.
#         Visit Purpose: {booking.visit_purpose}.
#         Animals: {booking.animals}.
#         Cleaner Name: {booking.assigned_cleaner.full_name}.
#         Cleaner Phone: {booking.assigned_cleaner.phone}.
#         Payment Details:
#     '''

#     return booking_info


# def define_qustion_type(message):
#     return ""


# def process_message(message):
#     # Implement your logic to process the incoming message
#     # This is where you generate the response text
#     return f'You said: {message}'


# def send_telegram_message(chat_id, text):
#     # Send the response back to the Telegram API
#     telegram_api_url = f'https://api.telegram.org/bot{os.environ["TELEGRAM_TOKEN"]}/sendMessage'
#     data = {
#         'chat_id': chat_id,
#         'text': text,
#     }

#     # Use Django's HttpRequest to send the HTTP POST request to the Telegram API
#     response = requests.post(telegram_api_url, data=data)

#     # Check the response if needed
#     if response.status_code == 200:
#         print('Message sent successfully')
#         print('************MESSAGE**********')
#         print(text)
#         print('**********************')
#     else:
#         print(f'Error sending message. Status code: {response.status_code}')
#         print(response.text)
