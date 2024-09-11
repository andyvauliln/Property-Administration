from django.shortcuts import render
from ..models import Apartment, Booking, Payment
from django.db.models import Q
import json
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import calculate_unique_booked_days, aggregate_profit_by_category, calculate_total_booked_days, aggregate_data, stringify_keys


@user_has_role('Admin')
def apartments_analytics(request):
    apartment_ids = request.GET.get('ids', "")  # 1,2,5,10
    apartment_type = request.GET.get('type', "")  # In Ownership, In Management
    rooms = request.GET.get('rooms', "")
    year = int(request.GET.get('year', date.today().year))
    apartments = Apartment.objects.all().order_by('name').values_list('id', 'name')

    start_date = date(year, 1, 1)
    today = date.today()
    year_range = list(range(2020, today.year + 3))

    end_date = start_date.replace(year=start_date.year + 1) - timedelta(days=1)
    queryset = Apartment.objects.all().order_by('name')

    # Apply filters based on the criteria
    if apartment_type:
        queryset = queryset.filter(apartment_type=apartment_type)

    if rooms:
        queryset = queryset.filter(bedrooms=rooms)

    selected_apartments = queryset
    if apartment_ids:
        if apartment_ids == '-1':
            # Show all apartments if ids is -1
            selected_apartments = queryset
        else:
            # If apartment_ids are specified, filter based on the list of IDs
            apartment_id_list = [int(id) for id in apartment_ids.split(',')]
            selected_apartments = queryset.filter(id__in=apartment_id_list)

    # Determine if specific apartments are selected

    bookings = Booking.objects.filter(
        Q(start_date__lte=end_date) & Q(end_date__gte=start_date))
    payments = Payment.objects.filter(
        payment_date__range=(start_date, end_date))

    isFilter = any([apartment_ids, apartment_type, rooms])

    if isFilter:
        bookings = bookings.filter(apartment__in=selected_apartments)
        payments = payments.filter(
            Q(apartment__in=selected_apartments) | Q(
                booking__apartment__in=selected_apartments)
        )

    apartments_data = {}
    apartments_month_data = []

    year_occupancy = 0
    year_income = 0
    year_outcome = 0
    year_pending_income = 0
    year_pending_outcome = 0
    year_pending_profit = 0
    year_sure_profit = 0
    year_avg_profit = 0
    year_avg_income = 0
    year_avg_outcome = 0
    year_non_operating_out = 0
    year_non_operating_in = 0

    num_apartments = len(selected_apartments)

    for i in range(12):
        month_date = start_date + relativedelta(months=i)
        next_month_date = month_date + relativedelta(months=1) - timedelta(days=1)

        # Filter bookings and payments for the current month
        bookings_for_month = bookings.filter(
            start_date__lte=next_month_date, end_date__gte=month_date)
        payments_for_month = payments.filter(
            payment_date__gte=month_date, payment_date__lte=next_month_date)

        month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
            payments_for_month)

        # Filter apartments available in this month
        available_apartments = selected_apartments.filter(
            Q(start_date__lte=next_month_date) &
            (Q(end_date__gte=month_date) | Q(end_date__isnull=True))
        ).exclude(name="Additional rental income")

        total_available_days = 0
        total_booked_days = 0

        for apartment in available_apartments:
            # Calculate available days for this apartment in this month
            apt_start = max(apartment.start_date.date() if apartment.start_date else month_date, month_date)
            apt_end = min(apartment.end_date.date() if apartment.end_date else date(9999, 12, 31), next_month_date)
            available_days = (apt_end - apt_start).days + 1
            total_available_days += available_days

            # Calculate booked days for this apartment in this month
            apartment_bookings = bookings_for_month.filter(apartment=apartment)
            booked_days = calculate_unique_booked_days(apartment_bookings, month_date, next_month_date)
            total_booked_days += booked_days

        num_apartments = available_apartments.count()
        total_days_in_month = (next_month_date - month_date).days + 1

        if total_available_days > 0:
            month_occupancy = round((total_booked_days / total_available_days) * 100)
        else:
            month_occupancy = 0

        month_sure_profit = month_income - month_outcome
        month_pending_profit = month_pending_income - month_pending_outcome

        operational_in, operational_out, non_operational_in, non_operational_out = aggregate_profit_by_category(
            payments_for_month)

        apartment_names = list(available_apartments.values_list('name', flat=True))

        if num_apartments > 0:
            month_avg_income = round(month_income / num_apartments)
            month_avg_profit = round(
                (month_income + month_pending_income - month_outcome - month_pending_outcome) / num_apartments)
            month_avg_outcome = round(month_outcome / num_apartments)
        else:
            month_avg_income = 0
            month_avg_outcome = 0
            month_avg_profit = 0


        apartments_month_data.append({
            'date': month_date.strftime('%b'),
            'month_income': round(month_income),
            'month_outcome': round(month_outcome),
            'month_pending_income': round(month_pending_income),
            'month_pending_outcome': round(month_pending_outcome),
            'month_sure_profit': round(month_sure_profit),
            'month_pending_proift': round(month_pending_profit),
            'month_occupancy': month_occupancy,
            'month_avg_profit': month_avg_profit,
            'month_avg_income': month_avg_income,
            'month_avg_outcome': month_avg_outcome,
            'month_apartments_length': num_apartments,
            'apartment_names': apartment_names,
            'month_total_booked_days': total_booked_days,
            'month_total_days': total_available_days,
            'month_non_operating_out': non_operational_out,
            'month_non_operating_in': non_operational_in,
        })

        year_income += month_income
        year_outcome += month_outcome
        year_pending_income += month_pending_income
        year_pending_outcome += month_pending_outcome
        year_pending_profit += month_pending_profit
        year_sure_profit += month_sure_profit
        year_occupancy += month_occupancy
        year_avg_outcome += month_avg_outcome
        year_avg_income += month_avg_income
        year_avg_profit += month_avg_profit
        year_non_operating_out += non_operational_out
        year_non_operating_in += non_operational_in

    apartments_data["apartments_month_data"] = apartments_month_data
    apartments_data["year_income"] = round(year_income)
    apartments_data["year_outcome"] = round(year_outcome)
    apartments_data["year_pending_income"] = round(year_pending_income)
    apartments_data["year_pending_outcome"] = round(year_pending_outcome)
    apartments_data["year_pending_profit"] = round(year_pending_profit)
    apartments_data["year_non_operating_out"] = round(year_non_operating_out)
    apartments_data["year_non_operating_in"] = round(year_non_operating_in)
    apartments_data["year_sure_profit"] = round(year_sure_profit)
    apartments_data["year_avg_profit"] = round(year_avg_profit/12)
    apartments_data["year_avg_income"] = round(year_avg_income/12)
    apartments_data["year_avg_outcome"] = round(year_avg_outcome/12)
    apartments_data["year_occupancy"] = round(year_occupancy / 12)

    aprat_len = apartments_data["apartments_month_data"][-1]["month_apartments_length"]
    selected_apartments_data = []

    if isFilter:
        for apartment in selected_apartments:
            selected_apartment = {
                'apartment': apartment,
                'month_data': [],
                "year_income": 0,
                "year_outcome": 0,
                "year_pending_income": 0,
                "year_pending_outcome": 0,
                "year_pending_profit": 0,
                "year_sure_profit": 0,
                "year_occupancy": 0,
                "year_avg_profit": 0,
                "year_avg_income": 0,
                "year_avg_outcome": 0,
                "year_non_operating_out": 0,
                "year_non_operating_in": 0,
            }

            if apartment.start_date and apartment.start_date.date() >= end_date:
                continue
            if apartment.end_date and apartment.end_date.date() <= start_date:
                continue

            # Define the minimum and maximum months based on start_date, start_date_apartment, end_date, and end_date_apartment
            min_month = 1
            max_month = 13
            if apartment.start_date and apartment.start_date.date() > start_date and apartment.start_date.date() < end_date:
                min_month = apartment.start_date.month

            if apartment.end_date and apartment.end_date.date() < end_date and apartment.end_date.date() > start_date:
                max_month = apartment.end_date.month

            num_month = max_month - min_month

            for i in range(12):
                month_date = start_date + relativedelta(months=i)
                next_month_date = month_date + \
                    relativedelta(months=1) - relativedelta(days=1)

                # Filter bookings and payments for the current month and selected apartment
                bookings_for_month = bookings.filter(start_date__lte=next_month_date,
                                                     end_date__gte=month_date, apartment=apartment)
                payments_for_month = payments.filter(
                    Q(apartment=apartment) | Q(booking__apartment=apartment),
                    payment_date__gte=month_date,
                    payment_date__lte=next_month_date
                )

                month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
                    payments_for_month)
                total_days_in_month = next_month_date.day

                total_booked_days = calculate_unique_booked_days(
                    bookings_for_month, month_date, next_month_date)

                month_occupancy = round(
                    (total_booked_days / (total_days_in_month)) * 100)

                month_sure_profit = month_income - month_outcome
                month_pending_profit = month_pending_income - month_pending_outcome

                operational_in, operational_out, non_operational_in, non_operational_out = aggregate_profit_by_category(
                    payments_for_month)

                selected_apartment['month_data'].append({
                    'month_date': month_date.strftime('%b'),
                    'month_income': round(month_income),
                    'month_outcome': round(month_outcome),
                    'month_pending_income': round(month_pending_income),
                    'month_pending_outcome': round(month_pending_outcome),
                    'month_pending_profit': round(month_pending_profit),
                    'month_sure_profit': round(month_sure_profit),
                    'month_occupancy': round(month_occupancy),
                    'total_days_in_month': total_days_in_month,
                    'total_booked_days': total_booked_days,
                    'month_non_operating_out': non_operational_out,
                    'month_non_operating_in': non_operational_in,
                })
                selected_apartment["year_income"] += month_income
                selected_apartment["year_outcome"] += month_outcome
                selected_apartment["year_pending_income"] += month_pending_income
                selected_apartment["year_pending_outcome"] += month_pending_outcome
                selected_apartment["year_pending_profit"] += month_pending_profit
                selected_apartment["year_sure_profit"] += month_sure_profit
                selected_apartment["year_occupancy"] += month_occupancy
                selected_apartment["year_non_operating_out"] += non_operational_out
                selected_apartment["year_non_operating_in"] += non_operational_in

            selected_apartment["year_avg_profit"] = round(
                (selected_apartment["year_sure_profit"] + selected_apartment["year_pending_profit"])/num_month)
            selected_apartment["year_avg_income"] = round(
                (selected_apartment["year_income"] + selected_apartment["year_pending_income"])/num_month)
            selected_apartment["year_avg_outcome"] = round(
                (selected_apartment["year_outcome"] + selected_apartment["year_pending_outcome"])/num_month)
            selected_apartments_data.append(selected_apartment)
            selected_apartment["year_occupancy"] = round(
                selected_apartment["year_occupancy"]/num_month)

    apartments_data["selected_apartments_data"] = selected_apartments_data
    apartments_data_str_keys = stringify_keys(apartments_data)
    apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    context = {
        'apartments_data': apartments_data,
        'apartments': apartments,
        'apartments_data_json': apartments_data_json,
        'apartment_ids': apartment_ids,
        'current_year': today.year,
        'year_range': year_range,
        'aprat_len': aprat_len,
        'year': year,
        'title': "Apt. Report",
        'isFilter': isFilter
    }

    return render(request, 'apartments_analytics.html', context)
