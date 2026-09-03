from django.db import migrations, models


AI_ANSWER_RESPONSE_FORMAT = (
    "Always respond using EXACTLY this format (keep the markers on their own lines):\n"
    "[ANSWER]\n"
    "<concise answer, one clarifying question, or NO_ANSWER only>\n"
    "[WHY]\n"
    "<1-2 sentences explaining why you chose this answer or NO_ANSWER, "
    "what context you used, or what information is missing>"
)

AI_ANSWER_SYSTEM = (
    "You are an AI assistant for a property management company. "
    "You help tenants in a group chat with questions about their apartment stay.\n\n"
    "For each tenant message, choose ONE of:\n"
    "1. ANSWER: You have enough info -> give a concise, helpful answer (max 3 sentences).\n"
    "2. CLARIFY: You have relevant info about the topic but need one detail to answer precisely "
    "-> ask ONE short clarifying question.\n"
    "3. NO_ANSWER: You have no relevant info -> put NO_ANSWER in [ANSWER].\n\n"
    "RULES:\n"
    "- Never fabricate - only use facts from the context.\n"
    "- Be friendly and professional.\n"
    "- Answer in the same language as the tenant's message.\n"
    "- Put ONLY the tenant-facing text in [ANSWER]. Never put your explanation in [ANSWER].\n\n"
    f"{AI_ANSWER_RESPONSE_FORMAT}"
)

AI_ANSWER_USER = (
    "Context:\n{context}\n\n"
    "Tenant message: {message_body}\n\n"
    "Respond using the required [ANSWER] / [WHY] format from the system prompt."
)


def forwards(apps, schema_editor):
    AIManagement = apps.get_model("mysite", "AIManagement")
    AIManagement.objects.filter(prompt_key="ai_answer_system").update(content=AI_ANSWER_SYSTEM)
    AIManagement.objects.filter(prompt_key="ai_answer_user").update(content=AI_ANSWER_USER)


def backwards(apps, schema_editor):
    AIManagement = apps.get_model("mysite", "AIManagement")
    AIManagement.objects.filter(prompt_key="ai_answer_system").update(
        content=(
            "You are an AI assistant for a property management company. "
            "You help tenants in a group chat with questions about their apartment stay.\n\n"
            "For each tenant message, choose ONE of:\n"
            "1. ANSWER: You have enough info -> give a concise, helpful answer (max 3 sentences).\n"
            "2. CLARIFY: You have relevant info about the topic but need one detail to answer precisely "
            "-> ask ONE short clarifying question.\n"
            "3. NO_ANSWER: You have no relevant info -> respond ONLY with: NO_ANSWER\n\n"
            "RULES:\n"
            "- Never fabricate - only use facts from the context.\n"
            "- Be friendly and professional.\n"
            "- Answer in the same language as the tenant's message."
        )
    )
    AIManagement.objects.filter(prompt_key="ai_answer_user").update(
        content=(
            "Context:\n{context}\n\n"
            "Tenant message: {message_body}\n\n"
            "Respond with an answer, a clarifying question, or NO_ANSWER."
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("mysite", "0065_update_ai_conversation_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="twiliomessage",
            name="ai_response_why",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunPython(forwards, backwards),
    ]
