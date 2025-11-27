# yourapp/management/commands/send_sms_notifications.py

from django.core.management.base import BaseCommand
from datetime import datetime, timedelta
from django.utils import timezone
from twilio.rest import Client
from mysite.models import Booking, TwilioConversation, TwilioMessage
import os
from twilio.base.exceptions import TwilioException
from twilio.twiml.messaging_response import MessagingResponse
from django.db.models import F
from django.db import models
import logging

logger_sms = logging.getLogger('mysite.sms_notifications')


def print_info(message):
    print(message)
    logger_sms.debug(message)


class Command(BaseCommand):
    help = 'Send SMS notifications for upcoming booking events'

    def handle(self, *args, **options):
        print_info("**************** START NOTIFICATION PROCESS ************")
        
        self.send_sms_for_event('move_in')
        self.send_sms_for_event('unsigned_contract_1d')
        self.send_sms_for_event('unsigned_contract_3d')
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
                print_info(
                    "\n**********************************************\n")
                print_info(
                    f"\n Found SMS to Send {event_type} for {booking.tenant.phone} {booking.apartment.name}  \n{message}\n")
                
                # Only send if conversation exists (production)
                self.send_sms(booking, message)

            else:
                print_info(f'Cannot notify tenant {booking.tenant.full_name} about {event_type} event because they do not have a phone number. Apt: [{booking.apartment.name}]. Booking id: {booking.id}, [{booking.start_date} {booking.end_date}]')

            print_info("\n**********************************************\n")

    def get_bookings_for_event(self, event_type):
        now = timezone.now()

        if event_type == 'unsigned_contract_1d':
            # Get bookings for unsigned_contract_1d (1 day after booking created and booking in status Waiting Contract)
            return Booking.objects.filter(
                status='Waiting Contract',
                created_at__date=now.date() - timedelta(days=1)
            )
        
        elif event_type == 'unsigned_contract_3d':
            # Get bookings for unsigned_contract_3d (3 days after booking created and booking in status Waiting Contract)
            return Booking.objects.filter(
                status='Waiting Contract',
                created_at__date=now.date() - timedelta(days=3)
            )
        # booking creation date 3 day from now and booking starts меньше 4 дня после сейчас

        elif event_type == 'deposit_reminder':
            # Get bookings for deposit_reminder (2 days after booking created and booking in status Waiting Payment)
            return Booking.objects.filter(
                status='Waiting Payment',
                created_at__date=now.date() - timedelta(days=2)
            )

        elif event_type == 'move_in':
            # Get bookings for move_in (The day before booking start)
            return Booking.objects.exclude(
                status__in=['Blocked', 'Pending', 'Problem Booking']
            ).filter(
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
            return Booking.objects.exclude(
                status__in=['Blocked', 'Pending', 'Problem Booking']
            ).filter(
                models.Q(end_date__gt=F('start_date') + timedelta(days=25), end_date=now.date() + timedelta(weeks=1)) |
                models.Q(end_date__lte=F('start_date') + timedelta(days=25),
                         end_date=now.date() + timedelta(days=1)),
                start_date__lte=now.date()
            )

        elif event_type == 'move_out':
            # Get bookings for move_out (The day before booking end date)
            return Booking.objects.exclude(
                status__in=['Blocked', 'Pending', 'Problem Booking']
            ).filter(
                end_date=now.date() + timedelta(days=1)
            )

        elif event_type == 'safe_travel':
            # Get bookings for safe_travel (Next day after booking end date)
            return Booking.objects.exclude(
                status__in=['Blocked', 'Pending', 'Problem Booking']
            ).filter(
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
        if event_type == 'unsigned_contract_1d':
            return 'Hi! Just wanted to check - did you receive the contract link? Please let me know if you need any help with signing it.'
            # 1 day after booking created and booking in status Waiting Contract
        
        elif event_type == 'unsigned_contract_3d':
            return 'Hi, Did you get a chance to sign the contract?'
            # 3 days after booking created and booking in status Waiting Contract

        elif event_type == 'deposit_reminder':
            return 'Hi, Did you get a chance to send a deposit yet?'
            # 2 days after booking created and booking in status Waiting Payment

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
        else:
            return None

    def send_sms(self, booking, message):
        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]

        client = Client(account_sid, auth_token)

        try:
            # Check if conversation exists for this booking
            conversation = self.get_existing_conversation(booking)
            
            if conversation:
                # Send via conversation only if one exists
                sent_message = self.send_via_conversation(client, conversation, message, booking)
                print_info(
                    f'SMS sent to {booking.tenant.full_name}({booking.tenant.phone}): {sent_message.sid} \n Message: {message}')
            else:
                # No conversation exists - skip sending
                print_info(
                    f'No existing conversation found for {booking.tenant.full_name}({booking.tenant.phone}). Skipping SMS notification for: {message}')
                
        except TwilioException as e:
            print_info(f'Error sending SMS notification to {booking.tenant.full_name}({booking.tenant.phone}). Apt: {booking.apartment.name} \n Error: {str(e)}')

    def get_existing_conversation(self, booking):
        """
        Get existing conversation for this booking (no creation)
        """
        try:
            # First check if we have a conversation in our database for this booking
            conversation = TwilioConversation.objects.filter(booking=booking).first()
            if conversation:
                print_info(f"Found existing conversation linked to booking: {conversation.conversation_sid}")
                return conversation
            
            # Check if there's any conversation for this tenant phone number
            tenant_phone = booking.tenant.phone
            if tenant_phone:
                conversation = TwilioConversation.objects.filter(
                    messages__author=tenant_phone
                ).first()
                
                if conversation:
                    print_info(f"Found conversation by phone number: {conversation.conversation_sid}")
                    # Link it to this booking if not already linked
                    if not conversation.booking:
                        conversation.booking = booking
                        conversation.apartment = booking.apartment
                        conversation.save()
                        print_info(f"Linked conversation {conversation.conversation_sid} to booking {booking.id}")
                    return conversation
            
            print_info(f"No existing conversation found for booking {booking.id}")
            return None
            
        except Exception as e:
            print_info(f"Error getting conversation for booking {booking.id}: {e}")
            return None

    def send_via_conversation(self, client, conversation, message, booking):
        """
        Send message via Twilio conversation
        """
        try:
            # Send message via conversation API
            sent_message = client.conversations.v1.conversations(conversation.conversation_sid).messages.create(
                author='ASSISTANT',
                body=message
            )
            
            # Save to our database
            self.save_message_to_db(sent_message, booking, message, 'outbound', conversation.conversation_sid)
            
            print_info(f"Message sent via conversation {conversation.conversation_sid}")
            return sent_message
            
        except Exception as e:
            print_info(f"Error sending via conversation: {e}")
            raise e  # Re-raise the exception instead of falling back

    def save_message_to_db(self, sent_message, booking, message_body, direction, conversation_sid=None):
        """
        Save sent message to our database
        """
        try:
            # Get or create conversation object
            conversation_obj = None
            if conversation_sid:
                conversation_obj, created = TwilioConversation.objects.get_or_create(
                    conversation_sid=conversation_sid,
                    defaults={
                        'friendly_name': f"{booking.tenant.full_name} - {booking.apartment.name}",
                        'booking': booking,
                        'apartment': booking.apartment,
                    }
                )
            
            # Create message record
            message = TwilioMessage(
                message_sid=sent_message.sid,
                conversation=conversation_obj,
                conversation_sid=conversation_sid or '',
                author='ASSISTANT',
                body=message_body,
                direction=direction,
                webhook_sid='',
                messaging_binding_address=booking.tenant.phone,
                messaging_binding_proxy_address=os.environ.get("TWILIO_PHONE_SECONDARY", ''),
            )
            message.save()
            
            print_info(f"Message saved to database: {sent_message.sid}")
            
        except Exception as e:
            print_info(f"Error saving message to database: {e}")
