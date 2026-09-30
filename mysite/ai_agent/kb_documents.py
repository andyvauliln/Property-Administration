"""
The knowledge base: ONE free-text document per apartment (Apartment.knowledge_base) plus the global document
(AIManagement 'global_knowledge_base'). Nothing else stores knowledge.

The Claude agent adds to them with KB_UPDATE (applied after the Telegram review window); staff edit them in
AI Management or the chat Knowledge Base window, and can draft them from a whole chat ("Generate").
New information is merged in with the 'ai_kb_merge' prompt through the Claude one-shot client.
"""
import difflib
import re

from mysite.unified_logger import log_warning

SCOPE_APARTMENT = 'apartment'
SCOPE_COMPANY = 'company'
SCOPES = (SCOPE_APARTMENT, SCOPE_COMPANY)
MAX_DROP_WITHOUT_REPLACES = 0.4   # a merge that loses more than this share of the document is not trusted


def document(scope, apartment=None):
    from mysite.views.messaging import get_global_knowledge_base_text
    if scope == SCOPE_COMPANY:
        return get_global_knowledge_base_text()
    return (getattr(apartment, 'knowledge_base', None) or '').strip()


def save_document(scope, text, apartment=None, source=None):
    from mysite.views.messaging import save_global_knowledge_base_text
    text = (text or '').strip()
    if scope == SCOPE_COMPANY:
        save_global_knowledge_base_text(text, source)
        return
    apartment.knowledge_base = text or None
    apartment.save(update_fields=['knowledge_base', 'updated_at'])


def label(scope, apartment=None):
    return "the global knowledge base (all apartments)" if scope == SCOPE_COMPANY else \
        f"the {getattr(apartment, 'name', '?')} knowledge base"


def _kb_rules():
    from mysite.ai_agent import config, prompt_library
    return prompt_library.raw(config.AI_AGENT_KB_RULES_KEY) or '(none)'


def _complete(prompt):
    from mysite.ai_agent import config, oneshot
    return (oneshot.complete(prompt, model=config.oneshot_model()) or {}).get('text') or ''


def _section(raw, marker, next_markers):
    if marker not in raw:
        return None
    part = raw.split(marker, 1)[1]
    for other in next_markers:
        part = part.split(other, 1)[0]
    return part.strip()


def _diff(before, after):
    return "\n".join(difflib.unified_diff((before or '').splitlines(), (after or '').splitlines(),
                                          'before', 'after', lineterm='', n=0))


def _append(before, text):
    return f"{before.rstrip()}\n{text.strip()}".strip() if before.strip() else text.strip()


def merge(scope, apartment, text, replaces='', source=''):
    """
    Merges new information into the document and saves it. Returns (detail, diff).
    Falls back to appending the text as a new line when the merge result looks wrong.
    """
    from mysite.ai_agent import prompt_library

    before = document(scope, apartment)
    method = 'merged'
    try:
        raw = _complete(prompt_library.get(
            'ai_kb_merge', document_label=label(scope, apartment), knowledge_base=before or '(empty)',
            new_information=text, replaces=replaces or '(nothing)', source=source or '-', kb_rules=_kb_rules(),
        ))
        after = _section(raw, '[UPDATED KB]', ['[CHANGES]'])
        changes = _section(raw, '[CHANGES]', [])
    except Exception as e:
        log_warning(f"KB merge failed ({e}); appending instead", category='sms')
        after, changes = None, None
    lost = len(before) - len(after or '')
    if not after or (not replaces and before and lost > MAX_DROP_WITHOUT_REPLACES * len(before)):
        after, method, changes = _append(before, text), 'appended as a new line (the merge result was not usable)', None
    if after.strip() == before.strip():
        return "already in the knowledge base - nothing changed", ''
    save_document(scope, after, apartment, source=f"AI agent ({source})" if source else 'AI agent')
    diff = _diff(before, after)
    return f"{label(scope, apartment)} updated - {method}" + (f": {changes}" if changes else ''), diff


def generate_from_history(conversation):
    """
    Drafts updated apartment + global documents from a whole chat. Nothing is saved (the person reviews the
    drafts in the chat Knowledge Base window). Returns {'success', 'apartment_kb', 'global_kb', 'summary'}.
    """
    from mysite.ai_agent import inputs, prompt_library
    from mysite.models import TwilioMessage

    apartment = conversation.apartment
    messages = list(TwilioMessage.objects.filter(conversation=conversation).exclude(message_sid__startswith='KB-UPDATE-')
                    .order_by('message_timestamp', 'id'))
    if not messages:
        return {'success': False, 'error': 'This chat has no messages yet.'}
    chat = "\n".join(inputs.format_message_line(m) for m in messages)
    apartment_kb, global_kb = document(SCOPE_APARTMENT, apartment), document(SCOPE_COMPANY)
    raw = _complete(prompt_library.get(
        'ai_kb_from_history', apartment_name=getattr(apartment, 'name', '-'), apartment_kb=apartment_kb or '(empty)',
        global_kb=global_kb or '(empty)', chat=chat[-60000:], kb_rules=_kb_rules(),
    ))
    markers = ['[APARTMENT KB]', '[GLOBAL KB]', '[SUMMARY]']
    new_apartment = _section(raw, '[APARTMENT KB]', markers[1:])
    new_global = _section(raw, '[GLOBAL KB]', markers[2:])
    summary = _section(raw, '[SUMMARY]', [])
    if new_apartment is None or new_global is None:
        return {'success': False, 'error': 'The AI answer could not be read; nothing changed.'}
    return {
        'success': True, 'apartment_kb': new_apartment, 'global_kb': new_global,
        'summary': summary or '', 'messages_analyzed': len(messages),
    }


_CREDENTIAL = re.compile(r"\b(pass ?word|passcode|wi-?fi|ssid|network name|code|pin|lock ?box|keypad|alarm)\b", re.I)


def touches_credentials(text, replaces=''):
    from mysite.ai_agent.knowledge import looks_like_access_code
    joined = f"{text} {replaces}"
    return bool(_CREDENTIAL.search(joined)) or looks_like_access_code('', joined)
