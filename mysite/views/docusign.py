from django.utils import timezone
from django.http import JsonResponse
import json
from mysite.models import Booking
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import logging
import traceback

logger = logging.getLogger(__name__)




@csrf_exempt
@require_http_methods(["POST", "GET"])
def docuseal_callback(request):
    print("LETS PROCESS IT!")
    if request.method == 'POST':
        try:
            data = json.loads(request.body).get('data', {})
            logger.info(f"Request Data: {data}")
            bookingid = data.get("metadata", {}).get('booking_id', None)
            submission_id = data.get("submission_id", None)
            
            booking = None
            
            # Try to find booking by ID from metadata
            if bookingid:
                parsed_bookingid = int(bookingid[2:-1])
                logger.info(f"Booking ID Parsing: Parsed booking_id: {bookingid} -> {parsed_bookingid}")
                
                try:
                    booking = Booking.objects.get(id=parsed_bookingid)
                    logger.info(f"Booking Found: Found booking by ID: {booking}")
                except Booking.DoesNotExist:
                    logger.warning(f"Booking with ID {parsed_bookingid} not found, trying by contract_id...")
            
            # Fallback: Try to find booking by contract_id (submission_id)
            if not booking and submission_id:
                try:
                    booking = Booking.objects.get(contract_id=str(submission_id))
                    logger.info(f"Booking Found: Found booking by contract_id {submission_id}: {booking}")
                except Booking.DoesNotExist:
                    logger.warning(f"Booking with contract_id {submission_id} also not found")
            
            # If still not found, return error with debugging info
            if not booking:
                error_msg = f"Booking not found - ID: {bookingid if bookingid else 'N/A'}, Contract ID: {submission_id}"
                logger.error(error_msg)
                
                # Check nearby booking IDs for debugging
                if bookingid:
                    parsed_bookingid = int(bookingid[2:-1])
                    nearby_bookings = Booking.objects.filter(
                        id__gte=parsed_bookingid-5, 
                        id__lte=parsed_bookingid+5
                    ).values_list('id', 'contract_id')
                    logger.info(f"Nearby bookings info: {list(nearby_bookings)}")
                
                return JsonResponse({
                    'status': 'error', 
                    'message': error_msg
                }, status=404)
            
            # Process the booking if found
            logger.info(f"Booking: {booking}")
            values = data.get('values', [])
            form_fields_dict = {item['field']: item.get('value', '') for item in values}
            logger.info(f"form_fields_dict: {form_fields_dict}")
            
            if len(values) > 0:
                    booking.status = 'Waiting Payment'
                    
                    # Handle visit_purpose with default value if missing or empty
                    if 'visit_purpose' in form_fields_dict and form_fields_dict['visit_purpose']:
                        booking.visit_purpose = form_fields_dict['visit_purpose']
                        logger.info(f"visit_purpose_updated: {form_fields_dict['visit_purpose']}")
                    elif not booking.visit_purpose:  # Only set default if current value is empty/null
                        booking.visit_purpose = 'Other'  # Default value from VISIT_PURPOSE choices
                        logger.info("visit_purpose_set_to_default: Other")
                    
                    # Handle animals field with default value if missing or empty
                    if 'animals' in form_fields_dict and form_fields_dict['animals']:
                        booking.animals = form_fields_dict['animals']
                        logger.info(f"animals_updated: {form_fields_dict['animals']}")
                    elif not booking.animals:  # Only set default if current value is empty/null
                        booking.animals = ''  # Empty string is allowed for this field
                        logger.info("animals_set_to_empty")
                    
                    # Handle source field with default value if missing or empty  
                    if 'source' in form_fields_dict and form_fields_dict['source']:
                        booking.source = form_fields_dict['source']
                        logger.info(f"source_updated: {form_fields_dict['source']}")
                    elif not booking.source:  # Only set default if current value is empty/null
                        booking.source = 'Other'  # Default value from SOURCE choices
                        logger.info("source_set_to_default: Other")
                    
                    if 'car_info' in form_fields_dict:
                        booking.is_rent_car = True if form_fields_dict["car_info"] == "Rent" else False
                        logger.info(f"car_info: {form_fields_dict['car_info']}")
                    if 'car_model' in form_fields_dict:
                        # Ensure car_model is never None - use empty string as default
                        booking.car_model = form_fields_dict["car_model"] if form_fields_dict["car_model"] else ""
                        logger.info(f"car_model: {form_fields_dict['car_model']}")
                    booking.save()
                    logger.info(f"booking status: {booking.status}")
                    logger.info("Booking Saved")
                    tenant = booking.tenant
                    logger.info(f"tenant object before update: {tenant}")
                    
                    # Check if email has changed and if the new email already exists
                    new_email = form_fields_dict.get('email', '').strip() if 'email' in form_fields_dict and form_fields_dict['email'] else None
                    
                    if new_email and new_email != tenant.email:
                        # Check if a user with this email already exists
                        from mysite.models import User
                        try:
                            existing_user = User.objects.get(email=new_email)
                            # Email exists for a different user - switch the booking to that user
                            logger.info(f"info: Email {new_email} already exists for user {existing_user}. Switching booking tenant.")
                            booking.tenant = existing_user
                            tenant = existing_user
                        except User.DoesNotExist:
                            # Email doesn't exist - safe to update current tenant
                            logger.info(f"info: Email {new_email} is available. Updating current tenant.")
                    
                    # Update tenant information
                    if 'tenant' in form_fields_dict and form_fields_dict['tenant']:
                        tenant.full_name = form_fields_dict['tenant'].strip()
                        logger.info(f"tenant: {form_fields_dict['tenant']}")
                    if new_email and new_email != tenant.email:
                        tenant.email = new_email
                        logger.info(f"email: {new_email}")
                    if 'phone' in form_fields_dict and form_fields_dict['phone']:
                        # Clean phone number: take only the first phone if multiple are provided
                        raw_phone = form_fields_dict['phone'].strip()
                        # Split by common separators and take the first phone number
                        phone_cleaned = raw_phone.split('//')[0].split(',')[0].strip()
                        # Set phone - validation happens in User.save()
                        tenant.phone = phone_cleaned
                        logger.info(f"phone: {raw_phone} -> {tenant.phone}")
                    tenant.save()
                    logger.info("TENANT Saved")
                    # Save booking in case tenant was changed
                    booking.save()
                    logger.info("SUCCESSFULLY UPDATED")
            
            return JsonResponse({'status': 'success', 'message': 'success'})
            
        except Exception as e:
            logger.error(f"error: Error processing request: {e}")
            logger.error(f"traceback: {traceback.format_exc()}")
            return JsonResponse({'status': 'error', 'message': 'An error occurred'}, status=500)

    elif request.method == 'GET':
        print("GET", request, request.json())
        return JsonResponse({'status': 'webhook endpoint'})
    return JsonResponse({'status': 'invalid method'}, status=405)
