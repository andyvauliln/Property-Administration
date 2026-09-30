"""
KB_UPDATE rules and the code-level privacy guards. The knowledge itself is only the knowledge-base documents
(kb_documents.py: one per apartment + the global one).

Rules enforced here, not only in the prompt:
- company-wide (global) knowledge only from staff (a staff message in the run, or a manager in Telegram)
- a tenant can add apartment knowledge; a tenant change that touches a code / password / wifi line is held
  until a manager replies "approve N" in Telegram
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
    from mysite.views.messaging import get_global_knowledge_base_text

    texts = [getattr(apartment, 'knowledge_base', None) or '', get_global_knowledge_base_text()]
    values = set()
    for text in texts:
        for line in text.splitlines():
            if _CODE_WORD.search(line) and not _WIFI_WORD.search(line):
                values.update(_DIGITS.findall(line))
    return values


def plan_kb_update(ctx, action, staff_in_trigger):
    """
    What one KB_UPDATE would do, without doing it (JSON-friendly dict). Raises ActionError when it is not allowed.
    Accepts the older key/value shape too (plans made before 2026-09-28).
    """
    from mysite.ai_agent import kb_documents
    from mysite.ai_agent.actions import ActionError

    text = str(action.get('text') or '').strip()
    if not text and action.get('value'):
        key = str(action.get('key') or '').replace('_', ' ').strip()
        text = f"{key[:1].upper()}{key[1:]}: {action['value']}".strip() if key else str(action['value'])
    text = text.strip()
    if not text:
        raise ActionError("the knowledge text is missing")
    replaces = str(action.get('replaces') or '').strip()
    if replaces.lower() in ('null', 'none', '-'):
        replaces = ''
    scope = action.get('scope') or kb_documents.SCOPE_APARTMENT
    if scope == 'building':
        scope = kb_documents.SCOPE_APARTMENT
    if scope not in kb_documents.SCOPES:
        raise ActionError(f"unknown scope {scope}")
    approved_by = action.get('approved_by')
    from_staff = bool(staff_in_trigger or approved_by)
    if scope == kb_documents.SCOPE_APARTMENT and not ctx.apartment:
        raise ActionError("this chat has no apartment")
    if scope == kb_documents.SCOPE_COMPANY and not from_staff:
        raise ActionError("company-wide knowledge is only saved from staff - not from a tenant message")
    needs_approval = not from_staff and kb_documents.touches_credentials(text, replaces)
    current = kb_documents.document(scope, ctx.apartment)
    return {
        'scope': scope, 'apartment_id': getattr(ctx.apartment, 'id', None), 'where': kb_documents.label(scope, ctx.apartment),
        'text': text, 'replaces': replaces, 'source': str(action.get('source') or '')[:255] or None,
        'from_tenant': not from_staff, 'needs_approval': needs_approval, 'approved_by': approved_by,
        'already': text.lower() in current.lower(),
    }


def apply_kb_update(ctx, action, staff_in_trigger):
    """Executes one KB_UPDATE: merges it into the knowledge-base document. Returns a detail string."""
    from mysite.ai_agent import kb_documents
    from mysite.ai_agent.actions import ActionError

    plan = plan_kb_update(ctx, action, staff_in_trigger)
    if plan['already']:
        return "already in the knowledge base - nothing changed"
    if plan['needs_approval']:
        raise ActionError("not saved: a tenant's change to a code / password / wifi line needs a manager's "
                          "\"approve N\" in the Telegram review")
    detail, diff = kb_documents.merge(plan['scope'], ctx.apartment, plan['text'], plan['replaces'],
                                      plan['source'] or ('tenant' if plan['from_tenant'] else 'staff'))
    return detail + (f"\n{diff}" if diff else '')
