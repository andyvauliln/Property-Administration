# AI alerts: the test and the real group

Date: 8 Oct 2026. State: v5 alerts are live since 7 Oct 16:26 ET (commit ebf556d).

## 1. Short answer

1. The reminder alert has the old format because v5 has no reminder alerts yet (group C). A restart does not change this.
2. The AI said "cannot" because a restart and a test start are server operations. A reply can change only AI rules.
3. The test and the real group use the same AI code. But some parts are not built, and the test skips them. So the test did not show these gaps.

## 2. The reminder alert (f-533, 630-322)

| Time (ET) | What occurred |
|---|---|
| 7 Oct 17:44 | The AI made a team reminder f-533 for Janna (urgent case, so due in 30 min). |
| 7 Oct 18:15 | The reminder became due. The worker started an AI run (event `FOLLOWUP_DUE`). |
| 7 Oct 18:15 | The v5 code makes alerts only for tenant messages, team messages and notifications (`mysite/ai_agent/alerts_v5.py:81`). A due reminder is not in this list. |
| 7 Oct 18:15 | So the old v4 code made the alert: "🔴 URGENT · TYPE 4 … TENANT SAID … INTERNAL ACTION". |

- This is the only old card since the deploy. But v5 makes reminders automatically for many cases, so more old cards will come.
- The worker already runs the new code. A restart loads the same code again, so it does not help.
- The spec of the new reminder alert is part 5 (C1–C8) of `claude_code_integration_doc/simple_telegram_alerts.md`.

## 3. Why the AI said "cannot"

What a reply under an alert can do today:

| Request in a reply | Result |
|---|---|
| Change how the AI works ("when ClickUp is off, do X") | `📏 AGENT RULE` + `✅ Apply Change`. The press writes the rule into TEAM RULES. |
| Change this case (answer, task, reminder, knowledge, parking) | The change + `✅ Apply Change`. |
| A harmful request | `⛔ NOT APPLIED` + the reason. |
| A server operation (restart, start a test, deploy) | `🛠 Needs a change in the code`. No button. |

Your request was a server operation, so it got the last row. That is how I built it. But two parts of the AI answers were wrong:

1. **The AI guessed the cause.** It did not know that reminder alerts are not built. It gave three "likely causes", and the restart cause was false. It must say "I do not know" when it does not know.
2. **It sent you to "Andrei (Engineering)".** This breaks your rule. The AI got the rule "do not send the manager to a developer" only for rule changes, not for questions.

## 4. The test and the real group: what is different

### 4.1 Same code, same result

The test runs these parts with the production code on real CRM rows. What you saw in the test is what the real group gets.

| Part | Note |
|---|---|
| The AI prompt and the context (booking, payments, parking, knowledge, chat) | The test adds only the sandbox rules block. |
| TENANT MESSAGE and TEAM MESSAGE alerts | Same text, same buttons. |
| Presses: Send, Edit Answer, Create Task, Close Reminder, Apply in CRM, apartment knowledge | Same code. |
| Typed replies (questions, changes, agent rules) | Same code. |
| After-hours decision and the after-hours message | Same code. |
| 08:00 notifications through the AI | Same code. In production, 2 notifications went through the AI on 8 Oct. |

### 4.2 Fake in the test on purpose (for safety)

These differences are correct. The test must not touch real people or real data.

| Part | In the test | In production |
|---|---|---|
| SMS to the tenant | A message in the sandbox chat | A real SMS (Twilio) |
| Phone calls (emergency) | Shown as made, no call | A real call |
| ClickUp tasks | TEST list, name `[SANDBOX]`, test assignees | The list of the apartment, the real team |
| Telegram | Test group, test bot `@pm_ai_alert_bot` | Real AI group, main bot |
| Test / live and ClickUp ON / OFF | The chapter sets them | The site sets them |
| Global knowledge, company rules, answer lessons, team rules | A sandbox file | The real knowledge base and prompts |
| Contract text | A sandbox file | The real contract |
| Notes in the alert, `🧪 Next test` / `Rerun` / `Stop`, Fix / No fix | Only in the test | Not shown |

### 4.3 Not the same: the real gaps

These parts do not work in production as they did in the test, or the test does not check them.

| # | Part | In the test | In production now | Effect |
|---|---|---|---|---|
| 1 | Due team reminders (C1, C2, C6, C7, C8) | Not built. The story skips these chapters. | Old v4 card | The card you got. |
| 2 | Due tenant reminders (C3, C4, D5, D6) | Not built. Skipped. | Old v4 card with a proposed text. Nothing goes to the tenant without a press. | A live tenant reminder is not sent automatically, as the spec says. |
| 3 | Daily reports (G1–G4) | Not built. Skipped. | No report | You see no list of open reminders, "2/2 no answer" or errors. |
| 4 | Who starts the work | The test runner takes the events itself and fires reminders with its own copy of the worker code. | The worker (`run_ai_agent`) does it. | A fault in the worker part does not show in the test. |
| 5 | Message batches | The test sends a group of messages at one time. | The worker waits some seconds and joins the messages that come in that time. | A different split of messages into alerts is possible. |
| 6 | SMS held for 08:00 | The test keeps them in memory and sends them at the chapter time. | `flush_pending_sms` sends them every 5 min (cron). | Different code. The result must be the same. |
| 7 | Global knowledge, answer lessons, team rules | Written to a sandbox file | Written to the real knowledge base and prompts | The first `📏 AGENT RULE` press in production creates the prompt "Agent - team rules". This has not occurred on the real database yet. |
| 8 | Old v4 cards in the real group | The test has no v4 cards. | Cards from before the deploy, and the reminder cards (#1, #2) | Their buttons and replies are not tested with v5. Your reply on the f-533 card went into the v5 reply logic. |
| 9 | Server operations from a reply (restart, start a test) | Not available | Not available | By design today. See part 5, step 2. |

## 5. Plan to fix

| Step | What I do | Where | Your OK |
|---|---|---|---|
| 1 | Build the v5 reminder alerts C1–C8: Close Reminder, Remind again in 24 hours, tenant reminder with Send now (test), "task closed" message, deadline reminders, hours. | `alerts_v5.py`, `service.py` (worktree) | no |
| 2 | Build the live tenant reminder: automatic send after a check, then the AI MESSAGE (D5, D6). | same | no |
| 3 | Fix the reply answers: the true system facts, "I do not know" when the cause is not known, never "ask Andrei / Engineering". | `alerts_v5.py` (`AGENT_NOTE`) | no |
| 4 | Add a short list of operations that a reply can start with a button. First one: `🧪 Send test reminder` (a reminder on the "Sandbox Test" apartment, due in 1 min). No restart from Telegram: a restart does not load new code, and anyone in the group could restart production. | `alerts_v5.py`, `answer_review.py` | no |
| 5 | Change the C, D5 and D6 chapters of the story from `not_built` to real chapters. Run them in the test group one by one with you. | `sandbox_cases.yaml` | no |
| 6 | Deploy: merge into main, restart `ai-agent`. | production | **yes** |
| 7 | Later: daily reports (G). | | ask first |

Rollback at any time: `AI_AGENT_ALERT_STYLE=v4` in `.env`, then `pm2 restart ai-agent --update-env`.
