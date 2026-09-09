---
name: file-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/file/CONTRACT.md
  - src/lingtai/tools/file/ANATOMY.md
  - src/lingtai/tools/file/__init__.py
  - src/lingtai/tools/file/manual/SKILL.md
  - src/lingtai/tools/file/_read.py
  - src/lingtai/tools/file/settings.py
  - tests/test_read_continuation.py
  - tests/test_file_tool_family.py
  - tests/test_file_tool_plugin_package.py
  - tests/test_file_settings.py
maintenance: |
  LABT v2, migrated 2026-08 from tests/test_read_continuation.py (previously
  filed as C005 under src/lingtai/tools/telegram/BEHAVIORS.md; re-homed here so
  file owns its own behavior tests). F001 guards read continuation; F002 guards
  settings SHOW inventory, redaction, construction snapshots, and failure.
  Keep both LABTs synchronized with CONTRACT.md and the focused tests, and keep
  ANATOMY.md reciprocal.
---
# File Behavior Tests

LABT v2. F001 and F002 are self-contained agent-executable behavioral tests for
the `file` tool's read continuation and settings safety. They guard the read and
settings clauses of
`src/lingtai/tools/file/CONTRACT.md` (frontmatter name `file-contract`); the
implementations are the unified `_read.py` operation and owner `settings.py`
provider under the File package.

## Behavior F001 — File read continuation via next_offset pagination

- **id**: F001
- **title**: `file` read-only continuation, truncation caps, and next_offset semantics
- **guards**: `file-contract` § read (`_read.py`) — read-only (caps, `next_offset` continuation, `line_truncated` skip) ([CONTRACT.md](CONTRACT.md#read-_readpy--read-only))
- **supersedes**: tests/test_read_continuation.py (CONVERT_BEHAVIOR)
- **runner**: any LingTai agent with the `file` tool
- **prerequisites**: repo checkout with src/lingtai/tools/file; a fixture file larger than one page and a file containing one very long line (or scratch copies the executor creates in a temp dir); operates on fixture files under tests/fixtures — never writes (read-only action only).
- **estimate**: 20 minutes

### Steps

1. Read a file larger than one page: `file(action=read, file_path=<fixture>, offset=1, limit=null, max_chars=null)`.
2. Take `next_offset` from the result and call read again with `offset=<next_offset>, limit=null, max_chars=null`.
3. Repeat with explicit `offset`/`limit` and with `max_chars` smaller than the page.
4. Read a file containing one very long line.

### Expected evidence

- [ ] **Caps**: `DEFAULT_READ_CAP_CHARS == 100_000`, `READ_HARD_CAP_CHARS == 200_000`, `PREVENTIVE_MAX_CHARS == 200_000`.
- [ ] **First page**: when the file exceeds the cap, the result is truncated and reports `next_offset == last_returned_line + 1`, plus `remaining_lines_estimate`, `total_lines`, and `lines_shown`.
- [ ] **Continuation**: reading with `offset == next_offset` starts exactly at that line (no overlap, no gap) and again returns its own `next_offset` for the next page.
- [ ] **Offset/limit**: explicit `offset` and `limit` are honored; a per-call `max_chars` returns `cap_chars == <requested>` and `returned_chars <= cap_chars`.
- [ ] **Single long line**: the line is truncated with `line_truncated: true`, `last_returned_line == 1`, and `next_offset == 2`.
- [ ] **Schema/description**: the read result schema mentions `max_chars`, `read-manual`, `truncated`, `next_offset`, and `line_truncated`, and the limits are documented as `100 000` and `200 000` (spaced thousands) in the tool description.
- [ ] **Discovery/non-regression**: `file(action="settings", input={})`
  exposes the same source-backed `read.default_*` and fresh
  `read.runtime_max_chars` policies without changing pagination, cap, file, or
  environment state.

### Pass / Fail

PASS when pagination is gap-free and overlap-free, caps hold, and the long-line case reports exactly `last_returned_line == 1` / `next_offset == 2`; FAIL on any skipped or repeated line.

## Behavior F002 — File settings SHOW is exact, redacted, and read-only

- **id**: F002
- **title**: File settings inventory truth, construction snapshot, redaction, and no mutation
- **guards**: `file-contract` § Settings discovery — read-only ([CONTRACT.md](CONTRACT.md#settings-discovery))
- **runner**: any LingTai agent with the `file` tool
- **prerequisites**: repo checkout with the File owner tests; disposable backend environment fixtures only; never modify a real launcher or sidecar configuration.
- **estimate**: 10 minutes

### Steps

1. Call `file(action="settings", input={})` and record all rows.
2. Confirm every comment points to `file-manual#settings-show-only`, the single
   stable File settings section.
3. In disposable construction fixtures only, resolve an explicit backend mode,
   canonical-plus-legacy executable overrides, and the legacy-only case; then
   mutate the ambient environment after binding.
4. Call one unchanged ordinary read action and build one complete real Agent
   prompt.

### Expected evidence

- [ ] **Order and inventory**: `settings` appears immediately before `manual`;
  all 13 canonical rows have exactly `key`, `current`, `default`,
  `configurable`, and `comment`, in that order.
- [ ] **Source truth**: read, glob, grep, traversal, native-timeout, UTF-8, and
  runtime-cap values/defaults match their actual source constants; the first 11
  rows are immutable and only `backend.mode` / `backend.sidecar` are
  configurable.
- [ ] **Construction truth**: explicit backend selection and canonical-then-
  legacy sidecar alias precedence are captured at service construction; later
  environment changes do not alter SHOW.
- [ ] **Manual routing**: every comment names the exact stable `file-manual`
  SHOW heading; row values and construction truth remain source/snapshot-backed.
- [ ] **Redaction**: the one sidecar row renders both current/default as
  `<redacted>`, the private flag is absent, and no executable or local path
  occurs anywhere in the result.
- [ ] **Failure/bounds**: unavailable construction truth produces the fixed
  complete no-row failure; nonempty or non-object input is rejected; the shared
  65,536-byte whole-response bound applies; no set/reset interface exists.
- [ ] **Non-regression/no mutation**: the ordinary read result and complete
  prompt build remain valid, while the tree and process environment are byte-for-
  byte unchanged by SHOW.

### Pass / Fail

PASS when inventory is source/snapshot-backed, exactly five-field,
manual-routed, redacted, bounded, and mutation-free, unavailable truth fails
without rows, and ordinary File behavior still works. FAIL on an extra or
missing row/field, path leak, ambient reread, mutation interface, partial
inventory, or operational result drift.
