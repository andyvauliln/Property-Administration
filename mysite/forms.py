# mysite/forms.py
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from mysite.models import Booking, User, Apartment, Payment, Cleaning, Notification, PaymentMethod, PaymenType
from datetime import date
import requests
import uuid


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
        items = User.objects.filter(role='Manager')
        if isData:
            return User.objects.all()
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
        items = User.objects.filter(role='Cleaner')
        if isData:
            return items
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'roles':
        return [{"value": x[0], "label": x[1]} for x in User.ROLES]
    elif identifier == 'animals':
        return [{"value": x[0], "label": x[1]} for x in Booking.ANIMALS]
    elif identifier == 'visit_purpose':
        return [{"value": x[0], "label": x[1]} for x in Booking.VISIT_PURPOSE]

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

    elif identifier == 'payment_type':
        items = PaymenType.objects.all()
        if isData:
            return items
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'payment_status':
        return [{"value": x[0], "label": x[1]} for x in Payment.PAYMENT_STATUS]

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
    pass


class DateTimeFieldEx(CustomFieldMixin, forms.DateTimeField):
    pass


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
        fields = ['id', 'email', 'full_name',
                  'password', 'phone', 'role', "notes"]

    def clean(self):
        
        cleaned_data = super().clean()
        email = cleaned_data.get("email")
        if email or cleaned_data.get("email") == "None":
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


class ApartmentForm(forms.ModelForm):
    class Meta:
        model = Apartment
        fields = ['name', 'apartment_type', 'status', 'notes', 'web_link', 'building_n', 'street', 'apartment_n',
                  'state', 'start_date', 'end_date', 'city', 'zip_index', 'bedrooms', 'bathrooms', 'manager', 'owner',]

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(ApartmentForm, self).__init__(*args, **kwargs)

    name = CharFieldEx(isColumn=True, isEdit=True,
                       isCreate=True, ui_element="input")
    web_link = URLFieldEx(isColumn=False, required=False,
                          isEdit=True, isCreate=True, ui_element="input")

    # Address fields
    building_n = CharFieldEx(isColumn=False, isEdit=True,
                             isCreate=True, ui_element="input")
    street = CharFieldEx(isColumn=False, isEdit=True,
                         isCreate=True, ui_element="input")
    apartment_n = CharFieldEx(
        isColumn=False, isEdit=True, isCreate=True, ui_element="input", required=False)
    state = CharFieldEx(isColumn=False, isEdit=True,
                        isCreate=True, ui_element="input")
    city = CharFieldEx(isColumn=False, isEdit=True,
                       isCreate=True, ui_element="input")
    zip_index = CharFieldEx(isColumn=False, isEdit=True,
                            isCreate=True, ui_element="input")

    bedrooms = IntegerFieldEx(
        isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    bathrooms = IntegerFieldEx(
        isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    apartment_type = ChoiceFieldEx(
        choices=Apartment.TYPES, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apart_types"))
    status = ChoiceFieldEx(
        choices=Apartment.STATUS, required=False, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apart_status"))
    notes = CharFieldEx(isColumn=False, isEdit=True,
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
            'tenant_email', 'tenant_full_name', 'tenant_phone', 'assigned_cleaner',
            'status', 'start_date', 'end_date', 'notes', 'tenant', 'apartment', 'source', 'tenants_n',
            'payment_type', 'payment_date', 'amount', "animals", "visit_purpose",  "other_tenants",  'is_rent_car',
            'car_model', 'car_price', 'car_rent_days',
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
            'number_of_months': [int(number_of_months) for number_of_months in self.request.POST.getlist('number_of_months[]')],
        }

        if form_data.get("status") == "Blocked":
            instance.saveEmpty(form_data=form_data)

        elif form_data:
            instance.save(form_data=form_data, payments_data=payments_data)

        return instance

    tenant_email = EmailFieldEx(isEdit=True, initial=f"tenant_{uuid.uuid4()}@example.com", order=3, required=False,
                                isCreate=True, ui_element="input")
    tenant_full_name = CharFieldEx(
        max_length=255, order=2, initial="Not Availabale", required=False, isEdit=True, isCreate=True, ui_element="input")
    tenant_phone = CharFieldEx(
        max_length=20, order=4, initial="", required=False, isEdit=True, isCreate=True, ui_element="input")
    assigned_cleaner = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        order=1,
        initial=17,
        isColumn=False, isEdit=True, isCreate=True, required=False, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("cleaners"))
    owner = ModelChoiceFieldEx(
        queryset=User.objects.all(),
        isColumn=False, isEdit=False, isCreate=False, required=False, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("owners"))

    start_date = DateFieldEx(isColumn=True, isEdit=True, order=-3,
                             isCreate=True, ui_element="datepicker")
    end_date = DateFieldEx(isColumn=True, order=-2, isEdit=True,
                           isCreate=True, ui_element="datepicker")
    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all().order_by('name'),
        isColumn=True,
        isEdit=True,
        isCreate=True,
        ui_element="dropdown",
        order=-1,
        # _dropdown_options=lambda: get_dropdown_options("apartments"),
        display_field=["apartment.name"]
    )
    notes = CharFieldEx(isColumn=False, order=10, isEdit=True,
                        isCreate=True, ui_element="textarea", initial="", required=False)
    other_tenants = CharFieldEx(
        isColumn=False, isEdit=True, initial="", order=9, isCreate=True, ui_element="textarea", required=False)
    tenant = ModelChoiceFieldEx(queryset=User.objects.all(), required=False, isColumn=True,
                                isEdit=False, isCreate=False, ui_element="input", display_field=["tenant.full_name"])
    tenants_n = DecimalFieldEx(isColumn=False, initial=1, order=8,
                               isEdit=True, isCreate=True, required=False,  ui_element="input")
    status = ChoiceFieldEx(choices=Booking.STATUS, isColumn=True, initial='Waiting Contract', isEdit=True,
                           required=False, isCreate=True, ui_element="radio", order=0,
                           _dropdown_options=lambda: get_dropdown_options("booking_status"))
    source = ChoiceFieldEx(choices=Booking.SOURCE, initial="Airbnb", order=5, isColumn=False, isEdit=True,
                           required=False, isCreate=True, ui_element="radio",
                           _dropdown_options=lambda: get_dropdown_options("booking_source"))
    animals = ChoiceFieldEx(choices=Booking.ANIMALS, order=7,  isColumn=False,  isEdit=True, required=False,
                            isCreate=True, ui_element="radio", _dropdown_options=lambda: get_dropdown_options("animals"))
    visit_purpose = ChoiceFieldEx(
        choices=Booking.VISIT_PURPOSE, isColumn=False, order=6, isEdit=True, required=False, isCreate=True,
        ui_element="radio", _dropdown_options=lambda: get_dropdown_options("visit_purpose"))

    send_contract = ChoiceFieldEx( choices=[
        (118378, "Send Occupant Agreement"),
        (120946, "Send Application Form"),
    ],
        required=False, isCreate=True, initial=None, isEdit=False, ui_element="radio", 
        _dropdown_options=[
            {"value": 118378, "label": "Send Occupant Agreement"},
            {"value": 120946, "label": "Send Application Form"},
            ],
            order=11)
    is_rent_car = CustomBooleanField(
        required=False, initial="", isCreate=True, isEdit=True, ui_element="radio", _dropdown_options=lambda: get_dropdown_options("is_rent_car"), order=12)
    
    car_model = CharFieldEx(max_length=100, initial="", required=False,
                            isCreate=True, isEdit=True, ui_element="input", order=13)
    car_price = DecimalFieldEx(
        max_digits=10, required=False, initial=0,  isCreate=True, isEdit=True, ui_element="input", order=14)
    car_rent_days = IntegerFieldEx(
        required=False,  isCreate=True, initial=0, isEdit=True, ui_element="input", order=15)
    

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')

        if 'tenant' not in self.data:
            cleaned_data.pop('tenant', None)

        if not status:
            cleaned_data['status'] = 'Waiting Contract'

        if status == "Blocked":
            cleaned_data["tenant_email"] = "blocked@gmail.com"
            cleaned_data["tenant_full_name"] = "Blocked"

        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')
        apartment = cleaned_data.get('apartment')

        if apartment.status == 'Unavailable':
            raise forms.ValidationError(
                "The selected apartment is currently unavailable.")

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

        return cleaned_data


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['payment_date', 'payment_method', 'bank', 'payment_status',
                  'amount', "number_of_months", 'payment_type', 'notes', 'booking', "apartment"]

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
        isColumn=True, isEdit=True, isCreate=True, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("payment_type"),
        display_field=["payment_type.name"])
    bank = ModelChoiceFieldEx(
        queryset=PaymentMethod.objects.all(),
        initial=6,
        order=5,
        isColumn=False, isEdit=True, isCreate=True, required=False, ui_element="radio",
        _dropdown_options=lambda: get_dropdown_options("banks"))
    notes = CharFieldEx(isColumn=True, required=False, order=9, initial="",
                        isEdit=True, isCreate=True, ui_element="textarea")
    booking = ModelChoiceFieldEx(
        queryset=Booking.objects.all(),
        isColumn=True, isEdit=False, required=False, isCreate=False, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("bookings"),
        display_field=["booking.apartment.name", "apartment.name", "booking.tenant.full_name"])

    apartment = ModelChoiceFieldEx(
        queryset=Apartment.objects.all().order_by('name'), order=4,
        isColumn=False, isEdit=True, isCreate=True, ui_element="dropdown",
        _dropdown_options=lambda: get_dropdown_options("apartments"),
        required=False)

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
        fields = ['date', 'booking', 'tasks', 'notes', 'status', "cleaner"]

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
        display_field=["booking.apartment.name"])

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
        fields = ['date', 'message', "send_in_telegram"]

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
        fields = ['name', 'type', "notes"]

    name = CharFieldEx(max_length=32, initial="", isColumn=True,
                       isEdit=True, isCreate=True, ui_element="input")
    type = ChoiceFieldEx(choices=PaymentMethod.TYPE, isColumn=True, isEdit=True, isCreate=True,
                         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_method_type"))
    notes = CharFieldEx(isColumn=False, initial="", isEdit=True,
                        required=False, isCreate=True, ui_element="textarea")

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(PaymentMethodForm, self).__init__(*args, **kwargs)


class PaymentTypeForm(forms.ModelForm):
    class Meta:
        model = PaymenType
        fields = ['name', 'type']

    name = CharFieldEx(max_length=32, initial="", isColumn=True,
                       isEdit=True, isCreate=True, ui_element="input")
    type = ChoiceFieldEx(choices=PaymenType.TYPE, isColumn=True, isEdit=True, isCreate=True,
                         ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type_direction"))

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        action = kwargs.pop('action', 'create')
        super(PaymentTypeForm, self).__init__(*args, **kwargs)
