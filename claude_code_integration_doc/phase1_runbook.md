# Phase 1 runbook — Claude agent for tenant group chats

Status: **code is in place, switched OFF by default.** Nothing changes for tenants until the
steps below are done. The old OpenRouter AI keeps working exactly as before.

## What was built

| Piece | Where |
|---|---|
| Queue + run tables | `AIEvent`, `AIRun` in `mysite/models.py`, migration `0083_ai_agent_events_runs` |
| Agent package | `mysite/ai_agent/` — `config`, `prompts`, `inputs`, `runner` (the only file that knows the CLI), `run_report`, `service`, `mcp_tools`, `schema.json`, `default_system_prompt.md` (= Farid's prompt) |
| Worker | `python manage.py run_ai_agent` (PM2 app `ai-agent`) |
| Offline test | `python manage.py ai_agent_replay` — replays stored tenant messages, **never sends** |
| Run listing | `python manage.py ai_run_report` |
| UI | `/ai-runs/` (list, totals, filters), `/ai-runs/<id>/` (all report files), chat page polls the result and links the report |
| Hooks | `twilio_webhook` (3 places) and chat UI `send_message` call `_enqueue_for_ai_agent()`; when it returns False the legacy AI runs |

## Turn it on

1. **Apply the migrations** `0083`–`0086` (7 new tables and 5 seeded staff rows; no existing table is altered):
   `python manage.py migrate mysite`
2. **Claude login for the worker.** Production should use an API key: put `ANTHROPIC_API_KEY=...`
   in `.env` (optionally `AI_AGENT_CLI_BARE=true`). For a first trial with an existing CLI login:
   `AI_AGENT_CLAUDE_CONFIG_DIR=~/.claude-faridagazizov` in `.env`.
3. **Start the worker:** `pm2 start pm2.config.js --only ai-agent` → `pm2 logs ai-agent`
4. **Restart the site** so gunicorn loads the new code (`./start.sh`).
5. **Switch the backend:** AI Management → add entry, type **AI Model**, prompt key
   *AI Backend*, content `claude_cli`. To go back instantly: change content to `openrouter`
   (no restart needed). Do not leave the type as *Knowledge* — knowledge entries are put into
   the AI's knowledge base.

**Before step 5, check which apartments are live** (Apartments → "Enable real group chat AI
answers"): Claude answers their tenants by SMS as soon as the backend is switched. On 2026-09-21
all apartments were switched OFF (`720-213`, `630-214`, `630-429` had been ON), so the start is test-only.

Test mode vs live is unchanged: an apartment sends real SMS only when
**"Enable real group chat AI answers"** (`Apartment.ai_group_chat_enabled`) is ticked. Everything
else is stored in the DB and visible in the chat page and `/ai-runs/`.

## Settings

| What | How | Default |
|---|---|---|
| Backend switch | AIManagement `ai_backend` (or env `AI_BACKEND`) | `openrouter` |
| Model | AIManagement type **AI Model**, key `ai_agent_model` (or env `AI_AGENT_MODEL`) | `claude-sonnet-5` |
| System prompt | AIManagement entry type **AI Prompt**, key `ai_agent_system` | `mysite/ai_agent/default_system_prompt.md` |
| The one AI Telegram chat (alerts live + test, failed runs, failed deliveries, worker crashes) | env `AI_AGENT_ALERT_CHAT_ID`; code: `mysite/ai_agent/notify.py` | **set 2026-09-21: group "[PM] AI GROUP" (`-5026850825`), bot `@PropertyManagmentBot` is a member, test message delivered** |
| Names in the prompt | env `AI_AGENT_ASSISTANT_NAME`, `AI_AGENT_COMPANY_NAME`, `AI_AGENT_TEAM_TIMEZONE` | Virtual Assistant / — / America/New_York |
| Limits | env `AI_AGENT_TIMEOUT_SECONDS` 180, `AI_AGENT_MAX_BUDGET_USD` 0.50, `AI_AGENT_DEBOUNCE_SECONDS` 60 (emergency-looking messages 0, chat-page tests `AI_AGENT_CHAT_UI_DEBOUNCE_SECONDS` 5) | |

## Investigating a run

Every run (worker, replay, failed, timed out) writes `logs/ai_runs/<date>/<time>_<unit>_<event>_<sid>/`:

- `report.md` — start here: message → answer → why → delivery → actions → tool calls → **tokens per turn + total, cost, turns, duration** → errors/warnings
- `01_system_prompt.md`, `02_input.md` — exactly what Claude received
- `03_command.txt`, `mcp_config.json` — exactly how it was called
- `04_transcript.jsonl` — raw CLI stream (source of truth) · `05_steps.md` — the same, readable
- `06_output.json`, `07_actions.json`, `08_delivery.json` — what came out and what the backend did with it

`logs/ai_runs/index.csv` has one line per run (open in Excel). `logs/` is git-ignored: reports contain tenant data.

## What is deliberately limited in Phase 1

- Claude has **no built-in tools** (no shell, files, web). Its only tools are `get_chat_history` and
  `search_chat_history`, locked to the conversation of the run (`mysite/ai_agent/mcp_tools.py`).
  The general `mcp_server.py` is **not** given to it: it can read other tenants' data.
- Actions are validated and recorded. In live and test mode `INTERNAL_ALERT`, `QUEUE_FOR_REVIEW`, `CREATE_TICKET`
  go to the one AI Telegram chat (`AI_AGENT_ALERT_CHAT_ID`); issues, follow-ups, KB updates and case notes are stored only (Phase 2/3).
- If the answer says "I've logged it / passed it to the team" but no matching action was emitted,
  the backend adds an `INTERNAL_ALERT` itself so the promise is true.
- If staff reply while Claude is working, the AI answer is not sent.
- If a run fails, the tenant's message goes to the same AI Telegram chat (and the CRM error log).
- Manager messages (KB extraction) and rule generation still use OpenRouter.

## First replay results (2026-09-21, claude-sonnet-5, test mode)

| Message | Old AI | Claude |
|---|---|---|
| "Yes since 10am" (reply to staff) | NO_ANSWER | NO_ANSWER + CASE_NOTE |
| "Zelle works" | NO_ANSWER | short acknowledgement + issue queued for Janna |
| "Is it 3800 total for the 6 weeks?" | answered "$3,800 total" and quoted payment policy | NO_ANSWER, queued for Janna (records do not prove it covers all 6 weeks) |
| "I can send the rest tomorrow…" | NO_ANSWER | NO_ANSWER, queued for Janna (payment arrangement = human decision) |

Cost: ~$0.07 for a cold run, ~$0.02–0.03 when the prompt cache is warm (within 5 minutes); 8–15 s per message.
