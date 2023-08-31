# mysite/forms.py

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from mysite.models import Booking, User, Property, Payment, Contract, Cleaning, Notification, PaymentMethod, Bank


class CustomUserLoginForm(AuthenticationForm):
    username = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-input'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input'}), )
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-blue-600'})
    )

class CustomUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'password', 'phone', 'role']

        
class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ['name', 'property_type', 'status', 'notes', 'web_link', 'address', 'bedrooms', 'bathrooms', 'manager', 'owner']
        
class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'payment_method', 'bank', 'payment_status', 'amount', 'payment_type', 'notes', 'booking']
        
class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ['sign_date', "contract_id","status", 'link', 'tenant', 'owner', 'appartment']

class CleaningForm(forms.ModelForm):
    class Meta:
        model = Cleaning
        fields = ['date', 'booking', 'tasks', 'notes', 'status']

class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ['date', 'status', 'message']
        
class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ['method_name', 'description']

class BankForm(forms.ModelForm):
    class Meta:
        model = Bank
        fields = ['bank_name', 'bank_details']
        
class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['start_date', 'end_date', 'tenant', 'contract', 'property', 'notes', 'status', 'price']