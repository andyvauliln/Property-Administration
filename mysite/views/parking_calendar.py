from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User, HandymanCalendar, ApartmentParking
import logging
from mysite.forms import BookingForm, ApartmentParkingForm  
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

def get_model_fields(form):
    """
    Convert form fields into a JSON-serializable format.
    Specifically designed for the parking calendar form fields.
    """
    serializable_fields = []
    
    for field_name, field_instance in form.fields.items():
        if isinstance(field_instance, CustomFieldMixin):
            field_data = {
                'name': field_name,
                'label': field_instance.label or field_name.replace('_', ' ').title(),
                'type': field_instance.__class__.__name__,
                'required': field_instance.required,
                'order': getattr(field_instance, 'order', 0),
                'ui_element': getattr(field_instance, 'ui_element', 'input'),
            }
            
            # Handle choices if the field has them
            if hasattr(field_instance, 'choices') and field_instance.choices:
                # Handle dropdown options
                dropdown_options = field_instance._dropdown_options
                if callable(dropdown_options):
                    field_data['choices'] = dropdown_options()
                else:
                    field_data['choices'] = dropdown_options

                # Set ui_element to 'select' if it has choices and no specific ui_element
                if 'ui_element' not in field_data:
                    field_data['ui_element'] = 'select'
            
            serializable_fields.append(field_data)
    
    # Sort fields by order
    serializable_fields.sort(key=lambda x: x['order'])
    return serializable_fields

def prepare_form_fields(request):
    """
    Prepare form fields data for the parking calendar.
    Returns JSON serializable form fields data.
    """
    form = ApartmentParkingForm(request=request)
    model_fields = get_model_fields(form)
    return json.dumps(model_fields, cls=DateEncoder)

def parking_calendar(request):
    status = request.GET.get('status',None)
    if status:
        parking_data = ApartmentParking.objects.filter(status=status)
    else:
        parking_data = ApartmentParking.objects.all()

    if request.method == 'POST':
        handle_post_request(request, ApartmentParking, ApartmentParkingForm)

    # Group parking data by apartment number
    grouped_parking = defaultdict(list)
    for parking in parking_data:
        # Get apartment building number with fallback
        building_n = getattr(parking.apartment, 'building_n', None)
        if building_n is None:
            continue  # Skip if no building number

        parking_dict = {
            'id': parking.id,
            'status': parking.status or None,
            'parking_number': parking.parking_number or None,
            'building_n': getattr(parking.apartment, 'name', None),
            'apartment_number': getattr(parking.apartment, 'apartment_n', None),
            'apartment_name': getattr(parking.apartment, 'name', None),
            'apartment': getattr(parking.apartment, 'id', None),
            'notes': getattr(parking, 'notes', None),
        }

        # Add booking-related information
        if hasattr(parking, 'booking') and parking.booking:
            parking_dict.update({
                'start_date': parking.booking.start_date if hasattr(parking.booking, 'start_date') else None,
                'booking_id': parking.booking.id if hasattr(parking.booking, 'id') else None,
                'end_date': parking.booking.end_date if hasattr(parking.booking, 'end_date') else None,
                'tenant_name': parking.booking.tenant.full_name if (hasattr(parking.booking, 'tenant') and 
                                                                  hasattr(parking.booking.tenant, 'full_name')) else None,
                'tenant_phone': parking.booking.tenant.phone if (hasattr(parking.booking, 'tenant') and 
                                                               hasattr(parking.booking.tenant, 'phone')) else None,
            })
        else:
            parking_dict.update({
                'start_date': None,
                'end_date': None,
                'tenant_name': None,
                'tenant_phone': None,
            })

        grouped_parking[building_n].append(parking_dict)

    # Convert the defaultdict to regular dict for JSON serialization
    grouped_parking_dict = dict(grouped_parking)
    parking_data_json = json.dumps(grouped_parking_dict, cls=DateEncoder)

    # Get form fields data
    form_fields = prepare_form_fields(request)

    context = {
        'parking_data': parking_data,
        'parking_data_json': parking_data_json,
        'title': "Parking Calendar",
        'endpoint': "/parking_calendar",
        'model_fields_json': form_fields,
    }

    return render(request, 'parking_calendar.html', context)