import re
from django.core.management.base import BaseCommand
from mysite.models import User, validate_and_format_phone


class Command(BaseCommand):
    help = 'Check user phone numbers for invalid formats using centralized validation'

    def handle(self, *args, **options):
        users = User.objects.all()
        
        invalid_users = []
        valid_users = 0
        
        self.stdout.write(self.style.WARNING('\n' + '='*80))
        self.stdout.write(self.style.WARNING('CHECKING USER PHONE NUMBERS'))
        self.stdout.write(self.style.WARNING('Using centralized E.164 validation (supports international numbers)'))
        self.stdout.write(self.style.WARNING('='*80 + '\n'))
        
        for user in users:
            if not user.phone:
                # Skip users without phone numbers
                continue
            
            phone = user.phone
            
            # Use the centralized validation function
            validated = validate_and_format_phone(phone)
            
            if validated is None:
                # Phone is invalid
                invalid_users.append({
                    'user': user,
                    'phone': phone,
                    'reason': 'Does not match E.164 format'
                })
            elif validated != phone:
                # Phone can be reformatted (not in optimal format)
                invalid_users.append({
                    'user': user,
                    'phone': phone,
                    'reason': f'Should be reformatted to: {validated}'
                })
            else:
                # Phone is valid
                valid_users += 1
        
        # Print results
        if invalid_users:
            self.stdout.write(self.style.ERROR(f'\nFound {len(invalid_users)} users with phone issues:\n'))
            
            for idx, item in enumerate(invalid_users, 1):
                user = item['user']
                
                self.stdout.write(self.style.WARNING(f'\n--- User #{idx} ---'))
                self.stdout.write(f'ID: {user.id}')
                self.stdout.write(f'Name: {user.full_name}')
                self.stdout.write(f'Email: {user.email}')
                self.stdout.write(f'Phone: {item["phone"]}')
                self.stdout.write(f'Role: {user.role}')
                self.stdout.write(f'Active: {user.is_active}')
                self.stdout.write(f'Created: {user.created_at}')
                
                self.stdout.write(self.style.ERROR(f'Issue: {item["reason"]}'))
            
            self.stdout.write(self.style.WARNING('\n' + '='*80))
            self.stdout.write(self.style.ERROR(f'SUMMARY:'))
            self.stdout.write(self.style.SUCCESS(f'  Valid phones: {valid_users}'))
            self.stdout.write(self.style.ERROR(f'  Invalid/Need reformatting: {len(invalid_users)}'))
            self.stdout.write(self.style.WARNING(f'\nRun "python manage.py fix_phone_numbers" to fix these issues'))
            self.stdout.write(self.style.WARNING('='*80 + '\n'))
        else:
            self.stdout.write(self.style.SUCCESS(f'\n✓ All {valid_users} user phone numbers are valid!\n'))

