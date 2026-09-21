# How AI message handling works — simple overview

## The idea in one sentence

A tenant writes in the group chat → the CRM saves the message → the AI reads it together with
everything we know about the apartment and booking → the AI either answers, asks one short
question, or stays silent and tells the team.

## Step by step (new Claude flow)

```
1. Tenant sends SMS
        │
2. Twilio calls our webhook ──► message is saved in the CRM (TwilioMessage)
        │                        and put in a waiting line (AIEvent). Takes < 1 second.
        │
3. The worker "ai-agent" (always running in PM2) picks it up.
        │   It waits 1 minute after the tenant's LAST message, so if the tenant sends
        │   3 messages in a row they are answered once, together. Two exceptions:
        │   • a message that looks like an emergency (fire, smoke, gas, flooding, 911…) → no wait
        │   • a test message typed by a manager in the CRM chat page → 5 seconds
        │
4. The worker builds the "package" for Claude:
        │   • the rules (Farid's prompt)
        │   • date and time
        │   • knowledge base: company + this apartment
        │   • booking: tenant, dates, status, payments, parking, cleanings
        │   • last 20 chat messages, each marked TENANT / STAFF / AI 
        │   • the new message(s)
        │
5. Claude thinks. If it needs older messages it can look them up
        │   (only in THIS chat — it cannot see other tenants or do anything else).
        │
6. Claude returns three things:
        │   • answer  – text for the tenant, or NO_ANSWER
        │   • actions – what the team should do (alert, ticket, follow-up, note…)
        │   • why     – one or two sentences explaining the decision
        │
7. The CRM checks the result and decides what really happens:
        │
        ├─ apartment has "real AI answers" ON  → answer is sent by SMS to the group chat,
        │                                         alerts go to the team (see "Where alerts go")
        └─ apartment has it OFF (test mode)    → nothing is sent; the answer and actions are
                                                  only saved so you can review them
                                                  (see "Test mode: global vs per apartment")
        │
8. Everything is saved: the result in the CRM (visible in the chat page and /ai-runs/)
    and a full report folder in logs/ai_runs/ (input, steps, output, tokens, cost).
```

## Where alerts go

Alerts from the AI to the team are **new** — the old AI never notified anyone, it only answered
or stayed silent. What already existed in the system:

| Existing channel | Used today for |
|---|---|
| Telegram error chat (`TELEGRAM_ERROR_CHAT_ID`) | system errors only |
| Telegram groups check-in / check-out / cleaning / payments | the daily 8:00 / 9:00 / 21:00 reports |
| Twilio **manager group chat** (`MANAGER_CHAT_SID`) | "Message wasn't delivered" notices from the assistant |

**Rule (decision 2026-09-21): everything about the AI goes to ONE Telegram chat** —
`AI_AGENT_ALERT_CHAT_ID` in `.env`. Nothing is split between chats, and nothing goes to the
Twilio manager chat by SMS.

What arrives there:

| Message | When |
|---|---|
| 🤖 staff alert (`INTERNAL_ALERT`, `QUEUE_FOR_REVIEW`, `CREATE_TICKET`) | live mode |
| 🧪 the same alert marked "TEST MODE (tenant was NOT answered)" | test mode |
| 🚨 run failed / timed out — with the tenant's message, so a human can answer | always |
| 🚨 AI answer was NOT delivered to the tenant (Twilio error) | live mode |
| 🚨 worker crashed / message could not be queued | always |

Until `AI_AGENT_ALERT_CHAT_ID` is set, all of the above goes to the existing Telegram error
chat (`TELEGRAM_ERROR_CHAT_ID`) — still one place, and nothing is lost. Errors are also saved in
the CRM error log as before. `ai_agent_replay` (offline replays) never notifies anyone.

Each alert contains: action type and priority, unit, tenant, who it is for, the AI's text and the
tenant's message. ClickUp apartment channels replace this in Phase 4.

`TELEGRAM_CHAT_ID_MANAGER` exists in `.env` but no code uses it, so alerts are not sent there.

## Test mode: global vs per apartment

Only the **per-apartment** switch is active now.

- `Apartment.ai_group_chat_enabled` ("Enable real group chat AI answers") is the only thing that
  decides whether an AI answer is really sent. OFF = test mode for that apartment.
- The global `AI_ASSISTANT_ENABLED` in `.env` (currently `false`) **no longer stops sending**.
  This came with the per-apartment change made before the Claude work: in the old flow it only
  selects which code branch runs, and both branches obey the apartment switch. The new Claude
  flow does not read it at all.
- State on 2026-09-21: **all 87 apartments are OFF → everything is test mode.** Three apartments
  (`720-213`, `630-214`, `630-429`) were ON until today and the old AI had really sent 5 answers
  (last one 2026-09-20, even though `AI_ASSISTANT_ENABLED=false`); they were switched off on
  2026-09-21 and the change is in the audit log.
- To go live for an apartment: tick "Enable real group chat AI answers" on it. Nothing else is needed.
- There is no global "stop all sending" button right now. If you want one, it is a small change:
  make `AI_ASSISTANT_ENABLED=false` block sending everywhere, on top of the apartment switch.

## Who decides what

| Claude decides | The CRM decides |
|---|---|
| What to say to the tenant (or to stay silent) | Whether the message is really sent (test mode / live) |
| What the team needs to do and who owns it | How the team is notified (Telegram now, ClickUp later) |
| How urgent it is | When reminders fire (timers live in the CRM) |

Claude never sends anything itself. It only returns text and a list of requests; the CRM does the rest.

## When the AI answers and when it stays silent

- **Answers:** facts we have on record — WiFi, address, parking, check-in/out instructions, booking dates, house rules.
- **Asks one question:** only when a detail is missing to log a problem ("Which bathroom?").
- **Stays silent + tells the team:** anything that needs a human — money, refunds, late checkout, extensions, scheduling, exceptions, complaints.
- **Stays silent:** "ok", "thanks", and replies meant for staff.
- **Always answers immediately:** emergencies (fire, flooding, gas) — safety first, and the team is alerted.

## Safety nets

- **If a manager answers first, the AI still writes its answer — but only for review.** It is never
  sent to the tenant, even on a live apartment. In the chat page it appears under the tenant's message
  like a test-mode answer, marked "[NOT SENT - staff answered first, shown for review only]", so you can
  compare what the AI would have said with what the manager said. This covers both cases: the manager
  replied during the 1-minute wait (Claude sees the reply, stays silent and fills a "review answer"),
  or the manager replied while Claude was already working (the finished answer is held back).
- If Claude says "I've passed it to the team" but forgot to create the alert → the CRM creates the alert itself.
- If Claude fails or times out → the tenant's message goes to the AI Telegram chat so a human sees it.
- An emergency message is never held back by the 1-minute wait.
- The tenant cannot "talk the AI into" anything: tenant text is treated as data, and Claude has no tools that change anything.

## Messages sent from the CRM chat page (UI)

The chat page has two "send as" modes, and they are handled differently:

**Send as client** (testing the AI — the page adds `(+++)` to the text)
1. The message is saved. If **"Send to group chat"** is ticked it is also really posted into the
   Twilio group; if not, it stays only in the CRM (nobody's phone gets it).
2. It is put in the same waiting line as a real tenant message, with a 5-second wait instead of 1 minute.
3. Claude handles it exactly like a tenant message. The page shows "AI agent is working…",
   then the answer, the "why", and a link to the run report.
4. The AI answer is really sent **only if both** are true: "Send to group chat" is ticked **and**
   the apartment has real AI answers ON. In every other case it is test mode: answer saved, not sent.
5. Alerts go to the AI Telegram chat as described in "Where alerts go" (marked TEST when not live).

So yes — a message sent from the UI is always handled by the AI, in test mode or live; the two
switches only decide whether the answer leaves the CRM.

**Send as manager** (a normal staff message)
- The message is sent to the group chat as usual. The AI does **not** answer it.
- The old knowledge-base logic checks it for reusable facts (see below).

Requirement for both: the chat must be linked to an apartment **and** a booking, otherwise the
page says the AI is not ready and nothing is run.

## Messages from managers

When a manager writes in the tenant chat, two things happen:
1. Claude is woken with `STAFF_MESSAGE`. It normally says nothing to the tenant; it updates its
   issues ("Edy is handling this"), stops reminders that are no longer needed, and sets a tenant
   reminder if the manager asked the tenant for something.
2. If the manager said something reusable (new WiFi password, trash room), Claude saves it as
   **verified knowledge** and uses it from the next message. General rules and anything a tenant
   claims wait for a manager's approval on `/ai-knowledge/`. One-time arrangements become case
   notes, never knowledge. (The old knowledge extraction still runs next to this.)

## What the AI is not allowed to see or say (Phase 3)

- **Access codes** reach the AI only from 24 hours before check-in until checkout. Earlier than
  that the codes are hidden from it, and if an answer still contained one the CRM blocks it.
- **Payments:** the AI sees the booking's payment rows; with none on file it must send the
  question to Janna instead of guessing.
- Full description: `phase3_knowledge_guards.md`.

## Issues and reminders (Phase 2)

- When something needs a human or a repair, Claude opens an **issue** for the chat and the CRM
  keeps it until it is really resolved — not just until someone replied.
- Claude asks for a **reminder**; the CRM sets the clock (30 min / 1 h / 3 h / 24 h, never at night
  for routine things). When the time comes the worker wakes Claude, Claude looks at the chat again
  and either does nothing (already handled) or reminds the right person.
- Managers see all of this in the chat page ("AI agent activity") and on `/ai-issues/`, and can
  close an issue by hand, which stops the reminders.
- Full description: `phase2_issues_followups.md`.

## Old flow vs new flow

| | Old (OpenRouter) | New (Claude agent) |
|---|---|---|
| Where the AI runs | Inside the webhook, Twilio waits | In a separate worker, webhook returns at once |
| Output | answer + why | answer + actions + why |
| Team notifications | none | one Telegram chat for alerts and errors, also in test mode (ClickUp later) |
| Several quick messages | each answered separately | answered once, together (1-minute wait) |
| What you can inspect | a log file | a full report folder per run + `/ai-runs/` page |
| Switch | AI Management → `AI Backend` = `openrouter` | `AI Backend` = `claude_cli` |

Both flows use the same on/off switch per apartment for real SMS, and the same chat page.
