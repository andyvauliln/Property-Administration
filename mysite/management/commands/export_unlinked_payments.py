from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from mysite.models import Payment
import json
import os


class Command(BaseCommand):
    help = "Export a JSON report with the count of Rent or Hold Deposit payments that have no booking attached."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            default="reports/unlinked_payments.json",
            help="Path to write the JSON report (default: reports/unlinked_payments.json)",
        )

    def handle(self, *args, **options):
        output_path = options["output"]

        queryset = Payment.objects.filter(
            booking__isnull=True,
            payment_type__name__in=["Rent", "Hold Deposit"],
        )

        total_count = queryset.count()

        report = {
            "generated_at": timezone.now().isoformat(),
            "criteria": "payment_type name in [Rent, Hold Deposit] and booking is null",
            "total_unlinked_payments": total_count,
        }

        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        self.stdout.write(self.style.SUCCESS(f"Wrote report to {output_path} (count={total_count})"))



