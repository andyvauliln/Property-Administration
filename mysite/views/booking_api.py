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
                "booked_dates": []
            }
            
            # Get all confirmed bookings for this apartment
            bookings = Booking.objects.filter(
                apartment=apartment,
            )
            
            # For each booking, get all dates between start and end date
            for booking in bookings:
                current_date = booking.start_date
                while current_date <= booking.end_date:
                    apartment_data["booked_dates"].append(
                        current_date.strftime("%Y-%m-%d")
                    )
                    current_date += timedelta(days=1)
            
            # Remove duplicates and sort dates
            apartment_data["booked_dates"] = sorted(list(set(apartment_data["booked_dates"])))
            response_data["apartments"].append(apartment_data)
        
        return Response(response_data, content_type='application/json')