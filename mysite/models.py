# mysite/models.py

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models

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
        self.isColumn = kwargs.pop('isColumn', False)
        self.isEdit = kwargs.pop('isEdit', False)
        self.isCreate = kwargs.pop('isCreate', False)
        self.ui_element = kwargs.pop('ui_element', None)
        self.dropdown_options = kwargs.pop('dropdown_options', None)
        super().__init__(*args, **kwargs)
   
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

    email = EmailFieldEx(unique=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    password = CharFieldEx(max_length=128, null=True, blank=True, isColumn=False, isEdit=True, isCreate=True, ui_element="input")
    full_name = CharFieldEx(max_length=255, db_index=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    phone = CharFieldEx(max_length=15, blank=True, null=True, isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    role = CharFieldEx(max_length=14, choices=ROLES, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": x[0], "label": x[1]} for x in ROLES])
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    objects = CustomUserManager()
    
    @classmethod
    def get_cleaners(cls):
        return cls.objects.filter(role='Cleaner')
    @classmethod
    def get_tenants(cls):
        return cls.objects.filter(role='Tenants')
    @classmethod
    def get_managers(cls):
        return cls.objects.filter(role='Manager')
    @classmethod
    def get_owners(cls):
        return cls.objects.filter(role='Owner')

    @property
    def links(self):
        links_list = []

        # For Tenant
        if self.role == "Tenant":
            links_list.append({"name": "Bookings", "link": f"/bookings?q=tenant.id={self.id}"})

        # For Cleaner
        if self.role == "Cleaner":
            links_list.append({"name": "Cleanings", "link": f"/cleanings?q=cleaner.id={self.id}"})

        # For Owner
        if self.role == "Owner":
            links_list.append({"name": "Owned Properties", "link": f"/properties?q=owner.id={self.id}"})

        # For Manager
        if self.role == "Manager":
            links_list.append({"name": "Managed Properties", "link": f"/properties?q=manager.id={self.id}"})

        return links_list
   
    
# Properties Model
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
    apartment_type = CharFieldEx(max_length=15, db_index=True, choices=TYPES,  isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": x[0], "label": x[1]} for x in TYPES])
    status = CharFieldEx(max_length=14, db_index=True, choices=STATUS, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": x[0], "label": x[1]} for x in STATUS])
    notes = TextFieldEx(blank=True, null=True, isColumn=False, isEdit=True, isCreate=True, ui_element="textarea")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    manager = ForeignKeyEx(User, on_delete=models.SET_NULL,  db_index=True, related_name='managed_properties', null=True, limit_choices_to={'role': 'Manager'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": manager.id, "label": manager.full_name} for manager in User.get_managers()], display_field='manager.full_name')
    owner = ForeignKeyEx(User, on_delete=models.SET_NULL, db_index=True, related_name='owned_properties', null=True, limit_choices_to={'role': 'Owner'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": owner.id, "label": owner.full_name} for owner in User.get_owners()], display_field='owner.full_name')
    
    @property 
    def links(self):
        links_list = []


        # Link to payments associated with this booking
        links_list.append({"name": "Payments", "link": f"/payments?q=booking.apartment.id={self.id}"})
        links_list.append({"name": "Contracts", "link": f"/contracts?q=apartment.id={self.id}"})
        links_list.append({"name": "Cleanings", "link": f"/cleanings?q=booking.apartment.id={self.id}"})


        # Link to the manager of the associated apartment
        if self.manager:
            links_list.append({"name": f"Manager: {self.manager.full_name}", "link": f"/users?q=id={self.manager.id}"})

        # Link to the owner of the associated apartment
        if self.owner:
            links_list.append({"name": f"Owner: {self.owner.full_name}", "link": f"/users?q=id={self.owner.id}"})

        return links_list

class Contract(models.Model):
    def __str__(self):
        return str(self.contract_id)

    STATUS = [
        ('Signed', 'Signed'),
        ('Pending', 'Pending'),
        ('Canceled', 'Canceled'),
    ]

    contract_id = CharFieldEx(max_length=64, unique=True, default='', isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    sign_date = DateFieldEx(db_index=True, blank=True, null=True, isColumn=True, isEdit=True, isCreate=True, ui_element="datepicker")
    link = URLFieldEx(isColumn=True, isEdit=True, isCreate=True, ui_element="input")
    status = CharFieldEx(max_length=32, db_index=True, choices=STATUS, default='Pending', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": x[0], "label": x[1]} for x in STATUS])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    owner = ForeignKeyEx(User, on_delete=models.CASCADE, db_index=True, related_name='owner_contracts', limit_choices_to={'role': 'Owner'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": owner.id, "label": owner.full_name} for owner in User.objects.filter(role='Owner')], display_field='owner.full_name')
    
    appartment = ForeignKeyEx(Apartment, on_delete=models.CASCADE, db_column='apartment', db_index=True, related_name='apartment_contracts', isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": prop.id, "label": prop.name} for prop in Apartment.objects.all()], display_field='apartment.name')
    
    tenant = ForeignKeyEx(User, on_delete=models.CASCADE, db_index=True, related_name='tenant_contracts', limit_choices_to={'role': 'Tenant'}, isColumn=True, isEdit=True, isCreate=True, ui_element="dropdown", dropdown_options=[{"value": tenant.id, "label": tenant.full_name} for tenant in User.objects.filter(role='Tenant')], display_field='tenant.full_name')

    def get_links(self):
        links_list = []

        # Link to the owner of the contract
        if self.owner:
            links_list.append({"name": f"Owner: {self.owner.full_name}", "link": f"/users?q=id={self.owner.id}"})

        # Link to the apartment associated with the contract
        if self.apartment:
            links_list.append({"name": f"Apartment: {self.appartment.name}", "link": f"/properties?q=id={self.appartment.id}"})

        # Link to the tenant of the contract
        if self.tenant:
            links_list.append({"name": f"Tenant: {self.tenant.full_name}", "link": f"/users?q=id={self.tenant.id}"})

        return links_list
    
    links = property(get_links) 


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
    price = models.DecimalField(max_digits=32, decimal_places=2)
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True,)
    period = models.CharField(max_length=32, db_index=True, choices=PERIOD)
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Pending')
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tenant = models.ForeignKey(User, on_delete=models.SET_NULL, db_index=True,related_name='bookings', null=True, limit_choices_to={'role': 'Tenant'})
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, db_index=True, related_name='bookings', null=True)
    apartment = models.ForeignKey(Apartment, on_delete=models.SET_NULL, db_index=True, related_name='booked_properties', null=True)

# PaymentMethods Model
class PaymentMethod(models.Model):
    def __str__(self):
        return self.method_name
    method_name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Banks Model
class Bank(models.Model):
    def __str__(self):
        return self.bank_name
    bank_name = models.CharField(max_length=50, unique=True)
    bank_details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    ]
    payment_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_type = models.CharField(max_length=32, db_index=True, choices=PAYMENT_TYPE)
    payment_status = models.CharField(max_length=32, db_index=True, choices=PAYMENT_STATUS,  default='Pending')
    bank = models.ForeignKey(Bank, on_delete=models.SET_NULL, db_index=True, related_name='bank_payments', null=True)
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, db_index=True, related_name='payment_methods_payments', null=True)
    notes = models.TextField(blank=True, null=True)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, db_index=True, related_name='payments', null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Cleanings Model
class Cleaning(models.Model):
    def __str__(self):
        return str(self.booking.apartment)
    STATUS = [
        ('Scheduled', 'Scheduled'),
        ('Completed', 'Completed'),
        ('Canceled', 'Canceled'),
    ]
    date = models.DateField()
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Scheduled')
    tasks = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    cleaner = models.ForeignKey(User, on_delete=models.SET_NULL,  db_index=True, related_name='cleanings', null=True, limit_choices_to={'role': 'Cleaner'})
    booking = models.ForeignKey(Booking, db_index=True, on_delete=models.CASCADE, related_name='cleanings')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Notifications Model
class Notification(models.Model):
    def __str__(self):
        return self.message
    STATUS = [
        ('Done', 'Done'),
        ('Pending', 'Pending'),
        ('Canceled', 'Canceled'),
    ]
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Pending')
    sendInTelegram = models.BooleanField(db_index=True, default=True)
    message = models.TextField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_index=True, related_name='notifications', null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
   