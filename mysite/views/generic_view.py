from calendar import month_name
from django.shortcuts import render
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, ApartmentPriceForm, CleaningForm,  PaymentMethodForm, PaymentForm, PaymentTypeForm
from django.core.paginator import Paginator
import json
from django.core import serializers
from datetime import date
from django.db.models import F, ExpressionWrapper, DateField, Value, Q, Case, When, IntegerField
from ..decorators import user_has_role
from .utils import handle_post_request, MODEL_MAP, get_related_fields, parse_query, get_model_fields
from .utils import DateEncoder
from datetime import datetime
from itertools import chain

@user_has_role('Admin')
def users(request):
    return generic_view(request, 'user', CustomUserForm, 'users.html')


@user_has_role('Admin', "Manager")
def apartments(request):
    return generic_view(request, 'apartment', ApartmentForm, 'apartments.html')


@user_has_role('Admin', "Manager")
def apartment_prices(request):
    return generic_view(request, 'apartmentprice', ApartmentPriceForm, 'apartment_prices.html')


@user_has_role('Admin', "Manager")
def bookings(request):
    return generic_view(request, 'booking', BookingForm, 'bookings.html')


@user_has_role('Admin', 'Cleaner')
def cleanings(request):
    return generic_view(request, 'cleaning', CleaningForm, 'cleanings.html')


@user_has_role('Admin')
def payment_methods(request):
    return generic_view(request, 'paymentmethod', PaymentMethodForm, 'payments_methods.html')


@user_has_role('Admin')
def payment_types(request):
    return generic_view(request, 'paymenttype', PaymentTypeForm, 'payments_types.html')


@user_has_role('Admin', 'Manager')
def payments(request):
    return generic_view(request, 'payment', PaymentForm, 'payments.html')


def format_dates(item):
    for key, value in item.items():
        if isinstance(value, str):
            try:
                # Try to parse the date string to a datetime object
                parsed_date = datetime.fromisoformat(value)
                item[key] = parsed_date.strftime('%B %d %Y')
            except ValueError:
                pass  # If parsing fails, leave the value as is
        elif isinstance(value, list):
            for sub_item in value:
                if isinstance(sub_item, dict):
                    format_dates(sub_item)
        elif isinstance(value, dict):
            format_dates(value)

def serialize_field(value):
    if isinstance(value, (datetime, date)):
        return value.strftime('%B %d %Y')
    elif hasattr(value, 'id'):
        return value.id
    elif isinstance(value, list):
        return [serialize_field(v) for v in value]
    elif isinstance(value, dict):
        return {k: serialize_field(v) for k, v in value.items()}
    return value

def generic_view(request, model_name, form_class, template_name, pages=30):
    search_query = request.GET.get('q', '')
    page = request.GET.get('page', 1)

    model = MODEL_MAP.get(model_name.lower())
    form = form_class(request=request)
    if not model:
        raise ValueError(f"No model found for {model_name}")

    if request.method == 'POST':
        handle_post_request(request, model, form_class)

    fk_or_o2o_fields, m2m_fields = get_related_fields(model)

    today = date.today()
    items = model.objects.select_related(
        *fk_or_o2o_fields).prefetch_related(*m2m_fields)
    
    # For apartments, prefetch pricing data for better performance
    if model_name.lower() == 'apartment':
        items = items.prefetch_related('prices')

    if request.user.role == 'Manager' and model_name.lower() == 'apartment':
        items = items.filter(manager=request.user)
    if request.user.role == 'Manager' and model_name.lower() == 'booking':
        items = items.filter(apartment__manager=request.user)
    if request.user.role == 'Manager' and model_name.lower() == 'payment':
        items = items.filter(
            Q(booking__apartment__manager=request.user) |
            Q(apartment__manager=request.user)
        ).distinct()

    # If there's a search query, apply the filters
    if search_query:
        q_objects = parse_query(model, search_query)
        items = items.filter(q_objects)

    # Specific model logic: If the model is Cleaning, order by date proximity.
    if model_name == "cleaning":
        # Apply Cleaner filter first if needed
        if request.user.role == 'Cleaner':
            items = items.filter(cleaner=request.user)
        
        # Apply apartment filter if provided
        apartment_filter = request.GET.get('apartment')
        if apartment_filter:
            items = items.filter(
                Q(apartment__id=apartment_filter) | 
                Q(booking__apartment__id=apartment_filter)
            )
        
        # Apply cleaner filter if provided
        cleaner_filter = request.GET.get('cleaner')
        if cleaner_filter:
            items = items.filter(cleaner__id=cleaner_filter)
        
        # Get direction parameter for bidirectional paging
        direction = request.GET.get('direction', 'both')  # 'both', 'future', 'past'
        
        if direction == 'future':
            # Show only today and future cleanings
            items = items.filter(date__gte=today).order_by('date')
        elif direction == 'past':
            # Show only past cleanings
            items = items.filter(date__lt=today).order_by('-date')
        else:
            # Default: bidirectional starting from today
            # Split into today's, future, and past cleanings
            # today_cleanings = items.filter(date=today)
            future_cleanings = items.filter(date__gt=today).order_by('date')
            #past_cleanings = items.filter(date__lt=today).order_by('-date')
            
            # Combine the querysets using itertools.chain
            items = list(chain(future_cleanings))
        
        # Manual pagination for the combined results
        paginator = Paginator(items, pages)
        items_on_page = paginator.get_page(page)
        
        # Skip the default pagination as we've already paginated
        paginator_already_applied = True
    # Specific model logic: If the model is Payment, apply filters.
    elif model_name == "payment":
        # Apply apartment filter if provided
        apartment_filter = request.GET.get('apartment')
        if apartment_filter:
            items = items.filter(
                Q(apartment__id=apartment_filter) | 
                Q(booking__apartment__id=apartment_filter)
            )
        
        # Apply tenant/amount/notes filter if provided
        tenant_filter = request.GET.get('tenant')
        if tenant_filter:
            try:
                # Try to parse as decimal for amount search
                amount_value = float(tenant_filter)
                items = items.filter(amount=amount_value)
            except (ValueError, TypeError):
                # If not a valid decimal, search by tenant name or notes
                items = items.filter(
                    Q(booking__tenant__full_name__icontains=tenant_filter) |
                    Q(notes__icontains=tenant_filter)
                )
        
        # Apply payment status filter if provided
        payment_status_filter = request.GET.get('payment_status_filter')
        if payment_status_filter:
            items = items.filter(payment_status=payment_status_filter)
        
        items = items.order_by('-id')
        paginator_already_applied = False
    # Specific model logic: If the model is ApartmentPrice, apply filters.
    elif model_name == "apartmentprice":
        # Apply apartment filter if provided
        apartment_filter = request.GET.get('apartment')
        if apartment_filter:
            items = items.filter(apartment__id=apartment_filter)
        
        # Apply date range filters if provided
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        
        if start_date:
            try:
                # Parse date from "July 03 2025" format
                start_date_obj = datetime.strptime(start_date, '%B %d %Y').date()
                items = items.filter(effective_date__gte=start_date_obj)
            except ValueError:
                # If date parsing fails, ignore the filter
                pass
        
        if end_date:
            try:
                # Parse date from "July 03 2025" format
                end_date_obj = datetime.strptime(end_date, '%B %d %Y').date()
                items = items.filter(effective_date__lte=end_date_obj)
            except ValueError:
                # If date parsing fails, ignore the filter
                pass
        
        items = items.order_by('-effective_date')
        paginator_already_applied = False
    else:
        items = items.order_by('-id')
        paginator_already_applied = False

    if not paginator_already_applied:
        paginator = Paginator(items, pages)
        items_on_page = paginator.get_page(page)
    
    # Add computed fields to objects for template access
    if model_name.lower() == 'apartment':
        for apartment in items_on_page:
            current_price = apartment.current_price
            apartment.current_price_display = f"${current_price}" if current_price else "No price set"
        
    items_list = []
    for original_obj in items_on_page:
        item = {field.name: serialize_field(getattr(original_obj, field.name)) for field in original_obj._meta.fields}
        item['id'] = original_obj.id
        item['links'] = serialize_field(original_obj.links)
        
        # Add form fields that aren't model fields but are marked as columns
        for field_name, field_instance in form.fields.items():
            if hasattr(field_instance, 'isColumn') and field_instance.isColumn and field_name not in item:
                # Initialize with empty value, will be populated below for specific models
                item[field_name] = None

        if hasattr(original_obj, 'assigned_cleaner'):
            item['assigned_cleaner'] = original_obj.assigned_cleaner.id if original_obj.assigned_cleaner else None
        if hasattr(original_obj, 'tenant') and original_obj.tenant is not None:
            item['tenant_full_name'] = original_obj.tenant.full_name
            item['tenant_email'] = original_obj.tenant.email
            item['tenant_phone'] = original_obj.tenant.phone

        # Add related payments if the model is Booking
        if hasattr(original_obj, 'payments'):
            item['payments'] = [
                {
                    'id': payment.id,
                    'amount': payment.amount,
                    'date': payment.payment_date,  # Ensure date is in ISO format
                    'status': payment.payment_status,
                    'notes': payment.notes,
                    'payment_type': payment.payment_type.id,
                    'invoice_url': payment.invoice_url if hasattr(payment, 'invoice_url') else None
                } for payment in original_obj.payments.all()
            ]

        # Add pricing information for apartment model
        if model_name.lower() == 'apartment':
            # Add current price
            current_price = original_obj.current_price
            item['current_price'] = float(current_price) if current_price else None
            item['current_price_display'] = f"${current_price}" if current_price else "No price set"
            
            # Add pricing history count
            item['price_count'] = original_obj.prices.count()
            
            # Add future price changes count
            future_prices_count = original_obj.get_future_prices().count()
            item['future_prices_count'] = future_prices_count
            
            # Add latest price date
            latest_price = original_obj.prices.first()  # Already ordered by -effective_date in model
            item['latest_price_date'] = latest_price.effective_date if latest_price else None

        # Add invoice_url for payment model
        if model_name.lower() == 'payment':
            item['invoice_url'] = original_obj.invoice_url if hasattr(original_obj, 'invoice_url') else None
            item['booking_id'] = original_obj.booking.id if original_obj.booking else None

        # Format all date fields in the item
        items_list.append(item)


    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list, cls=DateEncoder)    

    # Get fields from the model's metadata
    model_fields = get_model_fields(form)

    # Additional context for cleanings
    context = {
        'items': items_on_page, 
        "items_json": items_json, 
        'search_query': search_query, 
        'model_fields': model_fields, 
        "title": model_name.capitalize()
    }
    
    # Add apartments and cleaners for cleaning filters
    if model_name == "cleaning":
        from mysite.models import User, Apartment
        
        if request.user.role == 'Manager':
            apartments = Apartment.objects.filter(manager=request.user).order_by('name')
        else:
            apartments = Apartment.objects.all().order_by('name')
        
        cleaners = User.objects.filter(role='Cleaner').order_by('full_name')
        
        context['apartments'] = apartments
        context['cleaners'] = cleaners
    
    # Add apartments for payment filters
    if model_name == "payment":
        from mysite.models import Apartment
        
        if request.user.role == 'Manager':
            apartments = Apartment.objects.filter(manager=request.user).order_by('name')
        else:
            apartments = Apartment.objects.all().order_by('name')
        
        context['apartments'] = apartments
    
    # Add apartments for apartment prices filters
    if model_name == "apartmentprice":
        from mysite.models import Apartment
        
        if request.user.role == 'Manager':
            apartments = Apartment.objects.filter(manager=request.user).order_by('name')
        else:
            apartments = Apartment.objects.all().order_by('name')
        
        context['apartments'] = apartments

    return render(request, template_name, context)
