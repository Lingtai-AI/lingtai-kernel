---
related_files:
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/tools/notification/BEHAVIORS.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - src/lingtai/tools/notification/settings.py
  - src/lingtai/tools/notification/manual/SKILL.md
  - src/lingtai/tools/notification/manual/reference/channel-model/SKILL.md
  - src/lingtai/tools/notification/manual/reference/dismissal-safety/SKILL.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - src/lingtai/tools/registry.py
  - src/lingtai/agent.py
  - ENVIRONMENT_VARIABLES.md
  - tests/test_tool_plugin_declaration.py
  - tests/test_notification_settings.py
  - tests/test_notification_tool.py
  - tests/test_notification_delay_alarm.py
  - tests/test_notification_sync.py
  - tests/test_notification_one_shot.py
  - src/lingtai/tools/notification/glossary-en.md
  - src/lingtai/tools/notification/glossary-wen.md
  - src/lingtai/tools/notification/glossary-zh.md
maintenance: |
  Notification is an official declared host-plugin slice. Keep this Anatomy
  reciprocal with CONTRACT.md and BEHAVIORS.md and update the kernel Port,
  production adapter, declaration test, and package-owned manual together when
  its composition changes. Code is structural truth; report mismatches rather
  than normalizing them. Keep related paths repo-relative, existing, and
  duplicate-free.
---
# Notification Tool Anatomy

`src/lingtai/tools/notification/` owns the always-on public `notification`
family. It is an official declared host plugin, not an intrinsic: its static
`DECLARATION` is composed at import without an Agent, then its `setup(agent)`
passes that declaration to the kernel registrar. The public name, eight actions
(six operational actions `check | add | drop | edit | list | delay` plus reserved
`settings` and `manual`), closed LTP v2 envelope, strict per-action schemas, and
operational result shapes are unchanged except that the former
`dismiss_channel`/`dismiss_event`/`dismiss_ref` actions were removed with no
alias (one-shot delivery; producers own their state).

The family owns only model-facing adaptation. It never recreates notification
Store, delivery, producer, hook, or delay state. Its `notification_state` host
port reaches the existing Notification Core operations through a callback-only
production adapter bound to the real agent.

Known aggregate projection and process-local delivered identity live in `kernel/meta_block.py` and `kernel/base_agent/__init__.py`, not this family. Email/daemon/system/known IM use record IDs and material content; generic ID-less hooks use channel versions. Ordinary molt/rebuild/resync retain delivery identity; a new Agent/process restart does not persist it. Focused regression coverage is in `tests/test_notification_one_shot.py`.

## Components

- `schema.py` owns `NOTIFICATION_DECLARED_ACTIONS` and
  `DECLARED_INPUT_SCHEMAS`, the one source for notification's six operational
  action names and strict input schemas. It deliberately excludes `settings`
  and `manual`; `ACTION_ORDER` is the public compatibility view with both
  reserved actions appended last.
- `settings.py::notification_settings` turns the two effective scalar values
  from a zero-argument read callback into ordered `SettingRow` objects. The
  binder extracts that callback from `NotificationStatePort.read_settings()`;
  the provider receives no mutating port. It owns no parser, writer, cache,
  receipt, or configuration state.
- `__init__.py` owns the static `DECLARATION`, declaration-derived public
  `ACTION_ORDER`/`INPUT_SCHEMAS`, the schema-only `_FAMILY`, and `_bind(host)`.
  `_build_family(host)` builds either never-dispatching import-time children or
  a real host-bound `ToolFamily`; both read name, input schemas, manual
  destination, settings opt-in/provider, and action order from `DECLARATION`.
- The Host-layer action adapters perform only presentation rules: check's
  placeholder, nullable-field stripping, hook-manifest argument construction, and post-dispatch manual flattening.
  They pass all stateful work through `host.notification_state`.
- `manual/SKILL.md` and its nested `reference/` tree are the package-owned
  progressive-disclosure manual. Agent initialization copies this tree to
  `.library/intrinsic/capabilities/notification/`; the reserved child reads
  that installed copy through `build_manual_child(host.workdir,
  DECLARATION.manual)`.
- `src/lingtai/kernel/tool_plugin/__init__.py` owns the declaration shape,
  reserved official namespace, `NotificationStatePort`, and registrar.
  `src/lingtai/adapters/tool_plugin_host.py::AgentNotificationStateAdapter`
  implements that port from callbacks bound to the real Core functions.

## Connections

1. `tools.registry` lists `notification` in `BUILTIN_TOOLS` and `CORE_DEFAULTS`.
   It is therefore available by default like the former mandatory intrinsic,
   while absent from `INTRINSICS` so no direct-Agent dispatcher can mount it.
2. `Agent` boots the default capability. `notification.setup(agent)` calls
   `register_agent_tool_plugins(agent, [DECLARATION])`; the kernel checks the
   reserved name, grants exactly `workdir` and `notification_state`, binds the
   family, and mounts the resulting handler through the authorized transaction.
3. `AgentNotificationStateAdapter` holds callbacks only. It binds
   `delay_notification_channel` and the four hook-registry operations to the
   real agent; it exposes no dismissal operation (the private Core
   `dismiss_channel` helper is unreachable from the family). Notification Core
   consequently remains the only implementation of timer/alarm behavior,
   allowlist mirroring, and Store mutations.
4. `check` returns only a dict placeholder. The existing turn-loop
   `attach_active_notifications` hook stamps the complete current mirrors on
   that dict (a deliberate read, not a replay of automatic one-shot delivery);
   the declared family does not build a second snapshot.
5. The manual child is registered directly and returns its canonical
   `content`/`structuredContent` result. `_adapt_manual_result` flattens it only
   after dispatch to the pre-existing `status`/`notification_manual`/
   `manual_path` public shape.
6. Boolean `DECLARATION.settings=True` makes generic ToolFamily composition
   inject the strict-empty SHOW child immediately before `manual`. The bound
   provider reads through the host port; the schema-only family uses a no-I/O
   provider and never reads runtime state.

## State and boundaries

- Notification Core and its injected `NotificationStorePort` own all durable
  files below `.notification/`, including hook manifests, delay state, alarms,
  and producer-facing mirrors. The tool owns no parallel state or cache.
- The declaration has no Agent argument. `ToolPluginHost` exposes only the two
  ports in `DECLARATION.requires`; ungranted ports raise `AttributeError`.
  Mount authority remains host-only.
- `notification_state` is intentionally an operation port, not filesystem or
  Store access. It preserves the real agent-scoped Core behavior instead of
  granting a family the fields from which it could bypass producer policy.
- Producer-specific state remains producer-owned. Delivery never clears notification
  files or producer state; one-shot delivered identity lives in the kernel
  (`meta_block.pending_notification_payloads`), in memory only.
- Settings discovery owns no state and never writes process environment,
  `settings/system.json`, `init.json`, `.notification/`, or any owner file.

## Tests

`tests/test_tool_plugin_declaration.py` is the compact live vertical proof: it
asserts claim/mount, declaration ports, package-manual retrieval, check, and the
closed schema's rejection of the removed dismiss actions.
`tests/test_notification_tool.py` covers the public LTP and action behavior
(including removed-action rejection with no side effects) through a
declaration-bound test host. `tests/test_notification_one_shot.py` pins one-shot
delivery. `tests/test_notification_sync.py` and
`tests/test_notification_delay_alarm.py` exercise the same host-bound dispatch
alongside the real Core synchronization and durable delay/alarm paths.
`tests/test_notification_settings.py` proves exact five-field rows, live
environment and System-v2 precedence, defaults, manual targets, strict empty
input, whole-action failure, no mutation, and unchanged `check` behavior.
