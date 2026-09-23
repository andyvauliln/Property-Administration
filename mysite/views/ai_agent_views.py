from pathlib import Path

from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..ai_agent import config as agent_config
from ..decorators import user_has_role
from ..models import AICaseNote, AIEvent, AIFollowUp, AIIssue, AIKnowledge, AIRun, StaffMember, TwilioMessage

# Files of a run folder that may be shown in the browser, in display order
REPORT_FILES = (
    'report.md', '02_input.md', '05_steps.md', '06_output.json', '07_actions.json', '08_delivery.json',
    '01_system_prompt.md', '03_command.txt', '00_meta.json', 'stderr.txt', '04_transcript.jsonl',
)


def _safe_next(request, default):
    """Only local paths are accepted as a redirect target."""
    target = request.POST.get('next') or ''
    return target if target.startswith('/') and not target.startswith('//') else default


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


def get_ai_activity(conversation_sid):
    """What the AI agent is tracking in one chat (issues, timers, notes). None when there is nothing."""
    try:
        issues = AIIssue.objects.filter(conversation_sid=conversation_sid)
        open_issues = list(issues.exclude(state=AIIssue.STATE_RESOLVED).order_by('id'))
        resolved = list(issues.filter(state=AIIssue.STATE_RESOLVED).order_by('-resolved_at')[:5])
        followups = list(AIFollowUp.objects.filter(
            conversation_sid=conversation_sid, status=AIFollowUp.STATUS_PENDING,
        ).select_related('issue').order_by('due_at'))
        notes = list(AICaseNote.objects.filter(conversation_sid=conversation_sid).order_by('-id')[:8])
        runs = list(AIRun.objects.filter(conversation_sid=conversation_sid).order_by('-id')[:5])
        kb_candidates = AIKnowledge.objects.filter(
            conversation_sid=conversation_sid, status=AIKnowledge.STATUS_ACTIVE, confidence=AIKnowledge.CONFIDENCE_CANDIDATE,
        ).count()
    except Exception:
        return None  # tables not migrated yet
    if not (open_issues or resolved or followups or notes or runs or kb_candidates):
        return None
    return {'open_issues': open_issues, 'resolved_issues': resolved, 'followups': followups, 'notes': notes,
            'runs': runs, 'kb_candidates': kb_candidates}


@user_has_role('Admin', 'Manager')
@require_http_methods(["POST"])
def ai_issue_resolve(request, issue_id):
    """A manager closes an issue by hand; its pending timers stop, so the AI stops reminding."""
    issue = get_object_or_404(AIIssue, id=issue_id)
    if issue.is_open:
        issue.state = AIIssue.STATE_RESOLVED
        issue.resolved_at = timezone.now()
        issue.save()
        issue.followups.filter(status=AIFollowUp.STATUS_PENDING).update(
            status=AIFollowUp.STATUS_CANCELLED, status_note='issue resolved by a manager', updated_at=timezone.now(),
        )
    return redirect(_safe_next(request, f'/chat/{issue.conversation_sid}/'))


@user_has_role('Admin', 'Manager')
def ai_issues_view(request):
    issues = AIIssue.objects.select_related('apartment', 'booking__tenant').prefetch_related('followups')
    if request.GET.get('all'):
        title = 'All AI issues'
    else:
        issues = issues.exclude(state=AIIssue.STATE_RESOLVED)
        title = 'Open AI issues'
    page = Paginator(issues.order_by('-id'), 50).get_page(request.GET.get('page', 1))
    return render(request, 'ai_issues.html', {
        'page': page, 'title': title, 'show_all': bool(request.GET.get('all')),
        'pending_followups': AIFollowUp.objects.filter(status=AIFollowUp.STATUS_PENDING).count(),
    })


@user_has_role('Admin')
def ai_staff_view(request):
    """Who is Edy / Kevin / Janna: phone identifies them in the tenant chat, ClickUp id is for routing."""
    error = None
    if request.method == 'POST':
        member = get_object_or_404(StaffMember, id=request.POST['id']) if request.POST.get('id') else StaffMember()
        try:
            member.ai_name = (request.POST.get('ai_name') or '').strip()
            member.full_name = (request.POST.get('full_name') or '').strip() or None
            member.role = request.POST.get('role') or StaffMember.ROLE_STAFF
            member.phone = (request.POST.get('phone') or '').strip() or None
            member.secondary_phone = (request.POST.get('secondary_phone') or '').strip() or None
            member.clickup_user_id = (request.POST.get('clickup_user_id') or '').strip() or None
            member.telegram_username = (request.POST.get('telegram_username') or '').strip() or None
            member.is_active = bool(request.POST.get('is_active'))
            if not member.ai_name:
                raise ValueError("AI name is required")
            member.save()
            return redirect('/ai-staff/')
        except Exception as e:
            error = str(e)
    return render(request, 'ai_staff.html', {
        'members': StaffMember.objects.all(), 'roles': StaffMember.ROLE_CHOICES, 'error': error,
    })


@user_has_role('Admin', 'Manager')
def ai_knowledge_view(request):
    """What the AI agent learned. Candidates wait here until a manager approves or rejects them."""
    from ..ai_agent import knowledge

    if request.method == 'POST':
        entry = get_object_or_404(AIKnowledge, id=request.POST.get('id'))
        reviewer = getattr(request.user, 'full_name', None) or str(request.user)
        action = request.POST.get('action')
        new_value = (request.POST.get('value') or '').strip()
        if action in ('approve', 'save') and new_value and new_value != entry.value:
            entry.value = new_value
            entry.is_access_code = knowledge.looks_like_access_code(entry.key, new_value)
        if action == 'approve':
            knowledge.approve(entry, reviewer)
        elif action == 'reject':
            knowledge.reject(entry, reviewer)
        if action in ('approve', 'reject'):
            from ..ai_agent.notify import notify_knowledge_review
            notify_knowledge_review(entry, 'APPROVED' if action == 'approve' else 'REJECTED', reviewer)
        elif action == 'save':
            entry.save()
        return redirect(_safe_next(request, '/ai-knowledge/'))

    show = request.GET.get('show', 'candidates')
    entries = AIKnowledge.objects.select_related('apartment')
    if show == 'candidates':
        entries = entries.filter(status=AIKnowledge.STATUS_ACTIVE, confidence=AIKnowledge.CONFIDENCE_CANDIDATE)
    elif show == 'verified':
        entries = entries.filter(status=AIKnowledge.STATUS_ACTIVE, confidence=AIKnowledge.CONFIDENCE_VERIFIED)
    if request.GET.get('apartment'):
        entries = entries.filter(apartment_id=request.GET['apartment'])
    page = Paginator(entries.order_by('-id'), 50).get_page(request.GET.get('page', 1))
    return render(request, 'ai_knowledge.html', {
        'page': page, 'show': show,
        'candidates_count': AIKnowledge.objects.filter(
            status=AIKnowledge.STATUS_ACTIVE, confidence=AIKnowledge.CONFIDENCE_CANDIDATE).count(),
        'verified_count': AIKnowledge.objects.filter(
            status=AIKnowledge.STATUS_ACTIVE, confidence=AIKnowledge.CONFIDENCE_VERIFIED).count(),
    })
