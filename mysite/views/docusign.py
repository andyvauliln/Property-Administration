from django.utils import timezone
from django.http import JsonResponse
import json
from mysite.models import Booking
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import logging
import traceback

logger_common = logging.getLogger('mysite.common')


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
                    if 'visit_purpose' in form_fields_dict:
                        booking.visit_purpose = form_fields_dict['visit_purpose']
                        print_info(form_fields_dict['visit_purpose'], "visit_purpose_updated")
                    if 'car_info' in form_fields_dict:
                        booking.is_rent_car = True if form_fields_dict["car_info"] == "Rent" else False
                        print_info(form_fields_dict['car_info'], "car_info")
                    if 'car_model' in form_fields_dict:
                        booking.car_model = form_fields_dict["car_model"]
                        print_info(form_fields_dict['car_model'], "car_model")
                    booking.save()
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
                        tenant.phone = form_fields_dict['phone'].strip()
                        print_info(form_fields_dict['phone'], "phone")
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
