

import requests
from datetime import timedelta, date
from mysite.models import Notification, Payment, User
import os
from django.core.management.base import BaseCommand


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def get_pending_payments_message(manager_phone):
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
        if payment.booking and payment.booking.apartment and payment.booking.apartment.manager and payment.booking.apartment.manager.phone == manager_phone:

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
        elif payment.apartment and payment.apartment.manager and payment.apartment.manager.phone == manager_phone:
            message += f"\n- Amount: ${payment.amount}"
            message += f"\n  Payment Date: {payment.payment_date}"
            if payment.payment_type:
                message += f"\n  Type: {payment.payment_type.name}"
            if payment.apartment:
                message += f"\n  Apartment: {payment.apartment.name}"
            if payment.notes:
                message += f"\n  Notes: {payment.notes}"
            message += "\n"
    
    return message

def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day, send_in_telegram=True)

    telegram_manager_chat_id = os.environ["TELEGRAM_CHAT_ID_MANAGER"]
    telegram_token = os.environ["TELEGRAM_HANDY_MAN_BOT_TOKEN"]
    manager_phone = os.environ["MANAGER_PHONE3"]

    active_managers = User.objects.filter(role="Manager", is_active=True)

    for manager in active_managers:
        if manager.telegram_chat_id:
            pending_payments_message = get_pending_payments_message(manager.phone)
            if pending_payments_message:    
                send_telegram_message(manager.telegram_chat_id, telegram_token, pending_payments_message)



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
