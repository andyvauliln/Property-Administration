from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.shortcuts import render, redirect
from .forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView
from .models import User, Apartment, Booking, Contract, Cleaning, Notification, PaymentMethod, Payment, CustomFieldMixin
import logging
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, ContractForm, CleaningForm, NotificationForm, PaymentMethodForm, PaymentForm
from django.core.paginator import Paginator
from django.db.models import Q
from django.db import models
import json
from django.core import serializers
from datetime import datetime, date, timedelta
import inspect

logger = logging.getLogger(__name__)
def add_one_month(orig_date):
    # Calculate the next month and year
    new_month = orig_date.month % 12 + 1
    new_year = orig_date.year + (orig_date.month // 12)
    return date(new_year, new_month, 1)

@login_required
def index(request):
   # Fetch all properties
    apartments = Apartment.objects.all().prefetch_related('booked_apartments')


    # Fetch bookings for each property
    bookings = {apartment.id: Booking.objects.filter(apartment=apartment) for apartment in apartments}

    # Fetch today's notifications
    today = date.today()
    today_notifications = Notification.objects.filter(date=today)

    # Fetch next week's notifications
    next_week = today + timedelta(days=7)
    next_week_notifications = Notification.objects.filter(date__range=(today, next_week))

    # Fetch next month's notifications
    next_month = today + timedelta(days=30)
    next_month_notifications = Notification.objects.filter(date__range=(today, next_month))
    
    page = int(request.GET.get('page', 1))
    start_month = date.today().replace(day=1) + timedelta(days=30 * (page - 1) * 3)
    
    months = [start_month]
    for _ in range(2):
        start_month = add_one_month(start_month)
        months.append(start_month)

    context = {
        'apartments': apartments,
        'months': months,
        'bookings': bookings,
        'today_notifications': today_notifications,
        'next_week_notifications': next_week_notifications,
        'next_month_notifications': next_month_notifications
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
    # Replace + with AND for proper splitting
    query = query.replace('+', ' AND ')
    
    or_conditions = query.split(' OR ')
    q_objects = Q()

    for condition in or_conditions:
        and_conditions = condition.strip().split(' AND ')
        and_q = Q()
        for sub_condition in and_conditions:
            operator = None
            if '=' in sub_condition:
                field, value = sub_condition.split('=')
                operator = '__icontains'
            elif '>' in sub_condition:
                field, value = sub_condition.split('>')
                operator = '__gt'
            elif '<' in sub_condition:
                field, value = sub_condition.split('<')
                operator = '__lt'
            elif '>=' in sub_condition:
                field, value = sub_condition.split('>=')
                operator = '__gte'
            elif '<=' in sub_condition:
                field, value = sub_condition.split('<=')
                operator = '__lte'

            # Replace dot with double underscore for related fields
            field = field.replace('.', '__').strip()

            if 'date' in field and operator in ['__gt', '__lt', '__gte', '__lte']:
                try:
                    value = datetime.strptime(value.strip(), '%d.%m.%Y').date()
                except ValueError:
                    raise ValueError(f"Invalid date format for {field}: {value}")

            and_q &= Q(**{f"{field}{operator}": value})

        q_objects |= and_q

    return q_objects


@login_required
def generic_view(request, model_name, form_class, template_name, pages=10):
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
                form = form_class(request.POST, instance=instance)
                if form.is_valid():
                    if hasattr(form.instance, 'save') and 'form_data' in inspect.signature(form.instance.save).parameters:
                        form.save(form_data=request.POST)
                    else:
                        form.save()
            return redirect(request.path)
        elif 'add' in request.POST:
            form = form_class(request.POST)
            if form.is_valid():
                if model_name == "booking":
                    form.save(form_data=request.POST)
                else:
                    form.save()
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

    # Adding the links apartment
    for item, original_obj in zip(items_list, items_on_page):
        item['links'] = original_obj.links

    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)

    
    model_fields = [field for field in model._meta.get_fields() if isinstance(field, CustomFieldMixin)]
   
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