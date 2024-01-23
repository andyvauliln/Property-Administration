import re
from django.core.management.base import BaseCommand
from mysite.models import User


class Command(BaseCommand):
    help = 'Update phone numbers of all users to a specific format'

    def handle(self, *args, **options):
        users = User.objects.all()

        for user in users:
            if user.phone:
                formatted_phone = format_phone(user.phone)

                # Update the user's phone number
                user.phone = formatted_phone
                user.save()

                self.stdout.write(self.style.SUCCESS(
                    f'Update phone from {user.phone} to {formatted_phone} for {user.full_name}'))
            # else:
            #     self.stdout.write(self.style.WARNING(f'Skipping user {user.username} {user.phone}- no profile or phone information'))
            #     print("")


def format_phone(phone):
    if phone.startswith(('+')):
        cleaned_phone = re.sub(r'\D', '', phone)
        cleaned_phone = "+" + cleaned_phone
        return cleaned_phone

    elif phone.startswith(('0')):
        cleaned_phone = re.sub(r'\D', '', phone)
        cleaned_phone = "+1" + cleaned_phone[1:]
        return cleaned_phone

    elif phone.startswith(('1')):
        cleaned_phone = re.sub(r'\D', '', phone)
        cleaned_phone = "+" + cleaned_phone
        return cleaned_phone

    else:
        cleaned_phone = re.sub(r'\D', '', phone)
        cleaned_phone = "+1" + cleaned_phone
        return cleaned_phone
