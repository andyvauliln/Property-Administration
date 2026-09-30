# Plan: client's "AI Property Manager System Prompt and Workflow" (v4) in our AI agent

## Context

The client (Farid) sent a new spec for the tenant group-chat AI: 8 message types, explicit manager
approval in Telegram with buttons, per-issue case tracking with owners and deadlines, and an automatic
after-hours acknowledgment. The spec was written without knowing our system. Much of it already
exists (Claude worker, 15-min Telegram review, issues, reminders, ClickUp, test/live per apartment,
ClickUp write switch). Some of it **changes how things work today** and needs new development.
All open questions were answered by you (2026-09-30); the decisions are in section 4.

Once approved, a copy goes into the repo as `claude_code_integration_doc/client_spec_v4_plan.md`.
Production rule still applies: migrations, restarts, PM2 and live switches only with your OK.

---

## 1. Already there (keep)

| Client asks | Where it already is |
|---|---|
| Burst of messages → one case/answer | 1-min debounce, one run per burst (`service.claim_next_batch`) |
| Several issues in one message | multiple `CREATE_ISSUE` per run |
| Emergencies alerted at once | emergency words skip debounce + review (`answer_review.review_applies`) |
| Thanks/likes → no reply | `NO_ANSWER` rules (prompt STEP 2) |
| Staff already answered → no duplicate AI reply | `review_answer` + suppressed delivery (`service.deliver`) |
| Follow-up matched to existing issue, no duplicate task | `OPEN_ISSUES`/`OPEN_TICKETS` input, `TICKET_COMMENT` |
| Routing Edy / Janna / Kevin | prompt STEP 6, `StaffMember`, ClickUp assignees (`team_notify.py`) |
| Payments only from records → Janna; access codes only when verified | Phase 3 guards |
| TEST = nothing to tenant, LIVE per apartment | `Apartment.ai_group_chat_enabled` |
| ClickUp writes off → "would create" | `config.clickup_writes_enabled` |
| A proposal that waits for a manager, never auto-sent | legal-answer path (`needs_manager_confirmation`, `answer_review._needs_confirmation`), the base for the new approval model |
| Newer message replaces an older held answer | `answer_review._supersede_older`, `PENDING_AI_ANSWER` |
| Duplicate webhook doesn't run twice | `service._enqueue_message` dedupes per message |
| Typed Telegram replies (ok/stop/remove 3/lessons/facts) | `answer_review.handle_reply` + interpreter, **kept** |
| Full audit | `AIRun`, `logs/ai_runs/`, `/ai-runs/` |

## 2. Logic updates (same mechanics, new rules)

1. **Prompt merge**: client's prompt merged into the live DB row `ai_agent_system` (seed file
   `default_system_prompt.md` + `sync_ai_prompts`). Names, hours and mode are injected at runtime from
   `StaffMember`/config (`inputs.py`, `prompts.py`) instead of hard-coded.
2. **8 message types + NO_REPLY** as output fields: `primary_type`, `secondary_types`, `priority`,
   `case_status`, `tenant_deadline`, `next_action`, `uncertainties`, `verified_facts`,
   `no_reply_reason` (`schema.json`, stored on `AIRun`, shown on the card). Timing stays backend-computed.
3. **Farid** is the owner for exceptional decisions (routing text + `StaffMember` role).
4. **Office hours Mon–Fri 09:00–18:00** everywhere (`policy.STAFF_WINDOW` is 10–18 today, plus the prompt).
   **US federal holidays** are built into the code (no new library, computed by rule: New Year, MLK, Presidents,
   Memorial, Juneteenth, July 4, Labor, Columbus, Veterans, Thanksgiving, Christmas, with observed-day shift).
   Holidays count as outside office hours.
5. **No repeated "passed to the team"**: prompt rule + a repeat-question block on the card (asked N times,
   waiting X h, owner, next decision).
6. **Arrival time and similar updates → coordinator** without a tenant reply (prompt).
7. **Failed SMS**: one automatic retry 3 min after the failure alert; if that also fails, a second alert, no more retries.

## 3. Conflicts: new development / behavior changes

| # | Change | Today |
|---|---|---|
| C1 | **No tenant reply is sent without a click** (TEST and LIVE). No decision = never sent, no escalation for now | silence = yes: auto-sent after 15 min |
| C2 | **Buttons**: ✅ Approve · ✏️ Replace · ❌ Reject · 👤 I'll handle, plus **checkboxes** per plan item and **Apply selected** | typed replies only |
| C3 | **Replace** = typed text becomes a draft → shown again → second ✅ | corrected text sent at once |
| C4 | **Versioned proposals**: a new tenant/staff message makes the old card STALE (buttons off), and a new card (v+1) is posted in the thread. The chat is re-checked right before sending | supersede without versions |
| C5 | **I'll handle**: the AI stops everything on that issue (no drafts, reminders or ClickUp changes). New tenant messages are posted in the thread as info only, until **🤖 Give back to AI** is pressed or the issue is closed | STAFF_HANDLING state has no effect |
| C6 | **Note after any button**: the bot asks for an optional note. The note is saved on the case and the AI sees it on every later run | none |
| C7 | **After-hours auto-message**: exact client text + emergency line, sent at once at any hour (skips the 21–08 SMS hold), max once per 5 h **per tenant** (merged chats = one), no approval in LIVE, "WOULD AUTO-SEND" in TEST | doesn't exist |
| C8 | **URGENT keyword + phone call**: a tenant reply "URGENT" or an AI-detected emergency gives a 🚨 card and a **Twilio voice call to Farid** (+15614603904, then +15612205252 if no answer) | no calls |
| C9 | **Emergency safety reply stays instant** (fire/smoke/gas/CO/major flood/sparks/break-in/injury only). Urgent-but-not-dangerous (no water, lockout, AC) = instant card, reply waits for ✅ | same, keep |
| C10 | **Case stages**: acknowledged / owner accepted / answer sent / resolved stored separately, plus tenant deadline, next action and due | one `state` field |
| C11 | **Deadline reminders**: 24 h and 2 h before the tenant deadline; at 2 h with no staff action, tag Kevin too | fixed delays only |
| C12 | **Closing**: ClickUp task closed → AI proposes "Our team marked the ___ as done. If you still have any problem, just let us know." (needs ✅) → sent → issue closed. Tenant says still broken → reopened, Edy alerted | closed task = silent auto-resolve |
| C13 | **One thread per issue**: later cards for an issue reply under its first card (no Telegram Topics) | one message per run |
| C14 | **New card layout** (client template) with the after-hours line; all cards are posted immediately | numbered-plan layout |

## 4. Decisions (your answers, 2026-09-30)

| Q | Decision |
|---|---|
| 1 silence | never auto-send tenant text; undecided = not sent; no escalation for now |
| 2 internal actions | checkboxes: tick which plan items to apply |
| 3 emergency | instant safety reply stays (real emergencies only) |
| 4 approvers | anyone in the group; the clicker's name is logged |
| 5 night ack | send at once, any hour (bypasses 21–08 hold; other SMS unchanged) |
| 6 hours | 09–18 everywhere; US federal holidays built in |
| 7 emergency line | "If this is an emergency, call 911. For an urgent issue, reply URGENT." + Twilio call to Farid |
| 8 cooldown scope | per tenant, merged chats count as one |
| 9 closing | tell the tenant it's done + "let us know if still a problem", with ✅ |
| 10 threads | reply chain, no Topics |
| 11 routine cards | posted immediately, with the after-hours status line |
| 12 deadlines | 24 h + 2 h before, then Kevin |
| 13 I'll handle | AI fully out until "Give back to AI" or closed |
| 14 typing | keep all typed replies next to buttons |
| 15 SMS retry | one retry 3 min after the failure alert |
| 16 prompt | merge |

## 4b. Typed replies stay fully working (your requirement)

Buttons are an addition, not a replacement. Replying to any card in plain words keeps doing everything
it does today (`answer_review.handle_reply` + interpreter prompt `ai_agent_review_interpreter`):

| You type (reply to a card) | Result |
|---|---|
| a question: "why did you answer that?", "what payment is pending?" | answered in the thread, nothing changes (`decision: question`) |
| fixes to the plan: "remove 3", "2 urgent", "rename the task to …", "owner Janna" | plan items changed, the card is refreshed with the new checklist |
| a corrected answer / "tell her …" | becomes the new draft on a refreshed card → one ✅ sends it (same as ✏️ Replace) |
| "ok" / "send it" | same as ✅ Approve (explicit approval) |
| "stop" / "don't send" | same as ❌ Reject |
| existing ClickUp task: "done", "make it urgent", "comment …" | done on that task at once (as today) |
| a fact ("wifi password is B123") / a lesson ("next time …") | saved at once (as today) |
| a note ("call her after 5pm") | saved as a case note the AI follows |
| `test …` | dry run, nothing changes (as today) |

Changes for the new model: the interpreter gets the card version and the checkbox state. A reply to an
older (stale) card in the issue thread applies to the latest version of that issue. Typing and buttons can be
mixed on the same card. Tests for these are included in `e2e_client_v4.py` next to the existing `e2e_answer_review.py`.

## 5. Build order (each phase shippable, tested on Test_Apart2 first)

**Phase A: prompt + output fields + hours/holidays**
- Merge prompt; runtime injection of staff/hours/mode; new output fields stored on `AIRun` (migration).
- `config.py`: office hours 09–18, `is_office_hours(now)`, `us_federal_holidays(year)`; `policy.py` uses them.
- Files: `default_system_prompt.md`, `prompts.py`, `inputs.py`, `schema.json`, `service.py`, `policy.py`, `config.py`, `models.py`.

**Phase B: after-hours auto-message + URGENT + phone call**
- New `mysite/ai_agent/after_hours.py`: `decide(tenant_group, event, now)` → SEND / SUPPRESSED (reason, last sent) / NOT_APPLICABLE; suppressed when staff wrote in the chat in the last few minutes.
- New table `AIAfterHoursAck` (tenant group key = `conversation_groups.main_sid`, unique event id, status, sent_at, message_sid, mode). Time saved only after a successful Twilio send.
- Hooked where tenant events are queued (`service.enqueue_tenant_message`), independent of Claude; sends via `send_messsage_by_sid` directly (skips review + SMS hold); TEST = log + card line only.
- URGENT keyword detection → emergency event path; new `notify.call_on_call(...)` with Twilio `calls.create` + TwiML `<Say>`, fallback to the second number. **Check first**: our Twilio number must be voice-capable.

**Phase C: approval model (buttons, checkboxes, notes, versions)**
- `answer_review.fetch_updates`: add `callback_query`; `answerCallbackQuery` + `editMessageReplyMarkup` for checkbox toggles and "decided by X" state.
- Callback data `a|r|x|h|t|s|g:<run>:<version>[:item]` (approve / replace / reject / handle / toggle / apply-selected / give-back).
- `release_due`: tenant text no longer auto-released (reuse the legal "wait for ok" path for every answer); plan items no longer auto-applied, only by Apply selected / Approve.
- Replace: bot asks for text → new draft card → ✅. Note prompt after each button → `AICaseNote`.
- Versions in `AIRun.review` (`proposal_version`, STALE status); recheck chat before send.
- I'll handle / Give back: issue flag; `service` skips drafting + reminders for handled issues.
- Files: `answer_review.py`, `plan.py`, `notify.py`, `service.py`, `actions.py`, `models.py`.

**Phase D: case tracking + threads + card layout + closing**
- `AIIssue`: `acknowledged_at`, `owner_accepted_at`, `answered_at`, `tenant_deadline`, `next_action`, `next_action_due`, `handled_by`, `telegram_thread_message_id` (migration).
- New reminder kind `deadline_reminder` (24 h / 2 h, Kevin at 2 h) in `policy.py`/`actions.py`/`service.fire_due_followups`.
- `service.check_tickets_before_reminder`: closed task → run Claude with a "task closed, tell tenant" event instead of silent resolve.
- New card builder per client template in `answer_review.py`/`team_notify.py`, replying into the issue thread.

**Phase E: delivery retry + tests**
- SMS retry (3 min after failure alert, once).
- New testbed file `testbed/e2e_client_v4.py`: 3 messages in 5 h; message after 5 h; urgent during cooldown; manager reply before approval; changed facts after approval (stale card); duplicate webhook; failed SMS + retry; ClickUp writes off; two issues in one message; holiday = after-hours; URGENT reply → call (fake Twilio); I'll handle silences AI; checkbox subset applied.

## 6. Verification

- `bash claude_code_integration_doc/testbed/run_tests.sh` (existing + new; 4 SMS checks are clock-dependent outside 08–21 ET).
- Sandbox chat `/chat/CHSANDBOXAIAGENT00000000000000001/` (Test_Apart2, `manage.py ai_agent_sandbox`): messages in/out of hours, every button/checkbox, Replace, I'll handle/Give back, stale card, then `/ai-runs/`, `/ai-issues/`, the Telegram thread and the ClickUp test List.
- One real test call to Farid's number only with your OK.
- Deploy per phase with your OK (migration + `pm2 restart ai-agent --update-env` + `./start.sh`).

---

## 7. Simple version: how it works after the update

1. **Tenant writes.** The AI sorts it into one of 8 types (urgent repair, routine repair, arrival/checkout,
   payments, booking, property question, complaint, "call me back") or "no reply needed" (thanks).
2. **Outside office hours** (weeknights, weekends, US holidays) the tenant gets one fixed message at once:
   "We received your message outside our regular office hours… If this is an emergency, call 911. For an
   urgent issue, reply URGENT." It is sent at most once every 5 hours per tenant.
3. **Tenant replies URGENT** (or the AI sees a real emergency) → 🚨 card in Telegram **and Farid's phone rings**.
4. **Every message gives a Telegram card**: what the tenant said, what we know, the exact reply the AI suggests,
   who owns it, and a checklist of what the AI wants to do (open issue, ClickUp task, reminder…).
5. **Nothing happens until someone clicks**: ✅ send · ✏️ write my own · ❌ don't send · 👤 I'll handle it.
   Tick/untick checklist items and press "Apply selected". After any button you can add a note that the AI will follow.
   Replying in words works like today: ask the AI questions about the card, fix the answer, change or
   remove items, close/update ClickUp tasks, teach it facts and lessons.
6. **If nobody clicks, nothing is sent.** If the tenant writes again first, the old card is greyed out and an
   updated one appears in the same thread.
7. **"I'll handle"** = the AI stays out of that issue until you press "Give back to AI" or it's closed.
8. **Deadlines**: if the tenant says "I arrive Friday 3 pm", the owner is reminded 24 h and 2 h before, with Kevin at 2 h.
9. **When Edy closes the ClickUp task**, the AI suggests telling the tenant "it's done, let us know if there's still a
   problem". After ✅ it is sent and the issue closes; if the tenant says it's still broken, it reopens.
10. **Real emergencies** (fire, gas, flood…) still get the instant safety reply without waiting.
