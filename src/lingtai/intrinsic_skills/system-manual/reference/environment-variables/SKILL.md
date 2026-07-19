---
name: environment-variables
description: >
  Complete catalogue of environment variables consumed by the LingTai kernel,
  wrapper, bundled MCPs, adapters, tests, and daemon composition surfaces.
version: 1.0.0
last_changed_at: "2026-07-16T00:00:00-07:00"
related_files:
- ANATOMY.md
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/prompts/substrate/substrate.md
- src/lingtai/prompts/procedures/procedures.md
- src/lingtai/kernel/nudge/__init__.py
- src/lingtai/kernel/nudge/ANATOMY.md
- src/lingtai/kernel/config_resolve.py
- src/lingtai/kernel/refresh_watcher/MANUAL.md
- src/lingtai/ANATOMY.md
- src/lingtai/adapters/posix/ANATOMY.md
- src/lingtai/adapters/windows/ANATOMY.md
- src/lingtai/auth/ANATOMY.md
- src/lingtai/kernel/ANATOMY.md
- src/lingtai/kernel/base_agent/ANATOMY.md
- src/lingtai/llm/openai/ANATOMY.md
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/services/ANATOMY.md
- src/lingtai/tools/daemon/ANATOMY.md
- src/lingtai/tools/soul/ANATOMY.md
maintenance: |
  Keep this catalogue complete for every LINGTAI_* variable read by production
  source or intentionally exposed test/daemon composition. For each new read,
  add name, purpose, default, accepted values, scope, read point, reload/restart
  behavior, invalid-value behavior, implementation anchor, and security note.
  Keep the router/progressive-disclosure links reciprocal; do not paste this
  catalogue into resident substrate or procedures.
---
# LingTai environment-variable catalogue

This is the deep reference for environment configuration. Resident prompt layers
only explain the two global Nudge axes. Values are read by the component named in
the table; changing a variable does not imply that an already-running component
reloads it. Never put secrets directly in an environment value when a supported
`*_env`/`env_file` configuration path can be used.

## Product/runtime variables

| Variable | Purpose / default | Accepted values and scope | Read/reload and invalid behavior | Anchor / security |
|---|---|---|---|---|
| `LINGTAI_NUDGE_ENABLED` | Global Nudge publication switch; default `on`. | `on`/`off` (also `true`/`false`/`1`/`0`), all Nudge kinds for one process/workdir. | Read at each Nudge operation/heartbeat; no restart. Invalid falls back to `on` and emits a bounded diagnostic. | `src/lingtai/kernel/nudge/__init__.py`; disabling reminders is not an authorization boundary. |
| `LINGTAI_NUDGE_REPEAT_INTERVAL` | Post-dismiss repeat interval for the same unresolved finding; default `24h`. | Positive duration with `s`, `m`, `h`, or `d`, for example `30m`, `24h`, `2d`; global. | Read at each Nudge operation; no restart. Invalid/zero/negative falls back to `24h` and emits a bounded diagnostic. | `src/lingtai/kernel/nudge/__init__.py`; values affect reminder timing only, not resolution. |
| `LINGTAI_ACTIVE_STUCK_THRESHOLD_S` | ACTIVE no-progress watchdog threshold; default `600` seconds. | Numeric seconds; kernel clamps values below 30 to 30. | Read when the watchdog evaluates a turn; next evaluation sees a changed value. Invalid falls back to `600`. | `src/lingtai/kernel/base_agent/lifecycle.py`; tuning a watchdog can change availability, so operators should record it. |
| `LINGTAI_REFRESH_ENV_OVERWRITE` | One refresh handoff lets edited `env_file` values replace inherited process values; default unset/off. | `1` enables the one-shot overwrite. | Read on boot/refresh setup; consumed/removed after use. Other values are treated as off. | `src/lingtai/cli.py`, `src/lingtai/agent.py`; do not log env-file contents. |
| `LINGTAI_RUNTIME_PYTHON` | Interpreter used for runtime/self-check and host-tool routing; default unset (caller supplies `sys.executable`). | Executable path. | Read by consumers when invoked; restart/relaunch the consumer to change its process interpreter. Invalid/missing is a caller/configuration error, not a fallback to a remote runtime. | `src/lingtai/cli.py` and runtime checks; path is not a credential. |
| `LINGTAI_RUNTIME_VENV` | Active runtime virtualenv hint; default unset. | Local directory path. | Read by host tools when invoked; change takes effect for a new tool/process. Missing is tolerated where the caller has another interpreter. | `src/lingtai/cli.py`; never infer package freshness from an unrelated shell Python. |
| `LINGTAI_AGENT_DIR` | Workdir for an out-of-process MCP/client; default unset and normally injected by the launcher. | Existing local directory. | Read at MCP process start; restart the MCP process after changing. Invalid path fails the MCP/client operation. | `src/lingtai/mcp_servers/_config.py`; local path only, do not expose private workdir contents. |
| `LINGTAI_MCP_NAME` | Name of the active MCP server; default unset. | Registered MCP name. | Read at MCP process start; restart after changing. Invalid/missing fails closed rather than selecting an arbitrary server. | `src/lingtai/mcp_servers/_config.py`; keeps one process bound to one server identity. |
| `LINGTAI_ENV_VERSION` | Environment/config schema marker used by environment setup; default unset. | Version string. | Read by the setup caller; relaunch/setup after changing. Invalid is reported by the setup path. | `src/lingtai/venv_resolve.py`; do not treat a marker as proof of package trust. |
| `LINGTAI_TUI_DIR` | TUI-managed project/config root hint; default unset. | Local directory. | Read by TUI-facing callers at invocation; restart caller after changing. Invalid path fails the caller's lookup. | `src/lingtai/venv_resolve.py`/TUI integration; path may reveal local layout. |
| `LINGTAI_ORIGINATOR` | Non-secret origin label for launched work; default unset. | Short string. | Read at process construction; restart/relaunch to change. Invalid is treated as absent. | `src/lingtai/kernel` launch surfaces; do not use it for authorization. |
| `LINGTAI_FILE_IO_BACKEND` | Selects file-I/O backend; default implementation backend. | Backend name recognized by the wrapper. | Read when file-I/O service is constructed; rebuild/restart service. Unknown value fails closed. | `src/lingtai/services/file_io.py` (where available); backend selection is not a sandbox. |
| `LINGTAI_FILE_IO_SIDECAR` | File-I/O sidecar executable/path hint; default unset. | Local executable/path. | Read at service construction; restart service. Invalid/unavailable sidecar fails the selected path. | File-I/O service adapter; do not grant sidecar broader filesystem authority than intended. |
| `LINGTAI_SEARCH_SIDECAR` | Search sidecar executable/path hint; default unset (pure-Python fallback where supported). | Local executable/path. | Read when search service is constructed; restart service. Invalid sidecar uses the documented fallback or fails the operation; it never downloads one. | Search service adapter; validate executable ownership before use. |
| `LINGTAI_SKIP_RUST_BUILD` | Developer/package-build switch to skip the Rust search-sidecar build; default unset/off. | `1` enables skip in build/test setup. | Read by build setup at build invocation; rerun the build after changing. Invalid values are treated as off. | `setup.py` / wheel tests; do not ship a production wheel claiming a sidecar it did not build. |
| `LINGTAI_REQUIRE_RUST_BUILD` | Developer/package-build switch requiring the Rust search-sidecar build; default unset/off. | `1` enables requirement in build/test setup. | Read by build setup at build invocation; rerun the build after changing. Invalid values fail the required-build path. | `setup.py` / wheel tests; build policy is not runtime authorization. |
| `LINGTAI_SOUL_FLOW_ENABLED` | Enables optional soul-flow capability; default disabled unless the host enables it. | `1`/`0` (implementation also accepts the component's documented boolean forms). | Read when soul capability is bootstrapped; refresh/restart capability after changing. Invalid is treated as disabled. | `src/lingtai/tools/soul`; not a command-execution or approval switch. |
| `LINGTAI_TASK_CARD_MAX_TOOL_ROWS` | Bounds rendered Telegram task-card tool rows; default component limit. | Positive integer. | Read by the Telegram manager when rendering a card; next render sees valid changes. Invalid falls back to the component default. | `src/lingtai/mcp_servers/telegram`; bounds output size, not message authority. |
| `LINGTAI_PROFILE_MIME` | Optional MIME/profile hint for bundled skill/profile transport; default unset. | MIME string. | Read when the profile resource is loaded; reload resource after changing. Invalid is ignored/fails the resource lookup. | `src/lingtai` profile loader; never use it to bypass content validation. |
| `LINGTAI_SKILL_MIME` | Optional MIME hint for skill transport; default unset. | MIME string. | Read at skill-resource load; reload resource after changing. Invalid is ignored/fails lookup. | `src/lingtai` skill loader; content still requires normal skill validation. |
| `LINGTAI_CLOUD_MAIL_CONFIG` | Cloud-mail configuration path/name; default unset. | Local config reference. | Read when cloud-mail integration starts; restart integration after changing. Invalid config fails closed. | Cloud mail adapter; config may contain credentials, so keep file permissions private. |
| `LINGTAI_IMAP_CONFIG` | IMAP MCP config path/name; default unset. | Local config reference. | Read when IMAP MCP starts; restart MCP after changing. Invalid config fails closed. | `src/lingtai/mcp_servers/imap`; do not print config or password. |
| `LINGTAI_FEISHU_CONFIG` | Feishu MCP config path/name; default unset. | Local config reference. | Read when Feishu MCP starts; restart MCP after changing. Invalid config fails closed. | `src/lingtai/mcp_servers/feishu`; keep app secrets out of logs. |
| `LINGTAI_TELEGRAM_CONFIG` | Telegram MCP config path/name; default unset. | Local config reference. | Read when Telegram MCP starts; restart MCP after changing. Invalid config fails closed. | `src/lingtai/mcp_servers/telegram`; protect bot tokens and chat routing. |
| `LINGTAI_WECHAT_CONFIG` | WeChat MCP config path/name; default unset. | Local config reference. | Read when WeChat MCP starts; restart MCP after changing. Invalid config fails closed. | `src/lingtai/mcp_servers/wechat`; protect credentials and contact state. |
| `LINGTAI_WHATSAPP_CONFIG` | WhatsApp MCP config path/name; default unset. | Local config reference. | Read when WhatsApp MCP starts; restart MCP after changing. Invalid config fails closed. | `src/lingtai/mcp_servers/whatsapp`; protect credentials and recipient state. |

## Provider and transport variables

These are adapter/provider controls. The kernel does not reinterpret them as
Nudge policy. Defaults below mean “unset” unless the provider adapter documents a
provider-specific default.

| Variable | Purpose / default | Accepted values and scope | Read/reload and invalid behavior | Anchor / security |
|---|---|---|---|---|
| `LINGTAI_CODEX_TRANSPORT` | Legacy/diagnostic transport hint; normal runtime remains the adapter's default REST path. | Adapter-recognized diagnostic value; default unset. | Read by the provider adapter at session construction; restart session. Unsupported values do not silently switch to an unsafe transport. | `src/lingtai/llm`; do not put bearer tokens in transport hints. |
| `LINGTAI_CODEX_WS` | Legacy WebSocket experiment/diagnostic hint; default unset/off. | Adapter-recognized value; normal production behavior is unchanged when set by itself. | Read at provider-session construction; restart session. Unsupported values are ignored/rejected by the adapter. | `src/lingtai/llm`; transport selection is not a trust boundary. |
| `LINGTAI_CODEX_WS_EPOCH_RESET_TURNS` | Diagnostic WebSocket epoch reset count; default adapter default. | Positive integer. | Read at session construction; restart session. Invalid falls back to adapter default. | Codex adapter; avoid values that cause unexpected session churn. |
| `LINGTAI_CODEX_RESPONSES_TRACE` | Enables bounded Responses-wire tracing for diagnostics; default off. | Adapter-recognized boolean. | Read at session construction; restart session. Invalid is treated as off. | Codex adapter; traces must remain redacted and local. |
| `LINGTAI_CODEX_RESPONSES_TRACE_PATH` | Local destination for Responses-wire diagnostics; default unset/adapter default. | Local path. | Read at session construction; restart session. Invalid/unwritable path fails closed or disables tracing. | Codex adapter; trace files can contain sensitive prompts, so restrict permissions. |
| `LINGTAI_CLAUDE_MANAGED_ROOT` | Claude managed installation root; default host-specific/ unset. | Local directory. | Read by Claude launcher at launch; relaunch after changing. Invalid path fails closed. | Claude adapter; never widen root based on untrusted model text. |
| `LINGTAI_CLAUDE_INTERACTIVE_FIFO` | Claude interactive FIFO path; default unset. | Local FIFO/path. | Read at interactive launch; relaunch after changing. Invalid type/permissions fail closed. | Claude adapter; protect FIFO from other users/processes. |

## Daemon and test-only composition variables

The following variables are intentionally not user-facing product configuration.
They are injected by daemon supervisors, fake backends, or focused tests. They
are catalogued so an audit can distinguish a test hook from a runtime control.
Do not set them in a production agent environment.

| Variable | Purpose / default | Accepted values and scope | Read/reload and invalid behavior | Anchor / security |
|---|---|---|---|---|
| `LINGTAI_DAEMON_CAPSULE_FD` | Supervisor-provided capsule file descriptor; default unset. | Integer FD supplied by the supervisor. | Read once at daemon start; restart daemon. Invalid/missing fails the supervised path. | `src/lingtai/kernel/daemon_supervisor`; never accept an arbitrary inherited FD without supervisor validation. |
| `LINGTAI_DAEMON_CAPSULE_HANDLE` | Windows capsule pipe HANDLE number; default unset. | Integer OS handle inherited via the spawn `handle_list`. | Read/consumed once by the Windows daemon entrypoints, which convert it to the FD wire above; restart daemon. Carries only the handle number, never capsule content. | `src/lingtai/adapters/windows/daemon_supervisor.py`; never accept an arbitrary inherited handle without supervisor validation. |
| `LINGTAI_DAEMON_COMPLETION_FILE` | Daemon completion marker path; default unset. | Supervisor-owned local path. | Read once at daemon start; restart daemon. Invalid path fails completion reporting. | Daemon completion MCP/runner; keep path within the assigned run directory. |
| `LINGTAI_DAEMON_CREDENTIALS_RESTORED` | Internal marker that credential restoration finished; default unset. | Supervisor boolean marker. | Read during daemon startup; restart daemon. Invalid/missing means credentials are not considered restored. | Daemon supervisor; never log restored credential contents. |
| `LINGTAI_DAEMON_DETACHED_SUPERVISOR` | Internal detached-supervisor mode; default unset/off. | Supervisor boolean marker. | Read once at daemon startup; restart daemon. Invalid is treated as off/fails the detached path. | Daemon supervisor; does not authorize external side effects. |
| `LINGTAI_DAEMON_RUN_ID` | Correlates one daemon run; default unset. | Opaque run identifier. | Read at daemon startup and event/token writes; restart daemon to change. Invalid/missing is a run-integrity error where required. | Daemon run directory; do not use as a secret or expose unrelated run IDs. |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM` | Test fake-LLM switch; default off. | Test-only boolean. | Read at test/supervisor construction; restart child. Invalid is off. | Test/supervisor code; never enable in production. |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH` | Test fake-LLM finish mode; default unset. | Test scenario string. | Read at child construction; restart child. Invalid scenario fails the test. | Test/supervisor code; no production effect. |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SCENARIO` | Selects a fake-LLM test scenario; default unset. | Registered test scenario. | Read at child construction; restart child. Unknown scenario fails closed. | Test/supervisor code; never use to bypass real provider checks. |
| `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP` | Fake-LLM test delay; default zero/unset. | Non-negative numeric seconds. | Read at fake backend construction; restart child. Invalid falls back to zero or fails the test. | Test/supervisor code; not a production timeout control. |
| `LINGTAI_FAKE_CLI_REPORT` | Test fake CLI report path/content selector; default unset. | Test-only path/selector. | Read by fake CLI at invocation; rerun test after changing. Invalid fails the test. | `tests/_fake_*`; do not set in production. |
| `LINGTAI_TEST_CONFIG` | Test fixture configuration selector; default unset. | Test-only string/path. | Read when fixture is built; rerun test after changing. Invalid fails fixture setup. | `tests/`; no production behavior. |
| `LINGTAI_TEST_FAKE_CLAUDE_SIGNAL_RECORD` | Test fake Claude signal record path; default unset. | Test-only local path. | Read by fake launcher at invocation; rerun test after changing. Invalid fails the test. | `tests/`; keep artifacts in the test temp directory. |

## Audit notes

* An environment variable is process input, not an authorization grant. Human or
  config-owner approval is still required for configuration writes, refresh,
  downloads, sends, and other consequential actions.
* Invalid values must never be “fixed” by rewriting `init.json`; this catalogue
  describes fallback/failed-read behavior, while the real init reader reports
  parse/materialize/validate/resolve outcomes and leaves user input untouched.
* Secrets are intentionally described as references/paths only. Do not copy
  token values into Nudge payloads, structured logs, reports, or this catalogue.
