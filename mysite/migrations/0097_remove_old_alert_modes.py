"""
Cleanup after the simple Telegram alerts (docs/cleanup_plan.md, part 4.1 G): the fields of the old modes go.

- AIRun.backend: always 'claude_cli', never read.
- AIRun.hold_until: only the timer approval mode set it (removed).
- AIIssue.handled_by / handled_at / handled_prev_state: only the old card's "I'll handle" button set them (removed).
- AIEvent.event_type choices: the two types nothing creates go, NOTIFICATION_DUE (already used) is named.
- The AI Management rows of the two prompts of the removed ClickUp-through-Claude delivery.
"""
from django.db import migrations, models

OLD_PROMPTS = ('ai_agent_clickup_delivery', 'ai_agent_clickup_read')


def delete_old_prompts(apps, schema_editor):
    apps.get_model('mysite', 'AIManagement').objects.filter(prompt_key__in=OLD_PROMPTS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0096_client_spec_v4'),
    ]

    operations = [
        migrations.RemoveField(model_name='airun', name='backend'),
        migrations.RemoveField(model_name='airun', name='hold_until'),
        migrations.RemoveField(model_name='aiissue', name='handled_by'),
        migrations.RemoveField(model_name='aiissue', name='handled_at'),
        migrations.RemoveField(model_name='aiissue', name='handled_prev_state'),
        migrations.AlterField(
            model_name='aievent',
            name='event_type',
            field=models.CharField(choices=[('TENANT_MESSAGE', 'Tenant message'), ('STAFF_MESSAGE', 'Staff message'),
                                            ('FOLLOWUP_DUE', 'Follow-up due'), ('NOTIFICATION_DUE', 'Automatic notification due')],
                                   db_index=True, default='TENANT_MESSAGE', max_length=30),
        ),
        migrations.RunPython(delete_old_prompts, migrations.RunPython.noop),
    ]
