from django.db import migrations, models

LESSONS_KEY = 'ai_agent_answer_lessons'


def lessons_to_prompt(apps, schema_editor):
    """Answer lessons move from AIKnowledge rows to lines of the 'ai_agent_answer_lessons' prompt."""
    AIKnowledge = apps.get_model('mysite', 'AIKnowledge')
    AIManagement = apps.get_model('mysite', 'AIManagement')

    rows = AIKnowledge.objects.filter(knowledge_type='lesson')
    active = list(rows.filter(status='active').select_related('apartment').order_by('created_at', 'id'))
    if active:
        entry = AIManagement.objects.filter(prompt_key=LESSONS_KEY).first()
        lines = [line for line in ((entry.content if entry else '') or '').splitlines() if line.strip()]
        for row in active:
            if row.apartment_id:
                tag = f"apartment #{row.apartment_id} {getattr(row.apartment, 'name', '') or ''}".strip()
            else:
                tag = 'company'
            lines.append(f"- [{tag}] {row.key}: {' '.join((row.value or '').split())}")
        AIManagement.objects.update_or_create(
            prompt_key=LESSONS_KEY,
            defaults={'name': 'Agent - answer lessons', 'entry_type': 'prompt', 'content': "\n".join(lines)},
        )
    rows.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0093_twiliomessagemedia'),
    ]

    operations = [
        migrations.RunPython(lessons_to_prompt, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='aiknowledge',
            name='knowledge_type',
            field=models.CharField(choices=[('fact', 'Fact'), ('policy', 'Policy')], default='fact', max_length=10),
        ),
    ]
