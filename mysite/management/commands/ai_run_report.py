"""
List AI agent runs from logs/ai_runs/index.csv (works for worker runs and replays).

  python manage.py ai_run_report --last 20
  python manage.py ai_run_report --date 2026-09-21 --errors-only
  python manage.py ai_run_report --conversation CHxxxx
"""
import csv

from django.core.management.base import BaseCommand

from mysite.ai_agent import config


class Command(BaseCommand):
    help = "Summary of AI agent runs: result, tokens, cost, report folder"

    def add_arguments(self, parser):
        parser.add_argument('--last', type=int, default=20)
        parser.add_argument('--date', help='YYYY-MM-DD')
        parser.add_argument('--conversation', help='Conversation SID')
        parser.add_argument('--errors-only', action='store_true')

    def handle(self, *args, **options):
        index_path = config.RUNS_DIR / 'index.csv'
        if not index_path.exists():
            self.stdout.write("No runs yet.")
            return
        with index_path.open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))

        if options['date']:
            rows = [r for r in rows if (r.get('time') or '').startswith(options['date'])]
        if options['conversation']:
            rows = [r for r in rows if r.get('conversation_sid') == options['conversation']]
        if options['errors_only']:
            rows = [r for r in rows if r.get('error')]
        shown = rows[-options['last']:]

        for row in shown:
            self.stdout.write(
                f"{row.get('time')}  {row.get('apartment'):<14} {row.get('mode'):<5} {row.get('result'):<9} "
                f"actions={row.get('actions')} tools={row.get('tool_calls')} turns={row.get('turns')} "
                f"in={row.get('input_tokens')} out={row.get('output_tokens')} "
                f"cache={row.get('cache_read_tokens')}/{row.get('cache_write_tokens')} "
                f"${float(row.get('cost_usd') or 0):.4f} {row.get('duration_ms')}ms"
            )
            if row.get('error'):
                self.stdout.write(self.style.ERROR(f"    error: {row['error']}"))
            self.stdout.write(f"    {row.get('report_dir')}/report.md")

        def total(column):
            return sum(float(r.get(column) or 0) for r in rows)

        self.stdout.write("")
        self.stdout.write(
            f"{len(rows)} runs matched ({len(shown)} shown): "
            f"input {int(total('input_tokens'))}, output {int(total('output_tokens'))}, "
            f"cache read {int(total('cache_read_tokens'))}, cache write {int(total('cache_write_tokens'))}, "
            f"cost ${total('cost_usd'):.4f}"
        )
