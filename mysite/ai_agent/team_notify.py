"""
One readable notification per AI run, instead of one message per action.

Telegram: the whole run (incoming text once, answer, why, what the team has to do grouped by issue,
other actions in one line, link to the report). ClickUp (mapped apartments only): tasks for tickets +
one channel message with the team part.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from mysite.ai_agent import clickup, config
from mysite.ai_agent.notify import activity_enabled, send_ai_chat

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


def task_details(ticket_ref, cache=None):
    """Lines with the live state of an existing ClickUp task (status, assignees, due, last comment). Never raises."""
    cache = {} if cache is None else cache
    if ticket_ref not in cache:
        try:
            state = clickup.get_task_state(ticket_ref) if clickup.delivery_mode() == 'api' else None
        except clickup.ClickUpError as e:
            state = {'error': str(e)[:120]}
        cache[ticket_ref] = state
    state = cache[ticket_ref]
    if not state:
        return []
    if state.get('error'):
        return [f"     (could not read the task from ClickUp: {state['error']})"]
    lines = [f"     Status: {state['status']}{' (CLOSED)' if state['closed'] else ''} · assigned: "
             f"{', '.join(state['assignees']) or 'nobody'}" + (f" · due {state['due']}" if state.get('due') else "")
             + (f" · last change {state['updated']}" if state.get('updated') else "")]
    if state['comments']:
        last = state['comments'][-1]
        lines.append(f"     Last comment ({last['user']}, {last['when']}): {last['text'][:200]}")
    return lines


def _team_lines(groups, cache=None):
    lines = []
    for group in groups:
        issue = group['issue']
        head = f"• {group['priority'].upper()}" + (f" · {issue.public_id}" if issue else "") + f" · for {group['for']}"
        if group['queued']:
            head += " · queued for review"
        lines.append(head)
        if issue:
            lines.append(f"  {issue.summary}")
        if group['ticket_title'] and group.get('task_created'):
            lines.append(f"  🎫 ClickUp task CREATED: {group['task_url']}")
        elif group['task_url']:
            lines.append(f"  🎫 EXISTING ClickUp task (no new one): {group['ticket_title'] or (issue.ticket_title if issue else '')}")
            lines.append(f"     🔗 {group['task_url']}")
            lines += task_details(group['task_url'], cache)
        elif group.get('task_would_be'):
            lines.append(f"  🎫 Would CREATE ClickUp task \"{group['task_would_be']}\" - NOT created: ClickUp writes are OFF")
        elif group['ticket_title'] and group.get('no_list'):
            lines.append(f"  🎫 {group['ticket_title']} - NO ClickUp task: this apartment has no ClickUp List (Telegram only)")
        elif group['ticket_title']:
            lines.append(f"  🎫 {group['ticket_title']}")
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


def compose_telegram(meta, parsed, action_results, delivery, trigger_text, groups, ai_run, ticket_updates=(),
                     plan_items=None, plan_actions=None):
    """
    plan_items given (staff review on): NOTHING has been done yet - the alert lists the answer and every planned
    change with the time they happen. Otherwise (emergency / review off): what was done.
    """
    from mysite.ai_agent import plan as plan_mod

    live = meta.get('mode') == 'live'
    sent = bool((delivery or {}).get('sent_to_chat'))
    top = (plan_mod.top_priority(plan_items, plan_actions) if plan_items is not None else
           (groups[0]['priority'] if groups else None))
    header = (HEADER_ICON[top] if top else '💬') + f" {meta.get('apartment')} · {meta.get('tenant') or '-'}"
    lines = [
        header,
        f"{'🟢 LIVE' if live else '🧪 TEST - nothing is sent to the tenant'} · {meta.get('event_type')} · {_now_label()}",
    ]
    if meta.get('tenant_chats'):
        # The tenant writes in more than one chat: say which one this is and where they wrote last
        lines.append(f"🔗 {meta['tenant_chats']}")
    if not clickup.writes_enabled(meta.get('apartment')):
        lines.append("🚫 ClickUp writes OFF - ClickUp tasks/comments/closes below are only what the AI WOULD do")
    lines += [
        "",
        f"▶ {(trigger_text or '').strip()[:1000]}",
        "",
    ]
    held = bool((delivery or {}).get('held'))
    # Legal / contract question: the answer is a suggestion that is sent ONLY after a manager confirms it
    confirm = held and bool((delivery or {}).get('confirm'))
    changes = plan_mod.render(plan_items) if plan_items is not None else []
    automatic = plan_items is not None and (held or changes)
    if confirm:
        lines.insert(2, "⚖️ LEGAL QUESTION - NEEDS MANAGER CONFIRMATION. Nothing is sent to the tenant until a manager "
                        "replies \"ok\" or a corrected answer - NOT even after the "
                        f"{config.review_hold_minutes():g}-min review window.")
    if automatic:
        # First thing people see (also in the Telegram preview): it will happen by itself
        lines.insert(3 if confirm else 2,
                     f"⏰ AUTOMATIC at {_eta(delivery)} ({_minutes_left(delivery)}): if nobody replies to this message, "
                     f"the AI does everything in the PLAN below by itself" + (" - except the legal answer." if confirm else "."))
        lines.append(f"📋 PLAN - NOTHING IS DONE YET. At {_eta(delivery)} ({_minutes_left(delivery)}), unless someone replies to this message:")
        if confirm:
            lines.append("• ⚖️ SUGGESTED answer based on the contract - NOT sent automatically, only after a manager replies "
                         "\"ok\" (send as written) or a corrected answer:")
            lines.append(f"   \"{parsed['answer'][:900]}\"")
        elif held:
            lines.append("• 💬 " + ("Send this answer to the tenant:" if live else
                                    "This answer becomes final (TEST: shown in the CRM chat, never sent to Twilio):"))
            lines.append(f"   \"{parsed['answer'][:900]}\"")
        elif parsed.get('answer'):
            lines.append(f"• 💬 Answer to the tenant: {(delivery or {}).get('note') or ''}\n   \"{parsed['answer'][:600]}\"")
        else:
            lines.append("• 💬 No answer to the tenant")
        lines += changes
    elif confirm:
        lines.append("⚖️ SUGGESTED answer based on the contract - NOT sent, waits for a manager to reply \"ok\" or a "
                     f"corrected answer:\n{parsed['answer'][:900]}")
    elif parsed.get('answer'):
        how = 'sent to the tenant' if sent else 'NOT sent: ' + ((delivery or {}).get('note') or '')
        lines.append(f"🤖 AI answer ({how}):\n{parsed['answer'][:900]}")
    else:
        lines.append("🤖 AI: no answer to the tenant" + (" - and nothing else to change" if plan_items is not None else ""))
    if confirm:
        lines.append(f"📄 Contract basis: {(parsed.get('contract_basis') or 'not given by the AI - check the contract')[:900]}")
    if parsed.get('review_answer'):
        lines.append(f"📝 Would have said (staff answered first, never sent):\n{parsed['review_answer'][:600]}")
    if parsed.get('why'):
        lines.append(f"💡 {parsed['why'][:500]}")

    cache = {}
    if plan_items is not None:
        team = plan_mod.team_lines(plan_items)
        if team:
            lines += ["", "👤 FOR THE TEAM (this message is the notification):"] + team
        lines += plan_mod.info_lines(plan_items)
    else:
        if groups:
            lines += ["", "👤 FOR THE TEAM:"] + _team_lines(groups, cache)
        if ticket_updates:
            lines += ["", "📌 EXISTING CLICKUP TASK:"]
            for update in ticket_updates:
                issue = update['issue']
                lines.append(f"• t-{issue.id} 🎫 {issue.ticket_title or issue.summary}" + (f" → {issue.ticket_ref}" if issue.ticket_ref else ""))
                lines.append(f"  {('💬 ' + update['text'][:400]) if update.get('text') else ''} {update.get('clickup') or ''}".rstrip())
        parts, problems = _other_actions_line(action_results)
        kb = [f"📚 {i['action'].get('key')} = {str(i['action'].get('value'))[:120]} ({str(i.get('detail'))[:90]})"
              for i in action_results if isinstance(i.get('action'), dict) and i['action'].get('type') == 'KB_UPDATE'
              and i.get('status') != 'rejected']
        if parts:
            lines += ["", "⚙ Done now: " + " · ".join(parts)]
        lines += kb + problems
    lines += ["", f"🔗 {report_url(ai_run.id)}"]   # cost and tokens stay in the report and on /ai-runs/
    if confirm:
        lines += ["", "⚖️ The legal answer is NEVER sent automatically - it waits for your \"ok\" or corrected answer, "
                      "even after the review window."]
    if automatic:
        lines += ["", f"⏰ No reply by {_eta(delivery)} ({_minutes_left(delivery)}) → the whole plan above runs AUTOMATICALLY"
                      + (" (except the legal answer)" if confirm else "") + ". \"stop\" prevents it, \"ok\" runs it now."]
        lines += ["↩ REPLY to this message to change it: \"ok\" = do it all now · \"stop\" = do nothing · "
                      "\"remove 3\" · \"3 urgent\" / any change in words · a corrected answer · \"done\" / \"no task needed\" "
                      "for a ClickUp task · \"next time ...\" to teach the AI · start with \"test\" to only see what would happen."]
    elif confirm:
        lines += ["↩ REPLY to this message: \"ok\" = send the suggested answer now · a corrected answer = send that instead · "
                  "\"don't send\" = the tenant gets nothing · \"next time ...\" to teach the AI."]
    elif parsed.get('answer') or plan_items is not None:
        lines += ["", "↩ Reply to manage ClickUp tasks (\"done\", \"no task needed\"), add knowledge, or \"next time ...\" "
                      "to teach the AI. Start with \"test\" to only see what would happen."]
    return "\n".join(lines)


def _minutes_left(delivery):
    from django.utils import timezone
    until = (delivery or {}).get('plan_until') or (delivery or {}).get('hold_until')
    if not until:
        return f"in {config.review_hold_minutes():g} min"
    return f"in {max(1, round((until - timezone.now()).total_seconds() / 60))} min"


def _eta(delivery):
    until = (delivery or {}).get('plan_until') or (delivery or {}).get('hold_until')
    if not until:
        return 'the end of the review window'
    return until.astimezone(ZoneInfo(config.TEAM_TIMEZONE)).strftime('%H:%M') + f" {config.TIMEZONE_LABEL}"


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


def task_assignees(is_test, responsible=()):
    """ClickUp user ids for a new AI task (rules of 2026-09-22, see the constants at the top)."""
    from django.db.models import Q

    from mysite.models import StaffMember

    staff = StaffMember.objects.filter(is_active=True).exclude(clickup_user_id__isnull=True).exclude(clickup_user_id='')
    if is_test or clickup.test_list_id():
        staff = staff.filter(Q(role=StaffMember.ROLE_ENGINEERING) | Q(ai_name__in=ALWAYS_CLICKUP_TEST_ASSIGNEES))
    else:
        names = set(responsible or [])
        for name in list(names):
            names.update(config.STAFF_ALSO.get(name, ()))  # Kevin is also Farid
        staff = staff.filter(Q(ai_name__in=list(names)) | Q(ai_name__in=ALWAYS_CLICKUP_LIVE_ASSIGNEES))
    return list(staff.values_list('clickup_user_id', flat=True))


def staff_names(clickup_ids):
    from mysite.models import StaffMember
    names = [s.ai_name for s in StaffMember.objects.filter(clickup_user_id__in=[str(i) for i in clickup_ids])]
    return ", ".join(names) or 'nobody'


def task_name(title, unit, is_test):
    return '[AI] ' + ('[TEST] ' if is_test else '') + _with_unit(title, unit)


def _deliver_clickup(ctx, groups, trigger_text, ai_run):
    """Tasks for tickets + one channel message. Returns a note; never raises."""
    mapping = clickup.channel_for(ctx.apartment)
    if not mapping or not mapping.list_id:
        for group in groups:
            group['no_list'] = group['wants_task'] and not (group['issue'] and group['issue'].ticket_ref)
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
        assignees = task_assignees(ctx.is_test, group['responsible'])
        # Every AI task is marked as AI-made in its name, tagged, and gets a priority-based due date (user request 2026-09-22).
        tasks.append({
            'group': group,
            'name': task_name(group['ticket_title'], ctx.meta.get('apartment'), ctx.is_test),
            'description': f"{group['text']}\n\nTenant: {ctx.meta.get('tenant')}\n{(trigger_text or '')[:1500]}\n\nReport: {report_url(ai_run.id)}",
            'priority': group['priority'], 'assignees': assignees, 'due_at': clickup.default_due_at(group['priority']),
            'tags': AI_TASK_TAGS,
        })
    if not clickup.writes_enabled(ctx.apartment):
        for task in tasks:
            task['group']['task_would_be'] = task['name']
        return (f"ClickUp writes OFF: nothing changed in ClickUp (would create {len(tasks)} task(s) in {mapping.name}"
                + (", post a channel message" if mapping.channel_id else "") + ")")
    try:
        if mode == 'api':
            for task in tasks:
                task_id, task_url = clickup.create_task(
                    mapping.list_id, task['name'], task['description'], task['priority'], task['assignees'], task['due_at'],
                    tags=task['tags'],
                )
                task['group']['task_url'] = task_url or task_id
                task['group']['task_created'] = True
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


def deliver_clickup_now(ctx, trigger_text, ai_run):
    """After a reviewed plan was executed: ClickUp tasks + channel message for its tickets. Returns (groups, note)."""
    groups = alert_groups(ctx.alerts)
    return groups, _deliver_clickup(ctx, groups, trigger_text, ai_run) if ctx.notify else ''


def deliver(ctx, ai_run, parsed, action_results, delivery, trigger_text, plan_items=None, plan_actions=None):
    """
    Sends the run's notifications. Returns a short note and writes it into the alert actions' details.
    plan_items given: the staff review is on - only the Telegram alert with the plan, nothing in ClickUp yet.
    """
    from mysite.ai_agent import plan as plan_mod

    if plan_items is not None:
        groups, clickup_note = [], ''
        waiting = bool((delivery or {}).get('held')) or plan_mod.has_changes(plan_items)
        wanted = waiting or plan_mod.team_lines(plan_items) or activity_enabled()
    else:
        groups = alert_groups(ctx.alerts)
        clickup_note = _deliver_clickup(ctx, groups, trigger_text, ai_run) if ctx.notify else ''
        wanted = groups or activity_enabled()
    telegram_note = ''
    if ctx.notify and wanted:
        ok, telegram_note, message_id = send_ai_chat(
            compose_telegram(ctx.meta, parsed, action_results, delivery, trigger_text, groups, ai_run,
                             getattr(ctx, 'ticket_updates', ()), plan_items=plan_items, plan_actions=plan_actions))
        telegram_note = f"Telegram: {'sent' if ok else 'FAILED - ' + telegram_note}"
        if delivery is not None and message_id:
            delivery['telegram_message_id'] = message_id   # staff reply to it to change the plan
    note = "; ".join(n for n in (telegram_note, clickup_note) if n)
    delivered = 'Telegram: sent' in note or note.count('ClickUp (') and 'FAILED' not in clickup_note
    for item in action_results:
        action = item.get('action') if isinstance(item.get('action'), dict) else {}
        if action.get('type') in NOTIFY_TYPES and item.get('status') == 'executed':
            item['detail'] = f"{item.get('detail')}; {note}"
            if groups and not delivered:
                item['status'] = 'rejected'
    return note
