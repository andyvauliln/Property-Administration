from django.core.management.base import BaseCommand
from django.db.models import Q
from mysite.models import Payment


class Command(BaseCommand):
    help = "Set payment_status to 'Merged' for all payments where merged_payment_key has a value"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Find payments with merged_payment_key that is not null and not empty
        payments = Payment.objects.filter(
            ~Q(merged_payment_key__isnull=True) & 
            ~Q(merged_payment_key='')
        ).exclude(payment_status='Merged')

        total_count = payments.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would update {total_count} payment(s) to 'Merged' status"
                )
            )
            if total_count > 0:
                self.stdout.write("\nFirst 10 payments that would be updated:")
                for payment in payments[:10]:
                    self.stdout.write(
                        f"  - Payment ID: {payment.id}, "
                        f"Current Status: {payment.payment_status}, "
                        f"Merged Key: {payment.merged_payment_key}"
                    )
        else:
            # Update the payments
            updated_count = payments.update(payment_status='Merged')
            
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully updated {updated_count} payment(s) to 'Merged' status"
                )
            )


