from django.utils import timezone
from django.http import JsonResponse
import json
from mysite.models import Booking
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import logging
import traceback

logger_common = logging.getLogger('mysite.docuseal')


def print_info(message, type="info"):
    print(message)
    logger_common.debug(f"\n{type}:\n{message}\n")


@csrf_exempt
@require_http_methods(["POST", "GET"])
def docuseal_callback(request):
    print("LETS PROCESS IT!")
    if request.method == 'POST':
        try:
            data = json.loads(request.body).get('data', {})
            print_info(data, "Request Data")
            bookingid = data.get("metadata", {}).get('booking_id', None)
            if bookingid:
                parsed_bookingid = int(bookingid[2:-1])
                booking = Booking.objects.get(id=parsed_bookingid)
                print_info(booking, "Booking")
                values = data.get('values', [])
                form_fields_dict = {item['field']: item.get('value', '') for item in values}
                print_info(form_fields_dict, "form_fields_dict")
                if booking and len(values) > 0:
                    booking.status = 'Waiting Payment'
                    
                    # Handle visit_purpose with default value if missing or empty
                    if 'visit_purpose' in form_fields_dict and form_fields_dict['visit_purpose']:
                        booking.visit_purpose = form_fields_dict['visit_purpose']
                        print_info(form_fields_dict['visit_purpose'], "visit_purpose_updated")
                    elif not booking.visit_purpose:  # Only set default if current value is empty/null
                        booking.visit_purpose = 'Other'  # Default value from VISIT_PURPOSE choices
                        print_info('Other', "visit_purpose_set_to_default")
                    
                    # Handle animals field with default value if missing or empty
                    if 'animals' in form_fields_dict and form_fields_dict['animals']:
                        booking.animals = form_fields_dict['animals']
                        print_info(form_fields_dict['animals'], "animals_updated")
                    elif not booking.animals:  # Only set default if current value is empty/null
                        booking.animals = ''  # Empty string is allowed for this field
                        print_info('', "animals_set_to_empty")
                    
                    # Handle source field with default value if missing or empty  
                    if 'source' in form_fields_dict and form_fields_dict['source']:
                        booking.source = form_fields_dict['source']
                        print_info(form_fields_dict['source'], "source_updated")
                    elif not booking.source:  # Only set default if current value is empty/null
                        booking.source = 'Other'  # Default value from SOURCE choices
                        print_info('Other', "source_set_to_default")
                    
                    if 'car_info' in form_fields_dict:
                        booking.is_rent_car = True if form_fields_dict["car_info"] == "Rent" else False
                        print_info(form_fields_dict['car_info'], "car_info")
                    if 'car_model' in form_fields_dict:
                        # Ensure car_model is never None - use empty string as default
                        booking.car_model = form_fields_dict["car_model"] if form_fields_dict["car_model"] else ""
                        print_info(form_fields_dict['car_model'], "car_model")
                    booking.save()
                    print_info(booking.status, "booking status")
                    print_info("Booking Saved")
                    tenant = booking.tenant
                    print_info(tenant, "tenant object before update")
                    if 'tenant' in form_fields_dict and form_fields_dict['tenant']:
                        tenant.full_name = form_fields_dict['tenant'].strip()
                        print_info(form_fields_dict['tenant'], "tenant")
                    if 'email' in form_fields_dict and form_fields_dict['email']:
                        tenant.email = form_fields_dict['email'].strip()
                        print_info(form_fields_dict['email'], "email")
                    if 'phone' in form_fields_dict and form_fields_dict['phone']:
                        # Clean phone number: take only the first phone if multiple are provided
                        # and remove extra formatting to fit within 20 character limit
                        raw_phone = form_fields_dict['phone'].strip()
                        # Split by common separators and take the first phone number
                        phone_cleaned = raw_phone.split('//')[0].split(',')[0].strip()
                        # Truncate to 20 characters if still too long
                        tenant.phone = phone_cleaned[:20] if len(phone_cleaned) > 20 else phone_cleaned
                        print_info(f"phone: {raw_phone} -> {tenant.phone}", "phone")
                    tenant.save()
                    print_info("TENANT Saved")
                    booking.save()
                    print_info("SUCSSEFULY UPDATED")
                return JsonResponse({'status': 'success', 'message': 'success'})
        except Exception as e:
            print_info(f"Error processing request: {e}", "error")
            print_info(traceback.format_exc(), "traceback")
            return JsonResponse({'status': 'error', 'message': 'An error occurred'}, status=500)
        
        return JsonResponse({'status': 'error', 'message': 'booking_id not found'}, status=400)

    elif request.method == 'GET':
        print("GET", request, request.json())
        return JsonResponse({'status': 'webhook endpoint'})
    return JsonResponse({'status': 'invalid method'}, status=405)
