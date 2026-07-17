---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/avatar/_launcher.py
  - src/lingtai/tools/avatar/CONTRACT.md
  - src/lingtai/adapters/avatar_launcher.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/posix/avatar_launcher.py
  - src/lingtai/tools/avatar/manual/SKILL.md
  - tests/test_avatar_rules.py
  - src/lingtai/tools/avatar/glossary-en.md
  - src/lingtai/tools/avatar/glossary-zh.md
  - src/lingtai/tools/avatar/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# core/avatar

Avatar capability — spawn independent peer agents (分身) as fully detached
processes. Two modes:

- **Shallow (初生):** Copy `init.json` to a new working dir, strip identity,
  launch. The avatar gets the same LLM config + capabilities but no history.
- **Deep (二重身):** Copy identity and durable knowledge (`system/`, `knowledge/`, `exports/`)
  plus `init.json`, strip name + history. The avatar is a doppelgänger — same
  character, pad, knowledge — but starts a fresh conversation.

Both modes launch `lingtai-agent run <dir>` as a detached process. The avatar is an
independent life — its existence does not depend on yours.

## Components

- `avatar/__init__.py` — validation, preparation, boot policy, ledger, rules,
  schemas, and setup. The core class is `AvatarManager`.
- `avatar/_launcher.py` — immutable launch request/receipt and the avatar-local
  opaque-handle Port.

## Public API

The capability exposes one public tool, `avatar`, dispatched by an `action`
enum:

| Action | Description |
|------|-------------|
| `spawn` | Spawn a new avatar agent (shallow or deep) with a given name, optional type, and optional comment. Accepts `dry_run` (preview-only) and `confirm` (acknowledge mission-quality gate). |
| `rules` | Set rules content and distribute via `.rules` signal files to self + all descendants. |
| `manual` | Read-only: returns the exact `manual/SKILL.md` body. No mutation. |

`action` has no default — it is required both by the schema
(`"required": ["action"]`) and at runtime, matching the established action-tool
convention already used by `knowledge`, `mcp`, `skills`, `notification`,
`system`, `soul`, and `daemon`. Omitting `action` fails deterministically via
the same `dispatch_action` unknown-action envelope as an unrecognized value;
it never falls through to `spawn`.

`avatar` uses a single plain top-level `type: object` schema with an explicit
`action` enum, not a top-level `allOf`/`oneOf` combinator — some
OpenAI-compatible strict tool validators reject top-level JSON Schema
combinators. Action-specific required inputs beyond `action` itself (`name`
for spawn, `rules_content` for rules) are validated in the handler, not the
schema.

## Internal Module Layout

```
avatar/__init__.py
  ├── AvatarManager.__init__        — stores parent agent ref
  ├── handle()                      — public dispatcher for the avatar tool
  │                                    (action: spawn | rules | manual)
  ├── _manual()                     — reads the packaged manual/SKILL.md body
  │
  │  Spawn pipeline:
  ├── _spawn()                      — validates name, checks liveness, prepares working dir, launches process
  ├── _make_avatar_init()           — builds avatar's init.json from parent's (strips identity, reroots paths)
  ├── _prepare_deep()               — copies system/ + knowledge/ + exports/ + combo.json for deep mode
  ├── _launch()                     — resolves argv and delegates to the launcher Port
  ├── _wait_for_boot()              — polls .agent.heartbeat or Port exit truth
  │
  │  Ledger:
  ├── _append_ledger()              — appends spawn event to delegates/ledger.jsonl
  ├── _read_ledger()                — reads all ledger records
  │
  │  Rules distribution:
  ├── _rules()                      — admin-gated rules update, distributes via .rules signal files
  ├── _walk_avatar_tree()           — recursively discovers all descendants from ledger files
  └── _distribute_rules_to_descendants() — writes .rules signal file to every descendant
```

## Key Invariants

- **Name validation:** Avatar names must match `^[\w-]+$` (Unicode-aware), max 64 chars, no dots or path separators. The name doubles as the working directory basename.
- **Path scope:** The avatar's working directory must be a direct sibling of the parent's (same parent directory). Resolved path is checked against the network root to prevent escape.
- **No identity inheritance:** Avatars get no name (`agent_name` is set to the avatar name), no admin privileges, no comment, no brief, no addons (IMAP/Telegram). The inherited `lingtai` seed is blanked; the first turn still arrives via a separate `.prompt` signal file.
- **Preset stability:** Avatars always spawn on the parent's DEFAULT preset, not its currently-active one. Materialized `llm` + `capabilities` are stripped so the avatar re-materializes from the preset on first boot.
- **Relative path re-rooting:** Preset paths (`default`, `active`, `allowed`) that are relative are re-rooted against the parent's working dir so they remain valid from the avatar's different directory.
- **Liveness check:** Before spawning, existing ledger entries are observed through a target-bound `PosixAgentPresenceStoreAdapter` and Core `observe_alive()` policy. If a live avatar with the same name exists, the spawn is refused with `already_active`.
- **Boot verification:** After launching, `_wait_for_boot()` polls for `.agent.heartbeat` or Port exit truth within 5 seconds. If the process exits before handshaking, stderr is captured and the failure is reported. Port release after observation never kills a live slow avatar.
- **Deep copy scope guard:** `_prepare_deep()` asserts `dst.parent == src.parent` to prevent rmtree from reaching outside the network root.
- **Mission-quality gate (issue #33):** Before any filesystem mutation, `_spawn` runs `_mission_looks_unsafe(reasoning)` — empty / sub-20-char / debug-placeholder missions return `{"status": "confirmation_needed", ...}` unless `confirm=true`. The dry-run path is exempt (its purpose is preview without commitment).
- **Dry-run (issue #33):** `dry_run=true` short-circuits after parent `init.json` is loaded and before any working dir is created or process launched, returning `{"status": "dry_run", "preview": {...}}`. The preview includes whether the mission would have tripped the quality gate.

## Dependencies

- `lingtai.i18n` — `t()` for localized strings
- `lingtai.kernel.agent_presence` + `lingtai.adapters.posix.agent_presence` — ordered Core liveness policy and the target-bound production presence adapter
- `lingtai.kernel.handshake` — `resolve_address()` for ledger-based tree walking
- `lingtai.venv_resolve` — `resolve_venv()`, `venv_python()` for resolving the Python executable to launch the avatar
- `lingtai.agent.Agent` — parent agent type (TYPE_CHECKING only)

## Composition

- **Parent:** `src/lingtai/tools/` (tool package).
- **Siblings:** `daemon/`, `mcp/`, `knowledge/` (private durable memory), `skills/` (skill catalog), `bash/`.
- **Kernel hooks:** `setup()` is called during capability initialization; `AvatarManager.handle()` is registered as the single `avatar` tool handler, internally dispatching `spawn`/`rules`/`manual` via `lingtai.kernel.tool_dispatch.dispatch_action`. The daemon capability blacklists `avatar` to prevent avatar-in-daemon recursion and rules mutation from emanations.

Platform process mechanics are in `adapters/avatar_launcher.py` and the
POSIX reference adapter. Unsupported Windows selection fails loudly; a future
Windows adapter and native acceptance remain outside this re-cut.
