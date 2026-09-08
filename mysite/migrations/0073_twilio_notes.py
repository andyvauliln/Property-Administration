from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0072_ai_extract_standing_vs_onetime_rules'),
    ]

    operations = [
        migrations.AddField(
            model_name='twilioconversation',
            name='notes',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='twiliomessage',
            name='notes',
            field=models.TextField(blank=True, null=True),
        ),
    ]
