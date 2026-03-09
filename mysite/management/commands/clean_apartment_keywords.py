from django.core.management.base import BaseCommand
from django.db.models import Q
from mysite.models import Apartment, User


def split_keywords(keywords_str):
    """Split comma-separated keywords, strip whitespace, filter empty."""
    if not keywords_str or not keywords_str.strip():
        return []
    return [k.strip() for k in keywords_str.split(',') if k.strip()]


def should_remove_keyword(keyword, apartment):
    """
    Return True if keyword should be removed.
    Remove if: matches apartment name, owner name, owner email, or tenant full_name.
    """
    kw_lower = keyword.lower()

    # 1. Apartment name
    if apartment.name and kw_lower == apartment.name.lower():
        return True, 'apartment_name'

    # 2. Owner name or email
    if apartment.owner:
        if apartment.owner.full_name and kw_lower == apartment.owner.full_name.lower():
            return True, 'owner_name'
        if apartment.owner.email and kw_lower == apartment.owner.email.lower():
            return True, 'owner_email'

    # 3. Tenant with this name exists
    if User.objects.filter(role='Tenant', full_name__iexact=keyword).exists():
        return True, 'tenant_name'

    return False, None


class Command(BaseCommand):
    help = 'Clean apartment keywords: remove apartment name, owner name/email, and tenant names'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without saving',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be saved\n'))

        apartments = Apartment.objects.all().select_related('owner')
        total_removed = 0
        apartments_changed = 0

        for apt in apartments:
            keywords_list = split_keywords(apt.keywords)
            if not keywords_list:
                continue

            kept = []
            removed = []

            for kw in keywords_list:
                should_remove, reason = should_remove_keyword(kw, apt)
                if should_remove:
                    removed.append((kw, reason))
                else:
                    kept.append(kw)

            if not removed:
                continue

            apartments_changed += 1
            total_removed += len(removed)

            self.stdout.write(f'\n--- Apartment: {apt.name} (id={apt.id}) ---')
            self.stdout.write(f'  Before: {apt.keywords or "(empty)"}')
            for kw, reason in removed:
                self.stdout.write(f'  REMOVE "{kw}" (reason: {reason})')
            new_keywords = ', '.join(kept) if kept else ''
            self.stdout.write(f'  After:  {new_keywords or "(empty)"}')
            if dry_run:
                self.stdout.write('  [dry-run: not saved]')
            else:
                apt.keywords = new_keywords or None
                apt.save(updated_by=None)
                self.stdout.write(self.style.SUCCESS('  [saved]'))

        self.stdout.write('\n' + '=' * 50)
        self.stdout.write(f'Summary: {apartments_changed} apartment(s) changed, {total_removed} keyword(s) removed')
        if dry_run:
            self.stdout.write(self.style.WARNING('Run without --dry-run to apply changes'))
