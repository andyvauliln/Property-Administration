from django.shortcuts import render, redirect
from django.core.paginator import Paginator
from django.contrib import messages
from django.db.models import Q
from datetime import date
import json

from ..models import Apartment, User
from ..forms import ApartmentForm
from ..decorators import user_has_role
from .utils import DateEncoder, parse_query


@user_has_role('Admin', 'Manager')
def apartments_view(request):
    """
    Custom view for apartments with multi-manager checkbox support
    """
    search_query = request.GET.get('q', '')
    page = request.GET.get('page', 1)
    
    # Handle POST requests
    if request.method == 'POST':
        if 'edit' in request.POST:
            item_id = request.POST.get('id')
            if item_id:
                try:
                    instance = Apartment.objects.get(id=item_id)
                    form = ApartmentForm(request.POST, instance=instance, request=request, action='edit')
                    if form.is_valid():
                        apartment = form.save()
                        # Handle managers M2M field
                        manager_ids = request.POST.getlist('managers')
                        apartment.managers.set(manager_ids)
                    else:
                        for field, errors in form.errors.items():
                            for error in errors:
                                messages.error(request, f"{field}: {error}")
                except Exception as e:
                    messages.error(request, f"Error updating apartment: {e}")
            return redirect(request.path)
            
        elif 'add' in request.POST:
            form = ApartmentForm(request.POST, request=request)
            if form.is_valid():
                apartment = form.save()
                # Handle managers M2M field
                manager_ids = request.POST.getlist('managers')
                apartment.managers.set(manager_ids)
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field}: {error}")
            return redirect(request.path)
            
        elif 'delete' in request.POST:
            try:
                instance = Apartment.objects.get(id=request.POST.get('id'))
                instance.delete()
            except Exception as e:
                messages.error(request, f"Error deleting apartment: {e}")
            return redirect(request.path)
    
    # Get apartments with manager access control
    if request.user.role == 'Manager':
        apartments = Apartment.objects.filter(managers=request.user).prefetch_related('prices', 'managers')
    else:
        apartments = Apartment.objects.all().prefetch_related('prices', 'managers')
    
    # Apply search filter
    if search_query:
        # Check if query uses field=value syntax (like generic view)
        if '=' in search_query or '>' in search_query or '<' in search_query:
            try:
                q_objects = parse_query(Apartment, search_query)
                apartments = apartments.filter(q_objects)
            except Exception:
                # Fall back to simple search if parsing fails
                apartments = apartments.filter(
                    Q(name__icontains=search_query) |
                    Q(city__icontains=search_query) |
                    Q(state__icontains=search_query) |
                    Q(status__icontains=search_query)
                )
        else:
            apartments = apartments.filter(
                Q(name__icontains=search_query) |
                Q(city__icontains=search_query) |
                Q(state__icontains=search_query) |
                Q(status__icontains=search_query)
            )
    
    apartments = apartments.order_by('-id')
    
    # Pagination
    paginator = Paginator(apartments, 30)
    items_on_page = paginator.get_page(page)
    
    # Serialize apartments for JavaScript
    items_list = []
    for apt in items_on_page:
        item = {
            'id': apt.id,
            'name': apt.name,
            'web_link': apt.web_link,
            'building_n': apt.building_n,
            'street': apt.street,
            'apartment_n': apt.apartment_n,
            'state': apt.state,
            'city': apt.city,
            'zip_index': apt.zip_index,
            'bedrooms': apt.bedrooms,
            'bathrooms': apt.bathrooms,
            'apartment_type': apt.apartment_type,
            'status': apt.status,
            'notes': apt.notes,
            'keywords': apt.keywords,
            'raiting': float(apt.raiting) if apt.raiting else 0,
            'default_price': float(apt.default_price) if apt.default_price else 0,
            'start_date': apt.start_date.strftime('%B %d %Y') if apt.start_date else None,
            'end_date': apt.end_date.strftime('%B %d %Y') if apt.end_date else None,
            'owner': apt.owner.id if apt.owner else None,
            'managers': [m.id for m in apt.managers.all()],
            'manager_names': ', '.join([m.full_name for m in apt.managers.all()]),
            'current_price': float(apt.current_price) if apt.current_price else None,
            'current_price_display': f"${apt.current_price}" if apt.current_price else "No price set",
            'links': apt.links,
        }
        items_list.append(item)
    
    items_json = json.dumps(items_list, cls=DateEncoder)
    
    # Get all managers for checkbox rendering
    all_managers = User.objects.filter(role='Manager', is_active=True).order_by('full_name')
    
    # Get all owners for dropdown
    all_owners = User.objects.filter(role='Owner').order_by('full_name')
    
    # Form for field metadata
    form = ApartmentForm(request=request)
    
    context = {
        'items': items_on_page,
        'items_json': items_json,
        'search_query': search_query,
        'all_managers': all_managers,
        'all_owners': all_owners,
        'form': form,
        'title': 'Apartments',
        'model': 'apartments',
    }
    
    return render(request, 'apartments_custom.html', context)



