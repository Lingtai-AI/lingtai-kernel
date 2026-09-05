---
name: system-settings-inventory-reference
description: >
  Exact read-only System catch-all settings inventory: ownership, effective
  sources, defaults, accepted values, invalid behavior, redaction, timing,
  authorized change procedures, and explicit non-settings.
tags: [lingtai, system, settings, init, llm, environment, read-only]
version: 1.2.0
last_changed_at: "2026-09-04T00:00:00Z"
related_files:
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/adapters/posix/mail.py
  - src/lingtai/auth/codex.py
  - src/lingtai/auth/codex_pool.py
  - src/lingtai/cli.py
  - src/lingtai/init_reader.py
  - src/lingtai/init_schema.py
  - src/lingtai/kernel/config.py
  - src/lingtai/kernel/config_resolve.py
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/openai/adapter.py
  - src/lingtai/llm/service.py
  - src/lingtai/tools/email/CONTRACT.md
  - src/lingtai/tools/system/ANATOMY.md
  - src/lingtai/tools/system/CONTRACT.md
  - src/lingtai/tools/system/settings.py
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - tests/test_system_declared_plugin.py
maintenance: |
  Keep this owner manual aligned with System's ordered SHOW inventory, the
  canonical init/environment readers, and the focused classification tests.
  Add no mutation API; when ownership or runtime resolution changes, update
  the System Anatomy/Contract pair and this procedure together.
---

# System settings inventory

This reference teaches the kernel-level catch-all behind
`system(action="settings", input={})`. System owns a genuine adjustable
LingTai setting only when no other concrete ToolPlugin owns it. SHOW returns
exactly `key`, `current`, `default`, `configurable`, and `comment`; it never
sets, resets, writes, refreshes, or mutates process environment. A row with
`configurable: true` says only that an authorized external procedure below
exists. It does not authorize the caller to perform that procedure.

SHOW resolves one complete fresh snapshot. A malformed/unreadable `init.json`,
active preset, System owner document, or risky-action gate document makes the
whole inventory unavailable—there are no partial rows and
no exception details. Sensitive rows replace both `current` and `default` with
`<redacted>` before JSON serialization.

## Root and manifest inputs

The canonical source is the real `init.json` reader: JSONC parse, active-preset
materialization, provider inheritance, schema validation, and path resolution.
No environment peers are invented for these fields. An active preset replaces
the authored `manifest.llm` block, but its `llm.context_limit` remains
preset-local for fit/authorization checks and is removed before effective init
materialization; the System runtime-policy row reports the separate
environment/v2/default result. Psyche owns configurable prompt pairs through
its separate closed owner document; System neither resolves nor projects them.
Derived `system/manifest.resolved.json` is never authority.

| Key | Default and accepted value | Invalid behavior | Redaction | Application timing |
|---|---|---|---|---|
| `env_file` | absent; UTF-8 dotenv path | Missing file loads nothing; an invalid `init.json` path value fails the canonical read | full | Boot or System refresh; editing the dotenv needs refresh |
| `venv_path` | absent, so launcher-managed resolution applies; venv-root path | An unusable configured root fails launcher validation | full | Full relaunch only |
| `agent_name` | `null`; string or null boot seed | Wrong type fails init validation | none | Creation/full relaunch only; immutable for an existing identity, so `configurable` is false |
| `language` | `en`; string | Wrong type fails init validation | none | System refresh |
| `disable` | `[]`; `list[str]` | A non-list or any non-string entry fails init validation; entries are interpreted by capability composition without coercion or dropping | none | Capability rebuild during System refresh |
| `admin` | `{}`; object | Wrong type fails validation | full authorization map | System refresh |
| `time_awareness` | `true`; boolean | Wrong type fails validation | none | System refresh |
| `timezone_awareness` | `true`; boolean | Wrong type fails validation | none | System refresh |
| `preset.active` / `preset.default` | absent outside a preset block; non-empty strings and members of `allowed` | Missing/non-string/non-member values fail preset validation | full path/reference | Authorized preset workflow plus System refresh |
| `preset.allowed` | absent outside a preset block; non-empty `list[str]` when preset is present | Empty, non-list, or invalid entries fail preset validation | full path/reference list | Authorized preset workflow plus System refresh |
| `summarize_notification_threshold` | `3000`; non-negative integer, `0` disables the threshold hint | Negative or wrong type fails validation | none | System refresh |

The seven ordinary runtime-policy rows are not init/preset rows. They resolve
from valid `LINGTAI_<FIELD>` environment values, then a valid closed-v2
`settings/system.json` field, then the fixed runtime default:

| Key | Fixed default | Accepted environment / v2 value | Application timing |
|---|---|---|---|
| `context_limit` | `272000` conservative service window | positive integer; v2 also accepts `null` for no configured limit | boot and System refresh before rebuilding LLM/session state |
| `snapshot_interval` | `null` (off) | finite positive seconds; environment `off` / v2 `null` disables | boot and System refresh; enabling initializes the snapshot port first |
| `max_rpm` | `60` | integer `>= 0`; `0` disables the request gate | boot and System refresh before rebuilding the LLM service |
| `max_aed_attempts` | `3` | integer `>= 1` | boot and System refresh |
| `aed_timeout` | `360.0` seconds | finite positive number | boot and System refresh |
| `streaming` | `false` | canonical boolean words in the environment / JSON boolean in v2 | boot and System refresh, including the live SessionManager flag |
| `activeness` | `balanced` | non-blank environment string / non-blank string or `null` in v2 | boot and System refresh; compatibility posture only |

Legacy `manifest.context_limit`, `snapshot_interval`, `max_rpm`,
`max_aed_attempts`, `aed_timeout`, `streaming`, and `activeness` are
recognized-and-ignored compatibility data. SHOW never reports them as current
truth and an active preset cannot override these runtime-policy rows. The row
comment routes through the stable `system-manual#runtime-policy-v2` anchor
to the document grammar and authorized procedure below; that router does not
own another copy of this policy.

### Runtime-policy v2 document shape

The same `settings/system.json` file may be a closed v2 document naming any
subset of the seven ordinary runtime-policy fields above plus `cache_miss_budget`
(positive integer; see "Cache-miss budget" below) and `notification_max_chars`
(positive integer; Core still clamps it to 2048–10000 and
`LINGTAI_NOTIFICATION_MAX_CHARS` still wins), for example:

```json
{"schema_version": 2, "context_limit": 200000, "max_rpm": 30, "streaming": true}
```

Booleans never stand in for numbers, `NaN`/`Infinity` are rejected, and an
unknown, duplicate, or invalid key rejects the whole document so nothing is
applied partially. An absent key and an explicit `null` are different: absent
falls through to the fixed default, `null` is the configured value. The
kernel-fixed context-pressure thresholds (0.85 / 1.0 / 3 rounds / 0.75) and the
legacy `molt_*` fields are not settings: naming them makes the document
invalid. The seven ordinary runtime-policy fields are resolved at CLI boot, before
the first LLM service is built, and once on every refresh, so the service,
`AgentConfig`, and the session streaming flag always agree; `init.json` and
`system/manifest.resolved.json` are never rewritten to reflect it. Enabling
`snapshot_interval` by refresh on a running agent initializes the snapshot
repository first; if that fails, snapshots stay off for the process and
`snapshot_initialize_failed` is logged.

After explicit owner/human authorization, edit the intended environment value
at its launcher owner or the corresponding v2 field with File/Shell, preserving
unrelated fields. Follow `refresh-precheck` before applying a change at the
boot/refresh timing in the table, then query System settings again. An
invalid owner document applies no partial subset; this does not authorize
repair, runtime refresh or configuration writes by itself. Cache-budget live
resolution is separate and described below.

### Cache-miss budget

Default-only row illustration (query SHOW for the actual current value):

```json
{"key":"cache_miss_budget","current":2000000,"default":2000000,"configurable":true,"comment":"system-manual#cache-miss-budget"}
```

`current` resolves from a valid live `LINGTAI_CACHE_MISS_BUDGET`, then a live
valid `settings/system.json` (the v1-only document below, or the v2
`cache_miss_budget` field above), then the fixed `2,000,000` default. The
environment value is a positive base-10 integer string; the owner-file value
is a positive JSON integer (a boolean is not an integer here). Invalid
environment input falls through. A missing owner file selects the default; a
present unreadable, malformed, duplicate-key, wrong-version, or otherwise
invalid owner document makes SHOW return the fixed whole-inventory unavailable
failure unless a valid environment value bypasses it. The runtime consumer
retains its existing safe-default fallback on missing, unreadable, malformed
or invalid owner JSON; this is distinct from SHOW failing the whole inventory.
The reader never creates or rewrites the owner file. `configurable: true`
means an authorized owner procedure exists outside SHOW; SHOW itself never
writes, resets, or removes anything. This advisory budget is public, not
sensitive, and is not redacted.

The v1-only document shape (for an agent that only ever needs this one
setting) is:

```json
{"schema_version": 1, "cache_miss_budget": 2000000}
```

Both values must be JSON integers (not booleans), the version must be `1`,
the budget must be positive, and no other or duplicate keys are accepted.

Authorized change procedure: after explicit owner/human authorization, set
`LINGTAI_CACHE_MISS_BUDGET` in the launcher or the agent's configured
`env_file` (an `env_file` edit needs refresh before the running agent sees
it), or use the existing File/Shell capability to write one of the two
document shapes above to `settings/system.json` (remove the file through the
same capability to return to the default), then call
`system(action="settings", input={})` again and verify `current`. If the
environment source still wins, change or remove it at its launcher/`env_file`
owner instead of editing the lower-precedence file repeatedly. Direct
process-env and unshadowed file changes apply on the next metadata snapshot;
an `env_file` edit still needs refresh. Threshold changes and refreshes do
not reset cumulative `token_usage.session.cache_miss_tokens`; only molt does.
The threshold is advisory and never blocks a request. This path is unrelated
to `.notification/system.json`. Legacy `init.json` `manifest.cache_miss_budget`
is ignored and has no runtime effect.

`manifest.summarize_notification_threshold` remains System-owned: it controls
cross-cutting Agent/ToolExecutor result hints, not the Context ToolPlugin's
public summarize action. Conversely, `manifest.pseudo_agent_subscriptions`
belongs to the concrete Email ToolPlugin because CLI composition hands it
directly to `PosixFilesystemMailAdapter`, which resolves the subscription
paths. It is intentionally absent from System SHOW. The Email-owner inventory
uses the generic sensitive-value seam to fully redact both current and default path lists;
System neither duplicates that row nor exposes the paths.

Authorized change procedure: after explicit owner/human authorization, edit
the exact `init.json` field with the existing File or Shell capability. For a
preset-owned LLM/context value, edit the authorized preset outside SHOW or use
the existing `system(action="refresh", input={"preset": ...})` workflow; never
edit the derived resolved manifest or widen `preset.allowed` as a shortcut.
Run the refresh precheck, refresh/relaunch at the timing above, then call SHOW
again. Psyche-owned prompt/file pairs are changed only through the procedure in
`psyche-manual`; changing or removing a pointer never changes or deletes the
referenced file. Existing identity changes use
`system(action="name_set"|"name_nickname")`; editing `manifest.agent_name` is
not a supported rename procedure.

## LLM and provider inputs

Every effective `manifest.llm` axis is System-owned because no LLM ToolPlugin
exists. Precedence is active preset over authored init for the whole block.
The credential path is the exception inside the materialized block: a named
non-empty `api_key_env` value wins inline `api_key`. Initial boot uses only
those two authored sources through `resolve_env_checked`; it does not consult
an invented `{PROVIDER}_API_KEY` fallback. No secret, alias value, header, auth
path, endpoint pool, or credential-bearing URL is ever projected.

| Key | Default and accepted value | Invalid behavior | Projection | Timing |
|---|---|---|---|---|
| `llm.provider` | no default; required string supported by the adapter registry | Missing/wrong type fails init; unknown provider fails adapter construction | literal | LLM rebuild on refresh |
| `llm.model` | no default; required string | Missing/wrong type fails init/provider construction | literal | LLM rebuild on refresh |
| `llm.api_key` | no universal default; string/null plus the credential precedence above | Missing required credentials fail initial adapter construction; a missing named alias uses an authored inline key when present and otherwise remains absent | `<redacted>` | LLM rebuild on refresh |
| `llm.api_key_env` | absent; environment-variable name | Wrong type, or alias without inline key and without `env_file`, fails canonical validation | `<redacted>` | Resolved at boot/refresh |
| `llm.base_url` | provider-owned when omitted; string/null | Provider validation owns unsupported endpoints | `<redacted>` because URLs may embed credentials | LLM rebuild on refresh |
| `llm.compact_threshold` | no universal owner default; positive integer or null. Official OpenAI and `_custom` names (`custom`, `grok`, `qwen`, `kimi`) on effective `openai` compatibility have selected-route default `100000`; omission consumes it and explicit compact null is forwarded as disabled. Canonical provider-default normalization filters authored `api_compat: null`, so `_custom` still defaults to OpenAI and forwards that compact null. Exact `anthropic`/`gemini` compatibility ignores it. Other admitted non-null compatibility values fall through to `OpenAIAdapter` without forwarding this axis, so current/default are both `100000`. DeepSeek current is an authored positive value or null and default is null. Gemini/other ignored factories and native `codex`/`codex-pool`/`codex_pool` current/default are null; native Codex uses separate `codex_compact_token_limit` | Non-positive integer or wrong type fails validation | selected-adapter current and default | Adapter rebuild on refresh |
| `llm.wire_api` | Selected-route truth, not one universal default. Omitted official OpenAI, DeepSeek, and `_custom` OpenAI/fallback routes select `chat_completions`; omitted MiMo selects `responses`; an explicit selector that canonical init admits is forwarded and preserved on those factories (notably MiMo preserves explicit `auto`). Codex aliases ignore the generic selector and always report forced `responses`. `_custom` Anthropic/Gemini routes and every other factory that ignores this axis report null current/default | Unknown values fail init validation. Canonical init admits non-`auto` only for official OpenAI, DeepSeek, and exact `custom`+OpenAI compatibility; `auto` is admitted everywhere but ignored routes still report null | selected-factory current and omitted default | Adapter rebuild on refresh |
| `llm.inject_reasoning_fallback` | Selected-factory truth. Official OpenAI and `_custom` aliases on exact/default OpenAI compatibility forward an authored boolean; omission/null consults `LINGTAI_INJECT_REASONING_FALLBACK`, whose invalid/unset default is `true`. A malformed finite `_custom` selector still chooses OpenAI but `_custom` does not forward the authored axis, so the adapter consults that environment resolver. DeepSeek forwards an authored boolean and otherwise pins `true`, independent of that environment variable. Every ignoring factory reports null current/default | Wrong init type fails canonical validation; ignored factories add no provider-specific validation | selected-factory boolean/default or null | Adapter construction on refresh |
| `llm.reasoning_effort_vocab` | official OpenAI and `_custom` names (`custom`, `grok`, `qwen`, `kimi`) on effective `openai` compatibility have selected-route default `openai`; string/null (`seven_tier` selects retained alternate mapping), with omitted and explicit vocabulary null consuming `openai`. Canonical normalization filters authored compat null but retains a non-null authored vocabulary, so `_custom` forwards (for example) `seven_tier`. Exact `anthropic`/`gemini` compatibility ignores the axis. Other admitted non-null compatibility values fall through to `OpenAIAdapter` without forwarding the axis, so current/default remain `openai`. DeepSeek's provider policy, Gemini/other ignored factories, and all native Codex spellings ignore this generic axis, so current/default are null | Wrong type fails validation; other strings retain the OpenAI behavior only when the effective OpenAI route forwards them | selected-adapter current and default | Adapter rebuild on refresh |
| `llm.prompt_cache_namespace` | Official OpenAI and `_custom` aliases on exact/default OpenAI compatibility forward an authored string and otherwise use null. A malformed finite `_custom` selector chooses OpenAI without forwarding this axis, so it remains null. DeepSeek forwards an authored string and otherwise uses `deepseek`. Every ignoring factory reports null current/default | Wrong init type fails canonical validation; ignored factories add no provider-specific validation | selected-factory namespace/default only, never prompt/cache content | Adapter rebuild on refresh |
| `llm.service_tier` | Codex aliases only: absent is null; authored `fast` is reported as `fast` and the factory normalizes it to private wire `priority`. Every non-Codex route ignores the axis and reports null current/default | Wrong type fails init. An unsupported Codex value fails canonical factory validation and SHOW; unsupported OpenAI/custom/other values are ignored and are not predicted to fail | selected-factory canonical authored value | Adapter rebuild on refresh |
| `llm.thinking` | selected-route default comes from canonical `build_agent_config` hydration with thinking omitted: provider-owned `default` for Codex aliases/DeepSeek and legacy `high` otherwise | Unsupported provider/model/wire or effort fails canonical/provider validation | literal hydrated effort only | Session rebuild on refresh |
| `llm.api_compat` | Every name bound to `_custom` (`custom`, `grok`, `qwen`, `kimi`) has factory default `openai`; omission and explicit null both select/report `openai`. Exact `anthropic` and `gemini` report those adapter routes. Every other finite accepted value—including case variants, unknown strings, numbers, lists, and objects—selects and reports canonical public `openai`, because that is the custom adapter's fallback. Other registered factories ignore this axis and report null current/default | Any non-finite float at any nesting depth fails canonical init validation; other finite compatibility values remain deliberately tolerant and select the fallback above | effective selected adapter route and default, never malformed authored syntax | Adapter rebuild on refresh |
| `llm.codex_session_anchor` | derived from the resolved agent `init.json` path for Codex | Explicit value is an internal/testing escape, not an authorized production setting | `<redacted>` | Adapter rebuild; `configurable` is false |
| `llm.codex_auth_path` | provider-owned legacy auth path when absent; path-like override | Missing/unreadable/invalid auth fails the request/provider path closed | `<redacted>` | Adapter rebuild/request-owned reread |
| `llm.codex_auth_pool_path` | provider/TUI pool resolution when absent; path-like override | Invalid pool fails the provider account-source path closed | `<redacted>` | Adapter rebuild and request-bound account selection |
| `llm.codex_base_urls` | absent means single `base_url`; string or list accepted by the Codex adapter | Invalid/empty entries follow the adapter's pool validation/fallback | `<redacted>` | Adapter rebuild; selection rotates only at the documented molt boundary |
| `llm.default_headers` | `{}` user headers; JSON object in normal use | Non-object values are not forwarded as user headers; provider construction owns final validation | `<redacted>` including names and values | Adapter rebuild on refresh |

Authorized change procedure: after explicit owner/human authorization, update
the selected preset (when active) or `init.json`, keep credentials in the
supported private env/file source, run the refresh precheck, refresh, and SHOW
again. Never print a before/after credential, header, prompt, token, auth path,
or endpoint-pool value. SHOW never edits a preset, credential file, header map,
or process environment.

## Kernel environment controls

These are genuine kernel/LLM settings without another ToolPlugin owner. Direct
process-environment changes apply at the canonical read point; an `env_file`
edit first needs System refresh. Missing/invalid values fall back exactly as
shown. `LINGTAI_CODEX_WS` is a compatibility alias under the single
`llm.codex_transport` row: a non-empty canonical
`LINGTAI_CODEX_TRANSPORT` always decides first.

| Key (environment) | Default; accepted values; invalid behavior | Current read/application timing | Projection |
|---|---|---|---|
| `nudge.enabled` (`LINGTAI_NUDGE_ENABLED`) | on; `on/off`, `true/false`, `1/0`; invalid → on | Every Nudge operation | literal boolean |
| `nudge.repeat_interval_seconds` (`LINGTAI_NUDGE_REPEAT_INTERVAL`) | `86400`; positive duration with `s/m/h/d`; invalid → 24h | Every Nudge operation | numeric seconds |
| `nudge.folder_size_gb` (`LINGTAI_NUDGE_FOLDER_SIZE_GB`) | `5`; positive finite decimal GB; invalid → 5 | Every folder-size evaluation | number |
| `lifecycle.active_stuck_threshold_seconds` (`LINGTAI_ACTIVE_STUCK_THRESHOLD_S`) | `600`; finite numeric seconds floored to 30; parse failure or non-finite value → 600 | Each ACTIVE watchdog evaluation | number |
| `lifecycle.agent_alive_threshold_seconds` (`LINGTAI_AGENT_ALIVE_THRESHOLD_SEC`) | `10`; positive finite seconds; invalid → 10 | Kernel import/start (restart required); SHOW reports the imported effective constant | number |
| `prompt.tool_prose_section_enabled` (`LINGTAI_TOOL_PROSE_SECTION_ENABLED`) | off; `1/true/yes/on`; everything else off | Every prompt rebuild/provider payload | boolean |
| `prompt.system_prompt_pressure_ratio` (`LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO`) | `0.4`; finite `0 < value < 1`; invalid → 0.4 | Every metadata snapshot | number |
| `session_stats.refresh_seconds` (`LINGTAI_SESSION_STATS_REFRESH_SECONDS`) | `5`; positive finite seconds; invalid → 5 | Every Agent Record throttle check | number |
| `session_stats.daemon_limit` (`LINGTAI_SESSION_STATS_DAEMON_LIMIT`) | `1000`; positive integer; invalid → 1000 | Every Agent Record daemon aggregation | integer |
| `security.risky_action_gate` (`LINGTAI_RISKY_ACTION_GATE`) | off unless truthy env or `.security/gate_config.json` exists; `1/true/yes/on`; other env values off | Every gate-config load; malformed present document fails the guarded path and SHOW closed | enabled boolean only; never policy paths/lists |
| `logging.console_debug` (`LINGTAI_VERBOSE`) | off; exactly `1` enables boot DEBUG console logging; other values off | Agent boot/full relaunch | boolean |
| `runtime.tool_batch_memory_relief` (`LINGTAI_DAEMON_MEMORY_RELIEF`) | off; exactly `1` after surrounding whitespace enables the existing best-effort macOS relief hook; every other value, unsupported platform/symbol, or relief failure is an off/no-op result | Read after every global `ToolExecutor` batch, including ordinary main-agent and daemon paths; no restart for a direct process-environment change | boolean only; no allocator detail |
| `llm.codex_tui_dir` (`LINGTAI_TUI_DIR`) | env path wins; when unset, `~/.lingtai-tui` is expanded with the current user home. Runtime accepts any path string and applies only `Path.expanduser` (relative paths remain relative; an explicit empty string denotes `.` rather than the default) | Resolved lazily through `resolve_codex_tui_dir` only when SHOW/provider construction asks for it; module import and static declaration construction perform no home lookup. Change the launcher or `env_file` and fully relaunch before verification | `<redacted>` for both current and default |
| `llm.codex_transport` (`LINGTAI_CODEX_TRANSPORT`, alias `LINGTAI_CODEX_WS`) | REST; canonical `websocket/ws` or `rest/http/https`; alias truthy enables WS only when canonical is empty; invalid → REST | Adapter/session construction | literal `rest`/`websocket` |
| `llm.codex_ws_epoch_reset_turns` (`LINGTAI_CODEX_WS_EPOCH_RESET_TURNS`) | `0`; non-negative integer; invalid → 0 | Session construction | integer |
| `llm.codex_responses_trace` (`LINGTAI_CODEX_RESPONSES_TRACE`) | off; `1/true/yes/on`; other values off | Each trace-path decision | boolean |
| `llm.codex_responses_trace_path` (`LINGTAI_CODEX_RESPONSES_TRACE_PATH`) | trace default when enabled; local path | Each trace-path decision; unused while trace is off; write failure disables/fails the diagnostic path | `<redacted>` |
| `llm.read_timeout_seconds` (`LINGTAI_LLM_READ_TIMEOUT`) | `300`; positive finite seconds; invalid → 300 | Each OpenAI-compatible/Anthropic HTTP timeout build | number |

Authorized environment procedure: after explicit deployment-owner approval,
change the launcher environment or the agent's configured `env_file`; do not
mutate `os.environ` through a tool. Refresh when the source is `env_file`, and
restart only where the table says import/boot/session construction. Call SHOW
again and verify the effective value. Changing a threshold never grants access,
cleans files, resets counters, or authorizes a risky operation.

For `LINGTAI_TUI_DIR`, use that launcher procedure and a full relaunch. The
kernel does not create or validate the directory eagerly: an unexpandable `~`
can fail adapter construction, while a missing directory or unreadable/invalid
`codex-auth.json` / `codex-auth-pool.json` fails the later Codex account or
request path closed. Never print the resolved directory or credential paths;
SHOW fully redacts the env-selected and fallback paths.

## Explicit non-settings and exclusions

These classifications are tested against the canonical schemas/registry so
future fields cannot vanish silently:

- Concrete ToolPlugin owners stay out of System: Soul; Shell; Daemon;
  Notification; File/search sidecars; Vision; Web; Task Card; Plugin/Psyche;
  Skills; LingTai character; MCP, curated addons, and their config/session
  paths. Psyche owns the live Pad prompt inputs at root `pad`/`pad_file` and
  the six configurable system-prompt inputs in `settings/psyche.json`.
  `manifest.capabilities`, `manifest.plugins`, root `addons`/`mcp`, and root
  `lingtai`/`lingtai_file` therefore are not System rows.
- Inert/compatibility **init inputs** are not settings sources:
  `manifest.context_limit`, `manifest.snapshot_interval`, `manifest.max_rpm`,
  `manifest.max_aed_attempts`, `manifest.aed_timeout`, `manifest.streaming`,
  `manifest.activeness`, `manifest.llm.codex_thread_salt`, and nested init
  `manifest.llm.context_limit`, `manifest.max_turns`, context-serialization
  template fields, retired molt/stamina fields, and retired prompt/soul fields.
- Kernel-fixed context-pressure thresholds, the hidden idle-sleep timeout, and
  fixed tool-loop safety limits are code policy rather than settings.

Do not infer product ownership from a registry source-path cell: the concrete
ToolPlugin rule and System catch-all rule above are authoritative.

### Concrete ToolPlugin environment exclusions

These registered production variables belong to concrete tools/integrations,
not System:

- `LINGTAI_CLAUDE_INTERACTIVE_FIFO`
- `LINGTAI_CLAUDE_MANAGED_ROOT`
- `LINGTAI_CLOUD_MAIL_CONFIG`
- `LINGTAI_DAEMON_MANAGER_POOL_SIZE`
- `LINGTAI_DAEMON_MAX_TURNS`
- `LINGTAI_DAEMON_SYSTEM_PROMPT_BUDGET_CHARS`
- `LINGTAI_FEISHU_CONFIG`
- `LINGTAI_FILE_IO_BACKEND`
- `LINGTAI_FILE_IO_SIDECAR`
- `LINGTAI_IMAP_CONFIG`
- `LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS`
- `LINGTAI_NOTIFICATION_MAX_CHARS`
- `LINGTAI_SEARCH_SIDECAR`
- `LINGTAI_SHELL`
- `LINGTAI_SOUL_FLOW_ENABLED`
- `LINGTAI_TASKCARD_POLL_INTERVAL`
- `LINGTAI_TELEGRAM_CONFIG`
- `LINGTAI_TOOL_TIMEOUT_MAX_SECONDS`
- `LINGTAI_WEB_ENGINE`
- `LINGTAI_WEB_MAX_CHARS`
- `LINGTAI_WECHAT_CONFIG`
- `LINGTAI_WHATSAPP_CONFIG`
- `LINGTAI_WHATSAPP_SESSION_DIR`

### Injected or handoff environment exclusions

These values describe one launcher/process edge, descriptor, or run identity,
not an adjustable kernel policy:

- `LINGTAI_AGENT_DIR`
- `LINGTAI_DAEMON_CAPSULE_FD`
- `LINGTAI_DAEMON_CAPSULE_HANDLE`
- `LINGTAI_DAEMON_COMPLETION_FILE`
- `LINGTAI_DAEMON_MANAGER_TOKEN`
- `LINGTAI_DAEMON_RUN_ID`
- `LINGTAI_DAEMON_RUN_DIR`
- `LINGTAI_DERIVED_AVATAR_EXECUTION`
- `LINGTAI_DRIVER_AUTHORITY_FD`
- `LINGTAI_MCP_NAME`
- `LINGTAI_REFRESH_ENV_OVERWRITE`
- `LINGTAI_RUNTIME_PYTHON`
- `LINGTAI_RUNTIME_VENV`

### Build-only environment exclusions

- `LINGTAI_REQUIRE_RUST_BUILD`
- `LINGTAI_SKIP_RUST_BUILD`

### Test-only environment exclusions

- `LINGTAI_AVATAR_BOOT_WAIT_SECONDS`
- `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM`
- `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH`
- `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SCENARIO`
- `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP`
- `LINGTAI_FAKE_APP_SERVER_MODE`
- `LINGTAI_FAKE_CLI_REPORT`
- `LINGTAI_RUN_LIVE_KIMI_CODE`
- `LINGTAI_TEST_CONFIG`
- `LINGTAI_TEST_FAKE_CLAUDE_SIGNAL_RECORD`
