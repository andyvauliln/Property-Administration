from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0080_twilimessage_rule_added_flags'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='twiliomessage',
            name='ai_kb_rule_added',
        ),
        migrations.RemoveField(
            model_name='twiliomessage',
            name='ai_answer_rule_added',
        ),
    ]
