from django.db import migrations


AI_EXTRACT_CHECK = (
    "You evaluate whether a manager's message contains REUSABLE OPERATIONAL knowledge "
    "about an apartment that should be saved in free-text notes for FUTURE tenants.\n\n"
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be flagged as new — do not save it to notes:\n"
    "{fields_ctx}\n\n"
    "Recent chat history before this message (context only — focus extraction on the manager message below, "
    "not on history):\n"
    "{chat_history}\n\n"
    "RULES:\n"
    "- YES if the message states a standing procedure or fact that would still be true for the next tenant "
    "(how to access the property, how parking works, house rules, WiFi/credentials, how something operates, "
    "stable local tips). Save it even when it was said while handling a current request.\n"
    "- WiFi names/passwords and access codes MUST be saved; do not skip them as too sensitive.\n"
    "- NO only if the message is solely this-stay coordination: a specific time/ETA, meetup, payment drop-off, "
    "offering to bring or deliver an item now, a personal contact for one meeting, or an acknowledgment "
    "with no lasting procedure.\n"
    "- A current request can still contain a standing rule. If the manager explains how the property works, "
    "that is YES. If they only arrange a one-off action, that is NO.\n\n"
    "Manager message to evaluate: {message_body}\n\n"
    "Reply with YES or NO only."
)

AI_EXTRACT_MERGE = (
    "Current apartment knowledge base:\n{knowledge_base}\n\n"
    "Recent chat history before this message (context only — merge facts from the new information below, "
    "not from history):\n"
    "{chat_history}\n\n"
    "New information to add: {message_body}\n\n"
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

AI_EXTRACT_GLOBAL_CHECK = (
    "You evaluate whether a manager's message contains REUSABLE company-wide policy or procedure "
    "that should be saved in the global knowledge base for FUTURE stays across properties.\n\n"
    "Recent chat history before this message (context only — focus extraction on the manager message below, "
    "not on history):\n"
    "{chat_history}\n\n"
    "RULES:\n"
    "- YES for standing company-wide rules: payment procedures, deposit/hold policies, general company rules, "
    "reusable operational guidance that applies beyond one apartment or one stay.\n"
    "- NO for apartment-specific access details (door codes, WiFi for one unit), one-off coordination "
    "(times/ETAs, meetups, delivering an item now), greetings, or acknowledgments.\n"
    "- A message can contain both apartment-specific and company-wide facts — reply YES if ANY company-wide "
    "reusable policy is present.\n\n"
    "Manager message to evaluate: {message_body}\n\n"
    "Reply with YES or NO only."
)

AI_EXTRACT_GLOBAL_MERGE = (
    "Current global knowledge base:\n{knowledge_base}\n\n"
    "Recent chat history before this message (context only — merge facts from the new information below, "
    "not from history):\n"
    "{chat_history}\n\n"
    "New information to add: {message_body}\n\n"
    "Extract and merge company-wide reusable policies and standing procedures. "
    "Ignore apartment-specific access details unless they are a company-wide policy.\n\n"
    "RULES:\n"
    "- Keep payment procedures, company rules, and reusable operational guidance.\n"
    "- Drop apartment-specific codes/credentials and one-stay coordination.\n"
    "- If there is nothing standing to keep, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n"
    "Otherwise merge the standing facts. Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated global knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)


def unify_kb_extract_prompts(apps, schema_editor):
    AIManagement = apps.get_model('mysite', 'AIManagement')
    AIManagement.objects.filter(prompt_key='ai_extract_check').update(
        content=AI_EXTRACT_CHECK,
        description='Apartment KB check. Placeholders: {fields_ctx}, {chat_history}, {message_body}',
    )
    AIManagement.objects.filter(prompt_key='ai_extract_merge').update(
        content=AI_EXTRACT_MERGE,
        description='Apartment KB merge. Placeholders: {knowledge_base}, {chat_history}, {message_body}',
    )
    AIManagement.objects.filter(
        prompt_key__in=['ai_generate_apartment_kb', 'ai_generate_global_kb']
    ).delete()
    for prompt_key, name, description, content in (
        (
            'ai_extract_global_check',
            'AI Extract Global Check',
            'Global KB check. Placeholders: {chat_history}, {message_body}',
            AI_EXTRACT_GLOBAL_CHECK,
        ),
        (
            'ai_extract_global_merge',
            'AI Extract Global Merge',
            'Global KB merge. Placeholders: {knowledge_base}, {chat_history}, {message_body}',
            AI_EXTRACT_GLOBAL_MERGE,
        ),
    ):
        if AIManagement.objects.filter(prompt_key=prompt_key).exists():
            AIManagement.objects.filter(prompt_key=prompt_key).update(
                content=content,
                description=description,
                name=name,
            )
        else:
            AIManagement.objects.create(
                name=name,
                content=content,
                entry_type='prompt',
                prompt_key=prompt_key,
                description=description,
            )


def reverse_unify_kb_extract_prompts(apps, schema_editor):
    AIManagement = apps.get_model('mysite', 'AIManagement')
    AIManagement.objects.filter(
        prompt_key__in=['ai_extract_global_check', 'ai_extract_global_merge']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0075_kb_generate_prompts'),
    ]

    operations = [
        migrations.RunPython(unify_kb_extract_prompts, reverse_unify_kb_extract_prompts),
    ]
