from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User, HandymanCalendar
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
from mysite.forms import HandymanCalendarForm
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods


def handyman_calendar(request):

    page = request.GET.get('page', 1)

    if request.method == 'POST':
        handle_post_request(request, HandymanCalendar, HandymanCalendarForm)

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

    handyman_bookings = HandymanCalendar.objects.filter(
        date__gte=start_month,
        date__lte=end_date
    ).order_by('date', 'start_time')

    # Create a defaultdict to store events by date
    event_data = defaultdict(list)
    
    # Create JSON-serializable booking data grouped by date
    bookings_by_date = defaultdict(list)
    
    for booking in handyman_bookings:
        event_data[booking.date].append(booking)
        
        # Debug print the date format
        print("Original date:", booking.date)
        print("Date isoformat:", booking.date.isoformat())
        
        # Make sure we're using YYYY-MM-DD format for the date key
        date_key = booking.date.strftime('%Y-%m-%d')
        
        # Add booking data to JSON structure
        bookings_by_date[date_key].append({
            'id': booking.id,
            'tenant_name': booking.tenant_name,
            'apartment_name': booking.apartment_name,
            'start_time': booking.start_time.strftime('%H:%M'),
            'end_time': booking.end_time.strftime('%H:%M'),
            'notes': booking.notes,
            'tenant_phone': booking.tenant_phone,
            'date': date_key  # Use the same formatted date
        })

    # Convert defaultdict to regular dict for JSON serialization
    bookings_json = json.dumps(dict(bookings_by_date), default=str)
    print("Final bookings_json:", bookings_json)  # Debug the final JSON

    calendar_data = {}
    
    for month in months:
        calendar_data[month] = []
        weeks = generate_weeks(month)
        for week in weeks:
            week_data = []
            for day in week:
                # Convert day to date object if it isn't already
                day_date = day if isinstance(day, date) else date(day.year, day.month, day.day)
                bookings_for_day = event_data.get(day_date, [])
                
                day_data = {
                    'day': day,
                    'events': [{
                        'id': booking.id,
                        'tenant_name': booking.tenant_name,
                        'apartment_name': booking.apartment_name,
                        'start_time': booking.start_time,
                        'end_time': booking.end_time,
                        'notes': booking.notes,
                        'phone': booking.tenant_phone
                    } for booking in bookings_for_day]
                }
                week_data.append(day_data)
            calendar_data[month].append(week_data)

    model_fields = get_model_fields(HandymanCalendarForm(request=request))
    context = {
        'calendar_data': calendar_data,
        'current_date': timezone.now(),
        'months': months,
        'model_fields': model_fields,
        'prev_page': prev_page,
        'next_page': next_page,
        'bookings': handyman_bookings,
        'bookings_json': bookings_json,
        'title': "Handyman Calendar",
        'endpoint': "/handyman_calendar",
    }

    return render(request, 'handyman_calendar.html', context)
