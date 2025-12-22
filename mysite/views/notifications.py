from django.shortcuts import render
from ..models import Notification
from mysite.forms import NotificationForm, CustomFieldMixin
import json
from django.core import serializers
from datetime import date, timedelta
from collections import defaultdict
from ..decorators import user_has_role
from .utils import handle_post_request
from django.db.models import Q


@user_has_role('Admin', 'Manager')
def notifications(request):
    page = int(request.GET.get('page', "0"))
    today = date.today()

    if request.method == 'POST':
        handle_post_request(request, Notification, NotificationForm)

    # Revised date range calculation
    if page == 0:
        # Current period (today and next 30 days)
        start_date = today
        end_date = today + timedelta(days=30)
    elif page > 0:
        # Future periods
        start_date = today + timedelta(days=30 * page)
        end_date = start_date + timedelta(days=30)
    else:
        # Past periods
        start_date = today + timedelta(days=30 * page)  # page is negative, so this subtracts
        end_date = start_date + timedelta(days=30)

    notifications = Notification.objects.filter(
        date__range=(start_date, end_date)).order_by('date').select_related(
        'cleaning', 'booking', "payment")

    # Filter notifications for managers based on their apartments
    if request.user.role == 'Manager':
        notifications = notifications.filter(
            Q(cleaning__booking__apartment__managers=request.user) |
            Q(booking__apartment__managers=request.user) |
            Q(payment__booking__apartment__managers=request.user) |
            Q(payment__apartment__managers=request.user)
        )

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
        'title': "notifications",
        "model_fields": model_fields
    }

    return render(request, 'notifications.html', context)
