from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User, HandymanCalendar, HandymanBlockedSlot
import logging
from mysite.forms import BookingForm, HandymanBlockedSlotForm
from django.db.models import Q
import json
from datetime import date, timedelta, datetime
from collections import defaultdict
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import generate_weeks, DateEncoder, get_model_fields, send_handyman_telegram_notification
from mysite.forms import HandymanCalendarForm
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.shortcuts import redirect
from django.db.models import Q
from django.contrib import messages

def handle_post_request(request, model, form_class):
    try:
        # Print detailed POST data for debugging
        print("RAW POST DATA:", dict(request.POST.items()))
        
        if 'edit' in request.POST:
            item_id = request.POST['id']
            if item_id:
                instance = model.objects.get(id=item_id)
                form = form_class(request.POST, instance=instance, action='edit')
                if form.is_valid():
                    # Don't change the created_by field on edits
                    saved_instance = form.save()
                    send_handyman_telegram_notification(saved_instance, 'edited')
                    return JsonResponse({'id': saved_instance.id, 'success': True})
                else:
                    # Print detailed form validation errors for debugging
                    print("EDIT FORM VALIDATION ERRORS:", form.errors.as_json())
                    # Return specific form errors
                    errors = {}
                    for field, error_list in form.errors.items():
                        errors[field] = [str(error) for error in error_list]
                    # Add a specific message if errors are empty but form is invalid
                    if not errors:
                        errors['form'] = ['Form validation failed. Please check all fields.']
                    return JsonResponse({'error': errors}, status=400)
        elif 'add' in request.POST: 
            print("PROCESSING ADD REQUEST")
            
            # Explicitly handle time format conversion
            try:
                # Extract and format the times properly
                start_time = request.POST.get('start_time', '')
                end_time = request.POST.get('end_time', '')
                
                # Form data with proper time format
                form_data = {
                    'tenant_name': request.POST.get('tenant_name', ''),
                    'tenant_phone': request.POST.get('tenant_phone', ''),
                    'apartment_name': request.POST.get('apartment_name', ''),
                    'date': request.POST.get('date', ''),
                    'start_time': start_time,  
                    'end_time': end_time,
                    'notes': request.POST.get('notes', '')
                }
                
                print("EXTRACTED FORM DATA:", form_data)
                
                form = form_class(form_data)
                if form.is_valid():
                    print("FORM IS VALID")
                    instance = form.save(commit=False)
                    # Store the user identifier in created_by
                    instance.created_by = request.GET.get('user', 'anonymous')
                    instance.save()
                    send_handyman_telegram_notification(instance, 'created')
                    return JsonResponse({'id': instance.id, 'success': True, 'created_by': instance.created_by})
                else:
                    # Print detailed form validation errors for debugging
                    print("ADD FORM VALIDATION ERRORS:", form.errors.as_json())
                    print("FORM DATA:", form.data)
                    
                    # Return specific form errors
                    errors = {}
                    for field, error_list in form.errors.items():
                        errors[field] = [str(error) for error in error_list]
                    # Add a specific message if errors are empty but form is invalid
                    if not errors:
                        errors['form'] = ['Form validation failed. Please check all fields.']
                    return JsonResponse({'error': errors}, status=400)
            except Exception as e:
                print(f"TIME FORMAT ERROR: {str(e)}")
                return JsonResponse({
                    'error': {'server': [f"Error processing time format: {str(e)}"]}
                }, status=400)
        elif 'delete' in request.POST:
            instance = model.objects.get(id=request.POST['id'])
            # Only allow users to delete their own bookings or admin/manager to delete any
            if request.GET.get('user') in ['admin', 'manager'] or instance.created_by == request.GET.get('user', 'anonymous'):
                send_handyman_telegram_notification(instance, 'deleted')
                instance.delete()
                return JsonResponse({'success': True})
            else:
                return JsonResponse({'error': {'permission': ['You can only delete your own bookings']}}, status=403)
    except Exception as e:
        logging.exception("Error in handle_post_request")
        return JsonResponse({'error': {'server': [str(e)]}}, status=500)

def handle_block_request(request):
    """Handle manager requests to block or unblock time slots"""
    try:
        if 'block_slot' in request.POST:
            # Block a specific time slot
            date_str = request.POST.get('date')
            start_time = request.POST.get('start_time')
            end_time = request.POST.get('end_time')
            reason = request.POST.get('reason', 'Blocked by manager')
            
            # Parse date
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Parse times
            start_time_obj = datetime.strptime(start_time, '%H:%M').time()
            end_time_obj = datetime.strptime(end_time, '%H:%M').time()
            
            # Check if slot already exists
            existing_slot = HandymanBlockedSlot.objects.filter(
                date=date_obj,
                start_time=start_time_obj,
                end_time=end_time_obj
            ).first()
            
            if existing_slot:
                return JsonResponse({'error': 'This slot is already blocked'}, status=400)
            
            # Create a new blocked slot
            blocked_slot = HandymanBlockedSlot(
                date=date_obj,
                start_time=start_time_obj,
                end_time=end_time_obj,
                is_full_day=False,
                reason=reason
            )
            blocked_slot.save()
            
            return JsonResponse({'id': blocked_slot.id, 'success': True})
            
        elif 'unblock_slot' in request.POST:
            # Unblock a specific time slot
            slot_id = request.POST.get('id')
            if slot_id:
                slot = HandymanBlockedSlot.objects.get(id=slot_id)
                slot.delete()
                return JsonResponse({'success': True})
            else:
                # Try to find and delete by date and time
                date_str = request.POST.get('date')
                start_time = request.POST.get('start_time')
                end_time = request.POST.get('end_time')
                
                # Parse date
                date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
                
                # Parse times
                start_time_obj = datetime.strptime(start_time, '%H:%M').time()
                end_time_obj = datetime.strptime(end_time, '%H:%M').time()
                
                slot = HandymanBlockedSlot.objects.filter(
                    date=date_obj,
                    start_time=start_time_obj,
                    end_time=end_time_obj
                ).first()
                
                if slot:
                    slot.delete()
                    return JsonResponse({'success': True})
                else:
                    return JsonResponse({'error': 'Slot not found'}, status=404)
                
        elif 'block_day' in request.POST:
            # Block an entire day
            date_str = request.POST.get('date')
            reason = request.POST.get('reason', 'Entire day blocked by manager')
            
            # Parse date
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Check if day is already blocked
            existing_day = HandymanBlockedSlot.objects.filter(
                date=date_obj,
                is_full_day=True
            ).first()
            
            if existing_day:
                return JsonResponse({'error': 'This day is already blocked'}, status=400)
            
            # Create a new blocked day
            blocked_day = HandymanBlockedSlot(
                date=date_obj,
                is_full_day=True,
                reason=reason
            )
            blocked_day.save()
            
            return JsonResponse({'id': blocked_day.id, 'success': True})
            
        elif 'unblock_day' in request.POST:
            # Unblock an entire day
            date_str = request.POST.get('date')
            
            # Parse date
            date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Delete all blocked slots for this day
            HandymanBlockedSlot.objects.filter(date=date_obj).delete()
            
            return JsonResponse({'success': True})
            
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def handyman_calendar(request):
    page = request.GET.get('page', 1)
    is_manager = request.GET.get('user') == 'manager'

    if request.method == 'POST':
        # Debug the POST data
        print(f"DEBUG - POST data: {request.POST}")
        
        # Handle manager requests to block/unblock slots
        if is_manager and any(x in request.POST for x in ['block_slot', 'unblock_slot', 'block_day', 'unblock_day']):
            return handle_block_request(request)
        
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
    months = [start_month + relativedelta(months=i) for i in range(2)]
    end_date = months[-1] + relativedelta(months=1) - timedelta(days=1)

    handyman_bookings = HandymanCalendar.objects.filter(
        date__gte=start_month,
        date__lte=end_date
    ).order_by('date', 'start_time')
    
    # Get blocked slots
    blocked_slots = HandymanBlockedSlot.objects.filter(
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
            'created_by': booking.created_by,
        }
        
        
        bookings_by_date[date_key].append(booking_data)
    
    # Create JSON-serializable blocked slot data grouped by date
    blocked_slots_by_date = defaultdict(list)
    
    for slot in blocked_slots:
        date_key = slot.date.strftime('%Y-%m-%d')
        
        slot_data = {
            'id': slot.id,
            'is_full_day': slot.is_full_day,
            'reason': slot.reason,
            'created_by': slot.created_by
        }
        
        if not slot.is_full_day and slot.start_time and slot.end_time:
            slot_data['start_time'] = slot.start_time.strftime('%H:%M')
            slot_data['end_time'] = slot.end_time.strftime('%H:%M')
            slot_data['time_slot'] = f"{slot_data['start_time']} - {slot_data['end_time']}"
        
        blocked_slots_by_date[date_key].append(slot_data)

    # Convert defaultdicts to regular dicts for JSON serialization
    bookings_json = json.dumps(dict(bookings_by_date), default=str)
    blocked_slots_json = json.dumps(dict(blocked_slots_by_date), default=str)
    
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
                
                # Check if day has blocked slots
                date_key = day_date.strftime('%Y-%m-%d')
                has_blocked_slots = date_key in blocked_slots_by_date
                
                # Check if entire day is blocked - either by a full-day flag or if all slots are blocked
                is_fully_blocked = False
                if has_blocked_slots:
                    # Either there's a full day block OR all time slots are blocked
                    day_slots = blocked_slots_by_date.get(date_key, [])
                    is_fully_blocked = any(slot.get('is_full_day', False) for slot in day_slots)
                    
                    # If not fully blocked by flag, check if all time slots are blocked
                    if not is_fully_blocked:
                        # Generate all time slots for a day
                        all_time_slots = [f"{hour:02d}:{minute:02d} - {hour:02d}:{minute+30:02d}" if minute == 0 
                                        else f"{hour:02d}:{minute:02d} - {hour+1:02d}:00" 
                                        for hour in range(9, 17) for minute in (0, 30)]
                        
                        # Get all blocked time slots for this day
                        blocked_time_slots = [slot.get('time_slot') for slot in day_slots if slot.get('time_slot')]
                        
                        # Check if all time slots are blocked
                        is_fully_blocked = len(blocked_time_slots) >= 16  # Total number of 30-min slots from 9am-5pm
                
                day_data = {
                    'day': day,
                    'has_blocked_slots': has_blocked_slots,
                    'is_fully_blocked': is_fully_blocked,
                    'events': [{
                        'id': booking.id,
                        'start_time': booking.start_time,
                        'end_time': booking.end_time,
                        'apartment_name': booking.apartment_name,
                    } for booking in bookings_for_day]
                }
                week_data.append(day_data)
            calendar_data[month].append(week_data)

    model_fields = get_model_fields(HandymanCalendarForm())
    context = {
        'calendar_data': calendar_data,
        'current_date': timezone.now(),
        'months': months,
        'model_fields': model_fields,
        'prev_page': prev_page,
        'next_page': next_page,
        'bookings': handyman_bookings,
        'bookings_json': bookings_json,
        'blocked_slots_json': blocked_slots_json,
        'title': "Handyman Calendar",
        'endpoint': "/handyman_calendar",
        'apartments_json': apartments_json, 
        'today': date.today(),
        'is_manager': is_manager,
    }

    return render(request, 'handyman_calendar.html', context)
