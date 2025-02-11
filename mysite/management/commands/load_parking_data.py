import json
from django.core.management.base import BaseCommand
from mysite.models import Parking

class Command(BaseCommand):
    help = 'Load parking data from JSON file into Parking model'

    def handle(self, *args, **kwargs):
        # Load the JSON data
        with open('apartments.json', 'r') as file:
            data = json.load(file)

        # Counters for tracking
        created_count = 0
        skipped_count = 0

        # Process each building
        for building, apartments in data.items():
            for apartment_data in apartments:
                parking_numbers = apartment_data.get('parking', [])
                for parking_number in parking_numbers:
                    # Check if parking already exists
                    if not Parking.objects.filter(number=parking_number).exists():
                        # Create new parking
                        Parking.objects.create(
                            number=parking_number,
                            building=building,
                            notes=apartment_data.get('notes', '')
                        )
                        created_count += 1
                    else:
                        skipped_count += 1

        # Print final summary
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS(f"Processing completed with following results:"))
        self.stdout.write(f"Created: {created_count} parking spots")
        self.stdout.write(f"Skipped (already exist): {skipped_count} parking spots")
        self.stdout.write("="*50)