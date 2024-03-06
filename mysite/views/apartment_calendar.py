from django.shortcuts import render
from django.contrib.auth.views import LogoutView
from ..models import Apartment, Booking, Cleaning, Payment
from mysite.forms import BookingForm
from django.db.models import Q
import json
from datetime import date, timedelta
from collections import defaultdict
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from django.http import HttpResponseBadRequest
from ..models import Apartment, Booking, Cleaning, Payment
from ..decorators import user_has_role
from .utils import generate_weeks, DateEncoder, handle_post_request, stringify_keys, aggregate_data, get_model_fields


@user_has_role('Admin')
def apartment(request):
    apartment_id = request.GET.get('apartment.id', 22)
    year = request.GET.get('year')
    apartments = Apartment.objects.all().order_by('name').values_list('id', 'name')

    if request.method == 'POST':
        handle_post_request(request, Booking, BookingForm)

    try:
        apartment_id = int(apartment_id)
    except ValueError:
        return HttpResponseBadRequest("Invalid request. Apartment ID must be an integer.")

    today = date.today()

    if year:
        try:
            year = int(year)
        except ValueError:
            return HttpResponseBadRequest("Invalid request. Year must be an integer.")
        start_date = date(year, 1, 1)
    else:
        year = today.year
        start_date = date(year, today.month, 1)

    prev_year = year - 1
    next_year = year + 1

    end_date = start_date.replace(year=start_date.year + 1) - timedelta(days=1)

    # Fetch data for the specified apartment
    apartment = Apartment.objects.get(id=apartment_id)
    bookings = Booking.objects.filter(
        start_date__lte=end_date, end_date__gte=start_date, apartment=apartment)

    cleanings = Cleaning.objects.filter(date__range=(
        start_date, end_date), booking__apartment=apartment)
    payments = Payment.objects.filter(
        Q(booking__apartment=apartment) | Q(apartment=apartment),
        payment_date__range=(start_date, end_date)
    )

    event_data = defaultdict(lambda: defaultdict(list))

    current_date = start_date
    while current_date <= end_date:
        for booking in bookings:
            if booking.start_date <= current_date <= booking.end_date:
                key = (booking.apartment.id, current_date)
                event_data[key]['booking'].append(booking)

        for cleaning in cleanings:
            if cleaning.booking and cleaning.date == current_date:
                key = (cleaning.booking.apartment.id, current_date)
                event_data[key]['cleaning'].append(cleaning)

        for payment in payments:
            if payment.payment_date == current_date:
                apartment_id = None
                if payment.booking:
                    apartment_id = payment.booking.apartment.id
                elif payment.apartment:
                    apartment_id = payment.apartment.id

                if apartment_id:  # Only append if an apartment_id exists
                    key = (apartment_id, current_date)
                    event_data[key]['payment'].append(payment)

        # Increment to the next day
        current_date += timedelta(days=1)

    apartments_data = {}
    apartment_data = {
        'apartment': apartment,
        'months': defaultdict(list),
        'occupancy': {},  # Initialize occupancy data
        'profit': {}  # Initialize profit data
    }

    min_month = 1
    max_month = 13
    if apartment.start_date and apartment.start_date.date() > start_date and apartment.start_date.date() < end_date:
        min_month = apartment.start_date.month

    if apartment.end_date and apartment.end_date.date() < end_date and apartment.end_date.date() > start_date:
        max_month = apartment.end_date.month

    num_month = max_month - min_month

    for i in range(12):
        # Calculate the month, wrapping around to the next year if needed
        month_date = start_date + relativedelta(months=i)
        month_occupancy = 0
        month_income = 0
        month_outcome = 0
        month_pending_income = 0
        month_pending_outcome = 0

        # Generate weeks using the month_date
        weeks = generate_weeks(month_date)
        weeks_data = []
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
                    'tenants': [booking.tenant.full_name for booking in bookings_for_day],
                    'tenants_ids': [booking.tenant.id for booking in bookings_for_day],
                    'booking_statuses': [booking.status for booking in bookings_for_day],
                    'booking_starts': [booking.start_date for booking in bookings_for_day],
                    'booking_ends': [booking.end_date for booking in bookings_for_day],
                    'cleaning_ids': [cleaning.id for cleaning in cleanings_for_day],
                    'cleaning_statuses': [cleaning.status for cleaning in cleanings_for_day],
                    'payment_ids': [payment.id for payment in payments_for_day],
                    'payment_types': [payment.payment_type for payment in payments_for_day],
                    'payment_amounts': [payment.amount for payment in payments_for_day],
                    'payment_statuses': [payment.payment_status for payment in payments_for_day],
                }
                week_data.append(day_data)
                # Calculate occupancy for the day
                if day.month == month_date.month and len(bookings_for_day) > 0:
                    month_occupancy += 1

                # Calculate profit for the day
                if day.month == month_date.month:
                    income, outcome, pending_income, pending_outcome = aggregate_data(
                        payments_for_day)
                    month_income += income
                    month_outcome += outcome
                    month_pending_income += pending_income
                    month_pending_outcome += pending_outcome

            weeks_data.append(week_data)

        total_days_in_month = (
            month_date + relativedelta(months=1) - relativedelta(days=1)).day
        occupancy = round((month_occupancy / total_days_in_month)*100)

        apartment_data['months'][month_date] = {
            'weeks': weeks_data,
            'month_occupancy': occupancy,
            'month_total_profit': round(month_income + month_pending_income - month_outcome - month_pending_outcome),
            'month_pending_profit': round(month_pending_income - month_pending_outcome),
            'month_sure_profit': round(month_income - month_outcome),
            'month_outcome': round(month_outcome),
            'month_pending_outcome': round(month_pending_outcome),
            'month_income': round(month_income),
            'month_pending_income':  round(month_pending_income),

        }

        apartment_data["total_occupancy"] = round(sum(month_data.get('month_occupancy', 0)
                                                      for month_data in apartment_data['months'].values()) / num_month)
        apartment_data["profit"] = sum(month_data.get('month_sure_profit', 0)
                                       for month_data in apartment_data['months'].values())
        apartment_data["pending_profit"] = sum(month_data.get('month_pending_profit', 0)
                                               for month_data in apartment_data['months'].values())
        apartment_data["income"] = sum(month_data.get('month_income', 0)
                                       for month_data in apartment_data['months'].values())
        apartment_data["pending_income"] = sum(month_data.get('month_pending_income', 0)
                                               for month_data in apartment_data['months'].values())
        apartment_data["outcome"] = sum(month_data.get('month_outcome', 0)
                                        for month_data in apartment_data['months'].values())
        apartment_data["pending_outcome"] = sum(month_data.get('month_pending_outcome', 0)
                                                for month_data in apartment_data['months'].values())

        apartments_data[apartment.id] = apartment_data

    apartments_data_str_keys = stringify_keys(apartments_data)
    apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    apartments_data = dict(apartments_data)
    for apartment_id, apartment_data in apartments_data.items():
        apartment_data['months'] = dict(apartment_data['months'])

    model_fields = get_model_fields(BookingForm(request=request))
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
    items_json = json.dumps(bookings_data_list, cls=DateEncoder)
    context = {
        'apartments_data': apartments_data,
        'apartments': apartments,
        'items_json': items_json,
        'apartment_id': apartment_id,
        'current_date': timezone.now(),
        'apartments_data_json': apartments_data_json,
        'prev_year': prev_year,
        'model_fields': model_fields,
        'current_year': today.year,
        'next_year': next_year,
        'bookings': bookings,
        'title': "Apt. Callendar",
        'endpoint': "apartment/",
    }

    return render(request, 'apartment.html', context)
