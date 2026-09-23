"""
One readable notification per AI run, instead of one message per action.

Telegram: the whole run (incoming text once, answer, why, what the team has to do grouped by issue,
other actions in one line, link to the report). ClickUp (mapped apartments only): tasks for tickets +
one channel message with the team part.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from mysite.ai_agent import clickup, config
from mysite.ai_agent.notify import activity_enabled, notify_ai_chat

RANK = {'routine': 0, 'urgent': 1, 'emergency': 2}
HEADER_ICON = {'emergency': '🚨 EMERGENCY', 'urgent': '🔴 URGENT', 'routine': '🔔'}
NOTIFY_TYPES = ('INTERNAL_ALERT', 'QUEUE_FOR_REVIEW', 'CREATE_TICKET')

# StaffMember.ai_name values always added as ClickUp task assignees, on top of whoever the task would
# already go to (user request 2026-09-22).
ALWAYS_CLICKUP_TEST_ASSIGNEES = ('Farid',)               # test apartment channel (shared test List)
ALWAYS_CLICKUP_LIVE_ASSIGNEES = ('Farid', 'Janna', 'Edy')  # live apartment channels, alongside the responsible person
# Kevin (StaffMember, ClickUp user 118026268 / "Imie Malaay") is NOT in ALWAYS_CLICKUP_LIVE_ASSIGNEES yet:
# he does not have access to the "Local Portfolio" ClickUp space that holds the apartment Lists (verified
# 2026-09-22 - assigning him fails the whole task-creation call with "All assignees must have access to
# this task", which would break every live-apartment ClickUp task, not just his assignment). Add "Kevin"
# to the tuple above once he's been added to that space in ClickUp.

# Tags put on every AI-created ClickUp task (user request 2026-09-22).
AI_TASK_TAGS = ('ai', 'maintenance')


def _now_label():
    return f"{datetime.now(ZoneInfo(config.TEAM_TIMEZONE)).strftime('%b %d %H:%M')} {config.TIMEZONE_LABEL}"


def report_url(run_id):
    return f"{config.site_url()}/ai-runs/{run_id}/"


def alert_groups(alerts):
    """Alerts of one run grouped by issue: one block per issue, highest priority, one text, ticket title."""
    from mysite.ai_agent.actions import staff_label

    groups = {}
    for item in alerts:
        action, issue = item['action'], item['issue']
        key = issue.id if issue else f"none-{len(groups)}"
        group = groups.setdefault(key, {
            'issue': issue, 'priority': 'routine', 'responsible': [], 'text': '', 'ticket_title': None,
            'queued': False, 'wants_task': False, 'task_url': None,
        })
        priority = action.get('priority') if action.get('priority') in RANK else (issue.priority if issue else 'routine')
        if RANK[priority] > RANK[group['priority']]:
            group['priority'] = priority
        owners = action.get('responsible') or action.get('owner') or (issue.owner if issue else None) or []
        for owner in ([owners] if isinstance(owners, str) else owners):
            if owner not in group['responsible']:
                group['responsible'].append(owner)
        if action['type'] == 'CREATE_TICKET':
            group['ticket_title'] = str(action.get('title') or (issue.summary if issue else 'AI ticket'))
            group['wants_task'] = True
            group['text'] = group['text'] or str(action.get('description') or '')
        else:
            group['queued'] = group['queued'] or action['type'] == 'QUEUE_FOR_REVIEW'
            group['text'] = str(action.get('text') or '') or group['text']   # the alert text is written for staff
    for group in groups.values():
        group['for'] = staff_label(group['responsible']) or 'team'
    return sorted(groups.values(), key=lambda g: -RANK[g['priority']])


def _team_lines(groups):
    lines = []
    for group in groups:
        issue = group['issue']
        head = f"• {group['priority'].upper()}" + (f" · {issue.public_id}" if issue else "") + f" · for {group['for']}"
        if group['queued']:
            head += " · queued for review"
        lines.append(head)
        if issue:
            lines.append(f"  {issue.summary}")
        if group['ticket_title']:
            lines.append(f"  🎫 {group['ticket_title']}" + (f" → {group['task_url']}" if group['task_url'] else ""))
        if group['text'] and (not issue or group['text'].strip() != issue.summary.strip()):
            lines.append(f"  {group['text'][:600]}")
    return lines


def _other_actions_line(action_results):
    counts, problems = {}, []
    for item in action_results:
        action = item.get('action') if isinstance(item.get('action'), dict) else {}
        kind = action.get('type') or '?'
        if item.get('status') == 'rejected':
            problems.append(f"✗ {kind}: {str(item.get('detail'))[:140]}")
        elif kind not in NOTIFY_TYPES:
            counts[kind] = counts.get(kind, 0) + 1
    names = {'CREATE_ISSUE': 'issue opened', 'UPDATE_ISSUE_STATE': 'issue updated', 'SCHEDULE_FOLLOWUP': 'reminder set',
             'CANCEL_FOLLOWUP': 'reminder cancelled', 'CASE_NOTE': 'case note', 'TICKET_COMMENT': 'ticket note',
             'UPDATE_TICKET': 'ticket update', 'KB_UPDATE': 'knowledge update'}
    parts = [f"{n}× {names.get(k, k)}" if n > 1 else names.get(k, k) for k, n in counts.items()]
    return parts, problems


def compose_telegram(meta, parsed, action_results, delivery, trigger_text, groups, ai_run):
    live = meta.get('mode') == 'live'
    sent = bool((delivery or {}).get('sent_to_chat'))
    top = groups[0]['priority'] if groups else None
    header = (HEADER_ICON[top] if top else '💬') + f" {meta.get('apartment')} · {meta.get('tenant') or '-'}"
    lines = [
        header,
        f"{'🟢 LIVE' if live else '🧪 TEST - the AI did NOT answer the tenant'} · {meta.get('event_type')} · {_now_label()}",
        "",
        f"▶ {(trigger_text or '').strip()[:1000]}",
        "",
    ]
    if parsed.get('answer'):
        how = 'sent to the tenant' if sent else 'NOT sent: ' + ((delivery or {}).get('note') or '')
        lines.append(f"🤖 AI answer ({how}):\n{parsed['answer'][:900]}")
    else:
        lines.append("🤖 AI: no answer to the tenant")
    if parsed.get('review_answer'):
        lines.append(f"📝 Would have said (staff answered first, never sent):\n{parsed['review_answer'][:600]}")
    if parsed.get('why'):
        lines.append(f"💡 {parsed['why'][:500]}")
    if groups:
        lines += ["", "👤 FOR THE TEAM:"] + _team_lines(groups)
    parts, problems = _other_actions_line(action_results)
    kb = [
        f"📚 {i['action'].get('key')} = {str(i['action'].get('value'))[:120]} ({str(i.get('detail'))[:90]})"
        for i in action_results if isinstance(i.get('action'), dict) and i['action'].get('type') == 'KB_UPDATE' and i.get('status') != 'rejected'
    ]
    if parts:
        lines += ["", "⚙ " + " · ".join(parts)]
    lines += kb + problems
    lines += ["", f"🔗 {report_url(ai_run.id)}"]   # cost and tokens stay in the report and on /ai-runs/
    return "\n".join(lines)


def compose_clickup(meta, groups, trigger_text, ai_run):
    live = meta.get('mode') == 'live'
    lines = [
        ("" if live else "🧪 **TEST MODE - the AI did NOT answer the tenant**\n")
        + f"**{meta.get('apartment')} · {meta.get('tenant') or '-'}**",
        "", f"> {(trigger_text or '').strip()[:800]}", "",
    ] + _team_lines(groups) + ["", f"Report: {report_url(ai_run.id)}"]
    return "\n".join(lines)


def _with_unit(title, unit):
    """All apartments share one List during tests, so the task name must say which unit it is about."""
    return title if not unit or str(unit).lower() in title.lower() else f"{unit} · {title}"


def _deliver_clickup(ctx, groups, trigger_text, ai_run):
    """Tasks for tickets + one channel message. Returns a note; never raises."""
    from django.db.models import Q

    from mysite.models import StaffMember

    mapping = clickup.channel_for(ctx.apartment)
    if not mapping or not groups:
        return ''
    mode = clickup.delivery_mode()
    if mode == 'off':
        return 'ClickUp: skipped (no CLICKUP_API_TOKEN and AI_AGENT_CLICKUP_VIA_CLAUDE=off)'

    tasks = []
    for group in groups:
        issue = group['issue']
        if not (group['wants_task'] and mapping.list_id) or (issue and issue.ticket_ref):
            group['task_url'] = issue.ticket_ref if issue and issue.ticket_ref else None
            continue
        # Tasks are always created, in test mode too (decision 2026-09-21).
        # Test mode or the shared test List -> assigned to the engineering staff member (Andrei) + Farid.
        # Live on the apartment's own List -> the responsible person (e.g. Edy -> Farouk's ClickUp user),
        # plus Farid, Janna and Edy always (user request 2026-09-22).
        testing = ctx.is_test or bool(clickup.test_list_id())
        staff = StaffMember.objects.filter(is_active=True).exclude(clickup_user_id__isnull=True).exclude(clickup_user_id='')
        if testing:
            staff = staff.filter(Q(role=StaffMember.ROLE_ENGINEERING) | Q(ai_name__in=ALWAYS_CLICKUP_TEST_ASSIGNEES))
        else:
            staff = staff.filter(Q(ai_name__in=group['responsible']) | Q(ai_name__in=ALWAYS_CLICKUP_LIVE_ASSIGNEES))
        assignees = list(staff.values_list('clickup_user_id', flat=True))
        # Every AI task is marked as AI-made in its name, tagged, and gets a priority-based due date (user request 2026-09-22).
        tasks.append({
            'group': group,
            'name': '[AI] ' + ('[TEST] ' if ctx.is_test else '') + _with_unit(group['ticket_title'], ctx.meta.get('apartment')),
            'description': f"{group['text']}\n\nTenant: {ctx.meta.get('tenant')}\n{(trigger_text or '')[:1500]}\n\nReport: {report_url(ai_run.id)}",
            'priority': group['priority'], 'assignees': assignees, 'due_at': clickup.default_due_at(group['priority']),
            'tags': AI_TASK_TAGS,
        })
    try:
        if mode == 'api':
            for task in tasks:
                task_id, task_url = clickup.create_task(
                    mapping.list_id, task['name'], task['description'], task['priority'], task['assignees'], task['due_at'],
                    tags=task['tags'],
                )
                task['group']['task_url'] = task_url or task_id
            if mapping.channel_id:
                clickup.post_message(mapping.channel_id, compose_clickup(ctx.meta, groups, trigger_text, ai_run))
            note = f"ClickUp (API): {len(tasks)} task(s)" + (", channel message" if mapping.channel_id else ", no channel id")
        else:
            result = clickup.deliver_via_claude(
                mapping, tasks, lambda: compose_clickup(ctx.meta, groups, trigger_text, ai_run),
            )
            note = f"ClickUp (via Claude Code connection): {result}"
        for task in tasks:
            issue = task['group']['issue']
            if issue and task['group'].get('task_url'):
                issue.ticket_ref = task['group']['task_url']
                issue.save()
        return note
    except clickup.ClickUpError as e:
        return f"ClickUp FAILED ({e})"


def deliver(ctx, ai_run, parsed, action_results, delivery, trigger_text):
    """Sends the run's notifications. Returns a short note and writes it into the alert actions' details."""
    groups = alert_groups(ctx.alerts)
    clickup_note = _deliver_clickup(ctx, groups, trigger_text, ai_run) if ctx.notify else ''
    telegram_note = ''
    if ctx.notify and (groups or activity_enabled()):
        ok, telegram_note = notify_ai_chat(compose_telegram(ctx.meta, parsed, action_results, delivery, trigger_text, groups, ai_run))
        telegram_note = f"Telegram: {'sent' if ok else 'FAILED - ' + telegram_note}"
    note = "; ".join(n for n in (telegram_note, clickup_note) if n)
    delivered = 'Telegram: sent' in note or note.count('ClickUp (') and 'FAILED' not in clickup_note
    for item in action_results:
        action = item.get('action') if isinstance(item.get('action'), dict) else {}
        if action.get('type') in NOTIFY_TYPES and item.get('status') == 'executed':
            item['detail'] = f"{item.get('detail')}; {note}"
            if groups and not delivered:
                item['status'] = 'rejected'
    return note
