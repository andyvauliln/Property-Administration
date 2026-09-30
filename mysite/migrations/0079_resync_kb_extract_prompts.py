from django.db import migrations


# Originally re-synced the old KB extract prompts from code. The extraction pipeline and its prompts were removed
# on 2026-09-28 (the Claude agent updates the knowledge-base documents); this migration is kept as a no-op so the
# migration history stays intact.


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0078_alter_booking_status'),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
