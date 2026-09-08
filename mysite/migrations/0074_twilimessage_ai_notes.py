from django.db import migrations, models


def move_ai_unit_notes(apps, schema_editor):
    TwilioMessage = apps.get_model('mysite', 'TwilioMessage')
    qs = TwilioMessage.objects.exclude(notes__isnull=True).exclude(notes='')
    for message in qs.iterator():
        has_ai = bool(message.ai_response) or bool(message.ai_response_why)
        if not has_ai:
            continue
        if (message.ai_notes or '').strip():
            continue
        message.ai_notes = message.notes
        message.notes = None
        message.save(update_fields=['ai_notes', 'notes', 'updated_at'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0073_twilio_notes'),
    ]

    operations = [
        migrations.AddField(
            model_name='twiliomessage',
            name='ai_notes',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(move_ai_unit_notes, noop_reverse),
    ]
