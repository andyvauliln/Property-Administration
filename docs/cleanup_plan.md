# Cleanup plan: old, unused and duplicated code

Date: 8 Oct 2026. State: your decisions are in the "Status" part. The code work is done in the worktree `/home/superuser/site-alerts-v5`. Nothing is deployed yet.
Source: two read-only searches of the code (grep evidence for each item). Production: `/home/superuser/site`, commit ebf556d.

## 1. Summary

1. No large module is fully dead. Most old code is inside live modules, as a branch for an old mode.
2. The AI agent has three old modes. Production uses none of them:
  - the old v4 alert card (it is the rollback today),
  - the timer approval mode (production cannot reach it),
  - the old backend switch (OpenRouter / inline AI).
3. The rest of the site has dead views, templates, test commands, repo-root leftovers and many copies of the same helper.
4. The searches also found 5 problems that are **not** cleanup. They are safety problems. Do them first (part 2).



## 2. Fix first (safety, not cleanup)


| #   | Problem                                                                                                                         | Where                                                | Risk now                |
| --- | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ----------------------- |
| S1  | Passwords in plain text (sudo password, database password). The files are not in git, but they are on the production server.    | `start.sh`, `COMMANDS.md`                            | high                    |
| S2  | CSRF protection is off (the line is commented out).                                                                             | `mysite/settings.py:141`                             | high                    |
| S3  | A test command deletes a real Twilio conversation (hard-coded SID).                                                             | `mysite/management/commands/test-conversation.py:21` | high if someone runs it |
| S4  | PM2 app `mcp-tunnel` starts a script that does not exist.                                                                       | `pm2.config.js:23-27` (`cloudflared_start.sh`)       | low ()                  |
| S5  | 12 of 14 cron jobs use `/usr/bin/python3`, not the venv. That Python has other package versions (twilio 8 vs 9, openai 1 vs 2). | `cron.js:77-130, 147-184`                            | medium                  |


Notes:

- S1: change the passwords after you remove them from the files.
- S2: you must test all forms and AJAX calls after you turn CSRF on. Some POST calls can fail.
- S5: a change to the venv Python can change how the cron jobs work. Test each job one time.



## 3. Decisions for you


| #   | Question                                                                                                     | My recommendation                                                                 |
| --- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| D1  | When do we remove the v4 alerts? Today `AI_AGENT_ALERT_STYLE=v4` is the rollback.                            | Keep v4 for 2 weeks after the reminder alerts (group C) are live. Then remove it. |
| D2  | Do people still use payment sync v1 (`/payments-sync/`)? v2 replaced it, but the sidebar still has the link. | Remove v1 if nobody uses it.                                                      |
| D3  | Does an external system call the price APIs (`UpdateApartmentPriceByRooms`, `UpdateSingleApartmentPrice`)?   | Keep them until you know.                                                         |
| D4  | Keep the ClickUp "via Claude" fallback (used only when the ClickUp token is missing)?                        | Remove it. The token is set, so this path never runs.                             |
| D5  | Delete old one-off management commands, or move them to an `archive/` folder?                                | Move them to `archive/` first, then delete after one month.                       |




## 4. What I found



### 4.1 AI agent (`mysite/ai_agent/`)

**A. Dead code (no callers)** – low risk

- `config.is_agent_backend_enabled()` (`config.py:236`).
- `prompt_library.rule_lines()`; `BACKEND_CLAUDE` and `PromptSpec.backends` (nothing reads them).
- `AIIssue.is_handled_by_staff` (`models.py`).
- `messaging._conversation_ai_group_chat_enabled`, `messaging.update_conversation_booking_link`.

**B. Only for the v4 card (the rollback)** – medium risk, waits for D1

- `cards.py` (all of it).
- `approval.py`: the v4 buttons (`approve_all`, `stop_all`, `apply_ticked`, `toggle`, `take_over`, `give_back`, drafts, `keyboard`, the v4 part of `handle_callback`). v5 also uses part of this file, so you must split it. Do not delete the whole file.
- `team_notify.deliver`: the v4 card branch.
- The "I'll handle" logic (`cases.take_over/give_back`, `handled_ids`, `enforce_handled`, `handled_by` checks).
- `answer_review.handle_reply`: the v4 body and its helpers (`apply_plan_ops`, `plan_text`, ...).
- `policy.DELAYS` (the v4 reminder times).
- All `alert_style() == 'v5'` checks become the normal code.

**C. Only for the timer approval mode** – low risk (production cannot reach it)

- `answer_review.release_due` / `_due_runs` (the worker calls it every 5 s and it never finds anything).
- `service.deliver`: the legal hold and the timed hold.
- `answer_review.start`: `_supersede_older` and the "release without review" path.
- `team_notify.compose_telegram`: the "AUTOMATIC at …" parts and `_minutes_left` / `_eta`.
- `config.explicit_approval()`: the `AI_AGENT_APPROVAL` switch.

**D. Old backend leftovers** – low to medium risk

- `config.get_ai_backend()` always returns `claude_cli`. Only a badge on `/ai-runs/` shows it.
- Stale docstring in `views/messaging.py:66-72` ("legacy inline AI").
- `oneshot.ClaudeTextClient`: an OpenAI-style shim for the rule generator in `messaging.py`. It works, but you must change it to `oneshot.complete()`.
- Old `ai_backend='openrouter'` rows: only the tests read them.
- Keep: OpenRouter in `views/payment_sync_v2.py`. It is live (the "Merge with AI" feature).

**E. Settings that always have the same value**

- `AI_AGENT_ALERT_STYLE`: the code default is `v4`, production is `v5`. Change the default to `v5`, or remove the switch after D1.
- `AI_AGENT_APPROVAL`, `AI_AGENT_REVIEW_HOLD_MINUTES`, `AI_AGENT_TELEGRAM_ACTIVITY`, `AI_AGENT_STAFF_EVENTS`, `AI_AGENT_NOTIFICATIONS`, `AI_AGENT_CLI_BARE`, `AI_AGENT_CALLS`: not set, so they always have their default.
- `AI_AGENT_CLICKUP_VIA_CLAUDE`: no effect while the ClickUp token is set (D4).
- `AI_ASSISTANT_ENABLED=false`: the agent does not read it, but the chat page shows a "test mode globally" banner because of it. The banner is wrong.

**F. Tests and documents for old paths**

- `testbed_settings.py` removes all `AI_AGENT_*` settings. So most dry tests run **v4 with no review**, a mode that production never uses. These tests must move to v5 + explicit approval.
- Timer-only tests: `e2e_answer_review.py`, `e2e_legal.py`, part of `e2e_kb_documents.py`.
- v4-only tests: `e2e_client_v4.py`, the `style='v4'` part of `e2e_sandbox_runner.py`, `--style v4` of `ai_agent_sandbox_test`.
- Old documents: `answer_review_telegram.md`, parts of `client_spec_v4.md`, the OpenRouter parts of `migration_plan.md`, `phase1_runbook.md`, `phase3_knowledge_guards.md`, `how_ai_messages_work.md`, `testing_plan.md`.

**G. Database fields only for old paths** (list only; a migration needs your OK)

- `TwilioMessage.ai_kb_updated`, `ai_kb_changes`: nothing writes them; the chat page still reads them.
- `AIRun.backend`: never set, never read.
- `AIRun.hold_until`: only the timer mode sets it.
- `AIIssue.handled_by`, `handled_at`, `handled_prev_state`: only v4 "I'll handle" sets them.
- `AIEvent` types `TICKET_UPDATE`, `CLICKUP_MESSAGE`: nothing creates them.
- AI Management row `ai_backend`: nothing reads it.

**Do not remove (looks old, but is live)**

- `team_notify.compose_telegram` plain alert: v5 **emergencies** still use it. I must check this (part 5, step 2).
- ClickUp channel messages (`compose_clickup`, `_deliver_clickup`): the v5 `🎫 Create Task` button uses them.
- `flush_pending_sms`: cron runs it every 5 min.



### 4.2 The rest of the site

**A. Dead views, URLs and templates**

- `urls.py:23`: second `notifications/` route (never matches).
- `chat_template_list` (`urls.py:75`): `chat/<sid>/` catches the URL first, so nobody can open it.
- `load_more_messages` (`chat.py:1076-1134`): nothing calls it.
- Payment sync v1: `views/payment_sync.py` + 3 templates (D2).
- Templates never shown: `components/card.html`, `components/dropdown.html`, `components/table/table_filter.html`, `generic_table.html`, `temp.md`.
- Functions with no callers: `utils.get_payments_for_month`, `apartments_report.get_price_for_date_cached`, `utils.calculate_total_booked_days`.
- Small bug: the sidebar link of payment sync v2 is marked active on the v1 page (`_base.html:271`).

**B. Management commands that nothing runs**

- One-off fixes and exports (19 commands), for example `backfill_twilio_media`, `dedupe_user_phones`, `export_unlinked_payments` (D5).
- Test commands: `test_booking`, `test_centralized_errors`, `test_error_logging`, `test_phone_validation`, `test_price_api_reversible`, `telegram_group_test`, `test-conversation` (S3).
- `telegram_notifications_payment`: never scheduled, and a copy of parts of `telegram_notifications`.
- Keep: `fix_phone_numbers`, `sync_twilio_history` (other messages tell people to run them).

**C. Static files**

- `static/signature.png`: not used.
- `static/CACHE/css/`: 72 old compressed files (4.7 MB).
- Commented-out datepicker script in `_base.html`, `_base_no_sidebar.html`.

**D. Repo-root leftovers**

- `nixpacks.toml` and the Railway comment in `settings.py` (old hosting).
- `commands/*.py` (4 scripts, nothing uses them).
- `backups/*.sql` (old dumps, 3.1 MB), `data/*.json` one-off files (keep `data/twilio_media/`).
- Empty `cron_logs.json` in the root, `.cursor-history/`, empty `CLAUDE.md` in git.
- Keep: `check_twilio_balance.py`, `mcp_server.py`, `mcp_oauth.py` (live).

**E. Copies and commented-out code**

- `send_telegram_message`: 13 copies. `normalize_group_chat_id`: 6 copies. `my_cron_job`: 9 copies.
- Two error loggers (`telegram_logger.log_error` and `unified_logger.log_error`; `error_logger.log_exception` and `unified_logger.log_exception`).
- Payment sync v1 and v2 share 6 helper functions (copied).
- `payment_sync_v2.py`: 5 functions with no callers (`_tokenize`, `match_apartment`, `_match_apartment_token_split_legacy`, `get_date_range`, `query_db_payments`).
- `mysite/base_models.py`: nothing uses its 4 classes.
- Two `ai_management_view`, two `handle_post_request`, two `serialize_field`.
- Commented-out blocks in `forms.py` (a whole `ContractForm`) and in 5 templates; 46 `print(` calls in `mysite/views/`.

**F. Logs**

- `logs/payment_sync_v2_trace.jsonl` is 201 MB (the trace is on by default).
- `logs/group_chat_log.log` 27 MB, `logs/common.log` 9.7 MB. No log rotation.



## Status: your decisions and what is done (8 Oct 2026)

| Item | Your decision | Status |
|---|---|---|
| S1 passwords, S2 CSRF | keep as they are for now | not changed |
| S3 test command that deletes a chat | — | done: the command is deleted |
| S4 `mcp-tunnel` | remove | done in `pm2.config.js`; at deploy: `pm2 delete mcp-tunnel` |
| S5 cron Python | fix | done: all jobs use the venv Python |
| D1 v4 alerts | remove now | done: v4 card, its buttons and "I'll handle" are removed. **No rollback to v4 after the deploy.** |
| D2 payment sync v1 | keep | kept; the sidebar link bug is fixed |
| D3 price APIs | keep | kept |
| D4 ClickUp via Claude | remove | done |
| D5 one-off commands | delete | done: 25 deleted; kept `sync_twilio_history`, `fix_phone_numbers`, and `backfill_twilio_media`, `dedupe_user_phones` (tests use them) |
| 4.1 A–F | OK | done (old documents have a "historical" note) |
| 4.1 G fields | OK, but keep `ai_kb_updated`, `ai_kb_changes` | done: migration `0097_remove_old_alert_modes` (not applied yet) |
| 4.2 A | keep `/notifications/` and payment sync v1 | done: only the second, dead `notifications/` route is removed |
| 4.2 B–F | OK | done; the two error loggers stay (they do different things); 5 of 6 payment sync helpers differ, so they stay |
| Backups | fresh dump first | done: `backups/backup_2026-10-08.dump` (11 MB); old `.sql` files deleted; one-off data files in `backups/old_data_and_reports_2026-10-08.tar.gz` |

Problem during the cleanup: I deleted `static/CACHE`. The site then had no styles for about 5 minutes (the compressed CSS file gave 404). I made the file again; the site is normal.

Also done (your OK of 8 Oct):
- The safety answer of a real emergency is sent at once, also outside the 08:00-21:00 SMS hours (in the worktree, comes with the deploy).
- The live prompts in the database are clean: no "card", "I'll handle", review window, CLICKUP_MESSAGE / TICKET_UPDATE texts any more (main system prompt, runtime notes, reply interpreter). Backup of the old texts: `backups/ai_prompts_before_2026-10-08.json`. The two unused prompt rows (ClickUp via Claude) are deleted by migration 0097.

Still open: the v5 emergency alert (D3/D4) and the daily reports (G) are not built.

## 5. Plan

Rules for every phase:

- Work in a git worktree, never in the production checkout.
- Do not touch your own uncommitted changes (`mysite/models.py`, `static/output.css`).
- After each phase: the dry tests (`run_tests.sh`) and, for agent code, the sandbox story.
- A deploy, a restart or a migration only with your OK.


| Phase | What                                                                                                                          | Risk   | Needs                                |
| ----- | ----------------------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------ |
| 0     | Safety fixes S1–S5                                                                                                            | medium | your OK for each                     |
| 1     | Read-only database checks: runs with `hold_until`, old v4 alerts that still wait, issues with `handled_by`, `ai_backend` rows | none   | nothing                              |
| 2     | Remove dead code with no callers (4.1 A, 4.2 A without D2/D3, 4.2 C, 4.2 E unused functions, `base_models.py`)                | low    | dry tests                            |
| 3     | Remove the timer mode (4.1 C) and the old backend leftovers (4.1 D); fix the wrong "test mode globally" banner                | low    | dry tests + story                    |
| 4     | Move the dry tests to v5 + explicit approval; delete timer-only tests; update old documents (4.1 F)                           | low    | —                                    |
| 5     | Put copies together: one `send_telegram_message`, one error logger, shared payment sync helpers (4.2 E)                       | medium | dry tests + one run of each cron job |
| 6     | Archive one-off commands and repo-root leftovers (D5, 4.2 B, 4.2 D)                                                           | low    | your OK on the list                  |
| 7     | Log rotation; trace off by default                                                                                            | low    | —                                    |
| 8     | After D1 (2 weeks of stable v5): remove the v4 card and the "I'll handle" logic (4.1 B), the `--style v4` test option         | medium | your OK, story, deploy               |
| 9     | Database fields (4.1 G): one migration                                                                                        | medium | your OK, backup first                |


Order of value: phase 0 (safety) → 3 → 2 → 4 → 8. Phases 5–7 can come any time.

## Plan in ASD-STE100

1. Do the safety fixes first.
  - Remove the passwords from `start.sh` and `COMMANDS.md`. Then change the passwords. Risk: a script that uses the password stops.
  - Turn on the CSRF middleware in `mysite/settings.py`. Test all forms. Risk: some POST calls fail.
  - Remove the hard-coded conversation SID from `test-conversation.py`, or delete the command. Risk: none.
  - Remove the `mcp-tunnel` app from `pm2.config.js`, or add the missing script. Risk: low.
  - Change the cron jobs in `cron.js` to the venv Python. Run each job one time. Risk: package version changes.
2. Read the database. Do not change it. Count runs with `hold_until`, waiting v4 alerts, issues with `handled_by`, `ai_backend` rows.
3. Make a new git worktree. Do all code work there.
4. Remove the dead code with no callers (part 4.1 A, 4.2 A, 4.2 C, unused functions of 4.2 E, `mysite/base_models.py`). Run `run_tests.sh`.
5. Remove the timer mode: `release_due`, the timed hold in `service.deliver`, the timer parts of `answer_review.start` and `team_notify`. Run the dry tests and the sandbox story.
6. Remove the old backend leftovers: `get_ai_backend`, the stale docstring, the OpenAI-style shim. Keep OpenRouter in payment sync v2.
7. Fix the "test mode globally" banner of the chat page.
8. Change the dry tests to v5 and explicit approval. Delete the timer-only tests. Update the old documents.
9. Put the copied helpers together: one `send_telegram_message`, one error logger, one set of payment sync helpers. Run each cron job one time.
10. Move the one-off commands and the repo-root leftovers to `archive/`. Get your OK on the list first.
11. Add log rotation. Set the payment sync trace to off by default.
12. Wait 2 weeks after the reminder alerts are live. Then remove the v4 card, the v4 buttons and the "I'll handle" logic. Risk: no rollback to v4 after this step.
13. Make one migration for the old database fields. Make a database backup first. Get your OK first.
14. After each step: run the dry tests. For agent code, also run the sandbox story. Get your OK before each deploy.

