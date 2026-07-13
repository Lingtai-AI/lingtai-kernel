---
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/__init__.py
  - src/lingtai/mcp_servers/telegram/task_card/interface.py
  - src/lingtai/mcp_servers/telegram/task_card/controller.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/agent.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Telegram Programmable Task Card Anatomy

The Telegram-owned unit that drives the *programmable* slot of Telegram's one
tracked resident Task Card target. The model-facing `task_card` tool runs an
agent-supplied Python renderer and projects its validated output onto the
Telegram-owned reverse channel; `TelegramManager` remains the single
render/compose/persistence/transport owner (including the hard-at-most-one /
last-message resident transport) and this unit only normalizes those outcomes.
Normative promises live in the paired [`CONTRACT.md`](CONTRACT.md).

## Components

- `get_schema` / `get_description` — the `task_card` tool schema (`start` /
  `inspect` / `retry` / `stop`) and the description that routes to the manual
  (`controller.py:59`, `controller.py:100`).
- `TaskCardController` — thin Core: dispatch, synchronous first frame, watch
  registry, fail-loud/recovery wakes (`controller.py:179`). Key methods:
  `handle` (`controller.py:188`), `_start` (`controller.py:213`), `_inspect`
  (`controller.py:246`), `_run_renderer` (`controller.py:562`), `_validate_frame`
  (`controller.py:588`), `_project` (`controller.py:635`),
  `_validate_renderer_path` (`controller.py:674`), `_resolve_route`
  (`controller.py:713`), `shutdown_for_agent_stop` (`controller.py:738`).
- Stop lifecycle (never finalize/remove/`stopped` while the watcher thread is
  alive): `_stop` (`controller.py:266`), the post-projection late-`update` guard
  and compensation in `_tick` (`controller.py:391`), and
  `_compensate_stop_finalize` (`controller.py:442`) with the `finalized`
  watcher↔public-stop handshake. `_project` (`controller.py:635`) also normalizes
  the manager's `resident_persist_failed` (→ observable partial) and pre-send
  `stale_delete_failed` (→ error, no adopted id) outcomes.
- `_Watch` — per-watch in-memory state: thread, last-valid frame + timestamp,
  sticky `stopping`, `finalized` handshake flag, deduped error/epoch bookkeeping
  (`controller.py:118`).
- `setup(agent)` — registers the `task_card` tool with `glossary_package=None`
  (`controller.py:749`).
- `TelegramTaskCardAgent` — the narrow host Protocol the controller depends on
  instead of the concrete `Agent` (`interface.py:23`).

## Connections

- Composition root: `Agent._maybe_setup_task_card_controller` calls `setup`
  once a Telegram MCP client exists (`src/lingtai/agent.py:977`).
- Renderer: `_run_renderer` runs `sys.executable <renderer>` with the agent
  workdir as `cwd`; `_validate_renderer_path` confines the path to that workdir.
- Reverse channel: `_project` calls the private `_lingtai_telegram_task_card`
  tool with `channel="programmable"` on the `telegram` MCP client from
  `agent._mcp_clients_by_tool`, consumed by
  `TelegramManager._handle_task_card_update` (`src/lingtai/mcp_servers/telegram/manager.py`).
- Route: `_resolve_route` reads the automatic driver's turn-local
  `agent._telegram_task_card_context` so both slots resolve to the one tracked
  resident target for that account+chat.
- Transport ownership: the manager (`_deliver_channel_frame_locked`,
  `_rotate_task_card_to_latest`, `_replace_task_card_after_probe`) owns the
  hard-at-most-one / last-message resident transport; `_project` only reads its
  normalized `{status}`/`partial`/`resident_persist_failed`/`stale_delete_failed`
  outcome.
- Fail-loud: after-handle failures call `agent._enqueue_system_notification`.

## Composition

- **Parent:** [`src/lingtai/mcp_servers/ANATOMY.md`](../../ANATOMY.md).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md).
- **Automatic slot + route host:** the kernel Task Card hooks in
  [`src/lingtai/kernel/base_agent/ANATOMY.md`](../../../kernel/base_agent/ANATOMY.md);
  render/compose/persistence in `src/lingtai/mcp_servers/telegram/manager.py`.
- **Manual:** [`SKILL.md`](SKILL.md).

## State

The controller holds only in-memory per-watch state (`_watches`, threads,
last-valid frames, error epochs). It writes no files and deletes none — renderer
files are the agent's own. Durable Task Card state (resident message id per
account+chat, composed slots, the `/taskcard` delivery boolean) is owned by the
Telegram adapter, not here (see `src/lingtai/mcp_servers/ANATOMY.md`).

## Notes

- Telegram never executes agent code: the controller forwards only a validated
  card object, never the renderer, over the reverse channel.
- The first frame is synchronous, so a failing renderer yields a tool error and
  no watch handle; after-handle failures preserve the last valid frame and emit
  one deduped, per-episode wake plus one recovery wake.
- `_TASK_CARD_TOOL` here mirrors `lingtai.kernel.base_agent._TASK_CARD_TOOL` and
  `telegram/server.py:_PRIVATE_TASK_CARD_TOOL`; the three must stay in sync.
