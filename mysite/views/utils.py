from django.shortcuts import redirect
from ..models import User, Apartment, Booking, Cleaning, Notification, PaymentMethod, Payment, PaymenType
from mysite.forms import CustomFieldMixin
from django.db.models import Q
from django.db import models
from datetime import datetime, date, timedelta
from collections import defaultdict
import calendar
from django.contrib import messages
from django.db.models import Case, When
from decimal import Decimal
from django.core.serializers.json import DjangoJSONEncoder


def handle_post_request(request, model, form_class):
    try:
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
    except Exception as e:
        messages.error(request, f"Error: {e}")
        return redirect(request.path)


def generate_weeks(month_start):
    """Generate weeks for a given month."""
    cal = calendar.Calendar(firstweekday=calendar.SUNDAY)
    return cal.monthdatescalendar(month_start.year, month_start.month)


def aggregate_data(payments):
    income = sum(payment.amount for payment in payments if payment.payment_type.type ==
                 'In' and (payment.payment_status == 'Completed' or payment.payment_status == 'Merged'))
    outcome = sum(payment.amount for payment in payments if payment.payment_type.type ==
                  'Out' and (payment.payment_status == 'Completed' or payment.payment_status == 'Merged'))
    pending_income = sum(payment.amount for payment in payments if payment.payment_type.type ==
                         'In' and payment.payment_status == 'Pending')
    pending_outcome = sum(payment.amount for payment in payments if payment.payment_type.type ==
                          'Out' and payment.payment_status == 'Pending')

    return income, outcome, pending_income, pending_outcome

def aggregate_profit_by_category(payments: list[Payment]):
    operational_in = sum(payment.amount for payment in payments if payment.payment_type.type ==
                 'In' and payment.payment_type.category == 'Operating')
    operational_out = sum(payment.amount for payment in payments if payment.payment_type.type ==
                  'Out' and payment.payment_type.category == 'Operating')
    none_operational_in = sum(payment.amount for payment in payments if payment.payment_type.type ==
                         'In' and payment.payment_type.category == 'None Operating')
    non_operational_out = sum(payment.amount for payment in payments if payment.payment_type.type ==
                          'Out' and payment.payment_type.category == 'None Operating')

    return operational_in, operational_out, none_operational_in, non_operational_out


def aggregate_summary(payment_list):
    total_income = Decimal('0.00')
    total_expense = Decimal('0.00')
    total_pending_income = Decimal('0.00')
    total_pending_outcome = Decimal('0.00')

    for payment in payment_list:
        if payment.payment_type.type == 'In' and (payment.payment_status == 'Completed' or payment.payment_status == 'Merged'):
            total_income += payment.amount
        elif payment.payment_type.type == 'Out' and (payment.payment_status == 'Completed' or payment.payment_status == 'Merged'):
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


def stringify_keys(d):
    """Recursively convert a dictionary's keys to strings."""
    if not isinstance(d, dict):
        return d
    return {str(k): stringify_keys(v) for k, v in d.items()}


class DateEncoder(DjangoJSONEncoder):
    def default(self, obj):
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


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
    date_formats = ['%B %d %Y']

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


def get_payments_for_month(year, month):
    return Payment.objects.filter(
        payment_date__year=year,
        payment_date__month=month
    ).order_by(
        Case(
            When(payment_status="Pending", then=0),
            When(payment_status="Completed", then=1),
            When(payment_status="Merged", then=1),
        ),
        'payment_date'
    )


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


def get_model_fields(form):
    fields = [
        (field_name, field_instance)
        for field_name, field_instance in form.fields.items()
        if isinstance(field_instance, CustomFieldMixin)
    ]

    # Sort fields by the 'order' attribute
    sorted_fields = sorted(fields, key=lambda item: item[1].order)
    return sorted_fields


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
