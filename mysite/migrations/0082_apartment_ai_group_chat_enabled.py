from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0081_remove_twilimessage_rule_added_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='apartment',
            name='ai_group_chat_enabled',
            field=models.BooleanField(default=False),
        ),
    ]
