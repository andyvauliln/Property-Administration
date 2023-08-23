from django.core.management.base import BaseCommand
from django.utils import timezone
from django.apps import apps
from datetime import timedelta, date
from mysite.models import User, Property, Booking, Payment, Cleaning, Contract, Notification, PaymentMethod, Bank
from django.core.management.color import no_style
from django.db import connection

class Command(BaseCommand):
    help = 'Seeds the database with initial data'
    
    def reset_sequences(self):
    # Get the table names for the app
        tables = connection.introspection.table_names()
        tables = [t for t in tables if t.startswith("mysite" + '_')]

        with connection.cursor() as cursor:
            # Depending on your database, the SQL to reset sequences may vary
            if connection.vendor == 'postgresql':
                for table in tables:
                    sequence_name = f"{table}_id_seq"
                    sql = f"ALTER SEQUENCE {sequence_name} RESTART WITH 1;"
                    cursor.execute(sql)

    def delete_all_rows(self):
        # Get all models from all installed apps
        all_models = apps.get_models()

        # Delete all rows for each model
        for model in all_models:
            model.objects.all().delete()   
    
    def handle(self, *args, **kwargs):
        self.stdout.write('Deleting all data...')
        self.delete_all_rows()
        self.reset_sequences()
        
        self.stdout.write('Seeding data...')
        self._create_users()
        self._create_payment_methods()
        self._create_banks()
        self._create_properties()
        self._create_bookings()
        self.stdout.write('Done.')

    def _create_users(self):
        admin_user = User.objects.create(email="admin@gmail.com", role="admin", full_name="Admin")
        admin_user.set_password("admin")
        admin_user.save()
        User.objects.create(email="cleaner1@example.com", phone="+14345676", role="Cleaner", full_name="Cleaner One")
        User.objects.create(email="cleaner2@example.com",  phone="+13345676", role="Cleaner", full_name="Cleaner Two")
        User.objects.create(email="manager1@example.com",  phone="+15345676", role="Manager", full_name="Manager One")
        User.objects.create(email="manager2@example.com",  phone="+12345676", role="Manager", full_name="Manager Two")
        User.objects.create(email="tenant1@example.com", phone="+16345676", role="Tenant", full_name="Tenant One")
        User.objects.create(email="tenant2@example.com", phone="+17345676", role="Tenant", full_name="Tenant Two")
        User.objects.create(email="tenant3@example.com", phone="+18314676", role="Tenant", full_name="Tenant Three")
        User.objects.create(email="owner1@example.com",  phone="+19314676", role="Owner", full_name="Owner One")
        User.objects.create(email="owner2@example.com",  phone="+11445676", role="Owner", full_name="Owner Two")
        User.objects.create(email="owner3@example.com",  phone="+155345676", role="Owner", full_name="Owner Three")

    def _create_payment_methods(self):
        methods = ["Zelle", "CC", "Paypal", "Cash", "Venmo"]
        for method in methods:
            PaymentMethod.objects.create(method_name=method)

    def _create_banks(self):
        banks = ["BA", "TD", "PNC"]
        for bank in banks:
            Bank.objects.create(bank_name=bank)

    def _create_properties(self):
        # Fetching managers and owners
        managers = list(User.objects.filter(role="Manager"))
        owners = list(User.objects.filter(role="Owner"))

        # Property 1
        Property.objects.create(
            name="Property 1",
            property_type="In Management",
            status="Unavailable",
            manager=managers[0],
            owner=owners[0],
            notes="This is a note for Property 1",
            web_link="http://example.com/property1",
            address="123 Main St, City, Country",
            bedrooms=3,
            bathrooms=2
        )

        # Property 2
        Property.objects.create(
            name="Property 2",
            property_type="In Management",
            status="Available",
            manager=managers[1],  
            owner=owners[1],
            notes="This is a note for Property 2",
            web_link="http://example.com/property2",
            address="456 Second St, City, Country",
            bedrooms=4,
            bathrooms=3
        )

        # Property 3
        Property.objects.create(
            name="Property 3",
            property_type="In Ownership",
            status="Available",
            owner=owners[2],  
            notes="This is a note for Property 3",
            web_link="http://example.com/property3",
            address="789 Third St, City, Country",
            bedrooms=2,
            bathrooms=1
    )

    def _create_bookings(self):
        today = date(2023, 8, 20)
        holding_deposit = 500
        damage_deposit = 200
        tenants = list(User.objects.filter(role="Tenant"))
        cleaners = list(User.objects.filter(role="Cleaner"))
        properties = list(Property.objects.all())
        payment_methods = list(PaymentMethod.objects.all())  # Fetching multiple payment methods
        banks = list(Bank.objects.all())  # Fetching multiple banks
        

        booking_details = [
            # prope1 (not available) past 20d
            {"duration": 20, "booking_status": "Confirmed", "price": 100, "offset": -30, "period": "Dayly"}, 
             # prope2 feature 20d
            {"duration": 90, "booking_status": "Confirmed", "price": 3000, "offset": -90, "period": "Monthly"},
             # prope3 past 3m
            {"duration": 90, "booking_status": "Pending", "price": 3000, "offset": 0, "period": "Monthly"},
             # prope1 feature 20d
            {"duration": 90, "booking_status": "Canceled", "price": 3000, "offset": 30, "period": "Monthly"},
             # prope2 feature 20d
            {"duration": 30, "booking_status": "Pending", "price": 2000, "offset": 20, "period": "Monthly"},
             # prope3 feature 20d
            {"duration": 5, "booking_status": "Pending", "price": 1000, "offset": 55, "period": "Dayly"},
        ]
        properties.append(properties[0])
        properties.append(properties[1])
        properties.append(properties[1])
        # Create bookings for all properties
        for idx, property in enumerate(properties):
            details = booking_details[idx % len(booking_details)]
            tenant = tenants[idx % len(tenants)] 
            holding_deposit = 500 * (idx or 1)
            damage_deposit = 200 * (idx or 1)
            self.stdout.write('********************* BOOKING ********************')
            

            start_date = today + timedelta(days=details["offset"])
            end_date = start_date + timedelta(days=details["duration"])
            
            # *************** BOOKING ***************

            booking = Booking.objects.create(
                start_date=start_date,
                end_date=end_date,
                tenant=tenant,
                period=details["period"],
                property=property,
                status=details["booking_status"],
                price=details["price"]
            )
          

            # *************** CONTRACT ***************
            
            if details["booking_status"] == "Confirmed":
                contract_status = "Signed" if details["offset"] < 0 else "Signed"
            elif details["booking_status"] == "Pending":
                contract_status = "Pending"
            elif details["booking_status"] == "Canceled":
                contract_status = "Canceled"
            else:
                contract_status = "pending" 

            Contract.objects.create(
                sign_date=start_date,
                link="http://example.com/contract",
                tenant=tenant,
                status=contract_status,
                contract_id="12sdf444154gws2414",
                owner=property.owner,
                property=property
            )
            
             # *************** CLEANINGS ***************
            if details["booking_status"] == "Completed":
                cleaning_status = "Completed" if details["offset"] < 0 else "Scheduled"
            elif details["booking_status"] == "Scheduled":
                cleaning_status = "Scheduled"
            elif details["booking_status"] == "Canceled":
                cleaning_status = "Canceled"
            else:
                cleaning_status = "Scheduled" 
            
                
            Cleaning.objects.create(
                date=booking.end_date + timedelta(days=1),
                booking=booking,
                status=cleaning_status,
                cleaner=cleaners[idx % len(cleaners)],
                tasks="Buy pls new cleaning chemicals",  # You can customize this as needed
                notes="I left keys under the mat"
            )
            
             # *************** PAYMENTS ***************
            
            if details["booking_status"] == "Confirmed":
                payment_status = "Confirmed" if details["offset"] < 0 else "Pending"
            elif details["booking_status"] == "Pending":
                payment_status = "Pending"
            elif details["booking_status"] == "Canceled":
                payment_status = "Canceled"
            else:
                payment_status = "Pending" 

            Payment.objects.create(
                payment_date=start_date,
                amount=holding_deposit,
                payment_status=payment_status,
                booking=booking,
                payment_type="Holding Deposit",
                payment_method=payment_methods[idx % len(payment_methods)],
                bank=banks[idx % len(banks)],
                notes="Holding Deposit"
            )
            Payment.objects.create(
                payment_date=start_date + timedelta(days=1),
                amount=damage_deposit,
                payment_status=payment_status,
                booking=booking,
                payment_type="Damage Deposit",
                payment_method=payment_methods[idx % len(payment_methods)],
                bank=banks[idx % len(banks)],
                notes="Damage Deposit"
            )

            # ******** DAYLT PAYMENTS *********
            
            if details["period"] == "Dayly":
                Payment.objects.create(
                    payment_date=start_date + timedelta(days=1),
                    amount=details["price"] * details["duration"],  # Subtracting damage deposit
                    payment_status=payment_status,
                    booking=booking,
                    payment_type="Income",
                    payment_method=payment_methods[idx % len(payment_methods)],
                    bank=banks[idx % len(banks)],
                    notes="Daily rent payment"
                )

            # ******** MONTHLY PAYMENTS *********
            elif details["period"] == "Monthly":
                for month in range(details["duration"] // 30):
                    self.stdout.write('********************* PAYMENT ********************')
                    payment_amount = details["price"]
                    if month == 0:
                        payment_amount -= holding_deposit  # Subtracting damage holding for the first month


                    payment = Payment.objects.create(
                        payment_date=start_date + timedelta(days=30 * (month + 1)),
                        amount=payment_amount,
                        payment_status=payment_status,
                        booking=booking,
                        payment_type="Income",
                        payment_method=payment_methods[idx % len(payment_methods)],
                        bank=banks[idx % len(banks)],
                        notes=f"Ask bank for confirmation"
                    )

                    # Notification for payment
                    Notification.objects.create(
                        date=start_date + timedelta(days=30 * (month + 1)),
                        status="Pending",
                        message=f"Payment of ${payment.amount} for {booking.property.name} at {payment.payment_date}",
                        user=booking.tenant
                    )

            # *************** NOTIFICATIONS ***************

            # Create corresponding notifications
            Notification.objects.create(
                date=end_date,
                status="Pending",
                message=f"Rent for {property.name} is finishing at {end_date}.",
                user=tenant
            )
            Notification.objects.create(
                date=end_date + timedelta(days=1),
                status="Pending",
                message=f"Cleaning for {property.name} is scheduled at {end_date + timedelta(days=1)}.",
                user=tenant
            )
            Notification.objects.create(
                date=end_date,
                status="Pending",
                message=f"Contract for {property.name} has been send to tenant {tenant.full_name} at {start_date}",
                user=tenant
            )
            if details["booking_status"] != "Canceled":
                Notification.objects.create(
                    date=start_date,
                    status="Pending",
                    message=f"Contract for {property.name} has been signed at {start_date + timedelta(days=1)}",
                    user=tenant
                )
            Notification.objects.create(
                date=start_date + timedelta(days=5),
                status="Pending",
                message=f"Ask tenant if everything is okay and if they want additional cleaning at {start_date + timedelta(days=5)}",
                user=booking.tenant
            )
