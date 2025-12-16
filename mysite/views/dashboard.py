from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User
import logging
from mysite.forms import BookingForm
from django.db.models import Q
import json
from datetime import date, timedelta
from collections import defaultdict
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import generate_weeks, DateEncoder, handle_post_request, get_model_fields


@user_has_role('Admin', "Manager")
def index(request):

    page = request.GET.get('page', 1)
    report_start_date = request.GET.get('report_start_date', date(2022, 1, 1))
    report_end_date = request.GET.get('report_end_date', date(date.today().year, 12, 31))

    if request.method == 'POST':
        handle_post_request(request, Booking, BookingForm)

    try:
        page = int(page)
    except ValueError:
        page = 1

    prev_page = page - 1
    next_page = page + 1
    start_month = (date.today() + relativedelta(months=9 *
                   (page - 1))).replace(day=1)
    months = [start_month + relativedelta(months=i) for i in range(9)]
    end_date = months[-1] + relativedelta(months=1) - timedelta(days=1)

    if request.user.role == 'Manager':
        # Fetch properties managed by the current user
        apartments = Apartment.objects.filter(
            Q(end_date__gte=start_month) | Q(end_date__isnull=True),
            manager=request.user).order_by('name')
        bookings = Booking.objects.filter(
            Q(start_date__lte=end_date, end_date__gte=start_month),
            apartment__manager=request.user)
        cleanings = Cleaning.objects.filter(
            booking__apartment__manager=request.user,
            date__range=(start_month, end_date)).select_related('booking__apartment')
        payments = Payment.objects.filter(
            Q(booking__apartment__manager=request.user) | Q(apartment__manager=request.user),
            payment_date__range=(start_month, end_date)).select_related('booking__apartment')
    else:
        # Fetch all properties
        apartments = Apartment.objects.filter(
            Q(end_date__gte=start_month) | Q(end_date__isnull=True)).order_by('name')
        bookings = Booking.objects.filter(
            Q(start_date__lte=end_date, end_date__gte=start_month))
        cleanings = Cleaning.objects.filter(date__range=(
            start_month, end_date)).select_related('booking__apartment')
        payments = Payment.objects.filter(payment_date__range=(start_month, end_date)
                                          ).select_related('booking__apartment')

    event_data = defaultdict(lambda: defaultdict(list))

    current_date = start_month
    while current_date <= end_date:
        for booking in bookings:
            if booking.apartment and booking.start_date <= current_date <= booking.end_date:
                key = (booking.apartment.id, current_date)
                event_data[key]['booking'].append(booking)

        for cleaning in cleanings:
            if cleaning.booking and cleaning.booking.apartment and cleaning.date == current_date:
                key = (cleaning.booking.apartment.id, current_date)
                event_data[key]['cleaning'].append(cleaning)

        for payment in payments:
            if payment.payment_date == current_date:
                apartment_id = None
                if payment.booking and payment.booking.apartment:
                    apartment_id = payment.booking.apartment.id
                elif payment.apartment:
                    apartment_id = payment.apartment.id

                if apartment_id:  # Only append if an apartment_id exists
                    key = (apartment_id, current_date)
                    event_data[key]['payment'].append(payment)

        # Increment to the next day
        current_date += timedelta(days=1)

    apartments_data = {}

    for apartment in apartments:
        apartment_data = {
            'apartment': apartment,
            'months': defaultdict(list)
        }

        for month in months:
            weeks = generate_weeks(month)
            for week in weeks:
                week_data = []
                for day in week:
                    bookings_for_day = event_data.get(
                        (apartment.id, day), {}).get('booking', [])
                    cleanings_for_day = event_data.get(
                        (apartment.id, day), {}).get('cleaning', [])
                    payments_for_day = event_data.get(
                        (apartment.id, day), {}).get('payment', [])

                    day_data = {
                        'day': day,
                        'booking_ids': [booking.id for booking in bookings_for_day],
                        'tenants': [booking.tenant.full_name if booking.tenant else '' for booking in bookings_for_day],
                        'tenants_ids': [booking.tenant.id if booking.tenant else None for booking in bookings_for_day],
                        'booking_statuses': [booking.status for booking in bookings_for_day],
                        'booking_starts': [booking.start_date for booking in bookings_for_day],
                        'booking_ends': [booking.end_date for booking in bookings_for_day],
                        'cleaning_ids': [cleaning.id for cleaning in cleanings_for_day],
                        'cleaning_statuses': [cleaning.status for cleaning in cleanings_for_day],
                        'payment_ids': [payment.id for payment in payments_for_day],
                        'payment_types': [payment.payment_type for payment in payments_for_day],
                        'payment_amounts': [payment.amount for payment in payments_for_day],
                        'payment_statuses': [payment.payment_status for payment in payments_for_day],
                        'payment_notes': [payment.notes for payment in payments_for_day],
                        'notes': [booking.notes for booking in bookings_for_day],
                    }
                    # Mark days as blocked if the apartment is unavailable or if the date is outside of the apartment's availability range
                    apt_start_date = apartment.start_date.date() if apartment.start_date else None
                    apt_end_date = apartment.end_date.date() if apartment.end_date else None
                    if apartment.status != 'Available' or (apt_start_date and day < apt_start_date) or (apt_end_date and day > apt_end_date):
                        day_data['booking_statuses'] = ['Blocked']
                    week_data.append(day_data)
                apartment_data['months'][month].append(week_data)

        apartments_data[apartment.id] = apartment_data

    # apartments_data_str_keys = stringify_keys(apartments_data)
    # apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    apartments_data = dict(apartments_data)
    for apartment_id, apartment_data in apartments_data.items():
        apartment_data['months'] = dict(apartment_data['months'])

    bookings_list = list(bookings.values())

    # Extract the 'fields' from each item in the list
    bookings_data_list = [{'id': booking['id'], **booking}
                          for booking in bookings_list]

    for item, original_obj in zip(bookings_data_list, bookings):
        if hasattr(original_obj, 'assigned_cleaner'):
            item['assigned_cleaner'] = original_obj.assigned_cleaner.id if original_obj.assigned_cleaner else None
        if hasattr(original_obj, 'tenant'):
            item['tenant_full_name'] = original_obj.tenant.full_name
            item['tenant_email'] = original_obj.tenant.email
            item['tenant_phone'] = original_obj.tenant.phone
        item['links'] = original_obj.links

        if hasattr(original_obj, 'payments'):
            item['payments'] = [
                {
                    'id': payment.id,
                    'amount': payment.amount,
                    'date': payment.payment_date,
                    'status': payment.payment_status,
                    'notes': payment.notes,
                    'payment_type': payment.payment_type.id
                } for payment in original_obj.payments.all()
            ]

    items_json = json.dumps(bookings_data_list, cls=DateEncoder)

    model_fields = get_model_fields(BookingForm(request=request))
    context = {
        'apartments_data': apartments_data,
        'current_date': timezone.now(),
        # 'apartments_data_json': apartments_data_json,
        'months': months,
        'items_json': items_json,
        'model_fields': model_fields,
        'prev_page': prev_page,
        'next_page': next_page,
        'bookings': bookings,
        'title': "Dashboard",
        'endpoint': "",
        'report_start_date': report_start_date.strftime("%B %d %Y"),
        'report_end_date': report_end_date.strftime("%B %d %Y"),
        'users_json': json.dumps(list(User.objects.all().values('id', 'full_name', 'email', 'phone')), cls=DateEncoder),
        # 'today_notifications': today_notifications,
        # 'next_week_notifications': next_week_notifications,
        # 'next_month_notifications': next_month_notifications,
    }

    return render(request, 'index.html', context)
