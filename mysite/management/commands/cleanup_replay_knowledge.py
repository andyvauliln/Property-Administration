import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from mysite.replay_kb_cleanup import cleanup_knowledge_bases, collect_preserved_kb_from_state

DEFAULT_JSON_PATH = "reports/conversation.json"


class Command(BaseCommand):
    help = (
        "Reset apartment and replay-created global knowledge bases for a fresh replay. "
        "Keeps manual replay saves from conversation.json (saved_apartment/saved_global/to_save) "
        "and manual admin global KB entries."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=DEFAULT_JSON_PATH,
            help=f"Replay JSON state file used to detect manual saves (default: {DEFAULT_JSON_PATH})",
        )
        parser.add_argument(
            "--no-delete",
            action="store_true",
            help="Dry run: show what would be cleared without updating DB.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if kb_cleanup_done is already true in the JSON state.",
        )

    def handle(self, *args, **options):
        state_path = self._resolve_path(options["file"])
        state = self._load_state(state_path)

        if state.get("kb_cleanup_done") and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    "KB cleanup already done for this replay state file. Use --force to run again."
                )
            )
            return

        preserved_apartments, preserved_global = collect_preserved_kb_from_state(state)
        self.stdout.write(f"Preserved apartment IDs from JSON: {sorted(preserved_apartments.keys())}")
        self.stdout.write(f"Preserved global entries from JSON: {len(preserved_global)}")

        stats = cleanup_knowledge_bases(state, dry_run=options["no_delete"])
        self._print_stats(stats)

        if options["no_delete"]:
            self.stdout.write(self.style.WARNING("Dry run only (--no-delete). No DB changes made."))
            return

        state["kb_cleanup_done"] = True
        state["kb_cleanup"] = {
            "mode": stats["mode"],
            "preserved_apartment_ids": stats["preserved_apartment_ids"],
            "apartments_cleared": stats["apartments_cleared"],
            "apartments_restored": stats["apartments_restored"],
            "global_kept": stats["global_kept"],
            "global_deleted": stats["global_deleted"],
            "global_created": stats["global_created"],
            "ran_at": timezone.now().isoformat(),
        }
        state["updated_at"] = timezone.now().isoformat()
        self._save_state(state_path, state)
        self.stdout.write(self.style.SUCCESS(f"KB cleanup complete. State updated: {state_path}"))

    def _resolve_path(self, raw_path):
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        return path

    def _load_state(self, state_path):
        if not state_path.exists():
            return {"conversations": []}
        with state_path.open("r", encoding="utf-8") as state_file:
            return json.load(state_file)

    def _save_state(self, state_path, state):
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as tmp_file:
            json.dump(state, tmp_file, ensure_ascii=False, indent=2)
            tmp_file.write("\n")
        os.replace(tmp_path, state_path)

    def _print_stats(self, stats):
        self.stdout.write(f"Mode: {stats['mode']}")
        self.stdout.write(f"Apartments to clear: {stats['apartments_cleared']}")
        for item in stats["apartment_restore"]:
            self.stdout.write(f"  keep apartment {item['name']} (id={item['id']})")
        self.stdout.write(f"Global entries to keep: {stats['global_kept']}")
        for item in stats["global_keep"]:
            self.stdout.write(f"  keep global #{item['id']} {item['name']} ({item['reason']})")
        self.stdout.write(f"Global entries to delete: {stats['global_deleted']}")
        for item in stats["global_delete"]:
            self.stdout.write(f"  delete global #{item['id']} {item['name']}")
