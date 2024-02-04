
from django.shortcuts import render
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, CleaningForm,  PaymentMethodForm, PaymentForm, PaymentTypeForm
from django.core.paginator import Paginator
import json
from django.core import serializers
from datetime import date
from django.db.models import F, ExpressionWrapper, DateField, Value
from ..decorators import user_has_role
from .utils import handle_post_request, MODEL_MAP, get_related_fields, parse_query, get_model_fields


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


@user_has_role('Admin')
def payments(request):
    return generic_view(request, 'payment', PaymentForm, 'payments.html')


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
    items_json_data = serializers.serialize('json', items_on_page)

    # Convert the serialized data to a Python list of dictionaries
    data_list = json.loads(items_json_data)

    # Extract the 'fields' from each item in the list
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]

    for item, original_obj in zip(items_list, items_on_page):
        if hasattr(original_obj, 'assigned_cleaner'):
            item['assigned_cleaner'] = original_obj.assigned_cleaner.id if original_obj.assigned_cleaner else None
        if hasattr(original_obj, 'tenant'):
            item['tenant_full_name'] = original_obj.tenant.full_name
            item['tenant_email'] = original_obj.tenant.email
            item['tenant_phone'] = original_obj.tenant.phone
        item['links'] = original_obj.links

    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)

    # Get fields from the model's metadata
    model_fields = get_model_fields(form)

    return render(
        request, template_name,
        {'items': items_on_page, "items_json": items_json, 'search_query': search_query, 'model_fields': model_fields})
