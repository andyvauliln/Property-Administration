from django.shortcuts import render
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, CleaningForm,  PaymentMethodForm, PaymentForm, PaymentTypeForm
from django.core.paginator import Paginator
import json
from django.core import serializers
from datetime import date
from django.db.models import F, ExpressionWrapper, DateField, Value
from ..decorators import user_has_role
from .utils import handle_post_request, MODEL_MAP, get_related_fields, parse_query, get_model_fields
from .utils import DateEncoder
from datetime import datetime

@user_has_role('Admin')
def users(request):
    return generic_view(request, 'user', CustomUserForm, 'users.html')


@user_has_role('Admin', "Manager")
def apartments(request):
    return generic_view(request, 'apartment', ApartmentForm, 'apartments.html')


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

    if request.user.role == 'Manager' and model_name.lower() == 'apartment':
        items = items.filter(manager=request.user)
    if request.user.role == 'Manager' and model_name.lower() == 'booking':
        items = items.filter(apartment__manager=request.user)
    if request.user.role == 'Manager' and model_name.lower() == 'payment':
        items = items.filter(booking__apartment__manager=request.user) | items.filter(apartment__manager=request.user)


    # If there's a search query, apply the filters
    if search_query:
        q_objects = parse_query(model, search_query)
        items = items.filter(q_objects)

    # Specific model logic: If the model is Cleaning, order by date proximity.
    if model_name == "cleaning":
        difference = ExpressionWrapper(
            F('date') - Value(today), output_field=DateField())
        items = items.annotate(
            date_difference=difference).order_by('date_difference')
        if request.user.role == 'Cleaner':
            items = items.filter(cleaner=request.user)
    else:
        items = items.order_by('-id')

    paginator = Paginator(items, pages)
    items_on_page = paginator.get_page(page)
    items_list = []
    for original_obj in items_on_page:
        item = {field.name: serialize_field(getattr(original_obj, field.name)) for field in original_obj._meta.fields}
        item['id'] = original_obj.id
        item['links'] = serialize_field(original_obj.links)

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

    return render(
        request, template_name,
        {'items': items_on_page, "items_json": items_json, 'search_query': search_query, 'model_fields': model_fields, "title": model_name.capitalize()})
