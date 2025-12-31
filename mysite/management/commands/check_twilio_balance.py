from django.core.management.base import BaseCommand, CommandError

# Reuse the standalone script logic to keep one source of truth.
try:
    from check_twilio_balance import main as run_twilio_balance_check
except Exception as exc:
    raise ImportError(f"Unable to import check_twilio_balance script: {exc}")


class Command(BaseCommand):
    help = "Check Twilio balance and send a Telegram notification"

    def handle(self, *args, **options):
        exit_code = run_twilio_balance_check()

        if exit_code != 0:
            # The script already printed details; raise to signal failure.
            raise CommandError("Twilio balance check failed (see logs above).")

        self.stdout.write(self.style.SUCCESS("Twilio balance check completed."))




