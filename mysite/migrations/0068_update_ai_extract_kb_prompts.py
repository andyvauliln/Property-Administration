from django.db import migrations


AI_EXTRACT_CHECK = (
    "You evaluate whether a manager's message contains REUSABLE OPERATIONAL knowledge "
    "about an apartment that should be saved in free-text notes for FUTURE tenants.\n\n"
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be flagged as new — do not save it to notes:\n"
    "{fields_ctx}\n\n"
    "Reply NO (do not save) if the message is mainly:\n"
    "- A one-time scheduling or logistics arrangement (specific meetup time, place, date, ETA)\n"
    "- Payment or check/deposit drop-off coordination for a specific booking\n"
    "- A personal phone number or contact person for a specific upcoming meeting\n"
    "- Tenant-specific coordination that will not help future stays\n"
    "- General conversation, acknowledgments, or status updates\n\n"
    "Only reply YES if the message contains new REUSABLE OPERATIONAL knowledge such as:\n"
    "- WiFi network name or password\n"
    "- Door/gate/lock access codes\n"
    "- Specific parking instructions\n"
    "- House rules (noise, pets, smoking, guests, etc.)\n"
    "- Appliance instructions or quirks\n"
    "- Stable local tips or nearby amenities (not tied to one date/time)\n"
    "- Any other operational detail NOT covered by the fields above that future tenants need\n\n"
    "Manager message: {message_body}\n\n"
    "Reply with YES or NO only."
)

AI_EXTRACT_MERGE = (
    "Current knowledge base:\n{knowledge_base}\n\n"
    "New information to add: {message_body}\n\n"
    "If the new information is a one-time scheduling arrangement, payment meetup, personal contact "
    "for a specific meeting, or other ephemeral logistics for one booking, do NOT add it.\n"
    "In that case, return the current knowledge base unchanged in [UPDATED KB] and put "
    "'No reusable knowledge to add.' in [CHANGES].\n\n"
    "Otherwise merge the new reusable operational information into the knowledge base. "
    "Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)

PREVIOUS_AI_EXTRACT_CHECK = (
    "You evaluate whether a manager's message contains OPERATIONAL knowledge "
    "about an apartment that should be saved in the free-text notes.\n\n"
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be flagged as new — do not save it to notes:\n"
    "{fields_ctx}\n\n"
    "Only reply YES if the message contains new OPERATIONAL knowledge such as:\n"
    "- WiFi network name or password\n"
    "- Door/gate/lock access codes\n"
    "- Specific parking instructions\n"
    "- House rules (noise, pets, smoking, guests, etc.)\n"
    "- Appliance instructions or quirks\n"
    "- Local tips, nearby amenities\n"
    "- Any other operational detail NOT covered by the fields above\n\n"
    "Manager message: {message_body}\n\n"
    "Reply with YES or NO only."
)

PREVIOUS_AI_EXTRACT_MERGE = (
    "Current knowledge base:\n{knowledge_base}\n\n"
    "New information to add: {message_body}\n\n"
    "Merge the new information into the knowledge base. Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed>"
)


def forwards(apps, schema_editor):
    AIManagement = apps.get_model("mysite", "AIManagement")
    AIManagement.objects.filter(prompt_key="ai_extract_check").update(content=AI_EXTRACT_CHECK)
    AIManagement.objects.filter(prompt_key="ai_extract_merge").update(content=AI_EXTRACT_MERGE)


def backwards(apps, schema_editor):
    AIManagement = apps.get_model("mysite", "AIManagement")
    AIManagement.objects.filter(prompt_key="ai_extract_check").update(content=PREVIOUS_AI_EXTRACT_CHECK)
    AIManagement.objects.filter(prompt_key="ai_extract_merge").update(content=PREVIOUS_AI_EXTRACT_MERGE)


class Migration(migrations.Migration):
    dependencies = [
        ("mysite", "0067_update_ai_answer_system_prompt"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
