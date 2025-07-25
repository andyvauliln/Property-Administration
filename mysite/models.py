# mysite/models.py

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.contrib.auth.hashers import make_password
from dateutil.relativedelta import relativedelta
from datetime import datetime
from datetime import datetime, date
from mysite.docuseal_contract_managment import create_contract, sendWelcomeMessageToTwilio, update_contract, delete_contract
from itertools import zip_longest
import re
import uuid
import requests
import os

def convert_date_format(value):
    if isinstance(value, date):
        # If date_str is already a date object, return it as is
        return value

    try:
        return datetime.strptime(value, '%B %d %Y').date()
    except ValueError:
        pass

    raise ValueError(f"Invalid date format: {value}")



def format_phone(phone):
    if phone:
        cleaned_phone = re.sub(r'\D', '', phone)
        
        if phone.startswith('+'):
            return "+" + cleaned_phone

        elif phone.startswith('0'):
            return "+1" + cleaned_phone[1:]

        else:
            return "+" + cleaned_phone
    else:
        return ""


class CustomUserManager(BaseUserManager):
    def create_user(self, email=None, password=None, **extra_fields):
        if not email:
            email = f"tenant_{uuid.uuid4()}@example.com"
        email = self.normalize_email(email)
        if not extra_fields.get('full_name'):
            extra_fields['full_name'] = "Not Availabale"
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
    phone = models.CharField(max_length=20, blank=True, null=True)
    role = models.CharField(max_length=14, choices=ROLES)
    notes = models.TextField(blank=True, null=True)
    telegram_chat_id = models.CharField(max_length=255, blank=True, null=True)

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
        self.phone = format_phone(self.phone)
        super(User, self).save(*args, **kwargs)

    @property
    def links(self):
        links_list = []

        # For Tenant
        if self.role == "Tenant":
            links_list.append({"name": "Bookings: User Bookings",
                              "link": f"/bookings/?q=tenant.id={self.id}"})

        # For Cleaner
        if self.role == "Cleaner":
            links_list.append({"name": "Cleanings: User Cleanings",
                              "link": f"/cleanings/?q=cleaner.id={self.id}"})

        # For Owner
        if self.role == "Owner":
            links_list.append({"name": "Apartments: Owner Apartments",
                              "link": f"/apartments/?q=owner.id={self.id}"})

        # For Manager
        if self.role == "Manager":
            links_list.append({"name": "Apartemnts: Manager Apartments",
                              "link": f"/apartments/?q=manager.id={self.id}"})

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
    building_n = models.CharField(max_length=10)
    street = models.CharField(max_length=255)
    apartment_n = models.CharField(
        max_length=10, blank=True, null=True)  # optional
    state = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    zip_index = models.CharField(max_length=10)
    bedrooms = models.IntegerField()
    bathrooms = models.IntegerField()
    apartment_type = models.CharField(max_length=15, db_index=True, choices=TYPES)
    status = models.CharField(max_length=14, db_index=True, choices=STATUS)
    notes = models.TextField(blank=True, null=True)
    start_date = models.DateTimeField(blank=True, null=True, db_index=True)
    end_date = models.DateTimeField(blank=True, null=True, db_index=True)
    keywords = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    raiting = models.DecimalField(blank=True, null=True, default=0, max_digits=2, decimal_places=1)
    default_price = models.DecimalField(blank=True, null=True, default=0, max_digits=10, decimal_places=2)

    manager = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, db_index=True,
                                related_name='managed_apartments', null=True, limit_choices_to={'role': 'Manager'})
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, db_index=True,
                              related_name='owned_apartments', null=True, limit_choices_to={'role': 'Owner'})

    @property
    def address(self):
        return f" {self.building_n} {self.street}, {self.apartment_n}, {self.state}, {self.city}, {self.zip_index}"

    @property
    def current_price(self):
        """Get the price that is effective on the current date"""
        from datetime import date
        today = date.today()
        current_price_record = self.prices.filter(effective_date__lte=today).order_by('-effective_date').first()
        return current_price_record.price if current_price_record else None

    def get_price_on_date(self, target_date):
        """Get the price that was effective on a specific date"""
        price_record = self.prices.filter(effective_date__lte=target_date).order_by('-effective_date').first()
        return price_record.price if price_record else None

    def get_future_prices(self):
        """Get all price changes scheduled for the future"""
        from datetime import date
        today = date.today()
        return self.prices.filter(effective_date__gt=today).order_by('effective_date')

    def payment_revenue(self, start_date, end_date):
        if start_date and end_date:
            payments = self.payments.filter(payment_date__gte=start_date, payment_date__lte=end_date)
        else:
            payments = self.payments.all()
        revenue = 0
        for payment in payments:
            if payment.payment_type.type == "In":
                revenue += payment.amount
            else:
                revenue -= payment.amount
        return revenue
    
    @property
    def links(self):
        links_list = []
        
        # Pricing information
        current_price = self.current_price
        price_count = self.prices.count()
        future_count = self.get_future_prices().count()
        
        if current_price:
            price_status = f"Current: ${current_price}"
            if future_count > 0:
                price_status += f" ({future_count} future change{'s' if future_count != 1 else ''})"
        else:
            price_status = "No current price"
        
        links_list.append({
            "name": f"Pricing: {price_status} ({price_count} total)", 
            "link": f"/apartmentprice/?apartment={self.id}"
        })
        
        links_list.append({"name": "Booking Payments: Booking Payments",
                          "link": f"/payments/?q=booking.apartment.id={self.id}"})
        links_list.append({"name": "Apartment Payments: Apartment Payments",
                           "link": f"/payments/?q=apartment.id={self.id}"})
        # links_list.append({"name": "Contracts: Apartment Contracts:", "link": f"/contracts?q=apartment.id={self.id}"})
        links_list.append({"name": "Cleanings: Apartment Cleanings",
                          "link": f"/cleanings/?q=booking.apartment.id={self.id}"})
        links_list.append({"name": "Bookings: Apartment Bookings",
                          "link": f"/bookings/?q=apartment.id={self.id}"})

        if self.manager:
            links_list.append({"name": f"Manager:{self.manager.full_name}",
                              "link": f"/users/?q=id={self.manager.id}"})

        if self.owner:
            links_list.append(
                {"name": f"Owner: {self.owner.full_name}", "link": f"/users/?q=id={self.owner.id}"})

        return links_list


class ApartmentPrice(models.Model):
    """Model to track apartment pricing history"""
    
    def __str__(self):
        return f"{self.apartment.name} - ${self.price} (effective {self.effective_date})"

    apartment = models.ForeignKey(Apartment, on_delete=models.CASCADE, db_index=True,
                                  related_name='prices')
    price = models.DecimalField(max_digits=10, decimal_places=2)
    effective_date = models.DateField(db_index=True, help_text="Date when this price becomes effective")
    notes = models.TextField(blank=True, null=True, help_text="Optional notes about this price change")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_date']
        unique_together = ['apartment', 'effective_date']

    @property
    def links(self):
        links_list = []
        links_list.append({"name": f"Apartment: {self.apartment.name}",
                          "link": f"/apartments/?q=id={self.apartment.id}"})
        return links_list


class Booking(models.Model):
    def __str__(self):
        return str(self.apartment)

    STATUS = [
        ('Confirmed', 'Confirmed'),
        ('Waiting Contract', 'Waiting Contract'),
        ('Waiting Payment', 'Waiting Payment'),
        ('Blocked', 'Blocked'),
        ('Pending', 'Pending'),
        ('Problem Booking', 'Problem Booking'),
    ]
    ANIMALS = [
        ('Cat', 'Cat'),
        ('Dog', 'Dog'),
        ('Other', 'Other'),
    ]
    SOURCE = [
        ('Airbnb', 'Airbnb'),
        ('Referral', 'Referral'),
        ('Returning', 'Returning'),
        ('Other', 'Other'),
    ]
    VISIT_PURPOSE = [
        ('Tourism', 'Tourism'),
        ('Work Travel', 'Work Travel'),
        ('Medical', 'Medical'),
        ('House Repair', 'House Repair'),
        ('Relocation', 'Relocation'),
        ('Other', 'Other'),
    ]

    contract_url = models.TextField(blank=True, null=True)
    contract_id = models.TextField(blank=True, null=True)

    contract_send_status = models.TextField(
        blank=True, null=True, default="Not Sent")
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    tenants_n = models.DecimalField(
        max_digits=2, decimal_places=0, null=True, blank=True)
    status = models.CharField(max_length=32, db_index=True,
                              blank=True, choices=STATUS, default='Waiting Contract')
    animals = models.CharField(max_length=32, blank=True, choices=ANIMALS)
    visit_purpose = models.CharField(
        max_length=32, blank=True, choices=VISIT_PURPOSE)
    source = models.CharField(max_length=32, blank=True, choices=SOURCE)
    apartment = models.ForeignKey(Apartment, on_delete=models.SET_NULL, db_index=True,
                                  related_name='booked_apartments', null=True)
    notes = models.TextField(blank=True, null=True)
    other_tenants = models.TextField(blank=True, null=True)

    keywords = models.TextField(blank=True, null=True)

    tenant = models.ForeignKey(
        User, on_delete=models.SET_NULL, db_index=True, related_name='bookings', null=True, blank=True)

    is_rent_car = models.BooleanField(null=True, blank=True, verbose_name="Is Rent Car")
    car_model = models.CharField(
        max_length=100, default="", verbose_name="Car Model")
    car_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Car Price")
    car_rent_days = models.IntegerField(
        default=0, verbose_name="Car Rent Days")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def saveEmpty(self, *args, **kwargs):
        form_data = kwargs.pop('form_data', None)
        self.get_or_create_tenant(form_data)
        super().save(*args, **kwargs)

    def update(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        parking_number = kwargs.pop('parking_number', None)
        if self.pk:  # If primary key exists, it's an update
            form_data = kwargs.pop('form_data', None)
            payments_data = kwargs.pop('payments_data', None)
            
            self.get_or_create_tenant(form_data)
            # self.create_payments(payments_data)
            orig = Booking.objects.get(pk=self.pk)
            # If start_date has changed
            if orig.start_date != self.start_date:
                # Update the related Notification
                Notification.objects.filter(
                    booking=self,
                    message="Start Booking"
                ).update(date=self.start_date)

            # If end_date has changed
            if orig.end_date != self.end_date:
                if self.end_date < orig.end_date:
                    # delete all payments except return deposit
                    self.deletePayments()

                # Change date for return deposit to the self.end_date
                Payment.objects.filter(
                    booking=self,
                    payment_type__name="Damage Deposit",
                    payment_type__type="Out"
                ).update(payment_date=self.end_date)

                # Change cleaning date to the self.end_date
                # Assuming Cleaning is a model related to Booking
                
                # First update just the date for all related cleanings
                Cleaning.objects.filter(
                    booking=self).update(date=self.end_date)

                # We'll handle the cleaner update separately in the code below

                Notification.objects.filter(
                    payment__booking=self,
                    payment__payment_type__name="Damage Deposit",
                    payment__payment_type__type="Out"
                ).update(date=self.end_date)

                # Update Notifications for Cleanings related to this Booking
                Notification.objects.filter(
                    cleaning__booking=self).update(date=self.end_date)

                # Update the related Notification
                Notification.objects.filter(
                    booking=self,
                    message="End Booking"
                ).update(date=self.end_date)

            # Update parking booking dates if start_date or end_date changed
            if orig.start_date != self.start_date or orig.end_date != self.end_date:
                ParkingBooking.objects.filter(
                    booking=self
                ).update(
                    start_date=self.start_date,
                    end_date=self.end_date
                )

            # Create or update payments
            self.create_payments(payments_data)
            if not self.cleanings.exists():
                self.schedule_cleaning(form_data)
            else:
                # Check if assigned_cleaner is present in form_data, even if it's None
                if 'assigned_cleaner' in form_data:
                    assigned_cleaner = form_data.get('assigned_cleaner')
                    cleaning = self.cleanings.first()
                    
                    # If assigned_cleaner is None or empty, explicitly set cleaner to None
                    if assigned_cleaner is None or assigned_cleaner == "" or assigned_cleaner == 0:
                        cleaning.cleaner = None
                        cleaning.save()
                    else:
                        # Handle the case where a cleaner is specified
                        if isinstance(assigned_cleaner, int):
                            try:
                                assigned_cleaner = User.objects.get(id=assigned_cleaner)
                            except User.DoesNotExist:
                                assigned_cleaner = None
                        elif isinstance(assigned_cleaner, str) and assigned_cleaner.isdigit():
                            try:
                                assigned_cleaner = User.objects.get(id=int(assigned_cleaner))
                            except User.DoesNotExist:
                                assigned_cleaner = None

                        cleaning.cleaner = assigned_cleaner
                        cleaning.save()

            super().save(*args, **kwargs)

            # Check for new or modified payments
            
            # Update Contract
            if self.contract_id:
                update_contract(self)
            # SEND CONTRACT
            if form_data and form_data["send_contract"] and form_data["send_contract"] != 0 and form_data["send_contract"] != None and form_data["send_contract"] != "None":
                create_contract(self, template_id=form_data["send_contract"], send_sms=form_data["create_chat"]
                )
            elif form_data and form_data["create_chat"]:
                sendWelcomeMessageToTwilio(self)
        else:
            form_data = kwargs.pop('form_data', None)
            payments_data = kwargs.pop('payments_data', None)
            if form_data:
                self.get_or_create_tenant(form_data)
                super().save(*args, **kwargs)  # Save the booking instance first to get an ID
                if not self.cleanings.exists():
                    self.schedule_cleaning(form_data)
                self.create_booking_notifications()
                
            if payments_data:
                self.create_payments(payments_data)

            super().save(*args, **kwargs)
             # SEND CONTRACT
            if form_data and form_data["send_contract"] and form_data["send_contract"] != 0 and form_data["send_contract"] != None and form_data["send_contract"] != "None":
                create_contract(self, template_id=form_data["send_contract"], send_sms=form_data["create_chat"])
            elif form_data and form_data["create_chat"]:
                sendWelcomeMessageToTwilio(self)

            # Update apartment keywords
            if self.apartment and self.tenant and self.tenant.full_name:
                if not self.apartment.keywords:
                    self.apartment.keywords = self.tenant.full_name
                else:
                    self.apartment.keywords += f", {self.tenant.full_name}"
                self.apartment.save()
        #update 
        if parking_number:
            parking = Parking.objects.get(id=parking_number)
            parking_booking = ParkingBooking.objects.create(
                parking=parking,
                booking=self,
                status= "Booked" if (self.is_rent_car or self.car_model or self.car_price or self.car_rent_days) else "No Car",
                start_date=self.start_date,
                end_date=self.end_date,
                apartment=self.apartment
            )
            parking_booking.save()
       
    def deletePayments(self):
        payments_to_delete = Payment.objects.filter(
            booking=self,
            payment_date__gt=self.end_date,
        ).exclude(payment_type__name="Damage Deposit", payment_type__type="Out")

        # Delete the filtered payments
        payments_to_delete.delete()

    def delete(self, *args, **kwargs):
        if self.contract_id != "" and self.contract_id != None:
            delete_contract(self.contract_id)

        # Delete related parking bookings
        ParkingBooking.objects.filter(booking=self).delete()

        super(Booking, self).delete(*args, **kwargs)

    def get_or_create_tenant(self, form_data):
        if form_data:
            tenant_email = form_data.get('tenant_email')
            tenant_full_name = form_data.get('tenant_full_name')
            tenant_phone = form_data.get('tenant_phone')

            if tenant_email:
                # Try to retrieve an existing user with the given email
                user = User.objects.filter(email=tenant_email).first()

                if user:
                    # If the user exists, update the full_name and phone fields
                    user.full_name = tenant_full_name
                    user.phone = tenant_phone
                    user.save()
                else:
                    # If the user doesn't exist, create a new one
                    user = User.objects.create(
                        email=tenant_email,
                        full_name=tenant_full_name,
                        phone=tenant_phone,
                        role='Tenant',
                        password=User.objects.make_random_password()
                    )

                # Assign the user to the booking's tenant field
                self.tenant = user

    def create_payments(self, payments_data):
        print("CREATE PAYMENT")
        if payments_data:
            payment_dates = payments_data.get('payment_dates', [])
            amounts = payments_data.get('amounts', [])
            payment_types = payments_data.get('payment_types', [])
            payment_notes = payments_data.get('payment_notes', [])
            number_of_months = payments_data.get('number_of_months', [])
            payment_ids = payments_data.get('payment_id', [])
            payment_statuses = payments_data.get('payment_status', [])
            print(payment_dates, amounts, payment_types, payment_notes, number_of_months, payment_ids, payment_statuses, "payment_dates")
            # Convert the dates to the expected format
            payment_dates = [convert_date_format(
                date) for date in payment_dates]

            for date, amount, p_type, p_notes, n_months, payment_id, payment_status in zip_longest(
                payment_dates, amounts, payment_types, payment_notes, number_of_months, payment_ids, payment_statuses, fillvalue=None
            ):
                self.create_payment(p_type, amount, date, p_notes, n_months, payment_id, payment_status)

    def create_booking_notifications(self):

        notification = Notification(
            date=self.end_date,
            message="End Booking",
            booking=self
        )
        notification.save()

        notification = Notification(
            date=self.start_date,
            message="Start Booking",
            booking=self
        )
        notification.save()

    def schedule_cleaning(self, form_data):
        # Schedule a cleaning for the day after the booking ends
        cleaning_date = self.end_date
        assigned_cleaner = form_data.get('assigned_cleaner')
        
        if assigned_cleaner:
            # Check if assigned_cleaner is an ID (integer)
            if isinstance(assigned_cleaner, int):
                try:
                    assigned_cleaner = User.objects.get(id=assigned_cleaner)
                except User.DoesNotExist:
                    assigned_cleaner = None
            elif isinstance(assigned_cleaner, str) and assigned_cleaner.isdigit():
                try:
                    assigned_cleaner = User.objects.get(id=int(assigned_cleaner))
                except User.DoesNotExist:
                    assigned_cleaner = None

            if assigned_cleaner and isinstance(assigned_cleaner, User):
                cleaning = Cleaning(date=cleaning_date,
                                    booking=self, cleaner=assigned_cleaner)
                cleaning.save()

    def create_payment(self, payment_type_id, amount, payment_date, payment_notes, number_of_months, payment_id, payment_status):
        payment_type_instance = PaymenType.objects.get(pk=payment_type_id)
        print("Create PAYMENT 2")

        if payment_id:
            if payment_id.endswith("_deleted"):
                payment_id = payment_id[:-8]
                payment = Payment.objects.get(pk=payment_id)
                payment.delete()
            else:
                payment = Payment.objects.get(pk=payment_id)
                payment.payment_type = payment_type_instance
                payment.amount = amount
                payment.notes = payment_notes
                payment.payment_date = payment_date
                payment.payment_status = payment_status
                payment.save()
        else:
            payment = Payment(
                payment_type=payment_type_instance,
                amount=amount,
                booking=self,
                notes=payment_notes,
                payment_date=payment_date
            )
            payment.save(number_of_months=number_of_months)

            if payment_type_instance.name == "Damage Deposit":
                damage_deposit_return_type = PaymenType.objects.get(
                    name="Damage Deposit", type="Out")
                if damage_deposit_return_type:
                    deposit_payment = Payment.objects.create(
                        payment_type=damage_deposit_return_type, amount=amount, booking=self, notes="Damage Deposit Return",
                    payment_date=self.end_date)
                else:
                    error_message = "Damage Deposit Return type not found"
                    raise Exception(error_message)
                    

                notification = Notification(
                    date=payment_date,
                    message="Payment",
                    payment=deposit_payment,
                )
                notification.save()

    @property
    def assigned_cleaner(self):
        cleaning = self.cleanings.first()
        return cleaning.cleaner if cleaning else None

    @property
    def payment_str(self):
        payments = self.payments.all()
        payment_str = ""
        for payment in payments:
            formatted_date = payment.payment_date.strftime("%B %d %Y")
            payment_str += f"{payment.payment_type}: ${payment.amount}, {formatted_date} \n"
        return payment_str
    

    def payment_revenue(self, start_date, end_date):
        if start_date and end_date:
            payments = self.payments.filter(payment_date__gte=start_date, payment_date__lte=end_date)
        else:
            payments = self.payments.all()
        revenue = 0
        for payment in payments:
            if payment.payment_type.type == "In":
                revenue += payment.amount
            else:
                revenue -= payment.amount
        return revenue
    
    @property
    def payment_str_for_contract(self):
        payments = self.payments.filter(payment_type__name__in=["Hold Deposit", "Rent", "Damage Deposit"], payment_type__type="In")
        payment_str = ""
        for payment in payments:
            formatted_date = payment.payment_date.strftime("%B %d %Y")
            payment_str += f"{payment.payment_type.name}: ${payment.amount}, {formatted_date} {payment.tenant_notes.strip() if payment.tenant_notes else ''} \n"
        return payment_str

    @property
    def links(self):
        links_list = []

        if self.tenant:
            links_list.append({"name": f"Tenant: {self.tenant.full_name}",
                              "link": f"/users/?q=id={self.tenant.id}"})
        if self.contract_url:
            links_list.append({"name": f"Contract: Open Contract {self.contract_id} ({self.contract_send_status})",
                              "link": f"{self.contract_url}"})

        if self.apartment:

            manager = self.apartment.manager
            owner = self.apartment.owner

            links_list.append({"name": f"Apartment: {self.apartment.name}",
                              "link": f"/apartments/?q=id={self.apartment.id}"})

            if manager:
                links_list.append(
                    {"name": f"Manager: {manager.full_name}", "link": f"/users/?q=id={manager.id}"})

            if owner:
                links_list.append(
                    {"name": f"Owner: {owner.full_name}", "link": f"/users/?q=id={owner.id}"})
        payments = self.payments.all()
        for payment in payments:
            formatted_date = payment.payment_date.strftime("%B %d %Y")
            links_list.append({
                "name":
                f"Payment: {payment.payment_type} - {payment.amount}$ on {formatted_date} [{payment.payment_status}]",
                "link": f"/payments/?q=id={payment.id}"})

        cleanings = self.cleanings.all()
        for cleaning in cleanings:
            cleaner = cleaning.cleaner
            if cleaner:
                links_list.append(
                    {"name": f"Cleaning: {cleaning.date.strftime('%B %d %Y')} [{cleaning.status}]",
                     "link": f"/cleanings/?q=id={cleaning.id}"})
                links_list.append(
                    {"name": f"Cleaner: {cleaner.full_name}", "link": f"/users/?q=id={cleaner.id}"})

        return links_list


class PaymentMethod(models.Model):
    def __str__(self):
        return self.name

    TYPE = [
        ('Payment Method', 'Payment Method'),
        ('Bank', 'Bank'),
    ]
    name = models.CharField(max_length=50, unique=True)
    type = models.CharField(max_length=32, db_index=True, choices=TYPE)
    keywords = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def links(self):
        return []


class PaymenType(models.Model):
    def __str__(self):
        return f"{'+' if self.type == 'In' else '-'} {self.name} ({'Op' if self.category == 'Operating' else 'NOp'})"

    TYPE = [
        ('In', 'In'),
        ('Out', 'Out'),
    ]
    CATEGORY = [
        ('Operating', 'Operating'),
        ('None Operating', 'None Operating'),
    ]
    BALANCE_SHEET_NAME = [
        ('Receivables', 'Receivables'),
        ('Paybels', 'Paybels'),
    ]
    name = models.CharField(max_length=50)
    category = models.CharField(max_length=50, db_index=True, null=True, blank=True, choices=CATEGORY)
    balance_sheet_name = models.CharField(max_length=50, db_index=True, null=True, blank=True, choices=BALANCE_SHEET_NAME)
    type = models.CharField(max_length=32, db_index=True, choices=TYPE)
    keywords = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def links(self):
        return []
    @property
    def full_name(self):
        category_short = "Op" if self.category == "Operating" else "NOp"
        return f"{self.name} ({category_short})"

    @property
    def full_name2(self):
        category_short = "Op" if self.category == "Operating" else "NOp"
        return f"{self.name} ({category_short})-{self.type}"


class Payment(models.Model):
    PAYMENT_STATUS = [
        ('Pending', 'Pending'),
        ('Completed', 'Completed'),
        ('Merged', 'Merged'),
    ]
    invoice_url = models.TextField(blank=True, null=True)
    payment_date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payment_type = models.ForeignKey(PaymenType, on_delete=models.CASCADE,
                                     db_index=True, related_name='payment_type_payments', null=False, blank=False, default=2)
    payment_status = models.CharField(
        max_length=32, db_index=True, choices=PAYMENT_STATUS, default='Pending')

    payment_method = models.ForeignKey(
        PaymentMethod, blank=True, on_delete=models.SET_NULL, db_index=True, related_name='payment_methods_payments',
        limit_choices_to={'type': 'Payment Method'},
        null=True)
    bank = models.ForeignKey(PaymentMethod, blank=True, on_delete=models.SET_NULL, db_index=True,
                             related_name='bank_payments', limit_choices_to={'type': 'Bank'}, null=True)
    notes = models.TextField(blank=True, null=True)
    tenant_notes = models.TextField(blank=True, null=True)
    keywords = models.TextField(blank=True, null=True)
    merged_payment_key = models.TextField(blank=True, null=True)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, db_index=True,
                                related_name='payments', null=True, blank=True)
    apartment = models.ForeignKey(Apartment, on_delete=models.CASCADE, db_index=True,
                                  related_name='payments', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        number_of_months = kwargs.pop('number_of_months', 0)

        # Check if it's an update
        if self.pk is not None:
            # Get the current Payment object from the database
            orig = Payment.objects.get(pk=self.pk)
            self.booking = orig.booking

            # If payment_date has changed
            if orig.payment_date != self.payment_date:
                # print(self.payment_date, "self.payment_date")
                # if isinstance(self.payment_date, tuple) and len(self.payment_date) > 0:
                #     payment_date_obj = self.payment_date[0]
                # else:
                #     payment_date_obj = self.payment_date

                # Ensure payment_date_obj is a date object or a string
                if isinstance(self.payment_date, date):
                    payment_date_str = self.payment_date.isoformat()
                elif isinstance(self.payment_date, str):
                    payment_date_str = self.payment_date
                else:
                    raise ValueError("Unsupported date format for self.payment_date")


                Notification.objects.filter(
                    payment=self).update(date=payment_date_str)

        if number_of_months and number_of_months > 0:
            self.create_payments(number_of_months)
        else:
            super().save(*args, **kwargs)

    def create_payments(self, number_of_months):
        for i in range(number_of_months):
            payment_date = convert_date_format(
                self.payment_date) + relativedelta(months=i)

            payment = Payment.objects.create(
                payment_type=self.payment_type,
                amount=self.amount,
                booking=self.booking,
                apartment=self.apartment,
                payment_status=self.payment_status,
                notes=self.notes,
                bank=self.bank,
                payment_method=self.payment_method,
                payment_date=payment_date
            )

            notification = Notification(
                date=payment_date,
                message="Payment",
                payment=payment
            )
            notification.save()

    def delete(self, *args, **kwargs):
        if self.payment_status != "Completed" and self.payment_status != "Merged":
            super(Payment, self).delete(*args, **kwargs)
    
    @property
    def apartmentName(self):
        if self.apartment:
            return self.apartment.name
        elif self.booking:
            return self.booking.apartment.name
        return ""

    @property
    def links(self):
        links_list = []

        # Link to the associated booking
        if self.booking:
            links_list.append({
                "name":
                f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date.strftime('%B %d %Y')} to {self.booking.end_date.strftime('%B %d %Y')}",
                "link": f"/bookings/?q=id={self.booking.id}"})
            links_list.append({
                "name":
                f"Tenant: {self.booking.tenant.full_name} {self.booking.tenant.phone} ",
                "link": f"/bookings/?q=id={self.booking.id}"})
        if self.apartment:
            links_list.append({"name": f"Apartment: {self.apartment.name}",
                              "link": f"/apartments/?q=id={self.apartment.id}"})
        if self.booking:
            if self.invoice_url:
                links_list.append({"name": f"Payment Invoice: Open Google Doc",
                                   "link": self.invoice_url})
            else:
                links_list.append({"name": f"Payment Invoice: Generate",
                                   "link": f"/generate-invoice/?id={self.id}"})

        return links_list


# Cleanings Model
class Cleaning(models.Model):
    def __str__(self):
        return str(self.date)

    STATUS = [
        ('Scheduled', 'Scheduled'),
        ('Completed', 'Completed'),
    ]

    date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=32, db_index=True, choices=STATUS, default='Scheduled')
    tasks = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    cleaner = models.ForeignKey(User, on_delete=models.SET_NULL, db_index=True,
                                related_name='cleanings', null=True, limit_choices_to={'role': 'Cleaner'})
    booking = models.ForeignKey(Booking, db_index=True, on_delete=models.CASCADE,
                                blank=True, null=True, related_name='cleanings')
    apartment = models.ForeignKey(Apartment, db_index=True, on_delete=models.CASCADE,
                                blank=True, null=True, related_name='cleanings')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Check if it's an update
        if self.pk is not None:
            # Get the current Cleaning object from the database
            orig = Cleaning.objects.get(pk=self.pk)
            self.booking = orig.booking
            
            # Check if orig.booking exists before accessing its apartment
            if orig.booking is not None:
                self.apartment = orig.booking.apartment

            # If date has changed
            if orig.date != self.date:
                # Update the related Notification
                Notification.objects.filter(
                    cleaning=self).update(date=self.date, apartment=self.apartment)
            
            # Send telegram notification about changes
            if (orig.date != self.date) or (orig.status != self.status) or (orig.cleaner != self.cleaner) or (orig.tasks != self.tasks) or (orig.notes != self.notes):
                self.send_telegram_notification(orig)
                
            super().save(*args, **kwargs)
        else:
            # For new cleaning objects
            if not self.apartment and self.booking is not None:
                self.apartment = self.booking.apartment
            super().save(*args, **kwargs)
            notification = Notification(
                date=self.date,
                message="Cleaning",
                cleaning=self,
            )
            notification.save()
            
            # Send telegram notification for new cleanings if they're within 3 days
            from datetime import date, timedelta
            today = date.today()
            days_until_cleaning = (self.date - today).days
            
            if days_until_cleaning <= 3 and days_until_cleaning >= 0 and self.cleaner and self.cleaner.telegram_chat_id:
                self.send_new_cleaning_notification()
       
    def send_telegram_notification(self, orig):
        changes = []
        if orig.date != self.date:
            changes.append(f"Date changed from {orig.date} to {self.date}")
        if orig.status != self.status:
            changes.append(f"Status changed from {orig.status} to {self.status}")
        if orig.cleaner != self.cleaner:
            old_cleaner = orig.cleaner.full_name if orig.cleaner else "No cleaner assigned"
            new_cleaner = self.cleaner.full_name if self.cleaner else "No cleaner assigned"
            changes.append(f"Cleaner {new_cleaner}")
        if orig.notes != self.notes:
            changes.append(f"Notes changed from '{orig.notes or 'None'}' to '{self.notes or 'None'}'")
        if orig.tasks != self.tasks:
            changes.append(f"Tasks changed from '{orig.tasks or 'None'}' to '{self.tasks or 'None'}'")
        
        if changes:
            # Only send notifications if cleaning is less than 5 days from today
            from datetime import date, timedelta
            today = date.today()
            days_until_cleaning = (self.date - today).days
            
            if days_until_cleaning <= 5 and days_until_cleaning >= 0:
                message = "Cleaning Update:\n" + "\n".join(changes)
                
                # Add apartment and booking details
                apartment_name = ""
                if self.booking and self.booking.apartment:
                    apartment_name = self.booking.apartment.name
                elif self.apartment:
                    apartment_name = self.apartment.name
                    
                if apartment_name:
                    message += f"\nApartment: {apartment_name}"
                    
                if self.booking:
                    message += f"\nBooking: {self.booking.start_date} - {self.booking.end_date}"
                    if self.booking.tenant:
                        message += f"\nTenant: {self.booking.tenant.full_name}"
                
                # Add notes if present
                if self.notes:
                    message += f"\nNotes: {self.notes}"
                
                # Add tasks if present
                if self.tasks:
                    message += f"\nTasks: {self.tasks}"
                
                # Send notification to the cleaner if one is assigned
                telegram_token = os.environ.get("TELEGRAM_TOKEN")
                if self.cleaner and self.cleaner.telegram_chat_id and telegram_token:
                    send_telegram_message(self.cleaner.telegram_chat_id.strip(), telegram_token, message)
                    
                # If cleaner was changed, send notification to the previous cleaner as well
                if orig.cleaner and orig.cleaner != self.cleaner and orig.cleaner.telegram_chat_id and telegram_token:
                    # Simple cancellation message instead of reassignment message
                    canceled_message = f"Cleaning canceled for apartment {apartment_name} on {self.date}."
                    send_telegram_message(orig.cleaner.telegram_chat_id.strip(), telegram_token, canceled_message)
       
    @property
    def links(self):
        links_list = []

        # Link to the booking associated with this cleaning
        if self.booking:
            apartment_name = self.booking.apartment.name if self.booking.apartment else self.apartment.name
            links_list.append({
                "name":
                f"Booking: Apartment {apartment_name} from {self.booking.start_date.strftime('%B %d %Y')} to {self.booking.end_date.strftime('%B %d %Y')}",
                "link": f"/bookings/?q=id={self.booking.id}"})

        return links_list

    def send_new_cleaning_notification(self):
        # Create message for new cleaning
        message = "🆕 New Cleaning Assigned:\n"
        
        # Add apartment details
        apartment_name = ""
        if self.booking and self.booking.apartment:
            apartment_name = self.booking.apartment.name
        elif self.apartment:
            apartment_name = self.apartment.name
            
        if apartment_name:
            message += f"Apartment: {apartment_name}\n"
            
        # Add booking and tenant details
        if self.booking:
            message += f"Booking period: {self.booking.start_date} - {self.booking.end_date}\n"
            if self.booking.tenant:
                message += f"Tenant: {self.booking.tenant.full_name}\n"
        
        # Add cleaning details
        message += f"Cleaning date: {self.date}\n"
        message += f"Status: {self.status}\n"
        
        # Add tasks if present
        if self.tasks:
            message += f"Tasks: {self.tasks}\n"
        
        # Add notes if present
        if self.notes:
            message += f"Notes: {self.notes}\n"
        
        # Send notification to the cleaner
        telegram_token = os.environ.get("TELEGRAM_TOKEN")
        if self.cleaner and self.cleaner.telegram_chat_id and telegram_token:
            send_telegram_message(self.cleaner.telegram_chat_id.strip(), telegram_token, message)


def format_date(date):
    if date:
        return date.strftime("%B %d %Y")
    return ""


class Notification(models.Model):
    def __str__(self):
        return self.message

    date = models.DateField(db_index=True)
    send_in_telegram = models.BooleanField(db_index=True, default=True)
    message = models.TextField(blank=True, null=True)
    booking = models.ForeignKey(Booking, db_index=True, blank=True, on_delete=models.CASCADE,
                                null=True, related_name='booking_notifications')
    payment = models.ForeignKey(Payment, db_index=True, blank=True, on_delete=models.CASCADE,
                                null=True, related_name='payment_notifications')
    cleaning = models.ForeignKey(Cleaning, db_index=True, blank=True, on_delete=models.CASCADE,
                                 null=True, related_name='cleaning_notifications')
    apartment = models.ForeignKey(Apartment, db_index=True, blank=True, on_delete=models.CASCADE,
                                 null=True, related_name='apartment_notifications')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def notification_message(self):
        if self.booking:
            start_date = format_date(self.booking.start_date)
            end_date = format_date(self.booking.end_date)
            return f"{self.message or 'Booking'}: {start_date} - {end_date}, {self.booking.apartment.name}, {self.booking.tenant.full_name}."
        elif self.payment:
            payment_date = format_date(self.payment.payment_date)
            apartment_name = self.payment.apartment.name if self.payment.apartment else (
                self.payment.booking.apartment.name if self.payment.booking and self.payment.booking.apartment else '')
            tenant_name = " (" + self.payment.booking.tenant.full_name + \
                ") " if self.payment.booking else ''
            return f"{self.message or 'Payment'}: {self.payment.payment_type} {self.payment.amount}$ {payment_date} {apartment_name} {self.payment.notes or ''}{tenant_name}[{self.payment.payment_status}]"
        elif self.cleaning:
            cleaning_date = format_date(self.cleaning.date)
            cleaner_name = self.cleaning.cleaner.full_name if self.cleaning.cleaner else "No cleaner assigned"
            apartment_name = ""
            if self.cleaning.booking and self.cleaning.booking.apartment:
                apartment_name = self.cleaning.booking.apartment.name
            elif self.cleaning.apartment:
                apartment_name = self.cleaning.apartment.name
            return f"{self.message or 'Cleaning'}: {cleaning_date} by {cleaner_name} {apartment_name} [{self.cleaning.status}]"
        else:
            return self.message
    
    def save(self, *args, **kwargs):
        if self.apartment:
            self.apartment = self.apartment
        elif self.booking:
            self.apartment = self.booking.apartment
        elif self.payment:
            self.apartment = self.payment.apartment
        elif self.cleaning:
            if self.cleaning.booking:
                self.apartment = self.cleaning.booking.apartment
            elif self.cleaning.apartment:
                self.apartment = self.cleaning.apartment
            
        super().save(*args, **kwargs)

    @property
    def links(self):
        links_list = []

        if self.booking:
            links_list.append({
                "name":
                f"Booking: Apartment {self.booking.apartment.name} from {self.booking.start_date} to {self.booking.end_date}",
                "link": f"/bookings/?q=id={self.booking.id}"})
        if self.payment:
            links_list.append({
                "name":
                f"Payment: {self.payment.payment_type} - {self.payment.amount}$ on {self.payment.payment_date} [{self.payment.payment_status}]",
                "link": f"/payments/?q=id={self.payment.id}"})
        if self.cleaning:
            cleaning_info = f"Cleaning: {self.cleaning.date} [{self.cleaning.status}]"
            links_list.append(
                {"name": cleaning_info,
                 "link": f"/cleanings/?q=id={self.cleaning.id}"})

        return links_list


class Chat(models.Model):
    SENDER_TYPE = [
        ('USER', 'USER'),
        ('MANAGER1', 'MANAGER1'),
        ('MANAGER2', 'MANAGER2'),
        ('GPT BOT', 'GPT BOT'),
    ]
    MESSAGE_TYPE = [
        ('NO_NEED_ACTION', 'NO_NEED_ACTION'),
        ('NEED_ACTION', 'NEED_ACTION'),
        ('DB', 'DB'),
        ('KNOWLEDGE_BASE', 'KNOWLEDGE_BASE'),
        ('NOTIFICATION', 'NOTIFICATION'),
        ('CONTRACT', 'CONTRACT'),
    ]
    MESSAGE_STATUS = [
        ('ERROR', 'ERROR'),
        ('SENDED', 'SENDED'),
    ]
    twilio_conversation_sid = models.CharField(max_length=100, blank=True, null=True)
    twilio_message_sid = models.CharField(max_length=100, blank=True, null=True)
    sender_phone = models.CharField(max_length=20, blank=True, null=True)
    receiver_phone = models.CharField(max_length=20, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, db_index=True,
                                related_name='chat', null=True, blank=True)
    message = models.TextField()
    sender_type = models.CharField(
        max_length=32, db_index=True, choices=SENDER_TYPE, null=True, blank=True)
    context = models.TextField(null=True, blank=True)
    message_type = models.CharField(
        max_length=32, db_index=True, choices=MESSAGE_TYPE, default='NO_NEED_ACTION', null=True, blank=True)
    message_status = models.CharField(
        max_length=32, db_index=True, choices=MESSAGE_STATUS, default='SENDED')


# Models
class HandymanCalendar(models.Model):
    tenant_name = models.CharField(max_length=255)
    tenant_phone = models.CharField(max_length=20)
    apartment_name = models.CharField(max_length=255)
    # apartment = models.ForeignKey(Apartment, on_delete=models.SET_NULL, db_index=True,
    #                             related_name='handmade_calendar', null=True, blank=True)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    notes = models.TextField()
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        print(self.start_time, "self.start_time")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.apartment_name} - {self.date} {self.start_time} - {self.end_time}"

class HandymanBlockedSlot(models.Model):
    date = models.DateField(db_index=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_full_day = models.BooleanField(default=False)
    reason = models.TextField(blank=True, null=True)
    created_by = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('date', 'start_time', 'end_time')
        
    def __str__(self):
        if self.is_full_day:
            return f"Blocked day: {self.date}"
        else:
            return f"Blocked slot: {self.date} {self.start_time} - {self.end_time}"

class Parking(models.Model):
    number = models.CharField(max_length=20, blank=True, null=False)
    notes = models.CharField(max_length=1000, blank=True, null=True)
    associated_room = models.CharField(max_length=255, blank=True, null=True)
    building = models.CharField(max_length=255, blank=True, null=True)
    def __str__(self):
        return f"Building: {self.building}-{self.associated_room}. P#{self.number}"
    
    def save(self, *args, **kwargs):
        print(self.associated_room, "self.associated_room", self.notes, "self.notes")
        super().save(*args, **kwargs)

class ParkingBooking(models.Model):
    STATUS = [
        ('Unavailable', 'Unavailable'),
        ('Booked', 'Booked'),
        ('No Car', 'No Car'),
    ]
    parking = models.ForeignKey(Parking, on_delete=models.SET_NULL, db_index=True,
                                related_name='parking_bookings', null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=255, blank=True, null=True, choices=STATUS)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    
    # Add relationships
    apartment = models.ForeignKey(
        Apartment, 
        on_delete=models.SET_NULL,
        related_name='parking_bookings',
        null=True
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.SET_NULL,
        related_name='parking_bookings',
        null=True,
        blank=True
    )

    def __str__(self):
        parking_str = f" {self.parking.number}" if self.parking else "No parking"
        apartment_name = f"{self.apartment.name}" if self.apartment else ""
        status_str = f"({self.status})"
        return f"{apartment_name} {parking_str}{status_str}"

    def save(self, *args, **kwargs):

        # If booking is assigned, use booking dates
        if self.booking:
            self.start_date = self.start_date or self.booking.start_date
            self.end_date = self.end_date or self.booking.end_date
            self.apartment = self.apartment or self.booking.apartment
        if not (self.start_date and self.end_date):
            raise ValueError("Start date and end date are required")

        super().save(*args, **kwargs)


    @property
    def links(self):
        links_list = []
        if self.apartment:
            links_list.append({
                "name": f"Apartment: {self.apartment.name}",
                "link": f"/apartments/?q=id={self.apartment.id}"
            })
        if self.booking:
            links_list.append({
                "name": f"Booking: {self.booking.tenant.full_name} ({self.booking.start_date} - {self.booking.end_date})",
                "link": f"/bookings/?q=id={self.booking.id}"
            })
        return links_list

def send_telegram_message(chat_id, token, message):
    if chat_id and token:
        base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
        requests.get(base_url)