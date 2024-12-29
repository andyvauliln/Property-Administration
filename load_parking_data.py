import json
from django.core.management.base import BaseCommand
from mysite.models import Apartment, ApartmentParking

class Command(BaseCommand):
    help = 'Load parking data from JSON file into ApartmentParking model'

    def log_message(self, building, apartment, message, style='SUCCESS'):
        """Helper method to format log messages consistently"""
        prefix = f"[Building {building}, Apt {apartment}]"
        style_method = getattr(self.style, style, self.style.SUCCESS)
        self.stdout.write(style_method(f"{prefix} {message}"))

    def handle(self, *args, **kwargs):
        # Load the JSON data
        try:
            with open('apartments.json', 'r') as file:
                data = json.load(file)
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR("Error: apartments.json file not found!"))
            return
        except json.JSONDecodeError:
            self.stdout.write(self.style.ERROR("Error: Invalid JSON format in apartments.json!"))
            return

        # Counters for tracking
        stats = {
            'created': 0,
            'skipped': 0,
            'not_found': 0,
            'empty_parking': 0,
            'errors': 0,
            'invalid_data': 0
        }

        # Process each building
        for building_number, apartments in data.items():
            self.stdout.write(self.style.MIGRATE(f"\n=== Processing Building {building_number} ==="))

            for apt_data in apartments:
                try:
                    apartment_number = apt_data['apartment']
                except KeyError:
                    stats['invalid_data'] += 1
                    self.log_message(building_number, "N/A", 
                        "Invalid apartment data format - missing apartment number", "ERROR")
                    continue

                parking_numbers = apt_data.get('parking', [])

                # Skip if no parking numbers
                if not parking_numbers:
                    stats['empty_parking'] += 1
                    self.log_message(building_number, apartment_number,
                        "No parking spots assigned - skipping", "NOTICE")
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
                            stats['empty_parking'] += 1
                            self.log_message(building_number, apartment_number,
                                "Empty parking number found - skipping", "WARNING")
                            continue

                        # Check if parking already exists
                        existing_parking = ApartmentParking.objects.filter(
                            parking_number=parking_number
                        ).first()

                        if existing_parking:
                            stats['skipped'] += 1
                            if existing_parking.apartment != apartment:
                                self.log_message(building_number, apartment_number,
                                    f"WARNING: Parking {parking_number} already assigned to apartment "
                                    f"{existing_parking.apartment.apartment_n} in building "
                                    f"{existing_parking.apartment.building_n}", "WARNING")
                            else:
                                self.log_message(building_number, apartment_number,
                                    f"Parking {parking_number} already exists - skipping", "NOTICE")
                            continue

                        try:
                            # Create new parking spot
                            ApartmentParking.objects.create(
                                parking_number=parking_number,
                                apartment=apartment,
                                status='Available',
                                notes=apt_data.get('notes', '')
                            )
                            stats['created'] += 1
                            self.log_message(building_number, apartment_number,
                                f"Successfully created parking spot {parking_number}")

                        except Exception as e:
                            stats['errors'] += 1
                            self.log_message(building_number, apartment_number,
                                f"Error creating parking {parking_number}: {str(e)}", "ERROR")

                except Apartment.DoesNotExist:
                    stats['not_found'] += 1
                    self.log_message(building_number, apartment_number,
                        "Apartment not found in database", "WARNING")

        # Print final summary
        self.stdout.write("\n" + "="*70)
        self.stdout.write(self.style.SUCCESS("IMPORT SUMMARY:"))
        self.stdout.write(f"Successfully created: {stats['created']} parking spots")
        self.stdout.write(f"Skipped (already exist): {stats['skipped']} parking spots")
        self.stdout.write(f"Apartments not found: {stats['not_found']}")
        self.stdout.write(f"Empty parking entries: {stats['empty_parking']}")
        self.stdout.write(f"Data format errors: {stats['invalid_data']}")
        self.stdout.write(f"Processing errors: {stats['errors']}")
        self.stdout.write("="*70) 