from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.shortcuts import render, redirect
from .forms import CustomUserLoginForm
from django.urls import reverse_lazy
from django.contrib.auth.views import LogoutView
from .models import User, Apartment, Booking, Cleaning, Notification, PaymentMethod, Payment, PaymenType
import logging
from mysite.forms import CustomUserForm, BookingForm, ApartmentForm, CleaningForm, NotificationForm, PaymentMethodForm, PaymentForm, CustomFieldMixin, PaymentTypeForm
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
from django.db.models import F, ExpressionWrapper, DateField, DecimalField, Sum, Case, When, Value
from django.http import JsonResponse
import os
from decimal import Decimal
import requests
from django.http import HttpResponseBadRequest
from .decorators import user_has_role
from django.db.models import Min
# from docusign_esign import EnvelopesApi, EnvelopeDefinition, TemplateRole, Text, Tabs, EnvelopeEvent
# from docusign_esign import ApiClient, AuthenticationApi, RecipientViewRequest, EventNotification, RecipientEvent

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


@user_has_role('Admin', "Manager")
def index(request):

    # Fetch today's notifications
    # today = date.today()
    # next_week = today + timedelta(days=7)
    # next_month = today + timedelta(days=30)
    # all_notifications = Notification.objects.filter(
    #     date__range=(today, next_month))

    page = request.GET.get('page', 1)

    try:
        page = int(page)
    except ValueError:
        page = 1

    prev_page = page - 1
    next_page = page + 1
    start_month = (date.today() + relativedelta(months=3 *
                   (page - 1))).replace(day=1)
    months = [start_month + relativedelta(months=i) for i in range(3)]
    end_date = months[-1] + relativedelta(months=1) - timedelta(days=1)

    if request.user.role == 'Manager':
        # Filter apartments managed by this user
        apartments = Apartment.objects.filter(
            Q(end_date__gte=start_month) | Q(end_date__isnull=True),
            manager=request.user
        ).order_by('name')
        # Filter bookings related to apartments managed by this user
        bookings = Booking.objects.filter(apartment__manager=request.user)
        # Filter cleanings related to bookings managed by this user
        cleanings = Cleaning.objects.filter(
            booking__apartment__manager=request.user, date__range=(start_month, end_date)).select_related(
            'booking__apartment')
        # Filter payments related to bookings managed by this user
        payments = Payment.objects.filter(booking__apartment__manager=request.user, payment_date__range=(
            start_month, end_date)).select_related('booking__apartment')

        # manager_related_notifications = []
        # for notification in all_notifications:
        #     if notification.booking:
        #         if notification.booking.apartment.manager == request.user:
        #             manager_related_notifications.append(notification)
        #     elif notification.cleaning:
        #         if notification.cleaning.booking and notification.cleaning.booking.apartment.manager == request.user:
        #             manager_related_notifications.append(notification)
        #     elif notification.payment:
        #         related_apartment = notification.payment.booking.apartment if notification.payment.booking else notification.payment.apartment
        #         if related_apartment and related_apartment.manager == request.user:
        #             manager_related_notifications.append(notification)

        # Group notifications by period
        # today_notifications = [
        #     n for n in manager_related_notifications if n.date == today]
        # next_week_notifications = [
        #     n for n in manager_related_notifications if today < n.date <= next_week]
        # next_month_notifications = [
        #     n for n in manager_related_notifications if next_week < n.date <= next_month]
    else:
        # Fetch all properties
        apartments = Apartment.objects.filter(
            Q(end_date__gte=start_month) | Q(end_date__isnull=True)).order_by('name')
        bookings = Booking.objects.all()
        cleanings = Cleaning.objects.filter(date__range=(
            start_month, end_date)).select_related('booking__apartment')
        payments = Payment.objects.filter(payment_date__range=(start_month, end_date)
                                          ).select_related('booking__apartment')

        # Group notifications by period
        # today_notifications = [n for n in all_notifications if n.date == today]
        # next_week_notifications = [
        #     n for n in all_notifications if today < n.date <= next_week]
        # next_month_notifications = [
        #     n for n in all_notifications if next_week < n.date <= next_month]

    event_data = defaultdict(lambda: defaultdict(list))

    current_date = start_month
    while current_date <= end_date:
        for booking in bookings:
            if booking.start_date <= current_date <= booking.end_date:
                key = (booking.apartment.id, current_date)
                event_data[key]['booking'].append(booking)

        for cleaning in cleanings:
            if cleaning.booking and cleaning.date == current_date:
                key = (cleaning.booking.apartment.id, current_date)
                event_data[key]['cleaning'].append(cleaning)

        for payment in payments:
            if payment.payment_date == current_date:
                apartment_id = None
                if payment.booking:
                    apartment_id = payment.booking.apartment.id
                elif payment.apartment:
                    apartment_id = payment.apartment.id

                if apartment_id:  # Only append if an apartment_id exists
                    key = (apartment_id, current_date)
                    event_data[key]['payment'].append(payment)

        # Increment to the next day
        current_date += timedelta(days=1)

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
                    bookings_for_day = event_data.get(
                        (apartment.id, day), {}).get('booking', [])
                    cleanings_for_day = event_data.get(
                        (apartment.id, day), {}).get('cleaning', [])
                    payments_for_day = event_data.get(
                        (apartment.id, day), {}).get('payment', [])

                    day_data = {
                        'day': day,
                        'booking_ids': [booking.id for booking in bookings_for_day],
                        'tenants': [booking.tenant.full_name for booking in bookings_for_day],
                        'tenants_ids': [booking.tenant.id for booking in bookings_for_day],
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

    # apartments_data_str_keys = stringify_keys(apartments_data)
    # apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    apartments_data = dict(apartments_data)
    for apartment_id, apartment_data in apartments_data.items():
        apartment_data['months'] = dict(apartment_data['months'])

    context = {
        'apartments_data': apartments_data,
        'current_date': timezone.now(),
        # 'apartments_data_json': apartments_data_json,
        'months': months,
        'prev_page': prev_page,
        'next_page': next_page,
        'bookings': bookings,
        # 'today_notifications': today_notifications,
        # 'next_week_notifications': next_week_notifications,
        # 'next_month_notifications': next_month_notifications,
    }

    return render(request, 'index.html', context)


MODEL_MAP = {
    'user': User,
    'apartment': Apartment,
    'booking': Booking,
    'cleaning': Cleaning,
    'notification': Notification,
    'paymentmethod': PaymentMethod,
    'paymenttype': PaymenType,
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
            nested_fk, nested_m2m = get_related_fields(
                field.related_model, f"{fk_name}__")
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
                        operator = '' if field == "id" or '.id' in field or 'date' in field else '__icontains'
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
            if 'date' in field and operator in ['', '__gt', '__lt', '__gte', '__lte'] and not isinstance(value, date):
                value = parse_date(value)

            stack.append(Q(**{f"{field}{operator}": value}))

    return combine_stack(stack)


def parse_date(value):
    if isinstance(value, date):
        # If date_str is already a date object, return it as is
        return value
    # Define the possible date formats to check
    date_formats = ['%d/%m/%Y', '%d.%m.%Y', '%m/%d/%Y']

    for date_format in date_formats:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            pass

    raise ValueError(f"Invalid date format: {value}")


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


def handle_post_request(request, model, form_class):
    if 'edit' in request.POST:
        item_id = request.POST['id']
        if item_id:
            instance = model.objects.get(id=item_id)
            form = form_class(request.POST, instance=instance,
                              request=request, action='edit')
            if form.is_valid():
                form.save()
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field}: {error}")
        return redirect(request.path)
    elif 'add' in request.POST:
        form = form_class(request.POST, request=request)
        if form.is_valid():
            form.save()
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
        return redirect(request.path)
    elif 'delete' in request.POST:
        instance = model.objects.get(id=request.POST['id'])
        instance.delete()
        form = form_class()
        return redirect(request.path)


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
        item['links'] = original_obj.links

    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)

    # Get fields from the model's metadata
    model_fields = [
        (field_name, field_instance) for field_name, field_instance in form.fields.items()
        if isinstance(field_instance, CustomFieldMixin)]

    return render(
        request, template_name,
        {'items': items_on_page, "items_json": items_json, 'search_query': search_query, 'model_fields': model_fields})


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
def notifications(request):
    page = int(request.GET.get('page', "0"))
    today = date.today()

    if request.method == 'POST':
        handle_post_request(request, Notification, NotificationForm)

    if page > 0:
        start_date = today + timedelta(days=30 * (page - 1))
    else:
        start_date = today - timedelta(days=30 * abs(page))

    end_date = start_date + timedelta(days=30)
    notifications = Notification.objects.filter(
        date__range=(start_date, end_date)).order_by('date').select_related(
        'cleaning', 'booking', "payment")

    grouped_notifications = defaultdict(list)

    # 3. Group notifications by date and type
    for notification in notifications:
        notification_date = notification.date.strftime('%b %d, %a')
        message = notification.message

        if message:
            if message.startswith('Cleaning'):
                notification_type = 'cleaning'
            elif message.startswith('Payment'):
                notification_type = 'payment'
            elif message.startswith('Start Booking'):
                notification_type = 'checkin'
            elif message.startswith('End Booking'):
                notification_type = 'checkout'
            else:
                notification_type = 'other'

            grouped_notifications[notification_date].append(
                (notification_type, notification))

    grouped_notifications_dict = {}
    for date2, notification_list in grouped_notifications.items():
        grouped_notifications_dict[date2] = notification_list

    form = NotificationForm(request=request)
    model_fields = [
        (field_name, field_instance) for field_name, field_instance in form.fields.items()
        if isinstance(field_instance, CustomFieldMixin)]

    items_json_data = serializers.serialize('json', notifications)

    # Convert the serialized data to a Python list of dictionaries
    data_list = json.loads(items_json_data)

    # Extract the 'fields' from each item in the list
    items_list = [{'id': item['pk'], **item['fields']} for item in data_list]

    for item, original_obj in zip(items_list, notifications):
        item['links'] = original_obj.links

    # Convert the list back to a JSON string for passing to the template
    items_json = json.dumps(items_list)

    context = {
        "grouped_notifications": grouped_notifications_dict,
        "model": "notifications",
        'prev_page': page - 1,
        'next_page': page + 1,
        'items_json': items_json,
        "model_fields": model_fields
    }

    return render(request, 'notifications.html', context)
    # return generic_view(request, 'notification', NotificationForm, 'notifications.html')


@user_has_role('Admin')
def payment_methods(request):
    return generic_view(request, 'paymentmethod', PaymentMethodForm, 'payments_methods.html')


@user_has_role('Admin')
def payment_types(request):
    return generic_view(request, 'paymenttype', PaymentTypeForm, 'payments_types.html')


@user_has_role('Admin')
def payments(request):
    return generic_view(request, 'payment', PaymentForm, 'payments.html')


def custom_login_view(request):
    if request.method == 'POST':
        form = CustomUserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            # Check user role
            if user.role == 'Cleaner':
                return redirect('/cleanings')
            elif user.role in ['Admin', 'Manager']:
                return redirect('/')

            # Check if "Remember Me" was not ticked
            if not form.cleaned_data.get('remember_me'):
                # Set session to expire when user closes browser
                request.session.set_expiry(0)

            return redirect('/')
    else:
        form = CustomUserLoginForm()
    return render(request, 'login.html', {'form': form})


class CustomLogoutView(LogoutView):
    # This will redirect to the URL pattern named 'login'
    next_page = reverse_lazy('login')

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        # Clear the session
        request.session.flush()
        return response


def get_payments_for_month(year, month):
    return Payment.objects.filter(
        payment_date__year=year,
        payment_date__month=month
    ).order_by(
        Case(
            When(payment_status="Pending", then=0),
            When(payment_status="Completed", then=1),
        ),
        'payment_date'
    )


def assign_color_classes(payments, in_colors, out_colors):
    for payment in payments:
        if payment.payment_type:
            payment_type_id = payment.payment_type.id

            if payment.payment_type.type == "In":
                payment.color_class = in_colors[payment_type_id % len(
                    in_colors)]
            else:
                payment.color_class = out_colors[payment_type_id % len(
                    out_colors)]
        else:
            payment.color_class = "text-gray-500"


def aggregate_data(payments):
    income = sum(payment.amount for payment in payments if payment.payment_type.type ==
                 'In' and payment.payment_status == 'Completed')
    outcome = sum(payment.amount for payment in payments if payment.payment_type.type ==
                  'Out' and payment.payment_status == 'Completed')
    pending_income = sum(payment.amount for payment in payments if payment.payment_type.type ==
                         'In' and payment.payment_status == 'Pending')
    pending_outcome = sum(payment.amount for payment in payments if payment.payment_type.type ==
                          'Out' and payment.payment_status == 'Pending')

    return income, outcome, pending_income, pending_outcome


def aggregate_summary(payment_list):
    total_income = Decimal('0.00')
    total_expense = Decimal('0.00')
    total_pending_income = Decimal('0.00')
    total_pending_outcome = Decimal('0.00')

    for payment in payment_list:
        if payment.payment_type.type == 'In' and payment.payment_status == 'Completed':
            total_income += payment.amount
        elif payment.payment_type.type == 'Out' and payment.payment_status == 'Completed':
            total_expense += payment.amount
        elif payment.payment_type.type == 'In' and payment.payment_status == 'Pending':
            total_pending_income += payment.amount
        elif payment.payment_type.type == 'Out' and payment.payment_status == 'Pending':
            total_pending_outcome += payment.amount

    total_profit = total_income - total_expense
    total_pending_profit = total_pending_income - total_pending_outcome

    return {
        'total_income': total_income,
        'total_expense': total_expense,
        'total_profit': total_profit,
        'total_pending_income': total_pending_income,
        'total_pending_outcome': total_pending_outcome,
        'total_pending_profit': total_pending_profit
    }


@user_has_role('Admin')
def paymentReport(request):
    apartment_filter = request.GET.get('apartment', None)
    start_date_str = request.GET.get('start_date', None)
    end_date_str = request.GET.get('end_date', None)
    payment_type_filter = request.GET.get('payment_type', None)
    apartment_type_filter = request.GET.get('apartment_type', None)
    payment_status_filter = request.GET.get('payment_status', None)

    # Convert the date strings to datetime objects or set to the start and end of the current year
    if start_date_str:
        start_date = datetime.strptime(start_date_str, '%m/%d/%Y')
    else:
        start_date = datetime(datetime.now().year, 1, 1)

    if end_date_str:
        end_date = datetime.strptime(end_date_str, '%m/%d/%Y')
    else:
        end_date = datetime(datetime.now().year, 12, 31)

    # Query for fetching apartments
    apartments = Apartment.objects.all().order_by(
        'name').values_list('name', flat=True)
    apartment_types = Apartment.TYPES
    payment_types = PaymenType.objects.all().values_list('name', flat=True)

    payments_within_range = Payment.objects.filter(
        payment_date__range=[start_date, end_date]
    ).select_related(
        'payment_type', 'payment_method', 'bank'
    ).order_by(
        'payment_date'
    )

    # Modify your payment query
    if apartment_filter:
        payments_within_range = [
            payment for payment in payments_within_range
            if (payment.booking and payment.booking.apartment.name == apartment_filter)
            or (payment.apartment and payment.apartment.name == apartment_filter)]

    if apartment_type_filter:
        payments_within_range = [payment for payment in payments_within_range if (
            (payment.booking and payment.booking.apartment.apartment_type == apartment_type_filter) or
            (payment.apartment and payment.apartment.apartment_type ==
             apartment_type_filter)
        )]

    if payment_type_filter:
        payments_within_range = [payment for payment in payments_within_range
                                 if payment.payment_type.name == payment_type_filter]

    if payment_status_filter:
        payments_within_range = [payment for payment in payments_within_range
                                 if payment.payment_status == payment_status_filter]

    in_colors = [
        "text-emerald-300",
        "text-emerald-400", "text-emerald-500", "text-emerald-600",
        "text-emerald-700", "text-emerald-800", "text-emerald-900"
    ]

    out_colors = [
        "text-rose-300",
        "text-rose-400", "text-rose-500", "text-rose-600",
        "text-rose-700", "text-rose-800", "text-rose-900"
    ]

    current_month = start_date.replace(day=1)

    monthly_data = []

    # Iterate through each month from the start date to the end date
    while current_month <= end_date:
        # Filter payments for the specific month
        payments_for_month = [
            payment for payment in payments_within_range
            if payment.payment_date.year == current_month.year and payment.payment_date.month == current_month.month
        ]
        assign_color_classes(payments_for_month, in_colors, out_colors)

        income, outcome, pending_income, pending_outcome = aggregate_data(
            payments_for_month)

        profit = income - outcome
        pending_profit = pending_income - pending_outcome

        monthly_data.append({
            "month_name": calendar.month_name[current_month.month] + " " + str(current_month.year),
            "payments": payments_for_month,
            "income": income,
            "outcome": outcome,
            "profit": profit,
            "pending_profit": pending_profit,
            "pending_income": pending_income,
            "pending_outcome": pending_outcome
        })

        # Move to the next month
        if current_month.month == 12:
            current_month = current_month.replace(
                year=current_month.year+1, month=1)
        else:
            current_month = current_month.replace(month=current_month.month+1)

    summary = aggregate_summary(payments_within_range)

    context = {
        'start_date': start_date.strftime('%m/%d/%Y'),
        'end_date': end_date.strftime('%m/%d/%Y'),
        'summary': summary,
        'monthly_data': monthly_data,
        'apartments': apartments,
        'apartment_types': apartment_types,
        'payment_types': payment_types,
        'current_apartment': apartment_filter,
        'current_apartment_type': apartment_type_filter,
        'current_payment_status': payment_status_filter,
        'current_payment_type': payment_type_filter,
    }

    return render(request, 'payment_report.html', context)


@user_has_role('Admin')
def apartment(request):
    apartment_id = request.GET.get('apartment.id', 22)
    year = request.GET.get('year')
    apartments = Apartment.objects.all().order_by('name').values_list('id', 'name')

    try:
        apartment_id = int(apartment_id)
    except ValueError:
        return HttpResponseBadRequest("Invalid request. Apartment ID must be an integer.")

    today = date.today()

    if year:
        try:
            year = int(year)
        except ValueError:
            return HttpResponseBadRequest("Invalid request. Year must be an integer.")
        start_date = date(year, 1, 1)
    else:
        year = today.year
        start_date = date(year, today.month, 1)

    prev_year = year - 1
    next_year = year + 1

    end_date = start_date.replace(year=start_date.year + 1) - timedelta(days=1)

    # Fetch data for the specified apartment
    apartment = Apartment.objects.get(id=apartment_id)
    bookings = Booking.objects.filter(
        start_date__lte=end_date, end_date__gte=start_date, apartment=apartment)

    cleanings = Cleaning.objects.filter(date__range=(
        start_date, end_date), booking__apartment=apartment)
    payments = Payment.objects.filter(
        Q(booking__apartment=apartment) | Q(apartment=apartment),
        payment_date__range=(start_date, end_date)
    )

    event_data = defaultdict(lambda: defaultdict(list))

    current_date = start_date
    while current_date <= end_date:
        for booking in bookings:
            if booking.start_date <= current_date <= booking.end_date:
                key = (booking.apartment.id, current_date)
                event_data[key]['booking'].append(booking)

        for cleaning in cleanings:
            if cleaning.booking and cleaning.date == current_date:
                key = (cleaning.booking.apartment.id, current_date)
                event_data[key]['cleaning'].append(cleaning)

        for payment in payments:
            if payment.payment_date == current_date:
                apartment_id = None
                if payment.booking:
                    apartment_id = payment.booking.apartment.id
                elif payment.apartment:
                    apartment_id = payment.apartment.id

                if apartment_id:  # Only append if an apartment_id exists
                    key = (apartment_id, current_date)
                    event_data[key]['payment'].append(payment)

        # Increment to the next day
        current_date += timedelta(days=1)

    apartments_data = {}
    apartment_data = {
        'apartment': apartment,
        'months': defaultdict(list),
        'occupancy': {},  # Initialize occupancy data
        'profit': {}  # Initialize profit data
    }

    min_month = 1
    max_month = 13
    if apartment.start_date and apartment.start_date.date() > start_date and apartment.start_date.date() < end_date:
        min_month = apartment.start_date.month
        
    if apartment.end_date and apartment.end_date.date() < end_date and apartment.end_date.date() > start_date:
        max_month = apartment.end_date.month

    num_month = max_month - min_month

    for i in range(12):
        # Calculate the month, wrapping around to the next year if needed
        month_date = start_date + relativedelta(months=i)
        month_occupancy = 0
        month_income = 0
        month_outcome = 0
        month_pending_income = 0
        month_pending_outcome = 0

        # Generate weeks using the month_date
        weeks = generate_weeks(month_date)
        weeks_data = []
        for week in weeks:
            week_data = []
            for day in week:
                bookings_for_day = event_data.get(
                    (apartment.id, day), {}).get('booking', [])
                cleanings_for_day = event_data.get(
                    (apartment.id, day), {}).get('cleaning', [])
                payments_for_day = event_data.get(
                    (apartment.id, day), {}).get('payment', [])

                day_data = {
                    'day': day,
                    'booking_ids': [booking.id for booking in bookings_for_day],
                    'tenants': [booking.tenant.full_name for booking in bookings_for_day],
                    'tenants_ids': [booking.tenant.id for booking in bookings_for_day],
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
                # Calculate occupancy for the day
                if day.month == month_date.month and len(bookings_for_day) > 0:
                    month_occupancy += 1

                # Calculate profit for the day
                if day.month == month_date.month:
                    income, outcome, pending_income, pending_outcome = aggregate_data(
                        payments_for_day)
                    month_income += income
                    month_outcome += outcome
                    month_pending_income += pending_income
                    month_pending_outcome += pending_outcome

            weeks_data.append(week_data)

        total_days_in_month = (
            month_date + relativedelta(months=1) - relativedelta(days=1)).day
        occupancy = round((month_occupancy / total_days_in_month)*100)

        apartment_data['months'][month_date] = {
            'weeks': weeks_data,
            'month_occupancy': occupancy,
            'month_total_profit': round(month_income + month_pending_income - month_outcome - month_pending_outcome),
            'month_pending_profit': round(month_pending_income - month_pending_outcome),
            'month_sure_profit': round(month_income - month_outcome),
            'month_outcome': round(month_outcome),
            'month_pending_outcome': round(month_pending_outcome),
            'month_income': round(month_income),
            'month_pending_income':  round(month_pending_income),

        }

        apartment_data["total_occupancy"] = round(sum(month_data.get('month_occupancy', 0)
                                                      for month_data in apartment_data['months'].values()) / num_month)
        apartment_data["profit"] = sum(month_data.get('month_sure_profit', 0)
                                       for month_data in apartment_data['months'].values())
        apartment_data["pending_profit"] = sum(month_data.get('month_pending_profit', 0)
                                               for month_data in apartment_data['months'].values())
        apartment_data["income"] = sum(month_data.get('month_income', 0)
                                       for month_data in apartment_data['months'].values())
        apartment_data["pending_income"] = sum(month_data.get('month_pending_income', 0)
                                               for month_data in apartment_data['months'].values())
        apartment_data["outcome"] = sum(month_data.get('month_outcome', 0)
                                        for month_data in apartment_data['months'].values())
        apartment_data["pending_outcome"] = sum(month_data.get('month_pending_outcome', 0)
                                                for month_data in apartment_data['months'].values())

        apartments_data[apartment.id] = apartment_data

    apartments_data_str_keys = stringify_keys(apartments_data)
    apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    apartments_data = dict(apartments_data)
    for apartment_id, apartment_data in apartments_data.items():
        apartment_data['months'] = dict(apartment_data['months'])

    context = {
        'apartments_data': apartments_data,
        'apartments': apartments,
        'apartment_id': apartment_id,
        'current_date': timezone.now(),
        'apartments_data_json': apartments_data_json,
        'prev_year': prev_year,
        'current_year': today.year,
        'next_year': next_year,
        'bookings': bookings,
    }

    return render(request, 'apartment.html', context)


def calculate_unique_booked_days(bookings, month_start, month_end):

    # Create a set to store all booked days
    booked_days = set()
    for booking in bookings:
        # Adjust the start and end dates of the booking to be within the month
        booking_start = max(booking.start_date, month_start)
        booking_end = min(booking.end_date, month_end)

        # Add each day of the booking to the set, excluding the end date
        current_date = booking_start
        while current_date <= booking_end:  # Note the strict less than
            booked_days.add(current_date)
            current_date += timedelta(days=1)

    # The number of unique booked days is the size of the set
    return len(booked_days)


def calculate_total_booked_days(bookings, month_start, month_end):
    # Dictionary to store booked days and total count for each apartment
    booked_data_by_apartment = defaultdict(
        lambda: {"booked_dates": set(), "total_days": 0})
    totalnumber = 0
    for booking in bookings:
        # Adjust the start and end dates of the booking to be within the month
        booking_start = max(booking.start_date, month_start)
        booking_end = min(booking.end_date, month_end)

        # Calculate the number of days booked for this booking and store each date

        current_date = booking_start
        while current_date <= booking_end:

            if current_date not in booked_data_by_apartment[booking.apartment.name]["booked_dates"]:
                booked_data_by_apartment[booking.apartment.name]["total_days"] += 1
                totalnumber += 1

            booked_data_by_apartment[booking.apartment.name]["booked_dates"].add(
                current_date)

            current_date += timedelta(days=1)

    return totalnumber


@user_has_role('Admin')
def apartments_analytics(request):
    apartment_ids = request.GET.get('ids', "")  # 1,2,5,10
    apartment_type = request.GET.get('type', "")  # In Ownership, In Management
    rooms = request.GET.get('rooms', "")
    year = int(request.GET.get('year', date.today().year))
    apartments = Apartment.objects.all().order_by('name').values_list('id', 'name')

    start_date = date(year, 1, 1)
    today = date.today()
    year_range = list(range(2020, today.year + 3))

    end_date = start_date.replace(year=start_date.year + 1) - timedelta(days=1)
    queryset = Apartment.objects.all().order_by('name')

    # Apply filters based on the criteria
    if apartment_type:
        queryset = queryset.filter(apartment_type=apartment_type)

    if rooms:
        queryset = queryset.filter(bedrooms=rooms)

    selected_apartments = queryset
    if apartment_ids:
        if apartment_ids == '-1':
            # Show all apartments if ids is -1
            selected_apartments = queryset
        else:
            # If apartment_ids are specified, filter based on the list of IDs
            apartment_id_list = [int(id) for id in apartment_ids.split(',')]
            selected_apartments = queryset.filter(id__in=apartment_id_list)

    # Determine if specific apartments are selected

    bookings = Booking.objects.filter(
        Q(start_date__lte=end_date) & Q(end_date__gte=start_date))
    payments = Payment.objects.filter(
        payment_date__range=(start_date, end_date))

    isFilter = any([apartment_ids, apartment_type, rooms])

    if isFilter:
        bookings = bookings.filter(apartment__in=selected_apartments)
        payments = payments.filter(
            Q(apartment__in=selected_apartments) | Q(
                booking__apartment__in=selected_apartments)
        )

    apartments_data = {}
    apartments_month_data = []

    year_occupancy = 0
    year_income = 0
    year_outcome = 0
    year_pending_income = 0
    year_pending_outcome = 0
    year_total_profit = 0
    year_sure_profit = 0
    year_avg_profit = 0
    year_avg_income = 0
    year_avg_outcome = 0

    num_apartments = len(selected_apartments)

    for i in range(12):
        month_date = start_date + relativedelta(months=i)
        next_month_date = month_date + \
            relativedelta(months=1) - relativedelta(days=1)

        # Filter bookings and payments for the current month
        bookings_for_month = bookings.filter(
            start_date__lte=next_month_date, end_date__gte=month_date)
        payments_for_month = payments.filter(
            payment_date__gte=month_date, payment_date__lte=next_month_date)

        month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
            payments_for_month)
        total_days_in_month = next_month_date.day

        total_booked_days = calculate_total_booked_days(
            bookings_for_month, month_date, next_month_date)

        month_sure_profit = month_income - month_outcome
        month_total_profit = month_income + month_pending_income - \
            month_outcome - month_pending_outcome

        target_apartments = selected_apartments.filter(
            Q(start_date__lte=next_month_date) &
            (Q(end_date__gte=month_date) | Q(end_date__isnull=True))
        )

        apartment_names = target_apartments.values_list(
            'name', flat=True)
        num_apartments = target_apartments.count()

        if num_apartments > 0:
            month_avg_income = round(month_income / num_apartments)
            month_avg_profit = round(
                (month_income + month_pending_income - month_outcome - month_pending_outcome) / num_apartments)
            month_avg_outcome = round(month_outcome / num_apartments)
            month_occupancy = round(
                (total_booked_days / (total_days_in_month * num_apartments)) * 100)
        else:
            month_occupancy = 0
            month_avg_income = 0
            month_avg_outcome = 0
            month_avg_profit = 0

        apartments_month_data.append({
            'date': month_date.strftime('%b'),
            'month_income': round(month_income),
            'month_outcome': round(month_outcome),
            'month_pending_income': round(month_pending_income),
            'month_pending_outcome': round(month_pending_outcome),
            'month_sure_profit': round(month_sure_profit),
            'month_expectied_proift': round(month_total_profit),
            'month_occupancy': month_occupancy,
            'month_avg_profit': month_avg_profit,
            'month_avg_income': month_avg_income,
            'month_avg_outcome': month_avg_outcome,
            'month_apartments_length': num_apartments,
            'apartment_names': apartment_names,
            'month_total_booked_days': total_booked_days,
            'month_total_days': total_days_in_month * num_apartments,
        })

        year_income += month_income
        year_outcome += month_outcome
        year_pending_income += month_pending_income
        year_pending_outcome += month_pending_outcome
        year_total_profit += month_total_profit
        year_sure_profit += month_sure_profit
        year_occupancy += month_occupancy
        year_avg_outcome += month_avg_outcome
        year_avg_income += month_avg_income
        year_avg_profit += month_avg_profit

    apartments_data["apartments_month_data"] = apartments_month_data
    apartments_data["year_income"] = round(year_income)
    apartments_data["year_outcome"] = round(year_outcome)
    apartments_data["year_pending_income"] = round(year_pending_income)
    apartments_data["year_pending_outcome"] = round(year_pending_outcome)
    apartments_data["year_expectied_proift"] = round(year_total_profit)
    apartments_data["year_sure_profit"] = round(year_sure_profit)
    apartments_data["year_avg_profit"] = round(year_avg_profit/12)
    apartments_data["year_avg_income"] = round(year_avg_income/12)
    apartments_data["year_avg_outcome"] = round(year_avg_outcome/12)
    apartments_data["year_occupancy"] = round(year_occupancy / 12)

    aprat_len = apartments_data["apartments_month_data"][-1]["month_apartments_length"]
    selected_apartments_data = []

    if isFilter:
        for apartment in selected_apartments:
            selected_apartment = {
                'apartment': apartment,
                'month_data': [],
                "year_income": 0,
                "year_outcome": 0,
                "year_pending_income": 0,
                "year_pending_outcome": 0,
                "year_total_profit": 0,
                "year_sure_profit": 0,
                "year_occupancy": 0,
                "year_avg_profit": 0,
                "year_avg_income": 0,
                "year_avg_outcome": 0,
            }

            if apartment.start_date and apartment.start_date.date() >= end_date:
                continue
            if apartment.end_date and apartment.end_date.date() <= start_date:
                continue

            # Define the minimum and maximum months based on start_date, start_date_apartment, end_date, and end_date_apartment
            min_month = 1
            max_month = 13
            if apartment.start_date and apartment.start_date.date() > start_date and apartment.start_date.date() < end_date:
                min_month = apartment.start_date.month
                
            if apartment.end_date and apartment.end_date.date() < end_date and apartment.end_date.date() > start_date:
                max_month = apartment.end_date.month

            num_month = max_month - min_month
            print(num_month, "num_month")
          
            for i in range(12):
                month_date = start_date + relativedelta(months=i)
                next_month_date = month_date + \
                    relativedelta(months=1) - relativedelta(days=1)

                # Filter bookings and payments for the current month and selected apartment
                bookings_for_month = bookings.filter(start_date__lte=next_month_date,
                                                     end_date__gte=month_date, apartment=apartment)
                payments_for_month = payments.filter(
                    Q(apartment=apartment) | Q(booking__apartment=apartment),
                    payment_date__gte=month_date,
                    payment_date__lte=next_month_date
                )

                month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
                    payments_for_month)
                total_days_in_month = next_month_date.day

                total_booked_days = calculate_unique_booked_days(
                    bookings_for_month, month_date, next_month_date)

                month_occupancy = round(
                    (total_booked_days / (total_days_in_month)) * 100)

                month_sure_profit = month_income - month_outcome
                month_total_profit = month_income + month_pending_income - \
                    month_outcome - month_pending_outcome

                # You can calculate more metrics here

                selected_apartment['month_data'].append({
                    'month_date': month_date.strftime('%b'),
                    'month_income': round(month_income),
                    'month_outcome': round(month_outcome),
                    'month_pending_income': round(month_pending_income),
                    'month_pending_outcome': round(month_pending_outcome),
                    'month_total_profit': round(month_total_profit),
                    'month_sure_profit': round(month_sure_profit),
                    'month_occupancy': round(month_occupancy),
                    'total_days_in_month': total_days_in_month,
                    'total_booked_days': total_booked_days,
                })
                selected_apartment["year_income"] += month_income
                selected_apartment["year_outcome"] += month_outcome
                selected_apartment["year_pending_income"] += month_pending_income
                selected_apartment["year_pending_outcome"] += month_pending_outcome
                selected_apartment["year_total_profit"] += month_total_profit
                selected_apartment["year_sure_profit"] += month_sure_profit
                selected_apartment["year_occupancy"] += month_occupancy

            selected_apartment["year_avg_profit"] = round(
                selected_apartment["year_total_profit"]/num_month)
            selected_apartment["year_avg_income"] = round(
                (selected_apartment["year_income"] + selected_apartment["year_pending_income"])/num_month)
            selected_apartment["year_avg_outcome"] = round(
                (selected_apartment["year_outcome"] + selected_apartment["year_pending_outcome"])/num_month)
            selected_apartments_data.append(selected_apartment)
            selected_apartment["year_occupancy"] = round(
                selected_apartment["year_occupancy"]/num_month)

    apartments_data["selected_apartments_data"] = selected_apartments_data
    apartments_data_str_keys = stringify_keys(apartments_data)
    apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

    context = {
        'apartments_data': apartments_data,
        'apartments': apartments,
        'apartments_data_json': apartments_data_json,
        'apartment_ids': apartment_ids,
        'current_year': today.year,
        'year_range': year_range,
        'aprat_len': aprat_len,
        'year': year,
        'isFilter': isFilter
    }

    return render(request, 'apartments_analytics.html', context)

# def apartments_analytics(request):
#     apartment_ids = request.GET.get('ids', "")  # 1,2,5,10
#     apartment_type = request.GET.get('type', "")  # In Ownership, In Management
#     rooms = request.GET.get('rooms', "")
#     year = int(request.GET.get('year', date.today().year))
#     apartments = Apartment.objects.all().order_by('name').values_list('id', 'name')
#     apartments_len = len(apartments)

#     start_date = date(year, 1, 1)
#     today = date.today()
#     year_range = list(range(2020, today.year + 3))

#     end_date = start_date.replace(year=start_date.year + 1) - timedelta(days=1)
#     queryset = Apartment.objects.all()

#     # Apply filters based on the criteria
#     if apartment_type:
#         queryset = queryset.filter(apartment_type=apartment_type)

#     if rooms:
#         queryset = queryset.filter(bedrooms=rooms)

#     if apartment_ids:
#         if apartment_ids == '-1':
#             # Show all apartments if ids is -1
#             selected_apartments = queryset
#         else:
#             # If apartment_ids are specified, filter based on the list of IDs
#             apartment_id_list = apartment_ids.split(',')
#             queryset = queryset.filter(id__in=apartment_id_list)
#             selected_apartments = queryset
#     else:
#         # Show no apartments if ids are not set
#         selected_apartments = []

#     # Getting bookings for the selected period
#     bookings = Booking.objects.filter(
#         Q(start_date__lte=end_date) & Q(end_date__gte=start_date)
#     )

#     # Getting payments for the selected period
#     payments = Payment.objects.filter(
#         payment_date__range=(start_date, end_date))

#     apartments_data = {}
#     apartments_month_data = []

#     # calculate avg monthly and yearly income, outcome, profit, occupancy for all apartments
#     year_occupancy = 0
#     year_income = 0
#     year_outcome = 0
#     year_pending_income = 0
#     year_pending_outcome = 0
#     year_total_profit = 0
#     year_sure_profit = 0
#     year_avg_profit = 0
#     year_avg_income = 0
#     year_avg_outcome = 0
#     for i in range(12):
#         month_date = start_date + relativedelta(months=i)
#         next_month_date = month_date + \
#             relativedelta(months=1) - relativedelta(days=1)

#         # Filter bookings and payments for the current month
#         bookings_for_month = bookings.filter(
#             start_date__lte=next_month_date, end_date__gte=month_date)
#         payments_for_month = payments.filter(
#             payment_date__gte=month_date, payment_date__lte=next_month_date)

#         month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
#             payments_for_month)
#         total_days_in_month = next_month_date.day
#         total_booked_days = calculate_total_booked_days(
#             bookings_for_month, month_date, next_month_date)

#         month_sure_profit = month_income - month_outcome
#         month_total_profit = month_income + month_pending_income - \
#             month_outcome - month_pending_outcome

#         created_apartments_len = len(
#             apartments.filter(created_at__lte=next_month_date))

#         # if created_apartments_len > 0:
#         month_occupancy = round(
#             (total_booked_days / (total_days_in_month * apartments_len)) * 100)
#         month_avg_outcome = round(month_outcome / apartments_len)
#         month_avg_income = round(month_income / apartments_len)
#         month_avg_profit = round(
#             (month_income + month_pending_income - month_outcome - month_pending_outcome) / apartments_len)
#         # else:
#         # month_occupancy = 0
#         # month_avg_outcome = 0
#         # month_avg_income = 0
#         # month_avg_profit = 0

#         apartments_month_data.append({
#             'date': month_date.strftime('%b'),
#             'month_income': round(month_income),
#             'month_outcome': round(month_outcome),
#             'month_pending_income': round(month_pending_income),
#             'month_pending_outcome': round(month_pending_outcome),
#             'month_sure_profit': round(month_sure_profit),
#             'month_expectied_proift': round(month_total_profit),
#             'month_occupancy': month_occupancy,
#             'month_avg_profit': month_avg_profit,
#             'month_avg_income': month_avg_income,
#             'month_avg_outcome': month_avg_outcome,
#             'month_apartments_length': created_apartments_len,
#         })

#         year_income += month_income
#         year_outcome += month_outcome
#         year_pending_income += month_pending_income
#         year_pending_outcome += month_pending_outcome
#         year_total_profit += month_total_profit
#         year_sure_profit += month_sure_profit
#         year_occupancy += month_occupancy
#         year_avg_outcome += month_avg_outcome
#         year_avg_income += month_avg_income
#         year_avg_profit += month_avg_profit

#     apartments_data["apartments_month_data"] = apartments_month_data
#     apartments_data["year_income"] = round(year_income)
#     apartments_data["year_outcome"] = round(year_outcome)
#     apartments_data["year_pending_income"] = round(year_pending_income)
#     apartments_data["year_pending_outcome"] = round(year_pending_outcome)
#     apartments_data["year_expectied_proift"] = round(year_total_profit)
#     apartments_data["year_sure_profit"] = round(year_sure_profit)
#     apartments_data["year_avg_profit"] = round(
#         year_total_profit/12/apartments_len)
#     apartments_data["year_avg_income"] = round(
#         (year_income + year_pending_income)/12/apartments_len)
#     apartments_data["year_avg_outcome"] = round(
#         (year_outcome + year_pending_outcome)/12/apartments_len)
#     apartments_data["year_occupancy"] = round(year_occupancy / 12)

#     selected_apartments_data = []

#     for apartment in selected_apartments:
#         selected_apartment = {
#             'apartment': apartment,
#             'month_data': [],
#             "year_income": 0,
#             "year_outcome": 0,
#             "year_pending_income": 0,
#             "year_pending_outcome": 0,
#             "year_total_profit": 0,
#             "year_sure_profit": 0,
#             "year_occupancy": 0,
#             "year_avg_profit": 0,
#             "year_avg_income": 0,
#             "year_avg_outcome": 0,
#         }

#         for i in range(12):
#             month_date = start_date + relativedelta(months=i)
#             next_month_date = month_date + \
#                 relativedelta(months=1) - relativedelta(days=1)

#             # Filter bookings and payments for the current month and selected apartment
#             bookings_for_month = bookings.filter(start_date__lte=next_month_date,
#                                                  end_date__gte=month_date, apartment=apartment)
#             payments_for_month = payments.filter(
#                 Q(apartment=apartment) | Q(booking__apartment=apartment),
#                 payment_date__gte=month_date,
#                 payment_date__lte=next_month_date
#             )

#             month_income, month_outcome, month_pending_income, month_pending_outcome = aggregate_data(
#                 payments_for_month)
#             total_days_in_month = next_month_date.day

#             total_booked_days = calculate_unique_booked_days(
#                 bookings_for_month, month_date, next_month_date)

#             month_occupancy = round(
#                 (total_booked_days / (total_days_in_month)) * 100)

#             month_sure_profit = month_income - month_outcome
#             month_total_profit = month_income + month_pending_income - \
#                 month_outcome - month_pending_outcome

#             # You can calculate more metrics here

#             selected_apartment['month_data'].append({
#                 'month_date': month_date.strftime('%b'),
#                 'month_income': round(month_income),
#                 'month_outcome': round(month_outcome),
#                 'month_pending_income': round(month_pending_income),
#                 'month_pending_outcome': round(month_pending_outcome),
#                 'month_total_profit': round(month_total_profit),
#                 'month_sure_profit': round(month_sure_profit),
#                 'month_occupancy': round(month_occupancy),
#                 'total_days_in_month': total_days_in_month,
#                 'total_booked_days': total_booked_days,
#             })
#             selected_apartment["year_income"] += month_income
#             selected_apartment["year_outcome"] += month_outcome
#             selected_apartment["year_pending_income"] += month_pending_income
#             selected_apartment["year_pending_outcome"] += month_pending_outcome
#             selected_apartment["year_total_profit"] += month_total_profit
#             selected_apartment["year_sure_profit"] += month_sure_profit
#             selected_apartment["year_occupancy"] += month_occupancy

#         selected_apartment["year_avg_profit"] = round(
#             selected_apartment["year_total_profit"]/12)
#         selected_apartment["year_avg_income"] = round(
#             (selected_apartment["year_income"] + selected_apartment["year_pending_income"])/12)
#         selected_apartment["year_avg_outcome"] = round(
#             (selected_apartment["year_outcome"] + selected_apartment["year_pending_outcome"])/12)
#         selected_apartments_data.append(selected_apartment)
#         selected_apartment["year_occupancy"] = round(
#             selected_apartment["year_occupancy"]/12)

#     apartments_data["selected_apartments_data"] = selected_apartments_data
#     apartments_data_str_keys = stringify_keys(apartments_data)
#     apartments_data_json = json.dumps(apartments_data_str_keys, default=str)

#     aprat_len = apartments_data["apartments_month_data"][-1]["month_apartments_length"]

#     context = {
#         'apartments_data': apartments_data,
#         'apartments': apartments,
#         'apartments_data_json': apartments_data_json,
#         'apartment_ids': apartment_ids,
#         'current_year': today.year,
#         'year_range': year_range,
#         'aprat_len': aprat_len,
#         'year': year
#     }

#     return render(request, 'apartments_analytics.html', context)


# def getMockData():
#     text_fields = [
#         {"tabLabel": "tenant_email", "value": "johnnypitt.ind@gmail.com"},
#         {"tabLabel": "tenant_full_name", "value": "Johnny Pitt"},
#         {"tabLabel": "tenant_phone", "value": "+1 234 567 8901"},
#         {"tabLabel": "owner_phone", "value": "+1 098 765 4321"},
#         {"tabLabel": "owner_full_name", "value": "Anna Smith"},
#         {"tabLabel": "apartment_state", "value": "California"},
#         {"tabLabel": "apartment_city", "value": "San Francisco"},
#         {"tabLabel": "apartment_street", "value": "Market Street"},
#         {"tabLabel": "apartment_number", "value": "123"},
#         {"tabLabel": "apartment_room", "value": "45A"},
#         {"tabLabel": "booking_start_date", "value": "2023-09-20"},
#         {"tabLabel": "booking_end_date", "value": "2023-09-30"},
#         {"tabLabel": "payments", "value": "1500.00"}
#     ]
#     # Convert to Text objects
#     text_tabs = [Text(tab_label=item["tabLabel"], value=item["value"]) for item in text_fields]

#     # Create a Tabs object containing only text_tabs
#     tabs = Tabs(text_tabs=text_tabs)

#     return tabs


# def get_user(access_token):
#     """Make request to the API to get the user information"""
#     # Determine user, account_id, base_url by calling OAuth::getUserInfo
#     # See https://developers.docusign.com/esign-rest-api/guides/authentication/user-info-endpoints

#     url = "https://account-d.docusign.com/oauth/userinfo"
#     auth = {"Authorization": "Bearer " + access_token}
#     response = requests.get(url, headers=auth).json()

#     return response


# def get_docusign_token():
#     api_client = ApiClient()
#     api_client.set_base_path(os.environ["DOCUSIGN_API_URL"])
#     private_key_content = os.environ["DOCUSIGN_PRIVATE_KEY"]
#     if '\\n' in private_key_content:
#         private_key_content = private_key_content.replace('\\n', '\n')

#     token = api_client.request_jwt_user_token(
#         client_id=os.environ["DOCUSIGN_INTEGRATION_KEY"],
#         user_id=os.environ["DOCUSIGN_USER_ID"],
#         oauth_host_name=os.environ["DOCUSIGN_AUTH_SERVER"],
#         private_key_bytes=private_key_content.encode('utf-8'),
#         expires_in=3600,
#         scopes=["signature"]
#     )

#     if token is None:
#         logger.error("Error while requesting token")
#     else:
#         api_client.set_default_header("Authorization", "Bearer " + token.access_token)
#         user = get_user(token.access_token)

#     return token.access_token


# def create_api_client(access_token):
#     """Create api client and construct API headers"""
#     api_client = ApiClient()
#     api_client.host = "https://demo.docusign.net/restapi"
#     api_client.set_default_header(header_name="Authorization", header_value=f"Bearer {access_token}")

#     return api_client


# def docusign(request):
#     token = get_docusign_token()
#     api_client = create_api_client(token)
#     envelope_api = EnvelopesApi(api_client)

#     tabs = getMockData()

#     tenant = TemplateRole(
#         email="andy.vaulin@gmail.com",
#         name="Andy Vaulin",
#         client_user_id="1244525",
#         role_name="Tenant",
#         tabs=tabs
#     )

#     # Create and send the envelope
#     envelope_definition = EnvelopeDefinition(
#         email_subject="Please sign this contract",
#         template_id=os.environ.get("DOCUSIGN_TEMPLATE_ID"),
#         template_roles=[tenant],
#         status="sent"
#     )
#     envelope_summary = envelope_api.create_envelope(
#         os.environ["DOCUSIGN_API_ACCOUNT_ID"],
#         envelope_definition=envelope_definition)

# event_notification = EventNotification(
#     url=os.environ.get('DOCUSIGN_CALLBACK_URL'),
#     logging_enabled='true',
#     require_acknowledgment='true',
#     envelope_events=[EnvelopeEvent(envelope_event_status_code='completed')],
#     recipient_events=[RecipientEvent(recipient_event_status_code='completed')]
# )

# envelope_definition = EnvelopeDefinition(
#     email_subject='Please sign this document',
#     template_id=os.environ.get('DOCUSIGN_TEMPLATE_ID'),
#     template_roles=[tenant_template_role],
#     status='sent',
#     event_notification=event_notification
# )

# recipient_view_request = RecipientViewRequest(
#     authentication_method="none",
#     client_user_id="1244525",
#     recipient_id="1",
#     return_url=f"http://localhost:8000/callback?envelopeId={envelope_summary.envelope_id}",
#     user_name=tenant.name,
#     email=tenant.email
# )

# results = envelope_api.create_recipient_view(
#     account_id=os.environ["DOCUSIGN_API_ACCOUNT_ID"],
#     envelope_id=envelope_summary.envelope_id,
#     recipient_view_request=recipient_view_request
# )

# return JsonResponse({'status': 'success', "envelope_id": envelope_summary.envelope_id,
#                      "envlope_uri": envelope_summary.uri})
#     return JsonResponse({'status': 'success', "envelope_id": envelope_summary.envelope_id,
#                         "envlope_uri": envelope_summary.uri, "redirect_url": results.url})


# def callback(request):
#     # Access query parameters from the GET request
#     envelope_id = request.GET.get('envelopeId', None)
#     event = request.GET.get('event', None)

#     if event == 'signing_complete' and envelope_id:
#         # Initialize your DocuSign API client
#         token = get_docusign_token()
#         api_client = create_api_client(token)
#         envelopes_api = EnvelopesApi(api_client)

#         # Fetch form data
#         account_id = os.environ["DOCUSIGN_API_ACCOUNT_ID"]
#         form_data_response = envelopes_api.get_form_data(account_id=account_id, envelope_id=envelope_id)

#         # Process and parse form data
#         recipient_form_data = form_data_response.recipient_form_data[0] if form_data_response.recipient_form_data else None
#         form_data_items = recipient_form_data.form_data if recipient_form_data and recipient_form_data.form_data else []

#         # Create a dictionary from form data items
#         form_data_dict = {item.name: item.value for item in form_data_items}

#         return JsonResponse({'status': 'success', 'formData': form_data_dict})

#     return JsonResponse({'status': 'failed'})
