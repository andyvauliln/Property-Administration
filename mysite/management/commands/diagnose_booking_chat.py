"""
Diagnose why booking 1645 (Brandon Troth) and Shulim ended up in the same SMS chat.
Run: python manage.py diagnose_booking_chat 1645
"""
from django.core.management.base import BaseCommand
from mysite.models import Booking, TwilioConversation, TwilioMessage, User


class Command(BaseCommand):
    help = "Diagnose chat mixing for a booking (e.g. two tenants in same conversation)"

    def add_arguments(self, parser):
        parser.add_argument("booking_id", type=int, help="Booking ID to diagnose")

    def handle(self, *args, **options):
        bid = options["booking_id"]
        self.stdout.write(f"\n=== Diagnosing Booking {bid} ===\n")

        try:
            booking = Booking.objects.select_related("tenant", "apartment").get(id=bid)
        except Booking.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Booking {bid} not found"))
            return

        self.stdout.write(f"Booking: {booking.id}")
        self.stdout.write(f"  Tenant: {booking.tenant.full_name} (id={booking.tenant_id})")
        self.stdout.write(f"  Phone: {booking.tenant.phone!r}")
        self.stdout.write(f"  Apartment: {booking.apartment.name}")
        self.stdout.write(f"  Dates: {booking.start_date} to {booking.end_date}\n")

        # Check for duplicate phone numbers (other tenants with same phone)
        tenant_phone = booking.tenant.phone
        if tenant_phone:
            same_phone = User.objects.filter(phone=tenant_phone, role="Tenant").exclude(id=booking.tenant_id)
            if same_phone.exists():
                self.stdout.write(self.style.WARNING("DUPLICATE PHONE DETECTED:"))
                for u in same_phone:
                    self.stdout.write(f"  - {u.full_name} (id={u.id}) also has phone {u.phone!r}")
                self.stdout.write(
                    "  -> Twilio 409: creating chat for second tenant reuses first tenant's conversation!\n"
                )
            else:
                self.stdout.write("  No other tenants share this phone.\n")

        # Conversations linked to this booking
        by_booking = TwilioConversation.objects.filter(booking=booking)
        self.stdout.write(f"Conversations linked to booking {bid}: {by_booking.count()}")
        for c in by_booking:
            self._show_conversation(c)

        # Conversations found by tenant phone (messages__author)
        if tenant_phone:
            by_phone = TwilioConversation.objects.filter(messages__author=tenant_phone).distinct()
            self.stdout.write(f"\nConversations with messages from {tenant_phone}: {by_phone.count()}")
            for c in by_phone:
                self._show_conversation(c)

        # All unique authors in those conversations
        conv_ids = list(by_booking.values_list("id", flat=True))
        if tenant_phone:
            conv_ids.extend(by_phone.values_list("id", flat=True))
        conv_ids = list(set(conv_ids))
        if conv_ids:
            authors = (
                TwilioMessage.objects.filter(conversation_id__in=conv_ids)
                .values_list("author", flat=True)
                .distinct()
            )
            self.stdout.write(f"\nAll message authors in related conversations: {list(authors)}")

        self.stdout.write("\n=== Done ===\n")

    def _show_conversation(self, conv):
        self.stdout.write(f"  - {conv.conversation_sid} | {conv.friendly_name}")
        self.stdout.write(f"    booking_id={conv.booking_id} apartment_id={conv.apartment_id}")
