"""
Sandbox test runner for the Telegram alerts (claude_code_integration_doc/simple_telegram_alerts.md, part 11).
Runs the REAL agent on the cases of testbed/sandbox_cases.yaml - the chapters of ONE story: one stay of one tenant in
the sandbox apartment "Sandbox Test", with real CRM rows (booking, payments, parking, knowledge page) written at the
start and carried from chapter to chapter - or on the messages of a real chat. Every alert is posted with a test
header and 🧪 TEST NOTES, checked, and the verdict posted. Nothing can reach a real tenant: see sandbox_test/world.py.

  python manage.py ai_agent_sandbox_test --list                 all cases and the last result of each (reads files only)
  python manage.py ai_agent_sandbox_test --cases A1             one case
  python manage.py ai_agent_sandbox_test --cases A1,A2,C3       several, one by one
  python manage.py ai_agent_sandbox_test --group A              all A cases
  python manage.py ai_agent_sandbox_test --all                  the whole catalog
  python manage.py ai_agent_sandbox_test --all --from A5        the story from chapter A5 (from its saved state)
  python manage.py ai_agent_sandbox_test --conversation CHxxxx [--last 20] [--since 2026-10-01]   a real chat

  --auto        go on to the next case without waiting; presses the buttons listed under `presses:` itself
  --real-time   wait the real delays instead of jumping the clock (reminders after 2 h / next day)
  --offline     post nothing to Telegram: alerts and verdicts are printed here only
  --no-judge    code checks only: no Claude judge, test notes taken from the case file
  --clean-chat  first delete from the Telegram test chat what earlier test runs posted there
  --serve       stay alive when the list is done or stopped, and take commands in the test group:
                continue · restart (cleans the chat, the story from its start) · run A5 / run A,B (from the saved
                state before that chapter) · test-conversation CHxxxx [last 20]

While it waits (not --auto) type: next · rerun · rerun with: <text> · change test: <what> · accept · skip · stop.

Where the alerts go: AI_AGENT_SANDBOX_CHAT_ID (the "[PM] AI TEST" group), else the team's AI group. Buttons and replies
only work with an own sandbox bot (AI_AGENT_SANDBOX_BOT_TOKEN): the live worker owns the main bot's updates. Without
it the run is view-only (buttons shown as text; --auto still presses the `presses:` in this process).
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Run the alert test cases (or a real chat) through the real AI agent in the sandbox"

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true')
        parser.add_argument('--cases', help='Case ids, comma separated: A1,A2,C3')
        parser.add_argument('--group', help='All cases of a group: A (or A,B)')
        parser.add_argument('--all', action='store_true')
        parser.add_argument('--conversation', help='Conversation SID of a real chat to replay')
        parser.add_argument('--last', type=int, help='With --conversation: only the last N messages')
        parser.add_argument('--since', dest='since', help='With --conversation: only messages from this date (YYYY-MM-DD)')
        parser.add_argument('--auto', action='store_true')
        parser.add_argument('--real-time', action='store_true')
        parser.add_argument('--offline', action='store_true')
        parser.add_argument('--no-judge', action='store_true')
        parser.add_argument('--from', dest='start_at', help='Start at this case of the selection (e.g. --group A --from A2)')
        parser.add_argument('--serve', action='store_true', help='Stay alive after the list: wait in the test group for continue / restart / run ... / test-conversation ...')
        parser.add_argument('--clean-chat', action='store_true', help='First delete what earlier test runs posted in the Telegram test chat')

    def handle(self, *args, **options):
        from mysite.ai_agent.sandbox_test import catalog, story
        from mysite.ai_agent.sandbox_test.runner import Runner, list_cases

        def out(text=''):
            self.stdout.write(str(text))
            self.stdout.flush()

        try:
            if options['list']:
                return list_cases(out)
            cases = None
            if not options['conversation']:
                if not (options['cases'] or options['group'] or options['all']):
                    raise CommandError("Give --list, --cases, --group, --all or --conversation")
                cases = catalog.select(catalog.load_cases(), options['cases'], options['group'], options['all'])
                all_cases = cases
                if options['start_at'] and options['start_at'] not in [case['id'] for case in cases]:
                    raise CommandError(f"--from {options['start_at']}: not one of the selected cases")
                if not cases:
                    raise CommandError("No such cases (see --list)")
            runner = Runner(auto=options['auto'], real_time=options['real_time'],
                            offline=options['offline'], use_claude=not options['no_judge'], out=out)
            # A stopped run (kill, pm2 stop) must still clean up after itself: nothing may be left for the live worker
            import signal

            def stop(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGHUP, stop)   # the terminal / session that started it went away
            runner.start(clean_chat=options['clean_chat'])
            try:
                if cases is None:
                    runner.serve([], "no list", stay=options['serve'],
                                 conversation=(options['conversation'], options['last'], options['since']))
                else:
                    label = "the whole catalog" if options['all'] else f"group {options['group'].upper()}" if options['group'] \
                        else f"case{'s' if len(cases) != 1 else ''} {', '.join(c['id'] for c in cases)}"
                    if options['start_at']:
                        # --from only says where to begin: "restart" still starts with the first case of the list
                        runner.position = [c['id'] for c in all_cases].index(options['start_at'])
                        runner.serve(all_cases, label, stay=options['serve'], begin=runner.position)
                    else:
                        runner.serve(cases, label, stay=options['serve'])
            except KeyboardInterrupt:
                out("\nStopped (Ctrl+C).")
            finally:
                runner.finish()
        except catalog.CatalogError as e:
            raise CommandError(str(e))
        except story.StoryError as e:
            raise CommandError(str(e))
