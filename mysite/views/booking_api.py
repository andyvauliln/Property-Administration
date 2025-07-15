from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework import status
from ..models import Apartment, Booking, ApartmentPrice
from datetime import datetime, timedelta, date
from django.db.models import Q
import os

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
        
        today = date.today()
        
        # Get apartment_ids from query parameters
        apartment_ids = request.GET.get('apartment_ids', '')
        if apartment_ids:
            apartment_ids = [int(id_) for id_ in apartment_ids.split(',')]
            apartments = Apartment.objects.filter(
                id__in=apartment_ids,
                status='Available'
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today)
            ).prefetch_related('prices')
        else:
            # giving all apartments
            apartments = Apartment.objects.filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today),
                status='Available',

            ).prefetch_related('prices')
            return Response({"apartments": []}, content_type='application/json')
        
        # Prepare response data
        response_data = {
            "apartments": []
        }
        
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
            
            # Build pricing data - include current period and future prices
            pricing_data = []
            
            # Add current active price if it exists
            if current_active_price:
                pricing_data.append({
                    "price": float(current_active_price.price),
                    "effective_date": current_active_price.effective_date.strftime("%Y-%m-%d"),
                    "default_price": float(apartment.default_price),
                    "notes": current_active_price.notes or ""
                })
            
            # Add future prices
            for price in future_prices:
                pricing_data.append({
                    "price": float(price.price),
                    "effective_date": price.effective_date.strftime("%Y-%m-%d"),
                    "default_price": float(apartment.default_price),
                    "notes": price.notes or ""
                })
            
            apartment_data = {
                "apartment_id": apartment.id,
                "current_price": float(current_price) if current_price else None,
                "apartment_name": apartment.name,
                "pricing_history": pricing_data,
                "bookings": []
            }
            
            # Get all active bookings for this apartment (ended yesterday or later)
            bookings = Booking.objects.filter(
                apartment=apartment,
                end_date__gte=today
            )
            
            # For each booking, add its period and status
            for booking in bookings:
                apartment_data["bookings"].append({
                    "start_date": booking.start_date.strftime("%Y-%m-%d"),
                    "end_date": booking.end_date.strftime("%Y-%m-%d"),
                    "status": booking.status
                })
            
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
        
        if existing_price:
            # Update existing price
            existing_price.price = new_price
            existing_price.notes = notes
            existing_price.save()
            action = "updated"
            price_record = existing_price
        else:
            # Create new price record
            price_record = ApartmentPrice.objects.create(
                apartment=apartment,
                price=new_price,
                effective_date=effective_date,
                notes=notes
            )
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
        
        response_data = {
            "message": f"Successfully {action} price for apartment {apartment.name}",
            "apartment_id": apartment.id,
            "apartment_name": apartment.name,
            "current_price": float(apartment.current_price) if apartment.current_price else None,
            "new_price_record": {
                "price": float(price_record.price),
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
        
        for apartment in apartments:
            # Check if a price already exists for this date
            existing_price = ApartmentPrice.objects.filter(
                apartment=apartment,
                effective_date=effective_date
            ).first()
            
            if existing_price:
                # Update existing price
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
                # Create new price record
                apartment_price = ApartmentPrice.objects.create(
                    apartment=apartment,
                    price=new_price,
                    effective_date=effective_date,
                    notes=notes
                )
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