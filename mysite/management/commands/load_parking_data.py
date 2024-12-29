import json
from django.core.management.base import BaseCommand
from mysite.models import Apartment, ApartmentParking

class Command(BaseCommand):
    help = 'Load parking data from JSON file into ApartmentParking model'

    def handle(self, *args, **kwargs):
        # Load the JSON data
        with open('apartments.json', 'r') as file:
            data = json.load(file)

        # Counters for tracking
        created_count = 0
        skipped_count = 0
        apartment_not_found = 0
        empty_parking = 0

        # Process each building
        for building_number, apartments in data.items():
            self.stdout.write(f"\nProcessing building {building_number}...")

            for apt_data in apartments:
                apartment_number = apt_data['apartment']
                parking_numbers = apt_data.get('parking', [])

                # Skip if no parking numbers
                if not parking_numbers:
                    empty_parking += 1
                    self.stdout.write(
                        self.style.NOTICE(
                            f"Skipping apartment {apartment_number} in building {building_number} - no parking assigned"
                        )
                    )
                    continue

                # Try to get the corresponding apartment
                try:
                    apartment = Apartment.objects.get(
                        building_n=building_number,
                        apartment_n=apartment_number
                    )

                    # Process each parking spot
                    for parking_number in parking_numbers:
                        # Skip if parking number is empty or None
                        if not parking_number:
                            empty_parking += 1
                            continue

                        # Check if parking already exists
                        existing_parking = ApartmentParking.objects.filter(
                            parking_number=parking_number,
                            apartment=apartment
                        ).first()

                        if not existing_parking:
                            try:
                                # Create new parking spot
                                ApartmentParking.objects.create(
                                    parking_number=parking_number,
                                    apartment=apartment,
                                    status='Available',
                                    notes=apt_data.get('notes', '')
                                )
                                created_count += 1
                                self.stdout.write(
                                    f"Created parking {parking_number} for apartment {apartment_number}"
                                )
                            except Exception as e:
                                self.stdout.write(
                                    self.style.ERROR(
                                        f"Error creating parking {parking_number} for apartment {apartment_number}: {str(e)}"
                                    )
                                )
                        else:
                            skipped_count += 1
                            self.stdout.write(
                                f"Skipped existing parking {parking_number} for apartment {apartment_number}"
                            )

                except Apartment.DoesNotExist:
                    apartment_not_found += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"Apartment not found: Building {building_number}, Apartment {apartment_number}"
                        )
                    )

        # Print final summary
        self.stdout.write("\n" + "="*50)
        self.stdout.write(self.style.SUCCESS(f"Processing completed with following results:"))
        self.stdout.write(f"Created: {created_count} parking spots")
        self.stdout.write(f"Skipped (already exist): {skipped_count} parking spots")
        self.stdout.write(f"Apartments not found: {apartment_not_found}")
        self.stdout.write(f"Empty parking entries: {empty_parking}")
        self.stdout.write("="*50)