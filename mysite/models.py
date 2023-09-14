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
from datetime import datetime
import os

def convert_date_format(date_str):
    # Parse the date from MM/DD/YYYY format
    date_obj = datetime.strptime(date_str, '%m/%d/%Y')
    
    # Convert the date object to YYYY-MM-DD format
    return date_obj.strftime('%Y-%m-%d')

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
    ROLES = [
        ('Admin', 'Admin'),
        ('Cleaner', 'Cleaner'),
        ('Manager', 'Manager'),
        ('Tenant', 'Tenant'),
        ('Owner', 'Owner'),
    ]

    email = models.EmailField(unique=True)
    password = models.CharField(max_length=128, null=True, blank=True)
    full_name = models.CharField(max_length=255, db_index=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    role = models.CharField(max_length=14, choices=ROLES)
    notes = models.TextField(blank=True, null=True)
   
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    objects = CustomUserManager()

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        # Check if the password is not hashed
        if self.password and not self.password.startswith(('pbkdf2_sha256$', 'bcrypt')):
            self.password = make_password(self.password)
        super(User, self).save(*args, **kwargs)

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

    name = models.CharField(max_length=255, db_index=True)
    web_link = models.URLField(blank=True, null=True)
    house_number = models.CharField(max_length=10)
    street = models.CharField(max_length=255)
    room = models.CharField(max_length=10, blank=True, null=True)  # optional
    state = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    zip_index = models.CharField(max_length=10)
    bedrooms = models.IntegerField()
    bathrooms = models.IntegerField()
    apartment_type = models.CharField(max_length=15, db_index=True, choices=TYPES)
    status = models.CharField(max_length=14, db_index=True, choices=STATUS)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    manager = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, db_index=True, related_name='managed_apartments', null=True, limit_choices_to={'role': 'Manager'})
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, db_index=True, related_name='owned_apartments', null=True, limit_choices_to={'role': 'Owner'})


   
    @property 
    def links(self):
        links_list = []


        # Link to payments associated with this booking
        links_list.append({"name": "Payments: Apartment Payments", "link": f"/payments?q=booking.apartment.id={self.id}"})
        links_list.append({"name": "Contracts: Apartment Contracts:", "link": f"/contracts?q=apartment.id={self.id}"})
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
    
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    status = models.CharField(max_length=32, db_index=True, blank=True, choices=STATUS, default='Pending')
    apartment = models.ForeignKey(Apartment, on_delete=models.SET_NULL, db_index=True, related_name='booked_apartments', null=True)
    notes = models.TextField(blank=True, null=True)
    tenant = models.ForeignKey(User, on_delete=models.SET_NULL, db_index=True, related_name='bookings', null=True, limit_choices_to={'role': 'Tenant'}, blank=True)
   
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
       
    
    def save(self, *args, **kwargs):
        if self.pk:  # If primary key exists, it's an update
            form_data = kwargs.pop('form_data', None)
            payments_data = kwargs.pop('payments_data', None)
            self.create_payments(payments_data)
            super().save(*args, **kwargs)
        else: 
            form_data = kwargs.pop('form_data', None)
            payments_data = kwargs.pop('payments_data', None)
            if form_data:
                self.get_or_create_tenant(form_data)
                super().save(*args, **kwargs)  # Save the booking instance first to get an ID
                self.create_contract()
                self.schedule_cleaning(form_data)
                self.create_payments(payments_data)
            else:
                super().save(*args, **kwargs)

        
    def get_or_create_tenant(self, form_data):
        tenant_email = form_data.get('tenant_email')
        tenant_full_name = form_data.get('tenant_full_name')
        tenant_phone = form_data.get('tenant_phone')

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
                
    def create_payments(self, payments_data):
        payment_dates = payments_data.get('payment_dates', [])
        amounts = payments_data.get('amounts', [])
        payment_types = payments_data.get('payment_types', [])

        # Convert the dates to the expected format
        payment_dates = [convert_date_format(date) for date in payment_dates]

        # Now, you can iterate over these lists and process the payments
        for date, amount, p_type in zip(payment_dates, amounts, payment_types):
            # Your logic to create a payment instance or whatever you need to do
            self.create_payment(p_type, amount, date)              
    
    
    def create_contract(self):
        # Create a new contract instance and associate it with the booking
        contract = Contract(booking=self)
        contract.link = "https://demo.docusign.net/restapi"
        contract.contract_id = "1244-14d451-145d54"
        # Set other necessary fields for the contract if needed
        # ...
        contract.save()
    
    def send_to_docusign(self):
        DOCUSIGN_ACCOUNT_ID = os.environ("DOCUSIGN_ACCOUNT_ID")
        DOCUSIGN_ACCOUNT_ID = os.environ("DOCUSIGN_ACCESS_TOKEN")
        
        # Initialize the DocuSign client
        api_client = ApiClient()
        api_client.host = 'https://demo.docusign.net/restapi'
        api_client.set_default_header("Authorization", "Bearer YOUR_ACCESS_TOKEN")

        # Create an envelope
        envelope_definition = EnvelopeDefinition(
            email_subject="Please sign this contract",
            status="sent"  # Automatically sends the envelope
        )

        # Add the document to the envelope
        # ... (You'll need to define the document content and add it to the envelope)

        # Add recipients (for example, the tenant)
        signer = Signer(
            email=self.tenant.email,
            name=self.tenant.full_name,
            recipient_id="1",
            routing_order="1"
        )

        # Specify where the recipient needs to sign
        sign_here = SignHere(document_id="1", page_number="1", x_position="100", y_position="100")
        signer.tabs = Tabs(sign_here_tabs=[sign_here])
        envelope_definition.recipients = Recipients(signers=[signer])

        # Send the envelope
        envelopes_api = EnvelopesApi(api_client)
        envelope_summary = envelopes_api.create_envelope("YOUR_ACCOUNT_ID", envelope_definition=envelope_definition)

        # Store the contract details
        contract = Contract(booking=self)
        contract.link = f"https://demo.docusign.net/Signing/startinsession.aspx?t={envelope_summary.envelope_id}"
        contract.contract_id = envelope_summary.envelope_id
        contract.save()
        
    
    def schedule_cleaning(self, form_data):
        # Schedule a cleaning for the day after the booking ends
        cleaning_date = self.end_date + timedelta(days=1)
        assigned_cleaner = form_data.get('assigned_cleaner')
        cleaning = Cleaning(date=cleaning_date, booking=self, cleaner=assigned_cleaner)
        cleaning.save()
        
        # Create a notification for the scheduled cleaning
        notification_message = f"A cleaning at {cleaning_date} by {assigned_cleaner}. in apartment {self.apartment.name}."
        notification = Notification(
            date=cleaning_date,
            send_in_telegram=True,
            message=notification_message,
            booking=self
        )
        notification.save()       
        notification_message = f"End of booking {self.end_date} for apartment {self.apartment.name}.  Tenant: {self.tenant.full_name}."
        notification = Notification(
            date=cleaning_date,
            send_in_telegram=True,
            message=notification_message,
            booking=self
        )
        notification.save()       
        
    def create_payment(self, payment_type, amount, payment_date):
    # Assuming you have a Payment model with fields 'type', 'amount', and 'booking'
        Payment.objects.create(
            payment_type=payment_type,
            amount=amount,
            booking=self,
            payment_date=payment_date
        )
        notification_message = f"A payment of {amount} at {payment_date} [{payment_type}]. for apartment {self.apartment.name}."
        notification = Notification(
            date=payment_date,
            send_in_telegram=True,
            message=notification_message,
            booking=self
        )
        notification.save()
    
    @property
    def assigned_cleaner(self):
        cleaning = self.cleanings.first()  # Assuming one cleaning per booking
        return cleaning.cleaner if cleaning else None
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
# Contract Model
class Contract(models.Model):
    def __str__(self):
        return str(self.contract_id)

    STATUS = [
        ('Signed', 'Signed'),
        ('Pending', 'Pending'),
        ('Canceled', 'Canceled'),
    ]

    contract_id = models.CharField(max_length=64, default='', db_index=True)
    sign_date = models.DateField(db_index=True, blank=True, null=True)
    link = models.URLField()
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Pending')
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, db_index=True, related_name='contract', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    name = models.CharField(max_length=50, unique=True)
    type = models.CharField(max_length=32, db_index=True, choices=TYPE)
    notes = models.TextField(blank=True, null=True)


    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def links(self):
        return []

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

    payment_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_type = models.CharField(max_length=32, db_index=True, choices=PAYMENT_TYPE)
    payment_status = models.CharField(max_length=32, db_index=True, choices=PAYMENT_STATUS, default='Pending')
    payment_method = models.ForeignKey(PaymentMethod, blank=True, on_delete=models.SET_NULL, db_index=True, related_name='payment_methods_payments', limit_choices_to={'type': 'Payment Method'}, null=True)
    bank = models.ForeignKey(PaymentMethod, blank=True, on_delete=models.SET_NULL, db_index=True, related_name='bank_payments', limit_choices_to={'type': 'Bank'}, null=True)
    notes = models.TextField(blank=True, null=True)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, db_index=True, related_name='payments', null=True, blank=True)
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
        return str(self.date)

    STATUS = [
        ('Scheduled', 'Scheduled'),
        ('Completed', 'Completed'),
        ('Canceled', 'Canceled'),
    ]

    date = models.DateField(db_index=True)
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Scheduled')
    tasks = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    cleaner = models.ForeignKey(User, on_delete=models.SET_NULL, db_index=True, related_name='cleanings', null=True, limit_choices_to={'role': 'Cleaner'})
    booking = models.ForeignKey(Booking, db_index=True, on_delete=models.CASCADE,  blank=True, null=True, related_name='cleanings')
    
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
    

    date = models.DateField(db_index=True)
    status = models.CharField(max_length=32, db_index=True, choices=STATUS, default='Pending')
    send_in_telegram = models.BooleanField(db_index=True, default=True)
    message = models.TextField()
    booking = models.ForeignKey(Booking, db_index=True, blank=True, on_delete=models.CASCADE, null=True, related_name='booking_notifications')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def links(self):
        links_list = []

        # Link to the user associated with this notification
        if self.booking:
             links_list.append({"name": f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date} to {self.booking.end_date}", "link": f"/bookings?q=id={self.booking.id}"})

        return links_list

    
 