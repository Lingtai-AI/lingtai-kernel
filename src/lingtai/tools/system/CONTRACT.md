---
name: system-contract
tool: system
contract_version: 5
related_files:
  - src/lingtai/tools/system/__init__.py
  - src/lingtai/tools/system/plugin.py
  - src/lingtai/tools/system/settings.py
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/agent.py
  - src/lingtai/tools/system/schema.py
  - src/lingtai/tools/system/name.py
  - src/lingtai/tools/system/summarize.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/tools/system/ANATOMY.md
  - src/lingtai/tools/system/BEHAVIORS.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/kernel/malloc_relief.py
  - src/lingtai/kernel/tool_executor.py
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/settings-inventory/SKILL.md
  - tests/test_tool_family_system_migration.py
  - tests/test_system_sleep_alarm.py
  - tests/test_system_declared_plugin.py
  - tests/test_system_target_refresh.py
maintenance: |
  Keep related_files as repo-relative paths to real files, including the
  paired ANATOMY.md, the LTP/ToolFamily contracts this family is governed by,
  the system-manual this capability is taught by, and the migration evidence
  suite. If behavior and this contract disagree, the code is the source of
  truth — fix the contract in the same change and bump contract_version on
  breaking contract edits.
  contract_version 5 applies the System kernel-level catch-all rule to every
  effective setting without another concrete ToolPlugin owner while retaining
  a SHOW-only five-field surface. contract_version 4 added read-only discovery
  for the family-owned cache-miss budget and retired its legacy init source.
  contract_version 3 is the
  breaking public-ownership
  change: the public
  summarize action left for context (which split it into record-only summarize and full reconstruction rebuild)
  and the two name actions arrived here from the dissolved psyche family.
  summarize.py stays here as a private engine only — keep the context Contract
  link current, since that family owns the public actions driving it.
---

# System capability contract

`system` is the runtime, lifecycle, and identity tool: refresh/preset swaps,
self-sleep, karma-gated control of *other* agents, preset listing, and the
agent's true name/nickname. It does **not** own any notification verb — those
live on the standalone `notification` tool — and it no longer owns any public
context-hygiene action: molt, tool-result summarization, and the
full prompt reconstruction, summary application, and provider replay all belong to `context`
(`src/lingtai/tools/context/CONTRACT.md`). The implementation lives in
`src/lingtai/tools/system/`; the code is the source of truth.

One internal dependency crosses that public boundary and is deliberate:
`src/lingtai/tools/system/summarize.py` remains here as the **private engine**
for tool-result summarization. `context(action='summarize')` and
`context(action='rebuild')` import and call it, and the kernel's forced-rebuild
path imports `SUMMARIZE_MARKER`/`mark_pending_summaries_done` from it. That is
an internal runtime interface only — it is never reachable as a `system`
action, and `system(action='summarize')` fails loudly as an unknown action.

`system` is an **LTP v2 family** (`src/lingtai/tools/CONTRACT.md`): its final
model-facing root is exactly `action`, `input`, `reasoning`, and `summarize`
with `additionalProperties: false`, and each action's arguments live only in
that action's own strict `input` object — so `address` belongs to the seven
address verbs, `preset`/`revert_preset` only to `refresh`, and `content` only
to the two name actions. It is the third migrated *intrinsic* (after `soul` and
`notification`) and therefore composes its dispatching family per call rather
than owning a per-Agent manager, and drops the kernel-injected `_tc_id` at its
own Host boundary. The migration changed the argument shape only: the public
tool name, every retained action value, every privilege gate, receipt, and error
are unchanged, and the children consume no additional model tool slots.
`system` is on the kernel's `_LTP_V2_MIGRATED_FAMILIES` allowlist
(`src/lingtai/kernel/tool_result_summary.py`), so the root `summarize` boolean
it advertises is actually honored.

## Declared host plugin and manual

`DECLARATION` is a static official declaration constructed at import. The
kernel reserves `system`, validates its unchanged operational action inventory,
inserts the explicitly opted-in reserved `settings` action immediately before
the reserved `manual` action, then binds and mounts it through the controlled
registrar. The production binder receives only `workdir`,
`system_runtime`, and `identity`: the first addresses the installed manual and
agent-local documents; the second exposes the existing refresh/preset,
self-sleep, authorization, CPR, token, and audit operations; the third exposes
only durable naming. No plugin handler receives a whole Agent.

The family-owned `manual` child derives its installed location from
`DECLARATION.manual == "system-manual"`. The shipped router bundle remains
`src/lingtai/intrinsic_skills/system-manual/SKILL.md` and the installed path
remains `.library/intrinsic/capabilities/system-manual/SKILL.md`; no fallback
or public manual result shape changed. `handle(agent, args)` remains a direct
in-process compatibility adapter, while normal `lingtai.Agent` dispatch uses
the registrar-mounted bound handler. `tests/test_system_declared_plugin.py`
pins the static declaration, single official mount, identity port behavior, and
manual path.

### System settings catch-all

`settings.py` supplies the complete, stable, unique read-only inventory for
genuine kernel settings that have no other concrete ToolPlugin owner. Each row
projects exactly `key`, `current`, `default`, `configurable`, and `comment`.
The inventory includes the cache-miss budget, the seven ordinary
runtime-policy values resolved as environment > closed-v2 System file > fixed
default, effective non-policy root/manifest inputs, all effective `manifest.llm`
axes because no LLM ToolPlugin exists, and the System-owned Nudge, lifecycle,
prompt-pressure, session-statistics, logging, risky-action, Codex
auth-directory/transport/trace, and LLM timeout environment controls. Legacy
init/preset copies of ordinary runtime-policy fields remain compatibility data
and never become SHOW current values.
Compatibility aliases are folded into their canonical setting rather than
emitted as duplicate rows.

Every current and default value comes from the runtime's canonical reader,
resolver, constant, or selected registered-factory route. SHOW fresh-reads
effective init/preset inputs and per-use environment resolvers where the runtime
does; process-start constants are reported as the current effective process
state. Canonical provider-default normalization runs before the narrow
registered-factory classifier projects each LLM row. A selected factory's row
reports the effective value and default that factory actually consumes; an axis
the factory ignores reports null current/default. Effective adapter selectors
report the selected public route rather than malformed authored syntax, and an
unknown selected provider fails the complete action loudly. Projection may
inspect registry/factory identity, canonical constants, and signatures only; it
never constructs a client, reads credentials, or performs network I/O.

Prompt text, credentials, headers, authorization material, tokens, and sensitive
paths are fully redacted. Canonically invalid or non-JSON-finite public values,
an unreadable current owner source, and any unsafe/malformed projected row fail
the whole inventory with no partial row or exception detail. The exact evolving
provider/alias matrix, omission/null/authored distinctions, source precedence,
accepted values, timing, and authorized operator procedures live only in the
[`settings-inventory` manual](../../intrinsic_skills/system-manual/reference/settings-inventory/SKILL.md#llm-and-provider-inputs).

The cache row retains its current owner-resolved value, default `2_000_000`,
and pointer `system-manual#cache-miss-budget`. Missing System JSON selects the
known default; a higher valid environment source bypasses that document. The
remaining rows point to the exact sections in
`system-manual/reference/settings-inventory` that define source, accepted
values, precedence, invalid behavior, redaction, application timing, and the
authorized external change procedure.

The `settings` action accepts only `input={}` and has no set, reset, write,
receipt, or mutation API. `configurable: true` means the system manual provides
an authorized owner procedure through existing environment/File/Shell routes;
it does not authorize this action to change anything. The exact meaning,
accepted values, source precedence, canonical environment and owner-file keys,
apply timing, sensitivity notes, real change procedure, and second-SHOW
verification live only in the manual section named by `comment`. Legacy
`manifest.cache_miss_budget` remains ignored and is not hydrated.

The owner-local classification explicitly excludes settings assigned to Soul,
Shell, Daemon, Notification, Email, File, Vision, Web, Task Card,
Plugin/Psyche, MCP, and curated-addon ToolPlugins. In particular,
`manifest.pseudo_agent_subscriptions` is Email-owned and is projected only by
Email's owner-local row, which fully redacts both path lists; System does not
project it. Psyche owns the live Pad inputs and the six configurable prompt
inputs in `settings/psyche.json`, so System excludes the Pad pair as
concrete-owner rows and the legacy init prompt spellings as inert compatibility
fields. Daemon's
manager-pool variable is a registered concrete-owner setting; its manager token
and run directory are registered injected/handoff values. The historically
named `LINGTAI_DAEMON_MEMORY_RELIEF` instead controls the global `ToolExecutor`
finally path used by ordinary main-agent and daemon batches, so System exposes
it once as `runtime.tool_batch_memory_relief` using the canonical
`malloc_relief.enabled()` resolver. The classification also excludes `manifest.activeness`,
`manifest.llm.codex_thread_salt`, max-turn/context-
serialization and other retired compatibility inputs, plus build/test-only
environment controls and pure injected identity/handoff descriptors. The
environment partition lives as one owner-local structured authority in
`settings.py`; manuals project it but do not define it. These classifications
do not create a generic registry or mutation service.

The outer Agent lazily exposes one scalar cache-budget hook; kernel code never imports
`lingtai.tools` and resolves that hook once per metadata snapshot for both
telemetry and the soft Context reminder. Threshold changes never reset
since-last-molt counters or block a request.

The complete/redacted/read-only agent-observable promise is guarded by
[B009](BEHAVIORS.md#behavior-b009); source resolution and exclusion mechanics
remain focused pytest contracts.

`settings.py` owns live `LINGTAI_CACHE_MISS_BUDGET` > closed-v1
`settings/system.json` (or the v2 `cache_miss_budget` field) > fixed
`2_000_000`. It only accepts positive integers, falls back safely, and never
writes configuration. The valid-v1 parse is byte-for-byte unchanged and v1 is
never widened. The outer Agent lazily exposes one scalar hook; kernel code never
imports `lingtai.tools` and resolves that hook once per metadata snapshot for
both telemetry and the soft Context reminder. Legacy
`manifest.cache_miss_budget` is ignored and not hydrated. Threshold changes
never reset since-last-molt counters or block a request. The System manual owns
the operator details; focused System, meta, and init tests own proof.

### Runtime-policy setting (v2)

`settings.py::resolve_runtime_policy(working_dir)` resolves the
ordinary fields `context_limit`, `max_rpm`, `streaming`, `aed_timeout`,
`max_aed_attempts`, `snapshot_interval`, and `activeness` as valid
`LINGTAI_<FIELD>` env > valid closed-v2 `settings/system.json` field > effective
fixed default, returning the values with per-field provenance. `init.json` and
its effective manifest are deliberately not resolver inputs: old root keys are
recognized-and-ignored compatibility data and are never validated, hydrated,
or materialized from presets. One invalid v2 key or
value rejects the whole document; absent and explicit `null` are distinct. The
`cli.build_llm_service`/`build_agent`
and `Agent._setup_from_init` (through `Agent.resolve_runtime_policy`) apply the
same resolved policy to the LLM service, `AgentConfig` (via
`build_agent_config(..., runtime_policy=)`), and the `SessionManager.streaming`
setter, so boot and refresh never disagree. Enabling `snapshot_interval` on a
started agent initializes the snapshot port before the new config is published;
on failure snapshots remain off and `snapshot_initialize_failed` is logged. The
v2 `notification_max_chars` field is exposed only through
`Agent.resolve_notification_max_chars()`; Core keeps the env-first read, the
shared 2048/10000 clamp, and the 10,000 default. The kernel-fixed
context-pressure thresholds and legacy `molt_*` fields are never System
settings. `tests/test_system_runtime_policy.py` owns proof.

### Single sleep use case

`karma.py::sleep_use_case` is the sole System semantic owner for self-sleep:
it validates the optional one-shot `delay`, reads attention fingerprints,
applies the pending-notification refusal and explicit `force` escape hatch,
emits the refusal/force/sleep/arm-failure audit events, returns the localized
receipt, orders alarm arming before the transition under the port's
heartbeat-shared lock, and performs the ASLEEP transition through the narrow
`SystemSleepPort` (`karma.py:24-132`). `_DirectSleepPort` is only the
legacy direct-entry translation (`karma.py:135-182`). The mounted route runs
the same use case over the granted `SystemRuntimePort`, whose
`sleep_attention_fingerprints` / `transition_to_asleep` / `sleep_alarm_lock` /
`arm_sleep_alarm` members are translation-only: neither the kernel port nor
`AgentSystemRuntimeAdapter` owns any fingerprint comparison, refusal/force
branch, receipt, or duplicate sleep policy, and no `runtime.sleep(reason,
force)` callback exists. Mounted and direct refusal/force parity
is guarded by [B008](BEHAVIORS.md#behavior-b008) and pinned by
`tests/test_system_declared_plugin.py::test_system_sleep_direct_and_mounted_routes_have_refusal_force_parity`.

## Routing Card

**Use this when:**
- You are editing runtime lifecycle: `refresh`/preset swap, `sleep`, or the
  karma-gated verbs (`lull`/`suspend`/`cpr`/`interrupt`/`clear`/`nirvana`).
- You are editing the agent's name: `name_set` (once, immutable) or
  `name_nickname` (mutable). Neither renames the agent's address or working
  directory — that is the operator migration workflow in `system-manual`.
- You are reviewing preset listing/connectivity or the karma/nirvana authz gate.

**Do not use this for:**
- Notification reads/dismissals: use the `notification` tool
  (`src/lingtai/tools/notification/CONTRACT.md`). `system` exposes no `notification`/
  `dismiss` alias; those actions are rejected as unknown.
- Context lifecycle and hygiene: molt, tool-result summarization, and the
  full prompt reconstruction, summary application, and provider replay all belong to `context`
  (`src/lingtai/tools/context/CONTRACT.md`) — `context(action='molt')`,
  `context(action='summarize')`, `context(action='rebuild')`. `system` owns
  none of them and exposes no `summarize` action or alias; `summarize.py`
  remains here only as the private engine those actions call.
- Code navigation only: read `src/lingtai/tools/system/ANATOMY.md`.

**Fast paths:** action list -> §Tool surface; karma signal files -> §State &
storage; name semantics -> §Anchored claims.

## Scope

- Canonical tool name: `system`.
- `name_set`/`name_nickname` live on `system` (runtime identity state). They
  update live in-memory identity, persist `.agent.json`, and rewrite the
  protected prompt `identity` section. They are NOT raw init/config editing and
  NOT the physical address/workdir rename.
- Non-goals: notification `check`/`dismiss_*`, context molt/summarize/rebuild
  (all on `context`), physical address/workdir rename, mailbox actions.

## Tool surface

Per-action `input` schemas are the data in `src/lingtai/tools/system/schema.py`
(`ACTION_ORDER`, `INPUT_SCHEMAS`); the model-facing family schema is composed
from them by `get_schema()` in `src/lingtai/tools/system/__init__.py`, and
dispatch is the generic `ToolFamily.handle()` that same module delegates to.
Every call takes `action` + `input` + `reasoning`; the "inputs" columns below
name properties of the selected action's own `input` object, never root
siblings.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `refresh` | — | `reason`, `preset`, `revert_preset` | `{status: "ok", message}` | `{status: "error", message}` on preset/revert conflict, unauthorized preset, oversize context, or activation failure |
| `target_refresh` | `address` | `reason` | `{status: "refresh_requested", address, message}` — the request was *submitted* (`<target>/.refresh` written); it is not evidence the target refreshed | `{error: True, message}` if the caller lacks karma, the target is self/non-agent, or the target is not running |
| `sleep` | - | `reason`, `force`, `delay` | `{status: "ok", message}` (self-sleep; refuses with an ok+message when notifications pending and not `force`; a finite positive `delay` arms the last-resort alarm) | `{status: "error", message}` for a non-number, bool, non-finite, zero, or negative `delay` |
| `lull` | `address` | `reason` | `{status: "asleep", address}` | `{error: True, message}` (no karma, no/invalid address, self-target, target not running) |
| `suspend` | `address` | `reason` | `{status: "suspended", address}` | `{error: True, message}` (as above) |
| `cpr` | `address` | `reason` | `{status: "resuscitated", address}` | `{error: True, message}` (target already running, CPR unsupported, or observed child exit before a fresh heartbeat) |
| `interrupt` | `address` | `reason` | `{status: "interrupted", address}` | `{error: True, message}` (as above) |
| `clear` | `address` | `reason` | `{status: "cleared", address, source}` | `{error: True, message}` (as above) |
| `nirvana` | `address` | `reason` | `{status: "nirvana", address}` | `{error: True, message}` (requires karma AND nirvana; shutdown-timeout error) |
| `presets` | — | — | `{status: "ok", active, available: [...]}` | `{status: "error", message}` on unreadable init.json |
| `name_set` | `content` | — | `{status: "ok", name}` | `{error}` when empty or when a true name is already set (immutable) |
| `name_nickname` | `content` | — | `{status: "ok", nickname}` (`null` when cleared) | — (empty `content` clears the nickname) |
| `settings` | — | — | `{"settings":[...]}` in the stable System catch-all order; every row has exactly `key`, `current`, `default`, `configurable`, `comment` | fixed no-row failure for invalid input, unavailable current, malformed provider row, unserializable value, or oversized complete response |

`manual` takes the canonical strict-empty `input` and returns the flat
`{status, manual, manual_path}` shape (plus `error` when the installed manual is
missing). The reserved child is registered unwrapped, so `ToolFamily.handle()`
returns its canonical `content`/`structuredContent` result verbatim and
`_adapt_manual_result` flattens it strictly *after* dispatch — no double wrap.

Unknown/absent `action` returns `{status: "error", message: "Unknown system
action: ..."}`, preserved verbatim from before the migration by normalizing the
generic dispatcher's `ACTION_REQUIRED` envelope failure. An unhashable `action`
(e.g. `[]` from invalid JSON) renders the same stable error rather than raising.
Notification verbs (`check`, `dismiss_channel`, `dismiss_event`, `dismiss_ref`)
and the legacy `notification`/`dismiss` aliases are **not** in the enum and
dispatch to the unknown-action error.

Envelope enforcement is two-layered. The composed schema correlates `action`
with `input` via a root `allOf`/`if`/`then` per action, and dispatch is the
always-authoritative, fail-closed second layer: it validates `action` before
child lookup, type-checks and strips root `summarize`, rejects unknown root
fields, and rejects `input` keys outside the selected action's own declared
schema **before** any handler runs. That last rule is the safety-relevant one
for this family — a cross-action smuggle such as `action='sleep',
input={'address': ...}` fails with no signal file written to any target.
Children receive only their own validated `input`: never `action`, `reasoning`,
`_reasoning`, `summarize`, or `_tc_id`.

Two fields deserve explicit mention because they are not a straight carry-over
of the pre-migration *schema*:

- `sleep.force` was always read by `karma._sleep` (the kernel#112 escape hatch)
  but was never advertised. A strict child `input` must declare every key its
  handler accepts, so it is now declared. This surfaces existing behavior; it
  grants no new capability.
- `sleep.delay` is a required-nullable optional property in the strict branch:
  `null` is stripped before the handler, exactly like the other optional fields.
  A supplied value must be a finite positive JSON number; bool, zero, negative,
  NaN, and infinity are rejected by the handler. There is no maximum. It is a
  last-resort one-shot alarm only for async work lacking reliable producer
  completion notification; ordinary waiting remains IDLE. An early wake does
  not cancel it, a later delayed sleep replaces it, and plain sleep leaves it.
- `notification_threshold_chars` is declared by no action here (nor by any
  `context` action) — the threshold is config-only
  (`manifest.summarize_notification_threshold` + refresh). The private engine's
  loud `runtime_threshold_change_not_supported` refusal is retained as the
  inner layer for direct in-process callers that bypass the envelope.

The karma gate (`_check_karma_gate`) requires `admin.karma=True` for the
six control verbs and `admin.karma AND admin.nirvana` for `nirvana`; it also
rejects a missing address, a self-target, and a non-agent target.

**Behavioral tests**: the agent-observable promises of this clause are guarded
by [`BEHAVIORS.md`](BEHAVIORS.md) — authorization gate
([B001](BEHAVIORS.md#behavior-b001)), signal-file effects
([B002](BEHAVIORS.md#behavior-b002), [B003](BEHAVIORS.md#behavior-b003)),
refusal paths ([B004](BEHAVIORS.md#behavior-b004),
[B005](BEHAVIORS.md#behavior-b005)), nirvana privilege
([B006](BEHAVIORS.md#behavior-b006)), CPR launch confirmation
([B007](BEHAVIORS.md#behavior-b007)), and target refresh submission
([B010](BEHAVIORS.md#behavior-b010)). Change any of these behaviors, update the
matching behavior entry and this clause together.

### Target refresh submission

`target_refresh` reuses the shared karma, address, and live-target gates. It
writes only the empty `<target>/.refresh` marker, logs
`karma_target_refresh(target, reason)`, and returns
`{status: "refresh_requested", address, message}`. The target heartbeat owns
marker consumption and the existing refresh handshake; the caller never writes
`.refresh.taken`, calls the target refresh implementation, or edits target
configuration. The receipt proves submission only, so completion requires a
separate target observation. Guarded by
[B010](BEHAVIORS.md#behavior-b010) and `tests/test_system_target_refresh.py`.

### CPR launch confirmation

After `cpr` spawns its detached child, it waits for a fresh heartbeat for the
shared confirmation interval `max(10 seconds, 2 * HEARTBEAT_LIVENESS_SECONDS)`:
20 seconds at the shared 10-second default and scaled by a valid
`LINGTAI_AGENT_ALIVE_THRESHOLD_SEC` override. At that boundary it makes one final heartbeat
observation and checks the child process: an observed exit (including exit code
zero) returns the existing launch-failure error with its relaunch-log tail; a
still-running child with no fresh heartbeat is logged as `cpr_launch_unconfirmed`
and returns the established `{status: "resuscitated", address}` receipt. The
latter records absence of confirmation evidence, not an ongoing-health guarantee.
This observable distinction is guarded by [B007](BEHAVIORS.md#behavior-b007).

## State & storage

Karma verbs write **signal files** into the *target* agent's working directory;
the target's heartbeat loop picks them up. Paths are relative to the resolved
target `working_dir`:

```text
<target>/.sleep      — written by lull (agent goes ASLEEP; process keeps running)
<target>/.suspend    — written by suspend and by nirvana (process shuts down)
<target>/.interrupt  — written by interrupt (cancels the current turn)
<target>/.clear      — written by clear; its contents become the recovery `source`
```

A self `system.sleep(delay=...)` owns exactly one additional root artifact:
`<workdir>/.alarm`. Its entire UTF-8 content is one parseable absolute
wall-clock deadline. The handler atomically overwrites it only after the
existing pending-notification/force gate passes and before ASLEEP; the
heartbeat holds the same capability-local lock, publishes one ordinary
`system.sleep_alarm` system event at/after the deadline with a stable ref hash
derived from that stored text, and removes the file only after publish succeeds.
A failed publish or consume retains the file for retry; Store ref/idempotency
deduplication suppresses duplicate retries. Malformed/unreadable state stays in
place and emits at most one `sleep_alarm_malformed` event per unchanged problem
per process. No queue, cancellation action, scheduler, timer service, or
configuration is part of this capability.

`nirvana` writes `.suspend`, waits up to ~10s for shutdown, then
`shutil.rmtree`s the whole target directory. `refresh`/preset swaps persist
`manifest.preset.default` into the agent's own `<workdir>/init.json`.
The private summarize engine (`summarize.py`, driven by
`context(action='summarize'|'rebuild')`) mutates live chat history in place
(replacing tool-result block content with a `lingtai_agent_summarized_result`
marker), persists via `_save_chat_history`, and leaves the original payload
traceable in `<workdir>/logs/events.jsonl` by `tool_call_id`. That state is
described here because the engine module lives here; the public actions that
drive it are `context`'s.

## Cross-platform invariants

- Address resolution uses `resolve_address` and all target file access is via
  `pathlib.Path.write_text` / `shutil.rmtree`; no shell-outs. DOCUMENT.
- Karma signal files are empty markers (except `.clear`, whose content is the
  `source` string), read/written UTF-8. DOCUMENT (do not change).
- The private summarize engine does no filesystem or subprocess work of its
  own beyond the history save; it operates on in-memory `ChatInterface` blocks.
  DOCUMENT: no platform-specific behavior; all file access via pathlib.
- No subprocess/PTY in this tool. DOCUMENT.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `system` is a wired intrinsic | `src/lingtai/tools/system/__init__.py` | `tests/test_system.py::test_system_in_all_intrinsics`, `tests/test_system.py::test_system_wired_in_agent` |
| `sleep` applies one pending-attention/refusal/force use case and transitions the agent to ASLEEP (self, no karma) | `src/lingtai/tools/system/karma.py:sleep_use_case` | `tests/test_system.py::test_system_self_sleep`, `tests/test_system_declared_plugin.py::test_system_sleep_direct_and_mounted_routes_have_refusal_force_parity` |
| `sleep(delay)` atomically persists one deadline, and heartbeat publishes/consumes its idempotent ordinary system event | `src/lingtai/tools/system/karma.py:sleep_use_case`, `src/lingtai/kernel/base_agent/lifecycle.py:_fire_sleep_alarm_if_due` | `tests/test_system_sleep_alarm.py` |
| Unknown/legacy actions return the unknown-action error | `src/lingtai/tools/system/__init__.py:handle` | `tests/test_system.py::test_system_rejects_unknown_and_retired_actions` |
| `refresh` with an unauthorized preset is refused | `src/lingtai/tools/system/preset.py:_refresh` | `tests/test_system.py::test_refresh_with_unauthorized_preset_returns_error` |
| `refresh` cannot combine `preset` and `revert_preset` | `src/lingtai/tools/system/preset.py:_refresh` | `tests/test_system.py::test_refresh_revert_preset_with_preset_arg_errors` |
| `presets` lists the allowed library and strips credentials | `src/lingtai/tools/system/preset.py:_presets` | `tests/test_system.py::test_presets_action_lists_full_library`, `tests/test_system.py::test_presets_action_strips_credentials` |
| `cpr` reports an observed launch exit as failure, but retains its established receipt for a child still running after bounded heartbeat confirmation | `src/lingtai/tools/system/karma.py:_cpr` | `tests/test_system.py::test_cpr_propagates_launch_failure_instead_of_resuscitated`, `tests/test_karma.py::TestCPRLingtai` |
| The two name actions preserve live identity, `.agent.json`, and the protected prompt `identity` section, and mutate neither address nor workdir | `src/lingtai/tools/system/name.py` | `tests/test_tool_family_system_migration.py::test_name_actions_preserve_identity_semantics` |
| The public `summarize` action is gone from `system` with no alias | `src/lingtai/tools/system/schema.py:ACTION_ORDER` | `tests/test_tool_family_system_migration.py::test_public_summarize_action_is_gone_and_fails_loudly`, `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss` |
| `items`/`rebuild` belong to no `system` action | `src/lingtai/tools/system/schema.py:INPUT_SCHEMAS` | `tests/test_tool_family_system_migration.py::test_departed_summarize_fields_are_rejected_on_every_action` |
| PRIVATE ENGINE (driven by `context`, not a `system` action): records a `pending` marker and can rebuild the provider context | `src/lingtai/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_summarize_writes_pending_status_marker`, `tests/test_system_summarize.py::test_rebuild_true_with_items_records_marks_done_then_rebuilds` |
| PRIVATE ENGINE: no items with the rebuild discriminator off is an invalid no-op | `src/lingtai/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_missing_items_without_rebuild_is_invalid_no_op` |
| PRIVATE ENGINE: runtime threshold mutation is rejected | `src/lingtai/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_summarize_runtime_threshold_change_rejected` |
| Notification/dismiss actions are dropped from the `system` schema | `src/lingtai/tools/system/schema.py` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss`, `tests/test_notification_tool.py::test_system_rejects_dismiss_action` |
| `target_refresh` is karma-gated, refuses self/non-agent/dead targets with no marker, writes only `<target>/.refresh`, and returns a submission-only receipt; the target heartbeat consumes the marker into `.refresh.taken`/its refresh process | `src/lingtai/tools/system/karma.py:_target_refresh` | `tests/test_system_target_refresh.py` |
| Karma signal files clear a target channel path end-to-end | `src/lingtai/tools/system/karma.py` | `tests/test_system_dismiss.py` |
| The model-facing root is the closed LTP v2 envelope with twelve operational actions followed by reserved `settings`, `manual` | `src/lingtai/tools/system/__init__.py:get_schema` | `tests/test_tool_family_system_migration.py::test_root_envelope_is_exactly_the_four_ltp_v2_fields`, `::test_public_tool_name_and_action_inventory_adds_only_reserved_settings` |
| Each action's arguments live only in its own strict `input` | `src/lingtai/tools/system/schema.py:INPUT_SCHEMAS` | `tests/test_tool_family_system_migration.py::test_action_input_fields_match_what_the_handler_reads` |
| A cross-action smuggle is rejected before any lifecycle I/O | `src/lingtai/tools/tool_family/__init__.py:ToolFamily.handle` | `tests/test_tool_family_system_migration.py::test_cross_action_input_is_rejected_before_any_lifecycle_io` |
| Envelope metadata never reaches a child handler | `src/lingtai/tools/system/__init__.py:_build_children` | `tests/test_tool_family_system_migration.py::test_envelope_metadata_never_reaches_a_child_handler` |
| `manual` is the reserved family-owned child, returned without double wrap | `src/lingtai/tools/system/__init__.py:_adapt_manual_result` | `tests/test_tool_family_system_migration.py::test_manual_child_is_registered_unwrapped`, `::test_manual_returns_the_pinned_flat_public_shape` |
| `settings` returns the complete ordered System catch-all, projects exactly five fields with secrets redacted, and fails the complete inventory when current truth is unavailable | `src/lingtai/tools/system/settings.py:system_settings_provider` | `tests/test_system_declared_plugin.py::test_system_settings_inventory_has_exact_public_contract`, `::test_system_settings_redacts_sensitive_effective_values`, `::test_system_settings_inventory_redacts_owner_io_failures`, `::test_system_budget_invalid_documents_use_default` |
| `system` is on the kernel `summarize` allowlist | `src/lingtai/kernel/tool_result_summary.py:_LTP_V2_MIGRATED_FAMILIES` | `tests/test_tool_family_system_migration.py::test_system_is_on_the_kernel_summarize_allowlist` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Karma gate blocks unauthorized control | `tests/test_system.py::test_refresh_with_unauthorized_preset_returns_error` + karma gate paths | Call `lull` without `admin.karma` | Any agent could sleep/destroy peers |
| `nirvana` requires karma AND nirvana | karma gate in `src/lingtai/tools/system/karma.py:_check_karma_gate` | Call `nirvana` with only karma | Irreversible deletion by under-privileged agent |
| The private engine preserves the original in events.jsonl | `tests/test_system_summarize.py::test_summarize_replaces_block_content` | `context(action='summarize')` a result, grep events.jsonl by tool_call_id | Loss of original tool output |
| The private engine's rebuild path flips pending markers done | `tests/test_system_summarize.py::test_rebuild_true_with_items_records_marks_done_then_rebuilds` | `context(action='summarize')` then `context(action='rebuild')`; inspect marker status | Pending compaction never applied |
| No public `summarize` action survives on `system` | `tests/test_tool_family_system_migration.py::test_public_summarize_action_is_gone_and_fails_loudly` | Call `system(action='summarize')` | A silent second context-hygiene surface |
| A true name stays immutable and neither name action renames the workdir | `tests/test_tool_family_system_migration.py::test_name_actions_preserve_identity_semantics` | `name_set` twice; inspect `.agent.json` + workdir | Identity overwrite or an unintended physical rename |
| No notification verbs on `system` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss` | Call `system(action='check')` | Duplicate notification surfaces diverge |
| System catch-all SHOW remains complete/read-only and each row points to its exact owner procedure | `tests/test_system_declared_plugin.py` settings/manual tests; [B009](BEHAVIORS.md#behavior-b009) | SHOW, perform one authorized external change, then SHOW again | Hidden mutation, secret exposure, omitted setting, or an unverified shadowed change |
| Cross-action input cannot reach a lifecycle handler | `tests/test_tool_family_system_migration.py::test_cross_action_input_is_rejected_before_any_lifecycle_io` | Call `sleep` with an `address` in `input` | A weaker action smuggles a privileged target |
| `nirvana`'s blast radius is exactly one directory | `tests/test_tool_family_system_migration.py::test_nirvana_destroys_only_the_disposable_target` | Inspect siblings after a disposable-target nirvana | Irreversible over-deletion |

Run before merging system changes:

```bash
python -m pytest tests/test_system.py tests/test_system_summarize.py tests/test_system_dismiss.py tests/test_notification_tool.py tests/test_karma.py tests/test_tool_family_system_migration.py tests/test_tool_family_context_migration.py tests/test_intrinsic_manual_actions.py -q
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
