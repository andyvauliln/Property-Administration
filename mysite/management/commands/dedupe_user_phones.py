"""
One user per phone number: runs the reviewed plan in data/user_phone_dedup_plan.json
(explained in data/user_phone_dedup_plan.md), then normalizes every stored phone to E.164.

  python manage.py dedupe_user_phones            # dry run: does everything inside a transaction, prints, rolls back
  python manage.py dedupe_user_phones --apply    # writes a JSON backup, then commits

Bookings, apartments, cleanings and chat templates of merged users are moved to the keeper with direct
UPDATEs, so Booking.save() side effects (welcome SMS, contracts, chat relinking) never fire. Test bookings
are hard-deleted with a queryset delete: their payments, cleanings and notifications cascade, the DocuSeal
contract is not touched, and the pre_delete signal still writes an AuditLog row for each deleted object.
The run aborts (nothing committed) if any id is missing, a user to delete still has bookings, or two users
still share a phone at the end.
"""
import json
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone


def _row(obj):
    data = model_to_dict(obj)
    data.pop('password', None)
    return {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v)) for k, v in data.items()}


class Command(BaseCommand):
    help = "Merge/delete users so every phone number belongs to one user (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument('--plan', default=str(Path(settings.BASE_DIR) / 'data' / 'user_phone_dedup_plan.json'))
        parser.add_argument('--apply', action='store_true', help='commit the changes (default: dry run)')
        parser.add_argument('--backup-dir', default=str(Path(settings.BASE_DIR) / 'data'))

    def handle(self, *args, **options):
        plan = json.loads(Path(options['plan']).read_text())
        stamp = timezone.now().strftime('%Y-%m-%d %H:%M')
        backup = {'plan': options['plan'], 'run_at': stamp}
        with transaction.atomic():
            self._run(plan, stamp, backup)
            if options['apply']:
                path = Path(options['backup_dir']) / f"user_phone_dedup_backup_{timezone.now():%Y%m%d_%H%M%S}.json"
                path.write_text(json.dumps(backup, indent=1, default=str))
                self.stdout.write(self.style.SUCCESS(f"APPLIED. Backup of every changed row: {path}"))
            else:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("DRY RUN - everything above was rolled back. Use --apply to commit."))

    def _run(self, plan, stamp, backup):
        from mysite.models import Apartment, Booking, ChatMessageTemplate, Cleaning, User, validate_and_format_phone

        merges = plan.get('merges', [])
        delete_ids = set(plan.get('delete_users', []))
        keepers = {m['keep'] for m in merges}
        merged = {i for m in merges for i in m['merge']}
        all_ids = keepers | merged | delete_ids | set(plan.get('clear_phone', [])) | set(plan.get('delete_cleanings_of_cleaners', []))
        users = User.objects.in_bulk(all_ids)
        missing = sorted(all_ids - set(users))
        if missing:
            raise CommandError(f"users not found: {missing}")
        clash = (keepers & (merged | delete_ids)) | (merged & delete_ids)
        if clash:
            raise CommandError(f"ids both kept and removed: {sorted(clash)}")

        backup['users_before'] = {u.id: _row(u) for u in users.values()}
        backup['managed_apartments_before'] = {
            uid: list(Apartment.objects.filter(managers__id=uid).values_list('id', flat=True)) for uid in merged}

        # 1. test bookings (+ their payments, cleanings, notifications by cascade)
        bookings = Booking.objects.filter(id__in=plan.get('delete_bookings', []))
        missing = sorted(set(plan.get('delete_bookings', [])) - set(bookings.values_list('id', flat=True)))
        if missing:
            raise CommandError(f"bookings not found: {missing}")
        backup['deleted_bookings'] = [
            {'booking': _row(b), 'payments': [_row(p) for p in b.payments.all()],
             'cleanings': [_row(c) for c in b.cleanings.all()]} for b in bookings]
        n_pay = sum(len(x['payments']) for x in backup['deleted_bookings'])
        n_cln = sum(len(x['cleanings']) for x in backup['deleted_bookings'])
        bookings.delete()
        self.stdout.write(f"1. deleted {len(backup['deleted_bookings'])} test bookings "
                          f"(with {n_pay} payments, {n_cln} cleanings)")

        # 2. cleanings of test cleaners
        cleanings = Cleaning.objects.filter(cleaner_id__in=plan.get('delete_cleanings_of_cleaners', []))
        backup['deleted_cleanings'] = [_row(c) for c in cleanings]
        cleanings.delete()
        self.stdout.write(f"2. deleted {len(backup['deleted_cleanings'])} cleanings of test cleaner(s) "
                          f"{plan.get('delete_cleanings_of_cleaners', [])}")

        # 3. merges
        backup['moved_bookings'] = []
        self.stdout.write("3. merges:")
        for m in merges:
            keeper = users[m['keep']]
            others = [users[i] for i in m['merge']]
            other_ids = [o.id for o in others]
            moved = list(Booking.objects.filter(tenant_id__in=other_ids).values_list('id', 'tenant_id'))
            backup['moved_bookings'] += [{'booking': b, 'from_user': t, 'to_user': keeper.id} for b, t in moved]
            Booking.objects.filter(tenant_id__in=other_ids).update(tenant=keeper)
            Apartment.objects.filter(owner_id__in=other_ids).update(owner=keeper)
            for apt in Apartment.objects.filter(managers__id__in=other_ids).distinct():
                apt.managers.add(keeper)
            Cleaning.objects.filter(cleaner_id__in=other_ids).update(cleaner=keeper)
            ChatMessageTemplate.objects.filter(created_by_user_id__in=other_ids).update(created_by_user=keeper)

            lines = [f"[{stamp} merged U{o.id}: {o.full_name} | {o.email} | {o.phone} | {o.role}]" for o in others]
            fields = {'notes': '\n'.join(filter(None, [keeper.notes] + lines))}
            if m.get('rename'):
                fields['full_name'] = m['rename']
            if not keeper.telegram_chat_id:
                chat_id = next((o.telegram_chat_id for o in others if o.telegram_chat_id), None)
                if chat_id:
                    fields['telegram_chat_id'] = chat_id
            User.objects.filter(id=keeper.id).update(**fields)
            User.objects.filter(id__in=other_ids).delete()
            rename = f"  (renamed to '{m['rename']}')" if m.get('rename') else ''
            self.stdout.write(f"   keep U{keeper.id} {keeper.full_name}{rename} <- "
                              + ', '.join(f"U{o.id} {o.full_name}" for o in others)
                              + f"; bookings moved: {[b for b, _ in moved] or 'none'}")

        # 4. junk users (must have no bookings left)
        still_booked = sorted(set(Booking.objects.filter(tenant_id__in=delete_ids).values_list('tenant_id', flat=True)))
        if still_booked:
            raise CommandError(f"users to delete still have bookings: {still_booked}")
        User.objects.filter(id__in=delete_ids).delete()
        self.stdout.write(f"4. deleted {len(delete_ids)} junk users: {sorted(delete_ids)}")

        # 5. staff phones on other people's records
        User.objects.filter(id__in=plan.get('clear_phone', [])).update(phone=None)
        self.stdout.write(f"5. cleared the wrong phone on {plan.get('clear_phone', [])}")

        # 6. normalize every stored phone the way User.save() does
        fixed, invalid = [], []
        for uid, phone in User.objects.exclude(phone__isnull=True).exclude(phone='').values_list('id', 'phone'):
            good = validate_and_format_phone(phone)
            if good is None:
                invalid.append((uid, phone))
            elif good != phone:
                fixed.append({'user': uid, 'from': phone, 'to': good})
                User.objects.filter(id=uid).update(phone=good)
        User.objects.filter(phone='').update(phone=None)
        backup['normalized_phones'] = fixed
        self.stdout.write(f"6. normalized {len(fixed)} phones to +E.164; left {len(invalid)} invalid as they are: {invalid}")

        # 7. verify
        by_phone = defaultdict(list)
        for uid, phone in User.objects.exclude(phone__isnull=True).values_list('id', 'phone'):
            by_phone[phone].append(uid)
        dups = {p: ids for p, ids in by_phone.items() if len(ids) > 1}
        if dups:
            raise CommandError(f"still shared phones, nothing committed: {dups}")
        self.stdout.write(self.style.SUCCESS(
            f"7. check passed: every phone belongs to exactly one user ({len(by_phone)} phones, "
            f"{User.objects.count()} users left)"))
