from django.core.management.base import BaseCommand
from django.utils import timezone
from mysite.models import Apartment, ApartmentPrice
from datetime import date


class Command(BaseCommand):
    help = 'Migrate existing apartment price data to the new ApartmentPrice model'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without actually doing it',
        )
        parser.add_argument(
            '--default-date',
            type=str,
            default=str(date.today()),
            help='Default effective date for migrated prices (YYYY-MM-DD format)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        default_date = options['default_date']
        
        try:
            effective_date = date.fromisoformat(default_date)
        except ValueError:
            self.stdout.write(
                self.style.ERROR(f'Invalid date format: {default_date}. Use YYYY-MM-DD format.')
            )
            return

        self.stdout.write(f'Processing apartment pricing migration...')
        self.stdout.write(f'Default effective date: {effective_date}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        apartments = Apartment.objects.all()
        migrated_count = 0
        skipped_count = 0

        for apartment in apartments:
            # Check if apartment already has pricing records
            existing_prices = apartment.prices.count()
            
            if existing_prices > 0:
                self.stdout.write(
                    f'Apartment "{apartment.name}" already has {existing_prices} price record(s). Skipping.'
                )
                skipped_count += 1
                continue

            # Note: Since we removed the price field, we can't migrate existing data
            # This is more of a template for future use
            self.stdout.write(
                f'Apartment "{apartment.name}" has no pricing history. '
                f'You may want to add an initial price record.'
            )
            
            # Example of how to add a price record (commented out since no existing price to migrate)
            # if not dry_run:
            #     ApartmentPrice.objects.create(
            #         apartment=apartment,
            #         price=0.00,  # Would use apartment.price if it still existed
            #         effective_date=effective_date,
            #         notes='Initial price record (migrated from old price field)'
            #     )
            #     migrated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Migration complete! Migrated: {migrated_count}, Skipped: {skipped_count}'
            )
        )
        
        # Show example usage
        self.stdout.write('\n' + '='*50)
        self.stdout.write(self.style.SUCCESS('EXAMPLE USAGE OF NEW PRICING SYSTEM:'))
        self.stdout.write('='*50)
        
        self.stdout.write('\n1. Add a price for an apartment:')
        self.stdout.write('   from mysite.models import Apartment, ApartmentPrice')
        self.stdout.write('   from datetime import date')
        self.stdout.write('   ')
        self.stdout.write('   apartment = Apartment.objects.first()')
        self.stdout.write('   ApartmentPrice.objects.create(')
        self.stdout.write('       apartment=apartment,')
        self.stdout.write('       price=1500.00,')
        self.stdout.write('       effective_date=date.today(),')
        self.stdout.write('       notes="Initial pricing"')
        self.stdout.write('   )')
        
        self.stdout.write('\n2. Get current price (effective today or before):')
        self.stdout.write('   current_price = apartment.current_price')
        
        self.stdout.write('\n3. Get price on specific date:')
        self.stdout.write('   from datetime import date')
        self.stdout.write('   price_on_date = apartment.get_price_on_date(date(2024, 1, 1))')
        
        self.stdout.write('\n4. Update price (create new record):')
        self.stdout.write('   ApartmentPrice.objects.create(')
        self.stdout.write('       apartment=apartment,')
        self.stdout.write('       price=1600.00,')
        self.stdout.write('       effective_date=date.today(),')
        self.stdout.write('       notes="Price increase"')
        self.stdout.write('   )')
        
        self.stdout.write('\n5. Schedule future price change:')
        self.stdout.write('   from datetime import date, timedelta')
        self.stdout.write('   future_date = date.today() + timedelta(days=30)')
        self.stdout.write('   ApartmentPrice.objects.create(')
        self.stdout.write('       apartment=apartment,')
        self.stdout.write('       price=1700.00,')
        self.stdout.write('       effective_date=future_date,')
        self.stdout.write('       notes="Scheduled price increase"')
        self.stdout.write('   )')
        self.stdout.write('   # current_price will still return 1600.00 until future_date arrives')
        
        self.stdout.write('\n6. View price history (all prices including future):')
        self.stdout.write('   for price in apartment.prices.all():')
        self.stdout.write('       status = "ACTIVE" if price.effective_date <= date.today() else "FUTURE"')
        self.stdout.write('       print(f"{price.effective_date}: ${price.price} [{status}] - {price.notes}")')
        
        self.stdout.write('\n7. Get only future price changes:')
        self.stdout.write('   future_prices = apartment.get_future_prices()')
        self.stdout.write('   for price in future_prices:')
        self.stdout.write('       print(f"Scheduled for {price.effective_date}: ${price.price}")')
        
        self.stdout.write('\n' + '='*50) 