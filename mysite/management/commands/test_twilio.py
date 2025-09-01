
from mysite.models import Booking
import os
from django.http import HttpResponse, HttpResponseServerError
from twilio.base.exceptions import TwilioException
from twilio.rest import Client
import re
from django.core.management.base import BaseCommand

current_customer_phone = None

# Test: +15618438867 Text readdress
# Wifi
# Wifi: +15618438867
# Manager: +15614603904
# Twilio: +18887028859


class Command(BaseCommand):
    help = 'Run sms notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running sms notification task...')
        forward_message()
        self.stdout.write(self.style.SUCCESS(
            'Successfully ran sms notification'))


def forward_message():
    global current_customer_phone
    from_phone = "+15614603904"
    incoming_message = "+15618438867 Test From APP"
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]

    print("***************SENDING SMS***************\n")
    print(f"Message came from {from_phone} \n")

    if (from_phone == manager_phone):  # message came from Manager
        if is_phone_number(incoming_message):
            print(f"Message Contains Phone")
            recipient, message = get_phone_number_from_message(
                incoming_message)
            booking = getBookingByPhone(recipient)

            return send_sms(message, recipient)

        elif current_customer_phone == None:
            message = 'Current customer has not been set. Pls provide phone number before message in format: +13525413455 message'
            return send_sms(message, manager_phone)
        else:
            booking = getBookingByPhone(current_customer_phone)
            return send_sms(incoming_message, current_customer_phone)

    else:  # message came from a User
        current_customer_phone = from_phone
        booking = getBookingByPhone(from_phone)

        if (booking):
            booking_info_message = f'''
                From: {booking.tenant.full_name}({current_customer_phone})
                Booking: {booking.start_date} - { booking.end_date}. [{booking.apartment.name}].
                \n {incoming_message}
            '''
            return send_sms(booking_info_message, manager_phone)
        else:
            message = f'{from_phone} {incoming_message}'
            return send_sms(message, manager_phone)


def send_sms(message, recipient, count=0):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone = os.environ["TWILIO_PHONE"]
    manager_phone = os.environ["TWILIO_MANAGER_PHONE"]
    print("twilio_phone", twilio_phone)
    print("manager_phone", manager_phone)
    print("recipient", recipient)
    print("message", message)

    client = Client(account_sid, auth_token)

    try:
        twilio_message = client.messages.create(
            from_=twilio_phone,
            to=recipient,
            body=message
        )

        print(
            f'SMS sent from {twilio_phone} to {recipient} \n{message}')
        return HttpResponse("Success", status=200)

    except TwilioException as e:
        context = f'Error sending SMS notification to {recipient}. \n{message} \n Error: {str(e)}, '
        print(context)
        if (count == 0):
            print(
                f"Try send message one more time to {recipient} \n {message}")
            return send_sms(manager_phone, context, 1)
        else:
            print(
                f"SMS can't be sent to {recipient} \n {message} after {count} attempt")
            return HttpResponseServerError(f"Error: {str(e)}")


def getBookingByPhone(phone):
    booking = Booking.objects.filter(
        tenant__phone=phone).order_by('-created_at').first()
    if (booking):
        print(
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

