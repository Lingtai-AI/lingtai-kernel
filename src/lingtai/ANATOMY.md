# lingtai

PyPI wrapper package — `Agent(BaseAgent)` with composable capabilities, preset materialization, CLI, and public re-exports.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | Role |
|---|---|
| `__init__.py` | Public API facade — re-exports `Agent`, `BaseAgent`, `Message`, services from kernel+wrapper |
| `__main__.py` | `python -m lingtai` → `cli.main()` |
| `agent.py` | **THE key file.** `Agent(BaseAgent)` — layer-2 agent with capability composition, preset swap, MCP, init.json refresh |
| `cli.py` | `lingtai run <dir>` / `lingtai check-caps` entry points |
| `network.py` | Read-only network topology crawler — avatar/contact/mail edge discovery |
| `presets.py` | Preset library — load, validate, materialize `{llm, capabilities}` bundles |
| `init_schema.py` | `validate_init()` — strict schema for init.json |
| `config_resolve.py` | Env/file/path resolution helpers for init.json fields |
| `venv_resolve.py` | Python venv resolution — init.json → global runtime → auto-create |
| `preset_connectivity.py` | TCP reachability probes for preset LLM endpoints |
| `intrinsic_skills/__init__.py` | Standalone skill bundles (docs-only) copied into `.library/intrinsic/` |

### Key functions / classes

**`agent.py`** — `Agent(BaseAgent)`: `__init__` :33 (expand groups, decompress addons, setup caps, install manuals, load MCP) · `_setup_capability` :134 · `_persist_llm_config` :109 · `_install_intrinsic_manuals` :156 · `_load_mcp_from_workdir` :282 · `_read_init` :583 (read + materialize preset + validate) · `_setup_from_init` :707 (**full reconstruct** — shared by boot and live refresh) · `_activate_preset` :633 (runtime swap, atomic write) · `_reload_prompt_sections` :914 · `connect_mcp` :454 / `connect_mcp_http` :506 · `start` :447 / `stop` :552

**`cli.py`**: `load_init` :20 · `build_agent` :60 · `run` :147 · `main` :192

**`presets.py`**: `load_preset` :175 · `materialize_active_preset` :290 · `expand_inherit` :400 · `discover_presets_in_dirs` :121

**`init_schema.py`**: `validate_init` :64 · `TOP_OPTIONAL` :13 · `MANIFEST_OPTIONAL` :42

**`config_resolve.py`**: `load_jsonc` :16 · `resolve_env` :42 · `resolve_paths` :98 · `_resolve_capabilities` :121

**`network.py`**: `build_network` :306 · `_discover_agents` :143 · `_build_avatar_edges` :168

**`venv_resolve.py`**: `resolve_venv` :19 · `venv_python` :40 · `ensure_package` :94

**`preset_connectivity.py`**: `check_connectivity` :63 · `check_many` :118

## Connections

**Inbound:** `lingtai-tui` calls `cli.run()` to boot agents; imports `load_preset`, `discover_presets_in_dirs` for UI. Kernel's `BaseAgent` is the parent class.

**Outbound — kernel:** `lingtai_kernel.base_agent.BaseAgent`, `.config.AgentConfig`, `.prompt.build_system_prompt`, `.handshake.resolve_address`, `.intrinsics.{email,psyche}`, `.services.mail.FilesystemMailService`, `.migrate.run_migrations`.

**Cross-module:** `agent.py` → `capabilities.setup_capability`, `core.mcp.{decompress_addons,read_registry,MCPInboxPoller}`, `services.mcp.{MCPClient,HTTPMCPClient}`, `llm.service.LLMService`, `presets`, `config_resolve`, `init_schema`. `cli.py` → `agent.Agent`, `config_resolve`, `presets`.

**Agent → BaseAgent:** Three-layer hierarchy: `BaseAgent` (kernel) → `Agent` (capabilities) → `CustomAgent` (domain). Agent adds capability registration, MCP auto-loading, preset swap, full init.json reconstruct.

**Capability registration:** `setup_capability()` in `capabilities/__init__.py`; `@register_capability` decorator. Agent calls `_setup_capability` (agent.py:134) during `__init__` and `_setup_from_init`.

**Preset materialization:** `materialize_active_preset` (presets.py:290) called by `cli.load_init` (boot) and `Agent._read_init` (refresh). Reads `manifest.preset.active`, loads preset, substitutes `llm`+`capabilities` into manifest before validation.

## Composition

Parent: `src/lingtai/` under `lingtai-kernel/src/` alongside `lingtai_kernel/` (kernel package). Siblings: `capabilities/`, `core/`, `llm/`, `services/`, `auth/`, `i18n/`. See `../ANATOMY.md`.

## State

| Path | When | What |
|---|---|---|
| `<workdir>/init.json` | `_activate_preset` :633, `cli.run` :159 | Preset swap (atomic write); venv_path writeback |
| `<workdir>/system/llm.json` | `_persist_llm_config` :109 | LLM provider/model/base_url for revive |
| `<workdir>/system/{covenant,principle,procedures,brief,rules,pad}.md` | `_reload_prompt_sections` :914 | Prompt sections from init.json |
| `<workdir>/.library/intrinsic/` | `_install_intrinsic_manuals` :156 | Wipe-and-rewrite every boot |
| `<workdir>/.agent.json` | `_build_manifest` :231 via `_workdir.write_manifest` | Runtime manifest snapshot |
| `<workdir>/.mcp_inbox/` | MCPInboxPoller (started at :451) | LICC events from out-of-process MCPs |

## Notes

- **Boot vs refresh share one code path:** `cli.build_agent` constructs minimal `Agent`, calls `_setup_from_init()` :707. Live refresh re-enters the same method.
- **`materialize_active_preset` is pure dict mutation** — disk write only in `_activate_preset` :633 (atomic `.tmp` → replace).
- **`presets.py` imports kernel** (`lingtai_kernel.migrate`) — preset validation normalizes legacy shapes via kernel migrations before type-checking.
- **Sensitive key stripping:** `_build_manifest` :239 strips `api_key`, `api_key_env`, `token`, `password` from capability kwargs before writing `.agent.json`.
- **Drift hazard:** `_setup_from_init` :817–834 manually reconstructs `AgentConfig` with inline defaults that MUST mirror `lingtai_kernel.config.AgentConfig` (comment at :811, nothing enforces).
- **Addon decompression** runs BEFORE capability setup so `mcp` capability sees populated `mcp_registry.jsonl` on first reconcile (:79, :853).
