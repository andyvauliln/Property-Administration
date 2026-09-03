from django.db import migrations


DEFAULT_CHAT_MODEL = "openai/gpt-5.6-terra"
DEPRECATED_CHAT_MODELS = {
    "openai/gpt-5.2-chat",
    "openai/gpt-5.2",
    "gpt-5.2-chat",
    "gpt-5.2",
}


def forwards(apps, schema_editor):
    AIManagement = apps.get_model("mysite", "AIManagement")
    entry = AIManagement.objects.filter(prompt_key="ai_conversation_model").first()
    current = (entry.content or "").strip() if entry else ""
    if entry and current and current not in DEPRECATED_CHAT_MODELS:
        return
    defaults = {
        "name": "AI Conversation Model",
        "content": DEFAULT_CHAT_MODEL,
        "entry_type": "ai_model",
        "description": "The model used for AI conversations",
    }
    AIManagement.objects.update_or_create(
        prompt_key="ai_conversation_model",
        defaults=defaults,
    )


def backwards(apps, schema_editor):
    AIManagement = apps.get_model("mysite", "AIManagement")
    AIManagement.objects.filter(prompt_key="ai_conversation_model").update(
        content="openai/gpt-4o-mini"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("mysite", "0064_restore_completed_add_expected"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
