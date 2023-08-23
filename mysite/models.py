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
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, db_index=True)
    password = models.CharField(max_length=128, null=True, blank=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    role = models.CharField(max_length=14, choices=ROLES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    objects = CustomUserManager()
    notes = models.TextField(blank=True, null=True)
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']


# Properties Model
class Property(models.Model):
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
    name = models.CharField(max_length=255,  db_index=True)
    property_type = models.CharField(max_length=15, db_index=True, choices=TYPES)
    status = models.CharField(max_length=14, db_index=True, choices=STATUS )
    notes = models.TextField(blank=True, null=True)
    web_link = models.URLField(blank=True, null=True)
    address = models.CharField(max_length=255)
    bedrooms = models.IntegerField()
    bathrooms = models.IntegerField()
    manager = models.ForeignKey(User, on_delete=models.SET_NULL,  db_index=True, related_name='managed_properties', null=True, limit_choices_to={'role': 'Manager'})
    owner = models.ForeignKey(User, on_delete=models.SET_NULL,  db_index=True, related_name='owned_properties', null=True, limit_choices_to={'role': 'Owner'})
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Contracts Model
class Contract(models.Model):
    STATUS = [
        ('Signed', 'Signed'),
        ('Pending', 'Pending'),
        ('Panceled', 'Canceled'),
    ]
    sign_date = models.DateField(db_index=True, blank=True, null=True )
    link = models.URLField()
    tenant = models.ForeignKey(User, on_delete=models.CASCADE,  db_index=True, related_name='tenant_contracts', limit_choices_to={'role': 'Tenant'})
    status = models.CharField(max_length=32, db_index=True, choices=STATUS,  default='Pending')
    contract_id = models.CharField(max_length=64, default='')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, db_index=True, related_name='owner_contracts', limit_choices_to={'role': 'Owner'})
    property = models.ForeignKey(Property, on_delete=models.CASCADE, db_index=True, related_name='property_contracts')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Bookings Model
class Booking(models.Model):
    STATUS = [
        ('Confirmed', 'Confirmed'),
        ('Canceled', 'Canceled'),
        ('Pending', 'Pending'),
    ]
    PERIOD = [
        ('Dayly', 'Dayly'),
        ('Monthly', 'Monthly'),
    ]
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True,)
    tenant = models.ForeignKey(User, on_delete=models.SET_NULL, db_index=True,related_name='bookings', null=True, limit_choices_to={'role': 'Tenant'})
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, db_index=True, related_name='bookings', null=True)
    property = models.ForeignKey(Property, on_delete=models.SET_NULL, db_index=True, related_name='booked_properties', null=True)
    notes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Pending')
    period = models.CharField(max_length=32, db_index=True, choices=PERIOD)
    price = models.DecimalField(max_digits=32, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# PaymentMethods Model
class PaymentMethod(models.Model):
    method_name = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Banks Model
class Bank(models.Model):
    bank_name = models.CharField(max_length=50, unique=True)
    bank_details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Payments Model
class Payment(models.Model):
    PAYMENT_STATUS = [
        ('pending', 'Pending'),
        ('received', 'Received'),
        ('cancelled', 'Cancelled'),
    ]
    PAYMENT_TYPE = [
        ('Income', 'Income'),
        ('Outcome', 'Outcome'),
        ('Damage Deposit', 'Damage Deposit'),
        ('Hold Deposit', 'Hold Deposit'),
    ]
    payment_date = models.DateField(db_index=True)
    bank = models.ForeignKey(Bank, on_delete=models.SET_NULL, db_index=True, related_name='bank_payments', null=True)
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, db_index=True, related_name='payment_methods_payments', null=True)
    payment_status = models.CharField(max_length=32, db_index=True, choices=PAYMENT_TYPE,  default='Pending')
    payment_type = models.CharField(max_length=32, db_index=True, choices=PAYMENT_STATUS)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, db_index=True, related_name='payments', null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Cleanings Model
class Cleaning(models.Model):
    STATUS = [
        ('scheduled', 'Scheduled'),
        ('completed', 'Completed'),
        ('canceled', 'Canceled'),
    ]
    date = models.DateField()
    booking = models.ForeignKey(Booking, db_index=True, on_delete=models.CASCADE, related_name='cleanings')
    tasks = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    cleaner = models.ForeignKey(User, on_delete=models.SET_NULL,  db_index=True, related_name='cleanings', null=True, limit_choices_to={'role': 'Cleaner'})
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Scheduled')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

# Notifications Model
class Notification(models.Model):
    STATUS = [
        ('Done', 'Done'),
        ('Pending', 'Pending'),
        ('Canceled', 'Canceled'),
    ]
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Pending')
    message = models.TextField()
    sendInTelegram = models.BooleanField(db_index=True, default=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_index=True, related_name='notifications', null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)