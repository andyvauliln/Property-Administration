"""
Replay stored tenant messages through the Claude agent without sending anything.
Writes a full run report per message and compares with the answer stored by the current AI.

  python manage.py ai_agent_replay --message-id 12345
  python manage.py ai_agent_replay --conversation CHxxxx --last 5
  python manage.py ai_agent_replay --recent 10
"""
from django.core.management.base import BaseCommand, CommandError

from mysite.ai_agent import actions as agent_actions
from mysite.ai_agent import inputs, run_report, service


class Command(BaseCommand):
    help = "Replay tenant messages through the Claude agent in test mode (never sends SMS)"

    def add_arguments(self, parser):
        parser.add_argument('--message-id', type=int, action='append', help='TwilioMessage.id (repeatable)')
        parser.add_argument('--conversation', help='Conversation SID: replay its last tenant messages')
        parser.add_argument('--last', type=int, default=3, help='How many messages with --conversation')
        parser.add_argument('--recent', type=int, help='Replay the N most recent tenant messages that have a stored AI result')

    def _messages(self, options):
        from mysite.models import TwilioMessage

        linked = TwilioMessage.objects.filter(
            direction='inbound',
            conversation__apartment__isnull=False,
            conversation__booking__isnull=False,
        ).select_related('conversation')
        if options['message_id']:
            return list(TwilioMessage.objects.filter(id__in=options['message_id']).select_related('conversation'))
        if options['conversation']:
            found = list(linked.filter(conversation_sid=options['conversation']).order_by('-id')[:options['last']])
            return list(reversed(found))
        if options['recent']:
            found = list(linked.exclude(ai_response_why__isnull=True).order_by('-id')[:options['recent']])
            return list(reversed(found))
        raise CommandError("Give --message-id, --conversation or --recent")

    def handle(self, *args, **options):
        from mysite.models import AIEvent, AIRun, Apartment, Booking

        messages = self._messages(options)
        if not messages:
            raise CommandError("No messages found")

        total_cost = 0.0
        for message in messages:
            conversation = message.conversation
            if not (conversation and conversation.apartment_id and conversation.booking_id):
                self.stdout.write(self.style.WARNING(f"#{message.id}: conversation not linked to apartment + booking, skipped"))
                continue
            if inputs.classify_sender(message)[0] != inputs.ROLE_TENANT:
                self.stdout.write(self.style.WARNING(f"#{message.id}: not a tenant message, skipped"))
                continue
            apartment = Apartment.objects.prefetch_related('managers').select_related('owner').get(id=conversation.apartment_id)
            booking = Booking.objects.select_related('tenant').get(id=conversation.booking_id)

            outcome = service.run_agent(
                AIEvent.TYPE_TENANT_MESSAGE, message.conversation_sid, apartment, booking, [message], AIRun.MODE_TEST,
                now=message.message_timestamp,
            )
            parsed, run, meta = outcome['parsed'], outcome['run'], outcome['meta']
            meta['replay'] = True
            actions = agent_actions.execute_actions(parsed, agent_actions.ActionContext(
                AIRun.MODE_TEST, meta, outcome['new_messages_text'], message.conversation_sid,
                persist=False, notify=False,
            )) if parsed else []
            delivery = {'sent_to_chat': False, 'note': 'replay - never sent'}
            summary = run_report.write_report(
                outcome['run_dir'], meta, outcome['user_input'], run, parsed, actions, delivery,
                new_messages_text=outcome['new_messages_text'],
            )
            total_cost += float(summary['cost_usd'] or 0)

            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(f"#{message.id} {meta['apartment']} - {meta['tenant']}"))
            self.stdout.write(f"  tenant:    {inputs._strip_ui_markers(message.body)[:300]}")
            self.stdout.write(f"  stored AI: {(message.ai_response or 'NO_ANSWER')[:300]}")
            if parsed:
                self.stdout.write(f"  claude:    {(parsed['answer'] or 'NO_ANSWER')[:300]}")
                self.stdout.write(f"  why:       {(parsed['why'] or '')[:300]}")
                self.stdout.write(f"  actions:   {[a['action'].get('type') for a in actions if isinstance(a.get('action'), dict)]}")
            else:
                self.stdout.write(self.style.ERROR(f"  ERROR:     {run.get('error')}"))
            self.stdout.write(
                f"  tokens:    in {summary['input_tokens']} / out {summary['output_tokens']} / "
                f"cache read {summary['cache_read_tokens']} / cache write {summary['cache_write_tokens']}  "
                f"cost ${summary['cost_usd']:.4f}  turns {summary['num_turns']}  "
                f"tools {len(summary['tool_calls'])}  {summary['duration_ms']} ms"
            )
            self.stdout.write(f"  report:    {outcome['run_dir']}/report.md")

        self.stdout.write("")
        self.stdout.write(f"Total cost: ${total_cost:.4f}")
