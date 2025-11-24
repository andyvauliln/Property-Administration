from django.core.management.base import BaseCommand
from mysite.models import User


class Command(BaseCommand):
    help = 'Re-validate and format all user phone numbers using centralized validation'

    def handle(self, *args, **options):
        users = User.objects.all()
        
        self.stdout.write(self.style.WARNING('\n' + '='*80))
        self.stdout.write(self.style.WARNING('FIXING USER PHONE NUMBERS'))
        self.stdout.write(self.style.WARNING('='*80 + '\n'))
        
        total_users = 0
        fixed_users = 0
        invalid_users = 0

        for user in users:
            if user.phone:
                original_phone = user.phone
                total_users += 1
                
                try:
                    # Just save - validation happens automatically in User.save()
                    user.save()
                    
                    if user.phone != original_phone:
                        if user.phone is None:
                            self.stdout.write(self.style.ERROR(
                                f'❌ Invalid phone for {user.full_name} ({user.email}): "{original_phone}" -> None'))
                            invalid_users += 1
                        else:
                            self.stdout.write(self.style.SUCCESS(
                                f'✅ Fixed phone for {user.full_name} ({user.email}): "{original_phone}" -> "{user.phone}"'))
                            fixed_users += 1
                    else:
                        self.stdout.write(self.style.SUCCESS(
                            f'✓ Valid phone for {user.full_name} ({user.email}): "{user.phone}"'))
                
                except ValueError as e:
                    # Handle validation errors by setting phone to None
                    self.stdout.write(self.style.ERROR(
                        f'❌ Invalid phone for {user.full_name} ({user.email}): "{original_phone}" -> None (Error: {str(e)})'))
                    user.phone = None
                    # Use update_fields to bypass the validation in save()
                    user.save(update_fields=['phone'])
                    invalid_users += 1
                
                except Exception as e:
                    # Catch any other unexpected errors
                    self.stdout.write(self.style.ERROR(
                        f'❌ Unexpected error for {user.id}, {user.phone}, {user.full_name} ({user.email}): {str(e)}'))
                    invalid_users += 1
        
        self.stdout.write(self.style.WARNING('\n' + '='*80))
        self.stdout.write(self.style.WARNING(f'SUMMARY:'))
        self.stdout.write(self.style.WARNING(f'Total users with phones: {total_users}'))
        self.stdout.write(self.style.WARNING(f'Fixed: {fixed_users}'))
        self.stdout.write(self.style.WARNING(f'Invalid (set to None): {invalid_users}'))
        self.stdout.write(self.style.WARNING(f'Already valid: {total_users - fixed_users - invalid_users}'))
        self.stdout.write(self.style.WARNING('='*80 + '\n'))
