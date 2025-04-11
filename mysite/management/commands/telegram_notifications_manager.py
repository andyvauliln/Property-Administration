

import requests
from datetime import timedelta, date
from mysite.models import Notification, Payment, User
import os
from django.core.management.base import BaseCommand


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def sent_pending_payments_message(chat_id, token):
    tomorrow = date.today() + timedelta(days=1)
    # Get all pending payments that are due in the past
    month_ago = date.today() - timedelta(days=30)
    pending_payments = Payment.objects.filter(
        payment_status='Pending',
        payment_date__lt=tomorrow,
        payment_date__gte=month_ago
    ).order_by('payment_date')
    
    if not pending_payments.exists():
        print("No pending payments found")
        return ""
        
    message = "\n\n🚨 PENDING PAYMENTS FROM PAST PERIODS:"
    for payment in pending_payments:
        message += f"\n- Amount: ${payment.amount}"
        message += f"\n  Payment Date: {payment.payment_date}"
        message += f"\n  Status: {payment.payment_status}"
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
        
        send_telegram_message(chat_id.strip(), token, message)
        message = ""
    return message

def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day, send_in_telegram=True)
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    active_managers = User.objects.filter(role="Manager", is_active=True)
    
    for manager in active_managers:
        if manager.telegram_chat_id:
            send_telegram_message(manager.telegram_chat_id, telegram_token, "test")

            for notification in notifications:
                if notification.payment and (notification.payment.payment_status == "Completed" or notification.payment.payment_status == "Merged"):
                    continue
                elif notification.booking and notification.booking.apartment and notification.booking.apartment.manager and notification.booking.apartment.manager.phone == manager.phone:
                    message = f"{notification.notification_message}"
                    send_telegram_message(manager.telegram_chat_id, telegram_token, message)
                    continue
                elif notification.payment and notification.payment.booking and notification.payment.booking.apartment and notification.payment.booking.apartment.manager and notification.payment.booking.apartment.manager.phone == manager.phone:
                    message = f"{notification.notification_message}"
                    send_telegram_message(manager.telegram_chat_id, telegram_token, message)
                    continue
                elif notification.payment and notification.payment.apartment and notification.payment.apartment.manager and notification.payment.apartment.manager.phone == manager.phone:
                    message = f"{notification.notification_message}"
                    send_telegram_message(manager.telegram_chat_id, telegram_token, message)
                    continue
                elif notification.cleaning and notification.cleaning.booking and notification.cleaning.booking.apartment and notification.cleaning.booking.apartment.manager and notification.cleaning.booking.apartment.manager.phone == manager.phone:
                    message = f"{notification.notification_message}"
                    send_telegram_message(manager.telegram_chat_id, telegram_token, message)
                    continue

            


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
