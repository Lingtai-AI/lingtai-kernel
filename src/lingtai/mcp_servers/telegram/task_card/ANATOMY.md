---
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/__init__.py
  - src/lingtai/mcp_servers/telegram/task_card/interface.py
  - src/lingtai/mcp_servers/telegram/task_card/controller.py
  - src/lingtai/mcp_servers/telegram/task_card/resident.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/telegram/task_card/assets/render_bash_async.py
  - src/lingtai/mcp_servers/telegram/task_card/assets/render_daemon.py
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

The Telegram-owned Task Card unit drives the programmable slot and names the
resident boundary shared with the automatic event projection. `TaskCardResident`
owns channel frames, per-account+chat locks, compose, atomic enablement, and
the deterministic project/ensure boundary; `TelegramManager` remains the
Telegram transport adapter for the hard-at-most-one / last-message transaction.
The model-facing `task_card` tool runs an agent-supplied Python renderer and
projects only validated data onto that same resident target.
Normative promises live in the paired [`CONTRACT.md`](CONTRACT.md).

## Components

- `get_schema` / `get_description` — the `task_card` tool schema (`start` /
  `inspect` / `retry` / `stop`) and the description that routes to the manual
  (`controller.py:59`, `controller.py:100`).
- `TaskCardResident` — resident owner for channel frames, per-route tail-driven
  automatic provenance, route locks, atomic enablement, and `ensure`/`project`
  (`resident.py:9`).
- `TaskCardController` — thin Core: dispatch, synchronous first frame, watch
  registry, fail-loud/recovery wakes (`controller.py:179`). Key methods:
  `handle` (`controller.py:188`), `_start` (`controller.py:213`), `_inspect`
  (`controller.py:281`), `_run_renderer` (`controller.py:597`), `_validate_frame`
  (`controller.py:623`), `_project` (`controller.py:670`),
  `_validate_renderer_path` (`controller.py:746`), `_resolve_route`
  (`controller.py:785`), `shutdown_for_agent_stop` (`controller.py:810`).
  `_start` (`controller.py:213`) also keeps the watch addressable on a validated
  initial persistence-partial (`resident_persist_failed` with a route-matching id)
  rather than discarding it, and discards on any other first-frame error.
- Stop lifecycle (never finalize/remove/`stopped` while the watcher thread is
  alive): `_stop` (`controller.py:301`), the post-projection late-`update` guard
  and compensation in `_tick` (`controller.py:426`), and
  `_compensate_stop_finalize` (`controller.py:477`) with the `finalized`
  watcher↔public-stop handshake.
- Outcome validation: `_project` (`controller.py:670`) normalizes the manager's
  `resident_persist_failed` (→ observable partial surfacing the validated
  `message_id`) and treats pre-send `stale_delete_failed` / `indeterminate_send` /
  any malformed id as a plain error (no adopted id). `_route_matched_message_id`
  (`controller.py:720`) independently validates every returned compound id — route
  match to `watch.account`/`watch.chat_id` plus a positive-integer terminal id —
  for both clean and partial outcomes.
- `_Watch` — per-watch in-memory state: thread, last-valid frame + timestamp,
  sticky `stopping`, `finalized` handshake flag, deduped error/epoch bookkeeping
  (`controller.py:118`).
- `setup(agent, controller=...)` — registers the controller-bound `task_card`
  handler and its schema with `glossary_package=None`, reusing an existing
  controller when a full Agent refresh rebuilds the public tool registries
  (`controller.py:821`).
- `TelegramTaskCardAgent` — the narrow host Protocol the controller depends on
  instead of the concrete `Agent` (`interface.py:23`).

## Connections

- Composition root: `Agent._maybe_setup_task_card_controller`
  (`src/lingtai/agent.py:1023-1064`; `setup` call at
  `src/lingtai/agent.py:1064`) calls `setup` only after the newly rebuilt
  reverse-route map contains Telegram; it re-registers the same controller after a
  full refresh clears the public tool surface or a colliding MCP overwrites it,
  verifying the handler binding and owned schema rather than a name/count alone. It
  runs at the end of each MCP-connect path that may add the Telegram route
  (`src/lingtai/agent.py:1020`, `src/lingtai/agent.py:1121`).
- Renderer: `_run_renderer` runs `sys.executable <renderer>` with the agent
  workdir as `cwd`; `_validate_renderer_path` confines the path to that workdir.
- Reverse channel: `_project` calls the private `_lingtai_telegram_task_card`
  tool with `channel="programmable"` on the `telegram` MCP client from
  `agent._mcp_clients_by_tool`, consumed by
  `TelegramManager._handle_task_card_update` (`src/lingtai/mcp_servers/telegram/manager.py`).
- Route: `_resolve_route` reads the programmable controller's turn-local
  `agent._telegram_task_card_context` so its frames resolve to the one tracked
  resident target for that account+chat; the automatic event-tail broadcast is
  manager-owned and does not use this route.
- Transport ownership: the manager (`_deliver_channel_frame_locked`,
  `_rotate_task_card_to_latest`, `_replace_task_card_after_probe`) owns the
  hard-at-most-one / last-message resident transport; `_project` only reads its
  normalized `{status}`/`partial`/`resident_persist_failed`/`stale_delete_failed`/
  `indeterminate_send` outcome. The manager's `send_progress_message` forms a
  compound id only after `_sent_message_id_or_none` confirms a real positive `int`,
  else returns `indeterminate_send` so cold-send/old-first replacement fail closed.
- Fail-loud: after-handle failures call `agent._enqueue_system_notification`.

## Automatic event-tail projection paths

- **Rows/timestamps:** after validating `type == "tool_call"`,
  `_project_tool_call_row` reads only `tool_name`, redacted/bounded
  `tool_args._reasoning`, and top-level Unix-epoch `ts`; raw action is excluded.
  `_format_task_card_row_timestamp` projects a valid value as optional
  `started_at` in `HH:MM:SS UTC±HH`; missing, boolean, non-numeric, non-finite,
  or out-of-range values omit it. `_meta`, row arguments, notifications, and
  render time are never timestamp sources. Navigation:
  `manager.py:_project_tool_call_row`, `_format_task_card_row_timestamp`, and
  `_format_rows_task_card_text` (currently around lines 1990, 2061, and 2941).
- **Current telemetry:** `_project_final_carrier_metadata` accepts only a
  final-carrier `type == "notification_block_injected"` event's latest whole
  `_meta.agent_meta`, then projects
  `_meta.agent_meta.agent_state.token_usage.session` fields
  `session_cache_rate`, `cache_miss_tokens`, `cache_miss_budget`, `api_calls`,
  `context_tokens`, `context_window`, and `context_usage`. The tail stores no
  historical holders and passes this bounded projection to the existing
  `_format_task_card_metadata` two-line/150-character formatter through
  `_broadcast_task_card_event_window`; malformed or missing values omit safely.
  It never reads retired `tool_meta.token_usage`, row args, notifications, or
  render time. Navigation: `manager.py:_project_final_carrier_metadata`,
  `_reverse_tail_latest_rows`, `_append_new_lines`, `_current_automatic_frame`,
  and `_broadcast_task_card_event_window` (currently around lines 2022, 2129,
  2285, 2364, and 2391).
- **Freshness at every edit, committed atomically with transport success
  (Telegram 8482/8485/8487):** `_poll_event_tail` (`manager.py:2280`) splits
  into `_sync_event_tail_state` (the bounded incremental read alone,
  `manager.py:2232`) and the broadcast side effect. `_deliver_channel_frame_locked`
  (`manager.py:1694`) calls `_sync_event_tail_state` and renders a fresh
  automatic frame via `_current_automatic_frame` (`manager.py:2364`, the same
  renderer `_broadcast_task_card_event_window` calls, so a refresh can never
  diverge in row order, grouping, dividers, or truncation from a broadcast)
  immediately before composing a `programmable` edit, whenever the
  Telegram-owned `TaskCardResident` (`resident.py:9`) already has a
  *tail-driven* automatic frame committed for that route
  (`TaskCardResident.is_automatic_tail_driven`, `resident.py:104`) — marked by
  `TaskCardResident.set_frame`'s `tail_driven` flag (`resident.py:75`), which
  the manager sets only via `_set_channel_frame`'s pass-through
  (`manager.py:1638`) from `_broadcast_task_card_event_window` (the sole
  production renderer of a rows/metadata automatic frame from the tail). That
  refreshed frame is a **transaction-local proposal only**: it is passed to
  `_compose_channels`'s `automatic_override` parameter (`manager.py:1652`,
  which forwards to `TaskCardResident.compose`, `resident.py:108`, alongside
  the existing `channel`/`frame` programmable proposal) to build the outgoing
  text, and is committed through `TaskCardResident.set_frame` — together with
  the programmable frame, atomically, via one local `_commit()` closure inside
  `_deliver_channel_frame_locked` — only at the same post-transport-success
  points the programmable frame itself already committed at (in-place edit OK,
  rotation OK, replacement-recovery OK, or a confirmed send). Any transport
  failure (unknown/transient edit failure, rejected rotation/recovery,
  failed/indeterminate send) commits neither proposal, leaving the previously
  committed automatic frame and its tail-driven provenance byte-for-byte
  unchanged — a failed programmable edit can never poison or resurrect an
  automatic frame Telegram never received. A route with no automatic frame
  yet, or one that was never tail-driven (e.g. the legacy scalar single-tool
  automatic form, which carries no footer), proposes no automatic override at
  all, so this never fabricates a footer that was never there. Navigation:
  `manager.py:_sync_event_tail_state`, `_deliver_channel_frame_locked`,
  `_compose_channels`, `_set_channel_frame`;
  `resident.py:TaskCardResident.set_frame`, `.compose`,
  `.is_automatic_tail_driven`.
- **Regression/drift triggers:** the event-to-final-render coverage is
  `tests/test_telegram_task_card_event_tail.py:test_event_log_final_carrier_projects_session_telemetry_into_final_render`
  plus `test_malformed_current_telemetry_carrier_clears_previous_snapshot` and the adjacent timestamp/malformed-input cases. Cross-channel freshness
  coverage is `test_programmable_edit_re_reads_telemetry_appended_since_last_broadcast`,
  `test_second_programmable_edit_picks_up_telemetry_changed_between_edits`,
  the atomic-commit-on-failure coverage
  `test_failed_programmable_edit_does_not_commit_refreshed_automatic_frame`
  and `test_retry_after_failed_programmable_edit_commits_fresh_telemetry`, and
  `test_programmable_edit_does_not_fabricate_automatic_footer`. Update this
  anatomy and the paired contract/tests together if event types, the
  final-carrier metadata path, supported session fields, the two-line
  formatter budget, timestamp provenance, or the pre-edit refresh gating
  changes; do not broaden the automatic source without revisiting the
  authoritative-event rule.

## Composition

- **Parent:** [`src/lingtai/mcp_servers/ANATOMY.md`](../../ANATOMY.md).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md).
- **Automatic slot owner:** `TelegramManager`'s `logs/events.jsonl` tail
  worker and broadcast in `src/lingtai/mcp_servers/telegram/manager.py` (see
  `src/lingtai/mcp_servers/ANATOMY.md`); BaseAgent no longer builds or renders
  automatic rows.
- **Programmable route host:** the kernel Task Card hooks in
  [`src/lingtai/kernel/base_agent/ANATOMY.md`](../../../kernel/base_agent/ANATOMY.md)
  capture only the turn-local `{account, chat_id}` route this controller
  reads; render/compose/persistence for both channels stays in
  `src/lingtai/mcp_servers/telegram/manager.py`.
- **Manual:** [`SKILL.md`](SKILL.md).
- **Manual assets:** [`assets/render_bash_async.py`](assets/render_bash_async.py)
  and [`assets/render_daemon.py`](assets/render_daemon.py) — the two co-located,
  stdlib-only renderer templates the manual routes agents to (bash-async job and
  daemon task). They read an orchestrator-owned working-dir state snapshot and
  print one bounded Task Card object; they are packaged skill assets, not runtime
  code (the controller never imports them — it runs the agent's own working-dir
  copy as a subprocess).

## State

The resident module holds only in-memory channel frames, route locks, the
observed enablement transition, and — alongside the frames — which routes'
committed automatic frame is tail-driven (`_automatic_tail_driven`, cleared or
set in lockstep with the automatic frame it describes so the two can never
drift). Resident message ids remain in the existing TelegramAccount
`task_cards` state map; event history remains `events.jsonl`.
The controller holds only in-memory per-watch state (`_watches`, threads,
last-valid frames, error epochs). It writes no files and deletes none — the
renderer files it runs are the agent's own working-dir copies (the shipped
`assets/` templates are read-only starting points the agent copies and adapts).
Durable Task Card state (resident message id per account+chat, composed slots,
the `/taskcard` delivery boolean) is owned by the Telegram adapter, not here
(see `src/lingtai/mcp_servers/ANATOMY.md`).

## Notes

- Telegram never executes agent code: the controller forwards only a validated
  card object, never the renderer, over the reverse channel.
- The first frame is synchronous, so a failing renderer yields a tool error and
  no watch handle; after-handle failures preserve the last valid frame and emit
  one deduped, per-episode wake plus one recovery wake.
- `_TASK_CARD_TOOL` here mirrors `lingtai.kernel.base_agent._TASK_CARD_TOOL` and
  `telegram/server.py:_PRIVATE_TASK_CARD_TOOL`; the three must stay in sync.
