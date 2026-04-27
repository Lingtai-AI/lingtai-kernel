# Daemon Filesystem Refactor — Design

**Date:** 2026-04-27
**Status:** Approved for implementation
**Scope:** `lingtai-kernel`, single file capability `lingtai/core/daemon/`

## Problem

The daemon (神識) capability has three operational gaps:

1. **Token usage is not attributable.** Daemons accumulate tokens in local variables and append a single aggregate entry to the parent's `logs/token_ledger.jsonl` at run end. The total is correct, but per-daemon attribution ("how much did em-3 spend?") is unanswerable.

2. **Daemons are not filesystem-backed.** Emanations live entirely in `DaemonManager._emanations` — an in-memory dict of futures, locks, buffers. Nothing on disk. When a daemon's behavior is suspect, there is nothing to inspect.

3. **No progress visibility for the parent agent.** `daemon(action="list")` returns `{id, task[:80], status, elapsed_s}` — no token spend, no turn count, no current activity, no chat history. Parent agents abort daemons prematurely because they cannot tell whether a long-running daemon is doing real work or hung.

## Goal

Make daemons **mini-avatars from a filesystem perspective** while keeping the **threaded process model unchanged**. Each daemon gets its own working folder under the parent's `daemons/` directory, mirroring the avatar log surface (chat history, token ledger, events log, status JSON, heartbeat). Folders persist forever (until molt sweeps the working dir or the user manually deletes them); `reclaim` only stops the process, never touches disk.

The refactor must:
- Provide per-daemon token attribution (each daemon owns its own ledger)
- Keep the parent's `logs/token_ledger.jsonl` as the single source of truth for "everything this agent and its daemons spent" (write-duplication, tagged)
- Expose live per-daemon status as a one-file read (`daemon.json` snapshot)
- Preserve full forensic history (chat transcript + structured events) for any completed daemon
- Not change the daemon API surface (`emanate` / `list` / `ask` / `reclaim` semantics unchanged)
- Not change the threading or LLMService model

## Non-Goals

- Process isolation. Daemons remain threads inside the parent process. Subprocess daemons are explicitly out of scope.
- Provider routing. The "LLM preset / quiver" idea (letting agents pick which provider runs each daemon/avatar) is deferred to a separate spec.
- Folder cleanup. There is no GC, no TTL, no quota. Molts wipe the working dir and incidentally clean up daemons. The user/operator is responsible for non-molting agents that accumulate.
- Cross-process recovery. If the parent dies mid-daemon, the run folder is left as-is with whatever last state was written. No recovery sweep on agent startup.
- Migration of historical daemon folders. There is no historical data — daemons have never been on disk before.

## Architecture

`DaemonManager` keeps its current responsibilities — capability registration, tool-surface building, prompt construction, threaded execution, and the in-memory registry — and gains one new collaborator: `DaemonRunDir`, a per-emanation object that owns every filesystem effect for one run.

```
parent agent working_dir/
├── logs/token_ledger.jsonl              ← parent's billing view (existing;
│                                          gains tagged daemon entries)
└── daemons/
    └── em-3-20260427-094215-a1b2c3/     ← one folder per emanation, forever
        ├── daemon.json                   ← identity card + live status
        ├── .prompt                       ← system prompt as built (forensic)
        ├── .heartbeat                    ← mtime-touched on activity
        ├── history/
        │   └── chat_history.jsonl        ← session transcript
        └── logs/
            ├── token_ledger.jsonl        ← daemon-scoped per-call tokens
            └── events.jsonl              ← daemon_start, tool_*, daemon_done/...
```

### Folder naming

`em-<N>-<YYYYMMDD-HHMMSS>-<hash6>/` where:
- `N` is the in-context handle (1-based, resets to 1 on `reclaim` — current behavior preserved)
- `YYYYMMDD-HHMMSS` is UTC time at construction
- `hash6` is `secrets.token_hex(3)` (24 bits of entropy, collision-free in practice)

The handle prefix means `ls daemons/` sorts naturally by handle and reclaim cycle. The timestamp+hash means two daemons with the same handle (across reclaim cycles) get distinct folders.

### Process and lifecycle ownership

- `DaemonManager` owns "is this run currently active in memory" — the `_emanations` registry, `_pools`, the cancel events.
- `DaemonRunDir` owns "what's on disk for this run" — folder creation, JSON state writes, JSONL appends, heartbeat touches.
- They share `run_id` (folder basename) and `handle` (e.g., "em-3"), but never read or write each other's state directly.

Process model is unchanged: `ThreadPoolExecutor` workers, single shared `LLMService`, `threading.Event` cancellation. The FS layer is purely additive observability.

**Tracked-session invariant.** Daemon sessions are created with `tracked=False` (current behavior, preserved). This keeps the kernel from writing daemon LLM calls into the parent's `history/chat_history.jsonl`. With this refactor, the daemon's own transcripts now have a home at `daemons/<run_id>/history/chat_history.jsonl` — written explicitly by `DaemonRunDir.record_user_send` and `DaemonRunDir.bump_turn`, not by the kernel session machinery. Parent's chat history stays parent-only; daemon chat history is per-daemon. Same for the kernel's own token-ledger emission: daemon sessions being `tracked=False` means the kernel doesn't auto-write to the parent's ledger, leaving `DaemonRunDir.append_tokens` as the single explicit writer that controls both ledgers (daemon's and parent's tagged copy).

## Components

### `DaemonRunDir` — new class in `core/daemon/run_dir.py`

One instance per emanation. Constructed inside `_handle_emanate` before the worker thread starts (so the registry has the `run_dir` reference even if the future hasn't begun). All filesystem effects for that run flow through this object.

**Construction**

```python
DaemonRunDir(
    parent_working_dir: Path,
    handle: str,                  # "em-3"
    task: str,
    tools: list[str],
    model: str,
    max_turns: int,
    timeout_s: float,
    parent_addr: str,             # parent._working_dir.name
    parent_pid: int,              # os.getpid()
    system_prompt: str,           # the built emanation prompt
)
```

The constructor:
1. Generates `run_id = f"{handle}-{strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"`.
2. Creates `<parent>/daemons/<run_id>/`, plus `history/` and `logs/` subdirectories.
3. Writes initial `daemon.json` (atomic) with `state="running"`, identity fields populated, counters at zero.
4. Writes `.prompt` once (the system prompt verbatim).
5. Touches `.heartbeat`.
6. Appends a `daemon_start` event to `logs/events.jsonl`.

**Mutating methods**

| Method | Hook | Effect |
|---|---|---|
| `record_user_send(text, kind)` | Before `session.send(text)` | Appends `{role: "user", text, kind, turn, ts}` to `chat_history.jsonl`. `kind` ∈ `{"task", "tool_results", "followup"}`. Tool result payloads (`kind="tool_results"`) are written verbatim — no truncation. Chat history is forensic; we want full fidelity. JSONL line size is bounded only by `O_APPEND` atomicity (lines >PIPE_BUF=4096B may interleave under multi-writer, but this file has only one writer per daemon) |
| `bump_turn(turn, response_text)` | After each `session.send` returns | Updates `daemon.json` (turn, elapsed_s, current_tool=null) atomically. Appends `{role: "assistant", text, turn, ts}` to `chat_history.jsonl`. Touches heartbeat |
| `set_current_tool(name, args)` | On each tool dispatch | Updates `daemon.json` (current_tool, tool_call_count) atomically. Appends `{event: "tool_call", name, args_preview}` to `events.jsonl`. Touches heartbeat |
| `clear_current_tool(result_status)` | After each tool returns | Updates `daemon.json` (current_tool=null) atomically. Appends `{event: "tool_result", name, status}` to `events.jsonl` |
| `append_tokens(input, output, thinking, cached)` | After each LLM response | Appends one entry to daemon's `logs/token_ledger.jsonl` via `append_token_entry`. Also writes one **tagged** entry to parent's `logs/token_ledger.jsonl` (see "Token ledger tagging" below). Updates running totals in `daemon.json` |
| `mark_done(text)` | Normal completion | Updates `daemon.json` (state=done, finished_at, result_preview=text[:200]) atomically. Appends `daemon_done` event |
| `mark_failed(exc)` | Exception in run loop | Updates `daemon.json` (state=failed, error.{type, message}) atomically. Appends `daemon_error` event |
| `mark_cancelled()` | Cancel event observed | Updates `daemon.json` (state=cancelled) atomically. Appends `daemon_cancelled` event |
| `mark_timeout()` | Watchdog fired before cancel | Updates `daemon.json` (state=timeout) atomically. Appends `daemon_timeout` event |

**Internal helpers (private)**

- `_atomic_write_json(path, data)` — write to `path.tmp`, `os.replace()` to `path`. Used only for `daemon.json`.
- `_append_jsonl(path, entry)` — open append, single line. Single-writer per file (except parent's token_ledger which relies on `O_APPEND` + sub-PIPE_BUF lines).
- `_now_iso()` → `"2026-04-27T09:42:15Z"` matching token-ledger format.
- `_now_secs()` → time since `started_at`, for `elapsed_s`.

**Properties**

- `run_id`, `handle`, `path` (the daemon dir), `chat_path`, `events_path`, `daemon_json_path`, `prompt_path`, `heartbeat_path`.

### `daemon.json` schema

```json
{
  "handle": "em-3",
  "run_id": "em-3-20260427-094215-a1b2c3",
  "parent_addr": "researcher",
  "parent_pid": 48211,

  "task": "Find every TODO comment under src/ and report file:line references",
  "tools": ["file", "bash"],
  "model": "claude-sonnet-4-6",
  "max_turns": 30,
  "timeout_s": 300.0,

  "state": "running",
  "started_at": "2026-04-27T09:42:15Z",
  "finished_at": null,
  "elapsed_s": 12.4,

  "turn": 4,
  "current_tool": "read",
  "tool_call_count": 7,

  "tokens": {
    "input": 4521,
    "output": 312,
    "thinking": 188,
    "cached": 1024
  },

  "result_preview": null,
  "error": null
}
```

**State values:** `running`, `done`, `failed`, `cancelled`, `timeout`.

**Cadence:** rewritten atomically on every LLM round (turn bump) and every tool dispatch (current_tool update), plus terminal transition. For a 30-turn daemon doing ~3 tool calls per turn, this is ~120 atomic writes — sub-millisecond each on SSD, invisible relative to LLM round-trip latency.

### Token ledger tagging

The existing `lingtai_kernel.token_ledger.append_token_entry` writes a fixed schema: `{ts, input, output, thinking, cached}`. It does not accept extra fields. We have two options for tagging the parent's copy:

**Chosen:** add an optional `extra: dict | None = None` keyword to `append_token_entry`. When `extra` is supplied, its keys are merged into the entry dict before serialization. Existing callers stay untouched (default `None`). The daemon passes `extra={"source": "daemon", "em_id": handle, "run_id": run_id}`.

**Rejected:** write to parent's ledger via raw JSON append outside `append_token_entry`. This duplicates the schema and decouples the format from the canonical writer.

The schema for `sum_token_ledger` reads only the four numeric fields (`input/output/thinking/cached`); extra keys are ignored. So tagging is purely additive — existing summing behavior continues to count daemon spend in the parent's lifetime totals exactly as it does today.

**Forward note:** `intrinsics/soul.py` and `base_agent.py` also call `append_token_entry` for the parent's own LLM calls. Those callers do NOT need to be tagged with `source: "daemon"` — they're parent calls. The `source` tag is added only by daemon writes. Future analytics can decompose the parent's ledger by checking for the `source` key (absence = parent call).

### `DaemonManager` — slimmed orchestrator

Surface unchanged (capability still exposes `emanate`, `list`, `ask`, `reclaim`). Internal changes:

- `_emanations` registry entry gains `"run_dir": DaemonRunDir` field.
- `_handle_emanate` constructs the `DaemonRunDir` (including folder creation) before scheduling the future. If construction raises (filesystem failure), returns an error response without scheduling — never partial state.
- `_run_emanation` adds calls into `run_dir` at every hook: `record_user_send` before each `session.send`, `bump_turn` after each response, `set_current_tool`/`clear_current_tool` around each tool dispatch, `append_tokens` after each response, terminal `mark_done`/`mark_failed`/`mark_cancelled` in the `finally` block.
- `_handle_list` is unchanged — still in-memory only — but the per-emanation dict it returns gains `"run_id"` and `"path"` fields so an inspecting agent knows where to look on disk.
- `EMANATION_BLACKLIST` unchanged: `{"daemon", "avatar", "psyche", "library"}`.

### `core/daemon/__init__.py` — entry point

Stays as the entry point with `setup()`, `get_description()`, `get_schema()`, and `DaemonManager`. The FS code lives one import away in `run_dir.py`. The current import `from lingtai_kernel.token_ledger import append_token_entry` and the inline ledger-write block at the end of `_run_emanation` (the existing `if tok_in or tok_out ...` aggregator) are removed — token writes now happen per-call inside `DaemonRunDir.append_tokens`.

### `core/daemon/manual/SKILL.md` — populated

Currently a placeholder. This refactor populates it with:
- Folder layout reference
- Inspection patterns ("when a daemon looks stuck, read `daemons/em-3-.../daemon.json` for live state, then `chat_history.jsonl` for the transcript")
- Worked examples of using `bash`/`read` to investigate a slow or failed daemon
- Note that `list` shows in-memory active runs only; historical runs require FS inspection

## Data flow — one daemon's lifetime

Concrete write sequence for an emanation that does 2 LLM rounds with one tool call between, then completes normally. Times illustrative.

```
T+0.000s  emanate(task, tools=["file"])
              ↓
          run_id = "em-3-20260427-094215-a1b2c3"
          run_dir = DaemonRunDir(...)         ← constructor:
              mkdir daemons/em-3-.../{,history,logs}
              write daemon.json (state=running, turn=0)        [atomic]
              write .prompt                                    [single shot]
              touch .heartbeat
              append events.jsonl: {event: "daemon_start", ts}
          future = pool.submit(_run_emanation, em_id, run_dir, ...)
          registry["em-3"] = {future, task, run_dir, cancel_event, ...}

T+0.001s  ── _run_emanation thread starts ──
          run_dir.record_user_send(task, kind="task")
              append history/chat_history.jsonl: {role:"user", text:task, turn:0, kind:"task", ts}

T+0.002s  session = service.create_session(...)
          response = session.send(task)        ← LLM call #1
T+1.840s  ← response: text="Scanning...", tool_calls=[read(...)]

T+1.840s  run_dir.append_tokens(input=4521, output=312, thinking=188, cached=1024)
              append daemons/.../logs/token_ledger.jsonl       [O_APPEND]
              append parent's logs/token_ledger.jsonl (tagged) [O_APPEND]
              update daemon.json: tokens={...}                  [atomic]

T+1.841s  run_dir.bump_turn(turn=1, response_text="Scanning...")
              update daemon.json: turn=1, elapsed_s=1.84, current_tool=null  [atomic]
              append history/chat_history.jsonl: {role:"assistant", ...}
              touch .heartbeat

T+1.842s  parent inbox notification: "[daemon:em-3]\n\nScanning..."  (existing _notify_parent — unchanged)

T+1.843s  ── tool dispatch loop ──
          run_dir.set_current_tool(name="read", args={"file_path":"src/main.py"})
              update daemon.json: current_tool="read", tool_call_count=1   [atomic]
              append events.jsonl: {event:"tool_call", name, args_preview, ts}
              touch .heartbeat

T+1.850s  result = handler({"file_path":"src/main.py"})
T+1.890s  ← {"content": "..."}

T+1.891s  run_dir.clear_current_tool(result_status="ok")
              update daemon.json: current_tool=null            [atomic]
              append events.jsonl: {event:"tool_result", name, status:"ok", ts}

T+1.892s  run_dir.record_user_send(tool_results_payload, kind="tool_results")
              append history/chat_history.jsonl: {role:"user", kind:"tool_results", ...}

T+1.893s  response = session.send(tool_results)   ← LLM call #2
T+3.510s  ← response: text="Task done...", tool_calls=[]

T+3.510s  run_dir.append_tokens(...)               ← second per-call entry
T+3.511s  run_dir.bump_turn(turn=2, response_text="Task done...")

T+3.512s  ── loop exits ──
          followup = self._drain_followup(em_id)  (unchanged)

T+3.513s  ── _run_emanation returns "Task done..." ──

T+3.514s  ── finally block ──
          run_dir.mark_done("Task done...")
              update daemon.json: state="done", finished_at=..., result_preview=text[:200]  [atomic]
              append events.jsonl: {event:"daemon_done", elapsed_s, ts}

T+3.515s  ── future.add_done_callback fires ──
          _on_emanation_done: notifies parent inbox  (existing — unchanged)
```

**Total disk writes for this 2-round daemon:** ~22 file operations over ~3.5 seconds. Sub-millisecond per op on SSD, invisible relative to ~3.4 seconds of LLM round-trip.

### Deviation paths

- **Cancellation mid-tool-loop:** `cancel_event.is_set()` returns True at the next checkpoint. `_run_emanation` returns `"[cancelled]"`. `finally` calls `run_dir.mark_cancelled()`. Folder preserved.
- **Exception during run:** caught in `finally`, `run_dir.mark_failed(exc)` writes `state="failed"` and `error={type, message}`. Future surfaces exception via `future.exception()` to `_on_emanation_done`.
- **Watchdog timeout:** watchdog sets `cancel_event` after `timeout_s`. Same path as cancellation, but `mark_timeout()` distinguishes the cause in events.
- **Reclaim during run:** `_handle_reclaim` sets cancel events, shuts down pools. Each emanation's `finally` runs `mark_cancelled()`. Registry clears, `_next_id` resets. Folders untouched.
- **Parent process dies mid-daemon:** thread dies with parent. `state` left at last write (could remain `running`). Heartbeat mtime stops advancing — inspectors detect orphans by `now() - heartbeat_mtime > threshold`. No recovery sweep.

## Error handling

### Filesystem failures

- **Constructor failures** (mkdir, initial daemon.json, .prompt): raise. `_handle_emanate` catches, returns `{status: "error", message: "Failed to create daemon folder: ..."}`. Never schedules thread, never registers in `_emanations`.
- **Mutation failures** (during the run): wrapped in `try/except OSError`. Logged via `self._agent._log("daemon_fs_error", ...)`, run continues. Best-effort policy — missing a status update is far less harmful than crashing the LLM loop.
- **Atomic-replace partial failure**: tempfile may be left behind. Acceptable — next `_atomic_write_json` overwrites cleanly. No `.tmp` cleanup logic.
- **Token-ledger duplication divergence**: each write is its own try/except. Daemon's local ledger is authoritative for forensics; parent's ledger may miss entries on failure. No transactional coupling.

### Concurrency

- `_atomic_write_json` for `daemon.json`: only the run thread writes — never concurrent. Per-pid tempfile suffix not needed.
- `_append_jsonl`: relies on POSIX `O_APPEND` + sub-PIPE_BUF (4096B) line atomicity. Our entries are well under that.
- Parent's `logs/token_ledger.jsonl`: subject to multi-writer concurrency (parent + each daemon's `append_tokens`). `O_APPEND` covers this; no lock.

### Path validation

- `parent_working_dir` is already a resolved validated `Path` from `Agent._working_dir`. Trusted.
- `run_id` built from fixed-format string (`em-{N}-{strftime}-{token_hex}`). No user input flows in. No traversal risk.

### Folder collisions

`secrets.token_hex(3)` provides 24 bits of entropy. Same-second collision within same parent requires ~16M simultaneous emanations — impossible given `max_emanations=4`. `mkdir(exist_ok=True)` is used defensively but not relied on.

## Testing

### Layer 1 — `tests/test_daemon_run_dir.py` (NEW)

Pure FS unit tests. No threads, no LLM mocks, no `Agent`. Each test creates a `tmp_path` parent dir, instantiates `DaemonRunDir`, calls methods, asserts file contents.

- Construction: folder structure, initial `daemon.json` fields, `.prompt` written verbatim, `run_id` format, no collision across same-handle constructions.
- Mutations: `bump_turn` updates JSON + appends chat, `record_user_send` kinds, `set_current_tool`/`clear_current_tool` transitions, `append_tokens` writes both ledgers (parent's tagged), running totals accumulate, terminal markers (`mark_done`/`mark_failed`/`mark_cancelled`) write correct state, heartbeat mtime advances.
- Atomicity & robustness: monkeypatched `os.replace` mid-flight leaves prior valid file readable, OSError in mutation does not propagate, every JSONL line is parseable.

### Layer 2 — `tests/test_daemon.py` (UPDATED)

Existing 17 tests: in-memory shortcuts that build `_emanations` entries by hand are refactored to either go through `_handle_emanate` (with real folders in `tmp_path`) or construct a `DaemonRunDir` and inject it.

New end-to-end tests:
- `test_emanate_creates_folder_visible_to_parent` — emanate, then assert `(tmp_path/.../daemons).iterdir()` returns one entry matching the run_id pattern.
- `test_completed_daemon_has_terminal_state_in_json` — emanate, wait for done, read `daemon.json`, assert `state="done"` and `result_preview` populated.
- `test_token_ledger_duplication_at_runtime` — emanate, wait for done, parent's ledger contains a tagged entry matching daemon's entry.
- `test_reclaim_preserves_folder` — emanate, reclaim, folder still exists with `state="cancelled"`.

### Coverage target

After this work, `core/daemon/` should reach ~95% line coverage between `__init__.py` and `run_dir.py`. Uncovered paths are the OSError best-effort branches.

## Files touched

```
src/lingtai/core/daemon/__init__.py        — slimmed; orchestration only
src/lingtai/core/daemon/run_dir.py         — NEW (~250 lines)
src/lingtai/core/daemon/manual/SKILL.md    — populated; was placeholder
src/lingtai/i18n/{en,zh,wen}.json          — new keys for inspection guidance in daemon description
tests/test_daemon.py                        — updated for FS-backed semantics
tests/test_daemon_run_dir.py                — NEW; pure FS unit tests
docs/superpowers/specs/2026-04-27-daemon-fs-refactor-design.md  — this spec
```

## Out of scope (explicitly)

- LLM provider preset / quiver — separate spec to follow.
- Subprocess process model for daemons.
- Folder cleanup / TTL / quota.
- Cross-process recovery on agent startup.
- Migration of historical daemon folders (no historical data).
- Concurrent reclaim + emanate locking (current code does not handle either; not introducing the problem).
