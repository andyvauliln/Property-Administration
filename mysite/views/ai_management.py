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
        # Handle AI Model update specifically if it's in the POST
        if 'update_ai_model' in request.POST:
            model_name = request.POST.get('ai_model')
            if model_name:
                AIManagement.objects.update_or_create(
                    prompt_key='ai_conversation_model',
                    defaults={
                        'name': 'AI Conversation Model',
                        'content': model_name,
                        'entry_type': 'ai_model',
                        'description': 'The model used for AI conversations'
                    }
                )
                messages.success(request, f"AI Model updated to {model_name}")
            return redirect('ai_management')
        
        return handle_post_request(request, model, form_class)

    fk_or_o2o_fields, m2m_fields = get_related_fields(model)
    items = model.objects.select_related(*fk_or_o2o_fields).prefetch_related(*m2m_fields)

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

    # Get current AI model
    current_model_obj = AIManagement.objects.filter(prompt_key='ai_conversation_model').first()
    current_model = current_model_obj.content if current_model_obj else 'openai/gpt-4o-mini'

    def _format_context_length(n):
        if n is None:
            return None
        try:
            n = int(n)
            if n >= 1_000_000:
                return f"{n // 1_000_000}M"
            if n >= 1000:
                return f"{n // 1000}k"
            return str(n)
        except (ValueError, TypeError):
            return None

    # Fetch models from OpenRouter API
    openrouter_models = []
    try:
        import requests
        import os
        api_key = os.environ.get('OPENROUTER_API_KEY')
        if api_key:
            response = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            if response.status_code == 200:
                data = response.json()
                for m in data.get('data', []):
                    pricing = m.get('pricing', {})
                    prompt_price = float(pricing.get('prompt', 0)) * 1000000
                    completion_price = float(pricing.get('completion', 0)) * 1000000
                    ctx = m.get('context_length')
                    openrouter_models.append({
                        'value': m.get('id'),
                        'label': m.get('name'),
                        'prompt_price': f"{prompt_price:.2f}",
                        'completion_price': f"{completion_price:.2f}",
                        'context_length': ctx,
                        'context_length_display': _format_context_length(ctx),
                    })
                # Sort by label
                openrouter_models.sort(key=lambda x: x['label'])
    except Exception as e:
        print(f"Error fetching OpenRouter models: {e}")

    # Fallback/Static AI Model options if API fails or for core models
    ai_models = [
        {'value': 'openai/gpt-4o', 'label': 'GPT-4o', 'context_length_display': '128k'},
        {'value': 'openai/gpt-4o-mini', 'label': 'GPT-4o Mini', 'context_length_display': '128k'},
        {'value': 'openai/o1-preview', 'label': 'O1 Preview', 'context_length_display': '128k'},
        {'value': 'openai/o1-mini', 'label': 'O1 Mini', 'context_length_display': '128k'},
        {'value': 'anthropic/claude-3.5-sonnet', 'label': 'Claude 3.5 Sonnet', 'context_length_display': '200k'},
    ]
    
    # Use OpenRouter models if available
    if openrouter_models:
        ai_models = openrouter_models

    context = {
        'items': items_on_page,
        'items_json': items_json,
        'search_query': search_query,
        'model_fields': model_fields,
        'title': 'AI Management',
        'current_ai_model': current_model,
        'ai_models': ai_models,
    }

    return render(request, 'ai_management.html', context)
