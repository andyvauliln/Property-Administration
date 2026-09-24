"""
Structured knowledge base of the AI agent + the code-level privacy guards.

Rules enforced here, not only in the prompt:
- a fact becomes VERIFIED only when the run was triggered by an authorized STAFF message;
  anything learned from a tenant is a CANDIDATE and is never shown to the AI as knowledge
- policies and company-wide entries always wait for a manager (they affect every tenant)
- a verified entry replaces the older verified entry with the same key in the same place
- access codes reach the AI only inside the booking's access window
"""
import re
from datetime import datetime, time, timedelta

from django.utils import timezone

ACCESS_CODE_HOURS_BEFORE_CHECKIN = 24

_CODE_WORD = re.compile(r"\b(code|codes|pin|passcode|combination|combo|lock ?box|keypad|key ?pad)\b", re.I)
_WIFI_WORD = re.compile(r"\b(wi-?fi|network|ssid|router|internet)\b", re.I)
_DIGITS = re.compile(r"\d{3,}")
_KEY_IS_CODE = re.compile(r"(door|gate|lock|alarm|access|entry|garage|building|keypad).*(code|pin)|(code|pin)$|lockbox|passcode", re.I)
REDACTED = "[hidden: access codes are shared from 24h before check-in until checkout]"


def normalize_key(key):
    return re.sub(r"[^a-z0-9]+", "_", str(key or '').lower()).strip('_')[:100]


def looks_like_access_code(key, value=''):
    if _WIFI_WORD.search(f"{key} {value}"):
        return False
    return bool(_KEY_IS_CODE.search(str(key or '')) or (_CODE_WORD.search(str(value or '')) and _DIGITS.search(str(value or ''))))


def access_codes_allowed(booking, now=None):
    """True from 24h before check-in (start of start_date) until the end of the checkout day."""
    if not booking or not booking.start_date or not booking.end_date:
        return False
    from mysite.ai_agent.inputs import _team_tz
    now = (now or timezone.now()).astimezone(_team_tz())
    tz = now.tzinfo
    opens = datetime.combine(booking.start_date, time.min, tzinfo=tz) - timedelta(hours=ACCESS_CODE_HOURS_BEFORE_CHECKIN)
    closes = datetime.combine(booking.end_date, time.max, tzinfo=tz)
    return opens <= now <= closes


def redact_access_codes(text):
    """Hides the digits on free-text KB lines that talk about a code. Returns (text, hidden_lines)."""
    hidden = 0
    lines = []
    for line in (text or '').splitlines():
        if _CODE_WORD.search(line) and _DIGITS.search(line) and not _WIFI_WORD.search(line):
            line = _DIGITS.sub('####', line) + f"  {REDACTED}"
            hidden += 1
        lines.append(line)
    return "\n".join(lines), hidden


def hidden_code_values(apartment):
    """Every code-looking number the AI must not say outside the access window."""
    from mysite.models import AIKnowledge, AIManagement

    texts = [getattr(apartment, 'knowledge_base', None) or '']
    texts += [k.content or '' for k in AIManagement.objects.filter(entry_type=AIManagement.ENTRY_TYPE_KNOWLEDGE)]
    values = set()
    for text in texts:
        for line in text.splitlines():
            if _CODE_WORD.search(line) and not _WIFI_WORD.search(line):
                values.update(_DIGITS.findall(line))
    for entry in _entries_for(apartment).filter(is_access_code=True):
        values.update(_DIGITS.findall(entry.value))
    return values


def _entries_for(apartment):
    from django.db.models import Q

    from mysite.models import AIKnowledge

    place = Q(scope=AIKnowledge.SCOPE_COMPANY)
    if apartment:
        place |= Q(scope=AIKnowledge.SCOPE_APARTMENT, apartment=apartment)
        if apartment.building_n:
            place |= Q(scope=AIKnowledge.SCOPE_BUILDING, building=apartment.building_n)
    return AIKnowledge.objects.filter(place, status=AIKnowledge.STATUS_ACTIVE)


def knowledge_block(apartment, booking, now=None, sources=None):
    """Verified structured entries for the AI input (candidates are never included)."""
    from mysite.models import AIKnowledge

    entries = list(
        _entries_for(apartment).filter(confidence=AIKnowledge.CONFIDENCE_VERIFIED)
        .exclude(knowledge_type=AIKnowledge.TYPE_LESSON).order_by('scope', 'key')
    )
    codes_ok = access_codes_allowed(booking, now)
    lines, hidden = [], 0
    for entry in entries:
        value = entry.value
        if entry.is_access_code and not codes_ok:
            value, hidden = REDACTED, hidden + 1
        lines.append(f"- [{entry.scope}] {entry.key} = {value}  (learned {entry.created_at:%Y-%m-%d}, source: {entry.source or '-'})")
    if sources is not None:
        sources.update({'kb_verified_entries': len(entries), 'kb_access_codes_hidden': hidden, 'access_codes_allowed': codes_ok})
    if not lines:
        return None
    return (
        "=== VERIFIED KB ENTRIES (learned from staff; newer than the free-text knowledge base, "
        "on conflict these win) ===\n" + "\n".join(lines)
    )


LESSONS_LIMIT = 60


def lessons_block(apartment, sources=None):
    """Answer lessons staff taught by replying to AI answers in Telegram (answer_review.py), newest first."""
    from mysite.models import AIKnowledge

    entries = list(
        _entries_for(apartment).filter(confidence=AIKnowledge.CONFIDENCE_VERIFIED, knowledge_type=AIKnowledge.TYPE_LESSON)
        .order_by('-created_at')[:LESSONS_LIMIT]
    )
    if sources is not None:
        sources['answer_lessons'] = len(entries)
    if not entries:
        return None
    return (
        "=== ANSWER_LESSONS (staff corrected earlier AI answers and said how to answer such messages; follow them "
        "for similar messages - an apartment lesson wins over a company one) ===\n"
        + "\n".join(f"- [{e.scope_label}] {e.key}: {e.value}  ({e.created_at:%Y-%m-%d})" for e in entries)
    )


def save_lesson(apartment, key, rule, source=None, conversation_sid=None, run=None):
    """
    A staff member taught how to answer a kind of message. Active at once (it comes from staff),
    replaces the older lesson with the same key in the same place. Returns (entry, detail).
    """
    from mysite.models import AIKnowledge

    scope = AIKnowledge.SCOPE_APARTMENT if apartment else AIKnowledge.SCOPE_COMPANY
    key = normalize_key(key) or 'answer_lesson'
    same_place = AIKnowledge.objects.filter(
        scope=scope, apartment=apartment, building=None, key=key, knowledge_type=AIKnowledge.TYPE_LESSON,
        status=AIKnowledge.STATUS_ACTIVE,
    )
    entry = AIKnowledge.objects.create(
        scope=scope, apartment=apartment, knowledge_type=AIKnowledge.TYPE_LESSON, key=key, value=rule.strip(),
        confidence=AIKnowledge.CONFIDENCE_VERIFIED, source=(source or '')[:255] or None,
        conversation_sid=conversation_sid, created_by_run=run, reviewed_by=(source or '')[:255] or None,
        reviewed_at=timezone.now(),
    )
    replaced = same_place.exclude(id=entry.id).update(status=AIKnowledge.STATUS_SUPERSEDED, updated_at=timezone.now())
    detail = f"#{entry.id} '{key}'" + (f", replaced {replaced} older" if replaced else "")
    return entry, detail


def plan_kb_update(ctx, action, staff_in_trigger):
    """
    What one KB_UPDATE would save, without saving it (JSON-friendly dict). The confidence rules are applied
    here: verified only when an authorized staff message started the run and it is not a policy or company-wide.
    'already' is True when the same value is already stored.
    """
    from mysite.ai_agent.actions import ActionError
    from mysite.models import AIKnowledge

    key = normalize_key(action.get('key'))
    value = str(action.get('value') or '').strip()
    if not key or not value:
        raise ActionError("key or value is missing")
    scope = action.get('scope') or AIKnowledge.SCOPE_APARTMENT
    if scope not in dict(AIKnowledge.SCOPE_CHOICES):
        raise ActionError(f"unknown scope {scope}")
    knowledge_type = action.get('knowledge_type') if action.get('knowledge_type') in dict(AIKnowledge.TYPE_CHOICES) else AIKnowledge.TYPE_FACT

    apartment = ctx.apartment if scope == AIKnowledge.SCOPE_APARTMENT else None
    building = (getattr(ctx.apartment, 'building_n', None) or None) if scope == AIKnowledge.SCOPE_BUILDING else None
    if scope == AIKnowledge.SCOPE_APARTMENT and not apartment:
        raise ActionError("no apartment for an apartment-scoped entry")
    if scope == AIKnowledge.SCOPE_BUILDING and not building:
        raise ActionError("this apartment has no building number")

    confidence, notes = AIKnowledge.CONFIDENCE_CANDIDATE, []
    if action.get('approved_by'):
        # A manager approved / corrected it in the Telegram review: that IS the manager's approval
        confidence, notes = AIKnowledge.CONFIDENCE_VERIFIED, [f"approved by {action['approved_by']} in the Telegram review"]
    elif action.get('confidence') == AIKnowledge.CONFIDENCE_VERIFIED:
        if not staff_in_trigger:
            notes.append("kept as candidate: no authorized staff message in this run")
        elif knowledge_type == AIKnowledge.TYPE_POLICY or scope == AIKnowledge.SCOPE_COMPANY:
            notes.append("kept as candidate: policies and company-wide entries need a manager's approval")
        else:
            confidence = AIKnowledge.CONFIDENCE_VERIFIED
    already = AIKnowledge.objects.filter(
        scope=scope, apartment=apartment, building=building, key=key, status=AIKnowledge.STATUS_ACTIVE,
        value__iexact=value, confidence=confidence,
    ).exists()
    return {
        'scope': scope, 'apartment_id': getattr(apartment, 'id', None), 'building': building,
        'where': plan_where(scope, apartment, building), 'knowledge_type': knowledge_type, 'key': key, 'value': value,
        'confidence': confidence, 'notes': notes, 'source': str(action.get('source') or '')[:255] or None, 'already': already,
        'reviewer': action.get('approved_by'),
    }


def plan_where(scope, apartment, building):
    if scope == 'apartment':
        return f"{getattr(apartment, 'name', '?')} (this apartment)"
    if scope == 'building':
        return f"building {building}"
    return "all apartments (company-wide)"


def save_kb_plan(plan, conversation_sid, run=None, reviewer=None):
    """Saves a plan_kb_update() dict. Returns a human-readable detail string."""
    from mysite.models import AIKnowledge

    same_place = AIKnowledge.objects.filter(
        scope=plan['scope'], apartment_id=plan['apartment_id'], building=plan['building'], key=plan['key'],
        status=AIKnowledge.STATUS_ACTIVE,
    )
    if same_place.filter(value__iexact=plan['value'], confidence=plan['confidence']).exists():
        return "already known - nothing changed"
    entry = AIKnowledge.objects.create(
        scope=plan['scope'], apartment_id=plan['apartment_id'], building=plan['building'],
        knowledge_type=plan['knowledge_type'], key=plan['key'], value=plan['value'], confidence=plan['confidence'],
        is_access_code=looks_like_access_code(plan['key'], plan['value']), source=plan.get('source'),
        note="; ".join(plan.get('notes') or [])[:255] or None, conversation_sid=conversation_sid, created_by_run=run,
        reviewed_by=reviewer, reviewed_at=timezone.now() if reviewer else None,
    )
    detail = f"saved {plan['confidence']} {plan['scope']} entry #{entry.id} '{plan['key']}'"
    if plan['confidence'] == AIKnowledge.CONFIDENCE_VERIFIED:
        replaced = same_place.filter(confidence=AIKnowledge.CONFIDENCE_VERIFIED).exclude(id=entry.id).update(
            status=AIKnowledge.STATUS_SUPERSEDED, updated_at=timezone.now(),
        )
        if replaced:
            detail += f", replaced {replaced} older verified entr{'y' if replaced == 1 else 'ies'}"
    if plan.get('notes'):
        detail += f" ({'; '.join(plan['notes'])})"
    return detail


def apply_kb_update(ctx, action, staff_in_trigger):
    """Executes one KB_UPDATE action at once. Returns a human-readable detail string."""
    plan = plan_kb_update(ctx, action, staff_in_trigger)
    if plan['already']:
        return "already known - nothing changed"
    return save_kb_plan(plan, ctx.conversation_sid, ctx.ai_run, reviewer=plan.get('reviewer'))


def approve(entry, reviewer):
    """A manager turns a candidate into verified knowledge."""
    from mysite.models import AIKnowledge

    AIKnowledge.objects.filter(
        scope=entry.scope, apartment=entry.apartment, building=entry.building, key=entry.key,
        status=AIKnowledge.STATUS_ACTIVE, confidence=AIKnowledge.CONFIDENCE_VERIFIED,
    ).exclude(id=entry.id).update(status=AIKnowledge.STATUS_SUPERSEDED, updated_at=timezone.now())
    entry.confidence = AIKnowledge.CONFIDENCE_VERIFIED
    entry.status = AIKnowledge.STATUS_ACTIVE
    entry.reviewed_by, entry.reviewed_at = reviewer, timezone.now()
    entry.save()


def reject(entry, reviewer):
    from mysite.models import AIKnowledge

    entry.status = AIKnowledge.STATUS_REJECTED
    entry.reviewed_by, entry.reviewed_at = reviewer, timezone.now()
    entry.save()
