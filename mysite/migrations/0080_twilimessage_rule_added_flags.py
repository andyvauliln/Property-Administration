from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0079_resync_kb_extract_prompts'),
    ]

    operations = [
        migrations.AddField(
            model_name='twiliomessage',
            name='ai_kb_rule_added',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='twiliomessage',
            name='ai_answer_rule_added',
            field=models.BooleanField(default=False),
        ),
    ]
