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
from mysite.unified_logger import log_error, log_info, log_warning, logger


class Command(BaseCommand):
    help = 'Send SMS notifications for upcoming booking events'

    def handle(self, *args, **options):
        log_info("Starting SMS notification process", category='sms')
        
        self.send_sms_for_event('move_in')
        self.send_sms_for_event('unsigned_contract_1d')
        self.send_sms_for_event('unsigned_contract_3d')
        self.send_sms_for_event('deposit_reminder')
        self.send_sms_for_event('due_payment')
        self.send_sms_for_event('extension')
        self.send_sms_for_event('move_out')
        self.send_sms_for_event('safe_travel')
        
        log_info("SMS notification process completed", category='sms')

    def send_sms_for_event(self, event_type):
        bookings = self.get_bookings_for_event(event_type)

        for booking in bookings:
            if booking.tenant.phone:
                message = self.get_message_for_event(event_type)
                log_info(
                    f"Sending {event_type} SMS to {booking.tenant.phone} for {booking.apartment.name}",
                    category='sms',
                    details={'booking_id': booking.id, 'event': event_type}
                )
                
                # Only send if conversation exists (production)
                self.send_sms(booking, message)

            else:
                log_warning(
                    f"Cannot notify tenant {booking.tenant.full_name} about {event_type} - no phone number",
                    category='sms',
                    details={
                        'tenant': booking.tenant.full_name,
                        'apartment': booking.apartment.name,
                        'booking_id': booking.id,
                        'dates': f"{booking.start_date} - {booking.end_date}"
                    }
                )

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
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        
        if not account_sid or not auth_token:
            log_warning("Twilio credentials not available", category='sms')
            return

        client = Client(account_sid, auth_token)

        try:
            # Check if conversation exists for this booking
            conversation = self.get_existing_conversation(booking)
            
            if conversation:
                # Send via conversation only if one exists
                sent_message = self.send_via_conversation(client, conversation, message, booking)
                log_info(
                    f'SMS sent successfully',
                    category='sms',
                    details={
                        'tenant': booking.tenant.full_name,
                        'phone': booking.tenant.phone,
                        'message_sid': sent_message.sid
                    }
                )
            else:
                # No conversation exists - skip sending
                log_warning(
                    f'No existing conversation - SMS not sent',
                    category='sms',
                    details={
                        'tenant': booking.tenant.full_name,
                        'phone': booking.tenant.phone
                    }
                )
                
        except TwilioException as e:
            log_error(
                e,
                f'SMS Send Failed - {booking.tenant.full_name}',
                source='command',
                severity='high',
                additional_info={
                    'tenant': booking.tenant.full_name,
                    'phone': booking.tenant.phone,
                    'apartment': booking.apartment.name
                }
            )

    def get_existing_conversation(self, booking):
        """
        Get existing conversation for this booking (no creation)
        """
        try:
            # First check if we have a conversation in our database for this booking
            conversation = TwilioConversation.objects.filter(booking=booking).first()
            if conversation:
                return conversation
            
            # Check if there's any conversation for this tenant phone number
            tenant_phone = booking.tenant.phone
            if tenant_phone:
                conversation = TwilioConversation.objects.filter(
                    messages__author=tenant_phone
                ).first()
                
                if conversation:
                    # Link it to this booking if not already linked
                    if not conversation.booking:
                        conversation.booking = booking
                        conversation.apartment = booking.apartment
                        conversation.save()
                    return conversation
            
            return None
            
        except Exception as e:
            log_error(e, f"Get Conversation - Booking {booking.id}", source='command')
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
            
            return sent_message
            
        except Exception as e:
            log_error(e, "Send via Conversation", source='command')
            raise e  # Re-raise the exception

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
            
        except Exception as e:
            log_error(e, "Save Message to DB", source='command')
