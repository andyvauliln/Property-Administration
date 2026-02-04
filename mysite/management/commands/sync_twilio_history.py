from django.core.management.base import BaseCommand
from django.utils import timezone
from twilio.rest import Client
import os
import logging
from mysite.models import TwilioConversation, TwilioMessage, Booking, User
from datetime import datetime, timedelta
import time

# Configure logging
logger = logging.getLogger('mysite.sync_twilio')

class Command(BaseCommand):
    help = 'Sync all Twilio conversations and messages with the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Number of days back to sync (default: 90 days)'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of conversations to process'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without actually saving to database'
        )
        parser.add_argument(
            '--conversation-sid',
            type=str,
            help='Sync specific conversation by SID'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.days_back = options['days']
        self.limit = options['limit']
        self.specific_conversation = options['conversation_sid']
        
        self.stdout.write(self.style.SUCCESS('Starting Twilio sync...'))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        # Initialize Twilio client
        try:
            account_sid = os.environ["TWILIO_ACCOUNT_SID"]
            auth_token = os.environ["TWILIO_AUTH_TOKEN"]
            self.client = Client(account_sid, auth_token)
            self.stdout.write(f'Connected to Twilio account: {account_sid[:8]}...')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to connect to Twilio: {e}'))
            return
        
        # Phone numbers for direction detection
        self.twilio_phone = "+13153524379"
        self.manager_phone = "+15612205252"
        self.manager_phone_2 = "+15614603904"
        
        # Sync conversations and messages
        try:
            if self.specific_conversation:
                self.sync_specific_conversation(self.specific_conversation)
            else:
                self.sync_all_conversations()
                
            self.stdout.write(self.style.SUCCESS('Sync completed successfully!'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Sync failed: {e}'))
            raise

    def sync_specific_conversation(self, conversation_sid):
        """Sync a specific conversation by SID"""
        self.stdout.write(f'Syncing specific conversation: {conversation_sid}')
        
        try:
            conversation = self.client.conversations.v1.conversations(conversation_sid).fetch()
            self.process_conversation(conversation)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to fetch conversation {conversation_sid}: {e}'))

    def sync_all_conversations(self):
        """Sync all conversations from Twilio"""
        self.stdout.write(f'Fetching conversations...')
        
        # Calculate date filter for filtering after fetching
        cutoff_date = timezone.now() - timedelta(days=self.days_back)
        
        try:
            # Fetch conversations (no date filter in API call)
            all_conversations = self.client.conversations.v1.conversations.list(
                limit=None  # Get all conversations
            )
            
            # Filter by date and limit locally
            conversations = []
            for conv in all_conversations:
                if conv.date_created and conv.date_created >= cutoff_date:
                    conversations.append(conv)
                    if self.limit and len(conversations) >= self.limit:
                        break
            
            total_conversations = len(conversations)
            self.stdout.write(f'Found {total_conversations} conversations to process')
            
            conversations_synced = 0
            conversations_skipped = 0
            
            for i, conversation in enumerate(conversations, 1):
                try:
                    self.stdout.write(f'Processing conversation {i}/{total_conversations}: {conversation.sid}')
                    
                    if self.process_conversation(conversation):
                        conversations_synced += 1
                    else:
                        conversations_skipped += 1
                        
                    # Add small delay to avoid rate limiting
                    time.sleep(0.1)
                    
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Error processing conversation {conversation.sid}: {e}'))
                    conversations_skipped += 1
                    continue
            
            self.stdout.write(self.style.SUCCESS(
                f'Conversations processed: {conversations_synced} synced, {conversations_skipped} skipped'
            ))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Failed to fetch conversations: {e}'))
            raise

    def process_conversation(self, twilio_conversation):
        """Process a single conversation and its messages"""
        conversation_sid = twilio_conversation.sid
        friendly_name = twilio_conversation.friendly_name or f"Conversation {conversation_sid}"
        
        # Check if conversation already exists
        existing_conversation = TwilioConversation.objects.filter(
            conversation_sid=conversation_sid
        ).first()
        
        if existing_conversation:
            if self.dry_run:
                # IMPORTANT: In dry-run we must not claim we'd create an existing conversation.
                self.stdout.write(f'  Conversation already exists: {conversation_sid}')
                self.preview_conversation_messages(conversation_sid)
                return True

            self.stdout.write(f'  Conversation already exists: {conversation_sid}')
            db_conversation = existing_conversation
        else:
            # Try to find related booking/apartment
            booking, apartment = self.find_related_booking_apartment(twilio_conversation)
            
            if self.dry_run:
                self.stdout.write(f'  Would create conversation: {friendly_name}')
                if booking:
                    self.stdout.write(f'    Would link to booking: {booking}')
                if apartment:
                    self.stdout.write(f'    Would link to apartment: {apartment}')
            else:
                # Create new conversation
                db_conversation = TwilioConversation(
                    conversation_sid=conversation_sid,
                    friendly_name=friendly_name,
                    booking=booking,
                    apartment=apartment
                )
                db_conversation.save()
                self.stdout.write(f'  Created conversation: {friendly_name}')
        
        # Sync messages for this conversation
        if not self.dry_run:
            self.sync_conversation_messages(conversation_sid, db_conversation)
        else:
            self.preview_conversation_messages(conversation_sid)
        
        return True

    def find_related_booking_apartment(self, twilio_conversation):
        """Try to find related booking and apartment from conversation participants"""
        booking = None
        apartment = None
        
        try:
            # Get participants
            participants = self.client.conversations.v1.conversations(
                twilio_conversation.sid
            ).participants.list()
            
            # Look for customer phone numbers (not system phones)
            for participant in participants:
                if hasattr(participant, 'messaging_binding') and participant.messaging_binding:
                    address = participant.messaging_binding.get('address', '')
                    
                    # Skip system phones
                    if address in [self.twilio_phone, self.manager_phone, self.manager_phone_2]:
                        continue
                    
                    # Try to find booking from this phone number
                    booking = self.get_booking_from_phone(address)
                    if booking:
                        apartment = booking.apartment
                        break
                        
        except Exception as e:
            self.stdout.write(f'    Warning: Could not fetch participants: {e}')
        
        return booking, apartment

    def get_booking_from_phone(self, phone_number):
        """Find booking from phone number"""
        try:
            # Validate and format phone number
            validated_phone = self.validate_phone_number(phone_number)
            if not validated_phone:
                return None
                
            # Find user with this phone number
            user = User.objects.filter(phone=validated_phone, role='Tenant').first()
            if not user:
                return None
                
            # Find most recent booking for this tenant
            from datetime import date, timedelta
            cutoff_date = date.today() - timedelta(days=90)
            booking = Booking.objects.filter(
                tenant=user,
                end_date__gte=cutoff_date
            ).order_by('-start_date').first()
            
            return booking
            
        except Exception:
            return None

    def validate_phone_number(self, phone):
        """Validate and format phone number"""
        if not phone:
            return None
       
        import re
        
        # Remove any whitespace
        phone = str(phone).strip()
       
        # If it's already in E.164 format, validate it
        if phone.startswith('+'):
            # Must be + followed by 1-15 digits
            if re.match(r'^\+[1-9]\d{1,14}$', phone):
                return phone
            else:
                return None
       
        # If it's a US number without +1, add it
        # Remove any non-digit characters first
        digits_only = re.sub(r'\D', '', phone)
       
        # If it's 10 digits, assume US number
        if len(digits_only) == 10:
            return f"+1{digits_only}"
       
        # If it's 11 digits and starts with 1, assume US number
        if len(digits_only) == 11 and digits_only.startswith('1'):
            return f"+{digits_only}"
       
        # Otherwise, return None as invalid
        return None

    def sync_conversation_messages(self, conversation_sid, db_conversation):
        """Sync all messages for a conversation"""
        try:
            # Fetch messages (stream to avoid dropping messages due to default page size)
            messages = self.client.conversations.v1.conversations(
                conversation_sid
            ).messages.stream(page_size=200)
            
            messages_synced = 0
            messages_skipped = 0
            
            for message in messages:
                if self.process_message(message, db_conversation):
                    messages_synced += 1
                else:
                    messages_skipped += 1
            
            self.stdout.write(f'    Messages: {messages_synced} synced, {messages_skipped} skipped')
            
        except Exception as e:
            self.stdout.write(f'    Error fetching messages: {e}')

    def preview_conversation_messages(self, conversation_sid):
        """Preview messages that would be synced (dry run)"""
        try:
            # Avoid misleading counts: list() returns only the first page by default.
            self.stdout.write('    Would sync messages (streamed, all pages)')
            
        except Exception as e:
            self.stdout.write(f'    Error previewing messages: {e}')

    def process_message(self, twilio_message, db_conversation):
        """Process a single message"""
        message_sid = twilio_message.sid
        
        # Check if message already exists
        if TwilioMessage.objects.filter(message_sid=message_sid).exists():
            return False  # Skip existing message
        
        # Determine direction
        author = twilio_message.author
        direction = 'inbound' if author not in [self.twilio_phone, 'ASSISTANT', self.manager_phone, self.manager_phone_2] else 'outbound'
        
        # Create message
        message = TwilioMessage(
            message_sid=message_sid,
            conversation=db_conversation,
            conversation_sid=db_conversation.conversation_sid,
            author=author,
            body=twilio_message.body or '',
            direction=direction,
            message_timestamp=twilio_message.date_created or timezone.now()
        )
        message.save()
        
        return True  # Message was created
