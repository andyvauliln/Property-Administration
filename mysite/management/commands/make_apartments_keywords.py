from django.core.management.base import BaseCommand
from mysite.models import Apartment

class Command(BaseCommand):
    help = 'Updates apartment keywords with apartment name and owner information'

    def handle(self, *args, **options):
        apartments = Apartment.objects.all()
        updated_count = 0

        for apartment in apartments:
            # Get existing keywords as a set to avoid duplicates
            existing_keywords = set(keyword.strip() for keyword in apartment.keywords.split(',')) if apartment.keywords else set()
            new_keywords = set()
            
            # Add apartment name if not already in keywords
            if apartment.name:
                new_keywords.add(apartment.name.strip())
            
            # Add owner information if exists and not already in keywords
            if apartment.owner:
                new_keywords.add(apartment.owner.full_name.strip())
            
            # Combine existing and new keywords, removing duplicates
            final_keywords = existing_keywords.union(new_keywords)
            
            # Only update if there are new keywords to add
            if len(final_keywords) > len(existing_keywords):
                # Join with commas and strip any extra whitespace
                apartment.keywords = ', '.join(sorted(final_keywords))
                apartment.save()
                updated_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'Updated keywords for apartment: {apartment.name}')
                )

        self.stdout.write(
            self.style.SUCCESS(f'Successfully updated keywords for {updated_count} apartments')
        )
