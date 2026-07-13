---
related_files:
  - src/lingtai/tools/task_card/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/task_card/__init__.py
  - src/lingtai/tools/task_card/controller.py
  - src/lingtai/tools/task_card/manual/SKILL.md
  - src/lingtai/agent.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/SKILL.md
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Task Card Tool Anatomy

The concrete built-in tool that owns the public `task_card` surface and the
programmable Task Card watch lifecycle. It is a `lingtai.tools` tool, not kernel
machinery: it drives a workdir-contained Python renderer and projects validated
frames onto the resident Telegram Task Card's programmable slot. Its behavioral
promises live in the paired [`CONTRACT.md`](CONTRACT.md); the model-facing
procedure lives in [`manual/SKILL.md`](manual/SKILL.md).

## Components

- `setup(agent)` — composition-root entry that registers the `task_card` tool
  with `glossary_package=None` (`src/lingtai/tools/task_card/controller.py:563-582`).
- `TaskCardController` — thin Core for programmable watches
  (`src/lingtai/tools/task_card/controller.py:184`): `handle` action dispatch
  (`:193`), `_start` (`:212`), `_inspect` (`:244`), `_stop` (`:255`), watcher
  `_loop`/`_tick` (`:277`/`:288`).
- Fail-loud transitions — `_mark_error` (`controller.py:309`), `_mark_recovered`
  (`:325`), and the deduped, epoch-scoped `_emit_event` wake (`:335`).
- Renderer execution + validation — `_run_renderer`
  (`controller.py:390`, `subprocess.run([sys.executable, path])`), `_validate_frame`
  (`:416`, one JSON object), `_validate_renderer_path` symlink confinement (`:488`).
- Reverse projection — `_project` (`controller.py:463`) forwards validated data to
  the private `_lingtai_telegram_task_card` tool with `channel="programmable"`.
- `_Watch` in-memory watch state (`controller.py:139`);
  `shutdown_for_agent_stop` joins watcher threads (`:552`).
- `__init__.py` re-exports `setup`/`TaskCardController`/`TaskCardControllerError`/
  `get_schema`/`get_description` (`src/lingtai/tools/task_card/__init__.py:20-34`).

## Connections

- **← composition root** — `Agent._maybe_setup_task_card_controller`
  (`src/lingtai/agent.py:964-982`) lazily imports `lingtai.tools.task_card.setup`
  and registers the tool once a Telegram MCP client exists, idempotently from both
  `connect_mcp` (`agent.py:961`) and `connect_mcp_http` (`agent.py:1039`).
- **← kernel lifecycle** — `base_agent/lifecycle.py:_stop` joins the watcher
  threads via `shutdown_for_agent_stop` (`src/lingtai/kernel/base_agent/lifecycle.py:269-277`).
- **→ Telegram reverse channel (production adapters)** — `_project` reverse-calls
  `agent._mcp_clients_by_tool["telegram"]` with the unlisted `_TASK_CARD_TOOL`
  name. The server forces `_task_card_update` server-side
  (`src/lingtai/mcp_servers/telegram/server.py:727-734`) and the manager routes
  and composes the automatic + programmable slots into one resident message
  (`src/lingtai/mcp_servers/telegram/manager.py:1500` dispatch → `:1677`
  `_task_card_programmable` → `:1401` `_compose_channels`). These transport /
  composition adapters are mapped by
  [`src/lingtai/mcp_servers/ANATOMY.md`](../../mcp_servers/ANATOMY.md); the
  reverse-tool-forcing invariant lives in
  [`src/lingtai/tools/mcp/ANATOMY.md`](../mcp/ANATOMY.md). Model-facing routing is
  taught by this tool's [`manual/SKILL.md`](manual/SKILL.md) and, for Telegram
  users, [`src/lingtai/mcp_servers/telegram/SKILL.md`](../../mcp_servers/telegram/SKILL.md).
- **→ kernel wake** — `_emit_event` calls `agent._enqueue_system_notification`
  for `task_card.error`/recovery wakes. It reads the automatic driver's
  `agent._telegram_task_card_context` for the route and `agent._shutdown` to end
  loops. All are read off the injected agent; the kernel never imports this package.

## Composition

- **Parent:** `src/lingtai/tools/` (see [`ANATOMY.md`](../ANATOMY.md)).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns the behavioral promises.
- **Manual:** [`manual/SKILL.md`](manual/SKILL.md), linked from both owner twins.
- Registered by the outer `Agent` composition root, not by
  `lingtai.tools.registry` — it is not a `BUILTIN_TOOLS` capability.

## State

The controller keeps only in-memory `_Watch` state per watch (last valid frame,
error/episode counters, the daemon watcher thread and its stop event). It writes
no persistent files. Renderer files are author-owned under the agent working
directory and are never created or deleted by this tool; `stop`/shutdown clear the
programmable slot without touching the filesystem.

## Notes

- Telegram-gated: no Telegram MCP client means no `task_card` tool. The tool is
  agent-only and English-only, so it carries no localized glossary (root design
  principles — no i18n on an agent-only surface).
- The `_lingtai_telegram_task_card` literal is duplicated in three places on
  purpose (`controller.py`, `base_agent/__init__.py`, `telegram/server.py`) because
  neither the kernel nor this tool package may import `lingtai.mcp_servers`; keep
  the three in sync.
- The controller types its injected agent against a narrow local `_TaskCardAgent`
  `Protocol` (`controller.py`, TYPE_CHECKING-only), so the concrete tool never
  type-depends on the outer `Agent` composition root. `BaseAgent` cannot stand in
  because the consumed `_mcp_clients_by_tool` map is wrapper-owned, not a kernel
  attribute.
