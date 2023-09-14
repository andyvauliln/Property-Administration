# mysite/forms.py
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from mysite.models import Booking, User, Apartment, Payment, Contract, Cleaning, Notification, PaymentMethod
from datetime import date
from django.db.models import Q

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



def get_dropdown_options(identifier, isData=False):
    """
    Returns a list of dictionaries suitable for dropdown options based on the provided identifier.

    :param identifier: A string identifier for the dropdown options (e.g., 'managers', 'cleaners').
    :param isData: A boolean to determine if raw data should be returned.
    :return: A list of dictionaries with "value" and "label" keys or a queryset.
    """
    
    if identifier == 'managers':
        items = User.objects.filter(role='Manager')
        if isData:
            return User.objects.all()
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'cleaners':
        items = User.objects.filter(role='Cleaner')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'roles':
        return [{"value": x[0], "label": x[1]} for x in User.ROLES]

    elif identifier == 'owners':
        items = User.objects.filter(role='Owner')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'tenants':
        items = User.objects.filter(role='Tenant')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'payment_methods':
        items = PaymentMethod.objects.filter(type='Payment Method')
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'banks':
        items = PaymentMethod.objects.filter(type='Bank')
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'apartments':
        items = Apartment.objects.all()
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'bookings':
        today = date.today()
        items = Booking.objects.filter(Q(start_date__gte=today) | Q(status='Active'))
        if isData:
            return Booking.objects.all() 
        return [{"value": item.id, "label": f'{item.apartment.name}:[{item.start_date} - {item.end_date}]'} for item in items]

    elif identifier == 'apart_types':
        return [{"value": x[0], "label": x[1]} for x in Apartment.TYPES]

    elif identifier == 'apart_status':
        return [{"value": x[0], "label": x[1]} for x in Apartment.STATUS]

    elif identifier == 'contract_status':
        return [{"value": x[0], "label": x[1]} for x in Contract.STATUS]

    elif identifier == 'booking_status':
        return [{"value": x[0], "label": x[1]} for x in Booking.STATUS]

    elif identifier == 'booking_period':
        return [{"value": x[0], "label": x[1]} for x in Booking.PERIOD]

    elif identifier == 'payment_method_type':
        return [{"value": x[0], "label": x[1]} for x in PaymentMethod.TYPE]

    elif identifier == 'payment_type':
        return [{"value": x[0], "label": x[1]} for x in Payment.PAYMENT_TYPE]

    elif identifier == 'payment_status':
        return [{"value": x[0], "label": x[1]} for x in Payment.PAYMENT_STATUS]

    elif identifier == 'cleaning_status':
        return [{"value": x[0], "label": x[1]} for x in Cleaning.STATUS]

    elif identifier == 'notification_status':
        return [{"value": x[0], "label": x[1]} for x in Notification.STATUS]

    else:
        raise ValueError(f"Unsupported identifier: {identifier}")


class CustomFieldMixin:
    def __init__(self, *args, **kwargs):
        self._dropdown_options = kwargs.pop('_dropdown_options', None)
        self.isColumn = kwargs.pop('isColumn', False)
        self.isEdit = kwargs.pop('isEdit', False)
        self.isCreate = kwargs.pop('isCreate', False)
        self.ui_element = kwargs.pop('ui_element', None)
        self.display_field = kwargs.pop('display_field', None)
        super().__init__(*args, **kwargs)
         
    @property
    def dropdown_options(self):
        if callable(self._dropdown_options):
            return self._dropdown_options()
        return self._dropdown_options
    
class CharFieldEx(CustomFieldMixin, forms.CharField):
    pass

class IntegerFieldEx(CustomFieldMixin, forms.IntegerField):
    pass

class DecimalFieldEx(CustomFieldMixin, forms.DecimalField):
    pass

class DateFieldEx(CustomFieldMixin, forms.DateField):
    pass

class DateTimeFieldEx(CustomFieldMixin, forms.DateTimeField):
    pass

class EmailFieldEx(CustomFieldMixin, forms.EmailField):
    pass

class BooleanFieldEx(CustomFieldMixin, forms.BooleanField):
    pass

class URLFieldEx(CustomFieldMixin, forms.URLField):
    pass

class ChoiceFieldEx(CustomFieldMixin, forms.ChoiceField):
    pass
class ModelChoiceFieldEx(CustomFieldMixin, forms.ModelChoiceField):
    pass

class CustomUserForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super().__init__(*args, **kwargs)
        
        self.fields['password'].widget = forms.PasswordInput()
        self.fields['password'].help_text = "Leave blank if you don't want to change it"
        if 'instance' in kwargs and kwargs['instance']:
            self.fields['password'].initial = ""
    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'password', 'phone', 'role', "notes"]
    
    def clean(self):
        cleaned_data = super().clean()
        # Add any custom validation here
        return cleaned_data    
    
    email = EmailFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    password = CharFieldEx(max_length=128, required=False, isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    full_name = CharFieldEx(max_length=255, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    phone = CharFieldEx(max_length=15, required=False, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    role = CharFieldEx(max_length=14, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("roles"))
    notes = CharFieldEx(required=False, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")

           
class ApartmentForm(forms.ModelForm): 
    class Meta:
        model = Apartment
        fields = ['name', 'apartment_type', 'status', 'notes', 'web_link', 'house_number', 'street', 'room', 'state', 'city', 'zip_index', 'bedrooms', 'bathrooms', 'manager', 'owner']
        
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(ApartmentForm, self).__init__(*args, **kwargs)
                
    name = CharFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    web_link = URLFieldEx(isColumn=False, required=False, isEdit=True, isCreate=True, ui_element="input")
    
    # Address fields
    house_number = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    street = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    room = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input", required=False)
    state = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    city = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    zip_index = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    
    bedrooms = IntegerFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    bathrooms = IntegerFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    apartment_type = ChoiceFieldEx(choices=Apartment.TYPES, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("apart_types"))
    status = ChoiceFieldEx(choices=Apartment.STATUS, required=False, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("apart_status"))
    notes = CharFieldEx(isColumn=False, isEdit=True, required=False, isCreate=True, ui_element="textarea")
    manager = ModelChoiceFieldEx( queryset=User.objects.all(), isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("managers"), display_field="manager.full_name")
    owner = ModelChoiceFieldEx( queryset=User.objects.all(), isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("owners"), display_field="owner.full_name")


class BookingForm(forms.ModelForm):
    payment_date = forms.DateField(required=False, widget=forms.TextInput(attrs={'multiple': True}))
    amount = forms.DecimalField(max_digits=10, decimal_places=2, required=False, widget=forms.TextInput(attrs={'multiple': True}))
    payment_type = forms.MultipleChoiceField(choices=Payment.PAYMENT_TYPE, required=False)

    
    class Meta:
        model = Booking
        fields = [
            'tenant_email', 'tenant_full_name', 'tenant_phone', 'assigned_cleaner',
            'status', 'start_date', 'end_date', 'notes', 'tenant', 'apartment',
            'payment_type', 'payment_date', 'amount'
        ]
 
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(BookingForm, self).__init__(*args, **kwargs)

        # If action is 'edit', adjust the required fields
        if action == 'edit':
            self.fields['tenant_email'].required = False
            self.fields['assigned_cleaner'].required = False
            self.fields['apartment'].required = False
        
    def save(self, **kwargs):
        instance = super().save(commit=False)
        
        # Use self.cleaned_data to access the form's data
        form_data = self.cleaned_data
        
        payments_data = {
            'payment_dates': self.request.POST.getlist('payment_date[]'),
            'amounts': [abs(float(amount)) for amount in self.request.POST.getlist('amount[]')],
            'payment_types': self.request.POST.getlist('payment_type[]')
        }
        
        if form_data:
            instance.save(form_data=form_data, payments_data=payments_data)
        return instance
    
    payment_types = ChoiceFieldEx(choices=Payment.PAYMENT_TYPE, isColumn=False, isEdit=False, required=False, isCreate=False, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type"))

    tenant_email = EmailFieldEx(required=True, isEdit=False, isCreate=True, ui_element="input")
    tenant_full_name = CharFieldEx(max_length=255, required=False, isEdit=False, isCreate=True, ui_element="input")
    tenant_phone = CharFieldEx(max_length=20, required=False, isEdit=False, isCreate=True, ui_element="input")
    assigned_cleaner = ModelChoiceFieldEx(queryset=User.objects.all(), required=True, isColumn=False, isEdit=False, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaners"))
   
    start_date = DateFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    end_date = DateFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    apartment = ModelChoiceFieldEx(queryset=Apartment.objects.all(),isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("apartments"), display_field="apartment.name")
    
    notes = CharFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="textarea", required=False)
    tenant = ModelChoiceFieldEx(queryset=User.objects.all(), required=False, isColumn=True, isEdit=False, isCreate=False, ui_element="input", display_field="tenant.full_name")
   
    status = ChoiceFieldEx(choices=Booking.STATUS,  isColumn=True,  initial='Pending', isEdit=True,required=False, isCreate=False, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("booking_status"))
    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        
        if 'tenant' not in self.data:
            cleaned_data.pop('tenant', None)
        
        if not status:
            cleaned_data['status'] = 'Pending'
        
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        apartment = cleaned_data.get('apartment')

        # Check for overlapping bookings
        overlapping_bookings = Booking.objects.filter(
            apartment=apartment,
            start_date__lte=end_date,
            end_date__gte=start_date
        )

        # Exclude the current booking (for edit case)
        if self.instance.id:
            overlapping_bookings = overlapping_bookings.exclude(id=self.instance.id)

        if overlapping_bookings.exists():
            overlapping_booking = overlapping_bookings.first()
            raise forms.ValidationError(
                f"The apartment is already booked from {overlapping_booking.start_date} to {overlapping_booking.end_date}."
            )
        
        return cleaned_data

                
class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'payment_method', 'bank', 'payment_status', 'amount', 'payment_type', 'notes', 'booking']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(PaymentForm, self).__init__(*args, **kwargs)    
    payment_date = DateFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    amount = DecimalFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    payment_type = ChoiceFieldEx(choices=Payment.PAYMENT_TYPE, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type"))
    payment_status = ChoiceFieldEx(choices=Payment.PAYMENT_STATUS,  initial='Pending', required=False, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_status"))
    payment_method = ModelChoiceFieldEx(queryset=PaymentMethod.objects.all(), isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_methods"), display_field="payment_method.name")
    bank = ModelChoiceFieldEx(queryset=PaymentMethod.objects.all(), isColumn=True, isEdit=True, isCreate=True, required=False, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("banks"))
    notes = CharFieldEx(isColumn=False, required=False, isEdit=True, isCreate=True, ui_element="textarea")
    booking = ModelChoiceFieldEx(queryset=Booking.objects.all(), isColumn=True, isEdit=True,required=False, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("bookings"), display_field="booking.apartment.name")
    
    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')
    
        if amount is not None and amount <= 0:
            cleaned_data['amount'] = -amount
        status = cleaned_data.get('payment_status')
        
        if not status:
            cleaned_data['payment_status'] = 'Pending'
        
        return cleaned_data
        

class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ['contract_id', 'sign_date', 'link', 'status', 'booking']
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(ContractForm, self).__init__(*args, **kwargs)  
          
    contract_id = CharFieldEx(isColumn=True, isEdit=True,required=False, isCreate=True, ui_element="input")
    sign_date = DateFieldEx(isColumn=True, required=False, isEdit=True, isCreate=True, ui_element="datepicker")
    link = URLFieldEx(isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="input")
    status = ChoiceFieldEx(choices=Contract.STATUS, isColumn=True, required=False, initial='Pending', isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("contract_status"))
    booking = ModelChoiceFieldEx(queryset=Booking.objects.all(), isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("bookings"), display_field="booking.apartment.name")
    
    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        
        if not status:
            cleaned_data['status'] = 'Pending'
        
        return cleaned_data
 

class CleaningForm(forms.ModelForm):
    class Meta:
        model = Cleaning
        fields = ['date', 'booking', 'tasks', 'notes', 'status', "cleaner"]
    
    date = DateFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    status = ChoiceFieldEx(choices=Cleaning.STATUS,required=False, initial='Scheduled', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaning_status"))
    tasks = CharFieldEx(isColumn=False, isEdit=True,required=False, isCreate=True, ui_element="textarea")
    notes = CharFieldEx(isColumn=False, isEdit=True,required=False, isCreate=True, ui_element="textarea")
    cleaner = ModelChoiceFieldEx(queryset=User.objects.all(), isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaners"), display_field="cleaner.full_name")
    booking = ModelChoiceFieldEx(queryset=Booking.objects.all(), isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("bookings"), display_field="booking.apartment.name")
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(CleaningForm, self).__init__(*args, **kwargs)
        
    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        
        if not status:
            cleaned_data['status'] = 'Scheduled'
        
        return cleaned_data


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ['date', 'status', 'message', "send_in_telegram", "booking" ] 
        
    date = DateFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    status = ChoiceFieldEx(choices=Notification.STATUS,required=False,  initial='Pending', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("notification_status"))
    send_in_telegram = BooleanFieldEx(isColumn=True,required=False, isEdit=True, isCreate=True, ui_element="checkbox")
    message = CharFieldEx(isColumn=True,  isEdit=True, isCreate=True, ui_element="textarea")
    booking = ModelChoiceFieldEx(queryset=Booking.objects.all(), isColumn=True, required=False, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options('bookings'), display_field="booking.apartment.name")
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(NotificationForm, self).__init__(*args, **kwargs)
        
    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        
        if not status:
            cleaned_data['status'] = 'Pending'
        
        return cleaned_data
        
class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ['name', 'type', "notes"]

    name = CharFieldEx(max_length=32, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    type = ChoiceFieldEx(choices=PaymentMethod.TYPE, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_method_type"))
    notes = CharFieldEx(isColumn=False, isEdit=True,required=False, isCreate=True, ui_element="textarea")
    
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(PaymentMethodForm, self).__init__(*args, **kwargs)    

