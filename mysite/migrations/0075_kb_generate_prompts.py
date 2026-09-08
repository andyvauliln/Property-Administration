from django.db import migrations


AI_GENERATE_APARTMENT_KB = (
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be duplicated in notes:\n"
    "{fields_ctx}\n\n"
    "Current apartment knowledge base:\n{knowledge_base}\n\n"
    "Manager messages from the group chat (one block per message):\n{manager_messages}\n\n"
    "Merge standing operational facts that future tenants would still need. "
    "Ignore greetings, introductions, and conversational filler.\n\n"
    "RULES:\n"
    "- Keep how the property works: access, parking, house rules, WiFi/credentials, appliance operation, "
    "stable local tips. Credentials belong in the knowledge base.\n"
    "- Drop this-stay coordination only: specific times/ETAs, meetups, payment drop-offs, "
    "offering to bring or deliver an item now, personal contacts for one meeting.\n"
    "- If a message mixes a standing rule with a one-off action, merge only the standing rule.\n"
    "- If there is nothing standing to keep, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n"
    "Otherwise merge the standing facts. Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)

AI_GENERATE_GLOBAL_KB = (
    "Current global knowledge base:\n{knowledge_base}\n\n"
    "Full group chat conversation. Each line is author-labeled. "
    "Authors marked [notification] are automated system messages, not tenant/manager chat:\n"
    "{conversation}\n\n"
    "Extract company-wide reusable policies and standing procedures that apply across properties "
    "and future stays. Ignore one-off coordination, greetings, and tenant-specific scheduling.\n\n"
    "RULES:\n"
    "- Keep policies, payment procedures, company-wide rules, and reusable operational guidance.\n"
    "- Drop apartment-specific access details unless they are a company-wide policy.\n"
    "- If there is nothing standing to keep, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n"
    "Otherwise merge the standing facts. Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated global knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)


def seed_kb_generate_prompts(apps, schema_editor):
    AIManagement = apps.get_model('mysite', 'AIManagement')
    prompts = [
        {
            'prompt_key': 'ai_generate_apartment_kb',
            'name': 'Generate Apartment KB',
            'description': (
                'Bulk apartment KB generation from chat modal. '
                'Placeholders: {fields_ctx}, {knowledge_base}, {manager_messages}'
            ),
            'content': AI_GENERATE_APARTMENT_KB,
        },
        {
            'prompt_key': 'ai_generate_global_kb',
            'name': 'Generate Global KB',
            'description': (
                'Bulk global KB generation from chat modal. '
                'Placeholders: {knowledge_base}, {conversation}'
            ),
            'content': AI_GENERATE_GLOBAL_KB,
        },
    ]
    for prompt in prompts:
        if AIManagement.objects.filter(prompt_key=prompt['prompt_key']).exists():
            continue
        AIManagement.objects.create(
            name=prompt['name'],
            content=prompt['content'],
            entry_type='prompt',
            prompt_key=prompt['prompt_key'],
            description=prompt['description'],
        )


def unseed_kb_generate_prompts(apps, schema_editor):
    AIManagement = apps.get_model('mysite', 'AIManagement')
    AIManagement.objects.filter(
        prompt_key__in=['ai_generate_apartment_kb', 'ai_generate_global_kb']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0074_twilimessage_ai_notes'),
    ]

    operations = [
        migrations.RunPython(seed_kb_generate_prompts, unseed_kb_generate_prompts),
    ]
