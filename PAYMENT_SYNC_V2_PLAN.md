# Payment Sync V2 Plan (Group Matching + Cache)

## Goal
Add selection-based matching that runs only when **≥1 bank/file payment** is selected, provides **4 actions** (AI, AI Custom, Manual, Both), returns **grouped suggestions** in a **unified schema**, caches results in **localStorage by selection key**, and renders the DB column filters **All / Manual / AI / Both**.

## Confirmed Decisions
- **Selection unit**: `selection_key = sorted(selected_file_ids).join('+')`
- **Cache**: store results per `selection_key` and reuse when re-selected.
- **Manual scoring mode**: **sum-only** (selected file payments are combined into one **composite transaction**; amount is summed and other fields are merged, see “Manual composite merge rules” below).
- **DB date filter for AI**: ±1 month around selected file-payments date range.
- **AI candidate limit**: top **100** candidates (after deterministic prefilter/rank) sent to LLM.
- **AI provider**: OpenRouter (`OPENROUTER_API_KEY`), default model: `anthropic/claude-3.5-sonnet`
- **Both view**: union of manual + ai by DB payment id.\n+  - If the same `db_payment.id` exists in **both** sources, render **one DB payment card** and show **both blocks** (AI block + Manual block) on that same card.\n+  - Sort by `combined_score = (manual_score + ai_score)/2` when both exist.

## Files to Change
- `templates/payment_sync/sync_v2.html`
- `mysite/views/payment_sync_v2.py`
- `mysite/urls.py`
- `mysite/views/__init__.py`
- (optional reuse/adapt) `mysite/views/gpt.py` for OpenRouter client helper

## Unified Match Schema (manual/ai identical)
Each mode returns a `MatchResult`:
- `selection_key: string`
- `file_payment_ids: string[]`
- `source: "manual" | "ai"`
- `candidates: Candidate[]` (sorted by `score` desc)

Each `Candidate`:
- `db_payment: DbPaymentRich`
- `score: number` (0–100)
- `match_type: string`
- `criteria: string` (manual: constructed; ai: LLM explanation)

UI “Both” merges manual+ai by `db_payment.id` and computes:
- `combined_score = (manual_score + ai_score)/2` if both exist
- else `combined_score = the existing score`

## localStorage Cache Model
Add key: `payment_sync_v2_match_cache`

Suggested structure:
```json
{
  "version": 1,
  "by_selection": {
    "pid1+pid2": {
      "manual": { "selection_key": "pid1+pid2", "file_payment_ids": ["pid1","pid2"], "source": "manual", "candidates": [] },
      "ai":     { "selection_key": "pid1+pid2", "file_payment_ids": ["pid1","pid2"], "source": "ai", "candidates": [], "model": "anthropic/claude-3.5-sonnet", "base_prompt": "...", "custom_prompt": "..." }
    }
  }
}
```

Clear match cache when:
- `demo=1`
- new CSV upload detected (same “file ids changed” logic already used to clear other stored state)

Note: when clearing for `demo=1` or new upload, the intent is to clear **all local cache state except settings** at this moment (keep `payment_sync_v2_settings`).

## Manual composite merge rules (multi-select)
Manual mode treats the selected bank payments as one composite “transaction” to compare against DB payments. Besides summing amount, merge these fields:

- `amount_total`: sum of selected amounts (manual “sum-only”).
- `date_from` / `date_to`: min/max of selected `payment_date`.
- `apartment_name`:\n+  - If exactly one non-empty apartment across selection → set it.\n+  - If multiple apartments or none → set empty and keep `apartment_candidates` (list of unique non-empty apartment names).\n+- `payment_method_name` / `bank_name` (if present on file payments): same rule as apartment.\n+- `notes_combined`: join all notes/descriptions into one string; keep original list for criteria.\n+\n+Manual scoring should consider:\n+- amount vs `amount_total`\n+- date distance to the range `[date_from, date_to]`\n+- apartment/method/bank matching against the single value or candidates list\n+- criteria string should describe the composite (amount total, date range, apartments/methods/banks present) plus the reasons for a candidate score.

## Backend: New Endpoint
Add POST endpoint (example path):
- `payments-sync-v2/match-selection/`

Request payload:
- `mode: "manual" | "ai" | "both"`
- `selected_file_ids: string[]`
- `selected_file_payments: FilePayment[]` (from page)
- `db_days_before`, `db_days_after`, `with_confirmed`
- `amount_delta`, `date_delta`
- `ai_model` (from settings, default above)
- `ai_base_prompt` (from settings)
- `ai_custom_prompt` (optional, from modal)

Behavior:
- Determine selected date range; query DB payments using existing `query_db_payments_custom` but with a window approximating ±1 month (or equivalent days).
- Build rich DB dicts (tenant/apartment/bank/type/keywords/notes).
- Manual:
  - Combine selected file payments into one composite transaction (sum amount; merge notes; date range).
  - Score DB payments deterministically (reuse existing logic where possible) and produce `criteria` string.
- AI:
  - Prefilter/rank DB candidates (date/type/amount/keywords heuristic), keep top 100 (details below).
  - Call OpenRouter and request strict JSON: list of `{db_id, score, match_type, criteria}`.
  - Convert into `MatchResult` with `candidates[]`.
- Both:
  - Return manual+ai results in response (frontend unions), or return both results in one payload.

## AI DB candidate prefilter (before LLM) and top-100 selection
We must reduce the DB set before sending to the LLM (tokens/cost/time). The prefilter happens **server-side**.

### Step 1: Date window filter (hard filter)
- Compute selected file payments date range:\n+  - `date_from = min(selected.payment_date)`\n+  - `date_to = max(selected.payment_date)`\n+- Expand by ±1 month (approx 30 days each side):\n+  - `db_from = date_from - 30 days`\n+  - `db_to = date_to + 30 days`\n+- DB candidates are only payments with `payment_date` in `[db_from, db_to]`.

### Step 2: Type filter (hard filter where possible)
- If all selected file payments have the same direction/type (`In` or `Out`), keep only DB payments with the same `payment_type.type`.\n+- If selection is mixed/unknown, keep both but apply a penalty in ranking.

### Step 3: Amount filter (soft/hard)
Because group selection is treated as one composite transaction:\n+- `amount_total = sum(selected.amount)`\n+- Keep DB payments where `abs(db.amount - amount_total) <= amount_delta * max(1, number_of_selected_items)`.\n+  - This keeps window wider for 3+ grouped items.\n+  - (We can tune this later; initial goal is “don’t miss” while still limiting tokens.)

### Step 4: Deterministic pre-rank (for top-100)
Compute a quick heuristic score per DB candidate to choose the top 100 to send to the LLM:\n+- Amount closeness: higher score when `abs(db.amount - amount_total)` is smaller.\n+- Date closeness: higher score when DB date is inside `[date_from,date_to]`, else penalize by distance to nearest edge.\n+- Apartment match: if composite has a single apartment or apartment_candidates, boost when DB apartment matches.\n+- Keyword overlap: build a simple token set from combined notes + apartment candidates + (optional) method/bank; boost on overlap with DB notes/keywords/tenant/apartment keywords.\n+\n+Sort DB candidates by this heuristic score desc and take top 100.\n+
## Frontend (sync_v2.html)
- Replace current match buttons with 4 buttons:
  - Merge with AI
  - Merge with AI custom (opens modal, appends custom prompt)
  - Merge manually
  - Merge both
- Disable all 4 until `selected_bank_payments.size > 0`; show message.
- On click:
  - compute `selection_key`
  - if cache exists for selection+mode, render it (no request)
  - else POST to backend and save response under selection key
- DB column filter chips:
  - All: render full `db_payments_json`
  - Manual: render manual candidates for current selection
  - AI: render AI candidates for current selection
  - Both: union view showing both blocks and combined sorting

