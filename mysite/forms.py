# mysite/forms.py

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from mysite.models import User


class CustomUserLoginForm(AuthenticationForm):
    username = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-input'}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-input'}))
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox h-5 w-5 text-blue-600'})
    )

class CustomUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'password', 'phone', 'role']