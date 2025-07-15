# mysite/forms.py
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from mysite.models import Booking, User, Apartment, ApartmentPrice, Payment, Cleaning, Notification, PaymentMethod, PaymenType, HandymanCalendar, Parking, ParkingBooking, HandymanBlockedSlot
from datetime import date
import requests
import uuid
from datetime import datetime
from django.utils import timezone
from django.db.models import Case, When, Value, IntegerField

class CustomUserLoginForm(AuthenticationForm):
    username = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'form-input'}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-input'}), )
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(
            attrs={'class': 'form-checkbox h-5 w-5 text-blue-600'})
    )

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.password is None or user.password == "":
            raise forms.ValidationError(
                "Login not allowed.",
                code='invalid_login',
            )


def get_dropdown_options(identifier, isData=False, request=None):
    """
    Returns a list of dictionaries suitable for dropdown options based on the provided identifier.

    :param identifier: A string identifier for the dropdown options (e.g., 'managers', 'cleaners').
    :param isData: A boolean to determine if raw data should be returned.
    :return: A list of dictionaries with "value" and "label" keys or a queryset.
    """

    if identifier == 'managers':
        items = User.objects.filter(role='Manager').order_by('full_name')
        if isData:
            return User.objects.all().order_by('full_name')
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'apartments':
        if request and hasattr(request, 'user') and request.user.role == 'Manager':
            items = Apartment.objects.filter(
                manager=request.user).order_by('name')
        else:
            items = Apartment.objects.all().order_by('name')
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'cleaners':
        items = User.objects.filter(role='Cleaner').order_by('full_name')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]
    elif identifier == 'parking_numbers':
        items = Parking.objects.all()
        if isData:
            return items
        return [{"value": item.id, "label": str(item)} for item in items]

    elif identifier == 'roles':
        return [{"value": x[0], "label": x[1]} for x in User.ROLES]
    elif identifier == 'animals':
        return [{"value": x[0], "label": x[1]} for x in Booking.ANIMALS]
    elif identifier == 'visit_purpose':
        return [{"value": x[0], "label": x[1]} for x in Booking.VISIT_PURPOSE]

    elif identifier == 'owners':
        items = User.objects.filter(role='Owner').order_by('full_name')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'tenants':
        items = User.objects.filter(role='Tenant').order_by('full_name')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'payment_methods':
        items = PaymentMethod.objects.filter(type='Payment Method')
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'is_rent_car':
        items = [True, False]
        if isData:
            return items
        return [{"value": "true", "label": "Rent"}, {"value": "false", "label": "Own"}]

    elif identifier == 'banks':
        items = PaymentMethod.objects.filter(type='Bank')
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'bookings':
        today = date.today()
        items = Booking.objects.filter(end_date__gte=today)
        if isData:
            return items
        return [{"value": item.id, "label": f'{item.apartment.name}:[{item.start_date} - {item.end_date}]'} for item in items]

    elif identifier == 'apart_types':
        return [{"value": x[0], "label": x[1]} for x in Apartment.TYPES]

    elif identifier == 'apart_status':
        return [{"value": x[0], "label": x[1]} for x in Apartment.STATUS]

    elif identifier == 'booking_status':
        return [{"value": x[0], "label": x[1]} for x in Booking.STATUS]

    elif identifier == 'booking_source':
        return [{"value": x[0], "label": x[1]} for x in Booking.SOURCE]

    elif identifier == 'payment_method_type':
        return [{"value": x[0], "label": x[1]} for x in PaymentMethod.TYPE]

    elif identifier == 'payment_type_direction':
        return [{"value": x[0], "label": x[1]} for x in PaymenType.TYPE]
    elif identifier == 'payment_type_balance_sheet':
        return [{"value": x[0], "label": x[1]} for x in PaymenType.BALANCE_SHEET_NAME]
    elif identifier == 'payment_type_category':
        return [{"value": x[0], "label": x[1]} for x in PaymenType.CATEGORY]

    elif identifier == 'payment_type':
        items = PaymenType.objects.annotate(
            type_order=Case(
                When(type='In', then=Value(1)),
                When(type='Out', then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by('type_order', 'name')
        
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name2} for item in items]

    elif identifier == 'payment_status':
        return [{"value": x[0], "label": x[1]} for x in Payment.PAYMENT_STATUS]
    elif identifier == 'parkings':
        return [{"value": x.id, "label": x.number} for x in Parking.objects.all()]

    elif identifier == 'cleaning_status':
        return [{"value": x[0], "label": x[1]} for x in Cleaning.STATUS]

    else:
        raise ValueError(f"Unsupported identifier: {identifier}")


class CustomFieldMixin:
    def __init__(self, *args, **kwargs):
        self._dropdown_options = kwargs.pop('_dropdown_options', None)
        self.isColumn = kwargs.pop('isColumn', False)
        self.isEdit = kwargs.pop('isEdit', False)
        self.isCreate = kwargs.pop('isCreate', False)
        self.ui_element = kwargs.pop('ui_element', None)
        self.display_field = kwargs.pop('display_field', [])
        self.order = kwargs.pop('order', 100)
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
    def __init__(self, *args, **kwargs):
        self.display_format = '%B %d %Y'  # Display format: "December 02 2024"
        self.save_format = '%Y-%m-%d'  # Save format: "YYYY-MM-DD"
        self.input_formats = ['%B %d %Y', '%b. %d, %Y', '%Y-%m-%d']  # Add the new format
        placeholder = kwargs.pop('placeholder', timezone.now().strftime(self.display_format))
        kwargs['widget'] = forms.DateInput(
            format=self.display_format,
            attrs={'placeholder': placeholder}
        )
        kwargs['input_formats'] = self.input_formats  # Set the input formats
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if isinstance(value, str):
            try:
                # Try parsing with multiple formats
                for date_format in self.input_formats:
                    try:
                        date_obj = datetime.strptime(value, date_format)
                        return date_obj.strftime(self.display_format)
                    except ValueError:
                        continue
            except ValueError:
                pass
        return super().prepare_value(value)

class DateTimeFieldEx(CustomFieldMixin, forms.DateTimeField):
    def __init__(self, *args, **kwargs):
        self.display_format = '%B %d %Y'  # Display format: "Jun 24, 2023"
        self.save_format = '%Y-%m-%d'  # Save format: "YYYY-MM-DD"
        placeholder = kwargs.pop('placeholder', timezone.now().strftime(self.display_format))  # Default placeholder
        kwargs['widget'] = forms.DateInput(
            format=self.display_format,
            attrs={'placeholder': placeholder}  # Set the placeholder
        )
        kwargs['input_formats'] = [self.display_format]
        super().__init__(*args, **kwargs)

    # def to_python(self, value):
    #     # Convert the input value to a date object
    #     date_obj = super().to_python(value)
    #     if date_obj is not None:
    #         # Convert the date object to the save format
    #         return date_obj.strftime(self.save_format)
    #     return value

    def prepare_value(self, value):
        # Convert the saved value to the display format
        if isinstance(value, str):
            try:
                date_obj = datetime.strptime(value, self.save_format)
                return date_obj.strftime(self.display_format)
            except ValueError:
                pass
        return super().prepare_value(value)


class EmailFieldEx(CustomFieldMixin, forms.EmailField):
    pass

class CustomBooleanField(CustomFieldMixin, forms.CharField):
       def to_python(self, value):
           print(value, "TEST")
           if value == '' or value == None:
               return None
           return value == 'true'
       
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
        action = kwargs.pop('action', 'create')
        super().__init__(*args, **kwargs)

        self.fields['password'].widget = forms.PasswordInput()
        self.fields['password'].help_text = "Leave blank if you don't want to change it"
        if 'instance' in kwargs and kwargs['instance']:
            self.fields['password'].initial = ""

    class Meta:
        model = User
        fields = ['id', 'email', 'full_name', 'telegram_chat_id',
                  'password', 'phone', 'role', "notes"]

    def clean(self):
        
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        if not email or email == "None":
            cleaned_data["email"] = f"tenant_{uuid.uuid4()}@example.com"
        return cleaned_data

    email = EmailFieldEx(isColumn=True,required=False, initial=f"tenant_{uuid.uuid4()}@example.com", isEdit=True,
                         isCreate=True, ui_element="input")
    password = CharFieldEx(max_length=128, required=False, isColumn=False,
                           isEdit=True, isCreate=True, ui_element="input")
    full_name = CharFieldEx(max_length=255, required=False, isColumn=True,
                            isEdit=True, isCreate=True, ui_element="input")
    phone = CharFieldEx(max_length=15, required=False, isColumn=True,
                        isEdit=True, isCreate=True, ui_element="input")
    role = CharFieldEx(max_length=14, isColumn=True, initial="Tenant", isEdit=True, isCreate=True,
                       ui_element="radio", _dropdown_options=lambda: get_dropdown_options("roles"))
    notes = CharFieldEx(required=False, initial="", isColumn=False,
                        isEdit=True, isCreate=True, ui_element="textarea")
    telegram_chat_id = CharFieldEx(required=False, initial="", isColumn=False,
                        isEdit=True, isCreate=True, ui_element="input")
    is_active = BooleanFieldEx(required=False, initial=True, isColumn=True,
                        isEdit=True, isCreate=True, ui_element="checkbox")


class ApartmentForm(forms.ModelForm):
    class Meta:
        model = Apartment
        fields = ['name', 'apartment_type', 'keywords', 'status', 'notes', 'web_link', 'building_n', 'street', 'apartment_n',
                  'state', 'start_date', 'end_date', 'city', 'zip_index', 'bedrooms', 'bathrooms', 'manager', 'owner', 'raiting', 'default_price', 'current_price_display']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(ApartmentForm, self).__init__(*args, **kwargs)
        
        # Add current price information if this is an existing apartment
        if self.instance and self.instance.pk:
            current_price = self.instance.current_price
            if current_price:
                self.fields['current_price_display'].initial = f"${current_price}"
            else:
                self.fields['current_price_display'].initial = "No current price set"

    def clean_name(self):
        name = self.cleaned_data.get('name')
        raiting = float(self.cleaned_data.get('raiting', 0))
        if raiting > 5:
            raise forms.ValidationError("Raiting must be between 0 and 10")

        if name:
            existing_apartment = Apartment.objects.filter(name=name)
            if self.instance.id:
                existing_apartment = existing_apartment.exclude(id=self.instance.id)
            if existing_apartment.exists():
                raise forms.ValidationError(f"An apartment with the name '{name}' already exists.")
        return name

    name = CharFieldEx(isColumn=True, isEdit=True, initial="",
                       isCreate=True, ui_element="input")
    web_link = URLFieldEx(isColumn=False, initial="", required=False,
                          isEdit=True, isCreate=True, ui_element="input")
    
    current_price_display = CharFieldEx(
        label="Current Price",
        isColumn=True, 
        isEdit=True, 
        isCreate=False, 
        required=False, 
        initial="No price set", 
        ui_element="input",
        order=1.5
    )

    default_price = DecimalFieldEx(
        isColumn=False, isEdit=True, isCreate=True, required=False, initial=0, ui_element="input", order=1.6)

    # Address fields
    building_n = CharFieldEx(isColumn=False, isEdit=True, initial="",
                             isCreate=True, ui_element="input")
    street = CharFieldEx(isColumn=False, isEdit=True, initial="",
                         isCreate=True, ui_element="input")
    apartment_n = CharFieldEx(
        isColumn=False, isEdit=True, isCreate=True, initial="", ui_element="input", required=False)
    state = CharFieldEx(isColumn=False, isEdit=True, initial="",
                        isCreate=True, ui_element="input")
    city = CharFieldEx(isColumn=False, isEdit=True, initial="",
                       isCreate=True, ui_element="input")
    zip_index = CharFieldEx(isColumn=False, isEdit=True, initial="",
                            isCreate=True, ui_element="input")

    bedrooms = IntegerFieldEx(
        isColumn=False, isEdit=True, isCreate=True, initial="", ui_element="input")
    raiting = DecimalFieldEx(
        isColumn=False, isEdit=True, isCreate=True, required=False, initial=0, ui_element="input")
    bathrooms = IntegerFieldEx(
        isColumn=False, isEdit=True, isCreate=True, initial="", ui_element="input")
    apartment_type = ChoiceFieldEx(
        choices=Apartment.TYPES, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apart_types"))
    status = ChoiceFieldEx(
        choices=Apartment.STATUS, required=False, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apart_status"))
    notes = CharFieldEx(isColumn=False, isEdit=True, initial="",
                        required=False, isCreate=True, ui_element="textarea")
    keywords = CharFieldEx(isColumn=False, isEdit=True, initial="",
                        required=False, isCreate=True, ui_element="textarea")
    manager = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("managers"),
        display_field=["manager.full_name"])
    owner = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("owners"),
        display_field=["owner.full_name"])
    start_date = DateFieldEx(isColumn=False, required=False,
                             isEdit=True, isCreate=True, ui_element="datepicker")
    end_date = DateFieldEx(isColumn=False, required=False,
                           isEdit=True, isCreate=True, ui_element="datepicker")


class ApartmentPriceForm(forms.ModelForm):
    class Meta:
        model = ApartmentPrice
        fields = ['apartment', 'price', 'effective_date', 'notes']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(ApartmentPriceForm, self).__init__(*args, **kwargs)

    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all().order_by('name'),
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apartments"),
        display_field=["apartment.name"],
        order=1
    )

    price = DecimalFieldEx(
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=True,
        initial=0,
        ui_element="input",
        order=2
    )

    effective_date = DateFieldEx(
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="datepicker",
        order=3
    )

    notes = CharFieldEx(
        isColumn=False,
        isEdit=True,
        isCreate=True,
        required=False,
        initial="",
        ui_element="textarea",
        order=4
    )

    def clean(self):
        cleaned_data = super().clean()
        apartment = cleaned_data.get('apartment')
        effective_date = cleaned_data.get('effective_date')
        
        if apartment and effective_date:
            # Check if there's already a price for this apartment on this date
            existing_price = ApartmentPrice.objects.filter(
                apartment=apartment,
                effective_date=effective_date
            )
            
            # If this is an update, exclude the current instance
            if self.instance.pk:
                existing_price = existing_price.exclude(pk=self.instance.pk)
            
            if existing_price.exists():
                raise forms.ValidationError(
                    f"A price for {apartment.name} already exists for {effective_date}. "
                    "Only one price per apartment per date is allowed."
                )
        
        return cleaned_data


class BookingForm(forms.ModelForm):
    payment_date = forms.DateField(
        required=False, widget=forms.TextInput(attrs={'multiple': True}))
    amount = forms.DecimalField(max_digits=10, decimal_places=2, required=False,
                                widget=forms.TextInput(attrs={'multiple': True}))

    payment_type = ModelChoiceFieldEx(
        queryset=PaymenType.objects.all(),
        isColumn=False, isEdit=False, isCreate=False, required=False, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("payment_type"))

    class Meta:
        model = Booking
        fields = [
            'tenant_email', 'tenant_full_name', 'tenant_phone', 'keywords', 'assigned_cleaner',
            'status', 'start_date', 'end_date', 'notes', 'tenant', 'apartment', 'source', 'tenants_n',
            'payment_type', 'payment_date', 'amount', "animals", "visit_purpose",  "other_tenants",  'is_rent_car',
            'car_model', 'car_price', 'car_rent_days', 'parking_number',
        ]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')  # Default action is 'create'
        super(BookingForm, self).__init__(*args, **kwargs)

        if self.request and hasattr(self.request, 'user') and self.request.user.role == 'Manager':
            self.fields['apartment'].queryset = Apartment.objects.filter(
                manager=self.request.user).order_by('name')

        else:
            self.fields['apartment'].queryset = Apartment.objects.all().order_by(
                'name')

        self.fields['apartment']._dropdown_options = get_dropdown_options(
            "apartments", False, request=self.request)

    def save(self, **kwargs):
        instance = super().save(commit=False)

        # Use self.cleaned_data to access the form's data
        form_data = self.cleaned_data
        payments_data = {
            'payment_dates': self.request.POST.getlist('payment_date[]'),
            'amounts': [abs(float(amount)) for amount in self.request.POST.getlist('amount[]')],
            'payment_types': self.request.POST.getlist('payment_type[]'),
            'payment_notes': self.request.POST.getlist('payment_notes[]'),
            'payment_status': self.request.POST.getlist('payment_status[]'),
            'payment_id': self.request.POST.getlist('payment_id[]'),
            'number_of_months': [int(number_of_months) for number_of_months in self.request.POST.getlist('number_of_months[]')],
        }
        parking_number = form_data.get("parking_number", None)
        if form_data.get("status") == "Blocked" or form_data.get("status") == "Pending" or form_data.get("status") == "Problem Booking":
            instance.saveEmpty(form_data=form_data)

        elif form_data:
            instance.save(form_data=form_data, payments_data=payments_data, parking_number=parking_number)

        return instance
    start_date = DateFieldEx(isColumn=True, isEdit=True, order=0,
                             isCreate=True, ui_element="datepicker")
    end_date = DateFieldEx(isColumn=True, order=1, isEdit=True,
                           isCreate=True, ui_element="datepicker")
    
    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all().order_by('name'),
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="dropdown",
        order=2,
        display_field=["apartment.name"]
    )
    tenant_full_name = CharFieldEx(
        max_length=255, order=3, initial="Not Availabale", required=False, isEdit=True, isCreate=True, ui_element="input")

    tenant_email = EmailFieldEx(isEdit=True, initial=f"tenant_{uuid.uuid4()}@example.com", order=4, required=False,
                                isCreate=True, ui_element="input")
    
    tenant_phone = CharFieldEx(
        max_length=20, order=5, initial="", required=False, isEdit=True, isCreate=True, ui_element="input")
    
    send_contract = ChoiceFieldEx( choices=[
        (118378, "Send OCCUPANCY AGREEMENT"),
        (120946, "Send HOA PACKAGE"),
    ],
        required=False, isEdit=True, isCreate=True, initial=None, ui_element="radio", 
        _dropdown_options=[
            {"value": 118378, "label": "Send OCCUPANCY AGREEMENT"},
            {"value": 120946, "label": "Send HOA PACKAGE"},
        ],
        order=6)
    tenants_n = DecimalFieldEx(isColumn=False, initial=1, order=7,
                               isEdit=True, isCreate=True, required=False,  ui_element="input")
    other_tenants = CharFieldEx(    
        isColumn=False, isEdit=True, initial="", order=8, isCreate=True, ui_element="textarea", required=False)
    
    notes = CharFieldEx(isColumn=False, order=9, isEdit=True,
                        isCreate=True, ui_element="textarea", initial="", required=False)

    keywords = CharFieldEx(isColumn=False, order=10, isEdit=True,
                        isCreate=True, ui_element="textarea", initial="", required=False)
    

    assigned_cleaner = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        order=11,
        initial=None,
        isColumn=False, isEdit=True, isCreate=True, required=False, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("cleaners"))
    
    status = ChoiceFieldEx(choices=Booking.STATUS, isColumn=True, initial='Waiting Contract', isEdit=True,
                           required=False, isCreate=True, ui_element="radio", order=12,
                           _dropdown_options=lambda: get_dropdown_options("booking_status"))
    
    owner = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        order=13,
        isColumn=False, isEdit=False, isCreate=False, required=False, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("owners"))

  
    
   
    tenant = ModelChoiceFieldEx(queryset=User.objects.all(), order=14, required=False, isColumn=True,
                                isEdit=False, isCreate=False, ui_element="input", display_field=["tenant.full_name"])
    
    visit_purpose = ChoiceFieldEx(
        choices=Booking.VISIT_PURPOSE, isColumn=False, order=15, isEdit=True, required=False, isCreate=True,
        ui_element="radio", _dropdown_options=lambda: get_dropdown_options("visit_purpose"))

    source = ChoiceFieldEx(choices=Booking.SOURCE, initial="Airbnb", order=16, isColumn=False, isEdit=True,
                           required=False, isCreate=True, ui_element="radio",
                           _dropdown_options=lambda: get_dropdown_options("booking_source"))
    animals = ChoiceFieldEx(choices=Booking.ANIMALS, order=17,  isColumn=False,  isEdit=True, required=False,
                            isCreate=True, ui_element="radio", _dropdown_options=lambda: get_dropdown_options("animals"))
    
    is_rent_car = CustomBooleanField(
        required=False, initial="", isCreate=True, isEdit=True, ui_element="radio", _dropdown_options=lambda: get_dropdown_options("is_rent_car"), order=18)
    
    car_model = CharFieldEx(max_length=100, initial="", required=False,
                            isCreate=True, isEdit=True, ui_element="input", order=19)
    car_price = DecimalFieldEx(
        max_digits=10, required=False, initial=0,  isCreate=True, isEdit=True, ui_element="input", order=20)
    car_rent_days = IntegerFieldEx(
        required=False,  isCreate=True, initial=0, isEdit=True, ui_element="input", order=21)
    
    parking_number = IntegerFieldEx(
        required=False, isColumn=False, isCreate=True, initial=None, isEdit=False, ui_element="dropdown", order=22,
        _dropdown_options=lambda: get_dropdown_options("parking_numbers"))

    create_chat = ChoiceFieldEx( choices=[
        (True, "Create Chat"),
    ],
        required=False, isEdit=True, isCreate=True, initial=None, ui_element="radio", 
        _dropdown_options=[
            {"value": True, "label": "Create Chat"},
            ],
        order=23)
    
   
    
    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')

        tenant_email = cleaned_data["tenant_email"]

        if 'tenant' not in self.data:
            cleaned_data.pop('tenant', None)

        if not status:
            cleaned_data['status'] = 'Waiting Contract'

        if status == "Blocked":
            cleaned_data["tenant_email"] = "blocked@gmail.com"
            cleaned_data["tenant_full_name"] = "Blocked"
        
        if status == "Pending":
            cleaned_data["tenant_email"] = "pending@gmail.com"
            cleaned_data["tenant_full_name"] = "Pending"

        if status == "Problem Booking":
            cleaned_data["tenant_email"] = "problem_booking@gmail.com"
            cleaned_data["tenant_full_name"] = "Problem Booking"

        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        apartment = cleaned_data.get('apartment')

        if apartment.status == 'Unavailable':
            raise forms.ValidationError(
                "The selected apartment is currently unavailable.")
        if not tenant_email:
            raise forms.ValidationError(
                "Tenant is information is necessary for booking use temprorary genrated email if you don't know it")

        # Existing overlapping bookings check...
        overlapping_bookings = Booking.objects.filter(
            apartment=apartment,
            start_date__lt=end_date,
            end_date__gt=start_date
        )
        if self.instance.id:
            overlapping_bookings = overlapping_bookings.exclude(
                id=self.instance.id)

        if overlapping_bookings.exists():
            overlapping_booking = overlapping_bookings.first()
            raise forms.ValidationError(
                f"The apartment is already booked from {overlapping_booking.start_date} to {overlapping_booking.end_date}."
            )

        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError(
                "The start date cannot be later than the end date."
            )
        
        parking_number = cleaned_data.get('parking_number')
        if parking_number:
            overlapping_parking_bookings = ParkingBooking.objects.filter(
                parking=parking_number,
                start_date__lt=end_date,
                end_date__gt=start_date
            )
            if overlapping_parking_bookings.exists():
                parking = Parking.objects.get(id=parking_number)
                raise forms.ValidationError(
                    f"The parking spot is already booked from {overlapping_parking_bookings.first().start_date} to {overlapping_parking_bookings.first().end_date}. Building {parking.building} #{parking.number}"
                )

        return cleaned_data


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'payment_method', 'bank', 'keywords', 'payment_status', 'tenant_notes', 'keywords',
                  'amount', "number_of_months", 'payment_type', 'notes', 'booking', "apartment", "invoice_url"]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(PaymentForm, self).__init__(*args, **kwargs)

    payment_date = DateFieldEx(
        isColumn=True, isEdit=True, order=0, isCreate=True, ui_element="datepicker")
    amount = DecimalFieldEx(isColumn=True, initial=0, order=2, isEdit=True,
                            isCreate=True, ui_element="input")
    number_of_months = IntegerFieldEx(
        isColumn=False, isEdit=False, initial=1, order=1, required=False, isCreate=True, ui_element="input")
    payment_status = ChoiceFieldEx(
        choices=Payment.PAYMENT_STATUS, initial='Pending', order=8, required=False, isColumn=True, isEdit=True, isCreate=True,
        ui_element="radio", _dropdown_options=lambda: get_dropdown_options("payment_status"))
    payment_method = ModelChoiceFieldEx(
        queryset=PaymentMethod.objects.all(),
        isColumn=False, isEdit=True, required=False, initial=1, isCreate=True, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("payment_methods"),
        order=6,
        display_field=["payment_method.name"])
    payment_type = ModelChoiceFieldEx(
        queryset=PaymenType.objects.all(),
        order=7,
        isColumn=True, isEdit=True, required=True, isCreate=True, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("payment_type"),
        display_field=["payment_type.full_name2"])
    bank = ModelChoiceFieldEx(
        queryset=PaymentMethod.objects.all(),
        initial=6,
        order=5,
        isColumn=False, isEdit=True, isCreate=True, required=False, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("banks"))
    notes = CharFieldEx(isColumn=True, required=False, order=9, initial="",
                        isEdit=True, isCreate=True, ui_element="textarea")
    tenant_notes = CharFieldEx(isColumn=False, required=False, order=10, initial="",
                        isEdit=True, isCreate=True, ui_element="textarea")
    keywords = CharFieldEx(isColumn=False, required=False, order=11, initial="",
                        isEdit=True, isCreate=True, ui_element="textarea")
    booking = ModelChoiceFieldEx(
        queryset=Booking.objects.all(),
        isColumn=True, isEdit=False, required=False, isCreate=False, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("bookings"),
        display_field=["booking.apartment.name", "apartment.name", "booking.tenant.full_name"])

    invoice_url = CharFieldEx(
        isColumn=True, required=False, order=12, initial="",
        isEdit=False, isCreate=False, ui_element="link")

    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all(),
        isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apartments", False, request=None),
        display_field=["booking.apartment.name", "apartment.name"])

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get('amount')

        if amount is not None and amount <= 0:
            cleaned_data['amount'] = -amount
        status = cleaned_data.get('payment_status')

        number_of_months = cleaned_data.get('number_of_months')

        self.number_of_months = number_of_months

        if not status:
            cleaned_data['payment_status'] = 'Pending'

        return cleaned_data

    def save(self, **kwargs):
        instance = super().save(commit=False)

        instance.save(number_of_months=self.number_of_months or 0)
        return instance


# class ContractForm(forms.ModelForm):
#     class Meta:
#         model = Contract
#         fields = ['contract_id', 'sign_date', 'link', 'status', 'booking']

#     def __init__(self, *args, **kwargs):
#         self.request = kwargs.pop('request', None)
#         action = kwargs.pop('action', 'create')
#         super(ContractForm, self).__init__(*args, **kwargs)

#     contract_id = CharFieldEx(isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="input")
#     sign_date = DateFieldEx(isColumn=True, required=False, isEdit=True, isCreate=True, ui_element="datepicker")
#     link = URLFieldEx(isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="input")
#     status = ChoiceFieldEx(
#         choices=Contract.STATUS, isColumn=True, required=False, initial='Pending', isEdit=True, isCreate=True,
#         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("contract_status"))
#     booking = ModelChoiceFieldEx(
#         queryset=Booking.objects.all(),
#         isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
#         _dropdown_options=lambda: get_dropdown_options("bookings"),
#         display_field="booking.apartment.name")

#     def clean(self):
#         cleaned_data = super().clean()
#         status = cleaned_data.get('status')

#         if not status:
#             cleaned_data['status'] = 'Pending'

#         return cleaned_data


class CleaningForm(forms.ModelForm):
    class Meta:
        model = Cleaning
        fields = ['date', 'booking', 'tasks', 'notes', 'status', "cleaner", "apartment"]

    date = DateFieldEx(isColumn=True, isEdit=True,
                       isCreate=True, ui_element="datepicker")
    status = ChoiceFieldEx(
        choices=Cleaning.STATUS, required=False, initial='Scheduled', isColumn=True, isEdit=True, isCreate=True,
        ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaning_status"))
    tasks = CharFieldEx(isColumn=False, initial="", isEdit=True,
                        required=False, isCreate=True, ui_element="textarea")
    notes = CharFieldEx(isColumn=False, initial="", isEdit=True,
                        required=False, isCreate=True, ui_element="textarea")
    cleaner = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("cleaners"),
        display_field=["cleaner.full_name"])
    booking = ModelChoiceFieldEx(
        queryset=Booking.objects.all(),
        isColumn=True, isEdit=False, required=False, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("bookings"),
        display_field=["booking.apartment.name", "apartment.name"])
    
    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all(),
        isColumn=True, isEdit=True, required=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apartments", False, request=None),
        display_field=["apartment.name", "booking.apartment.name"])

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
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
        fields = ['date', 'message', 'apartment', "send_in_telegram"]

    date = DateFieldEx(isColumn=True, isEdit=True,
                       isCreate=True, ui_element="datepicker")
    send_in_telegram = BooleanFieldEx(
        isColumn=True, required=False, isEdit=True, isCreate=True, ui_element="checkbox")
    message = CharFieldEx(isColumn=True, initial="",  isEdit=True,
                          isCreate=True, ui_element="textarea")
    booking = ModelChoiceFieldEx(
        queryset=Booking.objects.all(),
        isColumn=True, required=False, isEdit=False, isCreate=False, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options('bookings'),
        display_field=["booking.apartment.name"])
    
    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all(),
        isColumn=True, isEdit=True, required=False, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apartments", False, request=None),
        display_field=["apartment.name"])

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(NotificationForm, self).__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        return cleaned_data


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ['name', 'type', "notes", "keywords"]

    name = CharFieldEx(max_length=32, initial="", isColumn=True,
                       isEdit=True, isCreate=True, ui_element="input")
    type = ChoiceFieldEx(choices=PaymentMethod.TYPE, isColumn=True, isEdit=True, isCreate=True,
                         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_method_type"))
    notes = CharFieldEx(isColumn=False, initial="", isEdit=True,
                        required=False, isCreate=True, ui_element="textarea")
    keywords = CharFieldEx(isColumn=False, initial="", isEdit=True,
                        required=False, isCreate=True, ui_element="textarea")

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(PaymentMethodForm, self).__init__(*args, **kwargs)


class PaymentTypeForm(forms.ModelForm):
    class Meta:
        model = PaymenType
        fields = ['name', 'type', 'category', 'keywords', 'balance_sheet_name']

    name = CharFieldEx(max_length=32, initial="", isColumn=True,
                       isEdit=True, isCreate=True, ui_element="input")
    balance_sheet_name = ChoiceFieldEx(choices=PaymenType.BALANCE_SHEET_NAME, required=False, initial="Receivables", isColumn=True, isEdit=True, isCreate=True,
                         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type_balance_sheet"))
    category = ChoiceFieldEx(choices=PaymenType.CATEGORY, required=False, isColumn=True, initial="Operating", isEdit=True, isCreate=True,
                         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type_category"))
    type = ChoiceFieldEx(choices=PaymenType.TYPE, isColumn=True, isEdit=True, isCreate=True,
                         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type_direction"))
    keywords = CharFieldEx(isColumn=False, initial="", isEdit=True,
                        required=False, isCreate=True, ui_element="textarea")

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(PaymentTypeForm, self).__init__(*args, **kwargs)


class HandymanCalendarForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        # Remove request from kwargs if it exists, store it as an instance variable
        self.request = kwargs.pop('request', None)
        # Get action parameter
        self.action = kwargs.pop('action', None)
        # Initialize the form
        super(HandymanCalendarForm, self).__init__(*args, **kwargs)

    class Meta:
        model = HandymanCalendar
        fields = ('tenant_name', 'tenant_phone', 'apartment_name',
                  'date', 'start_time', 'end_time', 'notes')
    
    def clean(self):
        """Validate the form data."""
        # For debugging: print all form data
        print("HANDYMAN FORM DATA:", self.data)
        
        cleaned_data = super().clean()
        print("CLEANED DATA:", cleaned_data)
        
        # Validate required fields
        required_fields = ['tenant_name', 'tenant_phone', 'apartment_name', 
                          'date', 'start_time', 'end_time']
        
        for field in required_fields:
            if not cleaned_data.get(field):
                self.add_error(field, f'{field} is required')
                
        # Make sure time format is correct
        start_time = cleaned_data.get('start_time')
        end_time = cleaned_data.get('end_time')
        
        # If both time fields exist, ensure they're valid time objects
        if start_time and end_time:
            # They should already be time objects from the model field validation
            if start_time >= end_time:
                self.add_error('end_time', 'End time must be after start time')
                
        return cleaned_data


class HandymanBlockedSlotForm(forms.ModelForm):
    def __init__(self, request=None, action=None, *args, **kwargs):
        super(HandymanBlockedSlotForm, self).__init__(*args, **kwargs)

    class Meta:
        model = HandymanBlockedSlot
        fields = ('date', 'start_time', 'end_time', 'is_full_day', 'reason', 'created_by')


class ParkingBookingForm(forms.ModelForm):
    class Meta:
        model = ParkingBooking
        fields = ['parking', 'notes', 'status', 'apartment', 'booking', 'start_date', 'end_date']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(ParkingBookingForm, self).__init__(*args, **kwargs)

    parking = ModelChoiceFieldEx(
        queryset=Parking.objects.all(),
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=True,
        ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("parkings"),
        display_field=["parking.number"],
        order=1
    )

    notes = CharFieldEx(
        max_length=255,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=False,
        ui_element="textarea",
        order=3
    )

    status = ChoiceFieldEx(
        choices=ParkingBooking.STATUS,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=False,
        initial='Unavailable',
        ui_element="dropdown",
        _dropdown_options=lambda: [{"value": x[0], "label": x[1]} for x in ParkingBooking.STATUS],
        order=2
    )

    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all().order_by('name'),
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apartments"),
        display_field=["apartment.name"],
        order=4
    )

    booking = ModelChoiceFieldEx(
        queryset=Booking.objects.all(),
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=False,
        ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("bookings"),
        display_field=["booking.tenant.full_name", "booking.start_date", "booking.end_date"],
        order=5
    )

    start_date = DateFieldEx(
        required=False,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="datepicker",
        order=6
    )

    end_date = DateFieldEx(
        required=False,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="datepicker",
        order=7
    )

    def clean(self):
        cleaned_data = super().clean()
        parking = cleaned_data.get('parking')
        apartment = cleaned_data.get('apartment')
        booking = cleaned_data.get('booking')
        status = cleaned_data.get('status', "Unavailable")
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        # If booking is assigned, use booking dates and apartment, but preserve user-selected status
        if booking:
            cleaned_data['start_date'] = booking.start_date
            cleaned_data['end_date'] = booking.end_date
            cleaned_data['apartment'] = booking.apartment
            # Only set status to 'Booked' if no status was explicitly provided (for new bookings)
            if not self.instance.pk and not status:
                cleaned_data['status'] = 'Booked'
        else:
            # If no booking but status is 'Booked', require dates
            if status == 'Booked' and not (start_date and end_date):
                raise forms.ValidationError(
                    "Start date and end date are required when status is 'Booked' without a booking"
                )

        # Validate date range if both dates are provided
        if start_date and end_date and start_date > end_date:
            raise forms.ValidationError(
                "End date must be after start date"
            )

        # Check for duplicate parking numbers within the same apartment
        if parking and apartment:
            existing_parking = ParkingBooking.objects.filter(
                apartment=apartment,
                parking=parking,
                start_date__lte=end_date,
                end_date__gte=start_date
            )
            if self.instance.id:
                existing_parking = existing_parking.exclude(id=self.instance.id)
            
            if existing_parking.exists():
                raise forms.ValidationError(
                    f"Parking {parking} already exists for this apartment"
                )

        return cleaned_data



class ParkingForm(forms.ModelForm):
    class Meta:
        model = Parking
        fields = ['building', 'number', 'notes', 'associated_room']

    def __init__(self, *args, **kwargs):
        # Remove request from kwargs if it exists, since we don't need it
        kwargs.pop('request', None)
        # Remove action from kwargs if it exists
        kwargs.pop('action', None)
        super().__init__(*args, **kwargs)

    building = CharFieldEx(
        max_length=255,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=True,
        order=1
    )

    associated_room = CharFieldEx(
        max_length=255,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=False,
        order=3
    )

    number = CharFieldEx(
        max_length=255,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=True,
        order=2
    )

    notes = CharFieldEx(
        max_length=255,
        isColumn=True,
        isEdit=True,
        isCreate=True,
        required=False,
        order=4
    )

    def clean(self):
        cleaned_data = super().clean()
        building = cleaned_data.get('building')
        number = cleaned_data.get('number')
        notes = cleaned_data.get('notes')
        associated_room = cleaned_data.get('associated_room')

        if not building or not number:
            raise forms.ValidationError("Building and number are required")

        # Check if parking already exists with this building and number
        existing_parking = Parking.objects.filter(building=building, number=number)
        if self.instance.id:
            existing_parking = existing_parking.exclude(id=self.instance.id)
        
        if existing_parking.exists():
            raise forms.ValidationError(f"Parking with building '{building}' and number '{number}' already exists")

        return cleaned_data
