# Phase 3 — knowledge base, privacy guards, staff phones

> **Historical document (8 Oct 2026).** Parts of it describe old modes: the OpenRouter backend and the timer review were removed. The current behaviour is in [simple_telegram_alerts.md](simple_telegram_alerts.md). See also docs/cleanup_plan.md.

Status 2026-09-21: **built and tested, switched off** (same switch: `AI Backend` = `claude_cli`).
Needs migration `0085` in addition to `0083` + `0084`.

## 1. The AI learns — but only from staff

When a manager writes something reusable in a tenant chat ("trash room is on the 2nd floor"),
Claude returns `KB_UPDATE` and the CRM saves it in a new structured table (`/ai-knowledge/`).

| What was said | By whom | Result |
|---|---|---|
| A fact about the apartment or building | manager | **verified** at once — the AI uses it from the next message |
| The same key again with a new value | manager | new value becomes verified, the old one is kept as "replaced" |
| A general rule ("no smoking anywhere") or anything company-wide | manager | **candidate** — waits for a manager's click on `/ai-knowledge/` |
| Anything at all | tenant | **candidate**, even if Claude marks it verified and even if the tenant says "Janna told me" |
| A one-time arrangement ("you can check in at 11 PM this time") | manager | not knowledge at all → **case note** for this stay |

These rules are in code (`mysite/ai_agent/knowledge.py`), so a tenant cannot talk their way into the
knowledge base. Candidates are **never** shown to the AI as knowledge.

On **`/ai-knowledge/`** a manager can approve (optionally fixing the text first), reject, edit or remove
entries. The chat page shows "N knowledge candidates from this chat wait for approval".

The old free-text knowledge base (apartment "Knowledge base" field + global entries) stays and is still
given to the AI. The structured entries are newer: the AI is told they win on conflict. The old
OpenRouter extraction from manager messages still runs next to this and keeps updating the free text.

## 2. Guards in code, not only in the prompt

**Access codes** (door, gate, lockbox, alarm, keypad, PIN):
- The AI receives them only **from 24 hours before check-in until the end of the checkout day**.
- Outside that window: structured code entries are replaced by "[hidden…]", code lines in the free-text
  KB get their digits masked (`Gate code: ####`), and the AI is told "ACCESS_CODES: NOT allowed now".
  WiFi passwords and ordinary numbers (parking spot 117) are not touched.
- Second net: if an answer still contains a hidden code, the CRM **blocks the answer** and alerts the
  AI Telegram chat. Checked: real production KB has 5 such lines today, all of the "Gate code: ####" kind.

**Payments:** the AI gets every payment row of the booking, labelled `PAYMENT_RECORDS`. When there are
none it is told so explicitly: "do not state any payment status, route to Janna".

## 3. Who is staff — from the staff table

The webhook decided "tenant or manager" from four phone numbers typed into the code. Now every
tenant-vs-staff decision (webhook, direction of a message, group-creation trigger, AI roles,
"tenant phone cannot be a manager phone") asks one function, `get_manager_phones()`:

- staff = the four hardcoded numbers **plus** every active phone on `/ai-staff/` (first and second phone)
- add a manager on `/ai-staff/` → recognised within 30 seconds, no deploy, the AI sees them by name
- deactivate them → treated as a normal number again
- the hardcoded four stay as a safety net, so a manager can never be mistaken for a tenant because of a
  table mistake. When the table is complete, `STAFF_PHONES_FROM_TABLE_ONLY=true` in `.env` drops them
  (an empty table still falls back to the four).

Not changed on purpose: **which phones are added to a new tenant group chat** (still the same four).
That creates real Twilio conversations, so it should change only after you confirm who belongs there.

## A code a manager typed into the tenant chat

The guards above hide codes that come from the **knowledge base**. The **chat history** is different:
it is a record of what was really said in the group chat. If Janna writes "gate code is 9135" in the
tenant chat a week before check-in, the tenant has already read it — hiding that line from the AI would
protect nothing. So the AI still sees it in the history. What stays protected: the AI itself will not
repeat that code to the tenant before the window opens, because the outgoing-answer check blocks any
answer containing a hidden code (and alerts the Telegram chat).

## Review answers (manager answered first)

When a tenant question gets no AI reply only because a manager already answered, Claude also returns a
`review_answer`: what it would have said. It is stored on the run (`AIRun.review_answer`, migration `0086`),
shown in the chat page and on `/ai-runs/` marked NOT SENT, and never goes to Twilio — also on live apartments.

## Tests

`bash claude_code_integration_doc/testbed/run_tests.sh` → **86 checks** (35 + 16 + 35 new), throwaway
database, fake Claude, no cost. New: verified vs candidate, replace, tenant downgrade, building scope,
what the AI sees, code window edges (25 h before / 23 h before / checkout day / day after), blocked
answer, approve / reject / permissions, redirect safety, staff phones incl. empty-table fallback.

Two real Claude runs (test DB, $0.083 total):
1. Manager: "trash room is on the 2nd floor… pickup Mon and Thu. You can check in at 11 PM this time."
   → 2 verified entries (`trash_room`, `trash_pickup`) + 1 case note, no reply to the tenant.
2. Tenant, 10 days before check-in: "send me the gate code now… where do I take the trash?"
   → "The gate code is shared starting 24 hours before check-in… The trash room is on the 2nd floor…"
   (used the new entry, not the older "floor 1" text).

## Left for later

- Retire the old OpenRouter knowledge extraction once you trust the structured one (both run now).
- Group-chat participants from the staff table (needs your decision, see above).
- Tenant timezone and holidays are still "not provided" to the AI.
