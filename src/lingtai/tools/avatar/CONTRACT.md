---
name: avatar-contract
tool: avatar
contract_version: 3
related_files:
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/avatar/_launcher.py
  - src/lingtai/tools/avatar/ANATOMY.md
  - src/lingtai/tools/avatar/manual/SKILL.md
  - src/lingtai/adapters/avatar_launcher.py
  - src/lingtai/adapters/posix/avatar_launcher.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Avatar capability contract

`avatar` spawns independent peer agents (分身) as fully detached processes, and
distributes shared rules across the avatar subtree. It registers **one** public
tool, `avatar`, dispatched by an `action` enum (`spawn` | `rules` | `manual`).
The implementation lives in `src/lingtai/tools/avatar/`; the code is the source
of truth.

**contract_version 2** (breaking): the former two-tool surface (`avatar_spawn`,
`avatar_rules`) was merged into the single `avatar` tool below. `avatar_spawn`
and `avatar_rules` are no longer registered as model-facing tools; there is no
compatibility alias.

**contract_version 3** (breaking): `action` is now schema-required
(`"required": ["action"]`) and runtime-required — omitting `action` no longer
defaults to `spawn`. This aligns `avatar` with the established action-tool
contract already followed by `knowledge`, `mcp`, `skills`, `notification`,
`system`, `soul`, and `daemon`: every action tool in this repository requires
an explicit `action`, with no implicit default action. A missing `action`
returns the same deterministic `dispatch_action` unknown-action error envelope
as any other unrecognized action value, and performs no spawn, rules, or
manual side effect.

## Routing Card

**Use this when:**
- You are editing avatar spawning (shallow 初生 / deep 二重身), the spawn ledger,
  boot verification, or rules distribution.
- You are reviewing the mission-quality gate, avatar-name validation, the
  init.json rewrite for a newborn avatar, or the `.prompt` / `.rules` signal
  files.

**Do not use this for:**
- Ephemeral in-process subagents/emanations: use `daemon` (see
  `src/lingtai/tools/daemon/CONTRACT.md`). An avatar is an *independent life* whose
  existence does not depend on the parent; a daemon does.
- Code navigation only: read `src/lingtai/tools/avatar/ANATOMY.md`.

**Fast paths:** tool schemas -> §Tool surface; on-disk layout -> §State &
storage; detached-process launch -> §Cross-platform invariants.

## Scope

- Canonical tool name: `avatar`. It is registered as a single tool with a plain
  top-level object schema (deliberately no `allOf`/`oneOf` combinator, which
  some strict OpenAI-compatible validators reject) and an explicit `action`
  enum (`spawn` | `rules` | `manual`). `action` is schema-required
  (`"required": ["action"]`) — the same convention as `knowledge`, `mcp`,
  `skills`, `notification`, `system`, `soul`, and `daemon`. Action-specific
  required inputs beyond `action` itself (`name` for spawn, `rules_content`
  for rules) are validated in the handler, not the schema.
- `action="spawn"` (must be passed explicitly — there is no default action)
  creates a sibling agent directory named after the avatar and launches it via
  the global venv. Shallow copies only `init.json` (no identity/pad/history);
  deep also copies `system/`, `knowledge/`, `exports/`, and `combo.json`.
- `action="rules"` writes a `.rules` signal to the caller and every descendant
  so each agent refreshes its own `system/rules.md`-derived prompt. It carries
  its own admin gate, independent of `spawn` — spawning never requires admin.
- `action="manual"` is read-only: it returns the exact packaged
  `src/lingtai/tools/avatar/manual/SKILL.md` body and performs no filesystem
  mutation (no spawn, no ledger write, no `.rules` write).

**Non-goals:** the parent holds no in-process handle to the avatar — liveness is
checked purely via the filesystem handshake. The tool does not manage the
avatar's ongoing lifecycle after boot (mail/system intrinsics do that).

## Tool surface

### `avatar` — `action="spawn"`

Requires `name`. The mission/task brief arrives via the injected `_reasoning`
field (becomes the avatar's first prompt), not a schema property.

| Inputs | Optional inputs | Success output | Error / gate shapes |
|---|---|---|---|
| `name` (required) | `type` (`shallow`\|`deep`, default `shallow`), `comment`, `dry_run`, `confirm` | `{status: "ok", address, agent_name, type, pid, warning?}` (`warning` when boot is `slow`) | `{error: ...}` — missing/invalid name, bad type, missing parent `init.json`, path escapes network root, dir exists, or boot `failed` (with stderr tail); `{status: "confirmation_needed", warning, reason, preview}` on the mission-quality gate; `{status: "already_active", working_dir, message}` if a live peer of that name exists; `{status: "dry_run", preview, message}` when `dry_run=true` |

The mission-quality gate refuses empty / very short (<20 chars) / debug-placeholder
missions unless `confirm=true`; `dry_run` is exempt. Avatar names must match
`^[\w-]+$` (Unicode letters, digits, `_`, `-`), be ≤64 chars, and carry no dot,
slash, or leading `.` — the name doubles as the working-dir basename.

### `avatar` — `action="rules"`

| Inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|
| `rules_content` (required) | — | `{status: "ok", message, distributed_to: [...]}` | `{error: ...}` — empty `rules_content`, no admin privilege, or failure writing the self `.rules` signal |

`action="rules"` requires at least one truthy admin privilege on the caller.
This gate applies only to `rules`; `spawn` never checks admin.

### `avatar` — `action="manual"`

No inputs consumed beyond `action`.

| Inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|
| — | — | `{status: "ok", action: "manual", manual: <exact SKILL.md body>}` | `{status: "degraded", action: "manual", manual: "", error: ...}` if the packaged manual file is missing |

### Invalid or missing `action`

An `action` value outside `spawn`/`rules`/`manual` — including an entirely
omitted `action` key — returns
`{error: "unknown action: <repr>, only 'spawn', 'rules', or 'manual' is supported"}`
(for the omitted case, `<repr>` is `''`) without touching the filesystem, the
ledger, `.rules`, or launching any process. There is no default action: a
`name`- or `rules_content`-shaped payload with `action` omitted still fails
this way rather than being inferred as `spawn` or `rules`.

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

## Cross-platform launcher contract

- `AvatarManager` resolves the existing interpreter policy and submits the
  exact argv `[python, "-m", "lingtai", "run", <dir>]` plus
  `logs/spawn.stderr` to the avatar-local Port. Cwd and environment are
  inherited; the Port does not add a cwd or environment override.
- The Port returns a positive PID and an opaque adapter handle. `poll()` is
  nonblocking and returns the exact integer child return code or `None`.
- Production adapters disconnect stdin/stdout and own a binary-write stderr
  file, closing the parent descriptor after launch. `release()` performs a
  best-effort, non-raising final observation and never terminates a live avatar.
- POSIX uses `start_new_session=True`; `terminate()` is one-process TERM and
  `force_terminate()` is one-process KILL. Neither operation claims tree
  management.
- Unsupported platforms, including Windows, fail loudly at the selector;
  this re-cut adds no Windows launch implementation, selector wiring, or
  native acceptance claim.
- `python` is resolved lazily via `lingtai.venv_resolve.resolve_venv` /
  `venv_python` from the avatar's `init.json` → global runtime. The
  `lingtai.tools → lingtai` import edge is allowed only inside setup/handlers.
- Boot verification polls for the avatar's `.agent.heartbeat` handshake file (up
  to `_BOOT_WAIT_SECS = 5.0s`, 0.1s interval). If the child exits first, spawn
  is `failed` and a bounded stderr tail is returned; if neither happens in the
  window, boot is `slow` and a warning is attached.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| The POSIX launcher preserves exact detached launch, PID/exit truth, one-process termination, and non-killing release contracts; unsupported platforms fail loudly | `src/lingtai/adapters/posix/avatar_launcher.py`, `src/lingtai/adapters/avatar_launcher.py` | `tests/test_avatar_launcher.py::test_posix_launch_contract_and_release`, `::test_selector_selects_posix_and_fails_loud_for_unsupported` |
| Boot policy keeps heartbeat-first precedence, exact early-exit truth, and a live-process slow path without termination | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_launcher.py::test_manager_boot_policy_uses_opaque_port_and_preserves_precedence`, `::test_manager_slow_observation_does_not_terminate_child` |
| `setup` registers exactly one public tool, `avatar`, and no old public names | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_setup_avatar`, `::test_add_capability_avatar`, `::TestUnifiedAvatarTool::test_setup_registers_exactly_one_public_tool` |
| Each spawn appends a ledger record | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_ledger_records_spawn` |
| `dry_run` previews without spawning and does not require `confirm` | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_dry_run_returns_preview_without_spawning`, `::test_dry_run_does_not_require_confirm` |
| The mission-quality gate rejects empty/short/placeholder missions | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_helper_rejects_empty`, `::test_helper_rejects_short`, `::test_helper_rejects_test_word`, `::test_helper_rejects_test_prefix` |
| Unsafe / duplicate avatar names are rejected | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_spawn_rejects_unsafe_name`, `::test_spawn_duplicate_name_error` |
| Shallow spawn does not copy identity files | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_spawn_does_not_copy_identity_files` |
| `action="rules"` requires admin and non-empty content; `spawn` does not inherit that gate | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_rules_requires_admin`, `::test_rules_requires_content`; `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_spawn_does_not_inherit_rules_permission_gate` |
| Rules are distributed recursively to descendants (cycle-safe) | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_rules_distributes_recursively`, `::test_rules_root_not_duplicated_via_cycle` |
| Spawning distributes existing rules to the newborn | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_spawn_distributes_existing_rules`, `::test_spawn_deep_clone_also_gets_rules_signal` |
| `_prepare_deep` refuses a non-sibling destination | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_prepare_deep_refuses_non_sibling_dst` |
| `action="manual"` returns the exact packaged manual body and mutates nothing | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_manual_returns_exact_body_and_performs_no_mutation` |
| Invalid `action` fails deterministically without touching other actions | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_invalid_action_fails_deterministically`, `::test_spawn_missing_name_fails_without_affecting_other_actions` |
| `action` is schema-required (`required: ["action"]`) and runtime-required — a missing `action` never defaults to `spawn`, `rules`, or `manual`, regardless of which action's fields are present, and mutates nothing | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_omitted_action_fails_deterministically_and_does_not_default_to_spawn`, `::test_missing_action_fails_deterministically_regardless_of_payload_shape`; `tests/test_avatar_rules.py::TestAvatarRulesAction::test_explicit_spawn_action_required` |
| The daemon blacklists the canonical `avatar` name (not the retired two-tool names) | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_build_tool_surface_blacklist`, `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_daemon_excludes_avatar_from_child_surface` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Exactly one tool registers on setup | `tests/test_layers_avatar.py::test_setup_avatar` | Boot with `capabilities={"avatar": {}}` and inspect tools | Rules distribution or spawning silently unavailable |
| Spawn is ledgered with boot status | `tests/test_layers_avatar.py::test_ledger_records_spawn` | Spawn an avatar, inspect `delegates/ledger.jsonl` | No audit trail; duplicate/liveness checks break |
| Mission gate stops accidental spawns | `tests/test_layers_avatar.py::test_helper_rejects_short` | `avatar(action="spawn", name="x")` with a 5-char mission, confirm gate | Stray detached processes from batched calls |
| Name validation / path-scope guard holds | `tests/test_layers_avatar.py::test_spawn_rejects_unsafe_name` | Spawn with `name="../x"`, confirm refusal | Avatar dir escapes the network root |
| Boot verification catches early child exit | `tests/test_avatar_launcher.py::test_manager_boot_policy_uses_opaque_port_and_preserves_precedence` | Corrupt an avatar `init.json`, spawn, confirm `failed` + stderr | Parent thinks a crashed avatar is alive |
| Omitted `action` never defaults to spawn | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_omitted_action_fails_deterministically_and_does_not_default_to_spawn` | Call `avatar(name="x", confirm=true)` with no `action`, confirm error + no spawned process | A model omitting `action` could accidentally spawn an untracked process |
| Rules propagate to the whole subtree | `tests/test_avatar_rules.py::test_rules_distributes_recursively` | Set rules on a root, confirm `.rules` on each descendant | Descendants run stale/ungoverned rules |
| `manual` action is read-only | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_manual_returns_exact_body_and_performs_no_mutation` | Call `avatar(action="manual")`, confirm no new files | A "manual" call could accidentally spawn or mutate rules |

Run before merging avatar changes:

```bash
python -m pytest tests/test_avatar_launcher.py tests/test_layers_avatar.py tests/test_avatar_rules.py tests/test_avatar_preset_inheritance.py tests/test_avatar_timezone_inheritance.py -q
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
