from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.renderers import JSONRenderer
from rest_framework import status
from ..models import Apartment, Booking
from datetime import datetime, timedelta, date
from django.db.models import Q

class ApartmentBookingDates(APIView):
    renderer_classes = [JSONRenderer]  # This ensures JSON response
    
    def get(self, request):
        today = date.today()
        # Get available apartments that either have no end_date or haven't ended yet
        apartments = Apartment.objects.filter(
            status='Available'
        ).filter(
            Q(end_date__isnull=True) | Q(end_date__gte=today)
        )
        
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
            
            # Get all bookings for this apartment
            bookings = Booking.objects.filter(
                apartment=apartment,
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