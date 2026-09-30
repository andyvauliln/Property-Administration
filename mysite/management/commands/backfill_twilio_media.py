from datetime import datetime

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from mysite.models import TwilioMessage, TwilioMessageMedia
from mysite.twilio_media import fetch_media_for_existing_message, store_message_media


class Command(BaseCommand):
    help = (
        "Finds stored Twilio messages with an empty body, asks Twilio whether they carried photos/files, "
        "and downloads them to TWILIO_MEDIA_DIR so they show in the chat UI. Never triggers the AI agent."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only list the candidate messages")
        parser.add_argument("--conversation", type=str, help="Only this conversation SID")
        parser.add_argument("--since", type=str, help="Only messages from this date on (YYYY-MM-DD)")
        parser.add_argument("--limit", type=int, default=0, help="At most N messages (0 = all)")
        parser.add_argument("--retry-failed", action="store_true",
                            help="Also re-download media rows whose earlier download failed")

    def handle(self, *args, **options):
        qs = (TwilioMessage.objects
              .filter(Q(body='') | Q(body__regex=r'^\s*$'))
              .filter(message_sid__startswith='IM')
              .filter(media__isnull=True))
        if options["conversation"]:
            qs = qs.filter(conversation_sid=options["conversation"])
        if options["since"]:
            since = timezone.make_aware(datetime.strptime(options["since"], "%Y-%m-%d"))
            qs = qs.filter(message_timestamp__gte=since)
        qs = qs.order_by("message_timestamp", "id")
        if options["limit"]:
            qs = qs[:options["limit"]]
        messages = list(qs)

        failed_rows = []
        if options["retry_failed"]:
            failed_rows = list(TwilioMessageMedia.objects.filter(file_path='').select_related('message'))
            if options["conversation"]:
                failed_rows = [m for m in failed_rows if m.message.conversation_sid == options["conversation"]]

        self.stdout.write(f"{len(messages)} empty message(s) without media rows"
                          + (f", {len(failed_rows)} media row(s) to retry" if options["retry_failed"] else ""))
        if options["dry_run"]:
            for m in messages:
                self.stdout.write(f"  #{m.id} {m.message_sid} {m.conversation_sid} {m.author} {m.message_timestamp:%Y-%m-%d %H:%M}")
            for media in failed_rows:
                self.stdout.write(f"  retry media #{media.id} {media.media_sid}: {media.download_error[:120]}")
            return

        from mysite.views.messaging import get_twilio_client
        client = get_twilio_client()
        service_sids = {}

        def service_sid(conversation_sid):
            if conversation_sid not in service_sids:
                service_sids[conversation_sid] = client.conversations.v1.conversations(conversation_sid).fetch().chat_service_sid
            return service_sids[conversation_sid]

        stats = {"with_media": 0, "files_stored": 0, "no_media": 0, "failed": 0}
        for m in messages:
            try:
                rows = fetch_media_for_existing_message(m, client=client, chat_service_sid=service_sid(m.conversation_sid))
            except Exception as e:
                stats["failed"] += 1
                self.stdout.write(self.style.WARNING(f"  #{m.id} {m.message_sid}: {e}"))
                continue
            if not rows:
                stats["no_media"] += 1
                continue
            stats["with_media"] += 1
            for media in rows:
                if media.is_downloaded:
                    stats["files_stored"] += 1
                else:
                    stats["failed"] += 1
                    self.stdout.write(self.style.WARNING(f"  #{m.id} media {media.media_sid}: {media.download_error[:200]}"))

        for media in failed_rows:
            try:
                item = {"sid": media.media_sid, "content_type": media.content_type,
                        "filename": media.filename, "size": media.size}
                store_message_media(media.message, service_sid(media.message.conversation_sid), [item])
                media.refresh_from_db()
            except Exception as e:
                media.download_error = str(e)[:1000]
            if media.is_downloaded:
                stats["files_stored"] += 1
            else:
                stats["failed"] += 1
                self.stdout.write(self.style.WARNING(f"  retry media #{media.id}: {media.download_error[:200]}"))

        self.stdout.write(self.style.SUCCESS(
            f"Done: {stats['with_media']} message(s) had media, {stats['files_stored']} file(s) stored, "
            f"{stats['no_media']} had no media, {stats['failed']} failed"
        ))
