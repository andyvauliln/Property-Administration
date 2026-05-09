import requests
from datetime import timedelta, date
from mysite.models import Payment, Booking, Cleaning, format_date
import os
from django.core.management.base import BaseCommand
from django.db.models import Q


def send_telegram_message(chat_id, token, message):
    base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
    requests.get(base_url)


def sent_pending_payments_message(chat_ids, token):
    tomorrow = date.today() + timedelta(days=1)
    month_ago = date.today() - timedelta(days=30)
    pending_payments = Payment.objects.filter(
        payment_status='Pending',
        payment_date__lt=tomorrow,
        payment_date__gte=month_ago
    ).order_by('payment_date')

    if not pending_payments.exists():
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
        for chat_id in chat_ids:
            send_telegram_message(chat_id.strip(), token, message)
        message = ""


def check_bookings_without_cleaning(chat_ids, token):
    today = date.today()
    three_days_from_now = today + timedelta(days=3)

    upcoming_end_bookings = Booking.objects.filter(
        end_date__gte=today,
        end_date__lte=three_days_from_now
    ).exclude(status__in=['Blocked', 'Cancelled']).select_related('apartment', 'tenant')

    for booking in upcoming_end_bookings:
        if not hasattr(booking, 'cleanings') or not booking.cleanings.exists():
            message = f"⚠️ WARNING: Booking ending soon without cleaning scheduled!\n"
            message += f"Booking Details:\n"
            message += f"- End Date: {booking.end_date}\n"
            if booking.apartment:
                message += f"- Apartment: {booking.apartment.name}\n"
            if booking.tenant:
                message += f"- Tenant: {booking.tenant.full_name}\n"
            message += f"Please schedule cleaning ASAP!"

            for chat_id in chat_ids:
                send_telegram_message(chat_id.strip(), token, message)


def _build_booking_message(booking, label):
    start_str = format_date(booking.start_date)
    end_str = format_date(booking.end_date)
    apt_name = booking.apartment.name if booking.apartment else 'Unknown'
    tenant_name = booking.tenant.full_name if booking.tenant else 'Unknown'
    header = f"{label}: {start_str} - {end_str}, {apt_name}, {tenant_name}."
    message = header + f"\nBooking Details:"
    message += f"\n- Start Date: {booking.start_date}"
    message += f"\n- End Date: {booking.end_date}"
    if booking.apartment:
        message += f"\n- Apartment: {booking.apartment.name}"
    return message


def _build_payment_message(payment):
    apt_name = ""
    if payment.booking and payment.booking.apartment:
        apt_name = payment.booking.apartment.name
    elif payment.apartment:
        apt_name = payment.apartment.name
    tenant_str = f" ({payment.booking.tenant.full_name})" if payment.booking and payment.booking.tenant else ""
    header = f"Payment: {payment.payment_type} {payment.amount}$ {format_date(payment.payment_date)} {apt_name}{tenant_str}[{payment.payment_status}]"
    message = header + f"\nPayment Details:"
    message += f"\n- Amount: ${payment.amount}"
    message += f"\n- Status: {payment.payment_status}"
    message += f"\n- Type: {payment.payment_type.name if payment.payment_type else 'N/A'}"
    if payment.notes:
        message += f"\n- Notes: {payment.notes}"
    return message


def _build_cleaning_message(cleaning, prefix):
    date_str = format_date(cleaning.date)
    cleaner_name = cleaning.cleaner.full_name if cleaning.cleaner else "No cleaner assigned"
    apt_name = (cleaning.booking.apartment.name if cleaning.booking and cleaning.booking.apartment else
                cleaning.apartment.name if cleaning.apartment else "")
    header = f"Cleaning: {date_str} by {cleaner_name} {apt_name} [{cleaning.status}]"
    message = f"{prefix}: {header}\nCleaning Details:"
    message += f"\n- Date: {cleaning.date}"
    if cleaning.booking and cleaning.booking.apartment:
        message += f"\n- Apartment: {cleaning.booking.apartment.name}"
    elif cleaning.apartment:
        message += f"\n- Apartment: {cleaning.apartment.name}"
    message += f"\n- Status: {cleaning.status}"
    message += f"\n- Cleaner: {cleaner_name}"
    return message


def my_cron_job():
    next_day = date.today() + timedelta(days=1)
    telegram_chat_ids = os.environ["TELEGRAM_CHAT_ID"].split(",")
    telegram_token = os.environ["TELEGRAM_TOKEN"]

    sent_pending_payments_message(telegram_chat_ids, telegram_token)
    check_bookings_without_cleaning(telegram_chat_ids, telegram_token)

    for chat_id in telegram_chat_ids:
        cid = chat_id.strip()

        for booking in Booking.objects.filter(start_date=next_day).exclude(status__in=['Blocked', 'Cancelled']).select_related('apartment', 'tenant'):
            message = _build_booking_message(booking, "Start Booking")
            send_telegram_message(cid, telegram_token, message)

        for booking in Booking.objects.filter(end_date=next_day).exclude(status__in=['Blocked', 'Cancelled']).select_related('apartment', 'tenant'):
            message = _build_booking_message(booking, "End Booking")
            send_telegram_message(cid, telegram_token, message)

        for payment in Payment.objects.filter(payment_date=next_day).exclude(
            payment_type__name__icontains='Mortage'
        ).filter(Q(booking__isnull=True) | ~Q(booking__status__in=['Blocked', 'Cancelled'])).select_related('payment_type', 'booking', 'booking__apartment', 'booking__tenant', 'apartment'):
            message = _build_payment_message(payment)
            send_telegram_message(cid, telegram_token, message)

        for cleaning in Cleaning.objects.filter(date=next_day).filter(
            Q(booking__isnull=True) | ~Q(booking__status__in=['Blocked', 'Cancelled'])
        ).select_related('cleaner', 'booking', 'booking__apartment', 'apartment'):
            message = _build_cleaning_message(cleaning, "TOMORROW")
            send_telegram_message(cid, telegram_token, message)


class Command(BaseCommand):
    help = 'Run telegram notification task'

    def handle(self, *args, **options):
        self.stdout.write('Running telegram notification task...')
        my_cron_job()
        self.stdout.write(self.style.SUCCESS('Successfully ran telegram notification'))
