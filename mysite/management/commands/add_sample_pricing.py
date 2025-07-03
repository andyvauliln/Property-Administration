from django.core.management.base import BaseCommand
from mysite.models import Apartment, ApartmentPrice
from datetime import date, timedelta
from decimal import Decimal


class Command(BaseCommand):
    help = 'Add sample pricing data to apartments for testing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apartment-id',
            type=int,
            help='Add pricing only to specific apartment ID',
        )

    def handle(self, *args, **options):
        apartment_id = options.get('apartment_id')
        
        if apartment_id:
            try:
                apartments = [Apartment.objects.get(id=apartment_id)]
            except Apartment.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Apartment with ID {apartment_id} not found.')
                )
                return
        else:
            # Get first 5 apartments for sample data
            apartments = Apartment.objects.all()[:5]

        if not apartments:
            self.stdout.write(
                self.style.WARNING('No apartments found to add pricing to.')
            )
            return

        today = date.today()
        
        for apartment in apartments:
            # Skip if apartment already has pricing
            if apartment.prices.exists():
                self.stdout.write(
                    f'Apartment "{apartment.name}" already has pricing data. Skipping.'
                )
                continue

            try:
                # Add historical price (3 months ago)
                historical_date = today - timedelta(days=90)
                ApartmentPrice.objects.create(
                    apartment=apartment,
                    price=Decimal('1200.00'),
                    effective_date=historical_date,
                    notes='Initial pricing'
                )

                # Add current price (1 month ago)
                current_date = today - timedelta(days=30)
                ApartmentPrice.objects.create(
                    apartment=apartment,
                    price=Decimal('1350.00'),
                    effective_date=current_date,
                    notes='Price adjustment'
                )

                # Add future price (1 month from now)
                future_date = today + timedelta(days=30)
                ApartmentPrice.objects.create(
                    apartment=apartment,
                    price=Decimal('1500.00'),
                    effective_date=future_date,
                    notes='Scheduled increase'
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f'Added sample pricing for apartment "{apartment.name}": '
                        f'Historical: $1200, Current: $1350, Future: $1500'
                    )
                )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'Error adding pricing for apartment "{apartment.name}": {e}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS('\nSample pricing data added successfully!')
        )
        self.stdout.write(
            'You can now view apartments to see current pricing information,'
        )
        self.stdout.write(
            'and visit /apartmentprice/ to manage all pricing data.'
        ) 