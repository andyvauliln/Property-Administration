# ClickUp — test channel for the test apartment

Status 2026-09-21: server side built and deployed, **waiting for a ClickUp API token**. Until the token is in
`.env` nothing is sent to ClickUp; everything keeps going to the Telegram group as before.

## Update 2026-09-21: works WITHOUT an API token, through the server's Claude Code ClickUp connection

The server's Claude Code login already has ClickUp connected (`claude mcp list` → clickup ✔). When no
`CLICKUP_API_TOKEN` is set, delivery goes through it: after an AI run that has something for the team, the
backend starts a **separate, tiny headless Claude session** (model Haiku) whose only job is to post what the
backend composed. It may use exactly two ClickUp tools (create task, send chat message), it never sees the
tenant conversation, and the backend checks afterwards that it really called the expected List / channel.
First real result: sandbox "kitchen sink is dripping" → task https://app.clickup.com/t/86akmxdje ($0.021, ~15 s).

| | Claude Code connection (now) | API token (`CLICKUP_API_TOKEN`) |
|---|---|---|
| Setup | nothing | 3 minutes |
| Cost per notification | ~$0.02–0.07 | free |
| Speed | 10–20 s | < 1 s |
| Depends on | the personal Claude Code login and its ClickUp login staying valid | a token that does not expire |
| Survives the move to an Anthropic API key / Agent SDK | **no** — that connection belongs to the Claude Code login | yes |
| Who the message is from | the ClickUp user who connected it | the token's user (can be an "AI assistant" user) |

Fine for the test channel. For real apartments the token is the better choice; the code switches by itself as
soon as the token is in `.env`. `AI_AGENT_CLICKUP_VIA_CLAUDE=off` disables the Claude route.

**Decision 2026-09-21 (test period): tasks are ALWAYS created - test mode or live - and EVERY apartment's task goes to
this one test List**, assigned to the engineering staff member (Andrei). Setting: `AI_AGENT_CLICKUP_TEST_LIST_ID=901329128753`
in `.env`. Task names carry `[TEST]` in test mode and the unit name. Remove the setting later to send tasks to each
apartment's own List (`manage.py ai_agent_clickup --link ...`), assigned to the responsible person. Real chats stay on
the new Claude logic (no going back to the old AI).

**Decision 2026-09-21: tasks in the apartment List are all that is needed.** Nothing else is written to ClickUp:
no chat messages, no other lists. (Optional, not needed: a List also has a chat; it only becomes visible to the API after a first message was typed in it in
ClickUp. After that: `python manage.py ai_agent_clickup --link Test_Apart2 --channel-id 6-901329128753-8`.
Until then - and by default - only tasks are created.)

**Assignee:** live → the responsible person's ClickUp user from `/ai-staff/` (Edy → Farouk Ahmed). Test mode → the
*engineering* staff member (Andrei), so assignment is tested without filling the operations inbox; task names start with `[TEST]`.

One message per run: tasks for tickets first, then ONE channel message (team part only) with the task links.

## What exists

| | |
|---|---|
| ClickUp List | **"TEST-AI-sandbox (Test_Apart2)"** in folder *Channels LIST* — id `901329128753` — https://app.clickup.com/9013651059/v/l/li/901329128753 |
| Chat channel of that List | **not created yet** — a List gets its channel only by a click in ClickUp or through the API (needs the token) |
| CRM mapping | `Test_Apart2` → list `901329128753` (table `AIClickUpChannel`, migration `0087`) |
| Code | `mysite/ai_agent/clickup.py` (API client), routing in `mysite/ai_agent/actions.py`, command `manage.py ai_agent_clickup` |

## What happens once the token is set

Only for apartments that are mapped (today: only `Test_Apart2`, i.e. the sandbox chat):

| AI action | ClickUp | Telegram |
|---|---|---|
| `INTERNAL_ALERT`, `QUEUE_FOR_REVIEW` | message in the apartment channel | also sent (safety copy) |
| `CREATE_TICKET` | **task** in the apartment List (urgent → High, emergency → Urgent) + channel message with the task link; the link is stored on the AI issue | also sent |
| errors, failed runs | — | Telegram only |

- In **test mode** messages start with "🧪 TEST MODE", task names start with `[TEST]`, and tasks are **not assigned**
  to anyone, so nobody's ClickUp inbox fills with test tickets. In live mode the task is assigned to the owner's
  ClickUp user from `/ai-staff/` (Edy → Farouk Ahmed).
- One issue gets one task, never a second one.
- If ClickUp fails, the alert is still delivered to Telegram and the failure is written into the run report.

## Finish the setup (3 minutes)

1. In ClickUp: avatar → **Settings → Apps → API Token** → copy the `pk_…` token.
   Best from a dedicated "AI assistant" ClickUp user, so messages are not shown under a person's name.
2. Add to `.env`: `CLICKUP_API_TOKEN=pk_...`
3. On the server:
   ```
   python manage.py ai_agent_clickup --create-channel Test_Apart2   # creates the List's chat channel
   python manage.py ai_agent_clickup --test-message Test_Apart2     # you should see it in ClickUp
   pm2 restart ai-agent                                             # the worker reads .env at start
   ```
4. In the sandbox chat send as client: `The kitchen sink is dripping` → a `[TEST]` task in the List, a message in
   the channel, and the same alert in Telegram.

If step 3 reports an API error, the exact ClickUp response is printed — send it to me and I adjust the call
(the chat API is newer than the task API and I could not call it from here without a token).

## Later: real apartments

`clickup_apartment_map.md` already lists which apartment has which channel (51 of 87). When the test channel works:
`python manage.py ai_agent_clickup --link 630-429 --list-id 901326630905 --channel-id 6-901326630905-8`, or I import
the whole map in one go.
