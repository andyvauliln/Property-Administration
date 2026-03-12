"""
Verify payment sync v2 manual scoring uses amount=25.
Run: python manage.py check_payment_sync_scoring
"""
from django.core.management.base import BaseCommand
from mysite.views.payment_sync_v2 import _manual_score_db_payment
from mysite.models import Payment
from datetime import date, timedelta


class Command(BaseCommand):
    help = "Verify _manual_score_db_payment returns amount score 25"

    def handle(self, *args, **options):
        p = Payment.objects.first()
        if not p:
            self.stdout.write(self.style.WARNING("No payments in DB - cannot test"))
            return
        today = date.today()
        composite = {
            "amount_total": float(p.amount) - 0.5,
            "date_from": today - timedelta(days=2),
            "date_to": today + timedelta(days=2),
            "notes_list": ["test"],
            "apartment_candidates": [],
            "tenant_candidates": [],
            "direction": "In" if p.payment_type and p.payment_type.type == "In" else None,
        }
        scored = _manual_score_db_payment(p, composite, 100, 4)
        if not scored:
            self.stdout.write(self.style.ERROR("Scored None"))
            return
        amt = next((d for d in scored.get("breakdown_details", []) if d.get("field") == "amount"), None)
        if not amt:
            self.stdout.write(self.style.ERROR("No amount in breakdown_details"))
            return
        expected = 25
        if amt["score"] == expected and amt["max"] == expected:
            self.stdout.write(self.style.SUCCESS(f"OK: amount={amt['score']}/{amt['max']} (expected {expected})"))
        else:
            self.stdout.write(
                self.style.ERROR(f"WRONG: amount={amt['score']}/{amt['max']} (expected {expected}/{expected})")
            )
