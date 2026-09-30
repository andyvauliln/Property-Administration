import difflib

from django.core.management.base import BaseCommand, CommandError

from mysite.ai_agent import prompt_library as lib


class Command(BaseCommand):
    help = (
        "Makes sure every AI prompt exists in AIManagement (created from the built-in default when missing; "
        "existing prompts are never overwritten) and refreshes their name/description from the prompt registry."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only show what is missing / edited")
        parser.add_argument("--diff", action="store_true", help="Show how edited prompts differ from the default")
        parser.add_argument("--reset", metavar="KEY", help="Overwrite one prompt with its built-in default")
        parser.add_argument("--refresh-defaults", action="store_true",
                            help="Also update text prompts nobody has edited since they were seeded to the current "
                                 "built-in default (after a code update changed a default)")

    def handle(self, *args, **options):
        from mysite.models import AIManagement

        if options["reset"]:
            key = options["reset"]
            if key not in lib.BY_KEY:
                raise CommandError(f"unknown prompt key {key}; known: {', '.join(lib.BY_KEY)}")
            if options["dry_run"]:
                self.stdout.write(f"would reset {key}")
                return
            lib.seed(key, force=True)
            self.stdout.write(self.style.SUCCESS(f"{key} reset to the built-in default"))
            return

        rows = {e.prompt_key: e for e in AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY))}
        for s in lib.SPECS:
            entry = rows.get(s.key)
            if entry is None:
                state = "MISSING -> will be created" if options["dry_run"] else "created"
            elif lib.is_edited(s.key, entry):
                state = "edited (kept)"
            else:
                state = "same as default"
            self.stdout.write(f"  {s.key:32} {state}")
            if options["diff"] and entry is not None and lib.is_edited(s.key, entry):
                diff = difflib.unified_diff(s.default_text().splitlines(), (entry.content or '').strip().splitlines(),
                                            'default', 'db', lineterm='', n=1)
                for line in diff:
                    self.stdout.write(f"      {line}")
        if options["dry_run"]:
            return
        created = lib.seed_missing()
        refreshed = []
        if options["refresh_defaults"]:
            for s in lib.SPECS:
                entry = AIManagement.objects.filter(prompt_key=s.key).first()
                untouched = entry and abs((entry.updated_at - entry.created_at).total_seconds()) < 5
                if s.kind == lib.KIND_TEXT and untouched and lib.is_edited(s.key, entry):
                    entry.content = s.default_text()
                    # keep updated_at == created_at so the row still counts as "never edited by a person"
                    AIManagement.objects.filter(id=entry.id).update(content=entry.content)
                    refreshed.append(s.key)
        self.stdout.write(self.style.SUCCESS(
            f"Done: {len(created)} prompt(s) created"
            + (f", {len(refreshed)} refreshed to the new default ({', '.join(refreshed)})" if refreshed else "")
            + f", {len(lib.SPECS)} in the registry"))
