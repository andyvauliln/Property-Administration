"""
The Telegram card of one AI run in the client's layout (client spec v4, section 4), used with explicit approval:

    🔔 ROUTINE · TYPE 2 Routine maintenance · ANSWERED · 720-101
    Tenant: Rita · Owner: Edy (Farouk Ahmed) · 🟢 LIVE
    Received: Tue 23:30 ET · Reply due: no fixed deadline
    🌙 After-hours message: SENT Tue 23:30 ET
    TENANT SAID / CONTEXT / AI PROPOSES TO SEND / INTERNAL ACTION / Next step / MANAGER ACTION

The buttons are added by approval.keyboard(); this module only writes the text.
"""
from mysite.ai_agent import clickup, config
from mysite.ai_agent import plan as plan_mod

ICON = {'emergency': '🚨', 'urgent': '🔴', 'routine': '🔔'}


def _type_label(triage):
    from mysite.ai_agent.service import TRIAGE_TYPE_LABELS
    primary = triage.get('primary_type')
    label = TRIAGE_TYPE_LABELS.get(primary, primary or '?')
    others = [TRIAGE_TYPE_LABELS.get(t, t).split(' ', 1)[0] for t in triage.get('secondary_types') or []]
    return f"TYPE {label}" + (f" (+{', '.join(others)})" if others else "")


def _deadline_text(triage):
    from mysite.ai_agent import service
    deadline = service.parse_tenant_deadline(triage.get('tenant_deadline'))
    return f"{deadline:%a %b %d %H:%M} {config.TIMEZONE_LABEL}" if deadline else 'no fixed deadline'


def compose(meta, parsed, delivery, trigger_text, plan_items, plan_actions, ai_run):
    from mysite.ai_agent import cases, team_notify
    from mysite.ai_agent.actions import staff_label

    triage = parsed.get('triage') or {}
    live = meta.get('mode') == 'live'
    held = bool((delivery or {}).get('held'))
    legal = held and bool((delivery or {}).get('confirm'))
    changes = plan_mod.render(plan_items) if plan_items is not None else []
    priority = triage.get('priority') or (plan_mod.top_priority(plan_items, plan_actions) if plan_items else None) or 'routine'
    no_reply = triage.get('primary_type') == 'NO_REPLY' and not parsed.get('answer') and not changes
    icon = '💬' if no_reply else ICON.get(priority, '🔔')
    case_status = triage.get('case_status') or '-'
    handled = parsed.get('handled_info')

    lines = [
        f"{icon} {priority.upper()} · {_type_label(triage)} · {case_status} · {meta.get('apartment')}",
        f"Tenant: {meta.get('tenant') or '-'} · Owner: {staff_label(triage.get('owner')) or '-'} · "
        + ('🟢 LIVE' if live else '🧪 TEST - nothing is ever sent to the tenant'),
        f"Received: {team_notify._now_label()} · Reply due: {_deadline_text(triage)}",
    ]
    if meta.get('urgent_reply'):
        lines.append("🚨 The tenant wrote URGENT")
    for key in ('after_hours_line', 'call_line'):
        if meta.get(key):
            lines.append(meta[key])
    if meta.get('tenant_chats'):
        lines.append(f"🔗 {meta['tenant_chats']}")
    if not clickup.writes_enabled(meta.get('apartment')):
        lines.append("🚫 ClickUp writes OFF - ClickUp items below are only what the AI WOULD do")

    lines += ["", "TENANT SAID", (trigger_text or '').strip()[:1200] or '(no new tenant message)']

    context = [f"✔ {fact}" for fact in triage.get('verified_facts') or []]
    context += [f"❓ {doubt}" for doubt in triage.get('uncertainties') or []]
    context += cases.repeat_lines(ai_run.conversation_sid, triage.get('issue_refs'))
    if triage.get('tenant_deadline'):
        context.append(f"⏰ Tenant deadline: {_deadline_text(triage)}")
    if handled:
        context.append(f"👤 About {', '.join(handled['issues'])} - handled by staff (I'll handle): the AI proposes "
                       f"nothing for it" + (f"; {handled['dropped_actions']} AI action(s) dropped" if handled['dropped_actions'] else ""))
    if legal:
        context.append(f"⚖️ LEGAL question - contract basis: {(parsed.get('contract_basis') or 'not given by the AI - check the contract')[:700]}")
    if context:
        lines += ["", "CONTEXT"] + context

    lines.append("")
    if parsed.get('answer'):
        lines.append("AI PROPOSES TO SEND" + (" (⚖️ legal - check the contract basis)" if legal else "")
                     + ("" if held or not (delivery or {}).get('note') else f" - {delivery['note']}"))
        lines.append(f"\"{parsed['answer'][:1500]}\"")
    else:
        reason = triage.get('no_reply_reason') or ('staff answered first' if parsed.get('review_answer') else '')
        lines.append(f"NO TENANT REPLY PROPOSED{(' — ' + reason) if reason else ''}")
    if parsed.get('review_answer'):
        lines.append(f"📝 Would have said (never sent):\n\"{parsed['review_answer'][:600]}\"")

    team = plan_mod.team_lines(plan_items or [])
    info = plan_mod.info_lines(plan_items or [])
    if changes or team or info:
        lines += ["", "INTERNAL ACTION" + (" (☑ = done when approved - tap a line below to untick it)" if changes else "")]
        lines += changes + info
        if team:
            lines += ["👤 For the team (this card is the notification):"] + team
    if triage.get('next_action') or triage.get('owner'):
        lines += ["", f"Next step: {staff_label(triage.get('owner')) or 'team'} - {triage.get('next_action') or '-'}"]
    if parsed.get('why'):
        lines.append(f"💡 {parsed['why'][:500]}")
    lines += ["", f"🔗 {team_notify.report_url(ai_run.id)}"]

    if held or changes:
        lines += ["", "MANAGER ACTION: ✅ Approve all · ✏️ Replace · ❌ Don't send · 👤 I'll handle · tick/untick items"
                      if held else "MANAGER ACTION: ✅ Apply ticked items · 👤 I'll handle · 🛑 Stop all · tick/untick items",
                  "↩ Or REPLY in words: ask a question, correct the answer, \"remove 2\", \"2 urgent\", \"done\" for a "
                  "ClickUp task, \"next time ...\" to teach the AI, \"test ...\" to only see what would happen.",
                  "⏸ No decision = nothing is sent to the tenant and nothing is done."]
    else:
        lines += ["", "↩ Reply to ask about it, manage ClickUp tasks (\"done\", \"no task needed\"), add knowledge or "
                      "\"next time ...\" to teach the AI."]
    return "\n".join(lines)
