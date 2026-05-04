# core/avatar

Avatar capability — spawn independent peer agents (分身) as fully detached
processes. Two modes:

- **Shallow (初生):** Copy `init.json` to a new working dir, strip identity,
  launch. The avatar gets the same LLM config + capabilities but no history.
- **Deep (二重身):** Copy the entire working dir (`system/`, `codex/`, `exports/`)
  plus `init.json`, strip name + history. The avatar is a doppelgänger — same
  character, pad, knowledge — but starts a fresh conversation.

Both modes launch `lingtai run <dir>` as a detached process. The avatar is an
independent life — its existence does not depend on yours.

## Components

- `avatar/__init__.py` — the entire capability in a single file. `get_description` (`avatar/__init__.py:49-50`), `get_schema` (`avatar/__init__.py:53-81`), `setup` (`avatar/__init__.py:666-672`). The core class is `AvatarManager` (`avatar/__init__.py:85-664`).

## Public API

The `avatar` tool exposes two actions:

| Action   | Description |
|----------|-------------|
| `spawn`  | Spawn a new avatar agent (shallow or deep) with a given name, optional type, and optional comment |
| `rules`  | Set rules content and distribute via `.rules` signal files to self + all descendants |

## Internal Module Layout

```
avatar/__init__.py
  ├── AvatarManager.__init__        — stores parent agent ref
  ├── handle()                      — dispatcher (spawn/rules)
  │
  │  Spawn pipeline:
  ├── _spawn()                      — validates name, checks liveness, prepares working dir, launches process
  ├── _make_avatar_init()           — builds avatar's init.json from parent's (strips identity, reroots paths)
  ├── _prepare_deep()               — copies system/ + codex/ + exports/ + combo.json for deep mode
  ├── _launch()                     — runs `lingtai run <dir>` as a detached subprocess
  ├── _wait_for_boot()              — polls .agent.heartbeat or process exit for boot verification
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
- **No identity inheritance:** Avatars get no name (`agent_name` is set to the avatar name), no admin privileges, no comment, no brief, no addons (IMAP/Telegram). The inherited prompt is blanked; the first prompt arrives via a `.prompt` signal file.
- **Preset stability:** Avatars always spawn on the parent's DEFAULT preset, not its currently-active one. Materialized `llm` + `capabilities` are stripped so the avatar re-materializes from the preset on first boot.
- **Relative path re-rooting:** Preset paths (`default`, `active`, `allowed`) that are relative are re-rooted against the parent's working dir so they remain valid from the avatar's different directory.
- **Liveness check:** Before spawning, existing ledger entries are checked via `handshake.is_alive()`. If a live avatar with the same name exists, the spawn is refused with `already_active`.
- **Boot verification:** After launching, `_wait_for_boot()` polls for `.agent.heartbeat` or process exit within 5 seconds. If the process exits before handshaking, stderr is captured and the failure is reported.
- **Deep copy scope guard:** `_prepare_deep()` asserts `dst.parent == src.parent` to prevent rmtree from reaching outside the network root.

## Dependencies

- `lingtai.i18n` — `t()` for localized strings
- `lingtai_kernel.handshake` — `is_alive()` for liveness checks, `resolve_address()` for ledger-based tree walking
- `lingtai.venv_resolve` — `resolve_venv()`, `venv_python()` for resolving the Python executable to launch the avatar
- `lingtai.agent.Agent` — parent agent type (TYPE_CHECKING only)

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `daemon/`, `mcp/`, `library/`, `codex/`, `bash/`.
- **Kernel hooks:** `setup()` is called during capability initialization; `AvatarManager.handle()` is registered as the `avatar` tool handler. The daemon capability blacklists `avatar` to prevent avatar-in-daemon recursion.
