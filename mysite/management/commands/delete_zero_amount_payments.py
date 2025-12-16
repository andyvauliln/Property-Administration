from django.core.management.base import BaseCommand
from mysite.models import Payment


class Command(BaseCommand):
    help = "Delete all payments with amount = 0"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Find payments with amount = 0
        payments = Payment.objects.filter(amount=0)

        total_count = payments.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would delete {total_count} payment(s) with 0 amount"
                )
            )
            if total_count > 0:
                self.stdout.write("\nFirst 20 payments that would be deleted:")
                for payment in payments[:20]:
                    apt_name = 'N/A'
                    if payment.booking and payment.booking.apartment:
                        apt_name = payment.booking.apartment.name
                    elif payment.apartment:
                        apt_name = payment.apartment.name
                    self.stdout.write(
                        f"  - Payment ID: {payment.id}, "
                        f"Date: {payment.payment_date}, "
                        f"Status: {payment.payment_status}, "
                        f"Type: {payment.payment_type.name if payment.payment_type else 'N/A'}, "
                        f"Apartment: {apt_name}"
                    )
        else:
            # Delete the payments
            deleted_count, _ = payments.delete()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully deleted {deleted_count} payment(s) with 0 amount"
                )
            )

