# Client spec v4: how the AI works now

> **Historical document (8 Oct 2026).** Parts of it describe old modes: the v4 card (Approve all, I'll handle, tick boxes, drafts) was replaced by the simple alerts and then removed. The current behaviour is in [simple_telegram_alerts.md](simple_telegram_alerts.md). See also docs/cleanup_plan.md.

Built 2026-09-30 from Farid's "AI Property Manager System Prompt and Workflow" and the user's decisions
(`client_spec_v4_plan.md`, section 4). Tests: `testbed/e2e_client_v4.py` (57 checks, in `run_tests.sh`).

## In short

1. A tenant writes. Outside office hours (Mon–Fri 09:00–18:00 ET; weekends and US federal holidays are outside) the
   tenant gets the fixed after-hours message at once, max once per 5 hours per tenant.
2. The AI classifies the message (8 types or NO_REPLY), drafts a reply and a plan, and says who owns it, the next
   action and the tenant's deadline.
3. The Telegram AI group gets one **card** with buttons. **Nothing is sent or done until someone presses a button**
   (or replies "ok"). No decision = nothing happens.
4. Real emergencies are the exception: the safety reply and the actions happen at once, and Farid is phoned.

## The card

```
🔔 ROUTINE · TYPE 2 Routine maintenance · ACKNOWLEDGED · 720-201
Tenant: Vera · Owner: Edy (Farouk Ahmed) · 🟢 LIVE
Received: Sep 30 23:31 ET, Florida · Reply due: no fixed deadline
🌙 After-hours message: SENT Tue 23:30 ET, Florida

TENANT SAID
[2026-09-30 23:30] Vera (TENANT): The kitchen sink is dripping

CONTEXT                      verified facts ✔, open questions ❓, 🔁 "asked 3 times, waiting 5 h", deadline, legal basis

AI PROPOSES TO SEND
"Thanks, I've logged the dripping sink with the team."

INTERNAL ACTION (☑ = done when approved - tap a line below to untick it)
1. 🆕 Open issue "Kitchen sink dripping" · MAINTENANCE_OPEN · owner Edy
2. 🎫 Create ClickUp task ...
3. ⏰ Reminder (staff_reminder) ...

Next step: Edy (Farouk Ahmed) - Edy: send the plumber
[✅ Approve all] [✏️ Replace] [❌ Don't send]
[👤 I'll handle] [🛑 Stop all]
[☑ 1. 🆕 Open issue "Kitchen sink dripping"…]
[☑ 2. 🎫 Create ClickUp task …]
[☑ 3. ⏰ Reminder …]
[▶ Apply ticked items only (answer keeps waiting)]
```

| Button | What happens |
|---|---|
| ✅ Approve all | answer sent + ticked items done |
| ✏️ Replace | bot asks for the text (reply to it) → a DRAFT card → **✅ Send this** sends it |
| ❌ Don't send | answer not sent; the plan still waits |
| 👤 I'll handle | answer not sent, only the new issue(s) opened, the AI stays out of them until **🤖 Give back to AI** or closed |
| 🛑 Stop all | nothing at all |
| ☑ / ☐ | tick / untick one item (unticking a new issue unticks what depends on it) |
| ✅ Apply ticked | only the ticked items; the answer keeps waiting |

After every decision the bot says what it did and asks for an optional **note** (reply to that message). The note
is saved as a case note that the AI sees on every later run of the chat.

**Typed replies still do everything they did before**: questions ("why did you say that?"), plan edits ("remove 2",
"2 urgent"), ClickUp task commands ("done", "no task needed"), facts ("wifi password is B123"), lessons
("next time ..."), `test ...` dry runs. "ok" = Approve all, "stop" = Stop all, "don't send" = Don't send. A typed
correction becomes a draft (one more click to send).

Safety checks:
- **Versions**: every change (checkbox, plan edit, draft) bumps the card version; a press on an outdated keyboard is
  refused.
- **Stale cards**: a new tenant or staff message makes older waiting proposals stale (buttons removed, presses
  refused). The new run is told about them (`PENDING_PROPOSAL`) and repeats what is still needed.
- **Final recheck**: Approve is refused when the tenant wrote again after the proposal.
- Staff answered the tenant directly meanwhile → the AI answer is not sent (as before).
- Card could not be posted → nothing is sent or done; an error goes to the AI chat.

Anyone in the AI group may press; the name is logged (`AIRun.review.decisions`).

## After-hours message (`mysite/ai_agent/after_hours.py`)

Text: AI Management prompt `ai_after_hours_ack` (client text + "If this is an emergency, call 911. For an urgent
issue, reply URGENT."). The worker checks every tenant message within seconds (`process_pending`), independent of
Claude and before the 1-minute burst wait.

- Once per rolling 5 hours per tenant (merged chats = one); the time counts only after Twilio accepted it.
- Sent at any hour (skips the 21:00–08:00 SMS hold). Other AI SMS still respect the hold.
- Not sent: office hours, pure "thanks"/"ok", staff wrote in the chat in the last 30 minutes, unlinked chats.
- Test apartments: `WOULD AUTO-SEND` on the card, nothing sent.
- Failure: alert + one retry 3 minutes later.
- Every decision is a row in `AIAfterHoursAck` (sent / would_send / suppressed + reason / not_applicable / failed).

## URGENT and emergencies: phone call (`mysite/ai_agent/calls.py`)

- Tenant writes URGENT ("not urgent" doesn't count) outside office hours, or the AI rates a message an emergency
  (any hour) → Twilio calls StaffMember `Farid` (`AI_AGENT_CALL_STAFF`), his second number if the first doesn't
  answer, then tells Telegram if neither answered. From `+13153524379` (voice-capable, `AI_AGENT_CALL_FROM`).
- One call per tenant per 30 minutes. Test apartments: simulated (`AI_AGENT_CALLS_IN_TEST=on` for a real test call).
  `AI_AGENT_CALLS=off` switches calls off. Rows: `AIAlertCall`.
- An URGENT message skips the 1-minute wait and its card is at least 🔴 urgent.

## Case tracking (`mysite/ai_agent/cases.py`, new `AIIssue` fields)

- `stage`: reported → acknowledged → owner_accepted → answered → resolved (forward only; `stage_times`).
- `next_action`, `tenant_deadline` from the AI's triage; reminders **24 h and 2 h** before the deadline
  (`deadline_reminder`, Kevin at 2 h if nobody acted). They don't count against the 8-reminder cap.
- `tenant_asks`: how often the tenant asked; the card shows "asked N times, waiting X h, owner, next".
- `telegram_thread_message_id`: the first card of the issue; later cards about it reply to it (one thread per issue).
- `handled_by`: "I'll handle". The backend drops AI actions on it, cancels and blocks its reminders, and turns a message
  only about it into an info card with Give back.
- `/ai-issues/` shows stage, next action, deadline, asks, handled.

## Closed ClickUp task

When a reminder finds the task closed, the other reminders stop and the AI proposes: "Our team marked the ___ as done.
If you still have any problem, just let us know." + resolve the issue. After ✅ it is sent and the issue is closed.
(Timer mode keeps the old quiet close.)

## Other changes

- Office hours 09:00–18:00 everywhere (routine staff reminders now start 09:00). `config.is_office_hours()`,
  `config.us_federal_holidays()`; the AI gets `OFFICE_HOURS` and a real `IS_HOLIDAY`.
- Output schema: `primary_type`, `secondary_types`, `priority`, `case_status`, `issue_refs`, `owner`, `next_action`,
  `tenant_deadline`, `verified_facts`, `uncertainties`, `no_reply_reason` → `AIRun.triage`.
- Prompt: the main prompt is V4 (client workflow merged into V3; Farid for exceptional decisions); runtime notes
  describe approval, PENDING_PROPOSAL, AFTER_HOURS_ACK, HANDLED BY STAFF, closed tasks.
- Failed answer send: one retry 3 min later (`answer_review.retry_failed_sends`); held SMS that fail at flush: one
  retry 3 min later (`PendingOutboundMessage.attempts`).
- Telegram is read every 3 s (`AI_AGENT_REVIEW_POLL_SECONDS`) so buttons react at once.

## Switches

| Setting | Effect |
|---|---|
| `AI_AGENT_APPROVAL=timer` | back to the old 15-minute "silence = yes" review |
| `AI_AGENT_REVIEW_HOLD_MINUTES=0` | no review at all (everything at once) |
| `AI_AGENT_CALLS=off` / `AI_AGENT_CALLS_IN_TEST=on` | calls off / real calls for test apartments |
| `AI_AGENT_CALL_STAFF`, `AI_AGENT_CALL_FROM` | who is called / from which number |
| `AI_AGENT_OFFICE_START` / `_END` | office hours (default 9 / 18) |
| prompt `ai_after_hours_ack` | the after-hours text |

## Deploy steps (need the user's OK: production)

1. Bring the branch `client-spec-v4` into `main`.
2. `python manage.py migrate` (0096: new AIIssue/AIRun fields, AIAfterHoursAck, AIAlertCall, PendingOutboundMessage.attempts).
3. `python manage.py sync_ai_prompts --reset ai_agent_system`, `--reset ai_agent_runtime_notes`,
   `--reset ai_agent_review_interpreter` (all three are unedited defaults in the DB), then `sync_ai_prompts` (creates
   `ai_after_hours_ack`).
4. `pm2 restart ai-agent --update-env` and `./start.sh` (site: issues page, views); `pm2 restart telegram-notifications`
   is not needed (cron.js unchanged).
