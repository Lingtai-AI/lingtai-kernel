# dual-ledger — Daemon Token Attribution

## What

Each daemon emanation writes its LLM token usage to **two** ledger files: the daemon's own `logs/token_ledger.jsonl` and the parent agent's `logs/token_ledger.jsonl`. This ensures daemon spend is visible both locally (per-emanation cost) and in the parent's lifetime totals — without requiring the parent's LLM session to track daemon calls.

## Contract

1. **Untracked session.** Emanation LLM sessions are created with `tracked=False` so the kernel's built-in `ChatSession` auto-ledger does not fire. The daemon manager handles token accounting manually.

2. **Dual write on every LLM response.** After each `session.send()`, `_accum()` reads `resp.usage` and calls `run_dir.append_tokens()` with `input`, `output`, `thinking`, `cached`, `model`, and `endpoint`.

3. **Daemon ledger** (`daemons/<run_id>/logs/token_ledger.jsonl`): one entry per LLM call, tagged `source: "daemon"`, `em_id`, `run_id`. Local to the emanation; already attributable by folder location.

4. **Parent ledger** (`logs/token_ledger.jsonl`): identical entry appended to the parent's ledger, tagged identically. Existing `sum_token_ledger()` sums all entries without filtering by `source`, so daemon spend is included in the parent's lifetime totals automatically.

5. **Model/endpoint tagging.** When a preset overrides the LLM, the entry records the preset's model and endpoint — enabling per-provider cost decomposition across the parent's ledger.

6. **Zero-skip.** If all four token counts are zero, no entries are written (avoids ledger noise from empty responses).

7. **Independent fault tolerance.** Each ledger write is wrapped in `_safe()` which swallows `OSError`. If the parent ledger write fails, the daemon's local ledger is still authoritative.

8. **Running totals in daemon.json.** `append_tokens()` also increments the `tokens` field in `daemon.json` (atomic-replaced), so `daemon(action="check")` reports cumulative spend without parsing JSONL.

## Source

Anchored by function name (`::`); re-locate with `grep -n 'def <func>' <file>` if line numbers drift.

- `run_dir.py::DaemonRunDir.append_tokens()` — dual-write logic (local + parent ledger)
- `run_dir.py::DaemonRunDir.__init__()` — `_parent_token_ledger` path set from `parent_working_dir`
- `run_dir.py::DaemonRunDir._safe()` — OSError-swallowing wrapper used by both writes
- `__init__.py::_run_emanation()` — creates session with `tracked=False`; `_accum()` callback wired here
- `__init__.py::_accum()` — reads `resp.usage` and calls `run_dir.append_tokens()`
- `lingtai_kernel/token_ledger.py::append_token_entry()` — shared JSONL append helper
- `lingtai_kernel/token_ledger.py::sum_token_ledger()` — sums all entries, no source filter

## Related

- `../verify_daemon_leaves.py` — 13 static checks for this leaf (run against source)
- `daemon-manual` skill — inspection patterns for `logs/token_ledger.jsonl`
- `lingtai-kernel-anatomy reference/file-formats.md` §10 — token ledger schema
- `lingtai-kernel-anatomy reference/memory-system.md` §5 — token ledger in the durability hierarchy
