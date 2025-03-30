import requests
from datetime import timedelta, date
from mysite.models import Notification, Payment
import os
from django.core.management.base import BaseCommand
from django.db.models import Q


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def get_pending_payments_message():
    tomorrow = date.today() + timedelta(days=1)
    # Get all pending payments that are due in the past
    pending_payments = Payment.objects.filter(
        payment_status='Pending',
        payment_date__lt=tomorrow
    ).order_by('payment_date')
    
    if not pending_payments.exists():
        return ""
        
    message = "\n\n🚨 PENDING PAYMENTS FROM PAST PERIODS:"
    for payment in pending_payments:
        message += f"\n- Amount: ${payment.amount}"
        message += f"\n  Payment Date: {payment.payment_date}"
        if payment.payment_type:
            message += f"\n  Type: {payment.payment_type.name}"
        if hasattr(payment, 'booking') and payment.booking and payment.booking.apartment:
            message += f"\n  Apartment: {payment.booking.apartment.name}"
            if payment.booking.tenant:
                message += f"\n  Tenant: {payment.booking.tenant.full_name}"
        elif hasattr(payment, 'apartment') and payment.apartment:
            message += f"\n  Apartment: {payment.apartment.name}"
        if payment.notes:
            message += f"\n  Notes: {payment.notes}"
        message += "\n"
    
    return message


def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day, send_in_telegram=True)

    telegram_chat_ids = os.environ["TELEGRAM_CHAT_ID"].split(",")
    telegram_token = os.environ["TELEGRAM_TOKEN"]
    
    # Get pending payments message once
    pending_payments_message = get_pending_payments_message()

    for chat_id in telegram_chat_ids:
        send_telegram_message(chat_id.strip(), telegram_token, pending_payments_message)

    for notification in notifications:
        message = f"{notification.notification_message}"
        
        # Add payment information if it exists
        if notification.payment:
            message += f"\nPayment Details:"
            message += f"\n- Amount: ${notification.payment.amount}"
            message += f"\n- Status: {notification.payment.payment_status}"
            message += f"\n- Type: {notification.payment.payment_type.name if notification.payment.payment_type else 'N/A'}"
            if notification.payment.notes:
                message += f"\n- Notes: {notification.payment.notes}"
        
        # Add booking information if it exists
        if notification.booking:
            message += f"\nBooking Details:"
            message += f"\n- Start Date: {notification.booking.start_date}"
            message += f"\n- End Date: {notification.booking.end_date}"
            if hasattr(notification.booking, 'apartment'):
                message += f"\n- Apartment: {notification.booking.apartment.name}"

            for chat_id in telegram_chat_ids:
                send_telegram_message(chat_id.strip(), telegram_token, message)
        
        if notification.cleaning:
            message += f"\nCleaning Details:"
            message += f"\n- Date: {notification.cleaning.date}"
            if hasattr(notification.cleaning, 'booking'):
                message += f"\n- Apartment: {notification.cleaning.booking.apartment.name}"
            if hasattr(notification.cleaning, 'status'):
                message += f"\n- Status: {notification.cleaning.status}"
            if hasattr(notification.cleaning, 'cleaner'):
                message += f"\n- Cleaner: {notification.cleaning.cleaner.name}"

            # Add pending payments message to each notification
            message += pending_payments_message

            for chat_id in telegram_chat_ids:
                send_telegram_message(chat_id.strip(), telegram_token, message)


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
