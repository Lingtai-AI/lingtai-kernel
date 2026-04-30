# Handshake Files

## What

Files that must exist in a working directory for an agent to be considered "alive"
by the network. Used by the parent's boot verification, the mail delivery service,
and peer-to-peer liveness checks. The handshake is entirely filesystem-based —
no IPC, no sockets, no protocol negotiation.

## Contract

### `.agent.json` — Identity manifest

**Created by:** `BaseAgent.__init__()` → `WorkingDir.write_manifest()` (base_agent.py:231-232, workdir.py:286-290). Full schema: file-formats.md §1. Key fields: `agent_id` (permanent birth ID), `agent_name`, `address`, `created_at`, `started_at`, `admin` (`null`=human, `{}`=no-privilege, truthy=orchestrator), `language`, `stamina`, `state` (5-state enum), `soul_delay`, `molt_count`, `capabilities`.

**Avatar-specific:** `admin` is always `{}` (line 383). **Write strategy:** atomic rename via `.agent.json.tmp` (workdir.py:286-290). **Refreshed:** on every `_set_state()` call (base_agent.py:608) — so `state` field changes in-flight; `agent_id`/`agent_name`/`admin`/`created_at`/`molt_count` are stable after init.

### `.agent.heartbeat` — Liveness timestamp

**Created by:** `_heartbeat_loop()` (base_agent.py:718-719), written every 1 second.

**Format:** plain text file containing a single Unix timestamp as a float:
```
1744567890.123456
```

**Liveness check** (`handshake.is_alive`, handshake.py:39-55):
```python
time.time() - float(hb.read_text().strip()) < threshold  # default 2.0s
```

**Deleted by:** `_stop_heartbeat()` (base_agent.py:704-706) on clean shutdown.

**Special case:** Human agents (`admin: null`) never write heartbeat and are
always considered alive (handshake.py:46-47).

### `.status.json` — Runtime snapshot

**Written by:** `_save_chat_history()` at end of each turn (base_agent.py:1689).
Calls `self.status()` (base_agent.py:1719–1782). Full schema: file-formats.md §3.

**Mutability:**

| Category | Fields | Source |
|----------|--------|--------|
| **Boot-constant** | `identity.*`, `runtime.started_at`, `runtime.stamina` | Never after boot |
| **Turn-variable** | `runtime.current_time`, `runtime.uptime_seconds`, `runtime.stamina_left` | Changes every turn |
| **Context-decomposing** | `tokens.context.{system_tokens, fixed_tokens}` | Only on system prompt rebuild |
| **Context-decomposing** | `tokens.context.{tools_tokens, window_size}` | Only on tool surface change |
| **Growth** | `tokens.context.{history_tokens, growing_tokens, total_tokens, usage_pct}` | Grows each turn |
| **Accumulating** | `tokens.{input,output,thinking,cached,total}_tokens`, `tokens.api_calls` | Monotonically increasing |
| **Irreversible** | `tokens.estimated` | `False→True` once; stays `True` |

Use boot-constant fields as test discriminators — they don't jitter between turns.

### `.agent.lock` — Exclusive process lock

**Created by:** `WorkingDir.acquire_lock()` (workdir.py:48-70) on agent start.
**Released by:** `WorkingDir.release_lock()` (workdir.py:72-84) on clean shutdown.
**Purpose:** Ensures only one agent process runs per working directory.
**Mechanism:** OS-level `fcntl.flock()` (POSIX) or `msvcrt.locking()` (Windows).
**Not required for liveness checks** — used only for singleton enforcement.

### Handshake sequence on boot

1. `lingtai run <dir>` → `build_agent()` → `Agent.__init__()`:
   creates `WorkingDir`, acquires `.agent.lock`, writes `.agent.json`.
2. `agent.start()` → `_start_heartbeat()`: daemon thread writes `.agent.heartbeat` every 1s.
3. Parent's `_wait_for_boot()` detects `.agent.heartbeat` → returns `"ok"`.

### Liveness queries from the network

| Query | File checked | Algorithm |
|-------|-------------|-----------|
| `is_agent(path)` | `.agent.json` | `is_file()` (handshake.py:25-27) |
| `is_alive(path)` | `.agent.heartbeat` | `now - timestamp < 2.0s` (handshake.py:39-55) |
| `is_human(path)` | `.agent.json` | `manifest.admin is None` (handshake.py:30-36) |
| Mail delivery | both | `.agent.json` exists AND heartbeat fresh (mail.py:141-142) |

## Source

| What | File | Line(s) |
|------|------|---------|
| `is_agent` | `src/lingtai_kernel/handshake.py` | 25-27 |
| `is_alive` | same | 39-55 |
| `is_human` | same | 30-36 |
| `resolve_address` | same | 13-22 |
| `.agent.json` write | `src/lingtai_kernel/workdir.py` | 286-290 |
| `.agent.json` schema | `src/lingtai_kernel/base_agent.py` | 1477-1501 |
| `.agent.heartbeat` write | same | 718-719 |
| `.agent.heartbeat` schema | anatomy `reference/file-formats.md` §11 | 591-602 |
| `.status.json` schema | anatomy `reference/file-formats.md` §3 | 245-281 |
| `.agent.lock` acquire/release | `src/lingtai_kernel/workdir.py` | 48-84 |
| Mail liveness check | `src/lingtai_kernel/services/mail.py` | 141-142 |

## Related

- `boot-verification` — the parent's specific use of these files to confirm avatar start.
- `spawn` — creates the working directory and init.json that boot the child.
