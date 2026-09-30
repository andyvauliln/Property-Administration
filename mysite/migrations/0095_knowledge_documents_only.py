from django.db import migrations

# Knowledge lives only in the knowledge-base documents (Apartment.knowledge_base + the 'global_knowledge_base'
# row). The AIKnowledge fact rows are moved into those documents and the table is dropped. The legacy
# OpenRouter / extraction prompts are deleted after the staff-taught rules in them are moved to the agent prompts.

LEGACY_KEYS = [
    'ai_extract_check', 'ai_extract_merge', 'ai_extract_global_check', 'ai_extract_global_merge',
    'ai_kb_scope_classify', 'ai_kb_explain_skip', 'ai_answer_system', 'ai_answer_user',
    'ai_backend', 'ai_conversation_model',
]

# Rules staff added over time to the old prompts (found on prod 2026-09-28): kept, in the agent's prompts
OLD_KB_RULES = {
    "- Reply NO — never save tenant-specific extension offers, temporary pricing, or month-by-month rent quotes to the property knowledge base.":
        "- [apartment KB] Never save tenant-specific extension offers, temporary pricing, or month-by-month rent quotes to the property knowledge base.",
    "- Reply NO — never save one-time billing overages, payment requests, or transaction-specific owner instructions to the KB.":
        "- [apartment KB] Never save one-time billing overages, payment requests, or transaction-specific owner instructions to the knowledge base.",
    "- Reply NO for one-time, transient unit-status updates (e.g., temporary key placement or planned appliance moves) that are not lasting property facts.":
        "- [apartment KB] Never save one-time, transient unit-status updates (e.g., temporary key placement or planned appliance moves) that are not lasting property facts.",
}
OLD_ANSWER_RULES = {
    "- If a tenant says they will respond later the same day, send a brief follow-up asking for an update.":
        "- [company] tenant_will_reply_later: If a tenant says they will respond later the same day, send a brief follow-up asking for an update.",
}


def _append_lines(AIManagement, key, name, lines):
    entry = AIManagement.objects.filter(prompt_key=key).first()
    current = [l for l in ((entry.content if entry else '') or '').splitlines() if l.strip()]
    new = [l for l in lines if l not in current]
    if not new:
        return
    AIManagement.objects.update_or_create(
        prompt_key=key, defaults={'name': name, 'entry_type': 'prompt', 'content': "\n".join(current + new)},
    )


def forwards(apps, schema_editor):
    AIKnowledge = apps.get_model('mysite', 'AIKnowledge')
    AIManagement = apps.get_model('mysite', 'AIManagement')
    Apartment = apps.get_model('mysite', 'Apartment')

    # 1. verified facts -> lines of their document
    facts = AIKnowledge.objects.filter(status='active', confidence='verified').order_by('created_at', 'id')
    for fact in facts:
        label = fact.key.replace('_', ' ').strip()
        line = f"{label[:1].upper()}{label[1:]}: {' '.join((fact.value or '').split())}"
        if fact.scope == 'company':
            entry = AIManagement.objects.filter(prompt_key='global_knowledge_base').first()
            text = ((entry.content if entry else '') or '').strip()
            if line.lower() not in text.lower():
                AIManagement.objects.update_or_create(
                    prompt_key='global_knowledge_base',
                    defaults={'name': 'Global Knowledge Base', 'entry_type': 'knowledge',
                              'content': f"{text}\n{line}".strip()},
                )
            continue
        apartments = (Apartment.objects.filter(id=fact.apartment_id) if fact.apartment_id else
                      Apartment.objects.filter(building_n=fact.building) if fact.building else Apartment.objects.none())
        for apartment in apartments:
            text = (apartment.knowledge_base or '').strip()
            if line.lower() not in text.lower():
                apartment.knowledge_base = f"{text}\n{line}".strip()
                apartment.save(update_fields=['knowledge_base'])

    # 2. staff-taught rules in the legacy prompts -> the agent prompts
    old = {e.prompt_key: e.content or '' for e in AIManagement.objects.filter(prompt_key__in=['ai_extract_check', 'ai_answer_system'])}
    _append_lines(AIManagement, 'ai_agent_kb_rules', 'Agent - KB rules',
                  [new for line, new in OLD_KB_RULES.items() if line in old.get('ai_extract_check', '')])
    _append_lines(AIManagement, 'ai_agent_answer_lessons', 'Agent - answer lessons',
                  [new for line, new in OLD_ANSWER_RULES.items() if line in old.get('ai_answer_system', '')])

    # 3. the legacy prompt / backend rows
    AIManagement.objects.filter(prompt_key__in=LEGACY_KEYS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0094_answer_lessons_to_prompt'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='AIKnowledge',
        ),
    ]
