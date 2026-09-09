---
name: file-manual
description: "Read/write/edit/glob/grep guide for LingTai's `file` tool: safe overwrite/edit discipline, explicit non-UTF-8 routing, SHOW-only policy, and pagination recovery."
version: 0.5.0
tags: [files, read, write, edit, grep, glob, settings, encoding, utf-8]
last_changed_at: "2026-09-08T00:00:00Z"
related_files:
- src/lingtai/tools/file/__init__.py
- src/lingtai/tools/file/CONTRACT.md
- src/lingtai/tools/file/_read.py
- src/lingtai/tools/file/_write.py
- src/lingtai/tools/file/_edit.py
- src/lingtai/tools/file/_glob.py
- src/lingtai/tools/file/_grep.py
- src/lingtai/tools/file/settings.py
- src/lingtai/services/file_io.py
- src/lingtai/services/file_io_sidecar.py
- ENVIRONMENT_VARIABLES.md
- src/lingtai/intrinsic_skills/read-manual/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
maintenance: |
  Tracks the tool/capability behavior it teaches; update when that tool's behavior changes.
---

# File Manual

Use the action schema for routine calls; `action` selects one child and `input`
belongs only to it. A JSON `null` optional value means absent and schema
defaults apply. Call `file(action="manual", input={})` once for unfamiliar or
high-consequence overwrite/edit, conversion, large-read, or prompt-source work.
This one-time lookup is enough: after the manual result, continue the ordinary
requested action; repeating the same manual call is an error loop. Use root
`summarize=true` only when bulky read/search output need not be exact; leave it
false for write/edit receipts and
manual text.

## Actions

The schema owns fields and defaults: `read` returns numbered UTF-8 text; `write`
creates or overwrites a whole file; `edit` does exact replacement and leaves a
missing or ambiguous match untouched; `glob` finds names; `grep` searches UTF-8
text; `settings` is strict-empty SHOW; and `manual` does no target I/O. Keep
search `path`/`glob` narrow: a partial traversal is not proof of no match.

## Safety boundaries

- File is text-only and UTF-8-only: binary, image, and audio inspection is out
  of scope; it never detects or converts encodings silently. For known non-UTF-8
  text, use an explicit codec such as
  `Path(...).read_text(encoding='gbk', errors='replace')`; `replace` is lossy,
  so review the source, then convert with `iconv -f gbk -t utf-8` before durable
  UTF-8 storage.
- Use approved targets under the granted workdir. Relative paths stay under its
  canonical root; parent/symlink escapes fail. Authorization still governs every
  target; containment is not protection against overwriting in-root files. Keep private
  local paths out of human-facing/public results.
- Before an important `write`, read the target. For `edit`, make `old_string`
  identify the intended match and inspect the receipt; `replace_all` is only for
  intentionally every-match edits. Read exact receipts: write reports `path` /
  UTF-8 `bytes`, edit reports `replacements`. Neither action changes the current
  system prompt. For durable prompt sources, load the owning `psyche` domain
  manual; use `context(action="rebuild", input={})` only for explicit activation.
  `context(action="manual", input={})` owns reconstruction depth.

## Read depth

A successful read can still be partial. If `truncated=true`, inspect
`cap_chars`, `returned_chars`, `last_returned_line`, and
`remaining_lines_estimate`, then resume with `offset=next_offset`. An uncapped
window may still end before EOF: compare `offset + lines_shown` to `total_lines`.
For a stable file, `next_offset` advances by physical line without overlap. `line_truncated=true` means one physical line was cut to a
bounded prefix; `next_offset` skips to the next line and no later File read
recovers its hidden tail. Use the nested
`read-manual` catalog entry (installed at
`<workdir>/.library/intrinsic/capabilities/read-manual/SKILL.md`) for cap math,
metadata preflight, spill recovery, complete-content loops, and targeted
`bash`/`sed`/`grep` escape hatches.

## Settings SHOW only

`settings` accepts only `{}` and returns the complete five-field rows
`key`, `current`, `default`, `configurable`, `comment`; there is no writer or
set/reset form. The source-backed immutable rows are read defaults `2000` lines
and `100 000` characters, runtime ceiling up to `200 000`, glob `2000`, grep
`200`, max file `4194304` bytes, search `20000` visited / `8.0` seconds,
canonical exclusions, sidecar timeout `30.0` seconds, and `utf-8`. The runtime
cap is observed fresh on each SHOW: `min(FileIOPort.max_result_chars, 200000)`,
or `200000` without a positive Host cap. Per-call choices do not change defaults.
The two
construction rows are `backend.mode` (default `auto`; explicit factory value,
then `LINGTAI_FILE_IO_BACKEND`, then auto) and sensitive `backend.sidecar`
(`LINGTAI_FILE_IO_SIDECAR`, then legacy `LINGTAI_SEARCH_SIDECAR`; a nonempty
canonical value shadows the alias). `backend.mode` accepts only
whitespace-trimmed, case-insensitive `auto`, `rust`, or `python`; invalid values
fail construction. An unusable canonical sidecar still shadows the alias;
packaged/dev-tree discovery and auto-mode Python fallback remain, while explicit
Rust fails without a usable source. No binary is downloaded; Python ignores the
sidecar. An override is an executable path/name: verify ownership and keep it private.

The first eleven rows are immutable; `configurable` is not permission. SHOW
uses source values plus the construction snapshot and does not reread ambient
environment. Sidecar current/default values are redacted. Unavailable truth or
a response over 65,536 UTF-8 bytes fails the whole inventory, never returns
partial rows. After an authorized construction change outside SHOW,
rebuild/restart the owning service, recheck SHOW, and run a File search; no File
settings action changes policy. Comments route to this single owner heading:
`file-manual#settings-show-only`.
