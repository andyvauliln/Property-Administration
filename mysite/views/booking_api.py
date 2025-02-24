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
            # If no apartment_ids provided, return empty response
            return Response({"apartments": []}, content_type='application/json')
        
        # Prepare response data
        response_data = {
            "apartments": []
        }
        
        # For each apartment, get its bookings
        for apartment in apartments:
            apartment_data = {
                "apartment_id": apartment.id,
                "bookings": []
            }
            
            # Get all active bookings for this apartment (ended yesterday or later)
            bookings = Booking.objects.filter(
                apartment=apartment,
                end_date__gte=yesterday
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