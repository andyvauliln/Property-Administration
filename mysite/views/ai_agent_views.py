from pathlib import Path

from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render

from ..ai_agent import config as agent_config
from ..decorators import user_has_role
from ..models import AIEvent, AIRun, TwilioMessage

# Files of a run folder that may be shown in the browser, in display order
REPORT_FILES = (
    'report.md', '02_input.md', '05_steps.md', '06_output.json', '07_actions.json', '08_delivery.json',
    '01_system_prompt.md', '03_command.txt', '00_meta.json', 'stderr.txt', '04_transcript.jsonl',
)


def _run_dir(ai_run):
    """Report folder of the run, only when it really is inside logs/ai_runs/."""
    if not ai_run.report_dir:
        return None
    run_dir = Path(ai_run.report_dir).resolve()
    root = agent_config.RUNS_DIR.resolve()
    if root not in run_dir.parents or not run_dir.is_dir():
        return None
    return run_dir


@user_has_role('Admin', 'Manager')
def ai_runs_view(request):
    runs = AIRun.objects.select_related('message').all()
    conversation_sid = request.GET.get('conversation', '').strip()
    if conversation_sid:
        runs = runs.filter(conversation_sid=conversation_sid)
    if request.GET.get('errors'):
        runs = runs.exclude(error__isnull=True).exclude(error='')
    if request.GET.get('mode') in (AIRun.MODE_LIVE, AIRun.MODE_TEST):
        runs = runs.filter(mode=request.GET['mode'])

    totals = runs.aggregate(
        input_tokens=Sum('input_tokens'), output_tokens=Sum('output_tokens'),
        cache_read_tokens=Sum('cache_read_tokens'), cache_write_tokens=Sum('cache_write_tokens'),
        cost_usd=Sum('cost_usd'),
    )
    page = Paginator(runs, 50).get_page(request.GET.get('page', 1))
    return render(request, 'ai_runs.html', {
        'page': page,
        'totals': totals,
        'runs_count': runs.count(),
        'conversation_sid': conversation_sid,
        'pending_events': AIEvent.objects.filter(status__in=[AIEvent.STATUS_PENDING, AIEvent.STATUS_RUNNING]).count(),
        'ai_backend': agent_config.get_ai_backend(),
        'agent_model': agent_config.get_agent_model(),
    })


@user_has_role('Admin', 'Manager')
def ai_run_detail_view(request, run_id):
    ai_run = get_object_or_404(AIRun, id=run_id)
    run_dir = _run_dir(ai_run)
    files = [name for name in REPORT_FILES if run_dir and (run_dir / name).is_file()]

    selected = request.GET.get('file') or (files[0] if files else None)
    content = None
    if selected:
        if selected not in files:
            raise Http404("Unknown report file")
        content = (run_dir / selected).read_text(encoding='utf-8', errors='replace')

    return render(request, 'ai_run_detail.html', {
        'run': ai_run,
        'files': files,
        'selected': selected,
        'content': content,
        'report_missing': run_dir is None,
    })


@user_has_role('Admin', 'Manager')
def ai_agent_message_status(request, conversation_sid, message_id):
    """Polled by the chat page after a test message was queued for the ai-agent worker."""
    message = get_object_or_404(TwilioMessage, id=message_id, conversation_sid=conversation_sid)
    event = AIEvent.objects.filter(message=message).order_by('-id').first()
    ai_run = AIRun.objects.filter(message=message).order_by('-id').first()
    if ai_run is None and event is not None:
        # Several quick messages are answered by one run, linked to the newest event of the batch
        ai_run = AIRun.objects.filter(
            conversation_sid=conversation_sid, event_id__gte=event.id,
        ).order_by('id').first()
    status = event.status if event else 'none'
    return JsonResponse({
        'status': status,
        'finished': status in (AIEvent.STATUS_DONE, AIEvent.STATUS_FAILED, AIEvent.STATUS_SKIPPED, 'none'),
        'error': (event.error if event else None) or (ai_run.error if ai_run else None),
        'message_id': message.id,
        'ai_response': message.ai_response,
        'ai_response_why': message.ai_response_why,
        'ai_sent_to_chat': message.ai_sent_to_chat,
        'run': {
            'id': ai_run.id,
            'mode': ai_run.mode,
            'actions': len(ai_run.actions or []),
            'tokens': ai_run.total_tokens,
            'cost_usd': float(ai_run.cost_usd or 0),
            'url': f'/ai-runs/{ai_run.id}/',
        } if ai_run else None,
    })
