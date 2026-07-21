from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.contrib import messages
from django.db.models import Q
import json

from ..models import User
from ..forms import CustomUserForm
from ..decorators import user_has_role
from .utils import DateEncoder


@user_has_role('Admin')
def users_view(request):
    """Dedicated users page with simple multi-field search."""
    search_query = request.GET.get('q', '').strip()
    page = request.GET.get('page', 1)

    if request.method == 'POST':
        if 'edit' in request.POST:
            item_id = request.POST.get('id')
            if item_id:
                try:
                    instance = User.objects.get(id=item_id)
                    form = CustomUserForm(
                        request.POST, instance=instance, request=request, action='edit'
                    )
                    if form.is_valid():
                        user = form.save(commit=False)
                        if not form.cleaned_data.get('password'):
                            user.password = instance.password
                        user.is_active = request.POST.get('is_active') == 'on'
                        user.save()
                    else:
                        for field, errors in form.errors.items():
                            for error in errors:
                                messages.error(request, f"{field}: {error}")
                except Exception as e:
                    messages.error(request, f"Error updating user: {e}")
            return redirect(request.path)

        elif 'add' in request.POST:
            form = CustomUserForm(request.POST, request=request)
            if form.is_valid():
                user = form.save(commit=False)
                user.is_active = request.POST.get('is_active') == 'on'
                user.save()
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field}: {error}")
            return redirect(request.path)

        elif 'delete' in request.POST:
            try:
                instance = User.objects.get(id=request.POST.get('id'))
                instance.delete()
            except Exception as e:
                messages.error(request, f"Error deleting user: {e}")
            return redirect(request.path)

    users = User.objects.all()

    if search_query:
        # Deep-link support: id=123 opens a specific user
        if search_query.startswith('id=') and search_query[3:].isdigit():
            users = users.filter(id=int(search_query[3:]))
        else:
            users = users.filter(
                Q(email__icontains=search_query) |
                Q(full_name__icontains=search_query) |
                Q(phone__icontains=search_query) |
                Q(role__icontains=search_query) |
                Q(notes__icontains=search_query) |
                Q(telegram_chat_id__icontains=search_query)
            )

    users = users.order_by('-id')

    paginator = Paginator(users, 30)
    items_on_page = paginator.get_page(page)

    items_list = []
    for user in items_on_page:
        items_list.append({
            'id': user.id,
            'email': user.email or '',
            'full_name': user.full_name or '',
            'phone': user.phone or '',
            'role': user.role or '',
            'notes': user.notes or '',
            'telegram_chat_id': user.telegram_chat_id or '',
            'is_active': user.is_active,
            'links': user.links,
        })

    context = {
        'items': items_on_page,
        'items_json': json.dumps(items_list, cls=DateEncoder),
        'search_query': search_query,
        'roles': [r[0] for r in User.ROLES],
        'title': 'Users',
        'model': 'users',
    }

    return render(request, 'users_custom.html', context)
