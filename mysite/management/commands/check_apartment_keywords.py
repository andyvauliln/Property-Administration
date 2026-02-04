from django.core.management.base import BaseCommand
from mysite.models import Apartment


class Command(BaseCommand):
    help = 'Check if all apartments have their name in keywords'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Add apartment name to keywords if missing'
        )

    def handle(self, *args, **options):
        fix_mode = options.get('fix', False)
        
        apartments = Apartment.objects.all()
        missing = []
        
        for apt in apartments:
            keywords = (apt.keywords or '').lower()
            name = apt.name.lower()
            
            # Check if apartment name is in keywords
            if name not in keywords:
                missing.append(apt)
        
        if not missing:
            self.stdout.write(self.style.SUCCESS(f'All {apartments.count()} apartments have their name in keywords'))
            return
        
        self.stdout.write(self.style.WARNING(f'Found {len(missing)} apartments without name in keywords:\n'))
        
        for apt in missing:
            self.stdout.write(f'  ID: {apt.id}')
            self.stdout.write(f'  Name: {apt.name}')
            self.stdout.write(f'  Keywords: {apt.keywords or "(empty)"}')
            self.stdout.write('')
            
            if fix_mode:
                if apt.keywords:
                    apt.keywords = f'{apt.name}, {apt.keywords}'
                else:
                    apt.keywords = apt.name
                apt.save()
                self.stdout.write(self.style.SUCCESS(f'  -> Fixed: {apt.keywords}'))
                self.stdout.write('')
        
        if fix_mode:
            self.stdout.write(self.style.SUCCESS(f'\nFixed {len(missing)} apartments'))
        else:
            self.stdout.write(f'\nRun with --fix to add apartment name to keywords')
