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

    if booking_status and booking_status != 'Available':
        booking_queryset = booking_queryset.filter(status=booking_status)

    booking_queryset = booking_queryset.prefetch_related('payments')

    # Create the Prefetch object
    prefetch_bookings = Prefetch('booked_apartments', queryset=booking_queryset, to_attr='all_relevant_bookings')

    # Fetch apartments based on filters
    apartments = Apartment.objects.all()
    
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
            'month_occupancy': 0,
            'days_in_month': days_in_month,
            'blocked_days': 0,
            'pending_days': 0,
            'problem_booking_days': 0,
        }

        for apartment in apartments:
            apartment_data = {
                'id': apartment.id,
                'name': apartment.name,
                'apartment_type': apartment.apartment_type,
                'days': {},
                'booked_days': 0,
                'start_date': apartment.start_date,
                'end_date': apartment.end_date,
            }

            bookings = [b for b in apartment.all_relevant_bookings 
                if b.start_date <= month_end and b.end_date >= month_start]

            # Skip this apartment for this month if booking_status is 'Available' and there are bookings
            if booking_status == 'Available' and bookings and any(b.start_date <= month_end and b.end_date >= current_date for b in bookings):
                continue

            # Check if the apartment has ended
            if booking_status == 'Available' and apartment.end_date:
                apartment_end_date = apartment.end_date.date() if hasattr(apartment.end_date, 'date') else apartment.end_date
                if apartment_end_date < month_start:
                    continue

            apartment_payments = [p for p in apartment.all_relevant_apartment_payments 
                        if month_start <= p.payment_date <= month_end]

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
                }

                day_bookings = [b for b in bookings if b.start_date <= date_obj <= b.end_date]
                
                # Check if the apartment is available on this date
                if apartment.start_date and date_obj < apartment.start_date.date():
                    apartment_data['days'][day]['status'] = 'Blocked'
                    month_data['blocked_days'] += 1
                elif day_bookings:
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
                        month_data['problem_booking_days'] += 1

                    apartment_data['days'][day]['is_start'] = any(date_obj == b.start_date for b in day_bookings)
                    apartment_data['days'][day]['is_end'] = any(date_obj == b.end_date for b in day_bookings)

                    for booking in day_bookings:
                        if booking.status in ['Confirmed', 'Waiting Contract', 'Waiting Payment']:
                            if booking.tenant and booking.tenant.full_name:
                                apartment_data['days'][day]['tenant_names'].append(booking.tenant.full_name)
                            if booking.status == 'Confirmed':
                                apartment_data['booked_days'] += 1
                                month_data['month_occupancy'] += 1
                        if booking.status == 'Blocked':
                            month_data['blocked_days'] += 1
                        if booking.status == 'Pending':
                            month_data['pending_days'] += 1
                        if booking.status == 'Problem Booking':
                            month_data['problem_booking_days'] += 1

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
                elif date_obj < current_date:
                    apartment_data['days'][day]['past'] = True

            # Calculate revenue for this apartment in this month
            apartment_revenue = 0
            for booking in bookings:
                if booking.status == 'Confirmed':
                    booking_payments = [p for p in booking.payments.all() 
                                        if month_start <= p.payment_date <= month_end]
                    apartment_revenue += sum(p.amount if p.payment_type.type == "In" else -p.amount 
                                            for p in booking_payments)

            # Add apartment payments
            apartment_revenue += sum(p.amount if p.payment_type.type == "In" else -p.amount 
                                    for p in apartment_payments)

            month_data['month_revenue'] += apartment_revenue
            apartment_data['revenue'] = apartment_revenue

            month_data['apartments'].append(apartment_data)

        # Sort apartments based on booked days (ascending order)
        month_data['apartments'].sort(key=lambda x: x['booked_days'])

        # Calculate occupancy percentage
        total_days = len(apartments) * days_in_month - month_data['blocked_days']
        month_data['month_occupancy'] = (month_data['month_occupancy'] / total_days) * 100 if total_days > 0 else 0

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
