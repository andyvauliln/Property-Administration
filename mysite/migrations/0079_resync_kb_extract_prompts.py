from django.db import migrations


def resync_kb_extract_prompts(apps, schema_editor):
    from mysite.views.messaging import sync_kb_extract_prompts_to_db

    sync_kb_extract_prompts_to_db()


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0078_alter_booking_status'),
    ]

    operations = [
        migrations.RunPython(resync_kb_extract_prompts, migrations.RunPython.noop),
    ]
