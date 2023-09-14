from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.shortcuts import render, redirect
from .forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView
from .models import User, Apartment, Booking, Contract, Cleaning, Notification, PaymentMethod, Payment
import logging
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, ContractForm, CleaningForm, NotificationForm, PaymentMethodForm, PaymentForm, CustomFieldMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.db import models
import json
from django.core import serializers
from datetime import datetime, date, timedelta
from collections import defaultdict
from django.utils import timezone
import calendar
from dateutil.relativedelta import relativedelta
from django.contrib import messages

logger = logging.getLogger(__name__)

def stringify_keys(d):
    """Recursively convert a dictionary's keys to strings."""
    if not isinstance(d, dict):
        return d
    return {str(k): stringify_keys(v) for k, v in d.items()}


def generate_weeks(month_start):
    """Generate weeks for a given month."""
    cal = calendar.Calendar(firstweekday=calendar.SUNDAY)
    return cal.monthdatescalendar(month_start.year, month_start.month)

@login_required
def index(request):
    # Fetch all properties
    apartments = Apartment.objects.all()

    # Fetch today's notifications
    today = date.today()
    next_week = today + timedelta(days=7)
    next_month = today + timedelta(days=30)
    all_notifications = Notification.objects.filter(date__range=(today, next_month))

    # Group notifications by period
    today_notifications = [n for n in all_notifications if n.date == today]
    next_week_notifications = [n for n in all_notifications if today < n.date <= next_week]
    next_month_notifications = [n for n in all_notifications if next_week < n.date <= next_month]
    
    page = int(request.GET.get('page', 1))
    start_month = (date.today() + relativedelta(months=3 * (page - 1))).replace(day=1)
    months = [start_month + relativedelta(months=i) for i in range(3)]
    end_date = months[-1] + relativedelta(months=1) - timedelta(days=1)  
    

    

    bookings = Booking.objects.filter(start_date__range=(start_month, end_date)).select_related('apartment')
    cleanings = Cleaning.objects.filter(date__range=(start_month, end_date)).select_related('booking__apartment')
    payments = Payment.objects.filter(payment_date__range=(start_month, end_date)).select_related('booking__apartment')

    event_data = defaultdict(lambda: defaultdict(list))  # Use a lambda to initialize nested defaultdicts

    for booking in bookings:
        current_date = booking.start_date
        while current_date <= booking.end_date:
            key = (booking.apartment.id, current_date)
            event_data[key]['booking'].append(booking)
            current_date += timedelta(days=1)

    for cleaning in cleanings:
        if cleaning.booking:
            key = (cleaning.booking.apartment.id, cleaning.date)
            event_data[key]['cleaning'].append(cleaning)

    for payment in payments:
        if payment.booking and payment.booking.apartment:
            key = (payment.booking.apartment.id, payment.payment_date)
            event_data[key]['payment'].append(payment)
        
    
    apartments_data = {}

    for apartment in apartments:
        apartment_data = {
            'apartment': apartment,
            'months': defaultdict(list)
        }

        for month in months:
            weeks = generate_weeks(month)
            for week in weeks:
                week_data = []
                for day in week:
                    bookings_for_day = event_data.get((apartment.id, day), {}).get('booking', [])
                    cleanings_for_day = event_data.get((apartment.id, day), {}).get('cleaning', [])
                    payments_for_day = event_data.get((apartment.id, day), {}).get('payment', [])

                    day_data = {
                        'day': day,
                        'booking_ids': [booking.id for booking in bookings_for_day],
                        'booking_statuses': [booking.status for booking in bookings_for_day],
                        'booking_starts': [booking.start_date for booking in bookings_for_day],
                        'booking_ends': [booking.end_date for booking in bookings_for_day],
                        'cleaning_ids': [cleaning.id for cleaning in cleanings_for_day],
                        'cleaning_statuses': [cleaning.status for cleaning in cleanings_for_day],
                        'payment_ids': [payment.id for payment in payments_for_day],
                        'payment_types': [payment.payment_type for payment in payments_for_day],
                        'payment_amounts': [payment.amount for payment in payments_for_day],
                        'payment_statuses': [payment.payment_status for payment in payments_for_day],
                    }
                    week_data.append(day_data)
                apartment_data['months'][month].append(week_data)

        apartments_data[apartment.id] = apartment_data

    apartments_data_str_keys = stringify_keys(apartments_data)    
    apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    apartments_data = dict(apartments_data)
    for apartment_id, apartment_data in apartments_data.items():
        apartment_data['months'] = dict(apartment_data['months'])


    context = {
        'apartments_data': apartments_data,
        'current_date': timezone.now(),
        'apartments_data_json': apartments_data_json,
        'months': months,
        'bookings': bookings,
        'today_notifications': today_notifications,
        'next_week_notifications': next_week_notifications,
        'next_month_notifications': next_month_notifications,
    }

    return render(request, 'index.html', context)

MODEL_MAP = {
    'user': User,
    'apartment': Apartment,
    'booking': Booking,
    'contract': Contract,
    'cleaning': Cleaning,
    'notification': Notification,
    'paymentmethod': PaymentMethod,
    'payment': Payment,
}


def get_related_fields(model, prefix=''):
    fk_or_o2o_fields = []
    m2m_fields = []
    for field in model._meta.get_fields():
        if isinstance(field, (models.ForeignKey, models.OneToOneField)) and field.related_model:
            fk_name = f"{prefix}{field.name}"
            fk_or_o2o_fields.append(fk_name)
            # Recursively fetch nested relationships
            nested_fk, nested_m2m = get_related_fields(field.related_model, f"{fk_name}__")
            fk_or_o2o_fields.extend(nested_fk)
            m2m_fields.extend(nested_m2m)
        elif isinstance(field, models.ManyToManyField) and field.related_model:
            m2m_fields.append(f"{prefix}{field.name}")
    return fk_or_o2o_fields, m2m_fields

def parse_query(model, query):
    tokens = tokenize_query(query)
    return parse_tokens(model, tokens)

def tokenize_query(query):
    # Add spaces around parentheses for proper splitting
    query = query.replace('(', ' ( ').replace(')', ' ) ')
    tokens = query.split()
    return tokens

def parse_tokens(model, tokens):
    stack = []
    while tokens:
        token = tokens.pop(0)
        if token == '(':
            # Recursively parse the content inside the parentheses
            sub_q = parse_tokens(model, tokens)
            stack.append(sub_q)
        elif token == ')':
            break
        elif token in ['+', '|']:
            stack.append(token)
        else:
            # Handle conditions like field=value, field>value, etc.
            operator = None
            for op in ['>=', '<=', '>', '<', '=']:
                if op in token:
                    field, value = token.split(op)
                    if op == '=':
                        operator = '' if field == "id" or 'date' in field else '__icontains'
                    else:
                        operator = {
                            '>': '__gt',
                            '<': '__lt',
                            '>=': '__gte',
                            '<=': '__lte'
                        }[op]
                    break

            field = field.replace('.', '__').strip()
            if isinstance(value, str):
                value = value.strip()
            if 'date' in field and operator in ['', '__gt', '__lt', '__gte', '__lte']:
                try:
                    value = datetime.strptime(value, '%d.%m.%Y').date()
                except ValueError:
                    raise ValueError(f"Invalid date format for {field}: {value}")

            stack.append(Q(**{f"{field}{operator}": value}))

    return combine_stack(stack)

def combine_stack(stack):
    while '+' in stack or '|' in stack:
        if '+' in stack:
            idx = stack.index('+')
            q1 = stack.pop(idx - 1)
            stack.pop(idx - 1)  # Remove the '+'
            q2 = stack.pop(idx - 1)
            stack.insert(idx - 1, q1 & q2)
        elif '|' in stack:
            idx = stack.index('|')
            q1 = stack.pop(idx - 1)
            stack.pop(idx - 1)  # Remove the '|'
            q2 = stack.pop(idx - 1)
            stack.insert(idx - 1, q1 | q2)

    return stack[0]




@login_required
def generic_view(request, model_name, form_class, template_name, pages=30):
    search_query = request.GET.get('q', '')
    page = request.GET.get('page', 1)

    model = MODEL_MAP.get(model_name.lower())
    if not model:
        raise ValueError(f"No model found for {model_name}")
    
    if request.method == 'POST':
        if 'edit' in request.POST:
            item_id =request.POST['id']
            if item_id:
                instance = model.objects.get(id=item_id)
                form = form_class(request.POST, instance=instance, request=request, action='edit')
                if form.is_valid():
                    form.save()
                else:
                    for error in form.errors.values():
                        messages.error(request, error)
            return redirect(request.path)
        elif 'add' in request.POST:
            form = form_class(request.POST, request=request)
            if form.is_valid():
                form.save()
            else:
                for error in form.errors.values():
                    messages.error(request, error)
            return redirect(request.path)
        elif 'delete' in request.POST:
            instance = model.objects.get(id=request.POST['id'])
            instance.delete()
            form = form_class()
            return redirect(request.path)
    else:
        form = form_class()

    fk_or_o2o_fields, m2m_fields = get_related_fields(model)


    # Default query to get all items
    items = model.objects.select_related(*fk_or_o2o_fields).prefetch_related(*m2m_fields).all().order_by('-id')

    if search_query:
        q_objects = parse_query(model, search_query)
        items = model.objects.select_related(*fk_or_o2o_fields).prefetch_related(*m2m_fields).filter(q_objects).order_by('-id')

    paginator = Paginator(items, pages)
    items_on_page = paginator.get_page(page)
    items_json_data = serializers.serialize('json', items_on_page)

    # Convert the serialized data to a Python list of dictionaries
    data_list = json.loads(items_json_data)

    # Extract the 'fields' from each item in the list
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]

    
    for item, original_obj in zip(items_list, items_on_page):
        if hasattr(original_obj, 'assigned_cleaner'):
            item['assigned_cleaner'] = original_obj.assigned_cleaner.id if original_obj.assigned_cleaner else None
        item['links'] = original_obj.links

    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)

    
    # Get fields from the model's metadata
    model_fields = [(field_name, field_instance) for field_name, field_instance in form_class.base_fields.items() if isinstance(field_instance, CustomFieldMixin)]

   
    return render(request, template_name, {'items': items_on_page, "items_json": items_json, 'search_query': search_query, 'model_fields': model_fields})

def users(request):
    return generic_view(request, 'user', CustomUserForm, 'users.html')

def apartments(request):
    return generic_view(request, 'apartment', ApartmentForm, 'apartments.html')

def bookings(request):
    return generic_view(request, 'booking', BookingForm, 'bookings.html')

def contracts(request):
    return generic_view(request, 'contract', ContractForm, 'contracts.html')

def cleanings(request):
    return generic_view(request, 'cleaning', CleaningForm, 'cleanings.html')

def notifications(request):
    return generic_view(request, 'notification', NotificationForm, 'notifications.html')

def payment_methods(request):
    return generic_view(request, 'paymentmethod', PaymentMethodForm, 'payments_methods.html')

def payments(request):
    return generic_view(request, 'payment', PaymentForm, 'payments.html')



def custom_login_view(request):
    if request.method == 'POST':
        form = CustomUserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            # Check if "Remember Me" was not ticked
            if not form.cleaned_data.get('remember_me'):
                # Set session to expire when user closes browser
                request.session.set_expiry(0)

            return redirect('/')
    else:
        form = CustomUserLoginForm()
    return render(request, 'login.html', {'form': form})

class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('login')  # This will redirect to the URL pattern named 'login'

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Clear the session
        request.session.flush()
        return response