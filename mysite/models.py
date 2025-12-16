# mysite/models.py

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.contrib.auth.hashers import make_password
from dateutil.relativedelta import relativedelta
from datetime import datetime
from datetime import datetime, date
from mysite.docuseal_contract_managment import create_contract, update_contract, delete_contract
from mysite.unified_logger import log_error, log_info, log_warning, logger
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



def _is_valid_e164(phone_with_plus):
    """
    Internal function to validate E.164 format.
    
    E.164 format requirements:
    - Must start with '+'
    - Must have 7-15 digits total
    - Country code must start with 1-9 (no leading 0)
    - US numbers (+1) must have exactly 11 digits
    - Cannot be all zeros
    
    Returns:
        str: Cleaned phone in E.164 format if valid
        None: If invalid
    """
    if not phone_with_plus or not phone_with_plus.startswith('+'):
        return None
    
    # Remove all non-digits
    cleaned = re.sub(r'\D', '', phone_with_plus)
    
    # Check length (7-15 digits as per E.164 standard)
    if not (7 <= len(cleaned) <= 15):
        return None
    
    # Verify country code is valid (must start with 1-9, no leading 0)
    if cleaned[0] not in '123456789':
        return None
    
    # Check if all zeros (invalid phone)
    if cleaned.strip('0') == '':
        return None
    
    # US country code (+1) specific validation
    # Numbers starting with +1 are ONLY valid if they have exactly 11 digits (US/Canada)
    # Other country codes like +123, +1264, +1268, etc. don't actually exist in E.164
    # All country codes starting with 1 are part of the North American Numbering Plan
    if cleaned.startswith('1'):
        if len(cleaned) != 11:
            # US/Canada numbers must be exactly 11 digits
            return None
        # Check if the actual phone number (after country code) is all zeros
        phone_number_part = cleaned[1:]  # Remove the '1' country code
        if phone_number_part.strip('0') == '':
            # All zeros after +1 is invalid
            return None
    
    # Valid E.164 format
    return f"+{cleaned}"


def validate_and_format_phone(phone):
    """
    Comprehensive phone validation and formatting.
    ALL returned phones are validated against E.164 standard.
    
    Returns:
        str: Validated phone in E.164 format
        None: If phone is invalid or empty
    
    Supported formats:
    - E.164: +[country_code][number] (e.g., +12025551234, +447738195342, +919876543210)
    - US without +1: 2025551234 (will add +1)
    - US with 1: 12025551234 (will add +)
    - US with leading 0: 0201234567 (strips 0, adds +1)
    """
    if not phone:
        return None
    
    # Convert to string and strip whitespace
    phone = str(phone).strip()
    
    if not phone:
        return None
    
    # Step 1: Format the phone number to E.164 candidate
    formatted_phone = None
    
    # If already starts with +, clean it
    if phone.startswith('+'):
        digits_only = re.sub(r'\D', '', phone)
        formatted_phone = f"+{digits_only}"
    else:
        # Remove all non-digit characters
        digits_only = re.sub(r'\D', '', phone)
        
        # If no digits found, invalid
        if not digits_only:
            return None
        
        # Special case: Numbers starting with 0 (assume US, strip the leading 0)
        # Common in some countries where local dialing uses 0 prefix
        stripped_leading_zero = False
        if digits_only.startswith('0'):
            # Strip ALL leading zeros, not just one
            original_length = len(digits_only)
            digits_only = digits_only.lstrip('0')
            
            # If all zeros, invalid
            if not digits_only:
                return None
            
            stripped_leading_zero = True
            stripped_count = original_length - len(digits_only)
            
            # After stripping 0(s), we need at least 10 digits for valid US phone
            if len(digits_only) < 10:
                return None
        
        # Now format based on length and pattern
        if stripped_leading_zero:
            # If we stripped a leading 0, check what we have left
            if len(digits_only) == 11 and digits_only.startswith('1'):
                # After stripping, we have 11 digits starting with 1 (complete US number)
                formatted_phone = f"+{digits_only}"
            else:
                # After stripping, treat as US number and add +1
                formatted_phone = f"+1{digits_only}"
        elif len(digits_only) == 10:
            # 10 digits: assume US number
            formatted_phone = f"+1{digits_only}"
        elif len(digits_only) == 11 and digits_only.startswith('1'):
            # 11 digits starting with 1: assume US number
            formatted_phone = f"+{digits_only}"
        elif 7 <= len(digits_only) <= 15:
            # Other lengths: assume international
            formatted_phone = f"+{digits_only}"
        else:
            return None
    
    # Step 2: Validate the formatted phone against E.164 standard
    validated_phone = _is_valid_e164(formatted_phone)
    
    if validated_phone:
        return validated_phone
    else:
        return None




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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['full_name']

    objects = CustomUserManager()

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        
        # Apply user tracking first
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        # Check if the password is not hashed
        if self.password and not self.password.startswith(('pbkdf2_sha256$', 'bcrypt')):
            self.password = make_password(self.password)
        
        # Validate and format phone number - SINGLE SOURCE OF TRUTH
        if self.phone and self.phone.strip():
            validated_phone = validate_and_format_phone(self.phone)
            if validated_phone:
                self.phone = validated_phone
            else:
                # Log the invalid phone and raise error
                log_warning(f"Invalid phone number for user {self.email}: '{self.phone}'", category='auth')
                raise ValueError(f"Invalid phone number for user {self.email}: '{self.phone}'")
        else:
            self.phone = None
        
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
    raiting = models.DecimalField(blank=True, null=True, default=0, max_digits=2, decimal_places=1)
    default_price = models.DecimalField(blank=True, null=True, default=0, max_digits=10, decimal_places=2)

    manager = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, db_index=True,
                                related_name='managed_apartments', null=True, limit_choices_to={'role': 'Manager'})
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, blank=True, db_index=True,
                              related_name='owned_apartments', null=True, limit_choices_to={'role': 'Owner'})

    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """
        Prevent deletion of apartment if it has any related objects.
        """
        from django.db.models import ProtectedError
        
        related_objects = []
        
        # Check for bookings
        bookings_count = self.booked_apartments.count()
        if bookings_count > 0:
            related_objects.append(f"{bookings_count} booking(s)")
        
        # Check for payments
        payments_count = self.payments.count()
        if payments_count > 0:
            related_objects.append(f"{payments_count} payment(s)")
        
        # Check for cleanings
        cleanings_count = self.cleanings.count()
        if cleanings_count > 0:
            related_objects.append(f"{cleanings_count} cleaning(s)")
        
        # Check for notifications
        notifications_count = self.apartment_notifications.count()
        if notifications_count > 0:
            related_objects.append(f"{notifications_count} notification(s)")
        
        # Check for prices
        prices_count = self.prices.count()
        if prices_count > 0:
            related_objects.append(f"{prices_count} price record(s)")
        
        if related_objects:
            raise ProtectedError(
                f"Cannot delete apartment '{self.name}' because it has related data: {', '.join(related_objects)}. "
                f"Please remove or reassign these items first.",
                set()
            )
        
        super().delete(*args, **kwargs)

    @property
    def address(self):
        return f" {self.building_n} {self.street}, {self.apartment_n}, {self.state}, {self.city}, {self.zip_index}"

    def get_rating_surcharge_per_day(self):
        """Calculate additional daily charge based on apartment rating"""
        if not self.raiting or self.raiting <= 5:
            return 0
        
        rating = float(self.raiting)
        if rating >= 6 and rating <= 10:
            # Formula: (rating - 5) * 3
            return (rating - 5) * 3
        
        return 0

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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-effective_date']
        unique_together = ['apartment', 'effective_date']
    
    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)

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
        max_length=100, default="", blank=True, verbose_name="Car Model")
    car_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Car Price")
    car_rent_days = models.IntegerField(
        default=0, verbose_name="Car Rent Days")

    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def saveEmpty(self, *args, **kwargs):
        form_data = kwargs.pop('form_data', None)
        self.get_or_create_tenant(form_data)
        super().save(*args, **kwargs)

    def update(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        
        # Extract custom kwargs
        parking_number = kwargs.pop('parking_number', None)
        form_data = kwargs.pop('form_data', None)
        payments_data = kwargs.pop('payments_data', None)
        updated_by = kwargs.pop('updated_by', None)
        
        # Apply user tracking
        apply_user_tracking(self, updated_by)
        
        is_creating = self.pk is None
        
        # Handle tenant creation/update if form_data is provided
        if form_data:
            self.get_or_create_tenant(form_data)
        
        # === UPDATING EXISTING BOOKING ===
        if not is_creating:
            self._handle_booking_update(form_data, payments_data)
        
        # === CREATING NEW BOOKING ===
        else:
            self._handle_booking_creation(form_data, payments_data)
        
        # Handle parking booking (both create and update)
        if parking_number:
            self._create_parking_booking(parking_number)
    
    def _handle_booking_update(self, form_data, payments_data):
        """Handle updates to existing bookings"""
        orig = Booking.objects.get(pk=self.pk)
        
        # Update notifications if dates changed
        if orig.start_date != self.start_date:
            Notification.objects.filter(
                booking=self,
                message="Start Booking"
            ).update(date=self.start_date)
        
        if orig.end_date != self.end_date:
            self._handle_end_date_change(orig)
        
        # Update parking booking dates if dates changed
        if orig.start_date != self.start_date or orig.end_date != self.end_date:
            ParkingBooking.objects.filter(booking=self).update(
                start_date=self.start_date,
                end_date=self.end_date
            )
        
        # Save the booking
        super().save()
        
        # Create or update payments
        if payments_data:
            self.create_payments(payments_data)
        
        # Update contract if exists
        if self.contract_id:
            update_contract(self)
        
        # Handle contract sending and messaging
        self._handle_contract_and_messaging(form_data)
        
        # Update conversation links
        if self.tenant and self.tenant.phone:
            self.update_conversation_links()
    
    def _handle_booking_creation(self, form_data, payments_data):
        """Handle creation of new bookings"""
        # Save the booking first to get an ID
        super().save()
        
        # Schedule cleaning if form_data provided
        if form_data and not self.cleanings.exists():
            self.schedule_cleaning(form_data)
        
        # Auto-create booking notifications (Start and End)
        self._create_booking_notifications()
        
        # Create payments if provided
        if payments_data:
            self.create_payments(payments_data)
        
        # Handle contract sending and messaging
        self._handle_contract_and_messaging(form_data)
        
        # Update conversation links
        if self.tenant and self.tenant.phone:
            self.update_conversation_links()
        
        # Update apartment keywords
        if self.apartment and self.tenant and self.tenant.full_name:
            if not self.apartment.keywords:
                self.apartment.keywords = self.tenant.full_name
            else:
                self.apartment.keywords += f", {self.tenant.full_name}"
            self.apartment.save()
    
    def _handle_end_date_change(self, orig):
        """Handle all updates needed when end_date changes"""
        # Delete payments beyond new end date (except damage deposit)
        if self.end_date < orig.end_date:
            self.deletePayments()
        
        # Update damage deposit return date
        Payment.objects.filter(
            booking=self,
            payment_type__name="Damage Deposit",
            payment_type__type="Out"
        ).update(payment_date=self.end_date)
        
        # Update cleaning date
        Cleaning.objects.filter(booking=self).update(date=self.end_date)
        
        # Update all related notifications
        Notification.objects.filter(
            payment__booking=self,
            payment__payment_type__name="Damage Deposit",
            payment__payment_type__type="Out"
        ).update(date=self.end_date)
        
        Notification.objects.filter(
            cleaning__booking=self
        ).update(date=self.end_date)
        
        Notification.objects.filter(
            booking=self,
            message="End Booking"
        ).update(date=self.end_date)
    
    def _create_booking_notifications(self):
        """Auto-create Start and End Booking notifications (excluding Blocked bookings)"""
        # Skip notification creation for Blocked bookings
        if self.status == 'Blocked':
            return
        
        # Check and create Start Booking notification
        start_notification_exists = Notification.objects.filter(
            booking=self,
            message="Start Booking"
        ).exists()
        
        if not start_notification_exists:
            notification = Notification(
                date=self.start_date,
                message="Start Booking",
                booking=self,
                send_in_telegram=True
            )
            notification.save()
        
        # Check and create End Booking notification
        end_notification_exists = Notification.objects.filter(
            booking=self,
            message="End Booking"
        ).exists()
        
        if not end_notification_exists:
            notification = Notification(
                date=self.end_date,
                message="End Booking",
                booking=self,
                send_in_telegram=True
            )
            notification.save()
    
    def _handle_contract_and_messaging(self, form_data):
        """Handle contract creation and welcome message sending"""
        if not form_data:
            return
        
        raw_send_contract = form_data.get("send_contract")
        raw_create_chat = form_data.get("create_chat")
        
        # Normalize template_id
        template_id = self._normalize_template_id(raw_send_contract)
        
        # Normalize create_chat to boolean
        create_chat_bool = self._normalize_boolean(raw_create_chat)
        
        # Send contract or welcome message
        if template_id:
            create_contract(self, template_id=template_id, send_sms=create_chat_bool)
        elif create_chat_bool:
            from mysite.views.messaging import sendWelcomeMessageToTwilio
            sendWelcomeMessageToTwilio(self)
    
    def _normalize_template_id(self, raw_value):
        """Normalize template ID to expected string values or None"""
        if raw_value in (None, "", 0, "0", "None"):
            return None
        
        try:
            candidate = str(int(str(raw_value).strip()))
            return candidate if candidate in {"118378", "120946"} else None
        except (ValueError, AttributeError):
            return None
    
    def _normalize_boolean(self, raw_value):
        """Normalize various boolean representations to actual boolean"""
        if isinstance(raw_value, bool):
            return raw_value
        elif isinstance(raw_value, (int, float)):
            return int(raw_value) == 1
        elif isinstance(raw_value, str):
            return raw_value.strip().lower() in {"true", "1", "yes", "on"}
        return False
    
    def _create_parking_booking(self, parking_number):
        """Create parking booking associated with this booking"""
        parking = Parking.objects.get(id=parking_number)
        status = "Booked" if (self.is_rent_car or self.car_model or 
                              self.car_price or self.car_rent_days) else "No Car"
        
        parking_booking = ParkingBooking(
            parking=parking,
            booking=self,
            status=status,
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

                if not user:
                    # If the user doesn't exist, create a new instance
                    user = User(
                        email=tenant_email,
                        full_name=tenant_full_name,
                        phone=tenant_phone,
                        role='Tenant',
                        password=User.objects.make_random_password()
                    )
                else:
                    # If the user exists, update the fields
                    user.full_name = tenant_full_name
                    user.phone = tenant_phone

                # Save the user (both new and existing go through same path)
                # This triggers phone validation in User.save()
                try:
                    user.save()
                except ValueError as e:
                    # Phone validation failed - re-raise with user-friendly message
                    from django.core.exceptions import ValidationError
                    raise ValidationError(
                        f"Invalid phone number: '{tenant_phone}'. "
                        f"Please use format +1XXXXXXXXXX for US numbers or +[country code][number] for international numbers."
                    )

                # Assign the user to the booking's tenant field
                self.tenant = user

    def create_payments(self, payments_data):
        if payments_data:
            payment_dates = payments_data.get('payment_dates', [])
            amounts = payments_data.get('amounts', [])
            payment_types = payments_data.get('payment_types', [])
            payment_notes = payments_data.get('payment_notes', [])
            number_of_months = payments_data.get('number_of_months', [])
            payment_ids = payments_data.get('payment_id', [])
            payment_statuses = payments_data.get('payment_status', [])
            
            # Convert the dates to the expected format
            payment_dates = [convert_date_format(
                date) for date in payment_dates]

            for date, amount, p_type, p_notes, n_months, payment_id, payment_status in zip_longest(
                payment_dates, amounts, payment_types, payment_notes, number_of_months, payment_ids, payment_statuses, fillvalue=None
            ):
                self.create_payment(p_type, amount, date, p_notes, n_months, payment_id, payment_status)


        notification = Notification(
            date=self.start_date,
            message="Start Booking",
            booking=self
        )
        notification.save()

    def schedule_cleaning(self, form_data):
        # Schedule a cleaning for the day after the booking ends
        cleaning_date = self.end_date
        assigned_cleaner = form_data.get('assigned_cleaner') if form_data else None
        
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
                    deposit_payment = Payment(
                        payment_type=damage_deposit_return_type, amount=amount, booking=self, notes="Damage Deposit Return",
                    payment_date=self.end_date)
                    deposit_payment.save()
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
        payments = self.payments.all().order_by('payment_date')
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
    
    def update_conversation_links(self):
        """
        Update existing Twilio conversations to link to this booking if more contextually relevant
        This handles both unlinked conversations and conversations linked to older bookings
        """
        try:
            if not self.tenant or not self.tenant.phone:
                return
                
            # Find ALL conversations that involve this tenant (linked and unlinked)
            all_tenant_conversations = TwilioConversation.objects.filter(
                messages__author=self.tenant.phone
            ).distinct()
            
            updated_count = 0
            relinked_count = 0
            
            for conversation in all_tenant_conversations:
                
                current_booking = conversation.booking
                should_link_to_new = self._should_link_conversation_to_booking(conversation)
                
                if not current_booking:
                    # Case 1: Unlinked conversation
                    if should_link_to_new:
                        conversation.booking = self
                        conversation.apartment = self.apartment
                        conversation.save()
                        updated_count += 1
                
                elif current_booking != self:
                    # Case 2: Conversation linked to different booking
                    if should_link_to_new:
                        # Check if this new booking is more contextually relevant than current
                        should_keep_current = current_booking._should_link_conversation_to_booking(conversation)
                        
                        if should_link_to_new and not should_keep_current:
                            # New booking is more relevant, relink
                            old_booking = current_booking
                            conversation.booking = self
                            conversation.apartment = self.apartment
                            conversation.save()
                            relinked_count += 1
                        
                        elif should_link_to_new and should_keep_current:
                            # Both bookings are contextually relevant - check timing to decide
                            if self._is_more_recent_booking_than(current_booking):
                                old_booking = current_booking
                                conversation.booking = self
                                conversation.apartment = self.apartment
                                conversation.save()
                                relinked_count += 1
                
                # Case 3: Already linked to this booking - no action needed
            
            if updated_count > 0 or relinked_count > 0:
                log_info(
                    f"Updated conversation links for booking {self.id}", 
                    category='booking',
                    details={'new_links': updated_count, 'relinks': relinked_count}
                )
            
        except Exception as e:
            log_error(e, f"Booking {self.id} - Update Conversation Links", source='model')
    
    def _is_more_recent_booking_than(self, other_booking):
        """
        Check if this booking is more recent than another booking
        """
        if not other_booking:
            return True
            
        # Compare by start date first, then by creation date
        if self.start_date != other_booking.start_date:
            return self.start_date > other_booking.start_date
        
        # If same start date, compare by creation time
        return self.created_at > other_booking.created_at
    
    def _should_link_conversation_to_booking(self, conversation):
        """
        Determine if a conversation should be linked to this booking
        based on timing and context
        """
        try:
            from datetime import timedelta
            
            # Get the earliest message in the conversation
            earliest_message = conversation.messages.order_by('message_timestamp').first()
            if not earliest_message:
                return True  # No messages, safe to link
            
            # If conversation started around the time of this booking, it's likely related
            booking_start_buffer = self.start_date - timedelta(days=30)  # 30 days before booking
            booking_end_buffer = self.end_date + timedelta(days=7)       # 7 days after booking
            
            conversation_start = earliest_message.message_timestamp.date()
            
            # Link if conversation started in the booking timeframe
            if booking_start_buffer <= conversation_start <= booking_end_buffer:
                return True
            
            # If conversation is very recent (last 3 days), link to most recent booking
            from datetime import date
            if (date.today() - conversation_start).days <= 3:
                return True
            
            return False
            
        except Exception as e:
            log_error(e, "Check Conversation Context", source='model')
            return True  # Default to linking if we can't determine context
    
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

    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)

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

    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)

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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255,  blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        from mysite.request_context import apply_user_tracking
        
        try:
            number_of_months = kwargs.pop('number_of_months', 0)
            updated_by = kwargs.pop('updated_by', None)
            is_creating = self.pk is None
            
            # Validate amount is not zero
            if self.amount == 0:
                raise ValidationError(
                    "Payment amount cannot be 0. Please enter a valid payment amount."
                )
            
            # Apply automatic user tracking
            apply_user_tracking(self, updated_by)

            # Validate that booking and apartment are consistent
            if self.booking and self.apartment and self.booking.apartment:
                if self.booking.apartment != self.apartment:
                    raise ValidationError(
                        f"Payment apartment ({self.apartment.name}) does not match booking apartment ({self.booking.apartment.name}). "
                        "Please ensure the apartment matches the booking's apartment."
                    )

            # Check if it's an update
            if self.pk is not None:
                # Get the current Payment object from the database
                orig = Payment.objects.get(pk=self.pk)

                # If payment_date has changed, update related notifications
                if orig.payment_date != self.payment_date:
                    # Ensure payment_date_obj is a date object or a string
                    from datetime import date
                    if isinstance(self.payment_date, date):
                        payment_date_str = self.payment_date.isoformat()
                    elif isinstance(self.payment_date, str):
                        payment_date_str = self.payment_date
                    else:
                        raise ValueError("Unsupported date format for self.payment_date")

                    Notification.objects.filter(
                        payment=self).update(date=payment_date_str)

            # Save the payment first
            if number_of_months and number_of_months > 0:
                self.create_payments(number_of_months)
            else:
                super().save(*args, **kwargs)

            # Auto-create notification for new payments (excluding mortage payments)
            if is_creating:
                should_create_notification = True
                
                # Check if this is a mortage payment (should not have notifications)
                if self.payment_type and (
                    'mortage' in self.payment_type.name.lower()
                ):
                    should_create_notification = False
                
                if should_create_notification:
                    # Check if notification already exists (safety check)
                    existing_notification = Notification.objects.filter(payment=self).first()
                    if not existing_notification:
                        notification = Notification(
                            date=self.payment_date,
                            message='Payment',
                            payment=self,
                            send_in_telegram=True
                        )
                        notification.save()
        except Exception as e:
            log_error(e, f"Payment Save - ID: {self.pk or 'NEW'}", source='model', severity='high')
            raise

        

    def create_payments(self, number_of_months):
        for i in range(number_of_months):
            payment_date = convert_date_format(
                self.payment_date) + relativedelta(months=i)

            payment = Payment(
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
            payment.save()

            

    def delete(self, *args, **kwargs):
        if self.payment_status != "Completed" and self.payment_status != "Merged":
            super(Payment, self).delete(*args, **kwargs)
    
    @property
    def apartmentName(self):
        if self.apartment:
            return self.apartment.name
        elif self.booking and self.booking.apartment:
            return self.booking.apartment.name
        return ""

    @property
    def links(self):
        links_list = []

        # Link to the associated booking
        if self.booking:
            apt_name = self.booking.apartment.name if self.booking.apartment else 'Unknown'
            links_list.append({
                "name":
                f"Booking: Apartment {apt_name} from {self.booking.start_date.strftime('%B %d %Y')} to {self.booking.end_date.strftime('%B %d %Y')}",
                "link": f"/bookings/?q=id={self.booking.id}"})
            tenant_name = self.booking.tenant.full_name if self.booking.tenant else 'Unknown'
            tenant_phone = self.booking.tenant.phone if self.booking.tenant else ''
            links_list.append({
                "name":
                f"Tenant: {tenant_name} {tenant_phone} ",
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

    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        
        # Pop updated_by from kwargs before passing to super().save()
        updated_by = kwargs.pop('updated_by', 'System')
        
        # Apply automatic user tracking
        apply_user_tracking(self, updated_by)
        
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
            
            # Set default tasks if not provided
            if not self.tasks:
                self.tasks = "Tasks: \nTenant living or move out: N/A \nCheckout time: N/A\nCheck-in date/time: N/A\nPets: N/A\nParking: N/A"
            
            super().save(*args, **kwargs)
            notification = Notification(
                date=self.date,
                message="Cleaning",
                cleaning=self,
            )
            notification.save()
            
            # Create payment for new cleaning
            try:
                payment_type = PaymenType.objects.get(pk=26)
                payment_method = PaymentMethod.objects.get(pk=1)
                bank = PaymentMethod.objects.get(pk=6)
                
                payment = Payment(
                    payment_date=self.date,
                    amount=1,
                    payment_type=payment_type,
                    payment_status='Pending',
                    payment_method=payment_method,
                    bank=bank,
                    booking=self.booking,
                    apartment=self.apartment,
                    notes=f"Auto-generated cleaning payment"
                )
                
                # Set last_updated_by
                if hasattr(updated_by, 'full_name'):
                    payment.last_updated_by = updated_by.full_name
                elif isinstance(updated_by, str):
                    payment.last_updated_by = updated_by
                else:
                    payment.last_updated_by = 'System'
                
                payment.save()
            except Exception as e:
                log_error(e, f"Create Cleaning Payment - Cleaning {self.id}", source='model')
            
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
            apartment_name = self.booking.apartment.name if self.booking.apartment else (self.apartment.name if self.apartment else 'Unknown')
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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def notification_message(self):
        if self.booking:
            start_date = format_date(self.booking.start_date)
            end_date = format_date(self.booking.end_date)
            apt_name = self.booking.apartment.name if self.booking.apartment else 'Unknown'
            tenant_name = self.booking.tenant.full_name if self.booking.tenant else 'Unknown'
            return f"{self.message or 'Booking'}: {start_date} - {end_date}, {apt_name}, {tenant_name}."
        elif self.payment:
            payment_date = format_date(self.payment.payment_date)
            apartment_name = self.payment.apartment.name if self.payment.apartment else (
                self.payment.booking.apartment.name if self.payment.booking and self.payment.booking.apartment else '')
            tenant_name = " (" + self.payment.booking.tenant.full_name + \
                ") " if self.payment.booking and self.payment.booking.tenant else ''
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
        from mysite.request_context import apply_user_tracking
        
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        
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
            apt_name = self.booking.apartment.name if self.booking.apartment else 'Unknown'
            links_list.append({
                "name":
                f"Booking: Apartment {apt_name} from {self.booking.start_date} to {self.booking.end_date}",
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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.apartment_name} - {self.date} {self.start_time} - {self.end_time}"

class HandymanBlockedSlot(models.Model):
    date = models.DateField(db_index=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_full_day = models.BooleanField(default=False)
    reason = models.TextField(blank=True, null=True)
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('date', 'start_time', 'end_time')
    
    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)
        
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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Building: {self.building}-{self.associated_room}. P#{self.number}"
    
    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        
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
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        parking_str = f" {self.parking.number}" if self.parking else "No parking"
        apartment_name = f"{self.apartment.name}" if self.apartment else ""
        status_str = f"({self.status})"
        return f"{apartment_name} {parking_str}{status_str}"

    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)

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

class TwilioConversation(models.Model):
    """Model to store Twilio conversation metadata"""
    
    def __str__(self):
        return f"{self.friendly_name} ({self.conversation_sid})"
    
    conversation_sid = models.CharField(max_length=100, unique=True, db_index=True)
    friendly_name = models.CharField(max_length=255)
    
    # Optional relationships
    booking = models.ForeignKey(
        Booking, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='twilio_conversations'
    )
    apartment = models.ForeignKey(
        Apartment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='twilio_conversations'
    )
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)
    
    @property
    def links(self):
        links_list = []
        if self.booking:
            apt_name = self.booking.apartment.name if self.booking.apartment else 'Unknown'
            links_list.append({
                "name": f"Booking: {apt_name} ({self.booking.start_date} - {self.booking.end_date})",
                "link": f"/bookings/?q=id={self.booking.id}"
            })
        if self.apartment:
            links_list.append({
                "name": f"Apartment: {self.apartment.name}",
                "link": f"/apartments/?q=id={self.apartment.id}"
            })
        return links_list


class TwilioMessage(models.Model):
    """Model to store individual Twilio messages"""
    
    def __str__(self):
        return f"{self.author}: {self.body[:50]}..." if len(self.body) > 50 else f"{self.author}: {self.body}"
    
    MESSAGE_DIRECTION = [
        ('inbound', 'Inbound'),
        ('outbound', 'Outbound'),
    ]
    
    message_sid = models.CharField(max_length=100, unique=True, db_index=True)
    conversation = models.ForeignKey(
        TwilioConversation,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    conversation_sid = models.CharField(max_length=100, db_index=True)
    author = models.CharField(max_length=50)  # Phone number or identity like "ASSISTANT"
    body = models.TextField()
    direction = models.CharField(max_length=10, choices=MESSAGE_DIRECTION, default='inbound')
    
    # Twilio metadata
    webhook_sid = models.CharField(max_length=100, blank=True, null=True)
    messaging_binding_address = models.CharField(max_length=20, blank=True, null=True)
    messaging_binding_proxy_address = models.CharField(max_length=20, blank=True, null=True)
    
    # Timestamps
    message_timestamp = models.DateTimeField(auto_now_add=True)
    
    # Tracking fields
    created_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    last_updated_by = models.CharField(max_length=255, blank=True, null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-message_timestamp']
    
    def save(self, *args, **kwargs):
        from mysite.request_context import apply_user_tracking
        updated_by = kwargs.pop('updated_by', None)
        apply_user_tracking(self, updated_by)
        super().save(*args, **kwargs)
    
    @property
    def links(self):
        links_list = []
        links_list.append({
            "name": f"Conversation: {self.conversation.friendly_name}",
            "link": f"/twilio-conversations/?q=id={self.conversation.id}"
        })
        return links_list


class AuditLog(models.Model):
    """
    Comprehensive audit log to track all database changes (creates, updates, deletes).
    This provides a complete history of database activities for monitoring and debugging.
    """
    ACTION_CHOICES = [
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
    ]
    
    # What changed
    model_name = models.CharField(max_length=100, db_index=True)
    object_id = models.CharField(max_length=100, db_index=True)
    object_repr = models.TextField(blank=True, null=True)  # String representation of the object
    action = models.CharField(max_length=10, choices=ACTION_CHOICES, db_index=True)
    
    # Who changed it
    changed_by = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    
    # When changed
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    # What data changed
    changed_fields = models.JSONField(blank=True, null=True)  # List of field names that changed
    old_values = models.JSONField(blank=True, null=True)  # Old values (for updates and deletes)
    new_values = models.JSONField(blank=True, null=True)  # New values (for creates and updates)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'model_name']),
            models.Index(fields=['model_name', '-timestamp']),
            models.Index(fields=['changed_by', '-timestamp']),
        ]
    
    def __str__(self):
        return f"{self.action.upper()} {self.model_name} #{self.object_id} by {self.changed_by or 'System'}"
    
    @property
    def formatted_changes(self):
        """Return a nicely formatted string of changes"""
        if not self.changed_fields:
            return "No field changes recorded"
        
        changes = []
        for field in self.changed_fields:
            old_val = self.old_values.get(field, 'N/A') if self.old_values else 'N/A'
            new_val = self.new_values.get(field, 'N/A') if self.new_values else 'N/A'
            changes.append(f"{field}: {old_val} → {new_val}")
        
        return "\n".join(changes)


class ErrorLog(models.Model):
    """
    Centralized error tracking - stores all application errors with full context
    """
    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]
    
    SOURCE_CHOICES = [
        ('web', 'Web Request'),
        ('api', 'API'),
        ('command', 'Management Command'),
        ('model', 'Model Operation'),
        ('task', 'Background Task'),
        ('webhook', 'Webhook'),
        ('other', 'Other'),
    ]
    
    # Error identification
    error_type = models.CharField(max_length=255, db_index=True)  # Exception class name
    error_message = models.TextField()  # Error message
    context = models.CharField(max_length=500, db_index=True)  # Where it happened
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='other', db_index=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='medium', db_index=True)
    
    # Stack trace and details
    traceback = models.TextField(blank=True, null=True)
    additional_info = models.JSONField(blank=True, null=True)  # Extra context as JSON
    
    # User context
    user_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    user_role = models.CharField(max_length=100, blank=True, null=True)
    user_email = models.CharField(max_length=255, blank=True, null=True)
    
    # Request context (for web errors)
    request_method = models.CharField(max_length=10, blank=True, null=True)
    request_path = models.CharField(max_length=500, blank=True, null=True, db_index=True)
    request_ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    
    # Notification tracking
    telegram_sent = models.BooleanField(default=False)
    telegram_sent_at = models.DateTimeField(blank=True, null=True)
    
    # Metadata
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolved_by = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    
    # Grouping similar errors
    error_hash = models.CharField(max_length=64, db_index=True, blank=True, null=True)  # Hash for grouping similar errors
    occurrences = models.IntegerField(default=1)  # Count of similar errors
    last_occurrence = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'severity']),
            models.Index(fields=['resolved', '-timestamp']),
            models.Index(fields=['source', '-timestamp']),
            models.Index(fields=['error_type', '-timestamp']),
            models.Index(fields=['username', '-timestamp']),
            models.Index(fields=['error_hash', '-last_occurrence']),
        ]
    
    def __str__(self):
        return f"{self.error_type} - {self.context} ({self.timestamp})"


class SystemLog(models.Model):
    """
    General system logging for important events and operations
    """
    LEVEL_CHOICES = [
        ('debug', 'Debug'),
        ('info', 'Info'),
        ('warning', 'Warning'),
        ('error', 'Error'),
        ('critical', 'Critical'),
    ]
    
    CATEGORY_CHOICES = [
        ('auth', 'Authentication'),
        ('sms', 'SMS/Messaging'),
        ('payment', 'Payment'),
        ('booking', 'Booking'),
        ('contract', 'Contract'),
        ('notification', 'Notification'),
        ('sync', 'Synchronization'),
        ('cleanup', 'Cleanup'),
        ('system', 'System'),
        ('other', 'Other'),
    ]
    
    # Log identification
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='info', db_index=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='other', db_index=True)
    message = models.TextField()
    context = models.CharField(max_length=500, blank=True, null=True, db_index=True)
    
    # Details
    details = models.JSONField(blank=True, null=True)  # Additional structured data
    
    # User context (if applicable)
    user_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    username = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    
    # Metadata
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['-timestamp', 'level']),
            models.Index(fields=['category', '-timestamp']),
            models.Index(fields=['level', 'category', '-timestamp']),
        ]
    
    def __str__(self):
        return f"[{self.level.upper()}] {self.category}: {self.message[:100]}"


def send_telegram_message(chat_id, token, message):
    if chat_id and token:
        base_url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"
        requests.get(base_url)