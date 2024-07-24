
from django.shortcuts import render
from ..models import Apartment, Booking, Payment
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
from datetime import datetime, timedelta
from calendar import monthrange
from django.db.models import Prefetch, Q


@user_has_role('Admin', "Manager")
def booking_availability(request):
    current_apartment = request.GET.get('apartment', '')
    current_apartment_type = request.GET.get('apartment_type', '')
    booking_status = request.GET.get('booking_status', '')
    
    # Calculate start and end dates based on the page (year)
    current_date = timezone.now().date()
    page_offset = int(request.GET.get('page', 0))
    start_date = (current_date.replace(day=1) + relativedelta(months=page_offset)).replace(day=1)
    end_date = start_date + relativedelta(months=12, days=-1)

    # Prepare the Prefetch for booked_apartments
    booking_queryset = Booking.objects.filter(
        start_date__lte=end_date,
        end_date__gte=start_date
    )

    if booking_status:
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

    # Filter out apartments that are not available during the selected period
    availability_query = Q(start_date__lte=end_date) & (Q(end_date__isnull=True) | Q(end_date__gte=start_date))
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
        }

        for apartment in apartments:
            apartment_data = {
                'id': apartment.id,
                'name': apartment.name,
                'apartment_type': apartment.apartment_type,
                'days': {},
                'booked_days': 0,
            }

            bookings = [b for b in apartment.all_relevant_bookings 
                if b.start_date <= month_end and b.end_date >= month_start]
    
            apartment_payments = [p for p in apartment.all_relevant_apartment_payments 
                          if month_start <= p.payment_date <= month_end]

            # Fill in the days data
            for day in range(1, days_in_month + 1):
                date = current_month.replace(day=day)
                booking = next((b for b in bookings if b.start_date <= date <= b.end_date), None)
                if booking:
                    apartment_data['days'][day] = booking.status
                    if booking.status in ['Confirmed', 'Blocked']:
                        apartment_data['booked_days'] += 1
                        if booking.status == 'Confirmed':
                            month_data['month_occupancy'] += 1

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
        total_days = len(apartments) * days_in_month
        month_data['month_occupancy'] = (month_data['month_occupancy'] / total_days) * 100 if total_days > 0 else 0

        monthly_data.append(month_data)
        current_month += relativedelta(months=1)

    context = {
        'monthly_data': monthly_data,
        'apartments': Apartment.objects.values_list('name', flat=True).distinct(),
        'apartment_types': Apartment.TYPES,
        'current_apartment': current_apartment,
        'current_apartment_type': current_apartment_type,
        'current_booking_status': booking_status,
        'start_date': start_date.strftime('%B %d %Y'),
        'end_date': end_date.strftime('%B %d %Y'),
        'prev_page': page_offset - 12,
        'next_page': page_offset + 12,
        'current_year': start_date.year,
    }

    return render(request, 'booking_availability.html', context)
