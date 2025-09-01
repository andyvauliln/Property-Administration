# yourapp/management/commands/send_sms_notifications.py

from django.core.management.base import BaseCommand
from datetime import timedelta
from django.utils import timezone
from twilio.rest import Client
from mysite.models import Booking
import os
from twilio.base.exceptions import TwilioException
import logging
import requests

logger_sms = logging.getLogger('mysite.sms_nofitications')


def print_info(message):
    print(message)
    logger_sms.debug(message)


class Command(BaseCommand):
    help = 'Send SMS notifications for upcoming booking events'

    def handle(self, *args, **options):
        print_info("**************** START NOTIFICATION PROCESS ************")
        self.send_sms_for_event('unsigned_contract')

    def send_sms_for_event(self, event_type):
        bookings = self.get_bookings_for_event(event_type)

        for booking in bookings:

            message = self.get_message_for_event(event_type, booking)
            print_info(
                "\n**********************************************\n")
            print_info(
                f"\n Found SMS to Send {event_type} for {booking.tenant.phone} {booking.apartment.name}  \n{message}\n")
            send_to_manager(message, booking)
            send_telegram_message(message)


    def get_bookings_for_event(self, event_type):
        now = timezone.now()

        if event_type == 'unsigned_contract':
            # Get bookings for unsigned_contract (12 hours after booking created and booking in status Waiting Contract)
            return Booking.objects.filter(
                status='Waiting Contract',
                created_at__lte=now - timedelta(hours=12),
                created_at__gte=now - timedelta(days=3)
            )
        else:
            return Booking.objects.none()

    def get_message_for_event(self, event_type, booking):

        if event_type == 'unsigned_contract':
            return f'Contract not signed after 12h. Booking: {booking.apartment.name} {booking.start_date} {booking.end_date}. Tenant:{booking.tenant.full_name} {booking.tenant.phone}'
            # 12h after booking created and booking in status Waiting Contract


def send_telegram_message(message):
    chat_ids = os.environ["TELEGRAM_CHAT_ID"].split(",")
    token = os.environ["TELEGRAM_TOKEN"]
    for chat_id in chat_ids:
        base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
        requests.get(base_url)


def send_to_manager(message, booking, message_status="SENDED"):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone_number = os.environ["TWILIO_PHONE_SECONDARY"]
    twilio_manager_number = os.environ["MANAGER_PHONE"]

    client = Client(account_sid, auth_token)
    try:

        message1 = client.messages.create(
            from_=twilio_phone_number,
            to=twilio_manager_number,
            body=message
        )
        print_info(
            f'\nMessage Sent from Twilio: {twilio_phone_number} to Manager: {twilio_manager_number} Status: {message1.status} SID: {message1.sid} \n {message1} \n')

    except TwilioException as e:
        print_info(f'\nError sending SMS notification to manager \n Error: {str(e)} \n ')
        raise RuntimeError(f'\nError sending SMS notification to manager \n Error: {str(e)} \n ')
        
