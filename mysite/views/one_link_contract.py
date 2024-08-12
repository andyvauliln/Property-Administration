
from django.shortcuts import redirect
from ..decorators import user_has_role
from mysite.models import Booking, Payment, Apartment
import os
from django.utils import timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from django.db.models import Sum
from dateutil.relativedelta import relativedelta
import uuid
from datetime import datetime

# link: https://property-managment/?
# apartment=630-18&
# hold_deposit=100&
# damage_deposit=100&
# rent_payment=200&
# contract_type=1&
# start_date=2024-05-01&
# end_date=2024-05-05&
# phone=1234567890
# name=John Doe
# link: https://property-managment/?apartment=630-18&hold_deposit=100&damage_deposit=100&rent_payment=200&contract_type=1&start_date=2024-05-01&end_date=2024-05-05&phone=1234567890&name=John Doe
from django.http import JsonResponse

def create_booking_by_link(request):
    try:
        apartment = request.GET.get('apartment', None)
        hold_deposit = request.GET.get('hold_deposit', None)
        damage_deposit = request.GET.get('damage_deposit', None)
        rent_payment = request.GET.get('rent_payment', None)
        contract_type = request.GET.get('contract_type', None)
        start_date = datetime.strptime(request.GET.get('start_date', None), '%Y-%m-%d').date() if request.GET.get('start_date', None) else None
        end_date = datetime.strptime(request.GET.get('end_date', None), '%Y-%m-%d').date() if request.GET.get('end_date', None) else None
        phone = request.GET.get('phone', None)
        name = request.GET.get('name', None)
        apartment = Apartment.objects.get(name=apartment)

        booking = Booking(
            apartment=apartment,
            start_date=start_date,
            end_date=end_date,
            status="Waiting Contract",
            source="Other"
        )
        form_data = {
            'send_contract':  "120946" if contract_type == 1 else "118378",
            'tenant_email': f"tenant_{uuid.uuid4()}@example.com",
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
            'number_of_months': [1, 1, 1],
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

