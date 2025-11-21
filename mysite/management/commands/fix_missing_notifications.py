from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from mysite.models import Booking, Payment, Notification
from datetime import date, timedelta


class Command(BaseCommand):
    help = 'Fix all missing notifications for bookings (Start/End) and payments'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be fixed without actually creating notifications',
        )
        parser.add_argument(
            '--exclude-blocked',
            action='store_true',
            default=True,
            help='Exclude bookings with Blocked status (default: True)',
        )
        parser.add_argument(
            '--exclude-old',
            action='store_true',
            help='Only fix bookings/payments with dates in the future or within last 30 days',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        exclude_blocked = options['exclude_blocked']
        exclude_old = options['exclude_old']

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('FIXING MISSING NOTIFICATIONS'))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made\n'))

        # Statistics
        stats = {
            'bookings_checked': 0,
            'bookings_fixed': 0,
            'start_notifs_created': 0,
            'end_notifs_created': 0,
            'payments_checked': 0,
            'payment_notifs_created': 0,
        }

        # Fix Booking Notifications
        self.stdout.write(self.style.SUCCESS('\n--- FIXING BOOKING NOTIFICATIONS ---\n'))
        
        # Find bookings missing Start or End notifications
        bookings_query = Booking.objects.annotate(
            start_notif_count=Count('booking_notifications', 
                                   filter=Q(booking_notifications__message='Start Booking')),
            end_notif_count=Count('booking_notifications', 
                                 filter=Q(booking_notifications__message='End Booking'))
        ).filter(
            Q(start_notif_count=0) | Q(end_notif_count=0)
        )

        if exclude_blocked:
            bookings_query = bookings_query.exclude(status='Blocked')

        if exclude_old:
            cutoff_date = date.today() - timedelta(days=30)
            bookings_query = bookings_query.filter(
                Q(start_date__gte=cutoff_date) | Q(end_date__gte=cutoff_date)
            )

        bookings = bookings_query.order_by('id')
        stats['bookings_checked'] = bookings.count()

        self.stdout.write(f'Found {stats["bookings_checked"]} bookings with missing notifications\n')

        for booking in bookings:
            fixed_this_booking = False
            
            # Check for missing Start Booking notification
            if booking.start_notif_count == 0:
                self.stdout.write(
                    f'  Booking {booking.id} ({booking.apartment.name if booking.apartment else "N/A"}): '
                    f'Missing START notification for {booking.start_date}'
                )
                
                if not dry_run:
                    Notification.objects.create(
                        date=booking.start_date,
                        message='Start Booking',
                        booking=booking,
                        send_in_telegram=True
                    )
                    self.stdout.write(self.style.SUCCESS('    ✓ Created Start Booking notification'))
                
                stats['start_notifs_created'] += 1
                fixed_this_booking = True

            # Check for missing End Booking notification
            if booking.end_notif_count == 0:
                self.stdout.write(
                    f'  Booking {booking.id} ({booking.apartment.name if booking.apartment else "N/A"}): '
                    f'Missing END notification for {booking.end_date}'
                )
                
                if not dry_run:
                    Notification.objects.create(
                        date=booking.end_date,
                        message='End Booking',
                        booking=booking,
                        send_in_telegram=True
                    )
                    self.stdout.write(self.style.SUCCESS('    ✓ Created End Booking notification'))
                
                stats['end_notifs_created'] += 1
                fixed_this_booking = True

            if fixed_this_booking:
                stats['bookings_fixed'] += 1

        # Fix Payment Notifications
        self.stdout.write(self.style.SUCCESS('\n--- FIXING PAYMENT NOTIFICATIONS ---\n'))
        
        # Find payments without notifications
        payments_query = Payment.objects.annotate(
            notif_count=Count('payment_notifications')
        ).filter(notif_count=0)

        # Exclude mortgage payments (they shouldn't have notifications)
        payments_query = payments_query.exclude(
            payment_type__name__icontains='Mortage'
        ).exclude(
            payment_type__name__icontains='Mortgage'
        )

        if exclude_old:
            cutoff_date = date.today() - timedelta(days=30)
            payments_query = payments_query.filter(payment_date__gte=cutoff_date)

        payments = payments_query.order_by('id')
        stats['payments_checked'] = payments.count()

        self.stdout.write(f'Found {stats["payments_checked"]} payments with missing notifications\n')

        for payment in payments:
            apartment_name = 'N/A'
            if payment.booking and payment.booking.apartment:
                apartment_name = payment.booking.apartment.name
            elif payment.apartment:
                apartment_name = payment.apartment.name

            payment_type = payment.payment_type.name if payment.payment_type else 'Unknown'
            
            self.stdout.write(
                f'  Payment {payment.id} ({apartment_name}): '
                f'Missing notification for {payment_type} on {payment.payment_date}'
            )
            
            if not dry_run:
                Notification.objects.create(
                    date=payment.payment_date,
                    message='Payment',
                    payment=payment,
                    send_in_telegram=True
                )
                self.stdout.write(self.style.SUCCESS('    ✓ Created Payment notification'))
            
            stats['payment_notifs_created'] += 1

        # Print Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('SUMMARY'))
        self.stdout.write(self.style.SUCCESS('='*80))
        self.stdout.write(f'\nBookings:')
        self.stdout.write(f'  Total bookings checked: {stats["bookings_checked"]}')
        self.stdout.write(f'  Bookings fixed: {stats["bookings_fixed"]}')
        self.stdout.write(f'  Start Booking notifications created: {stats["start_notifs_created"]}')
        self.stdout.write(f'  End Booking notifications created: {stats["end_notifs_created"]}')
        
        self.stdout.write(f'\nPayments:')
        self.stdout.write(f'  Total payments checked: {stats["payments_checked"]}')
        self.stdout.write(f'  Payment notifications created: {stats["payment_notifs_created"]}')
        
        self.stdout.write(f'\nTotal notifications created: {stats["start_notifs_created"] + stats["end_notifs_created"] + stats["payment_notifs_created"]}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN - No changes were made. Run without --dry-run to apply fixes.'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✓ All missing notifications have been created!'))
        
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))

