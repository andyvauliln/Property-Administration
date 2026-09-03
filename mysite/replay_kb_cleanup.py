"""Reset apartment/global knowledge for chat replay, keeping manual replay saves."""

from __future__ import annotations

from django.utils import timezone

from mysite.models import AIManagement, Apartment

REPLAY_GLOBAL_DESCRIPTION_MARKER = "Saved from chat replay"


def collect_preserved_kb_from_state(state):
    """
    Collect KB content to keep from replay JSON state.
    Preserves explicit saves (saved_apartment/saved_global) and to_save tags.
    Returns (preserved_apartments, preserved_global_entries).
    """
    preserved_apartments = {}
    preserved_global = []

    for conv in state.get("conversations", []):
        apartment_id = conv.get("apartment_id")
        conv_tags = conv.get("tags") or []

        for msg in conv.get("messages", []):
            kb = msg.get("kb") or {}
            tags = msg.get("tags") or []
            keep = (
                kb.get("saved_apartment")
                or kb.get("saved_global")
                or "to_save" in tags
                or "to_save" in conv_tags
            )
            if not keep:
                continue

            if apartment_id and kb.get("apartment"):
                preserved_apartments[apartment_id] = kb["apartment"]

            global_content = (kb.get("global") or "").strip()
            if global_content and (kb.get("saved_global") or "to_save" in tags or "to_save" in conv_tags):
                preserved_global.append(
                    {
                        "content": global_content,
                        "name": f"Replay preserved knowledge {timezone.now().strftime('%Y-%m-%d %H:%M')}",
                        "description": f"Preserved from replay JSON ({conv.get('conversation_sid')})",
                    }
                )

    return preserved_apartments, preserved_global


def _normalize_kb_text(value):
    return " ".join(str(value or "").split()).strip()


def _is_manual_admin_global_entry(entry):
    if entry.entry_type != AIManagement.ENTRY_TYPE_KNOWLEDGE:
        return False
    description = entry.description or ""
    return REPLAY_GLOBAL_DESCRIPTION_MARKER not in description


def cleanup_knowledge_bases(state, *, dry_run=False):
    """
    Clear apartment KB and replay-created global KB from scratch.
    Keeps manual replay saves from JSON and manual admin global entries.
    """
    preserved_apartments, preserved_global = collect_preserved_kb_from_state(state)
    preserved_global_contents = {_normalize_kb_text(item["content"]) for item in preserved_global}

    apartments_with_kb = list(
        Apartment.objects.exclude(knowledge_base__isnull=True).exclude(knowledge_base="").only(
            "id", "name", "knowledge_base"
        )
    )
    global_entries = list(
        AIManagement.objects.filter(entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE).only(
            "id", "name", "content", "description", "entry_type"
        )
    )

    apartment_clear_ids = []
    apartment_restore = []
    for apartment in apartments_with_kb:
        if apartment.id in preserved_apartments:
            apartment_restore.append(
                {
                    "id": apartment.id,
                    "name": apartment.name,
                    "from": apartment.knowledge_base,
                    "to": preserved_apartments[apartment.id],
                }
            )
        else:
            apartment_clear_ids.append(apartment.id)

    global_keep = []
    global_delete = []
    for entry in global_entries:
        normalized = _normalize_kb_text(entry.content)
        if _is_manual_admin_global_entry(entry):
            global_keep.append({"id": entry.id, "name": entry.name, "reason": "manual admin entry"})
            continue
        if normalized in preserved_global_contents:
            global_keep.append({"id": entry.id, "name": entry.name, "reason": "preserved replay save"})
            continue
        global_delete.append({"id": entry.id, "name": entry.name, "description": entry.description or ""})

    stats = {
        "mode": "no-delete" if dry_run else "live",
        "preserved_apartment_ids": sorted(preserved_apartments.keys()),
        "preserved_global_from_json": len(preserved_global),
        "apartments_cleared": len(apartment_clear_ids),
        "apartments_restored": len(preserved_apartments),
        "global_kept": len(global_keep),
        "global_deleted": len(global_delete),
        "global_created": 0,
        "apartment_clear_ids": apartment_clear_ids,
        "apartment_restore": apartment_restore,
        "global_keep": global_keep,
        "global_delete": global_delete,
    }

    if dry_run:
        return stats

    if apartment_clear_ids:
        Apartment.objects.filter(id__in=apartment_clear_ids).update(knowledge_base=None)

    for apartment_id, content in preserved_apartments.items():
        Apartment.objects.filter(id=apartment_id).update(knowledge_base=content)

    if global_delete:
        AIManagement.objects.filter(id__in=[item["id"] for item in global_delete]).delete()

    existing_global_normalized = {
        _normalize_kb_text(entry.content)
        for entry in AIManagement.objects.filter(entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE)
    }
    created = 0
    for item in preserved_global:
        normalized = _normalize_kb_text(item["content"])
        if not normalized or normalized in existing_global_normalized:
            continue
        AIManagement.objects.create(
            name=item["name"],
            content=item["content"],
            entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE,
            description=item["description"],
        )
        existing_global_normalized.add(normalized)
        created += 1

    stats["global_created"] = created
    return stats


def reset_knowledge_for_conversation(state, conversation_sid, apartment_id, *, dry_run=False):
    """
    Reset KB for one replay conversation.
    Other conversations keep their JSON progress and preserved KB.
    This apartment is restored from other conversations' saved KB, or cleared.
    """
    remaining = [
        conv
        for conv in state.get("conversations", [])
        if conv.get("conversation_sid") != conversation_sid
    ]
    preserved_apartments, preserved_global = collect_preserved_kb_from_state(
        {"conversations": remaining}
    )
    preserved_global_contents = {_normalize_kb_text(item["content"]) for item in preserved_global}

    stats = {
        "mode": "no-delete" if dry_run else "live",
        "conversation_sid": conversation_sid,
        "apartment_id": apartment_id,
        "apartment_action": "none",
        "global_deleted": 0,
    }

    if apartment_id:
        apartment = Apartment.objects.filter(id=apartment_id).only("id", "knowledge_base").first()
        if apartment:
            if apartment_id in preserved_apartments:
                stats["apartment_action"] = "restore"
                stats["apartment_to"] = preserved_apartments[apartment_id]
                if not dry_run:
                    Apartment.objects.filter(id=apartment_id).update(
                        knowledge_base=preserved_apartments[apartment_id]
                    )
            else:
                stats["apartment_action"] = "clear"
                if not dry_run:
                    Apartment.objects.filter(id=apartment_id).update(knowledge_base=None)

    global_entries = AIManagement.objects.filter(
        entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE,
        description__icontains=conversation_sid,
    )
    delete_ids = []
    for entry in global_entries:
        if _is_manual_admin_global_entry(entry):
            continue
        if _normalize_kb_text(entry.content) in preserved_global_contents:
            continue
        delete_ids.append(entry.id)
    stats["global_deleted"] = len(delete_ids)
    if delete_ids and not dry_run:
        AIManagement.objects.filter(id__in=delete_ids).delete()

    return stats
