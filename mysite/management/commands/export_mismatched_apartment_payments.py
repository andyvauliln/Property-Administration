from django.core.management.base import BaseCommand
from django.db.models import Q, F
from django.utils import timezone
from mysite.models import Payment
import json
import os


class Command(BaseCommand):
    help = "Export payments where payment.apartment does not match booking.apartment"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            default="reports/mismatched_apartment_payments.json",
            help="Path to write the JSON report (default: reports/mismatched_apartment_payments.json)",
        )

    def handle(self, *args, **options):
        output_path = options["output"]

        # Find payments where:
        # 1. Payment has a booking (booking is not null)
        # 2. Payment has an apartment (apartment is not null) 
        # 3. Payment's apartment is different from booking's apartment
        queryset = Payment.objects.filter(
            booking__isnull=False,
            apartment__isnull=False,
        ).exclude(
            apartment=F('booking__apartment')
        ).select_related(
            'booking',
            'apartment',
            'booking__apartment',
            'payment_type',
            'payment_method',
            'bank'
        )

        payments_data = []
        for payment in queryset:
            payment_info = {
                "payment_id": payment.id,
                "payment_date": payment.payment_date.isoformat(),
                "amount": str(payment.amount),
                "payment_type": payment.payment_type.name if payment.payment_type else None,
                "payment_status": payment.payment_status,
                "payment_apartment": {
                    "id": payment.apartment.id,
                    "name": payment.apartment.name,
                } if payment.apartment else None,
                "booking_id": payment.booking.id if payment.booking else None,
                "booking_apartment": {
                    "id": payment.booking.apartment.id,
                    "name": payment.booking.apartment.name,
                } if payment.booking and payment.booking.apartment else None,
                "booking_dates": {
                    "start": payment.booking.start_date.isoformat(),
                    "end": payment.booking.end_date.isoformat(),
                } if payment.booking else None,
                "tenant": payment.booking.tenant.full_name if payment.booking and payment.booking.tenant else None,
                "payment_method": payment.payment_method.name if payment.payment_method else None,
                "bank": payment.bank.name if payment.bank else None,
                "notes": payment.notes,
                "created_at": payment.created_at.isoformat(),
                "updated_at": payment.updated_at.isoformat(),
            }
            payments_data.append(payment_info)

        report = {
            "generated_at": timezone.now().isoformat(),
            "description": "Payments where payment.apartment does not match booking.apartment",
            "total_mismatched_payments": len(payments_data),
            "payments": payments_data,
        }

        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote report to {output_path} (found {len(payments_data)} mismatched payments)"
            )
        )

