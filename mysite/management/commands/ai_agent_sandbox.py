"""
Sandbox chat for testing the AI agent from the CRM chat page. It exists only in the database:
no Twilio conversation, no real tenant, no SMS. Lives on the test apartment (Test_Apart2).

  python manage.py ai_agent_sandbox                      create / show the sandbox
  python manage.py ai_agent_sandbox --reset              wipe its messages, issues, timers, notes, knowledge
  python manage.py ai_agent_sandbox --checkin-in-days 10 move check-in (access codes are hidden > 24h before it)
  python manage.py ai_agent_sandbox --due-now            make its pending follow-ups due now (timer tests)
  python manage.py ai_agent_sandbox --delete             remove the sandbox completely
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

SANDBOX_SID = "CHSANDBOXAIAGENT00000000000000001"
SANDBOX_TENANT_EMAIL = "ai-sandbox-tenant@example.com"
SANDBOX_TENANT_PHONE = "+15005550006"   # Twilio's magic test number: can never reach a real person
SANDBOX_NOTE = "AI SANDBOX - test data, safe to delete (python manage.py ai_agent_sandbox --delete)"
SANDBOX_KB = """WiFi network: Sandbox_5G, password: sunny2026
Gate code: 4821
Door lockbox code: 7719 (lockbox is on the left side of the door)
Parking: spot 12 in the garage, entrance from the back street
Trash room: 1st floor next to the mail boxes, pickup Tuesday and Friday
Check-in is at 4 PM, check-out at 11 AM
Washer and dryer are in the unit, in the hallway closet
No smoking inside the unit"""


class Command(BaseCommand):
    help = "Create / reset the database-only sandbox chat for AI agent tests"

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true')
        parser.add_argument('--delete', action='store_true')
        parser.add_argument('--due-now', action='store_true')
        parser.add_argument('--checkin-in-days', type=int)
        parser.add_argument('--apartment', default='Test_Apart2', help='Name of the TEST apartment to use')

    def _wipe(self, keep_runs=True):
        from mysite.models import AICaseNote, AIEvent, AIFollowUp, AIIssue, AIKnowledge, AIRun, TwilioMessage
        counts = {
            'messages': TwilioMessage.objects.filter(conversation_sid=SANDBOX_SID).delete()[0],
            'events': AIEvent.objects.filter(conversation_sid=SANDBOX_SID).delete()[0],
            'followups': AIFollowUp.objects.filter(conversation_sid=SANDBOX_SID).delete()[0],
            'notes': AICaseNote.objects.filter(conversation_sid=SANDBOX_SID).delete()[0],
            'issues': AIIssue.objects.filter(conversation_sid=SANDBOX_SID).delete()[0],
            'knowledge': AIKnowledge.objects.filter(conversation_sid=SANDBOX_SID).delete()[0],
        }
        if not keep_runs:
            counts['runs'] = AIRun.objects.filter(conversation_sid=SANDBOX_SID).delete()[0]
        return counts

    def handle(self, *args, **options):
        from mysite.models import AIFollowUp, Apartment, Booking, TwilioConversation, User

        apartment = Apartment.objects.filter(name=options['apartment']).first()
        if not apartment:
            raise CommandError(f"Apartment {options['apartment']} not found")
        if 'test' not in apartment.name.lower():
            raise CommandError("The sandbox may only live on a test apartment (name must contain 'test')")

        if options['delete']:
            counts = self._wipe(keep_runs=False)
            TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID).delete()
            Booking.objects.filter(notes=SANDBOX_NOTE).delete()
            User.objects.filter(email=SANDBOX_TENANT_EMAIL).delete()
            self.stdout.write(f"Sandbox deleted: {counts}")
            return

        # bulk_create on purpose: no model save() side effects (contracts, notifications, SMS)
        tenant = User.objects.filter(email=SANDBOX_TENANT_EMAIL).first()
        if not tenant:
            User.objects.bulk_create([User(
                email=SANDBOX_TENANT_EMAIL, full_name="Sandbox Tenant", role="Tenant", phone=SANDBOX_TENANT_PHONE, is_active=False,
            )])
            tenant = User.objects.get(email=SANDBOX_TENANT_EMAIL)
        booking = Booking.objects.filter(notes=SANDBOX_NOTE).first()
        if not booking:
            Booking.objects.bulk_create([Booking(
                apartment=apartment, tenant=tenant, status="Confirmed", notes=SANDBOX_NOTE,
                start_date=date.today() - timedelta(days=2), end_date=date.today() + timedelta(days=300),
            )])
            booking = Booking.objects.get(notes=SANDBOX_NOTE)
        conversation = TwilioConversation.objects.filter(conversation_sid=SANDBOX_SID).first()
        if not conversation:
            TwilioConversation.objects.bulk_create([TwilioConversation(
                conversation_sid=SANDBOX_SID, friendly_name="AI SANDBOX (database only, no SMS)",
                apartment=apartment, booking=booking, notes=SANDBOX_NOTE,
            )])
        if not (apartment.knowledge_base or '').strip():
            Apartment.objects.filter(id=apartment.id).update(knowledge_base=SANDBOX_KB)

        if options['reset']:
            self.stdout.write(f"Sandbox wiped: {self._wipe()}")
        if options['checkin_in_days'] is not None:
            start = date.today() + timedelta(days=options['checkin_in_days'])
            Booking.objects.filter(id=booking.id).update(start_date=start, end_date=start + timedelta(days=300))
            self.stdout.write(f"Check-in moved to {start}")
        if options['due_now']:
            moved = AIFollowUp.objects.filter(
                conversation_sid=SANDBOX_SID, status=AIFollowUp.STATUS_PENDING,
            ).update(due_at=timezone.now() - timedelta(seconds=5))
            self.stdout.write(f"{moved} pending follow-up(s) are due now - the worker picks them up within 30 seconds")

        booking.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"Sandbox chat: /chat/{SANDBOX_SID}/"))
        self.stdout.write(f"  apartment {apartment.name} | booking #{booking.id} {booking.start_date} -> {booking.end_date} | tenant {tenant.full_name}")
        self.stdout.write("  Use 'Send as client' / 'Send as manager' with 'Send to group chat' UNTICKED.")
