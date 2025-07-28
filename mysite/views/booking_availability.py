from django.shortcuts import render
from ..models import Apartment, Booking, Payment
from django.db.models import Q
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from datetime import timedelta
from calendar import monthrange
from django.db.models import Prefetch
import datetime

@user_has_role('Admin', "Manager")
def booking_availability(request):
    current_apartment_type = request.GET.get('apartment_type', '')
    booking_status = request.GET.get('booking_status', '')
    current_rooms = request.GET.get('rooms', None)
    
    # Calculate start and end dates based on the page (6 months)
    current_date = timezone.now().date()
    page_offset = int(request.GET.get('page', 0))
    start_date = (current_date.replace(day=1) + relativedelta(months=page_offset * 3)).replace(day=1)
    end_date = start_date + relativedelta(months=3, days=-1)

    # Prepare the Prefetch for booked_apartments
    booking_queryset = Booking.objects.filter(
        start_date__lte=end_date,
        end_date__gte=start_date
    )

    if request.user.role == 'Manager':
        booking_queryset = booking_queryset.filter(apartment__manager=request.user)

    if booking_status and booking_status != 'Available':
        booking_queryset = booking_queryset.filter(status=booking_status)

    booking_queryset = booking_queryset.prefetch_related('payments')

    # Create the Prefetch object
    prefetch_bookings = Prefetch('booked_apartments', queryset=booking_queryset, to_attr='all_relevant_bookings')

    # Fetch apartments based on filters
    apartments = Apartment.objects.all()
    
    if request.user.role == 'Manager':
        apartments = apartments.filter(manager=request.user)

    if current_apartment_type:
        apartments = apartments.filter(apartment_type=current_apartment_type)

    if current_rooms:
        apartments = apartments.filter(bedrooms=current_rooms)

    # Filter out apartments that are not available during the selected period
    availability_query = Q(
        Q(start_date__isnull=True) | Q(start_date__lte=end_date)
    ) & (
        Q(end_date__isnull=True) | Q(end_date__gte=start_date)
    )
    apartments = apartments.filter(availability_query)

    apartments = apartments.prefetch_related(
        prefetch_bookings,
        Prefetch('payments',  # Apartment payments
                queryset=Payment.objects.filter(
                    payment_date__gte=start_date,
                    payment_date__lte=end_date
                ).select_related('payment_type'),
                to_attr='all_relevant_apartment_payments'
        )
    )
    
    # Prepare monthly data
    monthly_data = []
    current_month = start_date
    while current_month <= end_date:
        days_in_month = monthrange(current_month.year, current_month.month)[1]
        month_end = current_month.replace(day=days_in_month)
        month_start = current_month

        month_data = {
            'month_name': current_month.strftime('%B %Y'),
            'apartments': [],
            'month_revenue': 0,
            'month_max_revenue': 0,  # New: max revenue with 100% occupancy using default_price
            'month_current_price_revenue': 0,  # New: revenue with 100% occupancy using current price
            'month_occupancy': 0,
            'days_in_month': days_in_month,
            'blocked_days': 0,
            'pending_days': 0,
            'problem_booking_days': 0,
        }
        
        print(f"\n--- Processing {current_month.strftime('%B %Y')} ---")
        print(f"Total apartments before filtering: {len(apartments)}")

        for apartment in apartments:
            apartment_data = {
                'id': apartment.id,
                'name': apartment.name,
                'apartment_type': apartment.apartment_type,
                'raiting': apartment.raiting,
                'days': {},
                'booked_days': 0,
                'start_date': apartment.start_date,
                'end_date': apartment.end_date,
                'web_link': apartment.web_link,
            }

            bookings = [b for b in apartment.all_relevant_bookings 
                if b.start_date <= month_end and b.end_date >= month_start]

            # Skip this apartment for this month if booking_status is 'Available' and there are bookings
            if booking_status == 'Available' and bookings and any(b.start_date <= month_end and b.end_date >= current_date for b in bookings):
                print(f"  SKIPPING {apartment.name}: has bookings while filtering for Available")
                continue

            # Check if the apartment has ended
            if booking_status == 'Available' and apartment.end_date:
                apartment_end_date = apartment.end_date.date() if hasattr(apartment.end_date, 'date') else apartment.end_date
                if apartment_end_date < month_start:
                    print(f"  SKIPPING {apartment.name}: ended before month start")
                    continue

            # apartment_payments = [p for p in apartment.pa 
            #             if month_start <= p.payment_date <= month_end]

            # Calculate available days for this apartment in this month
            apartment_available_days = 0
            
            for day in range(1, days_in_month + 1):
                date_obj = current_month.replace(day=day)
                apartment_data['days'][day] = {
                    'status': 'Available',
                    'is_start': False,
                    'is_end': False,
                    'tenant_names': [],  # List of tenant names
                    'day': date_obj.strftime('%B %d %Y'),  # Date in 'Month Day Year' format
                    'notes': [],  # List of notes
                    'booking_data': [],  # List of booking data strings
                    'booking_ids': [],  # List of booking IDs
                    'raiting': apartment.raiting,
                }

                day_bookings = [b for b in bookings if b.start_date <= date_obj <= b.end_date]
                
                # Check if the apartment is available on this date
                if (apartment.start_date and date_obj <= apartment.start_date.date()) or (apartment.end_date and date_obj >= apartment.end_date.date()):
                    apartment_data['days'][day]['status'] = 'Blocked'
                    month_data['blocked_days'] += 1
                    print(f"  {apartment.name} - Day {day} ({date_obj}): BLOCKED (apartment availability)")
                elif day_bookings:
                    apartment_available_days += 1  # Count this day as available for revenue calculation
                    
                    statuses = set(b.status for b in day_bookings)
                    if 'Confirmed' in statuses:
                        apartment_data['days'][day]['status'] = 'Confirmed'
                    elif 'Waiting Contract' in statuses:
                        apartment_data['days'][day]['status'] = 'Waiting Contract'
                    elif 'Waiting Payment' in statuses:
                        apartment_data['days'][day]['status'] = 'Waiting Payment'
                    elif 'Blocked' in statuses:
                        apartment_data['days'][day]['status'] = 'Blocked'
                    elif 'Pending' in statuses:
                        apartment_data['days'][day]['status'] = 'Pending'
                    elif 'Problem Booking' in statuses:
                        apartment_data['days'][day]['status'] = 'Problem Booking'

                    apartment_data['days'][day]['is_start'] = any(date_obj == b.start_date for b in day_bookings)
                    apartment_data['days'][day]['is_end'] = any(date_obj == b.end_date for b in day_bookings)

                    # Count day types based on status (only once per day)
                    if 'Blocked' in statuses:
                        month_data['blocked_days'] += 1
                        apartment_available_days -= 1  # Remove from available days if blocked
                        print(f"  {apartment.name} - Day {day} ({date_obj}): BLOCKED (booking status)")
                    elif 'Pending' in statuses:
                        month_data['pending_days'] += 1
                        # Count this day as occupied for occupancy calculation
                        month_data['month_occupancy'] += 1
                        print(f"  {apartment.name} - Day {day} ({date_obj}): OCCUPIED (Status: {apartment_data['days'][day]['status']}, Bookings: {len(day_bookings)})")
                    elif 'Problem Booking' in statuses:
                        month_data['problem_booking_days'] += 1
                        # Count this day as occupied for occupancy calculation
                        month_data['month_occupancy'] += 1
                        print(f"  {apartment.name} - Day {day} ({date_obj}): OCCUPIED (Status: {apartment_data['days'][day]['status']}, Bookings: {len(day_bookings)})")
                    else:
                        # For Confirmed, Waiting Contract, Waiting Payment - count as occupied
                        month_data['month_occupancy'] += 1
                        print(f"  {apartment.name} - Day {day} ({date_obj}): OCCUPIED (Status: {apartment_data['days'][day]['status']}, Bookings: {len(day_bookings)})")

                    for booking in day_bookings:
                        if booking.status in ['Confirmed', 'Waiting Contract', 'Waiting Payment']:
                            if booking.tenant and booking.tenant.full_name:
                                apartment_data['days'][day]['tenant_names'].append(booking.tenant.full_name)
                            if booking.status == 'Confirmed':
                                apartment_data['booked_days'] += 1

                        # Add notes and booking data
                        apartment_data['days'][day]['notes'].append(booking.notes)
                        booking_data_entry = f"{booking.start_date.strftime('%B %d %Y')} - {booking.end_date.strftime('%B %d %Y')} \n [{booking.tenant.full_name if booking.tenant else ''}] [{booking.status}] \n {booking.notes} \n"
                        
                        current_period_payments = [
                            p for p in booking.payments.all()
                            if date_obj.replace(day=1) <= p.payment_date <= date_obj.replace(
                                day=monthrange(date_obj.year, date_obj.month)[1]
                            )
                        ]

                        payment_str = ""
                        for payment in current_period_payments:
                            formatted_date = payment.payment_date.strftime("%B %d %Y")
                            payment_str += f" \n  {payment.payment_type}, [ {formatted_date} ], ${payment.amount}"

                        payment_str = payment_str if current_period_payments else "No payments this month"
                        booking_data_entry += f"Payments: \n{payment_str}"
                        apartment_data['days'][day]['booking_data'].append(booking_data_entry)

                        # Add the booking ID to booking_ids
                        apartment_data['days'][day]['booking_ids'].append(booking.id)
                elif apartment.end_date and date_obj > apartment.end_date.date():
                    apartment_data['days'][day]['status'] = 'Blocked'
                    month_data['blocked_days'] += 1
                    print(f"  {apartment.name} - Day {day} ({date_obj}): BLOCKED (past apartment end date)")
                elif date_obj < current_date:
                    apartment_data['days'][day]['past'] = True
                    apartment_available_days += 1  # Count past days as available for revenue calculation
                else:
                    apartment_available_days += 1  # Count available days

            # Calculate revenue for this apartment in this month
            apartment_revenue = 0
            for booking in bookings:
                if booking.status == 'Confirmed':
                    booking_payments = [p for p in booking.payments.all() 
                                        if month_start <= p.payment_date <= month_end]
                    apartment_revenue += sum(p.amount if p.payment_type.type == "In" else 0 
                                            for p in booking_payments)


            # Calculate max revenue with 100% occupancy using default_price
            apartment_max_revenue = 0
            if apartment.default_price:
                apartment_max_revenue = float(apartment.default_price/30) * apartment_available_days

            # Calculate current price revenue with 100% occupancy using current price
            apartment_current_price_revenue = 0
            current_price = apartment.get_price_on_date(month_start)
            if current_price:
                apartment_current_price_revenue = float(current_price/30) * apartment_available_days

            month_data['month_revenue'] += apartment_revenue
            month_data['month_max_revenue'] += apartment_max_revenue
            month_data['month_current_price_revenue'] += apartment_current_price_revenue
            
            apartment_data['revenue'] = apartment_revenue
            apartment_data['max_revenue'] = apartment_max_revenue
            apartment_data['current_price_revenue'] = apartment_current_price_revenue
            apartment_data['available_days'] = apartment_available_days

            month_data['apartments'].append(apartment_data)
            print(f"  PROCESSED {apartment.name}: {apartment_data['booked_days']} booked days, Max Revenue: ${apartment_max_revenue}, Current Price Revenue: ${apartment_current_price_revenue}")

        # Sort apartments based on booked days (ascending order)
        month_data['apartments'].sort(key=lambda x: x['booked_days'])

        # Calculate occupancy percentage
        total_apartments_in_month = len(month_data['apartments'])
        total_days = total_apartments_in_month * days_in_month - month_data['blocked_days']
        occupied_days = month_data['month_occupancy']
        
        # Debug logging
        print(f"\n=== DEBUG OCCUPANCY CALCULATION FOR {month_data['month_name']} ===")
        print(f"Total apartments processed: {total_apartments_in_month}")
        print(f"Days in month: {days_in_month}")
        print(f"Blocked days: {month_data['blocked_days']}")
        print(f"Occupied days: {occupied_days}")
        print(f"Total available days: {total_days}")
        print(f"Raw occupancy calculation: {occupied_days} / {total_days} = {(occupied_days / total_days) if total_days > 0 else 0}")
        print(f"Max Revenue: ${month_data['month_max_revenue']}")
        print(f"Current Price Revenue: ${month_data['month_current_price_revenue']}")
        
        month_data['month_occupancy'] = (occupied_days / total_days) * 100 if total_days > 0 else 0
        print(f"Final occupancy percentage: {month_data['month_occupancy']:.2f}%")
        print("=" * 60)

        
        month_data['month_potential_profit'] = month_data['month_occupancy'] - month_data['blocked_days']

        monthly_data.append(month_data)
        current_month += relativedelta(months=1)

    context = {
        'monthly_data': monthly_data,
        'apartments': Apartment.objects.values_list('name', flat=True).distinct(),
        'apartment_types': Apartment.TYPES,
        'current_apartment_type': current_apartment_type,
        'current_booking_status': booking_status,
        'start_date': start_date.strftime('%B %d %Y'),
        'end_date': end_date.strftime('%B %d %Y'),
        'prev_page': page_offset - 1,
        'next_page': page_offset + 1,
        'current_year': start_date.year,
        'current_date': current_date,
        'current_rooms': current_rooms,
    }

    return render(request, 'booking_availability.html', context)
