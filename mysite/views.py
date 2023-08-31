from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.shortcuts import render, redirect
from .forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView
from .models import User, Apartment, Booking, Contract, Cleaning, Notification, PaymentMethod, Payment, Bank, CustomFieldMixin
import logging
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, ContractForm, CleaningForm, NotificationForm, PaymentMethodForm, PaymentForm, BankForm
from django.contrib.auth.hashers import make_password
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
import html
from django.db import models
from django.forms.models import model_to_dict
import json
from django.core import serializers

logger = logging.getLogger(__name__)

@login_required
def index(request):
    context = {}
    return render(request, 'index.html', context )

MODEL_MAP = {
    'user': User,
    'apartment': Apartment,
    'booking': Booking,
    'contract': Contract,
    'cleaning': Cleaning,
    'notification': Notification,
    'paymentmethod': PaymentMethod,
    'payment': Payment,
    'bank': Bank
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


#   logger.error("**************ITEMS*************\n\n\n")
#     items_list = [model_to_dict(item) for item in items]
#     logger.error(items_list)
#     logger.error("**************ITEMS*************\n\n\n")

@login_required
def generic_view(request, model_name, form_class, template_name, pages=10):
    search_query = request.GET.get('q', '')
    page = request.GET.get('page', 1)
    search_fields_str = request.GET.get('search_fields', '')
    search_fields = search_fields_str.split(',')

    model = MODEL_MAP.get(model_name.lower())
    if not model:
        raise ValueError(f"No model found for {model_name}")

    fk_or_o2o_fields, m2m_fields = get_related_fields(model)


    if search_query and search_fields:
        queries = [Q(**{f"{field}__icontains": search_query}) for field in search_fields]
        query = queries.pop()
        for item in queries:
            query |= item
        items = model.objects.select_related(*fk_or_o2o_fields).prefetch_related(*m2m_fields).filter(query)
    else:
        items = model.objects.select_related(*fk_or_o2o_fields).prefetch_related(*m2m_fields).all()

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
   

    if request.method == 'POST':
        if 'edit_add' in request.POST:
            item_id =request.POST['id']
            if item_id:
                instance = model.objects.get(id=item_id)
                form = form_class(request.POST, instance=instance)
                if form.is_valid():
                    form.save()
            else:
                # Create logic
                form = form_class(request.POST)
                if form.is_valid():
                    form.save()
        elif 'delete' in request.POST:
            instance = model.objects.get(id=request.POST['id'])
            instance.delete()
            form = form_class()
    else:
        form = form_class()
    

    return render(request, template_name, {'items': items_on_page, "items_json": items_json, 'search_query': search_query, 'model_fields': model_fields})

def users(request):
    return generic_view(request, 'user', CustomUserForm, 'users.html')

def properties(request):
    return generic_view(request, 'apartment', ApartmentForm, 'properties.html')

def bookings(request):
    return generic_view(request, 'booking', BookingForm, 'bookings.html')

def contracts(request):
    return generic_view(request, 'contract', ContractForm, 'contracts.html')

def cleanings(request):
    return generic_view(request, 'cleaning', CleaningForm, 'cleanings.html')

def notifications(request):
    return generic_view(request, 'notification', NotificationForm, 'notifications.html')

def payment_methods(request):
    return generic_view(request, 'paymentmethod', PaymentMethodForm, 'payment_methods.html')

def payments(request):
    return generic_view(request, 'payment', PaymentForm, 'payments.html')

def banks(request):
    return generic_view(request, 'bank', BankForm, 'banks.html')


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