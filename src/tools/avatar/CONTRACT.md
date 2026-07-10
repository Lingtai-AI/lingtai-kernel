---
name: avatar-contract
tool: avatar_spawn, avatar_rules
contract_version: 1
related_files:
  - src/tools/avatar/__init__.py
  - src/tools/avatar/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Avatar capability contract

`avatar` spawns independent peer agents (分身) as fully detached processes, and
distributes shared rules across the avatar subtree. It registers **two** tools:
`avatar_spawn` and `avatar_rules`. The implementation lives in
`src/tools/avatar/`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing avatar spawning (shallow 初生 / deep 二重身), the spawn ledger,
  boot verification, or rules distribution.
- You are reviewing the mission-quality gate, avatar-name validation, the
  init.json rewrite for a newborn avatar, or the `.prompt` / `.rules` signal
  files.

**Do not use this for:**
- Ephemeral in-process subagents/emanations: use `daemon` (see
  `src/tools/daemon/CONTRACT.md`). An avatar is an *independent life* whose
  existence does not depend on the parent; a daemon does.
- Code navigation only: read `src/tools/avatar/ANATOMY.md`.

**Fast paths:** tool schemas -> §Tool surface; on-disk layout -> §State &
storage; detached-process launch -> §Cross-platform invariants.

## Scope

- Canonical tool names: `avatar_spawn` and `avatar_rules`. They are registered
  as two separate tools with plain top-level object schemas (deliberately no
  `allOf` combinator, which some strict OpenAI-compatible validators reject).
- `avatar_spawn` creates a sibling agent directory named after the avatar and
  launches it via the global venv. Shallow copies only `init.json` (no
  identity/pad/history); deep also copies `system/`, `knowledge/`, `exports/`,
  and `combo.json`.
- `avatar_rules` writes a `.rules` signal to the caller and every descendant so
  each agent refreshes its own `system/rules.md`-derived prompt.

**Non-goals:** the parent holds no in-process handle to the avatar — liveness is
checked purely via the filesystem handshake. The tool does not manage the
avatar's ongoing lifecycle after boot (mail/system intrinsics do that).

## Tool surface

### `avatar_spawn`

Schema requires `name`. The mission/task brief arrives via the injected
`_reasoning` field (becomes the avatar's first prompt), not a schema property.

| Inputs | Optional inputs | Success output | Error / gate shapes |
|---|---|---|---|
| `name` (required) | `type` (`shallow`\|`deep`, default `shallow`), `comment`, `dry_run`, `confirm` | `{status: "ok", address, agent_name, type, pid, warning?}` (`warning` when boot is `slow`) | `{error: ...}` — missing/invalid name, bad type, missing parent `init.json`, path escapes network root, dir exists, or boot `failed` (with stderr tail); `{status: "confirmation_needed", warning, reason, preview}` on the mission-quality gate; `{status: "already_active", working_dir, message}` if a live peer of that name exists; `{status: "dry_run", preview, message}` when `dry_run=true` |

The mission-quality gate refuses empty / very short (<20 chars) / debug-placeholder
missions unless `confirm=true`; `dry_run` is exempt. Avatar names must match
`^[\w-]+$` (Unicode letters, digits, `_`, `-`), be ≤64 chars, and carry no dot,
slash, or leading `.` — the name doubles as the working-dir basename.

### `avatar_rules`

| Inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|
| `rules_content` (required) | — | `{status: "ok", message, distributed_to: [...]}` | `{error: ...}` — empty `rules_content`, no admin privilege, or failure writing the self `.rules` signal |

`avatar_rules` requires at least one truthy admin privilege on the caller.

## State & storage

All paths are relative to the parent agent working directory (`<parent>/`) and
the network root (`<parent>/..`):

```text
<parent>/delegates/ledger.jsonl   # append-only spawn ledger (one JSON record/line)
<parent>/system/rules.md          # canonical rules; auto-distributed on spawn
<parent>/.rules                   # self rules signal (consumed by heartbeat)

<network-root>/<avatar-name>/     # sibling of the parent
  init.json                       # rewritten copy of parent's init.json
  .prompt                         # first-turn brief (parent identity + reasoning), consumed once
  .rules                          # distributed rules signal
  logs/spawn.stderr               # captured child stderr for boot diagnosis
  system/ knowledge/ exports/ combo.json   # deep mode only
```

The avatar's `init.json` is a deep copy of the parent's with: `agent_name` set,
`lingtai` seed blanked, `admin` cleared, `comment` reset, kernel/secretary
prompt-override fields and `addons` stripped, relative preset paths re-rooted,
and the avatar pinned to the parent's **default** preset. The spawn brief is
delivered out-of-band via the `.prompt` signal file, not the `lingtai` seed.

Each spawn appends a ledger record (`event: "avatar"`, `name`, `working_dir`,
`mission`, `type`, `pid`, `boot_status`, optional `boot_error`). Rules
distribution walks the ledger tree (cycle-guarded) and writes `.rules` to each
live descendant.

## Cross-platform invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- The avatar is launched via `subprocess.Popen([python, "-m", "lingtai", "run",
  <dir>], stdin=DEVNULL, stdout=DEVNULL, stderr=<logs/spawn.stderr>,
  start_new_session=True)` — a fully detached POSIX session so the avatar
  survives the parent and is not in the parent's process group.
- `python` is resolved lazily via `lingtai.venv_resolve.resolve_venv` /
  `venv_python` from the avatar's `init.json` → global runtime. The
  `tools → lingtai` import edge is allowed only inside setup/handlers.
- Boot verification polls for the avatar's `.agent.heartbeat` handshake file (up
  to `_BOOT_WAIT_SECS = 5.0s`, 0.1s interval). If the child exits first, spawn
  is `failed` and a bounded stderr tail is returned; if neither happens in the
  window, boot is `slow` and a warning is attached.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `setup` registers both `avatar_spawn` and `avatar_rules` | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_setup_avatar`, `::test_add_capability_avatar` |
| Each spawn appends a ledger record | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_ledger_records_spawn` |
| `dry_run` previews without spawning and does not require `confirm` | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_dry_run_returns_preview_without_spawning`, `::test_dry_run_does_not_require_confirm` |
| The mission-quality gate rejects empty/short/placeholder missions | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_helper_rejects_empty`, `::test_helper_rejects_short`, `::test_helper_rejects_test_word`, `::test_helper_rejects_test_prefix` |
| Unsafe / duplicate avatar names are rejected | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_spawn_rejects_unsafe_name`, `::test_spawn_duplicate_name_error` |
| Shallow spawn does not copy identity files | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_spawn_does_not_copy_identity_files` |
| `avatar_rules` requires admin and non-empty content | `src/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_rules_requires_admin`, `::test_rules_requires_content` |
| Rules are distributed recursively to descendants (cycle-safe) | `src/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_rules_distributes_recursively`, `::test_rules_root_not_duplicated_via_cycle` |
| Spawning distributes existing rules to the newborn | `src/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_spawn_distributes_existing_rules`, `::test_spawn_deep_clone_also_gets_rules_signal` |
| `_prepare_deep` refuses a non-sibling destination | `src/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_prepare_deep_refuses_non_sibling_dst` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Both tools register on setup | `tests/test_layers_avatar.py::test_setup_avatar` | Boot with `capabilities={"avatar": {}}` and inspect tools | Rules distribution or spawning silently unavailable |
| Spawn is ledgered with boot status | `tests/test_layers_avatar.py::test_ledger_records_spawn` | Spawn an avatar, inspect `delegates/ledger.jsonl` | No audit trail; duplicate/liveness checks break |
| Mission gate stops accidental spawns | `tests/test_layers_avatar.py::test_helper_rejects_short` | `avatar_spawn(name="x")` with a 5-char mission, confirm gate | Stray detached processes from batched calls |
| Name validation / path-scope guard holds | `tests/test_layers_avatar.py::test_spawn_rejects_unsafe_name` | Spawn with `name="../x"`, confirm refusal | Avatar dir escapes the network root |
| Boot verification catches early child exit | boot-status path in `tests/test_layers_avatar.py` | Corrupt an avatar `init.json`, spawn, confirm `failed` + stderr | Parent thinks a crashed avatar is alive |
| Rules propagate to the whole subtree | `tests/test_avatar_rules.py::test_rules_distributes_recursively` | Set rules on a root, confirm `.rules` on each descendant | Descendants run stale/ungoverned rules |

Run before merging avatar changes:

```bash
python -m pytest tests/test_layers_avatar.py tests/test_avatar_rules.py -q
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
- **Validation:** `python -m tools.glossary_validator --check`.
