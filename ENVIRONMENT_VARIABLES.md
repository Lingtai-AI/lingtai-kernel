---
name: environment-variable-registry
description: >
  Canonical registry for environment variables consumed by LingTai source,
  bundled MCPs, adapters, daemon composition, and focused tests.
version: 1.0.0
last_changed_at: "2026-08-10"
related_files:
- ANATOMY.md
- CONTRACT.md
- src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
- src/lingtai/ANATOMY.md
- src/lingtai/CONTRACT.md
- src/lingtai/adapters/posix/ANATOMY.md
- src/lingtai/adapters/windows/ANATOMY.md
- src/lingtai/auth/ANATOMY.md
- src/lingtai/kernel/ANATOMY.md
- src/lingtai/kernel/base_agent/ANATOMY.md
- src/lingtai/kernel/base_agent/CONTRACT.md
- src/lingtai/kernel/daemon_supervisor/ANATOMY.md
- src/lingtai/kernel/nudge/ANATOMY.md
- src/lingtai/kernel/refresh_watcher/ANATOMY.md
- src/lingtai/llm/openai/ANATOMY.md
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/prompts/ANATOMY.md
- src/lingtai/services/ANATOMY.md
- src/lingtai/tools/ANATOMY.md
- src/lingtai/tools/bash/ANATOMY.md
- src/lingtai/tools/daemon/ANATOMY.md
- src/lingtai/tools/mcp/ANATOMY.md
- src/lingtai/tools/soul/ANATOMY.md
maintenance: |
  This file is the one canonical environment-variable registry. Keep one row for
  each real environment name with its default, accepted values, scope,
  read/reload timing, invalid-value behavior, owner, and security note. Keep
  related_files repository-relative, duplicate-free, and reciprocal with every
  owner. The nested environment-variables skill is only a progressive-disclosure
  router; do not copy this table into it or into resident prompt layers.
---
# LingTai environment-variable registry

This is the canonical deep reference for environment configuration. Source code
and focused tests are the evidence for names and behavior; this document records
that behavior without introducing a parser, global configuration object, or new
setting. A changed value applies only at the read/reload point described below.
Secrets belong in supported private configuration files or secret stores, not in
reports, prompts, or this registry.

## Product and runtime

| Name | Default | Accepted values | Scope | Read/reload timing | Invalid behavior | Owner | Security |
|---|---|---|---|---|---|---|---|
| `LINGTAI_NUDGE_ENABLED` | `on` | `on`/`off`, `true`/`false`, `1`/`0`; one process/workdir | Nudge publication | Each Nudge operation and heartbeat; no restart | Falls back to `on` with a bounded diagnostic | `src/lingtai/kernel/nudge/__init__.py` | Disabling reminders is not an authorization boundary |
| `LINGTAI_NUDGE_REPEAT_INTERVAL` | `24h` | Positive duration using `s`, `m`, `h`, or `d`; global | Repeated unresolved Nudge findings | Each Nudge operation; no restart | Zero, negative, or malformed values fall back to `24h` | `src/lingtai/kernel/nudge/__init__.py` | Changes reminder timing only, not resolution or authority |
| `LINGTAI_NUDGE_FOLDER_SIZE_GB` | `5` | Positive finite number of decimal gigabytes (`1 GB = 10**9 bytes`); per-agent working directory | Folder-size Nudge threshold | Each folder-size evaluation; no restart | Missing, zero, negative, non-finite, or non-numeric values fall back to `5` | `src/lingtai/kernel/nudge/folder_size.py` | Advisory storage hygiene only; never grants deletion or cleanup authority |
| `LINGTAI_ACTIVE_STUCK_THRESHOLD_S` | `600` seconds | Numeric seconds; values below `30` clamp to `30` | ACTIVE no-progress watchdog | When the watchdog evaluates a turn | Invalid values fall back to `600` | `src/lingtai/kernel/base_agent/lifecycle.py` | Watchdog tuning can affect availability; it grants no capability |
| `LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO` | `0.4` | Finite float strictly greater than `0` and less than `1` | Main-agent and daemon metadata snapshots | Every metadata snapshot through the shared renderer | Missing, blank, non-numeric, non-finite, zero, negative, or `>=1` values fall back to `0.4` | `src/lingtai/kernel/config.py`, `src/lingtai/kernel/meta_block.py` | Advisory metadata only; never authorizes access or exposes prompt text |
| `LINGTAI_CACHE_MISS_BUDGET` | unset; `1_000_000` from `manifest.cache_miss_budget` | Positive integer; one agent/process | Soft since-last-molt cache-miss budget guard (restamps the `cache miss budget {N} reached, molt now` reminder and surfaces budget/remaining under `agent_meta.agent_state`) | Every cache-miss budget resolution (each meta snapshot) | Missing, non-int, bool, zero, or negative values fall back to the configured/default budget; the env never disables the guard below a positive int | `src/lingtai/kernel/meta_block.py` | Soft molt steering only; never blocks; not an authorization boundary. Governance delta: a budget that previously required a config-owner edit to schema-validated `manifest.cache_miss_budget` is now additionally agent-writable via `env_file`, with no upper bound and no validation feedback — an agent can set a huge value and silence its own molt nudge |
| `LINGTAI_REFRESH_ENV_OVERWRITE` | unset and off | `1` enables one refresh overwrite | One refresh handoff | Boot or refresh setup; consumed and removed after use | Other values are treated as off | `src/lingtai/cli.py`, `src/lingtai/agent.py` | Do not log inherited or env-file contents |
| `LINGTAI_RUNTIME_PYTHON` | unset; caller uses `sys.executable` | Local executable path | Runtime self-check and host-tool routing | When the consumer is invoked; relaunch to change interpreter | Missing or invalid is a caller/configuration error | `src/lingtai/cli.py` and runtime checks | A path is not a credential or a trust decision |
| `LINGTAI_RUNTIME_VENV` | unset | Local virtualenv directory path | Host-tool runtime hint | When a host tool is invoked; new process sees changes | Missing is tolerated when another interpreter is available | `src/lingtai/cli.py` | Do not infer package trust or freshness from an unrelated shell |
| `LINGTAI_VERBOSE` | unset and off | `1` enables DEBUG console logging for `lingtai-agent run` | Agent boot file logging; the rotating `logs/agent.log` file handler is always DEBUG, the console handler is DEBUG only when set | Boot of `lingtai-agent run`; the `--verbose` flag is equivalent | Other values are treated as off | `src/lingtai/cli.py`, `src/lingtai/kernel/logging.py` | Console verbosity only; not an authorization boundary |
| `LINGTAI_SHELL` | unset (platform default) | `posix`, `powershell`, `cmd`, `gitbash`, `wsl`; case-insensitive | Canonical shell tool dialect and spawn selection | Shell tool setup; restart to change | Unknown values fall back to the platform default | `src/lingtai/adapters/shell.py` | Only changes which shell program the model-driven shell tool spawns; it is not an authorization boundary |
| `LINGTAI_TOOL_TIMEOUT_MAX_SECONDS` | `120` | Positive finite number of seconds; values below the default sync timeout (30) are floored to `30` | Hard ceiling for the shell tool's sync `run.timeout` parameter | Each sync `run` call; no restart | Missing, empty, non-numeric, zero, negative, or non-finite values fall back to `120`; values below `30` are raised to `30` so the default sync run is never refused | `src/lingtai/tools/bash/_tool_family.py`, `src/lingtai/tools/bash/__init__.py` | Refusal above the ceiling only; work needing longer must be launched with `async=true`. The ceiling is an environment variable, not a per-config value |
| `LINGTAI_AGENT_DIR` | unset; normally launcher-injected | Existing local directory | Out-of-process MCP and client workdir | MCP/client process start; restart after change | Invalid path fails the MCP or client operation | `src/lingtai/mcp_servers/_config.py` | Keep private workdir contents out of model-facing output |
| `LINGTAI_MCP_NAME` | unset | Registered MCP name | One MCP process identity | MCP process start; restart after change | Missing or unknown name fails closed | `src/lingtai/mcp_servers/_config.py` | Prevents arbitrary server selection; it is not a secret |
| `LINGTAI_TUI_DIR` | unset | Local directory path | TUI-facing invocation and auth lookup | Invocation; restart caller after change | Invalid path fails the caller's lookup | TUI integration and `src/lingtai/venv_resolve.py` | May reveal local layout; do not treat as authorization |
| `LINGTAI_FILE_IO_BACKEND` | Implementation default | Recognized backend name | File-I/O service | Service construction; rebuild or restart service | Unknown value fails closed | File-I/O service adapter | Backend selection is not a filesystem sandbox |
| `LINGTAI_FILE_IO_SIDECAR` | unset | Local executable or path | File-I/O sidecar selection | Service construction; restart service | Invalid or unavailable sidecar fails that path | File-I/O service adapter | Validate ownership and keep sidecar authority narrow |
| `LINGTAI_SEARCH_SIDECAR` | unset; pure-Python fallback where supported | Local executable or path | Search service | Service construction; restart service | Documented fallback or operation failure; never downloads one | Search service adapter | Validate executable ownership before use |
| `LINGTAI_SKIP_RUST_BUILD` | unset and off | `1` enables skipping the Rust sidecar build | Developer and package build | Build invocation; rerun build after change | Invalid values are treated as off | `setup.py` and wheel tests | Never ship a wheel claiming a sidecar that was not built |
| `LINGTAI_REQUIRE_RUST_BUILD` | unset and off | `1` requires the Rust sidecar build | Developer and package build | Build invocation; rerun build after change | Invalid values fail the required-build path | `setup.py` and wheel tests | Build policy is not runtime authorization |
| `LINGTAI_SOUL_FLOW_ENABLED` | disabled unless host enables it | `1`/`0` and documented component boolean forms | Optional soul-flow capability | Capability bootstrap; refresh or restart after change | Treated as disabled | `src/lingtai/tools/soul` | Not a command-execution or approval switch |

## MCP and provider configuration

| Name | Default | Accepted values | Scope | Read/reload timing | Invalid behavior | Owner | Security |
|---|---|---|---|---|---|---|---|
| `LINGTAI_CLOUD_MAIL_CONFIG` | unset | JSON path; `~` expands; absolute or relative to `LINGTAI_AGENT_DIR` or cwd | Cloud-mail integration | Eagerly read and start at MCP startup; restart MCP after change | Missing/unreadable path, invalid JSON, or invalid schema leaves manager unavailable; tool calls fail closed | `src/lingtai/mcp_servers/cloud_mail/server.py` | Keep credentials in private files and out of logs |
| `LINGTAI_IMAP_CONFIG` | unset | Local configuration reference | IMAP MCP | MCP startup; restart after change | Invalid configuration fails closed | `src/lingtai/mcp_servers/imap` | Never print configuration or passwords |
| `LINGTAI_FEISHU_CONFIG` | unset | JSON path; `~` expands; absolute or relative to `LINGTAI_AGENT_DIR` or cwd | Feishu MCP | Eagerly read and start WebSocket service at MCP startup; restart MCP after change | Missing/unreadable path, invalid JSON, or invalid schema leaves manager unavailable; tool calls fail closed | `src/lingtai/mcp_servers/feishu/server.py` | Keep app secrets out of logs and payloads |
| `LINGTAI_TELEGRAM_CONFIG` | unset | Local configuration reference | Telegram MCP | MCP startup; restart after change | Invalid configuration fails closed | `src/lingtai/mcp_servers/telegram` | Protect bot tokens and chat routing |
| `LINGTAI_WECHAT_CONFIG` | unset | Local configuration reference | WeChat MCP | MCP startup; restart after change | Invalid configuration fails closed | `src/lingtai/mcp_servers/wechat` | Protect credentials and contact state |
| `LINGTAI_WHATSAPP_CONFIG` | unset | Local configuration reference | WhatsApp MCP | MCP startup; restart after change | Invalid configuration fails closed | `src/lingtai/mcp_servers/whatsapp` | Protect credentials and recipient state |
| `LINGTAI_CODEX_TRANSPORT` | unset; adapter's normal REST path | `websocket`/`ws` (opt-in); `rest`/`http`/`https` (explicit REST); anything else → REST | Codex provider session | Session construction; restart session | Invalid value keeps the safe REST default | `src/lingtai/llm/openai/adapter.py` | Never put bearer tokens in a transport hint |
| `LINGTAI_CODEX_WS` | unset and off | `1`/`true`/`yes`/`on` → WebSocket opt-in; anything else → REST | Codex provider session | Session construction; restart session | Invalid value keeps the safe REST default | `src/lingtai/llm/openai/adapter.py` | Transport selection is not a trust boundary |
| `LINGTAI_CODEX_WS_EPOCH_RESET_TURNS` | `0` | Non-negative integer | Codex diagnostic WebSocket epoch | Session construction; restart session | Invalid value falls back to the adapter default (`0`, turn-count reset disabled) | Codex adapter | Avoid values that cause unexpected session churn |
| `LINGTAI_CODEX_RESPONSES_TRACE` | off | Adapter-recognized boolean | Bounded Codex Responses-wire diagnostics | Session construction; restart session | Invalid value is treated as off | Codex adapter | Traces remain local and redacted |
| `LINGTAI_CODEX_RESPONSES_TRACE_PATH` | unset; adapter default | Local path | Codex Responses-wire diagnostics | Session construction; restart session | Unwritable or invalid path fails closed or disables tracing | Codex adapter | Trace files can contain sensitive prompts; restrict permissions |
| `LINGTAI_CLAUDE_MANAGED_ROOT` | Host-specific or unset | Local directory path | Claude launcher | Launch; relaunch after change | Invalid path fails closed | Claude adapter | Never widen the root from untrusted model text |
| `LINGTAI_CLAUDE_INTERACTIVE_FIFO` | unset | Local FIFO or path | Claude interactive launch | Interactive launch; relaunch after change | Wrong type or permissions fail closed | Claude adapter | Protect the FIFO from other users and processes |

## Daemon and test composition

These entries are injected by supervisors, fake backends, or focused tests rather
than being general user configuration. They remain here so the composition
surface is explicit; do not set test hooks in a production agent environment.

| Name | Default | Accepted values | Scope | Read/reload timing | Invalid behavior | Owner | Security |
|---|---|---|---|---|---|---|---|
| `LINGTAI_DAEMON_CAPSULE_FD` | unset | Supervisor-provided integer file descriptor | POSIX supervised daemon | Once at daemon start; restart daemon | Missing or invalid value fails the supervised path | `src/lingtai/kernel/daemon_supervisor` | Accept only a supervisor-validated inherited descriptor |
| `LINGTAI_DAEMON_CAPSULE_HANDLE` | unset | Integer OS handle in the spawn `handle_list` | Windows supervised daemon | Once at Windows entrypoint; restart daemon | Missing or invalid value fails the supervised path | `src/lingtai/adapters/windows/daemon_supervisor.py` | Convert only a validated inherited handle; it carries no capsule data |
| `LINGTAI_DAEMON_COMPLETION_FILE` | unset | Supervisor-owned local path | Daemon completion reporting | Daemon start; restart daemon | Invalid path fails completion reporting | Daemon completion MCP and runner | Keep the path inside the assigned run directory |
| `LINGTAI_DAEMON_RUN_ID` | unset | Opaque run identifier | One daemon run and its event/token writes | Daemon startup and each relevant write; restart to change | Missing or invalid value is a run-integrity error where required | Daemon run directory | Do not use as a secret or expose unrelated run IDs |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM` | off | Test-only boolean | Fake-LLM supervisor composition | Test or supervisor construction; restart child | Invalid value is off | Test and supervisor code | Never enable in production |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH` | unset | Registered test scenario string | Fake-LLM finish mode | Child construction; restart child | Invalid scenario fails the test | Test and supervisor code | No production effect |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SCENARIO` | unset | Registered test scenario | Fake-LLM scenario selection | Child construction; restart child | Unknown scenario fails closed | Test and supervisor code | Never use to bypass provider checks |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP` | zero or unset | Non-negative numeric seconds | Fake-LLM test delay | Fake backend construction; restart child | Falls back to zero or fails the test | Test and supervisor code | Not a production timeout control |
| `LINGTAI_FAKE_APP_SERVER_MODE` | unset; normal quota | Test scenario `exhausted` | Fake Codex app-server quota | Fake-server invocation; rerun test after change | Unrecognized value falls back to normal quota | `tests/_fake_codex_app_server.py` | Test-only; never set in production |
| `LINGTAI_FAKE_CLI_REPORT` | unset | Test-only path or selector | Fake CLI report | Fake CLI invocation; rerun test after change | Invalid value fails the test | `tests/_fake_*` | Keep artifacts in a test temporary directory |
| `LINGTAI_TEST_CONFIG` | unset | Test-only string or path | Test fixture setup | Fixture construction; rerun test after change | Invalid value fails fixture setup | `tests/` | No production behavior |
| `LINGTAI_TEST_FAKE_CLAUDE_SIGNAL_RECORD` | unset | Test-only local path | Fake Claude signal record | Fake launcher invocation; rerun test after change | Invalid value fails the test | `tests/` | Keep artifacts in a test temporary directory |

## Reading and ownership notes

- `LINGTAI_CACHE_MISS_BUDGET` is read live at each cache-miss budget resolution
  (live-read, like the nudge env vars), so setting it in the agent's `env_file`
  and refreshing applies it without editing `init.json`. The agent itself may
  tune it (e.g. raise the budget on a long, cache-expensive session) the same
  way. It is a soft steer: nothing is blocked, and an invalid value simply falls
  back to the configured/default budget. Note that *deleting* the line from
  `env_file` does not unset it — a refresh relaunches inheriting the old
  `os.environ`, and `load_env_file` only writes keys it finds in the file; to
  fall back to the configured budget in-band, set the value to `0` or blank.
- Environment values are process input, not authorization grants. Human or
  configuration-owner approval remains required for writes, refreshes, downloads,
  sends, and other consequential actions.
- Invalid values are described here as fallback or failed-read behavior. They
  must not be repaired by rewriting user configuration.
- A registry row is not a promise that a variable is safe to set in production;
  test and supervisor composition variables are deliberately labeled above.
