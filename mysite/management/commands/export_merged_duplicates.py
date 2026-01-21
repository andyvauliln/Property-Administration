from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from mysite.models import Payment
from collections import defaultdict
import json
import os


class Command(BaseCommand):
    help = "Export JSON of merged payments from last 40h that have duplicates (same date and amount) in all time"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            type=str,
            default="reports/merged_duplicates.json",
            help="Path to write the JSON report (default: reports/merged_duplicates.json)",
        )

    def handle(self, *args, **options):
        output_path = options["output"]
        
        cutoff = timezone.now() - timedelta(hours=40)
        
        # Get merged payments from last 40 hours
        recent_payments = Payment.objects.filter(
            payment_status="Merged",
            updated_at__gte=cutoff
        ).select_related('apartment', 'booking', 'payment_type', 'payment_method')
        
        # Get unique (date, amount) pairs from recent payments
        recent_keys = set()
        for p in recent_payments:
            recent_keys.add((p.payment_date, p.amount))
        
        # Find ALL merged payments with same date and amount
        from django.db.models import Q
        if not recent_keys:
            duplicates = []
        else:
            q_filter = Q()
            for date, amount in recent_keys:
                q_filter |= Q(payment_date=date, amount=amount)
            
            all_matching = Payment.objects.filter(
                q_filter,
                payment_status="Merged"
            ).select_related('apartment', 'booking', 'payment_type', 'payment_method')
            
            # Group by date and amount
            groups = defaultdict(list)
            for payment in all_matching:
                key = (str(payment.payment_date), str(payment.amount))
                groups[key].append({
                    "id": payment.id,
                    "payment_date": str(payment.payment_date),
                    "amount": str(payment.amount),
                    "apartment": payment.apartment.name if payment.apartment else None,
                    "booking_id": payment.booking_id,
                    "payment_type": payment.payment_type.name if payment.payment_type else None,
                    "payment_method": payment.payment_method.name if payment.payment_method else None,
                    "notes": payment.notes,
                    "merged_payment_key": payment.merged_payment_key,
                    "updated_at": payment.updated_at.isoformat(),
                    "last_updated_by": payment.last_updated_by,
                })
            
            # Filter only groups with duplicates (2+ payments same date and amount)
            duplicates = []
            for (date, amount), payments in groups.items():
                if len(payments) >= 2:
                    duplicates.append({
                        "payment_date": date,
                        "amount": amount,
                        "count": len(payments),
                        "payments": payments
                    })
        
        report = {
            "generated_at": timezone.now().isoformat(),
            "period": f"Last 40 hours (since {cutoff.isoformat()})",
            "criteria": "Merged payments from last 40h with duplicates (same date and amount) in all time",
            "total_duplicate_groups": len(duplicates),
            "total_duplicate_payments": sum(d["count"] for d in duplicates),
            "duplicates": duplicates
        }
        
        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        
        self.stdout.write(self.style.SUCCESS(
            f"Wrote report to {output_path} (groups={len(duplicates)}, total={report['total_duplicate_payments']})"
        ))
