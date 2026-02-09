import requests
from datetime import timedelta, date
from mysite.models import Notification, Payment
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def send_pending_payments(chat_id, token):
    tomorrow = date.today() + timedelta(days=1)
    month_ago = date.today() - timedelta(days=30)
    pending_payments = Payment.objects.filter(
        payment_status='Pending',
        payment_date__lt=tomorrow,
        payment_date__gte=month_ago,
    ).order_by('payment_date')

    if not pending_payments.exists():
        return

    for payment in pending_payments:
        message = "🚨 PENDING PAYMENTS FROM PAST PERIODS:"
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
        send_telegram_message(chat_id.strip(), token, message)


def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    chat_id = os.environ.get("TELEGRAM_GROUP_PAYMENT")
    token = os.environ.get("TELEGRAM_TOKEN")
    if not chat_id or not token:
        return

    send_pending_payments(chat_id, token)

    notifications = Notification.objects.filter(
        date=next_day,
        send_in_telegram=True,
        payment__isnull=False,
    ).exclude(booking__status='Blocked')

    for notification in notifications:
        message = f"PAYMENT TOMORROW: {notification.notification_message}"
        if notification.payment:
            message += "\nPayment Details:"
            message += f"\n- Amount: ${notification.payment.amount}"
            message += f"\n- Status: {notification.payment.payment_status}"
            message += f"\n- Type: {notification.payment.payment_type.name if notification.payment.payment_type else 'N/A'}"
            if notification.payment.notes:
                message += f"\n- Notes: {notification.payment.notes}"
        send_telegram_message(chat_id.strip(), token, message)


class Command(BaseCommandWithErrorHandling):
    help = 'Send daily payment notifications to Payment Telegram group'

    def execute_command(self, *args, **options):
        self.stdout.write('Running telegram group payment...')
        my_cron_job()
        self.stdout.write('Telegram group payment completed')
