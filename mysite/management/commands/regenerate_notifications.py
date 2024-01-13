from django.core.management.base import BaseCommand
from mysite.models import Notification, Payment, Cleaning, Booking

# create notifications from existing bookings, cleanings, and payments


class Command(BaseCommand):
    help = 'Recreate Notifications'

    def handle(self, *args, **kwargs):
        # Step 1: Delete all existing notifications
        Notification.objects.all().delete()

        # Step 2: Create notifications for all bookings
        for booking in Booking.objects.all():
            start_notification = Notification(
                date=booking.start_date,
                message='Start Booking',
                booking=booking
            )
            start_notification.save()

            end_notification = Notification(
                date=booking.end_date,
                message='End Booking',
                booking=booking
            )
            end_notification.save()

        # Step 3: Create notifications for all cleanings
        for cleaning in Cleaning.objects.all():
            cleaning_notification = Notification(
                date=cleaning.date,
                message='Cleaning',
                cleaning=cleaning
            )
            cleaning_notification.save()

        # Step 4: Create notifications for all payments
        for payment in Payment.objects.all():
            payment_notification = Notification(
                date=payment.payment_date,
                message='Payment',
                payment=payment
            )
            payment_notification.save()

        self.stdout.write(self.style.SUCCESS('Successfully recreated notifications'))
