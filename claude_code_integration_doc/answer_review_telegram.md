# Staff review in Telegram: nothing happens for 15 minutes

Requested 2026-09-23. Code: `mysite/ai_agent/answer_review.py` (review, replies, running the plan) and
`mysite/ai_agent/plan.py` (describes a plan without changing anything). Tests: `testbed/e2e_answer_review.py`
(45 checks).

## The rule

**Nothing an AI run wants is done at once.** This covers:
- the answer to the tenant
- opening or updating issues
- reminders and notes
- creating ClickUp tasks, commenting on them and closing them
- saving knowledge

All of it becomes a **plan** in the Telegram alert. After `AI_AGENT_REVIEW_HOLD_MINUTES` (default 15), the plan is
carried out, unless someone replied and changed or stopped it. The thread is then told what was done.

Test-mode apartments work the same way, except that the answer never goes to Twilio.

Emergencies (an `emergency` action or an emergency word in the message) skip the review and happen at once.

## The alert

```
🔔 720-101 · Rita Tenant
🟢 LIVE · TENANT_MESSAGE · Sep 23 11:51 ET, Florida

▶ [2026-09-23 11:51] Rita Tenant (TENANT): The kitchen sink is dripping

⏳ NOTHING IS DONE YET. At 12:06 ET, Florida, unless someone replies to this message:
• 💬 Send this answer to the tenant:
   "Thanks, I've logged the dripping sink with the team."
1. 🆕 Open issue "Kitchen sink dripping" · MAINTENANCE_OPEN · owner Edy
2. 🎫 Create ClickUp task "[AI] 720-101 · Kitchen sink dripping"
   List: 720-101 · routine · due 72h after creation · assigned: Farid, Janna, Edy
3. ⏰ Reminder (staff_reminder) about the new issue "Kitchen sink dripping": recheck the sink - would fire about Thu 10:00
4. 📚 Save to the knowledge base for 720-101 (this apartment): sink_brand = Moen
   → CANDIDATE - NOT used by the AI until a manager approves it (reply "approve N")
💡 maintenance

👤 FOR THE TEAM (this message is the notification):
• ROUTINE · for Edy (Farouk Ahmed) about the new issue "Kitchen sink dripping"
  sink dripping

🔗 http://68.183.124.79/ai-runs/123/

↩ REPLY to this message to change it: "ok" = do it all now · "stop" = do nothing · "remove 3" · "3 urgent" ...
```

Other items the plan can contain:
- **Closing an issue:** "🔄 Issue i-25 …: MAINTENANCE_OPEN → RESOLVED (its reminders stop) 🔒 and CLOSE its ClickUp task →
  link". It includes the task's live status, assignees, due date and last comment, read from ClickUp.
- **Commenting on a task:** "💬 Comment on ClickUp task t-25 … → link", with the same task details.
- **Notes and cancelled reminders:** internal notes, and "⏹ Cancel reminder f-7".
- **A ticket for an issue that already has a task:** "🎫 No new ClickUp task: … already has one → link".
- **An apartment without a ClickUp List:** "NO ClickUp task: this apartment has no ClickUp List (Telegram only)".

Staff alerts (INTERNAL_ALERT / QUEUE_FOR_REVIEW) aren't changes. The alert message itself is that notification, so
they appear under FOR THE TEAM and aren't numbered.

## Replies (use Telegram's reply on the alert)

| Reply | Result |
|---|---|
| `ok` / `go ahead` | everything at once: the answer and the whole plan |
| `stop` / `cancel` | nothing at all: the answer and the plan are cancelled |
| `don't send` | only the answer is cancelled; the plan still happens |
| "remove 3", "no reminder needed" | that item is removed. Removing a new issue also removes the items that depend on it |
| "no task needed" | removes the task and the items that only exist for it (the new issue, its reminders) |
| "keep it open" / "not fixed" | removes the item that would resolve the issue and close its task |
| "2 urgent", "rename the task to …", "the value is …" | that item is changed (priority / title / text / value / state / owner) |
| "approve 4" | a knowledge item is approved and saved as VERIFIED when the plan runs |
| a corrected answer / "tell her …" | sent now instead of the AI answer (the plan still runs on its timer) |
| "done", "delete that task", "make it urgent", "create a task for …" about an **existing** ClickUp task | done at once on that task |
| a new fact ("the wifi password is B123") | saved at once as VERIFIED knowledge for the apartment or company-wide |
| "next time …" / "always …" | a lesson; the AI follows it on similar messages |
| `test …` | dry run: the bot says what it would do and changes nothing. A plain `test` shows the current state |

One reply can combine these, for example "ok but don't save the router thing", or "make it urgent and remove the
reminder, router is actually in the bedroom". Every reply gets a report in the thread with these sections:
👤 Tenant · 📋 Plan (the edits, then the plan as it stands) · 🗂 Existing ClickUp tasks · 📚 Knowledge base ·
🤖 AI instructions.

When the time is up, the thread gets, for example:

```
⏰ Review window over, nobody stopped it - done now:
✅ Answer: sent to the tenant as written.
✅ 1. 🆕 Open issue "Kitchen sink dripping" · MAINTENANCE_OPEN · owner Edy
   → created i-1
✅ 2. 🎫 Create ClickUp task "[AI] 720-101 · Kitchen sink dripping"
   → CREATED → https://app.clickup.com/t/...
```

## Several messages in a row

- **The next run is told about the waiting plan.** It sees `PENDING_PLAN of your earlier run #N` and must not repeat
  those actions. It can refer to an issue that plan will create as `rN:new-1`. If the later plan is carried out first,
  for example on "ok", the earlier plan is carried out before it.
- **A newer answer replaces a held one.** The AI sees the held draft as `PENDING_AI_ANSWER`. The earlier plan still
  happens.
- **Staff answer the tenant directly in the chat.** The held answer is dropped when its time is up.

## Knowledge confidence

When a plan saves knowledge, the usual rules apply:
- **VERIFIED** only when a staff message started the run, and the entry is not a policy or company-wide.
- **Otherwise CANDIDATE:** not used until approved.
- **Approved in the review:** "approve N", or a corrected value, saves it as VERIFIED with the staff member as reviewer.
- **Facts in staff replies** are VERIFIED at once.

The old OpenRouter knowledge extractor is off while the Claude backend is on (user decision 2026-09-23).

## How it runs

- **Where it is stored.** `AIRun.review.plan` holds `status` (pending / applying / applied / cancelled), the action
  list, the item descriptions and, after it runs, `temp_map`. The answer uses `hold_status`, and both share the
  timer `hold_until`. Report files: `07_actions.json` (the real results after the plan runs) and `09_review.json`.
- **Worker timing.** The `ai-agent` worker reads Telegram every `AI_AGENT_REVIEW_POLL_SECONDS` (default 60) and checks
  for due work every 5 s. Before doing anything due, it reads Telegram once more.
- **Watchdog.** It alerts when an answer or plan is past its time and still not done.
- **Telegram alert failed.** Everything happens at once, because nobody could review it.
- **Turning it off.** `AI_AGENT_REVIEW_HOLD_MINUTES=0` switches the review off: things happen at once, as before.
