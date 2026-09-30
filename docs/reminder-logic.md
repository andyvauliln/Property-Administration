# AI reminders (follow-ups): full logic

How the Claude agent decides to create, change or cancel a reminder, how the backend times it,
what happens when it fires, and every way it stops. Everything below is taken from the code
as of 2026-09-28.

Diagram: [`diagrams/reminder-lifecycle.html`](diagrams/reminder-lifecycle.html)

---

## 1. The idea in one paragraph

The AI has no clock and no memory between runs. When it wants something re-checked later, it
emits a `SCHEDULE_FOLLOWUP` action saying **what** kind of reminder and **why**. The backend
stores it as an `AIFollowUp` row and decides **when**. When that time comes, the worker wakes
the AI again with a `FOLLOWUP_DUE` event. The AI then looks at the current state of the
chat, issue and ClickUp task, and decides whether the reminder is still needed. It either sends
a nudge or alert, or cancels it and stays silent.

```
AI run ──SCHEDULE_FOLLOWUP──► 15-min staff review ──► AIFollowUp (pending, due_at)
                                                            │ worker, every 30 s
                                                            ▼
                                        issue resolved? ──yes──► cancelled, AI not woken
                                                            │ no
                                                            ▼
                                        FOLLOWUP_DUE event ──► ClickUp task closed? ──yes──► issue resolved quietly
                                                            │ no / no task
                                                            ▼
                                        Claude re-checks ──► not needed: CANCEL_FOLLOWUP + NO_ANSWER
                                                            └► needed: nudge tenant / alert staff (+ maybe next reminder)
                                                                         │
                                                                         ▼
                                                               15-min review ──► SMS window 08–21 ──► tenant
```

---

## 2. Data model: `AIFollowUp` (`mysite/models.py`)

| Field | Meaning |
|---|---|
| `conversation_sid` | Chat the reminder belongs to |
| `issue` | Optional link to an `AIIssue`. Priority comes from here. Resolving the issue stops the reminder |
| `kind` | `tenant_nudge`, `second_tenant_nudge`, `staff_reminder`, `escalation_check` |
| `reason` | The AI's own words: what should be re-checked. Shown to the AI again when it fires |
| `due_at` | Calculated by the backend (section 4), never by the AI | NOTE: i need more details on that and what is section 4
| `status` | `pending` → `fired`, or `cancelled` |
| `status_note` | Why it was cancelled (`issue resolved`, `cancelled by AI`, `ClickUp task closed`, …) |
| `created_by_run` | The `AIRun` that asked for it |

Shown as `f-<id>` to the AI and staff (for example `f-88`).

---

## 3. When the AI decides to create a reminder

The rules come from the system prompt. Production uses the packaged file
`mysite/ai_agent/default_system_prompt.md`: the run reports show . NOTES: why we always don't use db and why it's not in db
`system_prompt_source: file:default_system_prompt.md`, so there is no database override. The N
relevant parts are *STEP 4 (maintenance)*, *STEP 6 (routing)*, *STEP 7 (follow-ups)* and
*STEP 8 (event handling)*.

### 3.1 The four kinds and what triggers each

| Kind | The AI schedules it when… | Typical companion actions |
|---|---|---|
| `escalation_check` | A tenant question **needs a human during staff hours** (approval, a decision, something not in the knowledge base). Example: late-checkout request | `CREATE_ISSUE` (owner Edy, `WAITING_FOR_EDY`) |
| `staff_reminder` | A **maintenance** issue was logged (routine or urgent). The reminder re-checks the ClickUp task later | `CREATE_ISSUE`, `CREATE_TICKET`, `INTERNAL_ALERT` or `QUEUE_FOR_REVIEW` |
| `tenant_nudge` | a) The tenant says they will reply later ("I'll send it later", "I'll check when I get home"), or b) staff asked the tenant for information, a document, a photo, a confirmation or an action, or c) staff/ClickUp says something is **fixed** and the AI asks the tenant to confirm (`WAITING_FOR_TENANT_CONFIRMATION`) | `UPDATE_ISSUE_STATE` |
| `second_tenant_nudge` | Scheduled **during the first nudge's run** when the first nudge went out and the tenant still owes an answer | Alert or queue to Edy |

What the AI does **not** schedule reminders for:
- **Emergencies.** They get an immediate `INTERNAL_ALERT` (Edy + Kevin) and a ticket, with no timer.
- **Questions that need a human outside staff hours.** These use `QUEUE_FOR_REVIEW`. The prompt
  explicitly says not to create a separate timer or "weekend digest" per message.
- **Acknowledgements.** "Never nudge about an acknowledgment."
- **An issue that already has a pending reminder.** `PENDING_FOLLOWUPS` is in every run's
  input, and the prompt says to check it before `SCHEDULE_FOLLOWUP`.

### 3.2 What the AI sends

```json
{"type": "SCHEDULE_FOLLOWUP", "issue_id": "new-1 | i-12 | r<run>:new-N", "kind": "staff_reminder", "reason": "Recheck the sink maintenance task"}
```

- `issue_id` may point to an issue created in the same response (`new-1`), an existing one
  (`i-12`), or an issue that a still-pending plan of an earlier run will create (`r<run>:new-N`).
- The AI must **not** output a time. The prompt says timing belongs to the backend. The delays in the
  prompt (≈30 min, ≈3 h…) are descriptions only.

### 3.3 What the AI sees about reminders in every run (`inputs.tracking_block`)

```
PENDING_FOLLOWUPS:
- followup_id: f-88 | kind: tenant_nudge | issue_id: i-41 | due: 2026-09-28 14:00 | reason: Waiting for photo of the leak
```

It also sees `OPEN_ISSUES` (with priority and state), `OPEN_TICKETS` and the last 10 `CASE_NOTES`.
This is how a later run knows to cancel or not duplicate a reminder.

### 3.4 When the AI cancels a reminder

Any run can emit `{"type": "CANCEL_FOLLOWUP", "followup_id": "f-88"}`. The prompt asks for it:
- On a **STAFF_MESSAGE** that makes it unnecessary ("cancel unnecessary follow-ups").
- On a **TENANT_MESSAGE** that already delivers what the reminder was waiting for.
- On **FOLLOWUP_DUE** when the reminder is no longer needed (section 6). In that case it
  answers `NO_ANSWER`.

Setting an issue to `RESOLVED` (`UPDATE_ISSUE_STATE`) also cancels all its pending reminders
automatically (section 7).

---

## 4. When a reminder is due (`mysite/ai_agent/policy.py`)

`policy.due_at(kind, priority)` is the only place with timing. Change timings here, not in the
prompt.

### 4.1 Delays

| Kind | Priority | Delay |
|---|---|---|
| `escalation_check` | any | +30 min |
| `tenant_nudge` | any | +3 h |
| `second_tenant_nudge` | any | +24 h |
| `staff_reminder` | routine | +24 h |
| `staff_reminder` | urgent | +1 h |
| `staff_reminder` | emergency | +30 min |

**Priority** is the linked issue's `priority` (`routine` by default). An issue's priority only
goes **up**: it is raised by the priority on an `INTERNAL_ALERT`, `QUEUE_FOR_REVIEW` or
`CREATE_TICKET` in the same or a later run (`actions._notify_action`). The backend runs a
response's actions in this order:
1. `CREATE_ISSUE`
2. alerts and tickets
3. everything else, including `SCHEDULE_FOLLOWUP`

So an urgent ticket raises the issue to urgent **before** the reminder's time is calculated, whatever
order the AI wrote them in. A reminder without an issue is always `routine`.

### 4.2 Allowed hours

All hours below are in `AI_AGENT_TEAM_TIMEZONE` (default `America/New_York`).

| Kind | Rule |
|---|---|
| `tenant_nudge`, `second_tenant_nudge` | Must land between **09:00 and 20:00**. Otherwise moved to **10:00** (the same morning if before 09:00, the next day if after 20:00) |
| any kind with priority **urgent / emergency** | Any hour, no moving |
| `staff_reminder` / `escalation_check`, routine | Must land between **10:00 and 18:00**. Otherwise moved to **10:00** of the same or the next day |

Examples (routine issue):
- A tenant asks at 19:00 for something that needs Edy. The `escalation_check` (+30 min = 19:30) is outside 10–18, so it fires at **10:00 the next day**.
- A tenant says at 18:30 "I'll send the photo later". The `tenant_nudge` (+3 h = 21:30) is outside 09–20, so it fires at **10:00 the next day**.
- Urgent AC failure at 23:00: the `staff_reminder` (+1 h) fires at **00:00**.

Weekends and holidays are **not** skipped. Only the hour of day is checked.

### 4.3 Guards when the reminder is stored (`actions._schedule_followup`)

1. `kind` must be one of the four kinds, otherwise the action is rejected.
2. **Cap:** an issue can have at most **8** reminders in total (`MAX_FOLLOWUPS_PER_ISSUE`). This counts
   every status, cancelled ones included. The 9th is rejected with "reached the limit". This stops
   endless reminder loops.
3. **No duplicates:** if the issue already has a **pending** reminder of the **same kind**, nothing
   new is created. The run result says which one is already pending.
4. The `AIFollowUp` row is created with `due_at` from 4.1–4.2.

---

## 5. Staff review before a reminder exists

A run's actions are **not executed immediately** (`answer_review.py`, `plan.py`):

1. The Telegram AI group gets the alert "📋 PLAN – NOTHING IS DONE YET. At 14:35 ET, unless someone
   replies…". The reminder is a numbered item:
   `⏰ Reminder (staff_reminder) about the new issue "Kitchen sink dripping": Recheck… – would fire about Mon 10:00 (counted from when it is applied)`
2. Staff can reply to the alert:
   - `remove 3` / `no reminder needed`: the reminder is dropped.
   - `no task needed` / `not a real issue`: the task **and everything that only exists for it** is dropped (new issue, its reminders, its notes).
   - Change the reason in words: Claude turns it into a `change` of the reminder's text.
   - `stop`: the whole plan and the answer are cancelled, so no reminder.
   - `ok`: everything is applied at once.
   - `test …`: a dry run that shows what would happen.
3. When the window ends (`AI_AGENT_REVIEW_HOLD_MINUTES`, default **15**), `apply_plan` runs the
   normal handlers. **The `AIFollowUp` row is created at that moment, and the delay is counted from
   then.** So a +30 min `escalation_check` fires about **45 min** after the tenant's message.
4. **Emergencies skip the review.** Their actions run at once.
5. If a newer run happens in the same chat while a plan is still waiting, the newer run is told
   about the pending plan (`pending_block`) so it does not schedule the same reminder again.

Known difference: for a reminder on an issue created in the same response, the preview time in
the Telegram plan is calculated as **routine**. `CREATE_ISSUE` carries no priority, and the
preview doesn't apply the ticket's priority first. The **real** time after the plan is applied
uses the raised priority, so an urgent `staff_reminder` really fires after about 1 h, even if the
preview said "Mon 10:00".

---

## 6. When it fires

### 6.1 Worker tick (`service.fire_due_followups`, called by `run_ai_agent` every **30 s**)

For each pending reminder with `due_at <= now` (up to 50 per tick):
1. The status changes `pending → fired` in a single database update, so it can never fire twice.
2. If its issue is **already resolved**, it becomes `cancelled` ("issue already resolved – AI not
   woken"). No AI run, no cost.
3. Otherwise an `AIEvent` of type `FOLLOWUP_DUE` is created, with the reminder's reason as the body and
   `followup_id` in the payload.

### 6.2 Batching (`service.claim_next_batch`)

- A batch made **only of** `FOLLOWUP_DUE` events has **no debounce wait** (0 s).
- If the tenant or staff also wrote in that chat, all events go into **one** Claude run. The run's
  type is then `TENANT_MESSAGE` or `STAFF_MESSAGE` (priority: tenant > staff > follow-up). The due
  reminder is still passed in as extra context.

### 6.3 ClickUp check before Claude (`service.check_tickets_before_reminder`)

This only applies if the reminder's issue has a ClickUp task and ClickUp access is not `off`:

| ClickUp task state | What happens |
|---|---|
| **Closed** | The issue becomes `RESOLVED`, **all** its pending reminders are cancelled ("ClickUp task closed"), and a case note is added: "Tenant was not contacted". Telegram gets "✅ … closed: its ClickUp task is closed… Reminders stopped". **Claude is not called** |
| **Open** | Status, assignees, last change and latest comments are passed to Claude as `CLICKUP_TASKS (internal – never quote this to the tenant)` |
| **Read error** | Claude is told the task could not be read and still runs |

### 6.4 What Claude gets on `FOLLOWUP_DUE`

```
FOLLOWUP_DUE (re-check the current state before acting; cancel or do nothing when it is no longer needed):
- followup_id: f-88 | kind: tenant_nudge | issue_id: i-41 | scheduled: 2026-09-28 11:00 | reason: Waiting for tenant photo
CLICKUP_TASKS (...)            ← only if the issue has an open task
```

It also gets the normal input: recent chat history, open issues and tickets, other pending reminders, case notes and the knowledge base.

### 6.5 How Claude decides (prompt STEP 7: "Never blindly execute an outdated reminder")

It must check:
- Did the tenant already respond?
- Did staff respond in the tenant chat?
- Did staff respond in ClickUp?
- Was the issue resolved?
- Was the task updated?
- Did another action already handle it?
- Is the follow-up still necessary?
- Is the current time appropriate?

**No longer needed:** `CANCEL_FOLLOWUP` + `NO_ANSWER`, often with an `UPDATE_ISSUE_STATE`. Example from the prompt: the tenant already sent the requested photo, so cancel `f-88` and move `i-41` to `WAITING_FOR_EDY`.

**Still needed, by kind:**

| Kind | What the AI does |
|---|---|
| `escalation_check` | Staff answered or issue resolved: do nothing / cancel. Otherwise an `INTERNAL_ALERT` to the responsible person (apartment ClickUp channel + Telegram) |
| `staff_reminder` | Re-check the task. No meaningful update: remind the responsible person, and escalate to Kevin if the overdue/escalation policy says so. The prompt also says: don't re-alert about the same unchanged issue unless the SLA expired, circumstances changed, a deadline was missed or severity increased |
| `tenant_nudge` (1st) | One short message to the tenant naming what is still pending; notify or queue Edy when relevant; if still needed, schedule `second_tenant_nudge` |
| `second_tenant_nudge` | One final short follow-up; alert or queue Edy; include Kevin only if the delay creates real operational, financial, check-in/out or safety risk. **Stop after 2 unanswered routine nudges** |

A tenant nudge that confirms a fix follows the "FIXED" flow:
- The tenant confirms: `UPDATE_TICKET tenant_confirmed_fixed`, and the issue becomes `RESOLVED`, which stops its reminders.
- The tenant says it's still broken: reopen and alert Edy.

---

## 7. Every way a reminder stops

| Where | Trigger | Code |
|---|---|---|
| AI, any run | `CANCEL_FOLLOWUP f-N` | `actions._cancel_followup` → `cancelled by AI` |
| AI, any run | `UPDATE_ISSUE_STATE … RESOLVED`: all pending reminders of the issue, and its ClickUp task is closed | `actions._update_issue_state` → `issue resolved` |
| Manager, CRM | "Resolve" on `/ai-issues` or the chat page | `ai_agent_views.ai_issue_resolve` → `issue resolved by a manager` |
| Staff, Telegram | Before it exists: `remove N`, `no reminder needed`, `no task needed`, `stop` | `answer_review.apply_plan_ops` / `cancel_plan` |
| Staff, Telegram | "done" / "close it" / "delete that task" about an existing ClickUp task: the issue is resolved, reminders stopped | `answer_review.apply_task_actions` → `_resolve_issue` |
| Worker, at fire time | The issue is already resolved: cancelled without waking the AI | `service.fire_due_followups` |
| Worker, at fire time | The ClickUp task is closed: issue resolved, all reminders cancelled, tenant not contacted | `service.check_tickets_before_reminder` |
| Backend guard | The 8-per-issue cap is reached: new ones are rejected | `policy.MAX_FOLLOWUPS_PER_ISSUE` |

---

## 8. How the reminder's message goes out

The answer produced by a `FOLLOWUP_DUE` run goes through exactly the same path as any answer
(`service.deliver`):

1. **Which chat.** A batch with no tenant or staff message is sent to the tenant's **main chat**
   (the one they wrote in last, `conversation_groups.main_sid`), not necessarily the chat where
   the reminder was created.
2. **Staff review.** The answer and the new actions are held **15 min** in Telegram, where staff can
   approve, edit or stop them. The Telegram alert says "⏰ Reminder became due: <reason>". Emergencies go at once.
   Legal answers wait for a manager's "ok".
3. **Suppressed** if staff wrote to the tenant in the meantime.
4. **SMS window.** Tenant SMS go out only **08:00–21:00** in `AI_AGENT_PROPERTY_TIMEZONE` (Florida by default).
   Outside it they are held in `PendingOutboundMessage` and sent by `flush_pending_sms` when the
   window opens.
5. **Test mode.** Reminders are stored and fire in test mode too, because actions are saved in
   both modes. Only the final Twilio send is skipped (`HOLD_TEST`).

So a tenant nudge due at 19:55 can reach the tenant after 20:10 because of the 15-minute review, and
a 20:50 one goes out at 08:00 the next morning.

---

## 9. Timeline example (routine maintenance)

| Time (ET) | What happens |
|---|---|
| Mon 19:00 | Tenant: "The kitchen sink is dripping." The AI answers and plans `CREATE_ISSUE`, `CREATE_TICKET` (routine), `QUEUE_FOR_REVIEW` and `SCHEDULE_FOLLOWUP staff_reminder` |
| Mon 19:15 | The review window ends: the answer is sent, issue `i-41` and its ClickUp task are created, and `f-90` is stored with due = 19:15 + 24 h = Tue 19:15, which is outside 10–18, so **Wed 10:00** |
| Wed 10:00 | The worker fires `f-90`. ClickUp check: the task is closed, so `i-41` is resolved and Telegram gets "✅ closed…". **Stop.** |
| (alternative) | Task still open, no comments: Claude runs and plans an `INTERNAL_ALERT` to Edy ("sink task has no update for 2 days") and maybe a new `staff_reminder`. These go through the 15-min review |
| Wed 14:00 | Edy comments "fixed" in ClickUp. On the next event, the AI sets `WAITING_FOR_TENANT_CONFIRMATION`, asks the tenant, and schedules `tenant_nudge` (+3 h = 17:00) |
| Wed 17:00 | No reply from the tenant, so the nudge goes out: "Just checking, is the sink working properly now?" |

---

## 10. Things that may surprise you

1. **"Tenant hours" use the team's timezone, not the tenant's.** `policy._tz()` uses
   `AI_AGENT_TEAM_TIMEZONE` for both windows. The comment in `policy.py` and the prompt both say
   "tenant local time". This is fine for Florida properties, but wrong for any property in another
   timezone.
2. **The review delay adds to every delay.** Reminders are created when the plan is applied (about
   15 min later), and the tenant-facing reminder message has its own 15-min review.
3. **The preview time in the Telegram plan can be wrong for new urgent issues** (section 5).
4. **The per-issue cap counts cancelled reminders.** An issue whose reminders were often cancelled
   and rescheduled can hit 8 and stop getting reminders.
5. **Reminders without an issue** skip the ClickUp check, are always routine, have no cap, and are
   only stopped by `CANCEL_FOLLOWUP`.
6. **Weekends are not special** in the timer code. Only hours of the day are.

---

## 11. Where everything lives

| What | File |
|---|---|
| Delays, allowed hours, cap | `mysite/ai_agent/policy.py` |
| Create, cancel, resolve handlers; action order | `mysite/ai_agent/actions.py` (`_schedule_followup`, `_cancel_followup`, `_update_issue_state`, `execute_actions`) |
| Firing, ClickUp check, FOLLOWUP_DUE context, batching, delivery | `mysite/ai_agent/service.py` (`fire_due_followups`, `check_tickets_before_reminder`, `_followup_block`, `_batch_wait_seconds`, `process_events`, `deliver`) |
| Worker loop (30 s reminder tick) | `mysite/management/commands/run_ai_agent.py` |
| Review plan text and staff edits | `mysite/ai_agent/plan.py`, `mysite/ai_agent/answer_review.py` |
| `PENDING_FOLLOWUPS` input block | `mysite/ai_agent/inputs.py` (`tracking_block`) |
| AI decision rules | `mysite/ai_agent/default_system_prompt.md` (STEP 4, 6, 7, 8, examples 1, 2, 4, 6) |
| Manager "Resolve" | `mysite/views/ai_agent_views.py` (`ai_issue_resolve`) |
| SMS window | `mysite/ai_agent/config.py`, `mysite/views/messaging.py` (`send_tenant_sms_gated`) |
| Testing timers | `python manage.py ai_agent_sandbox --due-now` makes the sandbox chat's pending reminders due now |
