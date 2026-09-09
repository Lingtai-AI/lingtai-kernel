---
name: telegram-task-card-behavior-tests
behavior_version: 3
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/resident.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/task_card/event_projection.py
  - tests/test_task_card_event_projection_shared.py
  - tests/test_task_card_locale.py
  - tests/test_telegram_task_card_programmable.py
  - tests/test_telegram_task_card_in_place.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  telegram task-card projection behavior clause changes, update the guarding
  LABT here in the same change.
---
# Telegram Task Card Projection Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md` (programmable body
only when status is exactly active and nonempty; diff-only projection;
no-op preservation; automatic per-call token metrics; whole-resident legacy
Markdown with escaped automatic values and trusted authored programmable
Markdown). Pinned pytest commands
must run from the repo root with
the project's Python.

## Behavior TT001 — the programmable frame is composed only for exact active with a nonempty body, and diff-only updates suppress transport churn

- **id**: TT001
- **title**: the programmable frame is composed only for exact active with a nonempty body, and diff-only updates suppress transport churn
- **guards**: `telegram-task-card-projection` § Behavior
- **runner**: any LingTai agent with `shell` and `file` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with `taskcard/status` and `taskcard/taskcard.md`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_telegram_task_card_programmable.py -q` and capture the outcome.
2. In `<scratch>`, set `taskcard/status` to exact `active` with a nonempty body and project the card; then set status to a missing/unreadable value and project again; record both compositions.
3. Re-project with identical body bytes and confirm no transport update occurs (diff-only); set status to exact `inactive` twice and confirm the second projection delivers nothing further.

### Expected evidence
- [ ] Step 1: the programmable projection suite passes, pinning active projection, diff-only updates, exact-`inactive` frame exclusion, reactivation, and last-good preservation.
- [ ] Step 2: the programmable frame is composed only for exact `active` with a nonempty body; missing/unreadable status or blank body is a no-op that preserves the last valid programmable frame.
- [ ] Step 3: identical body bytes produce no transport update; exact `inactive` idempotently excludes only the programmable frame and never deletes the resident message, the local body, or pauses automatic updates.

### Pass / Fail
Pass when the suite passes and the active/diff-only observations hold. Fail on programmable composition for a non-active status, on a transport update for identical bytes, or on `inactive` clearing the resident message or body; record the evidence trail in the task report.

## Behavior TT002 — automatic API-call dividers show reasoning tokens beside output tokens

- **id**: TT002
- **title**: automatic API-call dividers show reasoning tokens beside output tokens
- **guards**: `telegram-task-card-projection` § Behavior rule 9
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`
- **estimate**: ≈ 2 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_telegram_task_card_event_tail.py -q` and capture the outcome.
2. Project one tool-call group whose notification carrier reports `current_call.output`, `current_call.thinking`, cache miss/rate, and session context.
3. Project one pure-text group whose `llm_response` reports `output_tokens` and `thinking_tokens`, then repeat without a thinking field.

### Expected evidence
- [ ] Step 1: the automatic event-tail suite passes.
- [ ] Step 2: the divider contains `↓<output> (<thinking>) ↑<cache-miss>` with compact counts, while `_usage` remains private projection state.
- [ ] Step 3: the `llm_response` fallback renders the same parenthesized form; an old event without thinking tokens preserves the prior output/cache/context line with no dangling parentheses.

### Pass / Fail
Pass when both normalized usage paths render the same parenthesized reasoning count immediately after output and legacy missing-field input remains unchanged. Fail if the count is misplaced, reasoning text is exposed, or missing/malformed data leaves a dangling marker.

## Behavior TT003 — generated a-priori summaries add an existing-data-only cost line after the successful tool row

- **id**: TT003
- **title**: generated a-priori summaries add an existing-data-only cost line after the successful tool row
- **guards**: `telegram-task-card-projection` § Behavior rule 10 and Contract rule 11
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`
- **estimate**: ≈ 2 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_telegram_task_card_event_tail.py -q` and capture the outcome.
2. Project an existing successful `tool_result` followed by `apriori_summary_generated`, plus its already-recorded `source=summarize_apriori` token-ledger row, all sharing one `tool_call_id`.
3. Repeat with missing, malformed, mismatched, and unsuccessful inputs, and restart-rehydrate the valid case.

### Expected evidence
- [ ] Step 1: the automatic event-tail suite passes.
- [ ] Step 2: exactly one `(summary, <elapsed>, <input> in, <output> out)` line follows the completed tool row; elapsed derives from event timestamps and tokens from the bounded ledger tail.
- [ ] Step 3: valid data survives restart rehydration; all invalid/legacy cases preserve old output, while generated summary text, tool-result bodies, and provider metadata never render.

### Pass / Fail
Pass when only fully correlated existing data produces the second line in the required order and all unsafe/malformed cases omit it. Fail if a new producer event is required, more than the bounded ledger tail is read, an unsuccessful tool receives a summary line, or private payload fields render.

## Behavior TT004 — the complete Telegram resident uses legacy Markdown with an explicit two-sided trust boundary

- **id**: TT004
- **title**: the complete Telegram resident uses legacy Markdown with an explicit two-sided trust boundary
- **guards**: `telegram-task-card-projection` § Behavior rule 13 and Contract rule 14
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`
- **estimate**: ≈ 2 minutes

### Steps
1. From `<repo>`, run `python -m pytest -q tests/test_task_card_event_projection_shared.py tests/test_task_card_locale.py tests/test_telegram_task_card_programmable.py tests/test_telegram_task_card_in_place.py`.
2. Inspect the automatic-only, programmable-only, and combined resident calls, including automatic values containing underscore, asterisk, backtick, square brackets, backslash, and link-like text plus intentionally authored programmable Markdown.
3. Exercise an in-place edit whose Telegram provider response rejects Markdown parsing.

### Expected evidence
- [ ] Step 1: the focused suites pass; shared/Feishu plain bytes remain unchanged while English and Chinese Telegram headings are deterministic.
- [ ] Step 2: every resident send/edit has exact `parse_mode="Markdown"`; automatic metacharacters are escaped, and authored programmable Markdown appears byte-for-byte once.
- [ ] Step 3: parse rejection returns an error, preserves the prior committed frame/resident, sends no replacement, and never falls back to plain text.

### Pass / Fail
Pass when the exact parse mode, escaped-automatic/authored-programmable split, and fail-loud commit timing all hold. Fail on any missing parse mode, raw hostile automatic marker, double-escaped programmable body, false success, replacement, or plain-text retry.
