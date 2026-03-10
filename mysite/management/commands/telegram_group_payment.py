import requests
from datetime import timedelta, date
from django.db.models import Q
from mysite.models import Payment
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling


def normalize_group_chat_id(chat_id):
    s = (chat_id or "").strip()
    if not s or not s.lstrip("-").isdigit():
        return s
    return s


def send_telegram_message(chat_id, token, message, dry_run=False, stdout=None):
    if dry_run and stdout:
        stdout.write(message)
        stdout.write("\n" + "-" * 40 + "\n")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.get(url, params={"chat_id": chat_id, "text": message})


def build_pending_payment_message(payment, direction):
    direction_label = "INCOMING" if direction == "In" else "OUTGOING"
    message = f"🚨 PENDING {direction_label} PAYMENTS FROM PAST PERIODS:"
    message += f"\n- Amount: ${payment.amount}"
    message += f"\n  Payment Date: {payment.payment_date}"
    message += f"\n  Status: {payment.payment_status}"
    if payment.payment_type:
        message += f"\n  Type: {payment.payment_type.name}"
    if getattr(payment, 'booking', None) and payment.booking and payment.booking.apartment:
        message += f"\n  Apartment: {payment.booking.apartment.name}"
        if payment.booking.tenant:
            message += f"\n  Tenant: {payment.booking.tenant.full_name}"
    elif getattr(payment, 'apartment', None) and payment.apartment:
        message += f"\n  Apartment: {payment.apartment.name}"
    if payment.notes:
        message += f"\n  Notes: {payment.notes}"
    return message


def send_pending_payments(chat_id, token, direction, dry_run=False, stdout=None):
    tomorrow = date.today() + timedelta(days=1)
    month_ago = date.today() - timedelta(days=30)
    pending = Payment.objects.filter(
        payment_status='Pending',
        payment_date__lt=tomorrow,
        payment_date__gte=month_ago,
        payment_type__type=direction,
    ).order_by('payment_date').select_related('payment_type', 'booking', 'booking__apartment', 'booking__tenant', 'apartment')
    sent = 0
    for payment in pending:
        send_telegram_message(normalize_group_chat_id(chat_id), token, build_pending_payment_message(payment, direction), dry_run=dry_run, stdout=stdout)
        sent += 1
    return sent


def send_payment_notifications(chat_id, token, direction, next_day, dry_run=False, stdout=None):
    payments = Payment.objects.filter(
        payment_date=next_day,
        payment_type__type=direction,
    ).exclude(
        payment_type__name__icontains='Mortage',
    ).filter(
        Q(booking__isnull=True) | ~Q(booking__status='Blocked'),
    ).select_related('payment_type', 'booking', 'booking__apartment', 'booking__tenant', 'apartment')

    sent = 0
    for payment in payments:
        direction_label = "INCOMING" if direction == "In" else "OUTGOING"
        payment_date_str = payment.payment_date.strftime("%B %d %Y") if payment.payment_date else ""
        apt_name = ""
        if payment.booking and payment.booking.apartment:
            apt_name = payment.booking.apartment.name
        elif payment.apartment:
            apt_name = payment.apartment.name
        tenant_str = f" ({payment.booking.tenant.full_name})" if payment.booking and payment.booking.tenant else ""
        header = f"Payment: {payment.payment_type} ${payment.amount} {payment_date_str} {apt_name}{tenant_str} [{payment.payment_status}]"
        message = f"💰 {direction_label} PAYMENT TOMORROW: {header}\n\nPayment Details:"
        message += f"\n- Amount: ${payment.amount}"
        message += f"\n- Status: {payment.payment_status}"
        message += f"\n- Type: {payment.payment_type.name if payment.payment_type else 'N/A'}"
        if payment.notes:
            message += f"\n- Notes: {payment.notes}"
        send_telegram_message(normalize_group_chat_id(chat_id), token, message, dry_run=dry_run, stdout=stdout)
        sent += 1
    return sent


def my_cron_job(dry_run=False, stdout=None):
    next_day = date.today() + timedelta(days=1)
    chat_id_in = os.environ.get("TELEGRAM_GROUP_PAYMENT_IN")
    chat_id_out = os.environ.get("TELEGRAM_GROUP_PAYMENT_OUT")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        if not dry_run:
            return

    for direction, chat_id, label in [("In", chat_id_in, "Payment In"), ("Out", chat_id_out, "Payment Out")]:
        if not chat_id and not dry_run:
            if stdout:
                stdout.write(f"Skipping {label}: chat_id not set\n")
            continue
        sent = 0
        if chat_id:
            if stdout:
                stdout.write(f"Processing {label} (chat_id: {chat_id})...\n")
            sent += send_pending_payments(chat_id, token, direction, dry_run=dry_run, stdout=stdout)
            sent += send_payment_notifications(chat_id, token, direction, next_day, dry_run=dry_run, stdout=stdout)
        if sent == 0 and chat_id:
            msg = f"No {label.lower()} notifications for tomorrow."
            send_telegram_message(normalize_group_chat_id(chat_id), token, msg, dry_run=dry_run, stdout=stdout)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily payment notifications to Payment Telegram group'

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print messages only, do not send")

    def execute_command(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write("DRY RUN - messages that would be sent to Payment In & Payment Out groups:\n")
        else:
            self.stdout.write('Running telegram group payment...')
        my_cron_job(dry_run=dry_run, stdout=self.stdout)
        self.stdout.write('Telegram group payment completed' if not dry_run else 'Dry run done.')
