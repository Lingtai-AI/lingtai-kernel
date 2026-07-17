---
related_files:
  - ANATOMY.md
  - src/lingtai/__init__.py
  - src/lingtai/__main__.py
  - src/lingtai/agent.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/shell.py
  - src/lingtai/adapters/shell_process.py
  - src/lingtai/adapters/shell_state_lock.py
  - src/lingtai/adapters/refresh_watcher.py
  - src/lingtai/adapters/lifecycle_clock.py
  - src/lingtai/auth/ANATOMY.md
  - src/lingtai/cli.py
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/registry.py
  - src/lingtai/CONTRACT.md
  - src/lingtai/init.jsonc
  - src/lingtai/init_reader.py
  - src/lingtai/kernel/nudge/init_config.py
  - src/lingtai/init_schema.py
  - src/lingtai/intrinsic_skills/__init__.py
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/substrate-manual/SKILL.md
  - src/lingtai/llm/ANATOMY.md
  - src/lingtai/mcp_catalog.json
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/wechat/manager.py
  - src/lingtai/network.py
  - src/lingtai/presets.py
  - src/lingtai/prompts/ANATOMY.md
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/venv_resolve.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/snapshot/ANATOMY.md
  - tests/test_agent_preset_manifest.py
  - tests/test_cli.py
  - tests/test_deep_refresh.py
  - tests/test_kernel_migrate.py
  - tests/test_venv_resolve.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# lingtai

PyPI wrapper package — `Agent(BaseAgent)` with composable capabilities, preset materialization, CLI, and public re-exports.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | Role |
|---|---|
| `__init__.py` | Lazy public API facade — re-exports every name in ``__all__`` on first access via PEP-562 ``__getattr__`` from its canonical source module (``__init__.py:18-110``). ``import lingtai`` loads only the stdlib and the package version; heavy implementation modules are resolved lazily. |
| `__main__.py` | `python -m lingtai` → `cli.main()` |
| `agent.py` | **THE key file.** `Agent(BaseAgent)` — layer-2 agent with capability composition, preset swap, MCP, init.json refresh, and default POSIX event-journal + notification-store + agent-presence + workdir-lease + snapshot/source-revision injection for outer callers (it selects the platform lease via `lingtai.adapters.workdir_lease.select_workdir_lease` when `working_dir` is present and no `workdir_lease` was passed, and constructs `PosixAgentPresenceStoreAdapter(working_dir)` when no `agent_presence` was passed — see `kernel/agent_presence/CONTRACT.md`). It selects the refresh-watcher capability through `lingtai.adapters.refresh_watcher.select_refresh_watcher` when no watcher is injected. It also constructs the portable `SystemLifecycleClockAdapter()` when no `lifecycle_clock` was passed (no `working_dir` needed — see `kernel/lifecycle_clock/CONTRACT.md`). Selection and `BaseAgent` construction share one guard that closes the wrapper-owned journal best-effort while preserving the original failure. Tool schemas registered here carry package ownership into the inherited BaseAgent inventory renderer; `Agent` no longer duplicates tool-inventory rendering, so package glossaries are appended once in the kernel path. |
| `adapters/posix/` | Narrow production POSIX adapter package for the JSONL + SQLite event journal, filesystem mail transport, notification store, POSIX workdir lease, fixed-command Git snapshot/source-revision adapter, and POSIX avatar launcher. See `adapters/posix/ANATOMY.md`. |
| `adapters/lifecycle_clock.py` | The one portable production `SystemLifecycleClockAdapter` for the Core-owned `LifecycleClockPort` — direct `wall_seconds()`→`time.time()` / `monotonic_seconds()`→`time.monotonic()`, no caching or policy. Not POSIX (no filesystem/`fcntl`/platform selection), so it sits at the top of `adapters/` rather than under `adapters/posix/`; its promise/navigation are owned by the kernel `lifecycle_clock/` governed pair (`src/lingtai/kernel/lifecycle_clock/CONTRACT.md` + `ANATOMY.md`). |
| `cli.py` | `lingtai-agent run <dir>` / `lingtai-agent check-caps` / `lingtai-agent log ...` / `lingtai-agent maintenance cleanup <target>` entry points; the `run` composition root performs a post-stop hard exit only when existing worker-poison state would otherwise keep the old process alive and block the refresh watcher |
| `network.py` | Read-only network topology crawler — avatar/contact/mail edge discovery |
| `presets.py` | Compatibility shim re-exporting the kernel preset library (`lingtai.kernel.presets`) |
| `init.jsonc` / `init_reader.py` / `init_schema.py` | Kernel canonical shape plus the one real parse → materialize → validate → resolve reader. `InitReadOutcome` reports fully-effective, ignored-field, or failed reads with typed PASS/NUDGE/BLOCKED/UNKNOWN shape evidence without rewriting user-owned init.json; `validate_init()` remains the schema validator. See `CONTRACT.md`. |
| `venv_resolve.py` | Python venv resolution — explicit `init.json` venv → global runtime → auto-create, plus kernel-owned `.lingtai-env.json` marker check/stamp semantics for TUI and kernel callers |
| `intrinsic_skills/__init__.py` | Standalone skill bundles (manuals plus sidecar scripts/assets, e.g. the `lingtai-kernel-anatomy` checker and benchmark) copied verbatim into `.library/intrinsic/capabilities/` |
| `mcp_servers/` | Curated MCP server implementations shipped in the `lingtai` distribution and launched by `mcp_catalog.json` via `python -m lingtai.mcp_servers.<name>`; see `mcp_servers/ANATOMY.md` for the bundled `SKILL.md` manual-action and sidecar packaging contract. Stateful curated servers may own per-agent sidecars: the WeChat manager checkpoints `wechat/state.json` cursor progress and `wechat/inbox_seen.json` replay guards in `mcp_servers/wechat/manager.py:266` and `mcp_servers/wechat/manager.py:912`. |

### Key functions / classes

**`agent.py`** — `Agent(BaseAgent)`: `__init__` :38 (accept `capabilities=` + `disable=`, expand groups, `apply_core_defaults`, decompress addons, setup caps, install manuals, load MCP) · `_setup_capability` :161 · `_persist_llm_config` :136 · `_install_intrinsic_manuals` :183 · `_build_system_prompt` :368 / `_build_system_prompt_batches` :386 (pass the init-prompt contract's third-party `base_prompt` (`self._base_prompt`) to the kernel builder, which renders it after raw `principle` and before the rest of Batch 1) · `_load_mcp_from_workdir` :398 (also tracks specs in `_mcp_init_specs`) · `_retry_failed_mcps` :550 (re-spawn dead MCPs on `system(refresh)` — issue #34) · `_read_init` :1192 delegates the shared `lingtai.init_reader.read_init` path, then publishes the real reader's effective secret-redacted manifest to `system/manifest.resolved.json` via `lingtai.kernel.workdir.write_resolved_manifest` — issue #259; it never rewrites user-owned init.json. · `_setup_from_init` :1338 (**full reconstruct** — shared by boot and live refresh; reads `manifest.disable` and re-applies `apply_core_defaults`) · `_activate_preset` :1261 (runtime swap, atomic write) · `_reload_prompt_sections` :1602 (init-prompt contract: the externally changeable prompt surface is exactly `base_prompt`/`covenant`/`comment`. Resolves `base_prompt` into `self._base_prompt` (mirrored to `system/base_prompt.md`), writes `covenant`/`rules` plus kernel-owned `substrate`/`principle`/`procedures` (init overrides ignored) and `guidance.json`, then disk-sourced `brief` and `comment`; delegates `character` to `_lingtai_load` and `pad` to `_pad_load` — the canonical composers — so boot/refresh/molt are consistent and hook-order-independent) · `connect_mcp` :771 / `connect_mcp_http` :826 · `start` :764 / `stop` :875

**`cli.py`**: `load_init` :26 · `build_agent` :82 · `run` :226 · `_force_exit_if_worker_poisoned` :276 · `_handle_log_command` :319 · `_handle_maintenance_command` :369 · `main` :425

**`presets.py`**: compatibility re-export shim (`presets.py:1-21`); implementation lives in `lingtai.kernel.presets` (`discover_presets_in_dirs` :177 · `load_preset` :232 · `materialize_active_preset` :360 · `expand_inherit` :580).

**`init_reader.py` / `init_schema.py`**: `read_init` is the shared parse → compatibility classify → materialize → prepare → validate → resolve path; `InitReadOutcome` reports `FULLY_EFFECTIVE`, `READ_OK_WITH_IGNORED_FIELDS`, or `READ_FAILED` plus typed `PASS`/`NUDGE`/`BLOCKED`/`UNKNOWN` shape evidence without rewriting user-owned init.json. `manifest.capabilities.bash` is mapped in memory to canonical `shell` and differing dual values fail closed. `validate_init` remains the strict schema validator; legacy/deprecated fields are diagnosed as ignored paths rather than stripped. `manifest.llm.compact_threshold` and positive `manifest.cache_miss_budget` remain validated in `init_schema.py`.

**`network.py`**: `build_network` :310 · `_discover_agents` :147 · `_build_avatar_edges` :172

**`venv_resolve.py`**: `resolve_venv` :74 · `venv_python` :101 · `_is_default_runtime_dir` :108 · `_test_venv` :118 · `_env_marker_status` :298 · `_env_marker_status_detail` :303 · `_remove_mismatched_managed_venv` :353 · `_env_marker_main` :362

> Config-resolution helpers (`load_jsonc`/`resolve_env`/`resolve_paths`/`_resolve_capabilities`) and preset-connectivity probing (`check_connectivity`/`check_many`) live in the kernel — import directly from `lingtai.kernel.config_resolve` / `lingtai.kernel.preset_connectivity`. The former wrapper-side compatibility shims were removed (no back-compat shims per repo policy).

## Connections

**Inbound:** `lingtai-tui` calls `cli.run()` to boot agents; imports `load_preset`, `discover_presets_in_dirs` for UI. Kernel's `BaseAgent` is the parent class.

**Outbound — kernel:** `lingtai.kernel.base_agent.BaseAgent`, `.config.AgentConfig`, `.event_journal.EventJournalPort`, `.mail_transport.MailTransportPort`, `.workdir_lease.WorkdirLeasePort`, `.notification_store.NotificationStorePort` (S4: capability-native persistence for `.notification/` channel mirrors; see `kernel/notification_store/CONTRACT.md`), `.agent_presence.AgentPresenceStorePort` (own-heartbeat + foreign liveness; see `kernel/agent_presence/CONTRACT.md`), `.lifecycle_clock.LifecycleClockPort` (S7b: wall/monotonic lifecycle time; see `kernel/lifecycle_clock/CONTRACT.md`), `.snapshot.{SnapshotPort,SourceRevisionPort}` (S5: workdir capture and bounded source identity; see `kernel/snapshot/CONTRACT.md`), `.prompt.build_system_prompt`, `.handshake.resolve_address`, and the shared `lingtai.init_reader.read_init` / `lingtai.kernel.workdir.write_resolved_manifest` path. Legacy migration modules remain outside this production reader path; see `../lingtai/kernel/migrate/CONTRACT.md` for their retained historical/test surface.

**Outbound — adapters:** the CLI composition root injects `lingtai.adapters.posix.mail.PosixFilesystemMailAdapter` (the production `MailTransportPort` implementation, back-compat public name `FilesystemMailService`), `lingtai.adapters.posix.event_journal.PosixJsonlEventJournalAdapter`, `lingtai.adapters.posix.notification_store.PosixNotificationStoreAdapter` (S4: the production `NotificationStorePort` implementation), `lingtai.adapters.posix.git_cli.PosixGitCliAdapter` (S5: distinct workdir and running-source instances), the portable `lingtai.adapters.lifecycle_clock.SystemLifecycleClockAdapter` (S7b: the production `LifecycleClockPort` implementation, constructed by `Agent` fallback and explicitly by `cli.build_agent`), and — via `lingtai.adapters.workdir_lease.select_workdir_lease` — the production `WorkdirLeasePort` (`lingtai.adapters.posix.workdir_lease.PosixWorkdirLeaseAdapter`) for both agent construction and the `log rebuild` command; and — via `lingtai.adapters.refresh_watcher.select_refresh_watcher` — the production `RefreshWatcherPort` for `Agent` and CLI construction. Both selectors fail loud on unsupported platforms. The avatar capability
selects its POSIX production launcher lazily through
`lingtai.adapters.avatar_launcher.select_avatar_launcher`; unsupported Windows
selection fails loudly and no Windows mechanism is wired.

**Cross-module:** `agent.py` → `lingtai.tools.registry.{setup_capability,INTRINSICS,CORE_DEFAULTS}`, `services.mcp_registry.{decompress_addons,read_registry}`, `services.mcp_inbox.MCPInboxPoller`, `services.mcp.{MCPClient,HTTPMCPClient}`, `llm.service.LLMService`, `presets`, `lingtai.kernel.config_resolve`, `init_schema`. `cli.py` → `agent.Agent`, `lingtai.tools.registry.{CORE_DEFAULTS,get_all_providers}`, `lingtai.kernel.config_resolve`, `presets`.

**Agent → BaseAgent:** Three-layer hierarchy: `BaseAgent` (kernel) → `Agent` (capabilities) → `CustomAgent` (domain). Agent adds capability registration, MCP auto-loading, preset swap, full init.json reconstruct, and composes `PosixJsonlEventJournalAdapter`, `PosixNotificationStoreAdapter`, and distinct `PosixGitCliAdapter` snapshot/source-revision instances for callers that did not supply those dependencies (`agent.py:115-151`).

**Capability registration:** `setup_capability()` in `lingtai/tools/registry.py`; the registry is `BUILTIN_TOOLS` (per-tool module paths under `lingtai.tools.<pkg>`) plus `CORE_DEFAULTS` (which boot automatically). Agent calls `apply_core_defaults` + `_setup_capability` (agent.py) during `__init__` and `_setup_from_init`. Hosts disable defaults via the `disable=[...]` kwarg or `manifest.disable` in init.json. The five mandatory intrinsics are injected separately as `BaseAgent(intrinsics=lingtai.tools.registry.INTRINSICS)`.

**Agent init reader + preset materialization:** `cli.load_init` (boot) and `Agent._read_init` (refresh) are composition roots that both delegate the shared `lingtai.init_reader.read_init` parse/materialize/validate/resolve path; neither constructs a migration workspace or rewrites user-owned init.json. Then `materialize_active_preset` (`lingtai/kernel/presets.py`) reads `manifest.preset.active`, loads preset via the **required injected preset-loader callback** (the wrapper module-level `agent.load_preset`, whose production read callback is migration-free), and substitutes `llm`+`capabilities` into manifest before validation. Daemon/system tools resolve presets through the fail-loud `BaseAgent.load_preset` hook (`Agent` sets `_preset_loader = agent.load_preset`); preset materialization mutates only the in-memory effective mapping and the existing redacted manifest artifact is derived separately. The preset owns explicit opt-in capabilities, but per-agent init.json kwargs survive in two ways: (1) for capabilities the preset *also* enables, init.json wins key-by-key; (2) for always-on `CORE_DEFAULTS` capabilities the preset *omits* (daemon, bash, knowledge, …), init.json kwargs are carried forward so `apply_core_defaults` doesn't re-add an empty entry and lose e.g. `daemon.max_emanations`. Non-core optional caps the preset omits are dropped (the swap). `CORE_DEFAULTS` lives in `lingtai.tools.registry` and is injected via the `core_defaults=` arg by both callers (`agent._read_init` :1224, `cli.load_init` :62) — the kernel does not import the `lingtai.tools` package. `skills.paths` additionally append-merges (preset defaults first). For the LingTai-agent-facing preset runtime model (raw vs. resolved `init.json`, preset identity, the two catalogs, main-agent swap/refresh, and the daemon task/CLI distinction), read `src/lingtai/intrinsic_skills/system-manual/SKILL.md` → `reference/substrate-manual/SKILL.md` §11 — the canonical detailed reference this Anatomy routes coding agents toward.

## Composition

Parent: `src/lingtai/` under `lingtai-kernel/src/` alongside `lingtai/kernel/` (kernel package) and the `lingtai/tools/` package (concrete built-in tools; see `tools/ANATOMY.md`). Siblings: `llm/`, `services/`, `auth/`. See `../ANATOMY.md`.

## State

| Path | When | What |
|---|---|---|
| `<workdir>/init.json` | `_activate_preset` :1254 (explicit preset action only), `init_reader.py` read path | User-owned input. Boot/refresh parse, materialize, validate, and resolve in memory; the reader never strips, canonicalizes, persists venv paths, or otherwise rewrites this file. |
| `<workdir>/logs/{events.jsonl,log.sqlite}` | `Agent.__init__` :115-154 and `cli.build_agent` :125-141 | Authoritative structured event JSONL plus derived SQLite query sidecar, owned by the injected POSIX adapter. |
| `<workdir>/system/llm.json` | `_persist_llm_config` :136 | LLM provider/model/base_url for revive |
| `<workdir>/system/manifest.resolved.json` | `_read_init` :1169 via `lingtai.kernel.workdir.write_resolved_manifest` | Derived runtime artifact (issue #259): fully-resolved manifest (preset materialized, validated, paths resolved) with secret-bearing keys removed, plus `schema`/`generated_at`/`source`/`preset` metadata. Atomic write, regenerated on every boot/refresh/molt-reload; init.json is never written back. |
| `<workdir>/system/{base_prompt,covenant,principle,substrate,procedures,brief,rules,pad,lingtai}.md` + `system/guidance.json` + `pad_append.json` | `_reload_prompt_sections` :1602 | Prompt sections from init.json + disk. **Init-prompt contract:** the externally changeable system-prompt surface is exactly `base_prompt`, `covenant`, and `comment`. `base_prompt` is the third-party (application / recipe / preset) injection point: resolved from `data["base_prompt"]` (inline or `base_prompt_file`) into `self._base_prompt`, mirrored to `system/base_prompt.md`, and rendered by the kernel builder after the raw `principle` section and before the rest of Batch 1 (it is NOT a prompt-manager section). `covenant.md`→`covenant` (operator contract). `lingtai.md`→`character` (via `_lingtai_load`); `pad.md`+`pad_append.json`→`pad` (via `_pad_load`). `character` is the agent's self-authored identity (灵台): the required init seed field `lingtai` (inline or `lingtai_file`) seeds `system/lingtai.md` on boot/refresh (covenant-style disk mirror — once self-authored the on-disk file wins; an empty init `lingtai` does not blank it). It is distinct from `covenant`, from the third-party `base_prompt` injection point, and from the mechanical `identity` section. `lingtai`/`lingtai_file` was renamed from `prompt`/`prompt_file` with **no legacy alias** — a stale `prompt` field is an unknown-field warning and a missing `lingtai` is a hard validation error. Kernel-owned layers are NOT external overrides: `principle.md` mirrors packaged `lingtai/prompts/principle/principle.md` (init `principle`/`principle_file` ignored — legacy-migrated); `substrate` mirrors packaged `lingtai/prompts/substrate/substrate.md` on every boot (init `substrate`/`substrate_file` remain compatibility-known and ignored by the shared read-only reader — kept compact and routed to the packaged `system-manual` skill); `procedures` likewise kernel-owned. The three packaged bodies now live under `lingtai/prompts/<section>/` (one directory per section) alongside their `<section>.yaml` semantic definitions; the runtime-guidance catalog nests under the section it generates at `lingtai/prompts/meta_guidance/catalog/`; see `src/lingtai/prompts/ANATOMY.md` for the definition-vs-injection map. `brief` (secretary life context) is no longer an init override: sourced solely from `system/brief.md` on disk (legacy init `brief`/`brief_file` remain compatibility-known and are reported/ignored by the shared reader because the field is deprecated). `system/guidance.json` is a TUI-readable **derived** mirror serialized from the skill-style Markdown runtime-guidance catalog (`lingtai/prompts/meta_guidance/catalog/` — `INDEX.md` + per-section `<id>.md`, assembled by `lingtai.kernel.prompt_catalog.load_guidance_catalog`) and refreshed by `_reload_prompt_sections`; it is not itself a prompt section. The kernel-owned `principle`/`substrate`/`procedures` mirrors keep their skill-style frontmatter on disk, but the rendered prompt section is body-only (frontmatter stripped on read). In those prompt/guidance frontmatter blocks, `related_files` is not ANATOMY and not a dependency map: it is a maintained inner-link graph for crawling related prompt sources (principle ↔ prompt/guidance sources, guidance INDEX ↔ guidance sections), and it should not list tests or indirect package/runtime dependencies merely because they validate or load the files. `lingtai.kernel.prompt` composes no runtime principle prose: language/activeness remain legacy compatibility fields. |
| `<workdir>/.library/intrinsic/` | `_install_intrinsic_manuals` :174 | Wipe-and-rewrite every boot |
| `<workdir>/.agent.json` | `_build_manifest` :262 via `_workdir.write_manifest` | Runtime manifest snapshot. Includes sanitized `llm` (provider/model/base_url) from the live LLMService and `preset` (active/default/allowed) read from `init.json` by `_read_preset_from_init` :300 — see issue #78. |
| `<workdir>/.mcp_inbox/` | MCPInboxPoller (started at :701) | LICC events from out-of-process MCPs |

## Notes

- **Boot vs refresh share one code path:** `cli.build_agent` explicitly injects the POSIX event journal during minimal `Agent` construction, then calls `_setup_from_init()` :1338; constructor-time event JSON encoding therefore retains the existing `False` default before config hydration. Live refresh re-enters the same method.
- **Current init-reader discipline:** `lingtai.init_reader.read_init` is the single boot/refresh parse → materialize → validate → resolve path. It diagnoses legacy/deprecated paths and leaves user-owned `init.json` unchanged; `system/manifest.resolved.json` is the only derived effective-config artifact. The retained `lingtai.kernel.migrate` registry is not invoked by production boot/refresh; its historical tests remain in `tests/test_kernel_migrate.py`.
- **Init/preset documentation cross-check obligation:** a change to `init.json` composition, preset materialization, or the daemon-task preset path must re-check all four surfaces together in the same PR — this Anatomy's citations (above), the canonical `reference/substrate-manual/SKILL.md` §11 model, the resident `substrate`/`procedures` routing cues, and `tests/test_preset_runtime_model_docs.py` — rather than updating only the code or only one doc layer.
- **`materialize_active_preset` is pure dict mutation** — disk write only in `_activate_preset` :1261 (atomic `.tmp` → replace).
- **Preset implementation moved to kernel** — wrapper `presets.py` re-exports `lingtai.kernel.presets`; production preset reads validate authored data without invoking the retained migration registry.
- **Sensitive key stripping (capabilities):** `_build_manifest` :262 strips `api_key`, `api_key_env`, `api_secret`, `token`, `password` (`_SENSITIVE_KEYS`) from capability kwargs before writing `.agent.json`.
- **LLM / preset safelists (issue #78):** `_build_manifest` :262 also re-applies `_LLM_PUBLIC_KEYS = ("provider", "model", "base_url", "api_compat", "context_limit")` to the kernel-supplied `llm` block as defense-in-depth, and reads `manifest.preset` from init.json via `_read_preset_from_init` :300 filtered to `_PRESET_PUBLIC_KEYS = ("active", "default", "allowed")`. Anything outside the safelists never reaches `.agent.json` or the identity prompt section. This is the central safety claim of #78 — see `tests/test_agent_preset_manifest.py::test_manifest_never_contains_api_key`.
- **AgentConfig hydration:** `_setup_from_init` :1338 rebuilds runtime config via `build_agent_config`, overlaying explicit init.json values onto `lingtai.kernel.config.AgentConfig` defaults. `manifest.cache_miss_budget` overlays `AgentConfig.cache_miss_budget` (default 1,000,000). Legacy `max_turns` and `molt_*` manifest values remain deliberately ignored. `manifest.llm.thinking` hydrates verbatim when present (schema values `none`/`minimal`/`low`/`medium`/`high`/`xhigh`); when omitted, Codex-family providers (`THINKING_PROVIDERS`) keep the `"default"` sentinel so the Codex adapter applies its own default (`reasoning.effort = "xhigh"`), while other providers keep the legacy cross-provider `"high"` main-session default.
- **Addon decompression** runs BEFORE capability setup so `mcp` capability sees populated `mcp_registry.jsonl` on first reconcile (`Agent.__init__` :33, `_setup_from_init` :1338).
- **MCP retry contract (issue #34):** `_load_mcp_from_workdir` :376 records every registered init.json mcp entry into `self._mcp_init_specs` (name → `{cfg, source, client}`). `_retry_failed_mcps` :524 walks this dict, closes any dead client (`is_connected()` False), respawns with the original config, and reports `{retried, recovered, still_failed, healthy}`. `system(action="refresh")` calls it via `lingtai/tools/system/preset.py:_refresh` before `_perform_refresh` so the documented "fix config → refresh" recovery path works without full process restart.
- **Runtime venv markers:** `venv_resolve.py` accepts legacy managed venvs without `.lingtai-env.json` if `import lingtai` succeeds, then stamps the marker best-effort. Marker read/parse/probe failures are `error`, not `mismatch`, and never delete the managed runtime. A valid marker that proves a different OS/arch/Python environment is a confirmed mismatch: explicit `init.venv_path` candidates are rejected but left on disk, while only the managed global runtime venv (`~/.lingtai-tui/runtime/venv/`) may be removed before auto-create. The TUI calls this same logic through `python -m lingtai.venv_resolve env-marker {check,stamp} --venv <path>`.
- **Lazy top-level facade:** `src/lingtai/__init__.py` uses PEP-562 ``__getattr__`` to resolve every public name lazily from its canonical source module (``__init__.py:18-110``). A bare ``import lingtai`` performs only stdlib/importlib.metadata work; it must not load `lingtai.agent`, `lingtai.kernel`, `tools`, `lingtai.llm`, services, MCP servers, or concrete providers. ``__dir__`` returns standard module globals unioned with ``__all__`` (``__init__.py:112-113``). Verified by `tests/test_lingtai_facade.py` and `tests/test_kernel_isolation.py`.
