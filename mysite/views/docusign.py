from django.utils import timezone
from django.http import JsonResponse
import json
from mysite.models import Booking
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


@csrf_exempt
@require_http_methods(["POST", "GET"])
def docuseal_callback(request):
    print("YEEEEE!!!!!!!!!")
    if request.method == 'POST':
        data = json.loads(request.body).get('data', {})
        print(data, "data")
        bookingid = data.get("metadata", {}).get('booking_id', None)
        if bookingid:
            parsed_bookingid = int(bookingid[2:-1])
            booking = Booking.objects.get(id=parsed_bookingid)
            values = data.get('values', [])
            form_fields_dict = {item['field']: item.get('value', '') for item in values}
            print(form_fields_dict, "form_fields_dict")
            if booking and len(values) > 0:
                booking.status = 'Waiting Payment'
                if 'visit_purpose' in form_fields_dict:
                    booking.visit_purpose = form_fields_dict['visit_purpose']
                if 'car_info' in form_fields_dict:
                    booking.is_rent_car = True if form_fields_dict["car_info"] == "Rent" else False
                if 'car_model' in form_fields_dict:
                    booking.car_model = form_fields_dict["car_model"]
                booking.save()
                tenant = booking.tenant
                tenant.full_name = form_fields_dict['tenant'].strip()
                tenant.email = form_fields_dict['email'].strip()
                tenant.phone = form_fields_dict['phone'].strip()
                tenant.save()
                booking.save()
            return JsonResponse({'status': 'success', 'message': 'success'})
        
        return JsonResponse({'status': 'error', 'message': 'booking_id not found'}, status=400)

    elif request.method == 'GET':
        print("GET", request, request.json())
        return JsonResponse({'status': 'webhook endpoint'})
    return JsonResponse({'status': 'invalid method'}, status=405)
