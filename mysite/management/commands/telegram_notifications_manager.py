import logging
import requests
from datetime import timedelta, date
from mysite.models import Notification, Payment, User, Booking
import os
from django.core.management.base import BaseCommand
from django.db.models import Q    

def send_telegram_message(chat_id, token, message):
    print_info(f"Sending telegram message to {chat_id}: {message}")
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)

logger_sms = logging.getLogger('mysite.sms_notifications')


def print_info(message):
    print(message)
    logger_sms.debug(message)

def sent_pending_payments_message(chat_id, token):
    tomorrow = date.today() + timedelta(days=1)
    # Get all pending payments that are due in the past
    month_ago = date.today() - timedelta(days=30)
    pending_payments = Payment.objects.filter(
        payment_status='Pending',
        payment_date__lt=tomorrow,
        payment_date__gte=month_ago
    ).filter(
        ~Q(payment_type__name__icontains='Mortage')
    ).order_by('payment_date')
    
    if not pending_payments.exists():
        print_info("No pending payments found")
        return ""
    print_info(f"FOUND Pending payments: {pending_payments.count()}")    
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

def check_bookings_without_cleaning(chat_id, token):
    today = date.today()
    three_days_from_now = today + timedelta(days=3)
    
    # Get bookings ending in next 3 days
    upcoming_end_bookings = Booking.objects.filter(
        end_date__gte=today,
        end_date__lte=three_days_from_now
    ).select_related('apartment', 'tenant')
    
    for booking in upcoming_end_bookings:
        # Check if cleaning exists for this booking
        if not hasattr(booking, 'cleanings') or not booking.cleanings.exists():
            # Only send to manager responsible for this apartment
            print_info(f"Found Booking: {booking.id} without cleaning")
            if booking.apartment and booking.apartment.manager:
                if booking.apartment.manager.telegram_chat_id == chat_id:
                    message = f"⚠️ WARNING: Booking ending soon without cleaning scheduled!\n"
                    message += f"Booking Details:\n"
                    message += f"- End Date: {booking.end_date}\n"
                    if booking.apartment:
                        message += f"- Apartment: {booking.apartment.name}\n"
                    if booking.tenant:
                        message += f"- Tenant: {booking.tenant.full_name}\n"
                    message += f"Please schedule cleaning ASAP!"
                    
                    send_telegram_message(chat_id.strip(), token, message)

def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    notifications = Notification.objects.filter(date=next_day, send_in_telegram=True)
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    active_managers = User.objects.filter(role="Manager", is_active=True)
    
    for manager in active_managers:
        print_info(f"Manager {manager.full_name}: {manager.telegram_chat_id}")
        if manager.telegram_chat_id:
            # Check for bookings without cleanings for this manager
            check_bookings_without_cleaning(manager.telegram_chat_id, telegram_token)

            for notification in notifications:
                # Skip mortgage payments
                if notification.payment and notification.payment.payment_type and notification.payment.payment_type.name and 'Mortage' in notification.payment.payment_type.name:
                    continue
                    
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
