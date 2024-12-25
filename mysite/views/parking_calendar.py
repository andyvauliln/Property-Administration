from django.shortcuts import render
from ..models import Apartment, Booking, Cleaning, Payment, User, HandymanCalendar
import logging
from mysite.forms import BookingForm
from django.db.models import Q
import json
from datetime import date, timedelta
from collections import defaultdict
from django.utils import timezone
from dateutil.relativedelta import relativedelta
from ..decorators import user_has_role
from .utils import generate_weeks, DateEncoder, handle_post_request, get_model_fields
from mysite.forms import HandymanCalendarForm
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods


def parking_calendar(request):
    parking_data = []
    context = {
        'calendar_data': parking_data,
        'title': "Parking Calendar",
        'endpoint': "/parking_calendar",
    }

    return render(request, 'parking_calendar.html', context)