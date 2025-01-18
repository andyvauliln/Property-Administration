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
from .utils import generate_weeks, DateEncoder, get_model_fields
from mysite.forms import HandymanCalendarForm
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.shortcuts import redirect
from django.db.models import Q
from django.contrib import messages

def handle_post_request(request, model, form_class):
    try:
        if 'edit' in request.POST:
            item_id = request.POST['id']
            if item_id:
                instance = model.objects.get(id=item_id)
                form = form_class(request.POST, instance=instance,
                                  request=request, action='edit')
                if form.is_valid():
                    saved_instance = form.save()
                    return JsonResponse({'id': saved_instance.id, 'success': True})
                else:
                    return JsonResponse({'error': form.errors}, status=400)
        elif 'add' in request.POST: 
            form = form_class(request.POST, request=request)
            if form.is_valid():
                saved_instance = form.save()
                return JsonResponse({'id': saved_instance.id, 'success': True})
            else:
                return JsonResponse({'error': form.errors}, status=400)
        elif 'delete' in request.POST:
            instance = model.objects.get(id=request.POST['id'])
            instance.delete()
            return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def handyman_calendar(request):
    page = request.GET.get('page', 1)

    if request.method == 'POST':
        # For anonymous users, we'll only store the booking ID in the response
        response = handle_post_request(request, HandymanCalendar, HandymanCalendarForm)
        if isinstance(response, JsonResponse) and response.status_code == 200:
            data = json.loads(response.content)
            if 'id' in data:
                return JsonResponse({'id': data['id'], 'success': True})
        return response

    try:
        page = int(page)
    except ValueError:
        page = 1

    prev_page = page - 1
    next_page = page + 1
    start_month = (date.today() + relativedelta(months=9 * (page - 1))).replace(day=1)
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
        
        # Make sure we're using YYYY-MM-DD format for the date key
        date_key = booking.date.strftime('%Y-%m-%d')
        
        # For other users' bookings, only include minimal information
        booking_data = {
            'id': booking.id,
            'start_time': booking.start_time.strftime('%H:%M'),
            'end_time': booking.end_time.strftime('%H:%M'),
            'date': date_key,
            'tenant_name': booking.tenant_name,
            'tenant_phone': booking.tenant_phone,
            'notes': booking.notes,
            'apartment_name': booking.apartment_name,
        }
        
        
        bookings_by_date[date_key].append(booking_data)

    # Convert defaultdict to regular dict for JSON serialization
    bookings_json = json.dumps(dict(bookings_by_date), default=str)
    apartments = Apartment.objects.filter(
            status='Available'
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=date.today())
        )
    apartments_list = list(apartments.values_list('name', flat=True))
    apartments_json = json.dumps(apartments_list)
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
                        'start_time': booking.start_time,
                        'end_time': booking.end_time,
                        'apartment_name': booking.apartment_name,
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
        'apartments_json': apartments_json, 
        'today': date.today(),
    }

    return render(request, 'handyman_calendar.html', context)
