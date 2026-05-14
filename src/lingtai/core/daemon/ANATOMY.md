# core/daemon

Daemon capability (分神) — dispatch ephemeral subagents (分神) that operate
in parallel on the agent's working directory. Each emanation is a disposable
`ChatSession` with a curated tool surface, not an agent. Results are
persisted in per-run daemon folders; terminal completion/failure is surfaced
as a compact `.notification/system.json` event instead of ordinary parent
request text.

## Components

- `daemon/__init__.py` — public capability surface. `get_description`, `get_schema`, and `setup`; the core class is `DaemonManager`, which manages the full emanation lifecycle. Key internals: `_ToolCollector` (`daemon/__init__.py:37-64`) intercepts `add_tool` calls during preset-driven capability setup to build a sandboxed tool surface without mutating the parent's registry. `EMANATION_BLACKLIST` (`daemon/__init__.py:34`) prevents recursion by blocking `daemon`, `avatar`, `psyche`, `skills`, and deprecated `codex` tools in subagents.
- `daemon/run_dir.py` — per-emanation filesystem run directory. `DaemonRunDir` owns every filesystem effect for one run: folder layout, `daemon.json` atomic writes, JSONL appends, CLI progress events, heartbeat touches, `result.txt`, and terminal state markers. The `DaemonManager` calls into a `DaemonRunDir` at every lifecycle hook without itself touching the filesystem.

## Public API

The `daemon` tool exposes five actions:

| Action     | Description |
|------------|-------------|
| `emanate`  | Spawn one or more subagents with specified task + tools + optional preset |
| `list`     | List running/completed/failed emanations with status and elapsed time |
| `ask`      | Send a follow-up message to a running emanation |
| `check`    | Read-only progress tail: `daemon.json` state + last N events from `events.jsonl` |
| `reclaim`  | Cancel all running emanations, shut down thread pools, reset ID counter |

## Internal Module Layout

```
daemon/__init__.py
  ├── DaemonManager.__init__        — stores agent ref, config ceilings, emanation registry
  ├── handle()                      — top-level dispatcher (emanate/list/ask/check/reclaim)
  ├── _build_tool_surface()         — filters requested tools against blacklist, expands groups
  ├── _instantiate_preset_capabilities() — sets up preset tool surface in a sandbox
  ├── _build_emanation_prompt()     — composes the subagent's system prompt
  ├── _run_emanation()              — lingtai-backend worker tool loop (send → tool_calls → results)
  ├── _run_claude_code_emanation()  — claude-code backend; parses `--output-format stream-json --verbose` events in real time so `claude_session_id`, per-turn text, tool_use/tool_result, and token usage land in DaemonRunDir during the run (vs. post-hoc)
  ├── _run_codex_emanation()        — codex backend; one-shot `codex exec`, stdout streamed to record_cli_output
  ├── _find_claude_session_id()     — legacy `~/.claude/projects/` JSONL scan; now only a fallback when the stream-json `session_id` capture fails
  ├── _handle_emanate()             — validates presets, creates DaemonRunDir, submits to pool
  ├── _handle_list/check/reclaim    — individual action handlers
  ├── _handle_ask()                 — dispatcher: routes claude-code asks to _handle_ask_cli, lingtai asks to the followup buffer
  ├── _handle_ask_cli()             — claude-code follow-up via `claude --resume <session-id>`; same stream-json parse as _run_claude_code_emanation, so `daemon(check)` sees progress on follow-ups too
  ├── _watchdog()                   — timeout enforcement thread
  ├── _publish_daemon_notification() — publishes compact system notifications
  └── _drain_followup()             — drains per-emanation follow-up buffer (lingtai backend only)

daemon/run_dir.py
  ├── DaemonRunDir.__init__         — creates folder on disk, writes daemon.json + .prompt
  ├── Path properties               — run_id, path, daemon_json_path, prompt_path, heartbeat_path, chat_path, events_path, token_ledger_path, result_path
  ├── record_user_send()            — appends user-role entry to chat_history.jsonl
  ├── bump_turn()                   — marks end of LLM round (daemon.json + chat_history + heartbeat)
  ├── set_current_tool()            — marks tool dispatch starting (daemon.json + events + heartbeat)
  ├── clear_current_tool()          — marks tool dispatch finished
  ├── record_cli_output()           — records CLI backend stdout/stderr as cli_output events
  ├── append_tokens()               — dual-ledger token accounting (daemon's + parent's)
  ├── mark_done/failed/cancelled/timeout — terminal state markers (result.txt + preview on done)
  └── _atomic_write_json()          — tempfile + os.replace for crash-safe writes
```

## Key Invariants

- **No recursion:** `EMANATION_BLACKLIST` prevents emanations from spawning sub-emanations, avatars, psyche, the skill catalog, or deprecated codex.
- **Tool surface isolation:** `_ToolCollector` ensures preset-driven capability setup does not mutate the parent agent's tool registry.
- **Filesystem isolation:** Each emanation gets its own `daemons/em-<N>-<YYYYMMDD-HHMMSS>-<hash6>/` directory. `DaemonRunDir` uses atomic `os.replace` for `daemon.json` and single-writer append-only JSONL for events/chat history.
- **Timeout vs. cancel distinction:** Separate `timeout_event` and `cancel_event` allow the run loop to call `mark_timeout()` vs. `mark_cancelled()` based on which signal fired first.
- **Capacity control:** `max_emanations` caps concurrent subagents; completed futures are pruned before each new batch.
- **Preset validation is pre-flight:** Preset connectivity and capability instantiation are checked before any emanation is scheduled. A single failure refuses the whole batch.
- **Dual token ledger:** Token usage is written to both the daemon's own ledger and the parent's ledger with `source=daemon` attribution.
- **CLI progress stays inspectable, not conversational:** Claude Code/Codex stdout is persisted as `cli_output` events plus `daemon.json.last_output`; completion/failure publishes a bounded `system` notification pointing the parent to `daemon(action="check", id=...)`.
- **Full results live on disk:** `mark_done()` writes complete terminal output to `result.txt`; `daemon.json.result_preview` and notification bodies stay bounded.
- **Claude Code backend streams structured events, not buffered text.** `_run_claude_code_emanation` and `_handle_ask_cli` use `--output-format stream-json --verbose` so each Claude Code event (`system`/`init`, `assistant`, `user`, `result`) arrives on its own stdout line and is parsed live. The first event that carries a `session_id` writes `claude_session_id` to `daemon.json` immediately, so `daemon(action="ask")` is usable from the moment `emanate` returns rather than after the initial task completes. stderr drains in a background thread to its own pipe (no longer merged into stdout), so API/auth/rate-limit errors surface as `cli_output` events with `stream="stderr"`. A final `result` event with `is_error=true` is surfaced as `mark_failed`, so an error inside the LLM stream doesn't masquerade as success even when the underlying process exits 0. See GH issues #99 / #100 / #101 for the prior buffered-text failure mode that motivated this design.

## Dependencies

- `lingtai_kernel.llm.base.FunctionSchema` — tool schema type
- `BaseAgent._enqueue_system_notification` — compact daemon completion/failure events
- `lingtai_kernel.token_ledger` — `append_token_entry` for token accounting
- `lingtai.i18n` — `t()` for localized strings
- `lingtai.capabilities` — `setup_capability`, `_GROUPS` for preset sandbox instantiation
- `lingtai.presets` — `load_preset`, `expand_inherit` for per-emanation preset resolution
- `lingtai.preset_connectivity` — `check_connectivity` for LLM reachability pre-flight
- `lingtai.config_resolve` — `resolve_env` for API key resolution
- `lingtai.llm.service` — `LLMService` for dedicated preset LLM services
- `lingtai.agent.Agent` — parent agent type (TYPE_CHECKING only)

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `avatar/`, `mcp/`, `knowledge/` (private durable memory), `skills/` (skill catalog), `bash/`.
- **Manual:** `daemon/manual/SKILL.md` — skill documentation for the LLM.
- **Kernel hooks:** `setup()` is called during capability initialization; `DaemonManager.handle()` is registered as the `daemon` tool handler.
