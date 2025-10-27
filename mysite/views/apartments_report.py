from django.shortcuts import render, redirect
from ..models import Apartment, Booking, Payment
from django.db.models import Q, Sum
import json
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import calculate_unique_booked_days, aggregate_profit_by_category, calculate_total_booked_days, aggregate_data, stringify_keys
from .booking_report import get_google_sheets_service, share_document_with_user
import logging
from datetime import datetime


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


logger_common = logging.getLogger('mysite.common')


def print_info(message):
    print(message)
    logger_common.debug(message)


@user_has_role('Admin', 'Manager')
def apartment_report(request):
    if request.user.role == 'Manager':
        bookings = Booking.objects.filter(apartment__manager=request.user)
    else:
        bookings = Booking.objects.all()

    try:
        referer_url = request.META.get('HTTP_REFERER', '/')
        report_start_date = request.GET.get('report_start_date', None)
        report_end_date = request.GET.get('report_end_date', None)
        if report_start_date and report_end_date:
            start_date = datetime.strptime(report_start_date, "%B %d %Y").date()
            end_date = datetime.strptime(report_end_date, "%B %d %Y").date()
            # Include bookings that overlap the period
            bookings = bookings.filter(start_date__lte=end_date, end_date__gte=start_date)
            if bookings.exists():
                report_url = generate_apartment_excel(bookings, start_date, end_date)
                print_info(f'Apartment report created {report_url}')
                return redirect(report_url)
        return redirect(referer_url)
    except Exception as e:
        print_info(f"Error: Generating Apartment Report Error, {str(e)}")
        return redirect(request.META.get('HTTP_REFERER', '/'))


def generate_apartment_excel(bookings, start_date, end_date):
    sheets_service, drive_service = get_google_sheets_service()

    title_start = start_date.strftime('%B %d %Y')
    title_end = end_date.strftime('%B %d %Y')

    spreadsheet_body = {
        'properties': {
            'title': f"Apartment Report: {title_start} - {title_end}"
        },
        'sheets': [{
            'properties': {
                'title': 'Apartment Report'
            }
        }]
    }
    spreadsheet = sheets_service.create(body=spreadsheet_body).execute()
    spreadsheet_id = spreadsheet.get('spreadsheetId')

    rows = prepare_apartment_rows(bookings, start_date, end_date)
    insert_apartment_rows(sheets_service, spreadsheet_id, rows)

    share_document_with_user(drive_service, spreadsheet_id)

    return f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'


def prepare_apartment_rows(bookings, period_start, period_end):
    rows = []
    for booking in bookings:
        apartment_name = booking.apartment.name if booking.apartment else ''
        # Clamp booking dates to requested period
        clamped_start = max(booking.start_date, period_start)
        clamped_end = min(booking.end_date, period_end)

        days = (clamped_end - clamped_start).days if (clamped_start and clamped_end) else 0
        days = days if days > 0 else 0

        # Sum payments: Rent + Hold Deposit (In), linked to this booking, within clamped period
        total_payment = booking.payments.filter(
            payment_type__type='In',
            payment_type__name__in=['Rent', 'Hold Deposit'],
            payment_date__gte=clamped_start,
            payment_date__lte=clamped_end,
        ).aggregate(total=Sum('amount'))['total'] or 0

        adr_payments = float(total_payment) / days if days > 0 else 0

        # Price ADR: weighted average per-day price across the clamped period
        price_sum = 0.0
        if booking.apartment and days > 0:
            current = clamped_start
            while current < clamped_end:
                price_for_day = booking.apartment.get_price_on_date(current)
                if price_for_day is None:
                    price_for_day = booking.apartment.default_price or 0
                price_sum += float(price_for_day)
                current = current + timedelta(days=1)
        adr_price = (price_sum / days) if days > 0 else 0

        renter = booking.tenant.full_name if booking.tenant else ''

        rows.append([
            apartment_name,
            clamped_start.isoformat() if days > 0 else '',
            clamped_end.isoformat() if days > 0 else '',
            days,
            float(total_payment),
            round(adr_payments, 2),
            round(adr_price, 2),
            renter,
        ])
    return rows


def insert_apartment_rows(sheets_service, spreadsheet_id, rows):
    headers = [
        'Apartment', 'Start Date', 'End Date', 'Days',
        'Total Rent Payment', 'ADR (Payments)', 'ADR (Price)', 'Renter Name'
    ]
    values = [headers] + rows
    data_range = 'Apartment Report!A1'
    sheets_service.values().update(
        spreadsheetId=spreadsheet_id,
        range=data_range,
        valueInputOption='USER_ENTERED',
        body={'values': values}
    ).execute()
