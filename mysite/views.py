from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.shortcuts import render, redirect
from .forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView
from .models import User, Property, Booking, Contract, Cleaning, Notification, PaymentMethod, Payment, Bank
import logging
from mysite.forms import CustomUserForm, BookingForm, PropertyForm, ContractForm, CleaningForm, NotificationForm, PaymentMethodForm, PaymentForm, BankForm
from django.contrib.auth.hashers import make_password
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
import html
from django.db import models

logger = logging.getLogger(__name__)

@login_required
def index(request):
    context = {}
    return render(request, 'index.html', context )

MODEL_MAP = {
    'user': User,
    'property': Property,
    'booking': Booking,
    'contract': Contract,
    'cleaning': Cleaning,
    'notification': Notification,
    'paymentmethod': PaymentMethod,
    'payment': Payment,
    'bank': Bank
}
def get_special_fields(model):
    special_fields = {}
    
    for field in model._meta.fields:
        # Handle fields with choices
        if field.choices:
            special_fields[field.name] = {
                'type': 'dropdown',
                'options': [{'value': choice[0], 'label': choice[1]} for choice in field.choices]
            }
            
        if isinstance(field, models.DateField):
            special_fields[field.name] = {
                'type': 'datepicker'
            }

        # Check for BooleanField
        if isinstance(field, models.BooleanField):
            special_fields[field.name] = {
                'type': 'checkbox'
            }
        # Handle manager and owner fields for the Property model
        if model == Property:
            if field.name == 'manager':
                special_fields[field.name] = {
                    'type': 'dropdown',
                    'options': [{'value': user.id, 'label': user.full_name} for user in User.objects.filter(role='Manager')]
                }
            elif field.name == 'owner':
                special_fields[field.name] = {
                    'type': 'dropdown',
                    'options': [{'value': user.id, 'label': user.full_name} for user in User.objects.filter(role='Owner')]
                }
    
    return special_fields



def get_related_fields(model):
    fk_or_o2o_fields = []
    m2m_fields = []
    for field in model._meta.get_fields():
        if isinstance(field, (models.ForeignKey, models.OneToOneField)) and field.related_model:
            fk_or_o2o_fields.append(field.name)
        elif isinstance(field, models.ManyToManyField) and field.related_model:
            m2m_fields.append(field.name)
    return fk_or_o2o_fields, m2m_fields


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
    
    special_fields = get_special_fields(model)
   

    if request.method == 'POST':
        if 'edit_add' in request.POST:
            item_id =request.POST['id']
            if item_id:
                # Update logic
                instance = model.objects.get(id=item_id)
                form = form_class(request.POST, instance=instance)
                if form.is_valid():
                    logger.error("**************FORM*************")
                    logger.error(form.cleaned_data)
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
    
    field_data = []

    for field in model._meta.get_fields():
        if not (field.one_to_many or field.many_to_many):
            field_info = {
                "name": html.unescape(field.name),
                "is_foreign_key": isinstance(field, (models.ForeignKey, models.OneToOneField)),
                "related_model": field.related_model.__name__ if isinstance(field, (models.ForeignKey, models.OneToOneField)) else None
            }
            field_data.append(field_info)

    return render(request, template_name, {'items': items_on_page, "special_fields": special_fields, 'search_query': search_query, "field_data":field_data})

def users(request):
    return generic_view(request, 'user', CustomUserForm, 'users.html')

def properties(request):
    return generic_view(request, 'property', PropertyForm, 'properties.html')

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