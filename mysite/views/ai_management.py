from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.paginator import Paginator
import json
from datetime import date
from ..models import AIManagement
from ..forms import AIManagementForm
from .utils import handle_post_request, get_related_fields, parse_query, get_model_fields, DateEncoder
from ..decorators import user_has_role

def serialize_field(value):
    from datetime import datetime, date
    if isinstance(value, (datetime, date)):
        return value.strftime('%B %d %Y')
    elif hasattr(value, 'id'):
        return value.id
    elif isinstance(value, list):
        return [serialize_field(v) for v in value]
    elif isinstance(value, dict):
        return {k: serialize_field(v) for k, v in value.items()}
    return value

@user_has_role('Admin', 'Manager')
def ai_management_view(request):
    search_query = request.GET.get('q', '')
    page = request.GET.get('page', 1)
    pages = 30

    form_class = AIManagementForm
    model = AIManagement

    if request.method == 'POST':
        return handle_post_request(request, model, form_class)

    fk_or_o2o_fields, m2m_fields = get_related_fields(model)
    # Section "Settings & notifications": prompts have their own section, the global KB its own editor
    from mysite.ai_agent import prompt_library
    from .messaging import GLOBAL_KB_KEY
    items = (model.objects.select_related(*fk_or_o2o_fields).prefetch_related(*m2m_fields)
             .exclude(prompt_key__in=[*prompt_library.BY_KEY, GLOBAL_KB_KEY]))

    if search_query:
        q_objects = parse_query(model, search_query)
        items = items.filter(q_objects)

    items = items.order_by('-id')
    
    paginator = Paginator(items, pages)
    items_on_page = paginator.get_page(page)

    items_list = []
    for original_obj in items_on_page:
        item = {field.name: serialize_field(getattr(original_obj, field.name)) for field in original_obj._meta.fields}
        item['id'] = original_obj.id
        item['links'] = serialize_field(original_obj.links)
        items_list.append(item)

    items_json = json.dumps(items_list, cls=DateEncoder)
    form = form_class(request=request)
    model_fields = get_model_fields(form)

    prompt_groups, ai_backend = _prompt_cards()
    from ..models import Apartment
    from .messaging import get_global_knowledge_base_text
    context = {
        'prompt_groups': prompt_groups,
        'global_kb': get_global_knowledge_base_text(),
        'apartment_kbs': _apartment_kbs(),
        'ai_backend': ai_backend,
        'preview_apartments': Apartment.objects.order_by('name').values('id', 'name'),
        'items': items_on_page,
        'items_json': items_json,
        'search_query': search_query,
        'model_fields': model_fields,
        'title': 'AI Management',
        'ai_models_in_use': _models_in_use(),
    }

    return render(request, 'ai_management.html', context)


# ---------------------------------------------------------------------------
# Prompts: every AI prompt of the registry (mysite/ai_agent/prompt_library.py), documented and editable
# ---------------------------------------------------------------------------

def _models_in_use():
    from mysite.ai_agent import clickup, config as ai_config
    return [
        {'label': 'Agent (tenant chats)', 'value': ai_config.get_agent_model(), 'how': "row 'ai_agent_model' below, else env AI_AGENT_MODEL"},
        {'label': 'Chat-page helpers and knowledge-base merges', 'value': ai_config.oneshot_model(), 'how': 'env AI_AGENT_ONESHOT_MODEL, else the agent model'},
        {'label': 'Telegram reply interpreter', 'value': ai_config.review_model(), 'how': 'env AI_AGENT_REVIEW_MODEL, else the agent model'},
        {'label': 'ClickUp delivery via Claude', 'value': clickup.DELIVERY_MODEL, 'how': 'env AI_AGENT_CLICKUP_DELIVERY_MODEL'},
    ]


def _apartment_kbs():
    """Every apartment with its knowledge-base document (the ones that have one first)."""
    from ..models import Apartment
    rows = [{'id': a.id, 'name': a.name, 'kb': (a.knowledge_base or '').strip()}
            for a in Apartment.objects.order_by('name').only('id', 'name', 'knowledge_base')]
    rows.sort(key=lambda r: (not r['kb'], r['name']))
    return rows


@user_has_role('Admin', 'Manager')
def ai_knowledge_base_save(request, apartment_id=None):
    """POST {content}: saves the global knowledge base (no apartment_id) or one apartment's knowledge base."""
    from django.http import JsonResponse
    from ..models import Apartment
    from .messaging import save_global_knowledge_base_text

    if request.method != 'POST':
        return JsonResponse({'error': 'POST only.'}, status=405)
    try:
        content = str(json.loads(request.body or '{}').get('content') or '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)
    if apartment_id is None:
        save_global_knowledge_base_text(content)
        return JsonResponse({'success': True, 'length': len(content)})
    apartment = Apartment.objects.filter(id=apartment_id).first()
    if not apartment:
        return JsonResponse({'error': 'Unknown apartment.'}, status=404)
    apartment.knowledge_base = content or None
    apartment.save(update_fields=['knowledge_base', 'updated_at'])
    return JsonResponse({'success': True, 'length': len(content)})


def _prompt_cards():
    """Registry prompts grouped for the AI Management page, with their live state."""
    from mysite.ai_agent import config as ai_config, prompt_library as lib

    backend = ai_config.get_ai_backend()
    rows = {e.prompt_key: e for e in AIManagement.objects.filter(prompt_key__in=list(lib.BY_KEY))}
    groups = {}
    for s in lib.SPECS:
        entry = rows.get(s.key)
        content = (entry.content or '').strip() if entry else ''
        card = {
            'key': s.key, 'name': s.name, 'what': s.what, 'how': s.how, 'when': s.when, 'model': s.model,
            'placeholders': list(s.placeholders.items()), 'kind': s.kind,
            'in_db': entry is not None, 'entry_id': entry.id if entry else None,
            'edited': lib.is_edited(s.key, entry) if entry else False,
            'updated_at': entry.updated_at if entry else None,
            'updated_by': (entry.last_updated_by or entry.created_by) if entry else None,
            'length': len(content),
            'rule_count': sum(1 for line in content.splitlines() if line.strip().startswith('-')) if s.kind == lib.KIND_RULES else None,
        }
        groups.setdefault(s.group, []).append(card)
    return [(group, groups[group]) for group in lib.GROUP_ORDER if group in groups], backend


@user_has_role('Admin', 'Manager')
def ai_prompt_detail(request, prompt_key):
    """GET: the live text, default and docs of one prompt. POST {content}: saves it."""
    import difflib
    from django.http import JsonResponse
    from mysite.ai_agent import prompt_library as lib

    if prompt_key not in lib.BY_KEY:
        return JsonResponse({'error': 'Unknown prompt key.'}, status=404)
    s = lib.spec(prompt_key)
    if request.method == 'POST':
        try:
            payload = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)
        content = payload.get('content')
        if content is None:
            return JsonResponse({'error': 'content is required.'}, status=400)
        if s.kind == lib.KIND_TEXT and not content.strip():
            return JsonResponse({'error': 'This prompt cannot be empty (use Reset to default instead).'}, status=400)
        lib.save(prompt_key, content)
        return JsonResponse({'success': True, 'missing_placeholders': lib.missing_placeholders(prompt_key, content)})

    content = lib.raw(prompt_key)
    default = s.default_text()
    diff = "\n".join(difflib.unified_diff(default.splitlines(), content.splitlines(), 'built-in default', 'live (DB)',
                                          lineterm='', n=2))
    return JsonResponse({
        'key': s.key, 'name': s.name, 'content': content, 'default': default, 'diff': diff,
        'edited': content != default, 'kind': s.kind, 'placeholders': s.placeholders,
        'missing_placeholders': lib.missing_placeholders(prompt_key, content),
    })


@user_has_role('Admin', 'Manager')
def ai_prompt_reset(request, prompt_key):
    from django.http import JsonResponse
    from mysite.ai_agent import prompt_library as lib

    if request.method != 'POST':
        return JsonResponse({'error': 'POST only.'}, status=405)
    if prompt_key not in lib.BY_KEY:
        return JsonResponse({'error': 'Unknown prompt key.'}, status=404)
    entry, _ = lib.seed(prompt_key, force=True)
    return JsonResponse({'success': True, 'content': entry.content or ''})


@user_has_role('Admin', 'Manager')
def ai_prompt_preview(request):
    """The exact system prompt the Claude agent gets for a chat of the chosen apartment, part by part."""
    from django.http import JsonResponse
    from mysite.ai_agent import prompt_library as lib, prompts
    from ..models import Apartment

    apartment = None
    if request.GET.get('apartment'):
        apartment = Apartment.objects.filter(id=request.GET['apartment']).first()
    parts = prompts.system_prompt_parts(apartment)
    full = "\n\n".join(text for _key, text in parts)
    return JsonResponse({
        'apartment': getattr(apartment, 'name', None),
        'parts': [{'key': key, 'name': lib.spec(key).name, 'text': text, 'length': len(text)} for key, text in parts],
        'length': len(full),
    })
