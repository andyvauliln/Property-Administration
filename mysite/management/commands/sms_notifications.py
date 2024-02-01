# yourapp/management/commands/send_sms_notifications.py

from django.core.management.base import BaseCommand
from datetime import datetime, timedelta
from django.utils import timezone
from twilio.rest import Client
from mysite.models import Booking, Chat
import os
from twilio.base.exceptions import TwilioException
from twilio.twiml.messaging_response import MessagingResponse
from django.db.models import F
from django.db import models
import logging

logger_sms = logging.getLogger('mysite.sms_nofitications')


def print_info(message):
    print(message)
    logger_sms.debug(message)


class Command(BaseCommand):
    help = 'Send SMS notifications for upcoming booking events'

    def handle(self, *args, **options):
        print_info("**************** START NOTIFICATION PROCESS ************")
        self.send_sms_for_event('move_in')
        self.send_sms_for_event('unsigned_contract')
        self.send_sms_for_event('deposit_reminder')
        self.send_sms_for_event('due_payment')
        self.send_sms_for_event('extension')
        self.send_sms_for_event('move_out')
        self.send_sms_for_event('safe_travel')

    def send_sms_for_event(self, event_type):
        bookings = self.get_bookings_for_event(event_type)

        for booking in bookings:
            if booking.tenant.phone:
                message = self.get_message_for_event(event_type)
                # self.send_sms(booking, message) TODO: uncomment after test
                print_info(
                    "\n**********************************************\n")
                print_info(
                    f"\n Found SMS to Send {event_type} for {booking.tenant.phone} {booking.apartment.name}  \n{message}\n")
                send_sms_test_version(booking, message)

            else:
                message = f'Can not nofify tenant {booking.tenant.full_name} about {event_type} event because he doesnt have phone number. Apt: [{booking.apartment.name}]. Booking id: {booking.id},  [{booking.start_date} {booking.end_date}]'
                send_to_manager(message, booking)
                print_info(message)

            print_info("\n**********************************************\n")

    def get_bookings_for_event(self, event_type):
        now = timezone.now()

        if event_type == 'unsigned_contract':
            # Get bookings for unsigned_contract (3 days after booking created and booking start date more then 3 days from creation and booking in status Waiting Contract)
            return Booking.objects.filter(
                status='Waiting Contract',
                created_at__gte=now - timedelta(days=3),
                start_date__lte=now + timedelta(days=3)
            )
        # booking creation date 3 day from now and booking starts меньше 4 дня после сейчас

        elif event_type == 'deposit_reminder':
            # Get bookings for deposit_reminder (4 days after booking created and booking in status Waiting Payment)
            return Booking.objects.filter(
                status='Waiting Payment',
                created_at__gte=now - timedelta(days=4),
                start_date__lte=now + timedelta(days=4)
            )

        elif event_type == 'move_in':
            # Get bookings for move_in (The day before booking start)
            return Booking.objects.filter(
                start_date=now.date() + timedelta(days=1)
            )

        elif event_type == 'due_payment':
            # Get bookings for due_payment (The day before booking payment, payment type is Rent, and payment status Pending)
            return Booking.objects.filter(
                payments__payment_date=now.date() + timedelta(days=1),
                payments__payment_type__name='Rent',
                payments__payment_status='Pending'
            )
        elif event_type == 'extension':
            return Booking.objects.filter(
                models.Q(end_date__gt=F('start_date') + timedelta(days=25), end_date=now.date() + timedelta(weeks=1)) |
                models.Q(end_date__lte=F('start_date') + timedelta(days=25),
                         end_date=now.date() + timedelta(days=1)),
                start_date__lte=now.date()
            )

        elif event_type == 'move_out':
            # Get bookings for move_out (The day before booking end date)
            return Booking.objects.filter(
                end_date=now.date() + timedelta(days=1)
            )

        elif event_type == 'safe_travel':
            # Get bookings for safe_travel (Next day after booking end date)
            return Booking.objects.filter(
                end_date=now.date() - timedelta(days=1)
            )

        else:
            return Booking.objects.none()

    def get_message_for_event(self, event_type):
        """
        Get the SMS message based on the event type.

        Args:
            event_type (str): The type of booking event.

        Returns:
            str: SMS message corresponding to the event type.
        """
        if event_type == 'unsigned_contract':
            return 'Did you get a chance to sign the contract?'
            # 3 days after booking created and booking in status Waiting Contract

        elif event_type == 'deposit_reminder':
            return 'Did you get a chance to send a deposit yet?'
            # 4 days after booking created and booking in status Waiting Payment

        elif event_type == 'move_in':
            return 'Hey! How are you? What time are you planning to be here tomorrow?'
            # The day before booking start

        elif event_type == 'due_payment':
            return 'How are you? Gentle reminder that tomorrow is a due date for the payment. Please, let me know when you send it.'
            # The day before booking payment, payment type is Rent, and payment status Pending

        elif event_type == 'extension':
            return 'How are you? Do you think you might need an extension for your stay?'
            # 1 Week Before Booking End Date

        elif event_type == 'move_out':
            return 'Hey! What time do you think you will be leaving tomorrow? I need to arrange cleaners. My standard check out is 10am.'
            # The day before booking end date

        elif event_type == 'safe_travel':
            return 'Thank you for staying with me. Save my number please if you need something here in the future. Safe travels.'
            # Next day from booking eтв date

        # Add more event types and messages as needed

    def send_sms(self, booking, message):
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        twilio_phone_number = os.environ["TWILIO_PHONE"]

        client = Client(account_sid, auth_token)

        try:
            chat = create_notification(
                twilio_phone_number, booking.tenant.phone, booking, message)

            message = client.messages.create(
                from_=twilio_phone_number,
                to=booking.tenant.phone,
                body=message
            )

            print_info(
                f'SMS sent to {booking.tenant.full_name}({booking.tenant.phone}): {message.sid} \n Message: {message}')
        except TwilioException as e:
            context = f'Error sending SMS notification to {booking.tenant.full_name}({booking.tenant.phone}). Apt: {booking.apartment.name} \n Error: {str(e)}, '
            print_info(context)
            send_to_manager(context, booking)  # TODO remove it later
            chat.message_status = "ERROR"
            chat.context = context
            chat.save()


def send_sms_test_version(booking: Booking, message):

    manager_message = f'Going to send notification to {booking.tenant.full_name}({booking.tenant.phone}) Apt: {booking.apartment.name}, Booking [{booking.start_date} {booking.end_date}]. To process sending, just copy  message bellow and send it with included number. Message:'
    send_to_manager(manager_message, booking)
    message = f'{booking.tenant.phone} {message}'
    send_to_manager(message, booking, "NEED_ANSWERE")


def send_to_manager(message, booking, message_status="SENDED"):
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    twilio_phone_number = os.environ["TWILIO_PHONE"]
    twilio_manager_number = os.environ["TWILIO_MANAGER_PHONE"]

    client = Client(account_sid, auth_token)
    try:

        chat = create_notification(
            twilio_phone_number, twilio_manager_number, booking, message, None, message_status)

        message = client.messages.create(
            from_=twilio_phone_number,
            to=twilio_manager_number,
            body=message
        )
        print_info(
            f'\nMessage Sent from Twilio: {twilio_phone_number} to Menadger: {twilio_manager_number} Status: {message.status} SID: {message.sid} \n {message} \n')

    except TwilioException as e:
        context = f'\nError sending SMS notification to manager \n Error: {str(e)} \n '
        chat.message_status = "ERROR"
        chat.context = context
        chat.save()
        print_info(context)


def create_notification(sender_phone, receiver_phone, booking, message, context=None, message_status="SENDED", sender_type='SYSTEM', message_type='NOTIFICATION'):

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
