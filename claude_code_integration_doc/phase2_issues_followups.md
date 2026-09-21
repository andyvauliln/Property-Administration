# Phase 2 — issues, actions, follow-ups, staff

Status 2026-09-21: **built and tested, switched off** (same switch as Phase 1: `AI Backend` = `claude_cli`).
Needs migration `0084` in addition to `0083`.

## What changed, in simple words

In Phase 1 Claude could only answer and the CRM wrote down what Claude *wanted* to do.
Now the CRM really does it:

| Claude returns | The CRM does |
|---|---|
| `CREATE_ISSUE` | Saves an issue for this chat (`i-12`), with owner and state. No duplicates: the same open issue is reused. |
| `UPDATE_ISSUE_STATE` | Moves the issue (waiting for Edy → staff handling → waiting for tenant confirmation → resolved). On *resolved* all its timers stop. |
| `SCHEDULE_FOLLOWUP` | Creates a timer. **The CRM chooses the time**, Claude only says what kind. |
| `CANCEL_FOLLOWUP` | Stops a timer. |
| `CASE_NOTE` | Saves a one-off arrangement ("late checkout 1 PM approved for this stay"). Never goes into the knowledge base. |
| `INTERNAL_ALERT`, `QUEUE_FOR_REVIEW`, `CREATE_TICKET` | Message in the AI Telegram chat, with staff names resolved ("Edy (Farouk Ahmed)"), linked issue and run report link. |
| `TICKET_COMMENT`, `UPDATE_TICKET` | Saved as a note on the issue (becomes a real ClickUp task update in Phase 4). |
| `KB_UPDATE` | Recorded only (Phase 3). |

Anything else Claude invents is **rejected** and shown as rejected in the run report. Claude can only touch
issues and timers of its own conversation.

## The loop

```
tenant: "AC blows warm air"
   → Claude: answer + CREATE_ISSUE + CREATE_TICKET(urgent) + INTERNAL_ALERT + SCHEDULE_FOLLOWUP(staff_reminder)
   → CRM: issue i-4 [MAINTENANCE_OPEN, urgent], Telegram alert, timer f-12 in 1 hour

1 hour later, nobody reacted
   → worker sees f-12 is due → wakes Claude with EVENT = FOLLOWUP_DUE
   → Claude re-checks the chat: still nothing → reminds Edy, schedules the next reminder
     (if the issue was resolved meanwhile the CRM does not even wake Claude)

Edy writes in the tenant chat: "technician fixed it"
   → EVENT = STAFF_MESSAGE → Claude: state → WAITING_FOR_TENANT_CONFIRMATION,
     asks the tenant "is everything working now?", timer tenant_nudge

tenant: "yes, thanks"
   → Claude: issue RESOLVED → CRM stops all timers of that issue
```

On every run Claude now receives the real `OPEN_ISSUES`, `OPEN_TICKETS`, `PENDING_FOLLOWUPS`, `CASE_NOTES`
and the `STAFF` list, so it knows what is already being handled.

## Timers (file `mysite/ai_agent/policy.py` — change times there, not in the prompt)

| Kind | Delay | Allowed hours |
|---|---|---|
| `escalation_check` | 30 min | staff hours 10:00–18:00 ET, else next 10:00 |
| `staff_reminder` routine | 24 h | staff hours, else next 10:00 |
| `staff_reminder` urgent / emergency | 1 h / 30 min | any hour |
| `tenant_nudge` | 3 h | tenant hours 9:00–20:00, else next day 10:00 |
| `second_tenant_nudge` | 24 h | tenant hours |

Safety cap: max 8 follow-ups per issue, so a forgotten issue cannot remind forever.
The worker checks for due timers every 30 seconds.

## Test mode

Issues, timers and case notes are saved **in test mode too** (marked `test`). Otherwise Claude would
forget everything between messages and the workflow could not be reviewed before going live.
Only what reaches the tenant is blocked in test mode. Alerts go to the AI Telegram chat marked
"🧪 TEST MODE". `ai_agent_replay` still saves nothing and notifies nobody.

## Manager messages

A manager's message in a tenant chat (from a manager phone, or "send as manager" in the CRM chat page)
now also wakes Claude as `STAFF_MESSAGE`, so it can update issues and schedule a tenant nudge when
staff asked the tenant for something. It normally replies nothing. The old knowledge-base extraction
still runs next to it. Cost control: `AI_AGENT_STAFF_EVENTS=all` (default) | `open_issues` | `off`.

## New pages

- **Chat page** — "AI agent activity" panel: open issues (with a *resolve* link), pending follow-ups,
  case notes, last runs.
- **`/ai-issues/`** — all open issues across apartments, next follow-up, *Resolve* button.
  Resolving by hand stops the AI's reminders for that issue.
- **`/ai-staff/`** (Admin) — who is Edy / Kevin / Janna: phone (how they are recognised in the chat),
  ClickUp id, role. Seeded by the migration: Edy = Farouk Ahmed, Kevin = Farid Gazizov, Janna, Andrei.
  **Only Janna's phone is known — please fill in Edy's and Kevin's phones there**, otherwise their
  messages appear to the AI as "Manager 1917" instead of "Edy".

## Tests (no production data, no cost)

`bash claude_code_integration_doc/testbed/run_tests.sh` builds a throwaway SQLite database and runs:
- `e2e_phase2.py` — 35 checks: the whole loop above with a scripted fake Claude, test/live mode,
  burst = one run, staff-reply guard, failed run → human alerted, rejected actions, follow-up cap,
  other conversation's issues untouchable, instant fallback to the old AI.
- `ui_test.py` — 16 checks: every new page and endpoint, permissions, path traversal, phone validation.

Also verified with 2 real Claude runs (test DB): first message created an urgent AC issue, ticket,
alert and a 1-hour reminder; the follow-up message reused the same issue (`i-4`) — no duplicates.
Cost $0.078 cold, $0.024 warm.

## Known limits (next phases)

- Manager phones are still hardcoded in `views/messaging.py` for deciding tenant vs staff in the
  webhook; `StaffMember` is used for names and roles. Replacing the hardcoded list is a Phase 3 cleanup.
- `QUEUE_FOR_REVIEW` is delivered immediately (one quiet Telegram chat), not held until staff hours.
- Tickets are fields on the issue until ClickUp is connected (Phase 4).
