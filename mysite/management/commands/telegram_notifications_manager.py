from datetime import timedelta, date
from mysite.models import Payment, User, Booking, Cleaning, format_date
import os
from mysite.management.commands.base_command import BaseCommandWithErrorHandling
from django.db.models import Q
from mysite.unified_logger import log_error, log_info, log_warning, logger
import requests

def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def check_bookings_without_cleaning(chat_id, token):
    today = date.today()
    three_days_from_now = today + timedelta(days=3)

    upcoming_end_bookings = Booking.objects.filter(
        end_date__gte=today,
        end_date__lte=three_days_from_now
    ).exclude(status='Blocked').select_related('apartment', 'tenant')

    for booking in upcoming_end_bookings:
        if not hasattr(booking, 'cleanings') or not booking.cleanings.exists():
            log_info(f"Found Booking: {booking.id} without cleaning")
            if booking.apartment and booking.apartment.managers.exists():
                for apt_manager in booking.apartment.managers.all():
                    if apt_manager.telegram_chat_id == chat_id:
                        message = f"⚠️ WARNING: Booking ending soon without cleaning scheduled!\n"
                        message += f"Booking Details:\n"
                        message += f"- End Date: {booking.end_date}\n"
                        if booking.apartment:
                            message += f"- Apartment: {booking.apartment.name}\n"
                        if booking.tenant:
                            message += f"- Tenant: {booking.tenant.full_name}\n"
                        message += f"Please schedule cleaning ASAP!"
                        send_telegram_message(chat_id.strip(), token, message)
                        break


def _build_booking_message(booking, label):
    start_str = format_date(booking.start_date)
    end_str = format_date(booking.end_date)
    apt_name = booking.apartment.name if booking.apartment else 'Unknown'
    tenant_name = booking.tenant.full_name if booking.tenant else 'Unknown'
    return f"{label}: {start_str} - {end_str}, {apt_name}, {tenant_name}."


def _build_payment_message(payment):
    apt_name = (payment.booking.apartment.name if payment.booking and payment.booking.apartment else
                payment.apartment.name if payment.apartment else "")
    tenant_str = f" ({payment.booking.tenant.full_name})" if payment.booking and payment.booking.tenant else ""
    return f"Payment: {payment.payment_type} {payment.amount}$ {format_date(payment.payment_date)} {apt_name}{tenant_str}[{payment.payment_status}]"


def _build_cleaning_message(cleaning):
    date_str = format_date(cleaning.date)
    cleaner_name = cleaning.cleaner.full_name if cleaning.cleaner else "No cleaner assigned"
    apt_name = (cleaning.booking.apartment.name if cleaning.booking and cleaning.booking.apartment else
                cleaning.apartment.name if cleaning.apartment else "")
    return f"Cleaning: {date_str} by {cleaner_name} {apt_name} [{cleaning.status}]"


def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    active_managers = User.objects.filter(role="Manager", is_active=True)

    for manager in active_managers:
        log_info(f"Manager {manager.full_name}: {manager.telegram_chat_id}")
        if not manager.telegram_chat_id:
            continue

        chat_id = manager.telegram_chat_id
        manager_apartment_ids = list(manager.managed_apartments.values_list('id', flat=True))

        check_bookings_without_cleaning(chat_id, telegram_token)

        if not manager_apartment_ids:
            continue

        for booking in Booking.objects.filter(
            start_date=next_day,
            apartment_id__in=manager_apartment_ids
        ).exclude(status='Blocked').select_related('apartment', 'tenant'):
            message = _build_booking_message(booking, "Start Booking")
            send_telegram_message(chat_id, telegram_token, message)

        for booking in Booking.objects.filter(
            end_date=next_day,
            apartment_id__in=manager_apartment_ids
        ).exclude(status='Blocked').select_related('apartment', 'tenant'):
            message = _build_booking_message(booking, "End Booking")
            send_telegram_message(chat_id, telegram_token, message)

        for payment in Payment.objects.filter(
            payment_date=next_day
        ).exclude(payment_type__name__icontains='Mortage').filter(
            Q(booking__isnull=True) | ~Q(booking__status='Blocked')
        ).filter(
            Q(booking__apartment_id__in=manager_apartment_ids) |
            Q(apartment_id__in=manager_apartment_ids)
        ).exclude(payment_status__in=['Completed', 'Merged']).select_related(
            'payment_type', 'booking', 'booking__apartment', 'booking__tenant', 'apartment'
        ):
            message = _build_payment_message(payment)
            send_telegram_message(chat_id, telegram_token, message)

        for cleaning in Cleaning.objects.filter(
            date=next_day
        ).filter(
            Q(booking__isnull=True) | ~Q(booking__status='Blocked')
        ).filter(
            Q(booking__apartment_id__in=manager_apartment_ids) |
            Q(apartment_id__in=manager_apartment_ids)
        ).select_related('cleaner', 'booking', 'booking__apartment', 'apartment'):
            message = _build_cleaning_message(cleaning)
            send_telegram_message(chat_id, telegram_token, message)

            


class Command(BaseCommandWithErrorHandling):
    help = 'Run telegram notification task for managers'

    def execute_command(self, *args, **options):
        self.stdout.write('Running telegram notification task for managers...')
        my_cron_job()
        self.stdout.write('Telegram notification task for managers completed')
