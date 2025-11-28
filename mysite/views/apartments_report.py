from django.shortcuts import render, redirect
from ..models import Apartment, Booking, Payment, ApartmentPrice
from django.db.models import Q, Sum, Prefetch
import json
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import calculate_unique_booked_days, aggregate_profit_by_category, calculate_total_booked_days, aggregate_data, stringify_keys
from .booking_report import get_google_sheets_service, share_document_with_user
import logging
from datetime import datetime
from collections import defaultdict


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
            
            logger.info(f'Generating report for period: {start_date} to {end_date}')
            
            # Include bookings that overlap the period
            bookings = bookings.filter(start_date__lte=end_date, end_date__gte=start_date)
            
            booking_count = bookings.count()
            logger.info(f'Found {booking_count} bookings')
            
            if bookings.exists():
                report_url = generate_apartment_excel(bookings, start_date, end_date)
                logger.info(f'Apartment report created {report_url}')
                return redirect(report_url)
        return redirect(referer_url)
    except Exception as e:
        logger.info(f"Error: Generating Apartment Report Error, {str(e)}")
        import traceback
        logger.info(f"Traceback: {traceback.format_exc()}")
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
    
    # Get the actual sheet ID from the created spreadsheet
    sheet_id = spreadsheet['sheets'][0]['properties']['sheetId']
    logger.info(f'Created spreadsheet with sheet ID: {sheet_id}')

    rows = prepare_apartment_rows(bookings, start_date, end_date)
    insert_apartment_rows(sheets_service, spreadsheet_id, rows, sheet_id)

    share_document_with_user(drive_service, spreadsheet_id)

    return f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'


def build_price_cache(apartment_ids, period_start, period_end):
    """
    Build a cache of all apartment prices for the period.
    Returns: dict[apartment_id] -> list of (effective_date, price) tuples
    """
    logger.info(f"Building price cache for apartments: {apartment_ids}")
    
    price_cache = defaultdict(list)
    
    # Fetch all relevant prices at once
    prices = ApartmentPrice.objects.filter(
        apartment_id__in=apartment_ids,
        effective_date__lte=period_end
    ).select_related('apartment').order_by('apartment_id', '-effective_date')
    
    for price in prices:
        price_cache[price.apartment_id].append({
            'effective_date': price.effective_date,
            'price': float(price.price)
        })
    
    # Also get default prices
    default_prices = Apartment.objects.filter(
        id__in=apartment_ids
    ).values('id', 'default_price')
    
    for apt in default_prices:
        if apt['id'] not in price_cache or not price_cache[apt['id']]:
            price_cache[apt['id']].append({
                'effective_date': date(2000, 1, 1),
                'price': float(apt['default_price'] or 0)
            })
    
    logger.info(f"Price cache built with {len(price_cache)} apartments")
    return price_cache


def get_price_for_date_cached(price_cache, apartment_id, target_date, default_price):
    """Get price from cache for a specific date"""
    if apartment_id not in price_cache:
        return float(default_price or 0)
    
    # Prices are already sorted by effective_date descending
    for price_entry in price_cache[apartment_id]:
        if price_entry['effective_date'] <= target_date:
            return price_entry['price']
    
    return float(default_price or 0)


def calculate_daily_price_optimized(price_cache, apartment_id, default_price, start_date, end_date):
    """
    Optimized calculation of average daily price across a period.
    Instead of querying DB for each day, uses cached prices and calculates ranges.
    """
    if apartment_id not in price_cache or not price_cache[apartment_id]:
        monthly_price = float(default_price or 0)
        days = (end_date - start_date).days
        return (monthly_price / 30.0) * days if days > 0 else 0
    
    # Get all prices that could affect this period
    relevant_prices = [p for p in price_cache[apartment_id] 
                      if p['effective_date'] <= end_date]
    
    if not relevant_prices:
        monthly_price = float(default_price or 0)
        days = (end_date - start_date).days
        return (monthly_price / 30.0) * days if days > 0 else 0
    
    # Sort by effective_date ascending for easier processing
    relevant_prices = sorted(relevant_prices, key=lambda x: x['effective_date'])
    
    total_daily_sum = 0.0
    current_date = start_date
    
    # Find which price applies at the start
    current_price_idx = 0
    for i, price_entry in enumerate(relevant_prices):
        if price_entry['effective_date'] <= start_date:
            current_price_idx = i
    
    while current_date < end_date:
        # Find the next price change date
        next_price_date = end_date
        if current_price_idx + 1 < len(relevant_prices):
            next_change = relevant_prices[current_price_idx + 1]['effective_date']
            if next_change < end_date:
                next_price_date = next_change
        
        # Calculate days at current price
        days_at_price = (min(next_price_date, end_date) - current_date).days
        monthly_price = relevant_prices[current_price_idx]['price']
        daily_price = monthly_price / 30.0
        total_daily_sum += daily_price * days_at_price
        
        current_date = next_price_date
        current_price_idx += 1
    
    return total_daily_sum


def prepare_apartment_rows(bookings, period_start, period_end):
    """
    Optimized version with:
    - Single query to prefetch all related data
    - Price caching to avoid N+1 queries
    - JSON export for debugging
    """
    logger.info(f"Starting to prepare rows for {bookings.count() if hasattr(bookings, 'count') else len(bookings)} bookings")
    
    # OPTIMIZATION 1: Prefetch all related data in a single query
    bookings = bookings.select_related(
        'apartment',
        'tenant'
    ).prefetch_related(
        Prefetch(
            'payments',
            queryset=Payment.objects.select_related('payment_type').filter(
                payment_type__type='In',
                payment_type__name__in=['Rent', 'Hold Deposit']
            )
        )
    )
    
    # Convert to list to avoid multiple DB hits
    bookings_list = list(bookings)
    logger.info(f"Loaded {len(bookings_list)} bookings with related data")
    
    # OPTIMIZATION 2: Build price cache for all apartments at once
    apartment_ids = [b.apartment.id for b in bookings_list if b.apartment]
    price_cache = build_price_cache(apartment_ids, period_start, period_end)
    
    rows = []
    
    for idx, booking in enumerate(bookings_list):
        if idx % 50 == 0:
            logger.info(f"Processing booking {idx + 1}/{len(bookings_list)}")
        
        apartment_name = booking.apartment.name if booking.apartment else ''
        apartment_id = booking.apartment.id if booking.apartment else None
        default_price = booking.apartment.default_price if booking.apartment else 0
        
        # Clamp booking dates to requested period
        clamped_start = max(booking.start_date, period_start)
        clamped_end = min(booking.end_date, period_end)

        days = (clamped_end - clamped_start).days if (clamped_start and clamped_end) else 0
        days = days if days > 0 else 0

        # Filter payments from prefetched data
        rent_and_deposit_payments = [
            p for p in booking.payments.all()
            if p.payment_date >= clamped_start and p.payment_date <= clamped_end
        ]
        
        total_payment = sum(float(p.amount) for p in rent_and_deposit_payments)
        payment_found = len(rent_and_deposit_payments) > 0
        
        adr_payments = float(total_payment) / days if days > 0 else 0

        # OPTIMIZATION 3: Use cached prices with range calculation
        daily_price_sum = 0.0
        if apartment_id and days > 0:
            daily_price_sum = calculate_daily_price_optimized(
                price_cache, apartment_id, default_price, clamped_start, clamped_end
            )
        
        adr_price_table = (daily_price_sum / days) if days > 0 else 0

        # Calculate price difference
        price_difference = adr_payments - adr_price_table if days > 0 else 0

        renter = booking.tenant.full_name if booking.tenant else ''

        rows.append({
            'booking_id': booking.id,
            'apartment_name': apartment_name,
            'start_date': clamped_start.isoformat() if days > 0 else '',
            'end_date': clamped_end.isoformat() if days > 0 else '',
            'days': days,
            'total_payment': float(total_payment),
            'adr_payments': round(adr_payments, 2),
            'adr_price_table': round(adr_price_table, 2),
            'price_difference': round(price_difference, 2),
            'renter': renter,
            'payment_found': payment_found,
        })
    
    logger.info(f"Completed processing all {len(rows)} bookings")
    
    # DEBUGGING: Export to JSON file
    try:
        import os
        debug_file = '/tmp/apartment_report_debug.json'
        with open(debug_file, 'w') as f:
            json.dump({
                'period_start': period_start.isoformat(),
                'period_end': period_end.isoformat(),
                'total_bookings': len(rows),
                'bookings_with_missing_payments': sum(1 for r in rows if not r['payment_found']),
                'rows': rows
            }, f, indent=2)
        logger.info(f"Debug data exported to {debug_file}")
    except Exception as e:
        logger.info(f"Could not export debug JSON: {e}")
    
    return rows


def insert_apartment_rows(sheets_service, spreadsheet_id, rows, sheet_id):
    headers = [
        'Apartment', 'Start Date', 'End Date', 'Days',
        'Total Rent Payment', 'ADR (Payments)', 'ADR (Price Table)', 
        'Price Difference', 'Renter Name', 'Payment Found'
    ]
    
    # Convert row dictionaries to lists in the correct order
    values = [headers]
    for row in rows:
        values.append([
            row['apartment_name'],
            row['start_date'],
            row['end_date'],
            row['days'],
            row['total_payment'],
            row['adr_payments'],
            row['adr_price_table'],
            row['price_difference'],
            row['renter'],
            'Yes' if row['payment_found'] else 'No'
        ])
    
    # Insert data
    data_range = 'Apartment Report!A1'
    sheets_service.values().update(
        spreadsheetId=spreadsheet_id,
        range=data_range,
        valueInputOption='USER_ENTERED',
        body={'values': values}
    ).execute()
    
    # Apply conditional formatting to color rows red where payment is not found
    requests = []
    
    # Add conditional formatting for rows where Payment Found = "No"
    # This will color the entire row red
    for idx, row in enumerate(rows):
        if not row['payment_found']:
            row_number = idx + 2  # +2 because headers are row 1, data starts at row 2
            requests.append({
                'repeatCell': {
                    'range': {
                        'sheetId': sheet_id,
                        'startRowIndex': row_number - 1,
                        'endRowIndex': row_number,
                        'startColumnIndex': 0,
                        'endColumnIndex': 10
                    },
                    'cell': {
                        'userEnteredFormat': {
                            'backgroundColor': {
                                'red': 1.0,
                                'green': 0.8,
                                'blue': 0.8
                            }
                        }
                    },
                    'fields': 'userEnteredFormat.backgroundColor'
                }
            })
    
    # Format header row (bold + background color)
    requests.append({
        'repeatCell': {
            'range': {
                'sheetId': sheet_id,
                'startRowIndex': 0,
                'endRowIndex': 1,
                'startColumnIndex': 0,
                'endColumnIndex': 10
            },
            'cell': {
                'userEnteredFormat': {
                    'textFormat': {
                        'bold': True
                    },
                    'backgroundColor': {
                        'red': 0.85,
                        'green': 0.85,
                        'blue': 0.85
                    }
                }
            },
            'fields': 'userEnteredFormat(textFormat,backgroundColor)'
        }
    })
    
    # Auto-resize columns
    requests.append({
        'autoResizeDimensions': {
            'dimensions': {
                'sheetId': sheet_id,
                'dimension': 'COLUMNS',
                'startIndex': 0,
                'endIndex': 10
            }
        }
    })
    
    # Apply all formatting
    if requests:
        sheets_service.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute()
