---
name: psyche-contract
tool: psyche
contract_version: 1
related_files:
  - src/lingtai/tools/psyche/__init__.py
  - src/lingtai/tools/psyche/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Psyche capability contract

`psyche` is the bare essentials of agent self: the working `pad`, the
self-authored `lingtai` identity, the true `name`/nickname, and `context` molt
(shed history, keep a briefing). It is dispatched on an `(object, action)`
matrix, not a flat action enum. The implementation lives in `src/lingtai/tools/psyche/`;
the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the pad (`system/pad.md`), the self-authored identity
  (`system/lingtai.md` → `character` prompt section), or the true-name/nickname
  handlers.
- You are reviewing the context molt machinery — snapshotting, history archive,
  keep-lists, and the post-molt reminder.

**Do not use this for:**
- Provider-context rebuild after summarizing: that is `system(action=
  'summarize', rebuild=true)` (`src/lingtai/tools/system/CONTRACT.md`). Molt sheds
  *history*; summarize rebuilds the *active context* from pending summaries.
- Notification dismissal (including the post-molt reminder): the reminder is
  dismissed via the `notification` tool (`src/lingtai/tools/notification/CONTRACT.md`).
- Code navigation only: read `src/lingtai/tools/psyche/ANATOMY.md`.

**Fast paths:** the `(object, action)` matrix -> §Tool surface; molt/snapshot
paths -> §State & storage.

## Scope

- Canonical tool name: `psyche`.
- Schema requires both `object` and `action`.
- Non-goals: notification verbs, summarize/rebuild, mailbox actions.
- Former name `anima` is not a compatibility alias.

## Tool surface

Schema (`src/lingtai/tools/psyche/__init__.py:get_schema`) requires `object` (enum:
`pad`, `context`, `name`, `lingtai`) and `action` (free string, validated
against `_VALID_ACTIONS`). Dispatch is the `_DISPATCH[(object)][action]` table.

| object → action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `lingtai` → `update` | `content` (empty clears) | — | `{status: "ok", path}` | (object/action guard errors) |
| `lingtai` → `load` | — | — | `{status: "ok", size_bytes, content_preview}` | — |
| `pad` → `edit` | `content` (empty clears) **or** `files` | `content`, `files` | `{status: "ok", path, size_bytes}` | `{error: "Provide content ... files, or both."}`; `{error: "Files not found: ..."}` |
| `pad` → `load` | — | — | `{status: "ok", path, size_bytes, content_preview, append_*}` | — |
| `pad` → `append` | — | `files` (empty clears; `None` returns current) | `{status: "ok", action, files, count}` | `{error: "Files not found: ..."}`; `{error: "Only text files ..."}`; `{error: "Append files total ... token limit ..."}` |
| `context` → `molt` | `summary`, `session_journal_path` | `keep_tool_calls`, `keep_last` | `{status: "ok", note, molt_count, tokens_before/after/shed, kept_*, archive_path, summary_path, session_journal_path}` | `{error: "summary is required ..."}`; journal-validation `{error}`; `{error: "No active chat session to molt."}`; `{error, unmatched_ids}` / `{error, missing_call_ids}` for bad keep-lists; `{error: "keep_last must be ..."}` |
| `name` → `set` | `content` | — | `{status: "ok", name}` | `{error: "Name cannot be empty..."}`; `{error}` (name already set / immutable) |
| `name` → `nickname` | `content` (empty clears) | — | `{status: "ok", nickname}` | — |

An unknown `object` returns `{error: "Unknown object: ..."}`; a valid object
with an out-of-set action returns `{error: "Invalid action ... for <obj> ..."}`.

Note: system-forced molt is a separate code path (`context_forget`), invoked by
the kernel on a `.clear` signal, not an agent-callable `(object, action)`. It
synthesizes its own `psyche(object='context', action='molt', _initiator=
'system')` call/result pair.

## State & storage

All paths are relative to the agent working directory (`agent._working_dir`).

```text
system/pad.md                          — the working pad (pad edit/load)
system/pad_append.json                 — pinned read-only reference file list
system/lingtai.md                      — self-authored identity → `character` section
system/summaries/molt_<count>_<ts>.md  — molt retrospective (agent- or system-authored)
history/snapshots/snapshot_<count>_<ts>.json — frozen pre-molt ChatInterface substrate
history/chat_history.jsonl             — live chat history (moved on molt)
history/chat_history_archive.jsonl     — appended pre-molt history on each molt
.notification/post-molt.json           — post-molt "resume work" reminder (published on molt)
```

- `pad edit`/`lingtai update` write their file, then reload the corresponding
  protected prompt section (`pad` / `character`) and flush the system prompt.
- `context molt` writes a snapshot, wipes the session, increments `molt_count`
  (persisted to `init.json` manifest), archives + unlinks `chat_history.jsonl`,
  replays `keep_last`/`keep_tool_calls` into the fresh session, writes a summary,
  and publishes `.notification/post-molt.json`. Snapshot/summary writes are
  best-effort and never block the molt.

## Cross-platform invariants

- All file access is via `pathlib.Path` (`read_text`/`write_text`,
  `mkdir`, `unlink`) with UTF-8 for text sections; snapshot/summary writes go to
  a `.tmp` sibling then `Path.replace` for atomicity. DOCUMENT.
- Append-file paths may be absolute or workdir-relative (`_resolve_path`);
  binary files are rejected (`_is_text_file` null-byte + UTF-8 check). DOCUMENT.
- No subprocess/PTY; molt operates on in-memory `ChatInterface` objects plus the
  history-file archive. DOCUMENT — no platform-specific behavior; all file access
  via pathlib.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `psyche` is a wired intrinsic; `anima` is not an alias | `src/lingtai/tools/psyche/__init__.py` | `tests/test_psyche.py::test_psyche_is_intrinsic`, `tests/test_psyche.py::test_anima_alias_removed`, `tests/test_pad.py::test_psyche_in_all_intrinsics` |
| Schema exposes exactly the four objects and their valid actions | `src/lingtai/tools/psyche/__init__.py:get_schema`, `_VALID_ACTIONS` | `tests/test_psyche.py::test_psyche_schema_has_correct_objects`, `tests/test_psyche.py::test_psyche_schema_has_correct_actions` |
| `lingtai update` writes `system/lingtai.md` and loads the `character` section | `src/lingtai/tools/psyche/_lingtai.py:_lingtai_update`/`_lingtai_load` | `tests/test_psyche.py::test_lingtai_update_writes_lingtai_md`, `tests/test_psyche.py::test_lingtai_load_writes_character_section` |
| `pad edit` writes `system/pad.md`; empty content clears; bare edit is rejected | `src/lingtai/tools/psyche/_pad.py:_pad_edit` | `tests/test_psyche.py::test_pad_edit_content_only`, `tests/test_psyche.py::test_pad_edit_empty_errors` |
| `pad edit` imports files and errors on missing paths | `src/lingtai/tools/psyche/_pad.py:_pad_edit` | `tests/test_psyche.py::test_pad_edit_with_files`, `tests/test_psyche.py::test_pad_edit_missing_file_errors` |
| `context molt` returns the faint-memory result and shed counts | `src/lingtai/tools/psyche/_molt.py:_context_molt` | `tests/test_psyche.py::test_molt_returns_faint_memory` |
| Molt writes a summary file under `system/summaries/` | `src/lingtai/tools/psyche/_snapshots.py:_write_molt_summary` | `tests/test_psyche.py::test_molt_writes_summary_file_for_agent_path` |
| System-forced molt (`context_forget`) still works and writes its own summary | `src/lingtai/tools/psyche/_molt.py:context_forget` | `tests/test_psyche.py::test_context_forget_still_works`, `tests/test_psyche.py::test_context_forget_writes_summary_file_for_system_path` |
| A failed summary write does not block the molt | `src/lingtai/tools/psyche/_molt.py`, `_snapshots.py` | `tests/test_psyche.py::test_summary_write_failure_does_not_block_molt` |
| Invalid object/action are rejected before any handler runs | `src/lingtai/tools/psyche/__init__.py:handle` | `tests/test_psyche.py::test_invalid_object`, `tests/test_psyche.py::test_invalid_action_for_object` |
| The stop path does not overwrite `system/pad.md` | `src/lingtai/tools/psyche/_pad.py` | `tests/test_psyche.py::test_stop_does_not_overwrite_pad_md` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| `(object, action)` guard rejects unknowns pre-dispatch | `tests/test_psyche.py::test_invalid_object` / `test_invalid_action_for_object` | Call `psyche(object='foo', action='bar')` | Silent no-ops or wrong handler |
| Pad/lingtai edits reload their prompt sections | `tests/test_psyche.py::test_lingtai_load_writes_character_section`, `tests/test_pad.py::test_pad_edit_then_load` | Edit pad, inspect prompt sections | Stale identity/notes in prompt |
| Molt archives history and increments count | `tests/test_psyche.py::test_molt_returns_faint_memory` | Molt, inspect `history/` + manifest | Lost history / miscounted molts |
| Molt journal gate refuses without a valid session-journal path | `src/lingtai/tools/psyche/_molt.py:_context_molt` (journal validation) | Molt without `session_journal_path` | Context shed with no durable trail |
| Snapshot/summary write failure is non-fatal | `tests/test_psyche.py::test_summary_write_failure_does_not_block_molt` | Make summaries dir unwritable, molt | A disk hiccup wedges the agent |

Run before merging psyche changes:

```bash
python -m pytest tests/test_psyche.py tests/test_pad.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
