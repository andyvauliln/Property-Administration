from django.db import migrations


AI_EXTRACT_CHECK = (
    "You evaluate whether a manager's message contains REUSABLE OPERATIONAL knowledge "
    "about an apartment that should be saved in free-text notes for FUTURE tenants.\n\n"
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be flagged as new — do not save it to notes:\n"
    "{fields_ctx}\n\n"
    "IMPORTANT:\n"
    "- Reply YES if the message contains ANY reusable operational detail below, even when mixed "
    "with greetings, name introductions, or other conversational text.\n"
    "- WiFi network names and passwords, door/gate codes, and access credentials MUST be saved. "
    "They belong in the apartment knowledge base — never skip them as too sensitive.\n\n"
    "Reply NO only when the message has NO reusable operational content, for example:\n"
    "- Only a one-time scheduling or logistics arrangement (specific meetup time, place, date, ETA)\n"
    "- Only payment or check/deposit drop-off coordination for a specific booking\n"
    "- Only offering, delivering, or dropping off supplies for this stay "
    "(light bulbs, towels, linens, toiletries, extra keys, etc.), including "
    "'we have X, can I drop it off today?'\n"
    "- Only a personal phone number or contact person for a specific upcoming meeting\n"
    "- Only tenant-specific coordination, acknowledgments, or status updates with no operational facts\n\n"
    "Reply YES when the message contains new REUSABLE OPERATIONAL knowledge such as:\n"
    "- WiFi network name or password\n"
    "- Door/gate/lock access codes\n"
    "- Specific parking instructions\n"
    "- House rules (noise, pets, smoking, guests, etc.)\n"
    "- Appliance instructions or quirks (how something works, not a one-time delivery of a part or supply)\n"
    "- Standing supply facts only if they are property rules (e.g. spare bulbs live in the closet; "
    "guests replace bulbs themselves). Do NOT treat 'we have X / I'll drop X off' as standing inventory.\n"
    "- Stable local tips or nearby amenities (not tied to one date/time)\n"
    "- Any other operational detail NOT covered by the fields above that future tenants need\n\n"
    "Manager message: {message_body}\n\n"
    "Reply with YES or NO only."
)

AI_EXTRACT_MERGE = (
    "Current knowledge base:\n{knowledge_base}\n\n"
    "New information to add: {message_body}\n\n"
    "Extract ONLY reusable operational facts for future tenants. Ignore greetings, name introductions, "
    "do-you-see-this-message checks, and other conversational filler.\n\n"
    "Always merge WiFi names/passwords, door/gate codes, parking instructions, and house rules when present. "
    "Never refuse to save them because they are credentials — that is exactly what the apartment KB is for.\n\n"
    "If the message contains ONLY one-time scheduling, payment meetups, personal contacts for a specific "
    "meeting, offering/delivering supplies for this stay (light bulbs, towels, linens, extra keys, "
    "'can I drop them off today?'), or other ephemeral booking-specific coordination with no reusable "
    "operational facts, do NOT add it. "
    "Return the current knowledge base unchanged in [UPDATED KB] and put "
    "'No reusable knowledge to add.' in [CHANGES].\n\n"
    "If part of the message is reusable (parking, codes, WiFi, house rules) and part is a one-time "
    "delivery or drop-off offer, merge ONLY the reusable part. Do not save facts like "
    "'Bedroom light bulbs: Available from the property.'\n\n"
    "Otherwise merge the reusable operational information into the knowledge base. "
    "Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
)

PREVIOUS_AI_EXTRACT_CHECK = (
    "You evaluate whether a manager's message contains REUSABLE OPERATIONAL knowledge "
    "about an apartment that should be saved in free-text notes for FUTURE tenants.\n\n"
    "The following information is ALREADY stored in structured database fields "
    "and must NOT be flagged as new — do not save it to notes:\n"
    "{fields_ctx}\n\n"
    "IMPORTANT:\n"
    "- Reply YES if the message contains ANY reusable operational detail below, even when mixed "
    "with greetings, name introductions, or other conversational text.\n"
    "- WiFi network names and passwords, door/gate codes, and access credentials MUST be saved. "
    "They belong in the apartment knowledge base — never skip them as too sensitive.\n\n"
    "Reply NO only when the message has NO reusable operational content, for example:\n"
    "- Only a one-time scheduling or logistics arrangement (specific meetup time, place, date, ETA)\n"
    "- Only payment or check/deposit drop-off coordination for a specific booking\n"
    "- Only a personal phone number or contact person for a specific upcoming meeting\n"
    "- Only tenant-specific coordination, acknowledgments, or status updates with no operational facts\n\n"
    "Reply YES when the message contains new REUSABLE OPERATIONAL knowledge such as:\n"
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

PREVIOUS_AI_EXTRACT_MERGE = (
    "Current knowledge base:\n{knowledge_base}\n\n"
    "New information to add: {message_body}\n\n"
    "Extract ONLY reusable operational facts for future tenants. Ignore greetings, name introductions, "
    "do-you-see-this-message checks, and other conversational filler.\n\n"
    "Always merge WiFi names/passwords, door/gate codes, parking instructions, and house rules when present. "
    "Never refuse to save them because they are credentials — that is exactly what the apartment KB is for.\n\n"
    "If the message contains ONLY one-time scheduling, payment meetups, personal contacts for a specific "
    "meeting, or other ephemeral booking-specific coordination with no reusable operational facts, do NOT add it. "
    "Return the current knowledge base unchanged in [UPDATED KB] and put "
    "'No reusable knowledge to add.' in [CHANGES].\n\n"
    "Otherwise merge the reusable operational information into the knowledge base. "
    "Keep it clear and organized.\n\n"
    "Respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[UPDATED KB]\n"
    "<full updated knowledge base text>\n"
    "[CHANGES]\n"
    "<one or two sentences describing only what was added or changed, or 'No reusable knowledge to add.'>"
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
        ("mysite", "0069_ai_extract_save_wifi_credentials"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
