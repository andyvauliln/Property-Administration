from django.core.management.base import BaseCommand
from mysite.models import TwilioConversation, Booking, User
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Update Twilio conversation links to bookings and apartments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually saving'
        )
        parser.add_argument(
            '--phone',
            type=str,
            help='Update links for specific phone number only'
        )
        parser.add_argument(
            '--conversation-sid',
            type=str,
            help='Update links for specific conversation SID only'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        specific_phone = options['phone']
        specific_conversation = options['conversation_sid']
        
        self.stdout.write(self.style.SUCCESS('Starting conversation link updates...'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        if specific_conversation:
            self.update_specific_conversation(specific_conversation, dry_run)
        elif specific_phone:
            self.update_conversations_for_phone(specific_phone, dry_run)
        else:
            self.update_all_conversations(dry_run)
        
        self.stdout.write(self.style.SUCCESS('Update completed!'))

    def update_specific_conversation(self, conversation_sid, dry_run):
        """Update links for a specific conversation"""
        try:
            conversation = TwilioConversation.objects.get(conversation_sid=conversation_sid)
            
            if conversation.booking:
                self.stdout.write(f'Conversation {conversation_sid} already linked to booking: {conversation.booking}')
                return
                
            updated = self.process_conversation(conversation, dry_run)
            if updated:
                self.stdout.write(self.style.SUCCESS(f'Updated conversation: {conversation_sid}'))
            else:
                self.stdout.write(f'No updates needed for conversation: {conversation_sid}')
                
        except TwilioConversation.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Conversation not found: {conversation_sid}'))

    def update_conversations_for_phone(self, phone_number, dry_run):
        """Update links for conversations involving a specific phone number"""
        self.stdout.write(f'Looking for conversations involving phone: {phone_number}')
        
        # Normalize phone number
        validated_phone = self.validate_phone_number(phone_number)
        if not validated_phone:
            self.stdout.write(self.style.ERROR(f'Invalid phone number: {phone_number}'))
            return
        
        # Find conversations with messages from this phone
        conversations = TwilioConversation.objects.filter(
            messages__author=validated_phone,
            booking__isnull=True
        ).distinct()
        
        self.stdout.write(f'Found {conversations.count()} unlinked conversations for {validated_phone}')
        
        updated_count = 0
        for conversation in conversations:
            if self.process_conversation(conversation, dry_run):
                updated_count += 1
        
        self.stdout.write(f'Updated {updated_count} conversations for phone {validated_phone}')

    def update_all_conversations(self, dry_run):
        """Update links for all conversations (linked and unlinked)"""
        all_conversations = TwilioConversation.objects.all()
        total_count = all_conversations.count()
        
        self.stdout.write(f'Found {total_count} conversations to process (linked and unlinked)')
        
        updated_count = 0
        relinked_count = 0
        skipped_count = 0
        
        for i, conversation in enumerate(all_conversations, 1):
            self.stdout.write(f'Processing {i}/{total_count}: {conversation.conversation_sid}')
            
            current_booking = conversation.booking
            best_booking = self.find_best_booking_for_conversation(conversation)
            
            if not current_booking and best_booking:
                # Case 1: Unlinked conversation, found good booking
                if dry_run:
                    self.stdout.write(f'  Would link to booking: {best_booking}')
                else:
                    conversation.booking = best_booking
                    conversation.apartment = best_booking.apartment
                    conversation.save()
                    self.stdout.write(f'  Linked to booking: {best_booking}')
                updated_count += 1
                
            elif current_booking and best_booking and current_booking != best_booking:
                # Case 2: Already linked, but found better booking
                if self.is_better_booking_match(conversation, best_booking, current_booking):
                    if dry_run:
                        self.stdout.write(f'  Would relink from {current_booking} to {best_booking}')
                    else:
                        conversation.booking = best_booking
                        conversation.apartment = best_booking.apartment
                        conversation.save()
                        self.stdout.write(f'  Relinked from {current_booking} to {best_booking}')
                    relinked_count += 1
                else:
                    self.stdout.write(f'  Kept current booking: {current_booking}')
                    skipped_count += 1
            else:
                skipped_count += 1
        
        self.stdout.write(f'Results: {updated_count} new links, {relinked_count} relinks, {skipped_count} unchanged')

    def process_conversation(self, conversation, dry_run):
        """Process a single conversation to find and link booking"""
        try:
            # Get all unique authors from this conversation (excluding system phones)
            system_phones = ["+13153524379", "+17282001917"]
            system_identities = ["ASSISTANT"]
            
            customer_authors = conversation.messages.exclude(
                author__in=system_phones + system_identities
            ).values_list('author', flat=True).distinct()
            
            if not customer_authors:
                self.stdout.write(f'  No customer messages found in conversation {conversation.conversation_sid}')
                return False
            
            # Try to find booking for each customer author
            for author in customer_authors:
                booking = self.find_booking_for_phone(author)
                if booking:
                    if dry_run:
                        self.stdout.write(f'  Would link to booking: {booking} (apartment: {booking.apartment})')
                        return True
                    else:
                        conversation.booking = booking
                        conversation.apartment = booking.apartment
                        conversation.save()
                        self.stdout.write(f'  Linked to booking: {booking} (apartment: {booking.apartment})')
                        return True
            
            self.stdout.write(f'  No booking found for authors: {list(customer_authors)}')
            return False
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Error processing conversation {conversation.conversation_sid}: {e}'))
            return False

    def find_booking_for_phone(self, phone_number):
        """Find most recent booking for a phone number"""
        try:
            # Validate phone number
            validated_phone = self.validate_phone_number(phone_number)
            if not validated_phone:
                return None
                
            # Find user with this phone number
            user = User.objects.filter(phone=validated_phone, role='Tenant').first()
            if not user:
                return None
                
            # Find most recent booking for this tenant (within last 90 days)
            from datetime import date, timedelta
            cutoff_date = date.today() - timedelta(days=90)
            booking = Booking.objects.filter(
                tenant=user,
                end_date__gte=cutoff_date
            ).order_by('-start_date').first()
            
            return booking
            
        except Exception:
            return None

    def find_best_booking_for_conversation(self, conversation):
        """Find the best booking match for a conversation based on context and timing"""
        try:
            # Get all unique customer authors from this conversation
            system_phones = ["+13153524379", "+17282001917"]
            system_identities = ["ASSISTANT"]
            
            customer_authors = conversation.messages.exclude(
                author__in=system_phones + system_identities
            ).values_list('author', flat=True).distinct()
            
            all_possible_bookings = []
            
            for author in customer_authors:
                # Find all bookings for this customer phone
                bookings = self.find_all_bookings_for_phone(author)
                all_possible_bookings.extend(bookings)
            
            if not all_possible_bookings:
                return None
            
            # Find the best match based on timing and context
            best_booking = None
            best_score = -1
            
            for booking in all_possible_bookings:
                score = self.calculate_booking_match_score(conversation, booking)
                if score > best_score:
                    best_score = score
                    best_booking = booking
            
            return best_booking if best_score > 0 else None
            
        except Exception as e:
            self.stdout.write(f'Error finding best booking: {e}')
            return None

    def find_all_bookings_for_phone(self, phone_number):
        """Find all bookings for a phone number"""
        try:
            validated_phone = self.validate_phone_number(phone_number)
            if not validated_phone:
                return []
                
            user = User.objects.filter(phone=validated_phone, role='Tenant').first()
            if not user:
                return []
                
            # Get all bookings for this tenant (within reasonable timeframe)
            from datetime import date, timedelta
            cutoff_date = date.today() - timedelta(days=180)  # 6 months back
            bookings = list(Booking.objects.filter(
                tenant=user,
                end_date__gte=cutoff_date
            ).order_by('-start_date'))
            
            return bookings
            
        except Exception:
            return []

    def calculate_booking_match_score(self, conversation, booking):
        """Calculate how well a booking matches a conversation (higher = better)"""
        try:
            from datetime import timedelta
            
            # Get conversation timing
            earliest_message = conversation.messages.order_by('message_timestamp').first()
            latest_message = conversation.messages.order_by('-message_timestamp').first()
            
            if not earliest_message:
                return 0
            
            conversation_start = earliest_message.message_timestamp.date()
            conversation_end = latest_message.message_timestamp.date() if latest_message else conversation_start
            
            # Scoring factors
            score = 0
            
            # Factor 1: Timing overlap (most important)
            booking_start_buffer = booking.start_date - timedelta(days=30)
            booking_end_buffer = booking.end_date + timedelta(days=7)
            
            if booking_start_buffer <= conversation_start <= booking_end_buffer:
                score += 100  # Perfect timing match
            elif booking_start_buffer <= conversation_end <= booking_end_buffer:
                score += 80   # Partial timing match
            else:
                # Calculate distance penalty
                days_before = (booking_start_buffer - conversation_end).days if conversation_end < booking_start_buffer else 0
                days_after = (conversation_start - booking_end_buffer).days if conversation_start > booking_end_buffer else 0
                distance = max(days_before, days_after)
                score += max(0, 50 - distance)  # Penalty for distance
            
            # Factor 2: Recency bonus (newer bookings are more likely to be relevant)
            from datetime import date
            booking_age_days = (date.today() - booking.start_date).days
            if booking_age_days <= 30:
                score += 30  # Very recent
            elif booking_age_days <= 90:
                score += 20  # Recent
            elif booking_age_days <= 180:
                score += 10  # Somewhat recent
            
            # Factor 3: Activity recency (recent conversation activity)
            conversation_age_days = (date.today() - conversation_end).days
            if conversation_age_days <= 7:
                score += 20  # Very recent activity
            elif conversation_age_days <= 30:
                score += 10  # Recent activity
            
            return score
            
        except Exception as e:
            self.stdout.write(f'Error calculating booking match score: {e}')
            return 0

    def is_better_booking_match(self, conversation, new_booking, current_booking):
        """Determine if new_booking is a better match than current_booking"""
        try:
            new_score = self.calculate_booking_match_score(conversation, new_booking)
            current_score = self.calculate_booking_match_score(conversation, current_booking)
            
            # Only relink if new booking is significantly better (10+ point difference)
            # This prevents unnecessary relinking for marginal improvements
            return new_score > current_score + 10
            
        except Exception:
            return False

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
