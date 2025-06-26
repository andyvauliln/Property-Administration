from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework import status
from ..models import Apartment, Booking
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
        yesterday = today - timedelta(days=1)
        
        # Get apartment_ids from query parameters
        apartment_ids = request.GET.get('apartment_ids', '')
        if apartment_ids:
            apartment_ids = [int(id_) for id_ in apartment_ids.split(',')]
            apartments = Apartment.objects.filter(
                id__in=apartment_ids,
                status='Available'
            ).filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today)
            )
        else:
            # giving all apartments
            apartments = Apartment.objects.filter(
                Q(end_date__isnull=True) | Q(end_date__gte=today),
                status='Available',

            )
            return Response({"apartments": []}, content_type='application/json')
        
        # Prepare response data
        response_data = {
            "apartments": []
        }
        
        # For each apartment, get its bookings
        for apartment in apartments:
            apartment_data = {
                "apartment_id": apartment.id,
                "price": apartment.price,
                "apartment_name": apartment.name,
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
        
        try:
            number_of_rooms = int(number_of_rooms)
            new_price = float(new_price)
        except (ValueError, TypeError):
            return Response(
                {"error": "number_of_rooms must be an integer and new_price must be a number"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Find apartments with the specified number of bedrooms
        apartments = Apartment.objects.filter(bedrooms=number_of_rooms)
        
        if not apartments.exists():
            return Response(
                {"error": f"No apartments found with {number_of_rooms} rooms"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Update the price for all matching apartments
        updated_count = apartments.update(price=new_price)
        
        # Get the updated apartments for response
        updated_apartments = apartments.values('id', 'name', 'bedrooms', 'price')
        
        response_data = {
            "message": f"Successfully updated price to ${new_price} for {updated_count} apartments with {number_of_rooms} rooms",
            "updated_count": updated_count,
            "number_of_rooms": number_of_rooms,
            "new_price": new_price,
            "updated_apartments": list(updated_apartments)
        }
        
        return Response(response_data, content_type='application/json')