# mysite/forms.py

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from mysite.models import Booking, User, Apartment, Payment, Contract, Cleaning, Notification, PaymentMethod


class CustomUserLoginForm(AuthenticationForm):
    username = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-input'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input'}), )
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-blue-600'})
    )
    
        
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.password is None or user.password == "":
            raise forms.ValidationError(
                "Login not allowed.",
                code='invalid_login',
            )

class CustomUserForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password'].widget = forms.PasswordInput()
        self.fields['password'].help_text = "Leave blank if you don't want to change it"
        if 'instance' in kwargs and kwargs['instance']:
            self.fields['password'].initial = ""
    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'password', 'phone', 'role', "notes"]

        
class ApartmentForm(forms.ModelForm):
    class Meta:
        model = Apartment
        fields = ['name', 'apartment_type', 'status', 'notes', 'web_link', 'address', 'bedrooms', 'bathrooms', 'manager', 'owner']
        
class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'payment_method', 'bank', 'payment_status', 'amount', 'payment_type', 'notes', 'booking']
        
class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ['sign_date', "contract_id","status", 'link', 'booking']

class CleaningForm(forms.ModelForm):
    class Meta:
        model = Cleaning
        fields = ['date', 'booking', 'tasks', 'notes', 'status', "cleaner"]

class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ['date', 'status', 'message', "send_in_telegram", "booking" ]
        
class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ['name', 'type', "notes"]


        
class BookingForm(forms.ModelForm):
    class Meta:
        model = Booking
        fields = ['start_date', 'end_date', 'apartment', 'notes', 'status', 'price', "period"]
        
    def save(self, commit=True, **kwargs):
        form_data = kwargs.pop('form_data', None)
        instance = super().save(commit=False)
        if form_data:
            instance.save(form_data=form_data)
        if commit:
            instance.save()
        return instance
        
