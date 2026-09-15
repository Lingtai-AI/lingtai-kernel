---
name: declared-host-tool-plugin-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/__init__.py
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/mcp/__init__.py
  - src/lingtai/tools/mcp/settings.py
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/context/__init__.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/plugin/__init__.py
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/bash/_tool_family.py
  - src/lingtai/tools/system/__init__.py
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/tools/task_card/__init__.py
  - src/lingtai/tools/task_card/manual/SKILL.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/tools/web_search/__init__.py
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/kernel/notifications.py
  - tests/test_tool_plugin_declaration.py
  - tests/test_deep_refresh.py
  - tests/test_tool_family_avatar_migration.py
  - tests/test_context_declared_tool_plugin.py
  - tests/test_daemon.py
  - tests/test_email_official_tool_plugin.py
  - tests/test_plugin_tool.py
  - tests/test_notification_settings.py
  - tests/test_notification_delay_alarm.py
  - tests/test_notification_store.py
  - tests/test_shell_tool_plugin_declaration.py
  - tests/test_system_declared_plugin.py
  - tests/test_task_card_controller.py
  - tests/test_task_card_notifications.py
  - tests/test_tool_family_vision_migration.py
  - tests/test_web_official_plugin.py
  - tests/test_web_composition_port.py
  - tests/test_intrinsic_manual_actions.py
maintenance: |
  Created with the declared host-plugin primitive. Keep this file reciprocal
  with CONTRACT.md and ANATOMY.md (tridirectional loop): when a behavior clause
  of the declared host-plugin contract changes — the static-declaration rule,
  the least-privilege grant, the reserved official name list, or the
  check-before-bind ordering — update the guarding LABT here in the same change.
  Keep every command copy-paste executable from the repository root. When a
  further family recuts onto the contract, or when authoring-time line numbers
  drift, extend the affected evidence with that family's own focused proof rather
  than leaving a stale pass. The shared C register is family-generic and distinguishes
  target reserved names from candidate merge evidence. `mcp` is the shared-C base
  reference; Avatar, Context, Daemon, Email, Plugin, Notification, Shell,
  System, Task Card, Vision, and Web are current
  vertical evidence. Ports remain least-privilege and tool-specific, while registrar
  mounts are runtime-bound rather
  than per-call Agent dispatch.
---
# Declared Host Tool Plugin Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/tool_plugin/CONTRACT.md`. Run every command from the
repository root (`git rev-parse --show-toplevel`) with the repository virtual
environment (`uv venv --python 3.11 && uv pip install -e . pytest`, per
`AGENTS.md`). No network, no agent runtime, and no MCP server is needed.

## Behavior TP001 — an official declaration is static, least-privilege, and never receives the Agent

- **id**: TP001
- **title**: an official declaration is static, least-privilege, and never receives the Agent
- **guards**: `declared-host-tool-plugin` §
  [Purpose](CONTRACT.md#purpose), § Behavior, § Port
- **runner**: any LingTai agent with `shell` access to a clean
  checkout of the `lingtai-kernel` repository
- **prerequisites**: a clean checkout; a working `.venv/`
- **estimate**: ≈ 15 minutes

### Steps

1. Prove the declaration exists and validates before any Agent does:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.tools.mcp import DECLARATION as mcp; from lingtai.tools.bash._tool_family import DECLARATION as shell; print(mcp.name, mcp.actions, mcp.public_actions, mcp.requires); print(shell.name, shell.actions, shell.public_actions, shell.requires)"
   ```

   Expect `mcp ('info',) ('info', 'settings', 'manual') ('workdir', 'prompt_section')` and
   `shell ('run', 'poll', 'cancel') ('run', 'poll', 'cancel', 'settings',
   'manual') ('workdir', 'notifications', 'configuration')`. No `Agent` was
   constructed; reserved generic `settings` and `manual` actions are
   injected/appended, not declared as operations. There is no `file`
   declaration: `import lingtai.tools.file` must raise `ModuleNotFoundError`.

   Then prove the twelfth slice's declaration the same way:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.tools.task_card import DECLARATION as tc; print(tc.name, tc.actions, tc.public_actions, tc.requires)"
   ```

   Expect `task_card ('start', 'inspect', 'retry', 'stop', 'remove') ('start',
   'inspect', 'retry', 'stop', 'remove', 'manual') ('workdir', 'shutdown',
   'task_card_lifecycle', 'task_card_notifications')`. No `Agent` was
   constructed; the static declaration appends rather than declares the
   reserved action.

   Then prove the thirteenth slice's declaration the same way:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.tools.vision import DECLARATION as v; print(v.name, v.actions, v.public_actions, v.requires)"
   ```

   Expect `vision ('analyze', 'check', 'list') ('analyze', 'check', 'list',
   'manual') ('workdir', 'active_provider', 'configuration')`. No `Agent` was
   constructed; the static declaration appends rather than declares the
   reserved action.

   Then prove the fourteenth slice's declaration the same way:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.tools.web_search import DECLARATION as w; print(w.name, w.actions, w.public_actions, w.requires)"
   ```

   Expect `web ('search', 'browse') ('search', 'browse', 'settings', 'manual') ('workdir',
   'web_runtime', 'provider_identity')`. No `Agent` was constructed; the
   static declaration opts into generic `settings`, appends both reserved actions, and
   its typed `web_runtime` composition is not in existence yet — only
   `setup` composes and grants it.

2. Prove the public model-facing surface contains the bounded owner addition:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.tools.mcp import get_schema; s = get_schema(); print(sorted(s['properties']), s['properties']['action']['enum'], s['additionalProperties'])"
   ```

   Expect `['action', 'input', 'reasoning', 'summarize'] ['info', 'settings', 'manual']
   False`.

3. Prove a declaration is granted exactly its `requires` and nothing more:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.kernel.tool_plugin import GRANTABLE_HOST_PORTS, ToolPluginHost; from lingtai.tools.bash._tool_family import DECLARATION; h = ToolPluginHost.grant(DECLARATION, {'workdir': object(), 'notifications': object(), 'configuration': object(), 'prompt_section': object()}); print(GRANTABLE_HOST_PORTS, h.granted); h.prompt_section"
   ```

   Expect `('workdir', 'prompt_section', 'avatar_parent', 'context_runtime',
   'daemon_runtime', 'email_runtime', 'plugin_catalog', 'psyche_settings',
   'notification_state', 'notifications', 'configuration',
   'system_runtime', 'identity', 'shutdown', 'task_card_lifecycle',
   'task_card_notifications', 'active_provider', 'web_runtime',
   'provider_identity') ('workdir', 'notifications', 'configuration')` printed,
   then an `AttributeError` whose message says the plugin *did not require host
   port* `'prompt_section'`. Confirm `tool_mount` and `file_io` are both absent
   from `GRANTABLE_HOST_PORTS`: Shell receives exactly `WorkdirPort`,
   `NotificationPort`, and its immutable setup-selected `ConfigurationPort`,
   even when the host table contains another grantable port. The same rule
   covers the ports `agent_host_ports` always builds — `avatar_parent` and
   `plugin_catalog` — so run:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_tool_plugin_declaration.py::test_standard_port_table_grants_each_declaration_only_its_requires
   ```

   Expect `1 passed`: Plugin reaches exactly
   `('workdir', 'prompt_section', 'plugin_catalog')` while MCP reaches neither
   `plugin_catalog` nor `avatar_parent`.

   Task Card's three ports are built in the standard table only for its own
   declaration, and its notification port is closed to five operations:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "import sys; sys.path.insert(0, 'src'); from lingtai.kernel.tool_plugin import ToolPluginHost, TaskCardNotificationsPort; from lingtai.tools.task_card import DECLARATION; h = ToolPluginHost.grant(DECLARATION, {'workdir': object(), 'shutdown': object(), 'task_card_lifecycle': object(), 'task_card_notifications': object(), 'prompt_section': object()}); print(h.granted); print(sorted(n for n in dir(TaskCardNotificationsPort) if not n.startswith('_'))); h.prompt_section"
   ```

   Expect `('workdir', 'shutdown', 'task_card_lifecycle',
   'task_card_notifications')`, then `['clear_reminder', 'publish_error',
   'publish_limit', 'publish_recovered', 'submit_reminder']`, then an
   `AttributeError` whose message says the plugin *did not require host port*
   `'prompt_section'`. No `enqueue`, `source`, `channel`, or `**kwargs` name
   appears on the port.

   Web's typed composition is never in the standard table, and its bind fails
   closed without it:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_web_composition_port.py tests/test_web_official_plugin.py
   ```

   Expect all passed: a bare standard grant for `web` raises `HostPortError`
   because `web_runtime` is absent, `_bind` refuses a missing, legacy-carrier,
   or mistyped `web_runtime` with `HostPortError`, the standard table's
   `provider_identity` exposes only a `provider` label, and one mount serves
   the installed `capabilities/web/SKILL.md` manual.

4. Prove the family reaches the live Agent body only through granted ports:

   ```bash
   grep -n "def _reconcile\|def _build_family\|def _bind" src/lingtai/tools/mcp/__init__.py
   grep -n "working_dir = host.workdir.path" src/lingtai/tools/mcp/__init__.py
   grep -n "host.prompt_section.write_protected_section(xml)" src/lingtai/tools/mcp/__init__.py
   grep -n "host.workdir\|host.notifications\|host.configuration" src/lingtai/tools/bash/_tool_family.py
   grep -n "class AgentNotificationAdapter\|class StaticConfigurationAdapter\|extra_ports_for" src/lingtai/adapters/tool_plugin_host.py src/lingtai/tools/bash/__init__.py
   ```

   Expect MCP's three internals to take `host`, then exactly one match each for
   its two port calls. Expect Shell's `_bind` to reach the body only through
   `host.workdir`, `host.notifications`, and `host.configuration`, and its
   `setup` to supply `StaticConfigurationAdapter` only through
   `extra_ports_for`. Read those two adapters and confirm neither has an
   `Any`-typed escape hatch, `__getattr__`, generic dispatch, Agent slot, or
   mount method. Each `setup(agent, **_ignored)` still takes the Agent because
   it *is* the composition wiring; no binder or bound family does.

5. Prove the kernel still owns only the shape — no import of any tool package
   anywhere under `src/lingtai/kernel/`:

   ```bash
   grep -rnE "^[[:space:]]*(from|import)[[:space:]]+.*lingtai\.tools" src/lingtai/kernel/
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_kernel_isolation.py::test_kernel_has_no_lingtai_submodules
   ```

   Expect no grep output (exit status 1), then `1 passed` from the AST-based
   kernel isolation sweep, which permits kernel-relative imports while refusing
   imports of the outer `lingtai` or `tools` packages.

6. Prove the declared surface and the shipped surface are the same surface,
   not two literals that happen to match:

   ```bash
   grep -n "DECLARATION.manual\|DECLARATION.name\|DECLARATION.input_schemas\|DECLARATION.manual_input_schema" src/lingtai/tools/mcp/__init__.py src/lingtai/tools/bash/_tool_family.py
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_tool_plugin_declaration.py::test_official_mcp_mount_uses_controlled_host_and_real_dispatch \
     tests/test_shell_tool_plugin_declaration.py::test_shell_declaration_is_static_and_derives_its_shipped_surface \
     tests/test_shell_tool_plugin_declaration.py::test_shell_bind_uses_only_its_narrow_ports_and_defers_rehydration \
     tests/test_shell_tool_plugin_declaration.py::test_official_shell_mount_uses_only_narrow_ports_and_keeps_real_dispatch
   ```

   Expect both families' builders/binds to read name, per-action schemas, and
   installed-manual destination back out of `DECLARATION`, then `4 passed`.

7. Run the contract suite:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_tool_plugin_declaration.py tests/test_tool_family_avatar_migration.py \
     tests/test_context_declared_tool_plugin.py tests/test_daemon.py \
     tests/test_email_official_tool_plugin.py \
     tests/test_shell_tool_plugin_declaration.py tests/test_plugin_tool.py \
     tests/test_notification_delay_alarm.py tests/test_notification_store.py \
     tests/test_task_card_controller.py tests/test_task_card_notifications.py \
     tests/test_tool_family_vision_migration.py tests/test_intrinsic_manual_actions.py \
     tests/test_web_official_plugin.py tests/test_web_composition_port.py \
     tests/test_web_canonical_provider_routing.py tests/test_unified_web_capability.py
   ```

   Task Card's focused pair proves one retained manager bound only to its four
   ports, exact error/recovered/limit wire parity through the production
   `AgentTaskCardNotificationsAdapter`, reminder submit/clear parity, the
   five-operation port surface, and foreign source/channel/field refusal.
   Vision's focused suite proves its exact three-port grant, the four-action
   schema/dispatch surface, allowed-preset own-credential routing with no
   automatic fallback, `check`/`list`/`manual` no-request boundaries
   (VN001–VN006), and — through the strict controlled official host in
   `tests/test_intrinsic_manual_actions.py` — that its `manual` returns the
   installed `capabilities/vision/SKILL.md` body and path after a real
   registrar claim/mount.
   Web's focused suites prove its exact three-port grant, the
   `search | browse | settings | manual` surface, the fail-closed typed `web_runtime`
   bind, the standard-table `provider_identity` label, exact-match canonical
   provider gating for the explicit Anthropic/Gemini opt-in, and — through the
   same strict controlled official host in
   `tests/test_intrinsic_manual_actions.py` — that its `manual` returns the
   installed `capabilities/web/SKILL.md` body and path after a real registrar
   claim/mount.

### Expected evidence

- [ ] Step 1: MCP and Shell `DECLARATION`s import and validate with no Agent;
      reserved children are in `public_actions` but not in `actions`; no
      `file` declaration or module exists.
- [ ] Step 2: the closed LTP root and MCP's
      `["info", "settings", "manual"]` enum are exact.
- [ ] Step 3: only `requires` ports are granted; an ungranted port raises
      `AttributeError`; `tool_mount` and `file_io` are not grantable at all; a
      standard-table port such as `plugin_catalog` is unreachable for a
      declaration that did not name it.
- [ ] Step 4: MCP's workdir/prompt operations and Shell's
      workdir/notification/configuration reads go only through granted ports;
      `AgentNotificationAdapter` and `StaticConfigurationAdapter` have no Any,
      generic dispatch, whole Agent, or mount surface; each `setup` is wiring only.
- [ ] Step 5: no file under `src/lingtai/kernel/` imports `lingtai.tools`, by
      grep and by the AST sweep.
- [ ] Step 6: MCP and Shell derive name, `input` schemas, and manual destination
      from their declarations; the four named live/static tests pass.
- [ ] Step 7: the shared suite plus Avatar's, Context's, Daemon's, Email's,
      Shell's, and Plugin's focused declared slices pass. Plugin's slice includes
      the detached per-read catalog projection: mutating a returned registration
      mapping leaves the next read unchanged.
- [ ] Step 6: the family derives its name, `input` schemas, and manual
      destination from its declaration, and `bind()` refuses a plugin
      advertising anything other than `public_actions`.
- [ ] Notification: its static `DECLARATION` requires exactly `workdir` and
      `notification_state`; the granted host exposes neither Agent nor Store;
      one official schema and handler remain on construction and refresh under
      both null-capability and `disable` opt-outs; its package owns the canonical
      installed manual; `check` retains its deliberate placeholder; and a real
      Core-backed `dismiss_channel` returns the established success shape and
      clears the live mirror.
- [ ] Step 7: the shared suite plus Avatar's, Context's, Daemon's, Email's, and
      Notification's focused declared/Core slices pass.
- [ ] Task Card: its static `DECLARATION` requires exactly `workdir`,
      `shutdown`, `task_card_lifecycle`, and `task_card_notifications`; the
      granted notification port exposes exactly five closed operations and no
      generic publisher; one `TaskCardManager` is retained on the Agent and
      rebound across refresh; and the family suites' wire assertions pass
      through the production adapter.
- [ ] Vision: its static `DECLARATION` requires exactly `workdir`,
      `active_provider`, and `configuration`; `active_provider` is built in
      the standard table only for `vision` and reads the live `Agent.service`;
      the `configuration` snapshot reaches only the `vision` declaration
      through `extra_ports_for`; and its manual/preset/no-fallback suites pass.
- [ ] Web: its static `DECLARATION` requires exactly `workdir`,
      `web_runtime`, and `provider_identity`; `provider_identity` is built in
      the standard table only for `web` and exposes only the live
      `Agent.service.provider` label; the typed `WebComposition` reaches only
      the `web` declaration through `extra_ports_for` from `setup`, and
      `_bind` fails closed with `HostPortError` on a missing, legacy-carrier,
      or mistyped grant; and its manual/provider-gate/spill suites pass.

### Pass / Fail

Pass when every box above is observed. **Fail loudly** if a declaration needs a
live Agent to construct, if an official family's public surface changed, if an
ungranted port is reachable, if `tool_mount` becomes grantable, if any code path hands a
whole `Agent` to a plugin, if Shell's adapters use `Any`, generic dispatch, or a
mount operation, if a family restates its name, its per-action input schemas, or
its manual destination instead of deriving them from its own declaration, if a
`file` declaration or `file_io` grant reappears, or if the kernel package
imports `lingtai.tools`.
Record the exact command output in the task report. This task performs no
writes.

## Behavior TP002 — a reserved official name is claimed once and a conflict is refused before any bind or mount

- **id**: TP002
- **title**: a reserved official name is claimed once and a conflict is refused before any bind or mount
- **guards**: `declared-host-tool-plugin` § [Contract rules](CONTRACT.md#contract-rules)
- **runner**: any LingTai agent with `shell` access to a clean
  checkout of the `lingtai-kernel` repository
- **prerequisites**: a clean checkout; a working `.venv/`
- **estimate**: ≈ 15 minutes

### Steps

1. Read `register_official_tool_plugins` in
   `src/lingtai/kernel/tool_plugin/__init__.py`. Confirm its whole-batch name
   preflight completes before the registration loop can grant a host, bind,
   activate, mount, or claim a declaration.

2. Run the direct preflight regression:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_tool_plugin_declaration.py::test_registrar_refuses_unreserved_duplicate_and_claimed_names_before_any_bind_or_mount
   ```

   Expect the selected test to pass. It refuses an unreserved name, an in-batch
   duplicate, and a different declaration against a live claim while preserving
   the call record, mounted transactions, and claim map.

3. Run the direct ordering, issuance, idempotency, and limited-atomicity
   regression:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_tool_plugin_declaration.py::test_registrar_binds_then_activates_then_mounts_and_scopes_atomicity_to_names
   ```

   Expect the selected test to pass. `bind()` alone is side-effect-free; a
   successful member runs bind → activate → mount → claim; the same declaration
   object can be registered again through that controlled route; and the
   registrar alone issues the transaction carrying its exact bound result. A
   later host-port failure propagates after retaining the earlier mount and
   claim, so atomicity applies only to name checks.

4. Run the refresh-owner regression for the opt-in Web surface:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
     tests/test_deep_refresh.py::test_deep_refresh_drops_and_reclaims_web_official_surface
   ```

   Expect the selected test to pass. Removing Web from `init.json` removes its
   claim, schema, and handler; re-adding the capability restores the static
   declaration with one schema and handler.

### Expected evidence

- [ ] The registrar preflights every name before bind, activation, mount, or
      claim and leaves a refused batch unchanged.
- [ ] Registration uses bind → activate → mount → claim; same-object
      re-registration remains controlled; and only the registrar can issue an
      official mount transaction.
- [ ] A failure after the name checks does not imply rollback of an earlier
      member; name checks are the only atomic phase.
- [ ] The refresh owner observes the opt-in Web claim, schema, and handler
      disappear together and return together after re-add.

### Pass / Fail

Pass when every box above is observed. **Fail loudly** if a name conflict is
noticed after any bind, activation, or mount; a second declaration replaces a
live official claim; a caller can manufacture an official mount transaction; a
post-name-check failure rolls back or conceals an earlier member; or a refresh
leaves a Web claim, schema, or handler out of sync with its opt-in capability.
Record the exact command output in the task report. This task performs no
writes.
