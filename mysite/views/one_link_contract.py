
from django.shortcuts import redirect
from ..decorators import user_has_role
from mysite.models import Booking, Payment, Apartment, User
import os
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from django.db.models import Sum
from dateutil.relativedelta import relativedelta
import uuid
from datetime import datetime
from django.http import JsonResponse

#http://68.183.124.79/create-booking/?months=3&apartment=630-113&hold_deposit=100&damage_deposit=100&rent_payment=200&contract_type=1&start_date=2024-08-02&end_date=2024-08-09&phone=+15175681163&name=John%20Doe
def create_booking_by_link(request):
    try:
        apartment_name = request.GET.get('apartment', None)
        hold_deposit = request.GET.get('hold_deposit', None)
        damage_deposit = request.GET.get('damage_deposit', None)
        rent_payment = request.GET.get('rent_payment', None)
        contract_type = request.GET.get('contract_type', None)
        start_date = datetime.strptime(request.GET.get('start_date', None), '%Y-%m-%d').date() if request.GET.get('start_date', None) else None
        end_date = datetime.strptime(request.GET.get('end_date', None), '%Y-%m-%d').date() if request.GET.get('end_date', None) else None
        phone = request.GET.get('phone', None)
        name = request.GET.get('name', None)
        number_of_months = int(request.GET.get('months', 1))

        try:
            apartment = Apartment.objects.get(name=apartment_name)
        except Apartment.DoesNotExist:
            return JsonResponse({'error': f"Apartment with name '{apartment_name}' does not exist."}, status=400)
        

        # Check for existing tenant with the same phone number
        existing_tenant = User.objects.filter(phone=phone).first()
        tenant_email = existing_tenant.email if existing_tenant else f"tenant_{uuid.uuid4()}@example.com"

        # Existing overlapping bookings check...
        overlapping_bookings = Booking.objects.filter(
            apartment=apartment,
            start_date__lt=end_date,
            end_date__gt=start_date
        )
        if overlapping_bookings.exists():
            overlapping_booking = overlapping_bookings.first()
            return JsonResponse({'error': f"The apartment is already booked from {overlapping_booking.start_date} to {overlapping_booking.end_date}."}, status=400)

        booking = Booking(
            apartment=apartment,
            start_date=start_date,
            end_date=end_date,
            status="Waiting Contract",
            source="Other"
        )
        form_data = {
            'send_contract':  "120946" if contract_type == 1 else "118378",
            'tenant_email': tenant_email,
            'tenant_full_name': name,
            'tenant_phone': phone,
            'assigned_cleaner': 17,
        }
        payments_data = {
            'payment_dates': [start_date, timezone.now().date(), start_date],
            'amounts': [rent_payment, hold_deposit, damage_deposit],
            'payment_types': [6, 4, 3], #Rent, Hold, Damage
            'payment_notes': [],
            'payment_status': ["Pending", "Pending", "Pending"],
            'payment_id': [],
            'number_of_months': [number_of_months, 1, 1],
        }

        booking.save(form_data=form_data, payments_data=payments_data)
        booking_data = {
            'id': booking.id,
            'apartment': booking.apartment.name,
            'start_date': booking.start_date,
            'end_date': booking.end_date,
            'status': booking.status,
            'source': booking.source,
            'tenant_email': form_data['tenant_email'],
            'tenant_full_name': form_data['tenant_full_name'],
            'tenant_phone': form_data['tenant_phone'],
        }
        return JsonResponse({'booking': booking_data}, status=201)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

