"""
Token-authenticated JSON APIs: apartment availability/pricing, Rental Guru bookings/payments.

Documentation and curl examples: ``BOOKING_API.md`` in this directory.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework import status
from rest_framework.parsers import JSONParser
from ..models import Apartment, Booking, ApartmentPrice, Payment
from ..forms import BookingForm, PaymentForm
from datetime import datetime, timedelta, date
from django.db.models import Q, Prefetch
from django.http import QueryDict
from django.contrib.auth.models import AnonymousUser
from types import SimpleNamespace
from contextlib import contextmanager
import copy
import json
import os
import sys

from ..request_context import get_current_user, set_current_user


RENTAL_GURU_SOURCE = 'Rental Guru'

# Thread-local user for apply_user_tracking (token APIs have no Django session user).
_RENTAL_GURU_TRACKING_USER = SimpleNamespace(
    is_authenticated=True,
    full_name=RENTAL_GURU_SOURCE,
    email='',
)


@contextmanager
def rental_guru_user_tracking_context():
    prev = get_current_user()
    set_current_user(_RENTAL_GURU_TRACKING_USER)
    try:
        yield
    finally:
        set_current_user(prev)


def _rental_guru_request_logging_enabled():
    v = os.environ.get('RENTAL_GURU_API_LOG_REQUESTS', '').strip().lower()
    return v in ('1', 'true', 'yes', 'on')


def _redact_auth_token(mapping):
    if not isinstance(mapping, dict):
        return mapping
    out = copy.deepcopy(mapping)
    if 'auth_token' in out:
        out['auth_token'] = '***'
    return out


def _log_rental_guru_request(request, json_body=None):
    """Print method, URL, query, and JSON body to stderr when RENTAL_GURU_API_LOG_REQUESTS is set."""
    if not _rental_guru_request_logging_enabled():
        return
    query = {}
    if request.GET:
        query = dict(request.GET.items())
        if 'auth_token' in query:
            query = {**query, 'auth_token': '***'}
    payload = {
        'method': request.method,
        'path': request.path,
        'full_path': request.get_full_path(),
    }
    if query:
        payload['query'] = query
    if json_body is not None:
        payload['body'] = _redact_auth_token(dict(json_body) if isinstance(json_body, dict) else json_body)
    print(json.dumps(payload, indent=2, default=str), file=sys.stderr, flush=True)


def _api_auth_error_response(request):
    auth_token = None
    if hasattr(request, 'data') and isinstance(request.data, dict):
        auth_token = request.data.get('auth_token')
    if auth_token is None:
        auth_token = request.GET.get('auth_token')
    expected_token = os.environ.get('API_AUTH_TOKEN')
    if not auth_token or auth_token != expected_token:
        return Response(
            {'error': 'Invalid or missing authentication token'},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return None


def _to_query_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        # Django ChoiceField / boolean widgets expect 'True'/'False', not 'true'/'false'.
        return 'True' if value else 'False'
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)


def _body_scalars(body):
    if not isinstance(body, dict):
        return {}
    return {
        k: v
        for k, v in body.items()
        if k not in ('auth_token', 'payments') and not isinstance(v, (list, dict))
    }


def _dict_to_querydict(data):
    qd = QueryDict(mutable=True)
    for key, value in data.items():
        if value is None:
            qd[key] = ''
        else:
            qd[key] = _to_query_value(value)
    return qd


def _fake_request(qd):
    return SimpleNamespace(POST=qd, user=AnonymousUser())


def _payments_parallel_from_rows(rows):
    if not rows:
        return {
            'payment_dates': [],
            'amounts': [],
            'payment_types': [],
            'payment_notes': [],
            'payment_status': [],
            'payment_id': [],
            'number_of_months': [],
        }
    payment_dates = []
    amounts = []
    payment_types = []
    payment_notes = []
    payment_status = []
    payment_id = []
    number_of_months = []
    for row in rows:
        payment_dates.append(row.get('payment_date') or '')
        raw_amount = row.get('amount')
        if raw_amount is None:
            raw_amount = 0
        amounts.append(abs(float(raw_amount)))
        pt = row.get('payment_type')
        payment_types.append('' if pt is None else str(pt))
        payment_notes.append(row.get('payment_notes') or row.get('notes') or '')
        payment_status.append(row.get('payment_status') or 'Pending')
        pid = row.get('payment_id')
        payment_id.append('' if pid is None else str(pid))
        nm = row.get('number_of_months', 1)
        if nm in (None, ''):
            nm = 1
        number_of_months.append(int(nm))
    return {
        'payment_dates': payment_dates,
        'amounts': amounts,
        'payment_types': payment_types,
        'payment_notes': payment_notes,
        'payment_status': payment_status,
        'payment_id': payment_id,
        'number_of_months': number_of_months,
    }


def _booking_as_form_dict(booking):
    d = {
        'apartment': str(booking.apartment_id) if booking.apartment_id else '',
        'start_date': booking.start_date.isoformat() if booking.start_date else '',
        'end_date': booking.end_date.isoformat() if booking.end_date else '',
        'status': booking.status or '',
        'notes': booking.notes or '',
        'keywords': booking.keywords or '',
        'other_tenants': booking.other_tenants or '',
        'tenants_n': str(booking.tenants_n) if booking.tenants_n is not None else '',
        'animals': booking.animals or '',
        'visit_purpose': booking.visit_purpose or '',
        'source': booking.source or '',
        'source_id': booking.source_id or '',
        'is_rent_car': (
            'true' if booking.is_rent_car is True else 'false' if booking.is_rent_car is False else ''
        ),
        'car_model': booking.car_model or '',
        'car_price': str(booking.car_price),
        'car_rent_days': str(booking.car_rent_days),
        'tenant': str(booking.tenant_id) if booking.tenant_id else '',
    }
    if booking.tenant:
        d['tenant_email'] = booking.tenant.email or ''
        d['tenant_full_name'] = booking.tenant.full_name or ''
        d['tenant_phone'] = booking.tenant.phone or ''
    else:
        d['tenant_email'] = ''
        d['tenant_full_name'] = ''
        d['tenant_phone'] = ''
    return d


def _overlay_form_dict(base, patch_scalars):
    merged = {**base}
    for key, value in patch_scalars.items():
        merged[key] = value
    return merged


def _normalize_booking_flat_for_form(flat):
    """Omit create_chat when falsey so ChoiceField (only True is valid) does not error."""
    if not isinstance(flat, dict):
        return flat
    cc = flat.get('create_chat')
    if cc in (False, 'false', 'False', 0, '0', ''):
        flat.pop('create_chat', None)
    return flat


def _payment_as_form_dict(payment):
    return {
        'payment_date': payment.payment_date.isoformat() if payment.payment_date else '',
        'amount': str(payment.amount),
        # Must be 0 on edit so Payment.save() calls super().save() (not create_payments).
        'number_of_months': '0',
        'payment_status': payment.payment_status or 'Pending',
        'payment_method': (
            str(payment.payment_method_id) if payment.payment_method_id is not None else ''
        ),
        'payment_type': str(payment.payment_type_id) if payment.payment_type_id else '',
        'bank': str(payment.bank_id) if payment.bank_id is not None else '',
        'notes': payment.notes or '',
        'tenant_notes': payment.tenant_notes or '',
        'keywords': payment.keywords or '',
        'booking': str(payment.booking_id) if payment.booking_id is not None else '',
        'apartment': str(payment.apartment_id) if payment.apartment_id is not None else '',
        'invoice_url': payment.invoice_url or '',
        'source': payment.source or '',
        'source_id': payment.source_id or '',
    }


def _serialize_payment_type(payment_type):
    if payment_type is None:
        return None
    return {
        'id': payment_type.id,
        'name': payment_type.name,
        'type': payment_type.type,
        'category': payment_type.category or '',
        'balance_sheet_name': payment_type.balance_sheet_name or '',
        'keywords': payment_type.keywords or '',
    }


def _serialize_payment_method_ref(pm):
    if pm is None:
        return None
    return {
        'id': pm.id,
        'name': pm.name,
        'type': pm.type,
        'keywords': pm.keywords or '',
        'notes': pm.notes or '',
    }


def _serialize_payment(payment):
    return {
        'id': payment.id,
        'booking_id': payment.booking_id,
        'apartment_id': payment.apartment_id,
        'invoice_url': payment.invoice_url or '',
        'payment_date': payment.payment_date.isoformat() if payment.payment_date else None,
        'amount': str(payment.amount),
        'payment_status': payment.payment_status,
        'payment_type_id': payment.payment_type_id,
        'payment_type': _serialize_payment_type(payment.payment_type),
        'payment_method_id': payment.payment_method_id,
        'payment_method': _serialize_payment_method_ref(payment.payment_method),
        'bank_id': payment.bank_id,
        'bank': _serialize_payment_method_ref(payment.bank),
        'notes': payment.notes or '',
        'tenant_notes': payment.tenant_notes or '',
        'keywords': payment.keywords or '',
        'merged_payment_key': payment.merged_payment_key or '',
        'source': payment.source,
        'source_id': payment.source_id,
        'created_by': payment.created_by,
        'last_updated_by': payment.last_updated_by,
        'created_at': payment.created_at.isoformat() if payment.created_at else None,
        'updated_at': payment.updated_at.isoformat() if payment.updated_at else None,
    }


def _booking_payments_queryset():
    return Payment.objects.select_related(
        'payment_type', 'payment_method', 'bank'
    ).order_by('payment_date', 'id')


def _load_booking_for_api(booking_pk):
    return Booking.objects.select_related('tenant').prefetch_related(
        Prefetch('payments', queryset=_booking_payments_queryset()),
    ).get(pk=booking_pk)


def _serialize_booking(booking):
    if hasattr(booking, '_prefetched_objects_cache') and 'payments' in booking._prefetched_objects_cache:
        payment_rows = list(booking.payments.all())
    else:
        payment_rows = list(_booking_payments_queryset().filter(booking_id=booking.pk))
    payments = [_serialize_payment(p) for p in payment_rows]
    tenant = booking.tenant
    return {
        'id': booking.id,
        'apartment_id': booking.apartment_id,
        'contract_url': booking.contract_url or '',
        'contract_id': booking.contract_id or '',
        'contract_send_status': booking.contract_send_status or '',
        'start_date': booking.start_date.isoformat() if booking.start_date else None,
        'end_date': booking.end_date.isoformat() if booking.end_date else None,
        'tenants_n': str(booking.tenants_n) if booking.tenants_n is not None else None,
        'status': booking.status,
        'animals': booking.animals or '',
        'visit_purpose': booking.visit_purpose or '',
        'source': booking.source or '',
        'source_id': booking.source_id or '',
        'notes': booking.notes or '',
        'other_tenants': booking.other_tenants or '',
        'keywords': booking.keywords or '',
        'tenant_id': booking.tenant_id,
        'tenant_email': tenant.email if tenant else None,
        'tenant_full_name': tenant.full_name if tenant else None,
        'tenant_phone': tenant.phone if tenant else None,
        'is_rent_car': booking.is_rent_car,
        'car_model': booking.car_model or '',
        'car_price': str(booking.car_price),
        'car_rent_days': booking.car_rent_days,
        'created_by': booking.created_by,
        'last_updated_by': booking.last_updated_by,
        'created_at': booking.created_at.isoformat() if booking.created_at else None,
        'updated_at': booking.updated_at.isoformat() if booking.updated_at else None,
        'payments': payments,
    }

class ApartmentBookingDates(APIView):
    renderer_classes = [JSONRenderer]  # This ensures JSON response
    
    def get(self, request):
        # Check for auth token
        auth_token = request.GET.get('auth_token')
        expected_token = os.environ.get('API_AUTH_TOKEN')
        
        if not auth_token or auth_token != expected_token:
            return Response(
                {"error": "Invalid or missing authentication token"}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        _log_rental_guru_request(request, None)
        
        today = date.today()
        
        # Get apartment_ids from query parameters
        apartment_ids = request.GET.get('apartment_ids', '')
        if apartment_ids:
            try:
                apartment_ids = [int(id_) for id_ in apartment_ids.split(',') if str(id_).strip()]
            except (TypeError, ValueError):
                return Response(
                    {"error": "apartment_ids must be a comma-separated list of integers"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            future_bookings_qs = Booking.objects.select_related('tenant').prefetch_related(
                Prefetch('payments', queryset=_booking_payments_queryset()),
            ).filter(end_date__gte=today).order_by('start_date')
            apartments = Apartment.objects.filter(
                id__in=apartment_ids,
                status='Available'
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today)
            ).prefetch_related(
                'prices',
                Prefetch('booked_apartments', queryset=future_bookings_qs),
            ).order_by('id')
        else:
            # giving all apartments
            apartments = Apartment.objects.filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today),
                status='Available',

            ).prefetch_related('prices').order_by('id')
            return Response({"apartments": []}, content_type='application/json')
        
        # Prepare response data
        response_data = {
            "apartments": []
        }

        def safe_float(value):
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def add_surcharge(base_price, surcharge):
            if base_price is None:
                return None
            return base_price + (surcharge or 0)
        
        # For each apartment, get its bookings and pricing
        for apartment in apartments:
            # Get current price
            current_price = apartment.current_price
            
            # Get the current active price (most recent price with effective_date <= today)
            current_active_price = apartment.prices.filter(
                effective_date__lte=today
            ).order_by('-effective_date').first()
            
            # Get all future prices (effective_date > today)
            future_prices = apartment.prices.filter(
                effective_date__gt=today
            ).order_by('effective_date')
            
            # Calculate rating surcharge (daily rate needs to be converted to monthly)
            rating_surcharge_per_day = apartment.get_rating_surcharge_per_day()
            rating_surcharge_per_month = (rating_surcharge_per_day or 0) * 30  # Convert daily to monthly
            
            # Build pricing data - include current period and future prices with rating surcharge applied
            pricing_data = []
            
            # Add current active price if it exists
            if current_active_price:
                base_price = safe_float(current_active_price.price)
                pricing_data.append({
                    "price": add_surcharge(base_price, rating_surcharge_per_month),
                    "base_price": base_price,
                    "effective_date": current_active_price.effective_date.strftime("%Y-%m-%d"),
                    # "default_price": float(apartment.default_price),
                    "notes": current_active_price.notes or ""
                })
            
            # Add future prices
            for price in future_prices:
                base_price = safe_float(price.price)
                pricing_data.append({
                    "price": add_surcharge(base_price, rating_surcharge_per_month),
                    "base_price": base_price,
                    "effective_date": price.effective_date.strftime("%Y-%m-%d"),
                    # "default_price": float(apartment.default_price),
                    "notes": price.notes or ""
                })
            
            # Sort pricing data by effective_date to ensure chronological order
            pricing_data.sort(key=lambda x: x["effective_date"])
            
            default_price_value = safe_float(apartment.default_price)
            if default_price_value is None:
                default_price_value = safe_float(
                    (current_active_price.price if current_active_price else None) or current_price
                )
            if default_price_value is None:
                default_price_value = 0

            apartment_data = {
                "apartment_id": apartment.id,
                # "current_price": float(current_price) if current_price else None,
                "default_price": default_price_value,
                "apartment_name": apartment.name,
                "rating": safe_float(apartment.raiting),
                "rating_surcharge_per_day": safe_float(rating_surcharge_per_day),
                "pricing_history": pricing_data,
                "bookings": []
            }
            
            for booking in apartment.booked_apartments.all():
                apartment_data["bookings"].append(_serialize_booking(booking))
            
            response_data["apartments"].append(apartment_data)
        
        return Response(response_data, content_type='application/json')


class UpdateSingleApartmentPrice(APIView):
    renderer_classes = [JSONRenderer]  # This ensures JSON response
    
    def post(self, request):
        # Check for auth token
        auth_token = request.data.get('auth_token') or request.GET.get('auth_token')
        expected_token = os.environ.get('API_AUTH_TOKEN')
        
        if not auth_token or auth_token != expected_token:
            return Response(
                {"error": "Invalid or missing authentication token"}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        _log_rental_guru_request(request, dict(request.data))
        
        # Get parameters
        apartment_id = request.data.get('apartment_id')
        new_price = request.data.get('new_price')
        effective_date = request.data.get('effective_date')
        notes = request.data.get('notes', '')
        
        # Validate parameters
        if apartment_id is None:
            return Response(
                {"error": "apartment_id parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if new_price is None:
            return Response(
                {"error": "new_price parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if effective_date is None:
            return Response(
                {"error": "effective_date parameter is required (format: YYYY-MM-DD)"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            apartment_id = int(apartment_id)
            new_price = float(new_price)
            effective_date = datetime.strptime(effective_date, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response(
                {"error": "apartment_id must be an integer, new_price must be a number, and effective_date must be in YYYY-MM-DD format"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Find the apartment
        try:
            apartment = Apartment.objects.get(id=apartment_id)
        except Apartment.DoesNotExist:
            return Response(
                {"error": f"Apartment with id {apartment_id} not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if a price already exists for this date
        existing_price = ApartmentPrice.objects.filter(
            apartment=apartment,
            effective_date=effective_date
        ).first()
        
        with rental_guru_user_tracking_context():
            if existing_price:
                existing_price.price = new_price
                existing_price.notes = notes
                existing_price.save()
                action = "updated"
                price_record = existing_price
            else:
                price_record = ApartmentPrice(
                    apartment=apartment,
                    price=new_price,
                    effective_date=effective_date,
                    notes=notes
                )
                price_record.save()
                action = "created"
        
        # Get all prices for this apartment since the effective date
        prices_since_date = apartment.prices.filter(
            effective_date__gte=effective_date
        ).order_by('effective_date')
        
        
        pricing_data = []
        for price in prices_since_date:
            pricing_data.append({
                "price": float(price.price),
                "effective_date": price.effective_date.strftime("%Y-%m-%d"),
                "notes": price.notes or ""
            })
        
        # Sort pricing data by effective_date to ensure chronological order
        pricing_data.sort(key=lambda x: x["effective_date"])
        
        response_data = {
            "message": f"Successfully {action} price for apartment {apartment.name}",
            "apartment_id": apartment.id,
            "apartment_name": apartment.name,
            "current_price": float(apartment.current_price) if apartment.current_price else None,
            "rating": float(apartment.raiting) if apartment.raiting else None,
            "new_price_record": {
                "base_price": float(price_record.price),
                "effective_date": price_record.effective_date.strftime("%Y-%m-%d"),
                "notes": price_record.notes,
                "action": action
            },
            "pricing_history": pricing_data
        }
        
        return Response(response_data, content_type='application/json')


class UpdateApartmentPriceByRooms(APIView):
    renderer_classes = [JSONRenderer]  # This ensures JSON response
    
    def post(self, request):
        # Check for auth token
        auth_token = request.data.get('auth_token') or request.GET.get('auth_token')
        expected_token = os.environ.get('API_AUTH_TOKEN')
        
        if not auth_token or auth_token != expected_token:
            return Response(
                {"error": "Invalid or missing authentication token"}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        _log_rental_guru_request(request, dict(request.data))
        
        # Get parameters
        number_of_rooms = request.data.get('number_of_rooms')
        new_price = request.data.get('new_price')
        effective_date = request.data.get('effective_date')  # New parameter for date
        notes = request.data.get('notes', '')  # Optional notes
        
        # Validate parameters
        if number_of_rooms is None:
            return Response(
                {"error": "number_of_rooms parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if new_price is None:
            return Response(
                {"error": "new_price parameter is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if effective_date is None:
            return Response(
                {"error": "effective_date parameter is required (format: YYYY-MM-DD)"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            number_of_rooms = int(number_of_rooms)
            new_price = float(new_price)
            effective_date = datetime.strptime(effective_date, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return Response(
                {"error": "number_of_rooms must be an integer, new_price must be a number, and effective_date must be in YYYY-MM-DD format"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Find apartments with the specified number of bedrooms
        apartments = Apartment.objects.filter(bedrooms=number_of_rooms)
        
        if not apartments.exists():
            return Response(
                {"error": f"No apartments found with {number_of_rooms} rooms"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create new price records for all matching apartments
        created_prices = []
        skipped_apartments = []
        updated_count = 0
        
        with rental_guru_user_tracking_context():
            for apartment in apartments:
                existing_price = ApartmentPrice.objects.filter(
                    apartment=apartment,
                    effective_date=effective_date
                ).first()
                
                if existing_price:
                    existing_price.price = new_price
                    existing_price.notes = notes
                    existing_price.save()
                    created_prices.append({
                        "apartment_id": apartment.id,
                        "apartment_name": apartment.name,
                        "price": float(new_price),
                        "effective_date": effective_date.strftime("%Y-%m-%d"),
                        "notes": notes,
                        "action": "updated"
                    })
                    updated_count += 1
                else:
                    apartment_price = ApartmentPrice(
                        apartment=apartment,
                        price=new_price,
                        effective_date=effective_date,
                        notes=notes
                    )
                    apartment_price.save()
                    created_prices.append({
                        "apartment_id": apartment.id,
                        "apartment_name": apartment.name,
                        "price": float(apartment_price.price),
                        "effective_date": apartment_price.effective_date.strftime("%Y-%m-%d"),
                        "notes": apartment_price.notes,
                        "action": "created"
                    })
                    updated_count += 1
        
        response_data = {
            "message": f"Successfully processed price updates for {updated_count} apartments with {number_of_rooms} rooms",
            "updated_count": updated_count,
            "number_of_rooms": number_of_rooms,
            "new_price": new_price,
            "effective_date": effective_date.strftime("%Y-%m-%d"),
            "notes": notes,
            "price_records": created_prices
        }
        
        return Response(response_data, content_type='application/json')


class RentalGuruCreateBookingAPI(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def post(self, request):
        err = _api_auth_error_response(request)
        if err:
            return err
        body = dict(request.data)
        _log_rental_guru_request(request, body)
        flat = _normalize_booking_flat_for_form(_body_scalars(body))
        flat['source'] = RENTAL_GURU_SOURCE
        qd = _dict_to_querydict(flat)
        payments_data = _payments_parallel_from_rows(body.get('payments') or [])
        form = BookingForm(qd, request=_fake_request(qd), action='create')
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        with rental_guru_user_tracking_context():
            form.save(payments_data=payments_data)
        booking = _load_booking_for_api(form.instance.pk)
        return Response({'booking': _serialize_booking(booking)}, status=status.HTTP_201_CREATED)


class RentalGuruUpdateBookingAPI(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def patch(self, request, pk):
        err = _api_auth_error_response(request)
        if err:
            return err
        try:
            booking = Booking.objects.get(pk=pk)
        except Booking.DoesNotExist:
            return Response({'error': 'Booking not found'}, status=status.HTTP_404_NOT_FOUND)
        body = dict(request.data)
        _log_rental_guru_request(request, body)
        flat = _normalize_booking_flat_for_form(
            _overlay_form_dict(_booking_as_form_dict(booking), _body_scalars(body))
        )
        flat['source'] = RENTAL_GURU_SOURCE
        qd = _dict_to_querydict(flat)
        payments_data = _payments_parallel_from_rows(body.get('payments') or [])
        form = BookingForm(qd, request=_fake_request(qd), instance=booking, action='edit')
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        with rental_guru_user_tracking_context():
            form.save(payments_data=payments_data)
        booking = _load_booking_for_api(form.instance.pk)
        return Response({'booking': _serialize_booking(booking)})


class RentalGuruUpdateBookingBySourceIdAPI(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def patch(self, request, source_id):
        err = _api_auth_error_response(request)
        if err:
            return err
        booking = Booking.objects.filter(
            source=RENTAL_GURU_SOURCE,
            source_id=source_id,
        ).first()
        if not booking:
            return Response({'error': 'Booking not found'}, status=status.HTTP_404_NOT_FOUND)
        body = dict(request.data)
        _log_rental_guru_request(request, body)
        flat = _normalize_booking_flat_for_form(
            _overlay_form_dict(_booking_as_form_dict(booking), _body_scalars(body))
        )
        flat['source'] = RENTAL_GURU_SOURCE
        qd = _dict_to_querydict(flat)
        payments_data = _payments_parallel_from_rows(body.get('payments') or [])
        form = BookingForm(qd, request=_fake_request(qd), instance=booking, action='edit')
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        with rental_guru_user_tracking_context():
            form.save(payments_data=payments_data)
        booking = _load_booking_for_api(form.instance.pk)
        return Response({'booking': _serialize_booking(booking)})


class RentalGuruCreatePaymentAPI(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def post(self, request):
        err = _api_auth_error_response(request)
        if err:
            return err
        body = dict(request.data)
        _log_rental_guru_request(request, body)
        flat = _body_scalars(body)
        flat['source'] = RENTAL_GURU_SOURCE
        qd = _dict_to_querydict(flat)
        form = PaymentForm(qd, request=_fake_request(qd), action='create')
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        with rental_guru_user_tracking_context():
            form.save()
        payment = Payment.objects.select_related(
            'payment_type', 'payment_method', 'bank'
        ).get(pk=form.instance.pk)
        return Response({'payment': _serialize_payment(payment)}, status=status.HTTP_201_CREATED)


class RentalGuruUpdatePaymentAPI(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]

    def patch(self, request, pk):
        err = _api_auth_error_response(request)
        if err:
            return err
        try:
            payment = Payment.objects.get(pk=pk)
        except Payment.DoesNotExist:
            return Response({'error': 'Payment not found'}, status=status.HTTP_404_NOT_FOUND)
        body = dict(request.data)
        _log_rental_guru_request(request, body)
        flat = _overlay_form_dict(_payment_as_form_dict(payment), _body_scalars(body))
        flat['source'] = RENTAL_GURU_SOURCE
        qd = _dict_to_querydict(flat)
        form = PaymentForm(qd, request=_fake_request(qd), instance=payment, action='edit')
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)
        with rental_guru_user_tracking_context():
            form.save()
        payment = Payment.objects.select_related(
            'payment_type', 'payment_method', 'bank'
        ).get(pk=form.instance.pk)
        return Response({'payment': _serialize_payment(payment)})