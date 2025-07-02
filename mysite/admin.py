from django.contrib import admin
from .models import User, Apartment, ApartmentPrice

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['id', 'full_name', 'email', 'phone', 'role']
    search_fields = ['email', 'full_name']
    list_filter = ['role']


class ApartmentPriceInline(admin.TabularInline):
    model = ApartmentPrice
    extra = 1
    fields = ['price', 'effective_date', 'notes']
    ordering = ['-effective_date']


@admin.register(Apartment)
class ApartmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'apartment_type', 'status', 'current_price', 'manager', 'owner']
    search_fields = ['name', 'building_n', 'street', 'city']
    list_filter = ['apartment_type', 'status', 'manager', 'owner']
    inlines = [ApartmentPriceInline]
    
    def current_price(self, obj):
        price = obj.current_price
        return f"${price}" if price else "No price set"
    current_price.short_description = 'Current Price'


@admin.register(ApartmentPrice)
class ApartmentPriceAdmin(admin.ModelAdmin):
    list_display = ['apartment', 'price', 'effective_date', 'status', 'notes', 'created_at']
    search_fields = ['apartment__name', 'notes']
    list_filter = ['effective_date', 'created_at']
    ordering = ['-effective_date', 'apartment__name']
    
    def status(self, obj):
        from datetime import date
        if obj.effective_date <= date.today():
            return "ACTIVE"
        else:
            return "FUTURE"
    status.short_description = 'Status'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('apartment')