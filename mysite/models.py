# mysite/models.py

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.conf import settings
from django.contrib.auth.hashers import make_password
from docusign_esign import ApiClient, EnvelopesApi
from docusign_esign.models import EnvelopeDefinition, Signer, SignHere, Tabs, RecipientViewRequest
from datetime import timedelta
from dateutil.relativedelta import relativedelta
from datetime import date
from decimal import Decimal
from datetime import date
from django.db.models import Q



def get_dropdown_options(identifier):
    """
    Returns a list of dictionaries suitable for dropdown options based on the provided identifier.

    :param identifier: A string identifier for the dropdown options (e.g., 'managers', 'cleaners').
    :return: A list of dictionaries with "value" and "label" keys.
    """
    if identifier == 'managers':
        items = User.objects.filter(role='Manager')
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'cleaners':
        items = User.objects.filter(role='Cleaner')
        return [{"value": item.id, "label": item.full_name} for item in items]
    
    elif identifier == 'roles':
        return [{"value": x[0], "label": x[1]} for x in User.ROLES]

    elif identifier == 'owners':
        items = User.objects.filter(role='Owner')
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'tenants':
        items = User.objects.filter(role='Tenant')
        return [{"value": item.id, "label": item.full_name} for item in items]

    elif identifier == 'payment_methods':
        items = PaymentMethod.objects.filter(type='Payment Method')
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'banks':
        items = PaymentMethod.objects.filter(type='Bank')
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'apartments':
        items = Apartment.objects.all()
        return [{"value": item.id, "label": item.name} for item in items]

    elif identifier == 'bookings':
        today = date.today()
        items = Booking.objects.filter(Q(start_date__gte=today) | Q(status='Active'))
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


class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)

class CustomFieldMixin:
    def __init__(self, *args, **kwargs):
        self._dropdown_options = kwargs.pop('_dropdown_options', None)
        self.isColumn = kwargs.pop('isColumn', False)
        self.isEdit = kwargs.pop('isEdit', False)
        self.isCreate = kwargs.pop('isCreate', False)
        self.ui_element = kwargs.pop('ui_element', None)
        super().__init__(*args, **kwargs)
         
    @property
    def dropdown_options(self):
        if callable(self._dropdown_options):
            return self._dropdown_options()
        return self._dropdown_options
   
class CharFieldEx(CustomFieldMixin, models.CharField):
    pass

class IntegerFieldEx(CustomFieldMixin, models.IntegerField):
    pass

class DecimalFieldEx(CustomFieldMixin, models.DecimalField):
    pass

class DateFieldEx(CustomFieldMixin, models.DateField):
    pass

class DateTimeFieldEx(CustomFieldMixin, models.DateTimeField):
    pass

class EmailFieldEx(CustomFieldMixin, models.EmailField):
    pass

class BooleanFieldEx(CustomFieldMixin, models.BooleanField):
    pass

class URLFieldEx(CustomFieldMixin, models.URLField):
    pass

class ForeignKeyEx(CustomFieldMixin, models.ForeignKey):
    def __init__(self, *args, **kwargs):
        self.display_field = kwargs.pop('display_field', None)
        super().__init__(*args, **kwargs)

class ManyToManyFieldEx(CustomFieldMixin, models.ManyToManyField):
    pass

class OneToOneFieldEx(CustomFieldMixin, models.OneToOneField):
    pass

class TextFieldEx(CustomFieldMixin, models.TextField):
    pass
      

# User Model            
class User(AbstractBaseUser, PermissionsMixin):
    def __str__(self):
        return self.full_name

    ROLES = [
        ('Admin', 'Admin'),
        ('Cleaner', 'Cleaner'),
        ('Manager', 'Manager'),
        ('Tenant', 'Tenant'),
        ('Owner', 'Owner'),
    ]
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']
    
    def save(self, *args, **kwargs):
        # Check if the password is not hashed
        if self.password and not self.password.startswith(('pbkdf2_sha256$', 'bcrypt')):
            self.password = make_password(self.password)
        super(User, self).save(*args, **kwargs)

    email = EmailFieldEx(unique=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    password = CharFieldEx(max_length=128, null=True, blank=True, isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    full_name = CharFieldEx(max_length=255, db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    phone = CharFieldEx(max_length=15, blank=True, null=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")

    role = CharFieldEx(max_length=14, choices=ROLES, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("roles"))
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    objects = CustomUserManager()


    @property
    def links(self):
        links_list = []

        # For Tenant
        if self.role == "Tenant":
            links_list.append({"name": "Bookings: User Bookings", "link": f"/bookings?q=tenant.id={self.id}"})

        # For Cleaner
        if self.role == "Cleaner":
            links_list.append({"name": "Cleanings: User Cleanings", "link": f"/cleanings?q=cleaner.id={self.id}"})

        # For Owner
        if self.role == "Owner":
            links_list.append({"name": "Apartments: Owner Apartments", "link": f"/apartments?q=owner.id={self.id}"})

        # For Manager
        if self.role == "Manager":
            links_list.append({"name": "Apartemnts: Manager Apartments", "link": f"/apartments?q=manager.id={self.id}"})

        return links_list
     
# Apartment Model
class Apartment(models.Model):
    def __str__(self):
        return self.name
    TYPES = [
        ('In Management', 'In Management'),
        ('In Ownership', 'In Ownership'),
    ]
    STATUS = [
        ('Available', 'Available'),
        ('Unavailable', 'Unavailable'),
    ]
    name = CharFieldEx(max_length=255,  db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    web_link = URLFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    address = CharFieldEx(max_length=255, isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    bedrooms = IntegerFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    bathrooms = IntegerFieldEx(isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    apartment_type = CharFieldEx(max_length=15, db_index=True, choices=TYPES,  isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("apart_types"))
    status = CharFieldEx(max_length=14, db_index=True, choices=STATUS, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("apart_status"))
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    manager = ForeignKeyEx(User, on_delete=models.SET_NULL,  blank=True,  db_index=True, related_name='managed_apartments', null=True, limit_choices_to={'role': 'Manager'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("managers"), display_field='manager.full_name')
    owner = ForeignKeyEx(User, on_delete=models.SET_NULL,  blank=True, db_index=True, related_name='owned_apartments', null=True, limit_choices_to={'role': 'Owner'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("owners"), display_field='owner.full_name')

    
    @property 
    def links(self):
        links_list = []


        # Link to payments associated with this booking
        links_list.append({"name": "Payments: Apartment Payments", "link": f"/payments?q=booking.apartment.id={self.id}"})
        links_list.append({"name": "Contracts Apartment Contracts:", "link": f"/contracts?q=apartment.id={self.id}"})
        links_list.append({"name": "Cleanings: Apartment Cleanings", "link": f"/cleanings?q=booking.apartment.id={self.id}"})
        links_list.append({"name": "Bookings: Apartment Bookings", "link": f"/bookings?q=apartment.id={self.id}"})


        # Link to the manager of the associated apartment
        if self.manager:
            links_list.append({"name": f"Manager:{self.manager.full_name}", "link": f"/users?q=id={self.manager.id}"})

        # Link to the owner of the associated apartment
        if self.owner:
            links_list.append({"name": f"Owner: {self.owner.full_name}", "link": f"/users?q=id={self.owner.id}"})

        return links_list

# Bookings Model
class Booking(models.Model):
    def __str__(self):
        return str(self.apartment)

    STATUS = [
        ('Confirmed', 'Confirmed'),
        ('Canceled', 'Canceled'),
        ('Pending', 'Pending'),
    ]
    PERIOD = [
        ('Dayly', 'Dayly'),
        ('Monthly', 'Monthly'),
    ]
    
    
    tenant_email = EmailFieldEx(blank=True, null=True, editable=False, isEdit=False, isCreate=True, isColumn=False, ui_element="input")
    tenant_full_name = CharFieldEx(max_length=255, blank=True, null=True, editable=False, isEdit=False, isCreate=True, isColumn=False, ui_element="input")
    tenant_phone = CharFieldEx(max_length=20, blank=True, null=True, editable=False, isEdit=False, isCreate=True, isColumn=False, ui_element="input")
    assigned_cleaner = IntegerFieldEx(editable=False, null=True, blank=True, isColumn=False, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaners"))
   
    price = DecimalFieldEx(max_digits=32, decimal_places=2, isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    period = CharFieldEx(max_length=32, db_index=True, choices=PERIOD, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("booking_period"))
    status = CharFieldEx(max_length=32, db_index=True, blank=True, choices=STATUS, default='Pending', isColumn=True, isEdit=True, isCreate=False, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("booking_status"))
    start_date = DateFieldEx(db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    end_date = DateFieldEx(db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    holding_deposit = DecimalFieldEx(editable=False, max_digits=32, decimal_places=2, null=True, blank=True, isEdit=False, isCreate=True, isColumn=False, ui_element="input")
    damage_deposit = DecimalFieldEx(editable=False, max_digits=32, decimal_places=2, null=True, blank=True, isEdit=False, isCreate=True, isColumn=False, ui_element="input")
    
    tenant = ForeignKeyEx(User, on_delete=models.SET_NULL, db_index=True, related_name='bookings', null=True, limit_choices_to={'role': 'Tenant'}, blank=True, isColumn=True, isEdit=False, isCreate=False, ui_element="input", display_field='tenant.full_name')
    
    apartment = ForeignKeyEx(Apartment, on_delete=models.SET_NULL, db_index=True, related_name='booked_apartments', null=True, isColumn=True, isEdit=False, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("apartments"), display_field='apartment.name')

    @property
    def links(self):
        links_list = []
        
        # Link to the contract associated with this booking

        contract = self.contract.first()  # Fetch the associated contract using the reverse relationship
        if contract:
            links_list.append({"name": f"Contract: {contract.contract_id or contract.id}", "link": f"/contracts?q=id={contract.id}"})


        # Link to the tenant associated with this booking
        if self.tenant:
            links_list.append({"name": f"Tenant: {self.tenant.full_name}", "link": f"/users?q=id={self.tenant.id}"})

        

        if self.apartment:
            # Assuming the manager and owner are associated with the apartment
            manager = self.apartment.manager
            owner = self.apartment.owner

            links_list.append({"name": f"Apartment: {self.apartment.name}", "link": f"/apartments?q=id={self.apartment.id}"})

            if manager:
                links_list.append({"name": f"Manager: {manager.full_name}", "link": f"/users?q=id={manager.id}"})

            if owner:
                links_list.append({"name": f"Owner: {owner.full_name}", "link": f"/users?q=id={owner.id}"})
                
        payments = self.payments.all()
        for payment in payments:
            links_list.append({"name": f"Payment: {payment.payment_type} - {payment.amount}$ on {payment.payment_date} [{payment.payment_status}]", "link": f"/payments?q=id={payment.id}"})


        cleanings = self.cleanings.all()
        for cleaning in cleanings:
            cleaner = cleaning.cleaner
            if cleaner:
                links_list.append({"name": f"Cleaning: {cleaning.date} [{cleaning.status}]", "link": f"/cleanings?q=id={cleaning.id}"})
                links_list.append({"name": f"Cleaner: {cleaner.full_name}", "link": f"/users?q=id={cleaner.id}"})

        return links_list
    

    def handle_update(self):
        # Logic for updates, if any specific actions are required when a booking is updated
        pass
    
    def save(self, *args, **kwargs):
               
        if self.pk:  # If primary key exists, it's an update
            self.handle_update()
        else: 
            form_data = kwargs.pop('form_data', None)
            self.extract_and_set_temp_fields(form_data=form_data)
            self.get_or_create_tenant()
            super().save(*args, **kwargs)
            self.create_contract()
            self.schedule_cleaning()
            self.create_payments()
        super().save(*args, **kwargs)
    
    def extract_and_set_temp_fields(self, form_data):
        if form_data:
            # Extract necessary information from form_data
            holding_deposit_str = form_data.get('holding_deposit')
            damage_deposit_str = form_data.get('damage_deposit')
            
            # Convert to Decimal if the string is not empty or None
            self.holding_deposit = Decimal(holding_deposit_str) if holding_deposit_str else None
            self.damage_deposit = Decimal(damage_deposit_str) if damage_deposit_str else None
            
            self.assigned_cleaner = form_data.get('assigned_cleaner')
            self.tenant_email = form_data.get('tenant_email')
            self.tenant_full_name = form_data.get('tenant_full_name')
            self.tenant_phone = form_data.get('tenant_phone')
        
    def get_or_create_tenant(self):
        tenant_email = getattr(self, 'tenant_email', None)
        tenant_full_name = getattr(self, 'tenant_full_name', None)
        tenant_phone = getattr(self, 'tenant_phone', None)

        if tenant_email:
            # Check if a user with this email already exists
            user, created = User.objects.get_or_create(email=tenant_email, defaults={
                'full_name': tenant_full_name,
                'phone': tenant_phone,
                'role': 'Tenant',
                'password': User.objects.make_random_password()
            })

            # Assign the user to the booking's tenant field
            self.tenant = user           
                
    def create_payments(self):
        total_amount = self.calculate_total_amount()

        holding_deposit = getattr(self, 'holding_deposit', None)
        if holding_deposit:
            self.create_payment("Holding Deposit", holding_deposit, date.today())

        damage_deposit = getattr(self, 'damage_deposit', None)
        if damage_deposit:
            self.create_payment("Damage Deposit", damage_deposit, date.today())

        if self.period == 'Dayly':
            if holding_deposit:
                total_amount -= holding_deposit
            self.create_payment("Booking", total_amount, self.start_date)
        elif self.period == 'Monthly':
            months_of_booking = (self.end_date.year - self.start_date.year) * 12 + self.end_date.month - self.start_date.month
            for month in range(months_of_booking):
                payment_date = self.start_date + relativedelta(months=+month)
                if month == 0 and holding_deposit:  # If it's the first month and there's a holding deposit
                    self.create_payment("Booking", total_amount - holding_deposit, payment_date)
                else:
                    self.create_payment("Booking", total_amount, payment_date)               
    def calculate_total_amount(self):
        if self.period == 'Dayly':
            days_of_booking = (self.end_date - self.start_date).days
            return days_of_booking * self.price
        elif self.period == 'Monthly':
            # Assuming price is per month
            return self.price
        return 0        
    def create_contract(self):
        # Create a new contract instance and associate it with the booking
        contract = Contract(booking=self)
        contract.link = "https://demo.docusign.net/restapi"
        contract.contract_id = "1244-14d451-145d54"
        # Set other necessary fields for the contract if needed
        # ...
        contract.save()
        
        if False:
            # Integrate with DocuSign
            api_client = ApiClient()
            api_client.host = 'https://demo.docusign.net/restapi'  # Use the appropriate URL for your environment
            api_client.set_default_header("Authorization", "Bearer " + settings.DOCUSIGN_ACCESS_TOKEN)

            envelope_definition = EnvelopeDefinition(
                email_subject="Please sign this document",
                # Add more fields as required for your envelope definition
                # ...
            )

            # Add recipients, documents, and other necessary details to the envelope_definition
            # ...

            envelopes_api = EnvelopesApi(api_client)
            envelope_summary = envelopes_api.create_envelope(settings.DOCUSIGN_ACCOUNT_ID, envelope_definition=envelope_definition)

            # Update the contract with the DocuSign contract_id and web_link
            contract.contract_id = envelope_summary.envelope_id
            # Assuming you want to provide a signing link, you can create a RecipientViewRequest and get the URL
            view_request = RecipientViewRequest(
                return_url="YOUR_RETURN_URL_AFTER_SIGNING",
                client_user_id="UNIQUE_IDENTIFIER_FOR_THE_RECIPIENT",  # This should be unique for the signer
                authentication_method="None",  # Adjust as needed
                user_name="RECIPIENT_NAME",
                email="RECIPIENT_EMAIL"
            )
            view_response = envelopes_api.create_recipient_view(settings.DOCUSIGN_ACCOUNT_ID, envelope_summary.envelope_id, recipient_view_request=view_request)
            contract.link = view_response.url
            contract.save()
    def schedule_cleaning(self):
        # Schedule a cleaning for the day after the booking ends
        cleaning_date = self.end_date + timedelta(days=1)
        
        assigned_cleaner = getattr(self, 'assigned_cleaner', None)
        # Fetch the cleaner using the provided cleaner_id
        cleaner = User.objects.get(id=assigned_cleaner)
        
        cleaning = Cleaning(date=cleaning_date, booking=self, cleaner=cleaner)
        # Set other necessary fields for the cleaning if needed
        # ...
        cleaning.save()       
    def create_payment(self, payment_type, amount, payment_date):
    # Assuming you have a Payment model with fields 'type', 'amount', and 'booking'
        Payment.objects.create(
            payment_type=payment_type,
            amount=amount,
            booking=self,
            payment_date=payment_date
        )
    # Contract Model
class Contract(models.Model):
    def __str__(self):
        return str(self.contract_id)

    STATUS = [
        ('Signed', 'Signed'),
        ('Pending', 'Pending'),
        ('Canceled', 'Canceled'),
    ]

    contract_id = CharFieldEx(max_length=64, default='', isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    sign_date = DateFieldEx(db_index=True, blank=True, null=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    link = URLFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    status = CharFieldEx(max_length=32, db_index=True, choices=STATUS, default='Pending', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("contract_status"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    booking = ForeignKeyEx(Booking, on_delete=models.SET_NULL,  db_index=True, related_name='contract', null=True, isColumn=True, isEdit=True, isCreate=True,   blank=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("bookings"), display_field='booking.apartment.name') 


    @property
    def links(self):
        links_list = []

        # Link to the owner of the contract
        if self.booking and self.booking.apartment and self.booking.apartment.owner:
            links_list.append({"name": f"Owner: {self.booking.apartment.owner.full_name}", "link": f"/users?q=id={self.booking.apartment.owner.id}"})

        # Link to the apartment associated with the contract
        if self.booking and self.booking.apartment:
            links_list.append({"name": f"Apartment: {self.booking.apartment.name}", "link": f"/apartments?q=id={self.booking.apartment.id}"})

        # Link to the tenant of the contract
        if self.booking and self.booking.tenant:
            links_list.append({"name": f"Tenant: {self.booking.tenant.full_name}", "link": f"/users?q=id={self.booking.tenant.id}"})
            
        if self.booking :
            if self.booking:
                links_list.append({"name": f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date} to {self.booking.end_date} [{self.booking.status}]", "link": f"/bookings?q=id={self.booking.id}"})

        return links_list

# PaymentMethods Model
class PaymentMethod(models.Model):
    def __str__(self):
        return self.name
    
    TYPE = [
        ('Payment Method', 'Payment Method'),
        ('Bank', 'Bank'),
    ]
    name = CharFieldEx(max_length=50, unique=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    type = CharFieldEx(max_length=32, db_index=True, choices=TYPE, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_method_type"))
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def links(self):
        return []

# Payments Model
class Payment(models.Model):
    PAYMENT_STATUS = [
        ('Pending', 'Pending'),
        ('Received', 'Received'),
        ('Cancelled', 'Cancelled'),
    ]
    PAYMENT_TYPE = [
        ('Income', 'Income'),
        ('Outcome', 'Outcome'),
        ('Damage Deposit', 'Damage Deposit'),
        ('Hold Deposit', 'Hold Deposit'),
        ('Booking', 'Booking'),
    ]

    payment_date = DateFieldEx(db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    amount = DecimalFieldEx(max_digits=14, decimal_places=2, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    
    payment_type = CharFieldEx(max_length=32, db_index=True, choices=PAYMENT_TYPE, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_type"))
    
    payment_status = CharFieldEx(max_length=32, db_index=True, choices=PAYMENT_STATUS, default='Pending', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_status"))
   
    payment_method = ForeignKeyEx(PaymentMethod,  blank=True, on_delete=models.SET_NULL, db_index=True, related_name='payment_methods_payments', limit_choices_to={'type': 'Payment Method'}, null=True, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("payment_methods"), display_field='payment_method.name')
    
    bank = ForeignKeyEx(PaymentMethod,  blank=True, on_delete=models.SET_NULL, db_index=True, related_name='bank_payments', limit_choices_to={'type': 'Bank'}, null=True, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("banks"), display_field='bank.name')
    
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    
    booking = ForeignKeyEx(Booking, on_delete=models.SET_NULL,  db_index=True, related_name='payments', null=True, isColumn=True, isEdit=True, isCreate=True,   blank=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("bookings"), display_field='booking.apartment.name') 
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def links(self):
        links_list = []

        # Link to the associated booking
        if self.booking:
            links_list.append({"name": f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date} to {self.booking.end_date}", "link": f"/bookings?q=id={self.booking.id}"})

        return links_list

# Cleanings Model
class Cleaning(models.Model):
    def __str__(self):
        return str(self.booking.date)

    STATUS = [
        ('Scheduled', 'Scheduled'),
        ('Completed', 'Completed'),
        ('Canceled', 'Canceled'),
    ]

    date = DateFieldEx(db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    status = CharFieldEx(max_length=32, db_index=True, choices=STATUS, default='Scheduled', isColumn=True, isEdit=True, isCreate=False, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaning_status"))
    tasks = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    
    cleaner = ForeignKeyEx(User, on_delete=models.SET_NULL, db_index=True, related_name='cleanings', null=True, limit_choices_to={'role': 'Cleaner'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("cleaners"), display_field='cleaner.full_name')
    
    booking = ForeignKeyEx(Booking, db_index=True, on_delete=models.SET_NULL,  blank=True, null=True, related_name='cleanings', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options('bookings'), display_field='booking.apartment')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def links(self):
        links_list = []

        # Link to the booking associated with this cleaning
        if self.booking:
             links_list.append({"name": f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date} to {self.booking.end_date}", "link": f"/bookings?q=id={self.booking.id}"})

        return links_list

# Notifications Model
class Notification(models.Model):
    def __str__(self):
        return self.message

    STATUS = [
        ('Done', 'Done'),
        ('Pending', 'Pending'),
        ('Canceled', 'Canceled'),
    ]
    

    date = DateFieldEx(db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    status = CharFieldEx(max_length=32, db_index=True, choices=STATUS, default='Pending', isColumn=True, isEdit=True, isCreate=False, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options("notification_status"))
    send_in_telegram = BooleanFieldEx(db_index=True, default=True, isColumn=True, isEdit=True, isCreate=True, ui_element="checkbox")
    message = TextFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="textarea")
    booking = ForeignKeyEx(Booking, db_index=True,  blank=True, on_delete=models.SET_NULL, null=True, related_name='booking', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", _dropdown_options=lambda: get_dropdown_options('bookings'), display_field='booking.apartment')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def links(self):
        links_list = []

        # Link to the user associated with this notification
        if self.booking:
             links_list.append({"name": f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date} to {self.booking.end_date}", "link": f"/bookings?q=id={self.booking.id}"})

        return links_list

    
 