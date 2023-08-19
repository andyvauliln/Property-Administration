from django.contrib import admin
from .models import User

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ['id', 'full_name', 'email', 'phone', 'role']
    search_fields = ['email', 'full_name']
    list_filter = ['role']