from django.core.management.base import BaseCommand
from mysite.models import TwilioConversation, TwilioMessage
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Check the status of Twilio conversation sync'

    def add_arguments(self, parser):
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Show detailed breakdown by conversation'
        )

    def handle(self, *args, **options):
        detailed = options['detailed']
        
        self.stdout.write(self.style.SUCCESS('=== Twilio Sync Status ==='))
        
        # Basic counts
        total_conversations = TwilioConversation.objects.count()
        total_messages = TwilioMessage.objects.count()
        
        self.stdout.write(f'Total Conversations: {total_conversations}')
        self.stdout.write(f'Total Messages: {total_messages}')
        
        if total_conversations == 0:
            self.stdout.write(self.style.WARNING('No conversations found. Run sync_twilio_history to import data.'))
            return
        
        # Recent activity
        today = timezone.now().date()
        last_7_days = today - timedelta(days=7)
        last_30_days = today - timedelta(days=30)
        
        recent_conversations = TwilioConversation.objects.filter(created_at__date__gte=last_7_days).count()
        recent_messages = TwilioMessage.objects.filter(message_timestamp__date__gte=last_7_days).count()
        
        monthly_conversations = TwilioConversation.objects.filter(created_at__date__gte=last_30_days).count()
        monthly_messages = TwilioMessage.objects.filter(message_timestamp__date__gte=last_30_days).count()
        
        self.stdout.write('\n=== Recent Activity ===')
        self.stdout.write(f'Last 7 days: {recent_conversations} conversations, {recent_messages} messages')
        self.stdout.write(f'Last 30 days: {monthly_conversations} conversations, {monthly_messages} messages')
        
        # Conversations with/without bookings
        with_bookings = TwilioConversation.objects.filter(booking__isnull=False).count()
        with_apartments = TwilioConversation.objects.filter(apartment__isnull=False).count()
        
        self.stdout.write('\n=== Linkage Status ===')
        self.stdout.write(f'Conversations linked to bookings: {with_bookings}/{total_conversations} ({with_bookings/total_conversations*100:.1f}%)')
        self.stdout.write(f'Conversations linked to apartments: {with_apartments}/{total_conversations} ({with_apartments/total_conversations*100:.1f}%)')
        
        # Message direction breakdown
        inbound_messages = TwilioMessage.objects.filter(direction='inbound').count()
        outbound_messages = TwilioMessage.objects.filter(direction='outbound').count()
        
        self.stdout.write('\n=== Message Direction ===')
        self.stdout.write(f'Inbound messages: {inbound_messages} ({inbound_messages/total_messages*100:.1f}%)')
        self.stdout.write(f'Outbound messages: {outbound_messages} ({outbound_messages/total_messages*100:.1f}%)')
        
        if detailed:
            self.stdout.write('\n=== Conversation Details ===')
            conversations = TwilioConversation.objects.all().order_by('-created_at')[:10]
            
            for conv in conversations:
                message_count = conv.messages.count()
                booking_info = f"Booking: {conv.booking}" if conv.booking else "No booking linked"
                apartment_info = f"Apartment: {conv.apartment}" if conv.apartment else "No apartment linked"
                
                self.stdout.write(f'\n{conv.friendly_name} ({conv.conversation_sid})')
                self.stdout.write(f'  Messages: {message_count}')
                self.stdout.write(f'  {booking_info}')
                self.stdout.write(f'  {apartment_info}')
                self.stdout.write(f'  Created: {conv.created_at.strftime("%Y-%m-%d %H:%M")}')
        
        # Latest activity
        latest_message = TwilioMessage.objects.order_by('-message_timestamp').first()
        if latest_message:
            self.stdout.write(f'\n=== Latest Activity ===')
            self.stdout.write(f'Last message: {latest_message.message_timestamp.strftime("%Y-%m-%d %H:%M")}')
            self.stdout.write(f'From: {latest_message.author}')
            self.stdout.write(f'Direction: {latest_message.direction}')
            self.stdout.write(f'Preview: {latest_message.body[:50]}...')
