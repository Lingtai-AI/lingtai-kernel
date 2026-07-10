# `_fsutil` staged migration plan (issue #510)

`src/lingtai_kernel/_fsutil.py` is the shared, stdlib-only foundation for
crash-atomic writes and JSON/JSONL/timestamp handling. It exists so future
state-writing code stops re-solving atomicity, UTF-8 (`ensure_ascii`), and
`read_json(default=...)` exception policy differently in each module.

This module ships **with one proof-of-concept caller migrated**
(`WorkingDir.write_manifest` → `atomic_write_json`, proven byte-identical by
`tests/test_fsutil.py::test_write_manifest_byte_identical_to_legacy_format`).
All remaining call sites are tracked here and migrated in **small, reviewable
follow-up PRs**, never a single sweep.

## Helper ⇄ legacy-pattern mapping

| Helper | Replaces | Default format contract |
| --- | --- | --- |
| `atomic_write_text` | temp-file + `os.replace` / `Path.rename` text writes | parent dirs created; temp is a sibling of target; no fsync unless `fsync=True` |
| `atomic_write_json` | `tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))` + `os.replace` | `ensure_ascii=False`, `indent=2`, **no trailing newline** |
| `read_json` | local `read_json(default)` / `read_manifest`-style readers | returns `default` on missing/decode/OSError/type-mismatch, else raises |
| `append_jsonl` | `open(path, "ab")` + `json.dumps + "\n"` | returns first-byte offset; `ensure_ascii=True` (ledger default) |
| `iter_jsonl_records` / `tail_jsonl_records` | hand-rolled JSONL readers / reverse-tail recovery loops | tolerates blank/torn lines by default |
| `utc_now_iso` | `datetime.now(timezone.utc).isoformat()` | one canonical tz-aware UTC string |

## Migration discipline

1. Migrate **one concern per PR** (e.g. "route mailbox writes", not "route all
   writes").
2. For every model-visible / on-disk format, add a byte-identity golden test
   capturing the pre-migration bytes for a known input *before* swapping the
   implementation. A format change is a separate, explicitly-flagged decision.
3. If a caller currently adds a trailing newline (e.g.
   `write_resolved_manifest`) or fsyncs, either keep that by passing the
   matching option or call it out as an intentional change — do not let the
   helper silently alter the format.
4. `os.replace` is atomic **only on the same filesystem** — the helper always
   writes the temp file as a sibling of the target. Do not pass cross-device
   targets expecting atomicity.

## Staged caller backlog (highest-risk first)

Counts are from a `canonical/main` scan (`grep -rl` over `src/`): `os.replace`
in 34 files, `.write_text(` in 39 files, JSONL `open(..., "ab")` in 5 files.
These are migration *candidates*, not a commitment to change every one.

### Stage 1 — riskiest crash-sensitive / model-visible state (do first)
- [x] `workdir.py::WorkingDir.write_manifest` (`.agent.json`) — **migrated here**
- [x] `base_agent/__init__.py::_write_status_snapshot` (`.status.json`) — migrated with byte-parity coverage
- [ ] `workdir.py::write_resolved_manifest` (`manifest.resolved.json`, trailing newline → pass explicit newline or keep inline)
- [ ] notification files (`.notification/*.json`)
- [ ] `intrinsics/email/primitives.py` message/`message.json` writes (currently direct `write_text`; some non-contact paths can escape non-ASCII)
- [ ] `intrinsics/email/manager.py` contact persistence (already atomic via `mkstemp`+`os.replace`; migrate for consistency)

### Stage 2 — ledgers / append-only logs
- [ ] `token_ledger.py` append (also mirrors to sqlite via returned offset — keep offset contract)
- [ ] soul-flow / event JSONL writers
- [ ] `tool_result_recovery.py` JSONL read/reverse-tail
- [ ] runtime log JSONL recovery paths

### Stage 3 — broader writes + retire local helpers
- [ ] `migrate/*.py` write/replace sites
- [ ] `intrinsics/soul/config.py`, psyche snapshot/molt/archive writers
- [ ] remaining `read_json(default=...)`-style local readers → `_fsutil.read_json`

Each checkbox above should land as (or within) its own PR with format-parity
tests, then this list is updated so coverage is visible rather than assumed.
