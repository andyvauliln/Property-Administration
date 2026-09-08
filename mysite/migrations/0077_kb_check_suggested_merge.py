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
    "Reply using EXACTLY this format (keep the markers on their own lines):\n"
    "[DECISION]\n"
    "YES or NO\n"
    "[SUGGESTED]\n"
    "<concise standing facts to save for future tenants — only if YES; otherwise \"none\">"
)

AI_EXTRACT_MERGE = (
    "Current apartment knowledge base:\n{knowledge_base}\n\n"
    "Manager message:\n{message_body}\n\n"
    "Our system detected the following possible reusable knowledge from this message:\n"
    "{suggested_knowledge}\n\n"
    "Merge this into the knowledge base only if it is not already covered. "
    "Do not duplicate existing facts. Keep the result clear and organized.\n\n"
    "If nothing new to add, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n\n"
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
    "Reply using EXACTLY this format (keep the markers on their own lines):\n"
    "[DECISION]\n"
    "YES or NO\n"
    "[SUGGESTED]\n"
    "<concise company-wide policy text to save — only if YES; otherwise \"none\">"
)

AI_EXTRACT_GLOBAL_MERGE = (
    "Current global knowledge base:\n{knowledge_base}\n\n"
    "Manager message:\n{message_body}\n\n"
    "Our system detected the following possible reusable company-wide knowledge from this message:\n"
    "{suggested_knowledge}\n\n"
    "Merge this into the knowledge base only if it is not already covered. "
    "Do not duplicate existing facts. Keep the result clear and organized.\n\n"
    "If nothing new to add, return the current knowledge base unchanged in [UPDATED KB] "
    "and put 'No reusable knowledge to add.' in [CHANGES].\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated global knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)


def update_kb_check_merge_prompts(apps, schema_editor):
    AIManagement = apps.get_model('mysite', 'AIManagement')
    updates = (
        (
            'ai_extract_check',
            'Apartment KB check. Placeholders: {fields_ctx}, {chat_history}, {message_body}',
            AI_EXTRACT_CHECK,
        ),
        (
            'ai_extract_merge',
            'Apartment KB merge. Placeholders: {knowledge_base}, {message_body}, {suggested_knowledge}',
            AI_EXTRACT_MERGE,
        ),
        (
            'ai_extract_global_check',
            'Global KB check. Placeholders: {chat_history}, {message_body}',
            AI_EXTRACT_GLOBAL_CHECK,
        ),
        (
            'ai_extract_global_merge',
            'Global KB merge. Placeholders: {knowledge_base}, {message_body}, {suggested_knowledge}',
            AI_EXTRACT_GLOBAL_MERGE,
        ),
    )
    for prompt_key, description, content in updates:
        AIManagement.objects.filter(prompt_key=prompt_key).update(
            content=content,
            description=description,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('mysite', '0076_unify_kb_extract_prompts'),
    ]

    operations = [
        migrations.RunPython(update_kb_check_merge_prompts, migrations.RunPython.noop),
    ]
