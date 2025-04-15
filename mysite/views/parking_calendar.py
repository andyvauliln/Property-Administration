from calendar import monthrange
from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User, HandymanCalendar, ParkingBooking, Parking
import logging
from mysite.forms import BookingForm, ParkingBookingForm, ParkingForm
from django.db.models import Q
import json
from datetime import date, timedelta
from collections import defaultdict
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import generate_weeks, DateEncoder, handle_post_request
from mysite.forms import HandymanCalendarForm
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from mysite.forms import CustomFieldMixin



def parking_calendar(request):
    status = request.GET.get('status', None)
    if status:
        parking_bookings = ParkingBooking.objects.filter(status=status)
    else:
        parking_bookings = ParkingBooking.objects.all()

    parkings = Parking.objects.all().order_by('building', 'number')

    if request.method == 'POST':
        if 'add_booking' in request.POST or 'edit_booking' in request.POST or 'delete_booking' in request.POST:
            handle_post_request(request, ParkingBooking, ParkingBookingForm)
        else:
            handle_post_request(request, Parking, ParkingForm)
    
    current_date = timezone.now().date()
    page_offset = int(request.GET.get('page', 0))
    start_date = (current_date.replace(day=1) + relativedelta(months=page_offset * 3)).replace(day=1)
    end_date = start_date + relativedelta(months=3, days=-1)

    parking_bookings = parking_bookings.filter(start_date__lte=end_date, end_date__gte=start_date)

    monthly_data = []
    current_month = start_date
    while current_month <= end_date:
        days_in_month = monthrange(current_month.year, current_month.month)[1]
        month_end = current_month.replace(day=days_in_month)
        month_start = current_month

        month_data = {
            'month_name': current_month.strftime('%B %Y'),
            'parkings': [],
            'days_in_month': days_in_month,
            'month_occupancy': 0,
            'total_bookings': 0,
            'total_spots': len(parkings) * days_in_month
        }

        total_booked_days = 0
        for parking in parkings:
            parking_data = {
                'id': parking.id,
                'number': parking.number,
                'building': parking.building,
                'parking_room': next((booking.apartment.apartment_n for booking in ParkingBooking.objects.filter(
                    Q(end_date__gt=timezone.now().date()),
                    parking=parking,
                    status='Booked'
                ).select_related('apartment')), None),
                'days': {},
            }
            bookings_for_parking = parking_bookings.filter(parking=parking)
            for day in range(1, days_in_month + 1):
                date_obj = current_month.replace(day=day)
                parking_data['days'][day] = {
                    'status': 'Available',
                    'is_start': False,
                    'is_end': False,
                    'tenant_name': "", 
                    'day': date_obj.strftime('%Y-%m-%d'),  # Changed to YYYY-MM-DD format for input[type=date]
                    'notes': "",
                }
                
                # Check for bookings on this day
                for p_booking in bookings_for_parking:
                    if p_booking.start_date <= date_obj <= p_booking.end_date:
                        # This day is within a booking period
                        parking_data['days'][day].update({
                            'status': p_booking.status,
                            'is_start': date_obj == p_booking.start_date,
                            'is_end': date_obj == p_booking.end_date,
                            'tenant_name': p_booking.booking.tenant.full_name if p_booking.booking and p_booking.booking.tenant else "",
                            'notes': p_booking.notes or "",
                            'id': p_booking.id,
                            'apartment_id': p_booking.apartment.id if p_booking.apartment else "",
                            'booking_id': p_booking.booking.id if p_booking.booking else "",
                            'apartment_name': p_booking.apartment.name if p_booking.apartment else "",
                            'status': p_booking.status,
                            'start_date': p_booking.start_date.strftime('%Y-%m-%d'),
                            'end_date': p_booking.end_date.strftime('%Y-%m-%d')
                        })
                        if p_booking.status == 'Booked':
                            total_booked_days += 1
                        break

            month_data['parkings'].append(parking_data)

        # Calculate occupancy percentage
        if month_data['total_spots'] > 0:
            month_data['month_occupancy'] = round((total_booked_days / month_data['total_spots']) * 100)
        month_data['total_bookings'] = total_booked_days

        monthly_data.append(month_data)
        current_month += relativedelta(months=1)
    
    # Get apartments and bookings for dropdowns
    apartments = Apartment.objects.filter(
        Q(end_date__gt=timezone.now().date()) | Q(end_date__isnull=True)
    ).values('id', 'name').order_by('name')

    bookings = Booking.objects.filter(
        Q(end_date__gt=timezone.now().date())
    ).select_related('tenant', 'apartment').values(
        'id', 
        'start_date', 
        'end_date',
        'apartment_id',
        'apartment__name',
        'tenant__full_name'
    ).order_by('-start_date')

    # Convert dates to string format for JSON
    for booking in bookings:
        booking['start_date'] = booking['start_date'].strftime('%Y-%m-%d')
        booking['end_date'] = booking['end_date'].strftime('%Y-%m-%d')

    # Convert data to JSON for template
    parking_data_json = json.dumps(monthly_data, cls=DateEncoder)
    buildings = [{'building': b['building_n']} for b in Apartment.objects.filter(building_n__isnull=False).filter(Q(end_date__gt=timezone.now().date()) | Q(end_date__isnull=True)).values('building_n').distinct()]
    buildings_json = json.dumps(buildings)
    apartments_json = json.dumps(list(apartments))
    bookings_json = json.dumps(list(bookings))

    context = {
        'monthly_data': monthly_data,
        'buildings_json': buildings_json,
        'parking_data_json': parking_data_json,
        'apartments_json': apartments_json,
        'bookings_json': bookings_json,
        'title': "Parking Calendar",
        'endpoint': "/parking_calendar",
        'prev_page': page_offset - 1,
        'next_page': page_offset + 1,
    }

    return render(request, 'parking_calendar.html', context)