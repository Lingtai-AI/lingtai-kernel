---
name: avatar-contract
tool: avatar
contract_version: 7
related_files:
  - src/lingtai/tools/avatar/BEHAVIORS.md
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/avatar/_launcher.py
  - src/lingtai/tools/avatar/settings.py
  - src/lingtai/kernel/_fsutil.py
  - src/lingtai/tools/psyche/settings.py
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/avatar/ANATOMY.md
  - src/lingtai/tools/avatar/manual/SKILL.md
  - src/lingtai/adapters/avatar_launcher.py
  - src/lingtai/adapters/posix/avatar_launcher.py
  - src/lingtai/adapters/windows/avatar_launcher.py
  - src/lingtai/cli.py
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/kernel/tool_result_summary.py
  - tests/test_tool_family_avatar_migration.py
  - tests/test_avatar_preset_inheritance.py
  - tests/test_tool_plugin_declaration.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Avatar capability contract

`avatar` spawns independent peer agents (分身) as fully detached processes, and
distributes shared rules across the avatar subtree. It registers **one** public
tool, `avatar`, dispatched by an `action` enum (`spawn` | `rules` | `settings`
| `manual`).
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
returns the same deterministic unknown-action error envelope as any other
unrecognized action value, and performs no spawn, rules, or manual side effect.

**contract_version 4** (breaking): `avatar` is migrated to the LTP v2
action-separated envelope (`src/lingtai/tools/CONTRACT.md`). The former flat
property bag is gone: action-specific fields now live inside a strict, closed
per-action `input` object, and the model-facing root is exactly `action`,
`input`, required `reasoning`, and optional `summarize`, with
`additionalProperties: false`.

- Public tool name and action values are **unchanged**: `avatar` with
  `spawn | rules | manual`.
- `spawn` owns `name`, `type`, `comment`, `dry_run`, `confirm`. `rules` owns
  `rules_content`. `manual` has strict empty input. A key belonging to another
  action's branch, an unknown root field, a non-boolean `summarize`, or a
  missing/non-object `input` is rejected **before any handler I/O** with the
  generic typed failure (`{"status": "failed", "error_code":
  "INVALID_ARGUMENT", ...}`), performing no spawn, ledger, `.rules`, or process
  side effect. Previously an unrecognized extra key (e.g. the retired `dir`)
  was silently ignored; it now fails loudly.
- The spawn mission brief remains root `reasoning` (injected as `_reasoning`),
  **not** an `input` property, and is captured per call — it never leaks from
  one call into the next.
- The unknown/omitted-`action` error envelope is **preserved exactly** (see
  §Invalid or missing `action`).
- `manual`'s result gains `manual_path` (the host-local packaged resource
  path) alongside the unchanged `status`, `action`, and exact `manual` body.
- `avatar` joins the kernel's `_LTP_V2_MIGRATED_FAMILIES` allowlist so the
  root `summarize` boolean it advertises is honored by the single central
  summarizer rather than silently ignored.

**contract_version 7**: only an avatar spawn approved with a Driver child
endpoint lease persists a restrictive `.lingtai-derived-child.json` marker
before it is launched, outside the child-managed `system/` namespace. This makes the
requirement for nested derived-launch authority survive a direct restart of the
same child directory; the launch environment marker is redundant only. It does
not carry, create, or validate authority, and it does not claim to withstand a
same-OS-user child that can edit its own directory.

Schema composition and envelope dispatch are delegated to the optional generic
`src/lingtai/tools/tool_family/` infrastructure. That is an implementation
choice, not a contract requirement: per that package's "Implementation
independence" rule, avatar could hand-write an equivalent `handle()` without
changing any promise in this file.

**contract_version 5** (additive): Avatar opts into the generic read-only
`settings` action. It appears once immediately before `manual` and returns 16
immutable call-default, validation, and lifecycle rows with exactly
`key/current/default/configurable/comment`. All rows are
`configurable:false`; Avatar gains no file, environment source, writer, or
set/reset action. Parent identity, runtime/venv/auth, handoff, and
invocation/session facts remain outside the inventory. Provider/JSON/size
failure stays whole-inventory and bounded by the generic contract.

## Routing Card
Guarded by: [AV001](BEHAVIORS.md#behavior-av001),
[AV002](BEHAVIORS.md#behavior-av002)


**Use this when:**
- You are editing avatar spawning (shallow 初生 / deep 二重身), the spawn ledger,
  boot verification, or rules distribution.
- You are reviewing the mission-quality gate, avatar-name validation, the
  init.json plus narrow Psyche owner-document rewrite for a newborn avatar, or the `.prompt` / `.rules` signal
  files.

**Do not use this for:**
- Ephemeral in-process subagents/emanations: use `daemon` (see
  `src/lingtai/tools/daemon/CONTRACT.md`). An avatar is an *independent life* whose
  existence does not depend on the parent; a daemon does.
- Code navigation only: read `src/lingtai/tools/avatar/ANATOMY.md`.

**Fast paths:** tool schemas -> §Tool surface; on-disk layout -> §State &
storage; detached-process launch -> §Cross-platform invariants.

## Scope

- Canonical tool name: `avatar`. It is registered as a single model-facing tool
  whose root property set is exactly `action`, `input`, `reasoning`, and
  `summarize`, with `additionalProperties: false` and
  `required: ["action", "input", "reasoning"]`. `action` is the enum
  (`spawn` | `rules` | `settings` | `manual`) — schema-required, the same convention as
  `knowledge`, `mcp`, `skills`, `notification`, `system`, `soul`, and `daemon`.
  Each action's own strict, closed `input` schema is exposed to the model
  before invocation two ways, both generated from the one child registry: an
  `input.anyOf` disclosure branch per action (required because `settings` and
  `manual` both have strict empty input), and one root `allOf`/`if`/`then`
  condition per action correlating that action's `const` with that exact input
  shape. Dispatch re-validates independently and is always authoritative.
- Action-specific required inputs (`name` for spawn, `rules_content` for rules)
  are enforced both by the child schema and again in the handler.
- `action="spawn"` (must be passed explicitly — there is no default action)
  creates a sibling agent directory named after the avatar and launches it via
  the global venv. Shallow copies `init.json` plus only Psyche base/covenant
  owner inputs (no identity/pad/history);
  deep also copies `system/`, `knowledge/`, `exports/`, and `combo.json`.
- `action="rules"` writes a `.rules` signal to the caller and every descendant
  so each agent refreshes its own `system/rules.md`-derived prompt. It carries
  its own admin gate, independent of `spawn` — spawning never requires admin.
- `action="settings"` calls the Avatar-owned provider for immutable defaults,
  constraints, and lifecycle policy. It has no mutation or configuration I/O.
- `action="manual"` is read-only: it returns the exact packaged
  `src/lingtai/tools/avatar/manual/SKILL.md` body plus its host-local
  `manual_path`, and performs no filesystem mutation (no spawn, no ledger
  write, no `.rules` write). Avatar's manual ships inside this package rather
  than being installed into the agent's `.library` intrinsic catalog, so this
  action owns its own loader instead of the shared
  `load_installed_manual`/`build_manual_child` pair — that builder would
  report a `.library` path this family never reads.

**Non-goals:** the parent holds no in-process handle to the avatar — liveness is
checked purely via the filesystem handshake. The tool does not manage the
avatar's ongoing lifecycle after boot (mail/system intrinsics do that).

### Declared host-plugin boundary

Avatar is an official static `ToolPluginDeclaration`, reserved by the kernel and
mounted only through `register_agent_tool_plugins`. Its binder receives exactly
`workdir` and `avatar_parent`: the former resolves the current agent's local
spawn ledger, sibling directories, and rules signals; the latter exposes only
the existing parent name, optional inherited venv location, and the existing
any-admin-value authorization decision for `rules`. `AvatarManager` never holds
or accepts a whole Agent. The registrar still owns reservation, activation, and
mounting; `setup(agent)` is composition wiring only.

The declaration owns operational `spawn`/`rules`; generic composition inserts
the opted-in `settings` slot immediately before the reserved `manual` slot.
Avatar owns the manual child directly so its longstanding local-manual contract
remains unchanged: `manual` returns the packaged `manual/SKILL.md` body and its
package-local path, never an installed intrinsic copy and never a manager/host
side effect.

## Tool surface

### Envelope

Every call is `avatar(action=..., input={...}, reasoning="...", summarize?=bool)`.

| Root field | Required | Meaning |
|---|---|---|
| `action` | yes | One of `spawn` \| `rules` \| `settings` \| `manual`. No default. |
| `input` | yes | The selected action's own strict, closed object. |
| `reasoning` | yes | Cross-cutting rationale. For `spawn` this **is** the mission brief and becomes the avatar's first prompt. Never an `input` property. |
| `summarize` | no | Root-only result post-processing control, absent/false by default. Never action input, never reaches a handler. |

Rejected before any handler I/O, with no spawn/ledger/`.rules`/process side
effect: an unknown `action` (see below), a missing or non-object `input`, an
unknown root field, a non-boolean `summarize`, and any `input` key belonging to
another action's branch (`{"status": "failed", "error_code":
"INVALID_ARGUMENT", "message": "unsupported avatar input field"}`).

### `avatar` — `action="spawn"`

`input` owns exactly `name`, `type`, `comment`, `dry_run`, `confirm`. The
mission/task brief is root `reasoning` (injected as `_reasoning`), not an
`input` property, and is scoped to the single call that carried it.

| Input | Optional input | Success output | Error / gate shapes |
|---|---|---|---|
| `name` (required) | `type` (`shallow`\|`deep`, default `shallow`), `comment`, `dry_run`, `confirm` — nullable; null means absent | `{status: "ok", address, agent_name, type, pid, warning?}` (`warning` when boot is `slow`) | `{error: ...}` — missing/invalid name, bad type, missing parent `init.json`, path escapes network root, dir exists, or boot `failed` (with stderr tail); `{status: "confirmation_needed", warning, reason, preview}` on the mission-quality gate; `{status: "already_active", working_dir, message}` if a live peer of that name exists; `{status: "dry_run", preview, message}` when `dry_run=true` |

The mission-quality gate refuses empty / very short (<20 chars) / debug-placeholder
missions unless `confirm=true`; `dry_run` is exempt. Avatar names must match
`^[\w-]+$` (Unicode letters, digits, `_`, `-`), be ≤64 chars, and carry no dot,
slash, or leading `.` — the name doubles as the working-dir basename.

### `avatar` — `action="rules"`

`input` owns exactly `rules_content`. A `spawn`-branch key (e.g. `name`,
`dry_run`) in this action's `input` is rejected before the authorization check
and before any write.

| Input | Optional input | Success output | Error shapes |
|---|---|---|---|
| `rules_content` (required) | — | `{status: "ok", message, distributed_to: [...]}` | `{error: ...}` — empty `rules_content`, no admin privilege, or failure writing the self `.rules` signal |

`action="rules"` requires at least one truthy admin privilege (karma) on the
caller. This gate applies only to `rules`; `spawn` never checks admin. The
family must not hide this stronger action behind a weaker family posture.

### `avatar` — `action="settings"`

Input is exactly `{}`. Success is exactly `{"settings": [...]}`, with 16 rows
in the owner-defined stable order. Every row has exactly five fields, in order:
`key`, `current`, `default`, `configurable`, `comment`. The comments point to
stable `avatar-manual#...` sections that own meaning, source, precedence,
accepted values, timing, and the only valid change procedure. No source,
precedence, sensitivity, diagnostic, or writer metadata is projected.

The inventory contains only the pre-existing immutable spawn call defaults,
validation constraints, boot observation values, preset/environment/lifetime
policy, and cleared newborn-admin policy. Each fixed code value is both fresh
effective `current` and truthful `default`, and each row is
`configurable:false`. Per-call spawn inputs vary only one invocation. Avatar
owns no `settings/avatar.json`, action settings file, `LINGTAI_AVATAR_*`
environment peer, or alternate source.

Parent identity, runtime/venv/auth, handoff values, ignored capability
arguments, and invocation/session state are not settings and MUST NOT be read
or returned. A non-empty input fails before the provider runs. Provider,
malformed-row, non-JSON, or response-size failure produces the generic fixed
bounded no-row failure; no partial inventory or exception detail is exposed.
SHOW performs no filesystem, process, launcher, ledger, rules, privilege,
configuration, or environment mutation.

### `avatar` — `action="manual"`

Strict empty `input` (`{}`); any field is rejected. Performs no spawn or rules
I/O of any kind.

| Input | Optional input | Success output | Error shapes |
|---|---|---|---|
| `{}` | — | `{status: "ok", action: "manual", manual: <exact SKILL.md body>, manual_path: <host-local packaged path>}` | `{status: "degraded", action: "manual", manual: "", manual_path: ..., error: "avatar manual missing"}` if the packaged manual file is missing |

The result is avatar's own canonical shape, returned verbatim — it is never
nested inside another action's result envelope and never double-wrapped.

### Invalid or missing `action`

An `action` value outside `spawn`/`rules`/`settings`/`manual` — including an entirely
omitted `action` key — returns
`{error: "unknown action: <repr>, only 'spawn', 'rules', 'settings', or 'manual' is supported"}`
(for the omitted case, `<repr>` is `''`) without touching the filesystem, the
ledger, `.rules`, or launching any process. There is no default action: a
`name`- or `rules_content`-shaped payload with `action` omitted still fails
this way rather than being inferred as `spawn` or `rules`.

This exact envelope is a pinned public promise that predates the LTP v2
migration. The generic dispatcher's own `ACTION_REQUIRED` failure shape is
deliberately generic, so `AvatarManager.handle()` normalizes it back to the
string above strictly *after* dispatch returns — never by changing the generic
dispatcher's canonical error shape.

## State & storage

All paths are relative to the parent agent working directory (`<parent>/`) and
the network root (`<parent>/..`):

```text
<parent>/delegates/ledger.jsonl   # append-only spawn ledger (one JSON record/line)
<parent>/system/rules.md          # canonical rules; auto-distributed on spawn
<parent>/.rules                   # self rules signal (consumed by heartbeat)

<network-root>/<avatar-name>/     # sibling of the parent
  init.json                       # rewritten copy of parent's init.json
  settings/psyche.json            # base/covenant inheritance + spawn comment
  .prompt                         # first-turn brief (parent identity + reasoning), consumed once
  .rules                          # distributed rules signal
  logs/spawn.stderr               # captured child stderr for boot diagnosis
  logs/agent.log                  # rotating stdlib logging (boot + runtime warnings)
  .lingtai-derived-child.json     # Driver-derived child state only
  knowledge/ exports/ combo.json  # deep mode only (system/ also has deep state)
```

The avatar's `init.json` is a deep copy of the parent's with: `agent_name` set,
`lingtai` seed blanked, `admin` cleared, all six inert legacy prompt fields and
kernel/secretary prompt-override fields plus `addons` stripped, relative preset
paths re-rooted, and the avatar pinned to the parent's **default** preset. Its
separate Psyche document retains only parent base/covenant inputs, anchors their
relative pointers to the parent workdir, replaces comment with the spawn comment,
and omits `comment_file`. The spawn brief is delivered out-of-band via the
`.prompt` signal file, not the `lingtai` seed.

Each spawn appends a ledger record (`event: "avatar"`, `name`, `working_dir`,
`mission`, `type`, `pid`, `boot_status`, optional `boot_error`). Rules
distribution walks the ledger tree (cycle-guarded) and writes `.rules` to each
live descendant.

## Cross-platform launcher contract

- `AvatarManager` writes `.lingtai-derived-child.json` before process launch
  only for a Driver-approved decision that carries a child endpoint lease. Its
  presence is the authoritative restrictive state: every later
  `lingtai run <dir>` treats that directory as derived and requires authority
  before a nested daemon/avatar launch. Malformed or unexpected marker state
  remains restrictive. For upgrade compatibility, the former
  `system/derived_child.json` location is also restrictive when present; only
  `FileNotFoundError` from both locations relaxes the state, while any other
  read failure remains restrictive. This marker is not a credential, grant,
  parent identity, or authorization boundary; in the same-OS-user trusted-host
  model it protects against accidental launch-path/configuration loss, not a
  child that can edit its own directory.
- `AvatarManager` resolves the existing interpreter policy and submits the
  exact argv `[python, "-m", "lingtai", "run", <dir>]` plus
  `logs/spawn.stderr` to the avatar-local Port. Cwd is inherited. The
  `LINGTAI_DERIVED_AVATAR_EXECUTION=1` environment override is redundant
  immediate-launch defense only; it is non-secret and never carries an
  authority bearer. The opaque one-use lease travels with that same Driver
  decision to the POSIX launcher, which alone consumes it into the child's
  `pass_fds`; any early return or setup failure closes the unconsumed lease.
- The Port returns a positive PID and an opaque adapter handle. `poll()` is
  nonblocking and returns the exact integer child return code or `None`.
- Production adapters disconnect stdin/stdout and own a binary-write stderr
  file, closing the parent descriptor after launch. `release()` performs a
  best-effort, non-raising final observation and never terminates a live avatar.
- POSIX (`src/lingtai/adapters/posix/avatar_launcher.py`) uses
  `start_new_session=True`; `terminate()` is one-process TERM and
  `force_terminate()` is one-process KILL. Neither operation claims tree
  management.
- Windows (`src/lingtai/adapters/windows/avatar_launcher.py`,
  `WindowsAvatarLauncherAdapter`) uses
  `creationflags=_win32.DETACHED_CREATIONFLAGS` (new process group + no window,
  from the shared `lingtai.adapters.windows._win32` surface) plus
  `close_fds=True` in place of `start_new_session`. It keeps the same
  stdin/stdout-disconnect, owned binary-write stderr file, PID/exit-code, and
  non-killing `release()` contracts. Honest termination tier (owner decision
  U7): on Windows `Popen.terminate()` and `Popen.kill()` **both** call
  `TerminateProcess` — there is no graceful signal. The Windows adapter does not
  pretend a graceful tier exists: `terminate()` and `force_terminate()` are
  **both** forceful, immediate termination of exactly the owned process, never a
  tree kill. The Driver child-endpoint handoff is currently POSIX-only: Windows
  closes and rejects a supplied lease rather than launching a child without its
  approved endpoint.
- The selector `select_avatar_launcher()` returns the Windows adapter when
  `os.name == "nt"` (lazy import) and the POSIX adapter when `os.name ==
  "posix"`, both via lazy imports so each mechanism module loads only on its
  own platform. Any other `os.name` still fails loudly with `NotImplementedError`.
- `python` is resolved lazily via `lingtai.venv_resolve.resolve_venv` /
  `venv_python` from the avatar's `init.json` → global runtime. The
  `lingtai.tools → lingtai` import edge is allowed only inside setup/handlers.
- Boot verification polls for the avatar's `.agent.heartbeat` handshake file (up
  to the owner-declared 5.0s window, 0.1s interval). If the child exits first, spawn
  is `failed` and a bounded stderr tail is returned; if neither happens in the
  window, boot is `slow` and a warning is attached.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| The POSIX launcher preserves exact detached launch, PID/exit truth, one-process termination, and non-killing release contracts; unrecognized platforms fail loudly | `src/lingtai/adapters/posix/avatar_launcher.py`, `src/lingtai/adapters/avatar_launcher.py` | `tests/test_avatar_launcher.py::test_posix_launch_contract_and_release`, `::test_selector_selects_posix_and_fails_loud_for_unsupported` |
| The Windows launcher uses `DETACHED_CREATIONFLAGS` + `close_fds` (no `start_new_session`), keeps the same disconnect/stderr/PID/exit/non-killing-release contracts, and maps both `terminate` and `force_terminate` to forceful `TerminateProcess`; the selector routes `os.name == "nt"` to it | `src/lingtai/adapters/windows/avatar_launcher.py`, `src/lingtai/adapters/avatar_launcher.py` | `tests/test_avatar_launcher_windows.py::test_selector_returns_windows_adapter_when_os_name_is_nt`, `::test_windows_launch_uses_detached_flags_and_disconnects_streams`, `::test_windows_terminate_and_force_terminate_both_forceful`, `::test_windows_release_never_raises_and_never_terminates` |
| Boot policy keeps heartbeat-first precedence, exact early-exit truth, and a live-process slow path without termination | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_launcher.py::test_manager_boot_policy_uses_opaque_port_and_preserves_precedence`, `::test_manager_slow_observation_does_not_terminate_child` |
| `setup` claims and mounts exactly one official `avatar` tool, preserving its manager capability object | `src/lingtai/tools/avatar/__init__.py`, `src/lingtai/kernel/tool_plugin/__init__.py` | `tests/test_tool_family_avatar_migration.py::test_agent_mounts_avatar_only_through_the_official_registrar` |
| Each spawn appends a ledger record | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_ledger_records_spawn` |
| `dry_run` previews without spawning and does not require `confirm` | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_dry_run_returns_preview_without_spawning`, `::test_dry_run_does_not_require_confirm` |
| The mission-quality gate rejects empty/short/placeholder missions | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_helper_rejects_empty`, `::test_helper_rejects_short`, `::test_helper_rejects_test_word`, `::test_helper_rejects_test_prefix` |
| Unsafe / duplicate avatar names are rejected | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_spawn_rejects_unsafe_name`, `::test_spawn_duplicate_name_error` |
| Shallow spawn does not copy identity files | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_spawn_does_not_copy_identity_files` |
| `action="rules"` requires admin and non-empty content; `spawn` does not inherit that gate | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_rules_requires_admin`, `::test_rules_requires_content`; `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_spawn_does_not_inherit_rules_permission_gate` |
| `action="settings"` is immediately before `manual`, returns the exact 16 five-field fixed-policy rows, points to owner-manual anchors, excludes private/runtime/auth/handoff state, accepts no writer input or environment peer, and fails as one bounded result | `src/lingtai/tools/avatar/settings.py`, `src/lingtai/tools/avatar/__init__.py` | `tests/test_tool_family_avatar_migration.py::test_avatar_settings_inventory_is_exact_fresh_and_excludes_private_state`, `::test_avatar_settings_is_show_only_and_has_no_environment_peer`, `::test_avatar_settings_provider_failure_is_one_bounded_result`; `tests/test_tool_settings_contract.py` |
| Rules are distributed recursively to descendants (cycle-safe) | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_rules_distributes_recursively`, `::test_rules_root_not_duplicated_via_cycle` |
| Spawning distributes existing rules to the newborn | `src/lingtai/tools/avatar/__init__.py` | `tests/test_avatar_rules.py::test_spawn_distributes_existing_rules`, `::test_spawn_deep_clone_also_gets_rules_signal` |
| `_prepare_deep` refuses a non-sibling destination | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::test_prepare_deep_refuses_non_sibling_dst` |
| `action="manual"` returns the exact packaged manual body and mutates nothing | `src/lingtai/tools/avatar/__init__.py` | `tests/test_tool_family_avatar_migration.py::test_avatar_manager_uses_only_granted_ports_for_local_manual_and_rules`, `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_manual_returns_exact_body_and_performs_no_mutation` |
| Invalid `action` fails deterministically without touching other actions | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_invalid_action_fails_deterministically`, `::test_spawn_missing_name_fails_without_affecting_other_actions` |
| `action` is schema-required (root `required: ["action", "input", "reasoning"]`) and runtime-required — a missing `action` never defaults to `spawn`, `rules`, `settings`, or `manual`, regardless of which action's fields are present, and mutates nothing | `src/lingtai/tools/avatar/__init__.py` | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_missing_action_fails_deterministically_regardless_of_payload_shape`; `tests/test_avatar_rules.py::TestAvatarRulesAction::test_explicit_spawn_action_required` |
| The daemon blacklists the canonical `avatar` name (not the retired two-tool names) | `src/lingtai/tools/daemon/__init__.py` | `tests/test_daemon.py::test_build_tool_surface_blacklist`, `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_daemon_excludes_avatar_from_child_surface` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Exactly one official tool mounts on setup | `tests/test_tool_family_avatar_migration.py::test_agent_mounts_avatar_only_through_the_official_registrar` | Boot with `capabilities={"avatar": {}}` and inspect tools | Rules distribution or spawning silently unavailable |
| Spawn is ledgered with boot status | `tests/test_layers_avatar.py::test_ledger_records_spawn` | Spawn an avatar, inspect `delegates/ledger.jsonl` | No audit trail; duplicate/liveness checks break |
| Mission gate stops accidental spawns | `tests/test_layers_avatar.py::test_helper_rejects_short` | `avatar(action="spawn", input={"name": "x"}, reasoning="short")` with a 5-char mission, confirm gate | Stray detached processes from batched calls |
| Name validation / path-scope guard holds | `tests/test_layers_avatar.py::test_spawn_rejects_unsafe_name` | Spawn with `name="../x"`, confirm refusal | Avatar dir escapes the network root |
| Boot verification catches early child exit | `tests/test_avatar_launcher.py::test_manager_boot_policy_uses_opaque_port_and_preserves_precedence` | Corrupt an avatar `init.json`, spawn, confirm `failed` + stderr | Parent thinks a crashed avatar is alive |
| Omitted `action` never defaults to spawn | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_missing_action_fails_deterministically_regardless_of_payload_shape` | Call `avatar(input={"name": "x", "confirm": true}, reasoning="...")` with no `action`, confirm error + no spawned process | A model omitting `action` could accidentally spawn an untracked process |
| Rules propagate to the whole subtree | `tests/test_avatar_rules.py::test_rules_distributes_recursively` | Set rules on a root, confirm `.rules` on each descendant | Descendants run stale/ungoverned rules |
| `manual` action is read-only | `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_manual_returns_exact_body_and_performs_no_mutation` | Call `avatar(action="manual", input={}, reasoning="...")`, confirm no new files | A "manual" call could accidentally spawn or mutate rules |
| Settings SHOW is exact, fresh, bounded, read-only, and excludes private state | `tests/test_tool_family_avatar_migration.py` settings tests plus `tests/test_tool_settings_contract.py` | Call settings with `{}` and a set-shaped input; inspect exact fields/order/manual targets and no source mutation | A writer, stale result, partial inventory, or host-state leak could drift from the owner contract |

Run before merging avatar changes:

```bash
python -m pytest tests/test_avatar_launcher.py tests/test_layers_avatar.py \
  tests/test_avatar_rules.py tests/test_avatar_preset_inheritance.py \
  tests/test_avatar_timezone_inheritance.py \
  tests/test_tool_family_avatar_migration.py tests/test_tool_settings_contract.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters resolve the top-level tool description
  through `wire_tool_description`: the global `WIRE_TOOL_DESCRIPTION` pointer
  while the resident `## tools` section is opted in via
  `LINGTAI_TOOL_PROSE_SECTION_ENABLED`, otherwise the full
  `FunctionSchema.description` prose (that section is off by default, so the
  wire is where the canonical prose lands). Nested parameter descriptions are
  unchanged either way.
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
