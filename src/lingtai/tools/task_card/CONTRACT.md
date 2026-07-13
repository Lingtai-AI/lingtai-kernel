---
name: task-card-tool
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/task_card/ANATOMY.md
  - src/lingtai/tools/task_card/__init__.py
  - src/lingtai/tools/task_card/controller.py
  - src/lingtai/tools/task_card/manual/SKILL.md
  - src/lingtai/agent.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - tests/test_task_card_controller.py
  - tests/test_telegram_task_card_transport.py
  - tests/test_telegram_task_card_programmable.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Task Card Tool

## Purpose

`task_card` is the public, model-facing tool that drives the **programmable
slot** of the single resident Telegram Task Card (Jason #7258/#7259). It is a
concrete built-in tool owned by `lingtai.tools`, not kernel machinery: the kernel
owns the tool executor/guard/dispatch and the private Telegram reverse channel
plus the automatic Task Card driver; this component owns the programmable
controller and its watch lifecycle. An agent supplies a Python renderer file
under its own working directory; the controller runs that renderer locally with
the runtime interpreter, validates its output, and projects only validated data
onto the resident card. The capability procedure (start/inspect/retry/stop, the
renderer protocol, and the safe-renderer example) is taught by the co-located
[`manual/SKILL.md`](manual/SKILL.md); this contract owns the behavioral promise.

## Behavior

Agents and coding agents MUST preserve these observable semantics:

- The public tool name is `task_card` with exactly the actions `start`,
  `inspect`, `retry`, `stop`. It is agent-only and English-only; per the root
  design principles it carries no localized glossary and takes no i18n
  obligation (`glossary_package=None`).
- The tool is registered **only when a Telegram MCP client exists**, by the
  composition root; no Telegram means no `task_card` tool. Registration is
  idempotent across the stdio and HTTP MCP connection paths.
- `start`'s first renderer run is **synchronous and fail-loud**: a nonzero exit,
  timeout, non-object or multiple-object stdout, or an invalid field type is an
  immediate tool error that creates **no** watch. A synchronous first-frame
  reverse-call rejection likewise discards the unstarted watch and returns an
  error rather than a bogus handle.
- Each valid renderer run emits **exactly one** Task Card JSON object (`title`
  string, `lines` array of strings ≤ 20, `footer` string; at least one present).
  The renderer path is confined to the agent working directory after symlink
  resolution.
- After start, a watcher re-runs the renderer at `interval_s` and projects each
  valid frame onto the private `_lingtai_telegram_task_card` reverse channel with
  `channel="programmable"`. On failure it **preserves the last valid frame**,
  raises one deduped `task_card.error` LICC wake per distinct error code within a
  failure episode, and emits one recovery wake after the next valid frame. Wakes
  carry a stable code/message and safe watch metadata only — never raw renderer
  output or secrets. The same error code re-fires a fresh durable wake after a
  recovery (per-episode idempotency).
- Reverse-channel transport success is the committed channel-state boundary: a
  frame is only treated as delivered when the reverse call returns non-error.
- Agent lifecycle shutdown stops and joins all watcher threads without deleting
  renderer files.

Agents that observe drift MUST surface it against this contract rather than
weaken the promise. Procedures live in the manual and skill, not here.

## Port

The inbound Port is the `task_card` tool surface (`get_schema` /
`get_description` / `TaskCardController.handle`): the agent drives the
programmable-slot use case through the four actions. `TaskCardController` is the
thin Core that owns that use case — validating the renderer path, running the
renderer, reconciling frame/error state, and deciding when to wake.

The controller depends on two seams it does **not** own, both kernel/Telegram
owned and reached only through the injected agent:

- the private, unlisted Telegram reverse-channel MCP tool
  `_lingtai_telegram_task_card` (server-forced action `_task_card_update`,
  `channel="programmable"`), resolved from `agent._mcp_clients_by_tool["telegram"]`;
- the LICC wake `agent._enqueue_system_notification` for fail-loud
  `task_card.error` / recovery notifications.

The controller also reads the automatic driver's turn-local
`agent._telegram_task_card_context` to share one resident message, and
`agent._shutdown` to end watcher loops promptly. The tool surface names no
Telegram, subprocess, or filesystem vocabulary beyond `renderer_path`; the
concrete mechanisms live in the Adapters below.

## Adapters

- **Renderer executor** — `TaskCardController._run_renderer` adapts the local
  Python runtime and filesystem: `subprocess.run([sys.executable, <path>])` under
  a per-run timeout with the agent working directory as `cwd`, then
  `_validate_frame` enforces the one-JSON-object schema. Raw renderer output is
  never echoed into raised errors or wakes (redaction by construction).
- **Telegram transport** — `TaskCardController._project` adapts the Telegram
  channel by reverse-calling the private tool through the MCP client. The Telegram
  MCP server forces the private action server-side
  (`src/lingtai/mcp_servers/telegram/server.py:66-67` literals,
  `build_server._call_tool` branch at `:727-734`), and
  `src/lingtai/mcp_servers/telegram/manager.py` owns composition/persistence:
  `_handle_task_card_update` (`:1500`) routes `channel="programmable"` to
  `_task_card_programmable` (`:1677`), which validates the card object and renders
  it via `_format_programmable_card_text` (`:1472`), then `_deliver_channel_frame`
  (`:1434`) / `_compose_channels` (`:1401`) merge the automatic + programmable
  slots into one resident message. The manager remains the single
  render/compose/persistence owner; the controller sends validated data only.
  Structure of these adapters is mapped by
  [`src/lingtai/mcp_servers/ANATOMY.md`](../../mcp_servers/ANATOMY.md) and the
  reverse-tool-forcing invariant by
  [`src/lingtai/tools/mcp/ANATOMY.md`](../mcp/ANATOMY.md).
- **Composition root** — `Agent._maybe_setup_task_card_controller`
  (`src/lingtai/agent.py`) constructs and registers the controller once a
  Telegram MCP client is present, idempotently from both `connect_mcp` and
  `connect_mcp_http`. Kernel lifecycle (`base_agent/lifecycle.py:_stop`) joins the
  watcher threads via `shutdown_for_agent_stop`. The kernel never imports this
  package; only the outer `Agent` composition root does (lazily).

## Contract rules

1. `start` runs the renderer once synchronously; renderer/JSON/schema failure or
   a first-frame reverse-call rejection is a tool error and no watch survives.
2. A valid renderer produces exactly one Task Card JSON object; extra output,
   multiple values, non-objects, or bad field types are rejected.
3. `renderer_path` resolves relative to the agent working directory and is
   rejected if it escapes that directory after symlink resolution (`..`, absolute
   escape, or external symlink).
4. `interval_s` has a floor of 1s (default 5s) and `timeout_s` a floor of 0.1s
   (default 10s); non-numeric or `bool` values are rejected.
5. After start, failures preserve the last valid frame, emit one deduped
   `task_card.error` wake per distinct code within an episode, and emit one
   recovery wake after the next valid frame; the same code after recovery re-fires
   a fresh durable wake (epoch-scoped idempotency key).
6. Wakes and error messages never contain raw renderer output or secrets.
7. `stop` clears only the programmable slot (a `finalize` reverse call with no
   card) and forgets the watch; renderer files are never deleted. A second `stop`
   on a forgotten watch is a clean error, not a crash.
8. Watcher threads observe `agent._shutdown` and the per-watch stop event; the
   join budget exceeds the reverse-call timeout so a join is truthful.
9. Registration is Telegram-gated and idempotent; the tool is not a
   `registry.BUILTIN_TOOLS` capability.

## Contract tests

`tests/test_task_card_controller.py` pins the behavior: `setup` registers a
`task_card` tool with `glossary_package=None` and the four-action enum;
`Agent._maybe_setup_task_card_controller` registers exactly once and only with a
`telegram` reverse channel (idempotent); `start` projects the first frame
synchronously (`sub_action="create"`, `channel="programmable"`, no `action`
key), returns a `watch_id`, and stores the last valid frame; the six
synchronous-failure renderers (multi-object, non-object array, bad `lines`, empty
stdout, nonzero exit, timeout) and paths outside the workdir create no watch; a
first-frame backend rejection discards the watch; a missing route errors; repeated
identical `_tick` failures emit exactly one deduped `task_card.error` wake
(`priority="high"`, `skip_if_idempotency_key_exists=True`) with a recovery wake on
the next valid frame; the same code after recovery re-fires a fresh per-episode
wake; the join budget exceeds the reverse-call timeout; `retry` reruns now; and
`stop` finalizes with no `card` and forgets the watch. Telegram-side composition
is covered by the `tests/test_telegram_task_card_*` modules (notably
`test_telegram_task_card_programmable.py` and `test_telegram_task_card_transport.py`).

Run before merging changes to this component:

```bash
python -m pytest tests/test_task_card_controller.py \
  tests/test_telegram_task_card_programmable.py \
  tests/test_telegram_task_card_transport.py -q
```

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes update
this contract and `tests/test_task_card_controller.py` together; structural or
composition changes also update the paired
[`ANATOMY.md`](ANATOMY.md) and the reciprocal parent
[`src/lingtai/tools/ANATOMY.md`](../ANATOMY.md). The
[`manual/SKILL.md`](manual/SKILL.md) is linked from both owner twins; keep it and
this contract synchronized when the tool surface changes.
