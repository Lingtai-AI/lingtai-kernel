"""Agent — BaseAgent + composable capabilities.

Anatomy leaf: see ANATOMY.md in this directory for preset materialization.

Layer 2 of the three-layer hierarchy:
    BaseAgent (kernel) → Agent (capabilities) → CustomAgent (domain)

Capabilities are declared at construction and sealed before start().
"""
from __future__ import annotations

import contextlib
import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lingtai.kernel.base_agent import BaseAgent, StopResult
from lingtai.kernel.base_agent.prompt import _refresh_meta_guidance_section
from lingtai.kernel.config import (
    AgentConfig,
    HEARTBEAT_LIVENESS_SECONDS,
    THINKING_OWNED_PROVIDERS,
    THINKING_PROVIDERS,
)
from lingtai.kernel.llm.base import ToolCall
from lingtai.llm.service import (
    CONSERVATIVE_CONTEXT_WINDOW,
    LLMService,
    build_provider_defaults_from_manifest_llm,
)
from lingtai.kernel.prompt import build_system_prompt

if TYPE_CHECKING:
    from lingtai.tools.psyche.settings import (
        PsycheSettingsSnapshot,
    )
    from lingtai.tools.psyche.prompt import PromptPlan


# Runtime manual destinations remain stable even when a capability packages its
# manual beside code. This map is deliberately small and explicit: it is not a
# discovery or normalization rule for future manual collisions.
_TOOL_MANUAL_DESTINATION_NAMES: dict[str, str] = {
    "bash": "shell",
    "web_search": "web",
    "context": "context-manual",
    "file": "file-manual",
    "soul": "soul-manual",
}


def _detached_spawn_kwargs() -> dict[str, Any]:
    """Platform kwargs for launching a detached agent-run child.

    This wrapper is a composition root, so the one platform branch for the
    CPR relaunch mechanism lives here rather than in kernel Core. POSIX
    detaches into a new session; Windows uses the shared detached creation
    flags (new process group, no window — ``start_new_session`` is
    POSIX-only and Windows children survive parent exit by default).
    """
    import os as _os

    if _os.name == "nt":
        from lingtai.adapters.windows._win32 import DETACHED_CREATIONFLAGS

        return {"creationflags": DETACHED_CREATIONFLAGS, "close_fds": True}
    return {"start_new_session": True}


def _run_preset_library_migrations(directory: Path) -> None:
    """Retained callback slot; production preset reads are migration-free.

    Preset data is read as authored. The old migration registry remains only for
    explicit historical/test callers and is not a runtime dependency of boot,
    refresh, or preset selection.
    """
    return


def load_preset(name: str, working_dir: "Path | None" = None) -> dict:
    """Wrapper-level preset-loader: Core ``load_preset`` wired to the POSIX runner.
    The single shared implementation — CLI, each `Agent`'s ``_preset_loader`` hook,
    and `Agent`'s own preset paths call it, so nothing else builds a workspace.
    """
    from lingtai.kernel.presets import load_preset as _core_load_preset

    preset = _core_load_preset(
        name, working_dir=working_dir, run_migrations=_run_preset_library_migrations
    )
    _validate_provider_owned_thinking(preset, name)
    return preset


def _validate_provider_owned_thinking(preset: dict, name: str) -> None:
    """Apply a provider-owned effort contract to a loaded preset.

    The kernel validator only decides whether ``manifest.llm.thinking`` is in
    scope; it must not import provider modules (the DAG is
    lingtai -> tools -> lingtai.kernel, enforced by
    tests/test_kernel_isolation.py). The exact per-model/per-wire accepted set
    is owned by the provider and applied here, on the shared preset-loading
    path the CLI, each ``Agent``'s ``_preset_loader`` hook, and ``Agent``'s own
    preset paths all use.
    """
    from lingtai.llm.deepseek.policy import owns_provider, validate_llm_block

    llm = (preset or {}).get("manifest", {}).get("llm")
    if not isinstance(llm, dict) or not owns_provider(llm.get("provider")):
        return
    try:
        validate_llm_block(llm)
    except ValueError as exc:
        raise ValueError(f"preset {name!r}: {exc}") from exc


def build_agent_config(
    manifest: dict[str, Any],
    *,
    max_rpm: int,
    runtime_policy: Any | None = None,
) -> AgentConfig:
    """Overlay host manifest values onto AgentConfig defaults.

    The ordinary runtime fields are System-owned.  When *runtime_policy* is
    supplied it provides them; otherwise their fixed ``AgentConfig`` defaults
    apply.  Raw manifest values are compatibility data, never configuration.
    """
    defaults = AgentConfig()
    soul = manifest.get("soul", {})
    llm = manifest.get("llm", {})
    policy = runtime_policy.as_overrides() if runtime_policy is not None else {}

    return AgentConfig(
        soul_delay=soul.get("delay", defaults.soul_delay),
        consultation_past_count=soul.get(
            "consultation_past_count", defaults.consultation_past_count
        ),
        soul_voice=soul.get("voice", defaults.soul_voice),
        soul_voice_prompt=soul.get("voice_prompt", defaults.soul_voice_prompt),
        # ``manifest.max_turns`` is a legacy/resolved-manifest field and is no
        # longer the authoritative tool-loop guard source. ACTIVE-turn
        # tool-call safety is kernel-owned in ``lingtai.kernel.safety_limits``.
        # Keep AgentConfig.max_turns at its default for API compatibility, but
        # deliberately ignore stale init.json values here.
        language=manifest.get("language", defaults.language),
        activeness=policy.get("activeness", defaults.activeness),
        context_limit=policy.get("context_limit", defaults.context_limit),
        # Providers that own their omitted-thinking default keep the "default"
        # sentinel instead of being promoted to the legacy cross-provider
        # "high" main-session default: the Codex family (omitted ->
        # reasoning.effort "xhigh") and the provider-owned routes such as
        # DeepSeek (omitted -> no reasoning field at all, so the provider's own
        # default applies). Every other provider hydrates exactly as before.
        thinking=llm.get(
            "thinking",
            "default"
            if str(llm.get("provider") or "").lower()
            in THINKING_PROVIDERS + THINKING_OWNED_PROVIDERS
            else defaults.thinking,
        ),
        # Molt thresholds and the context.molt message are kernel-fixed runtime
        # constants and are NOT agent-configurable. Stale manifest
        # molt_notice/molt_pressure/molt_urgency/molt_prompt values are
        # deliberately ignored.
        snapshot_interval=policy.get(
            "snapshot_interval", defaults.snapshot_interval
        ),
        time_awareness=manifest.get("time_awareness", defaults.time_awareness),
        timezone_awareness=manifest.get(
            "timezone_awareness", defaults.timezone_awareness
        ),
        aed_timeout=policy.get("aed_timeout", defaults.aed_timeout),
        max_aed_attempts=policy.get(
            "max_aed_attempts", defaults.max_aed_attempts
        ),
        max_rpm=max_rpm,
    )


def _schema_declares_reasoning_param(schema: Any) -> bool:
    """True if a tool's own parameter schema has somewhere for reasoning to go.

    Covers both the LTP-v2 envelope's ``reasoning`` and any third-party tool
    that happens to declare its own ``reasoning``/``_reasoning`` parameter
    directly. Used by ``_dispatch_tool`` to decide whether the ``_reasoning``
    audit key is safe to forward as-is or must be dropped before dispatch.
    """
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    return "reasoning" in properties or "_reasoning" in properties


class Agent(BaseAgent):
    """BaseAgent with composable capabilities.

    Args:
        capabilities: Capability names to enable. Either a list of strings
            (no kwargs) or a dict mapping names to kwargs dicts.
            Each capability dict may include ``"provider"`` to route that
            capability to a specific LLM provider (e.g. ``"gemini"``, ``"minimax"``).
            Group names (e.g. ``"file"``) expand to individual capabilities.
        plugins: Agent Plugin package directories to register, the constructor
            form of ``init.json`` ``manifest.plugins``. Each declared plugin's
            ``skills/`` joins the skills catalog and its ``mcp.json`` servers
            join ``mcp_registry.jsonl`` with ``source="plugin:<name>"``.
        *args, **kwargs: Passed through to BaseAgent.
    """

    def _boot_official_intrinsics(self) -> None:
        """Defer only the CLI shell's initial declared-tool binding."""
        if self._from_init_boot:
            return
        super()._boot_official_intrinsics()

    def resolve_cache_miss_budget(self) -> int:
        """Lazily delegate cache-miss budget resolution to System settings."""
        from lingtai.tools.system.settings import resolve_cache_miss_budget

        return resolve_cache_miss_budget(self)

    def resolve_notification_max_chars(self) -> int | None:
        """Lazily expose the System v2 notification cap (file layer only).

        Kernel metadata consults this after its own live env read and applies
        its own clamp/default; ``None`` means "no System file value".
        """
        from lingtai.tools.system.settings import resolve_notification_max_chars

        return resolve_notification_max_chars(self)

    def resolve_runtime_policy(self) -> Any:
        """Resolve the System-owned ordinary runtime policy once per setup."""
        from lingtai.tools.system.settings import resolve_runtime_policy

        return resolve_runtime_policy(self._working_dir)

    def __init__(
        self,
        *args: Any,
        capabilities: list[str] | dict[str, dict] | None = None,
        addons: list[str] | None = None,
        plugins: list[str] | None = None,
        combo_name: str | None = None,
        disable: list[str] | None = None,
        _forced_disable: frozenset[str] | None = None,
        _turn_origin_policy: Any | None = None,
        _requires_turn_origin_policy: bool = False,
        _requires_derived_launch_admission_port: bool = False,
        _from_init_boot: bool = False,
        **kwargs: Any,
    ):
        # Private CLI composition sentinel. It is set before BaseAgent invokes
        # the overridable mandatory-official boot hook below.
        self._from_init_boot = _from_init_boot
        self._forced_disable = frozenset(_forced_disable or ())
        self._turn_origin_policy = _turn_origin_policy
        self._requires_turn_origin_policy = _requires_turn_origin_policy
        self._requires_derived_launch_admission_port = (
            _requires_derived_launch_admission_port
        )
        # Psyche SHOW reports only inputs successfully consumed by canonical
        # reconstruction. Direct construction starts at the eight meaningful
        # defaults; ambient init/source edits never mutate this applied state.
        from lingtai.tools.psyche.settings import PsycheSettingsSnapshot

        self._psyche_settings_snapshot = PsycheSettingsSnapshot()
        self._psyche_prompt_plan: PromptPlan | None = None

        # Default karma authority for the primary agent (本我)
        kwargs.setdefault("admin", {"karma": True})

        # Whether this caller made any statement about what this agent runs.
        # ``capabilities`` is about to be rewritten by ``apply_core_defaults``,
        # which makes it unconditionally truthy, so the question has to be asked
        # here. ``_register_declared_plugins`` below is gated on it: a minimal
        # construction that declares neither capabilities nor plugins is a
        # caller deferring everything to ``_setup_from_init``, and must not be
        # read as "no plugins are declared" — that reading would prune every
        # plugin-owned registry record on the way to re-registering it.
        _plugin_decision_made = capabilities is not None or plugins is not None

        # Inject the built-in intrinsic tool registry. The kernel owns the tool
        # machinery, not the concrete tools: it accepts intrinsics as injection
        # and a bare BaseAgent has none. lingtai.Agent is the composing layer, so
        # it supplies the five mandatory intrinsics here. Notification is an
        # always-on declared official family and mounts through the official
        # plugin registrar. ``setdefault`` lets a host override (e.g. a test
        # injecting a subset).
        from lingtai.tools.registry import INTRINSICS
        kwargs.setdefault("intrinsics", INTRINSICS)

        # The outer wrapper is a composition root for direct ``lingtai.Agent``
        # callers.  Explicit ``event_journal=None`` remains an honest opt-out.
        owned_event_journal = None
        if "event_journal" not in kwargs and "working_dir" in kwargs:
            from lingtai.adapters.posix.event_journal import (
                PosixJsonlEventJournalAdapter,
            )

            config = kwargs.get("config")
            ensure_ascii = bool(getattr(config, "ensure_ascii", False))
            owned_event_journal = PosixJsonlEventJournalAdapter(
                kwargs["working_dir"],
                ensure_ascii=ensure_ascii,
            )
            kwargs["event_journal"] = owned_event_journal

        try:
            # BaseAgent requires an explicit workdir lease. As the composition
            # root for direct ``lingtai.Agent`` callers, select and construct the
            # production adapter for the running platform (fail-loud on
            # unsupported platforms). A caller may inject its own lease.
            if "workdir_lease" not in kwargs and "working_dir" in kwargs:
                from lingtai.adapters.workdir_lease import select_workdir_lease

                kwargs["workdir_lease"] = select_workdir_lease(kwargs["working_dir"])

            # BaseAgent requires a notification store. As the composition root,
            # construct the production POSIX adapter. A caller may inject its own.
            if "notification_store" not in kwargs and "working_dir" in kwargs:
                from lingtai.adapters.posix.notification_store import (
                    PosixNotificationStoreAdapter,
                )

                kwargs["notification_store"] = PosixNotificationStoreAdapter(
                    kwargs["working_dir"]
                )

            # BaseAgent requires an agent-presence store bound to its own
            # working directory. As the composition root, construct the
            # production POSIX adapter. A caller may inject its own.
            if "agent_presence" not in kwargs and "working_dir" in kwargs:
                from lingtai.adapters.posix.agent_presence import (
                    PosixAgentPresenceStoreAdapter,
                )

                kwargs["agent_presence"] = PosixAgentPresenceStoreAdapter(
                    kwargs["working_dir"]
                )

            # BaseAgent requires a lifecycle clock. As the composition root for
            # direct ``lingtai.Agent`` callers, construct the portable system
            # adapter (no working_dir needed). A caller may inject its own.
            if "lifecycle_clock" not in kwargs:
                from lingtai.adapters.lifecycle_clock import (
                    SystemLifecycleClockAdapter,
                )

                kwargs["lifecycle_clock"] = SystemLifecycleClockAdapter()

            # BaseAgent requires a refresh-watcher Port for the detached relaunch
            # handoff. As the composition root, select the production capability
            # adapter for the running platform. A caller may inject its own.
            if kwargs.get("refresh_watcher") is None:
                from lingtai.adapters.refresh_watcher import select_refresh_watcher

                kwargs["refresh_watcher"] = select_refresh_watcher()

            # Compose the two required snapshot/revision capabilities outside Core.
            # Separate instances intentionally target the workdir and running source.
            if "snapshot_port" not in kwargs and "working_dir" in kwargs:
                from lingtai.adapters.posix.git_cli import PosixGitCliAdapter

                kwargs["snapshot_port"] = PosixGitCliAdapter(kwargs["working_dir"])
            if "source_revision_port" not in kwargs:
                from lingtai.adapters.posix.git_cli import PosixGitCliAdapter

                kwargs["source_revision_port"] = PosixGitCliAdapter(
                    Path(__file__).resolve().parent
                )

            # Store combo name before super().__init__ (not forwarded to BaseAgent)
            self._combo_name = combo_name
            super().__init__(*args, **kwargs)
            # One canonical full-context reconstruction hook serves both passive
            # molt and the public active context.rebuild operation. Intrinsic
            # Pad/LingTai boots do initial composition only; they never register
            # competing section-specific post-molt loaders.
            self._post_molt_hooks = [self._reconstruct_context]
            # Compose the preset-loader hook so daemon/system tools resolve
            # presets through the wrapper implementation instead of importing
            # Core load_preset or constructing a migration workspace adapter.
            self._preset_loader = load_preset
        except Exception:
            if owned_event_journal is not None:
                with contextlib.suppress(Exception):
                    owned_event_journal.close()
            raise

        # Soul remains an injected intrinsic for kernel lifecycle hooks, but its
        # model-facing root is an official declared plugin. Remove only the
        # temporary intrinsic dispatcher entry and mount the static declaration
        # before capability setup; the module remains available to hook lookup.
        if "soul" in self._intrinsics:
            from lingtai.tools import soul as _soul
            self.override_intrinsic("soul")
            _soul.setup(self)

        # Persist LLM config for revive (self-sufficient agents contract)
        self._persist_llm_config()

        # Auto-create FileIOService if not provided by host. Uses the
        # ``default_file_io_service`` factory so the Rust sidecar gets
        # picked up automatically when a wheel-bundled or env-provided
        # binary is available, with transparent pure-Python fallback.
        # See LINGTAI_FILE_IO_BACKEND in services/file_io_sidecar.py.
        if self._file_io is None:
            from .services.file_io_sidecar import default_file_io_service
            self._file_io = default_file_io_service(root=self._working_dir)

        # The CLI has already validated init.json and immediately delegates the
        # complete wrapper composition to `_setup_from_init`. Keep this initial
        # Agent body an internal shell so it does not compose and tear down a
        # second capability/manual/MCP surface first. The bookkeeping remains
        # available to the normal setup pass after CLI clears the private flag.
        if _from_init_boot:
            self._capabilities = []
            self._capability_managers = {}
            self._mcp_tool_names = set()
            self._plugin_skill_paths = []
            self._plugin_registration = {}
            self._from_init_boot = False
            return

        # Normalize to dict
        if isinstance(capabilities, list):
            from lingtai.tools.registry import normalize_capabilities
            capabilities = normalize_capabilities({name: {} for name in capabilities})
        elif isinstance(capabilities, dict):
            from lingtai.tools.registry import normalize_capabilities
            expanded_dict: dict[str, dict] = {}
            for name, cap_kwargs in capabilities.items():
                if cap_kwargs is None:
                    expanded_dict[name] = None  # propagate disable-sentinel
                else:
                    expanded_dict[name] = cap_kwargs
            capabilities = normalize_capabilities(
                {n: (v if v is not None else {}) for n, v in expanded_dict.items()}
            )
            # Preserve null sentinels for apply_core_defaults to interpret as opt-out.
            for n, v in expanded_dict.items():
                if v is None:
                    capabilities[n] = None  # type: ignore[assignment]

        # Apply core defaults — the always-on tool floor boots on every agent
        # unless explicitly disabled via `disable=[...]` or `"name": null` in
        # the capabilities dict. init.json kwargs override default kwargs.
        from lingtai.tools.registry import apply_core_defaults
        capabilities = apply_core_defaults(
            capabilities,
            disable=[*(disable or []), *self._forced_disable],
        )

        # Track for avatar replay
        self._capabilities: list[tuple[str, dict]] = []
        self._capability_managers: dict[str, Any] = {}
        # Names registered by parent MCP clients. Daemon uses this only to avoid
        # leaking parent MCP tools through tasks[].tools; task MCP access must
        # come from complete per-task registrations.
        self._mcp_tool_names: set[str] = set()

        # Decompress addons BEFORE capability setup so the `mcp` capability
        # sees the populated registry on its first reconcile.
        if addons:
            try:
                from .services.mcp_registry import decompress_addons
                report = decompress_addons(self._working_dir, addons)
                self._log("mcp_decompress", **report)
            except Exception as e:
                self._log("mcp_decompress_failed", reason=str(e))

        # Register declared Agent Plugins at the same point and for the same
        # reason: `skills` must see each plugin's skill directories and `mcp`
        # must see its plugin-sourced registry records on their first reconcile.
        # It runs after addon decompression so a plugin server cannot silently
        # take a name a curated addon owns.
        #
        # Skipped only for the minimal-construction path (neither `capabilities`
        # nor `plugins` given), where `_setup_from_init` does the real
        # registration moments later. Running here too would prune every
        # plugin-owned record and re-append it on every single boot, making the
        # idempotence this module promises untrue of the shipped boot flow.
        self._plugin_skill_paths: list[str] = []
        self._plugin_registration: dict = {}
        if _plugin_decision_made:
            self._register_declared_plugins(plugins, capabilities)

        # Register capabilities — provider kwarg flows through to setup() naturally
        if capabilities:
            for name, cap_kwargs in capabilities.items():
                try:
                    self._setup_capability(name, **cap_kwargs)
                except (ValueError, ImportError, TypeError) as e:
                    self._log("capability_skipped", capability=name, reason=str(e))

        # Install intrinsic manuals (wipe-and-rewrite .library/intrinsic/)
        # from the bundles shipped with each enabled capability.
        self._install_intrinsic_manuals()

        # Auto-load MCP servers from working directory.
        # Runs AFTER addon decompression so init.json mcp entries can reference
        # newly-decompressed registry records.
        self._load_mcp_from_workdir()

        # Re-write manifest now that capabilities are registered
        if self._capabilities:
            self._workdir.write_manifest(self._build_manifest())

    def _persist_llm_config(self) -> None:
        """Persist LLM config to llm.json for agent revive.

        Extracted from __init__ to avoid duplication.
        """
        _service = getattr(self, "service", None)
        if _service is None:
            return
        try:
            import json as _json
            llm_config: dict[str, Any] = {
                "provider": _service.provider,
                "model": _service.model,
            }
            _base_url = getattr(_service, "_base_url", None)
            if isinstance(_base_url, str) and _base_url:
                llm_config["base_url"] = _base_url
            llm_dir = self._working_dir / "system"
            llm_dir.mkdir(exist_ok=True)
            (llm_dir / "llm.json").write_text(
                _json.dumps(llm_config, ensure_ascii=False),
                encoding="utf-8",
            )
        except (TypeError, AttributeError, OSError):
            pass  # LLM config not available (e.g., mock service in tests)

    def _setup_capability(self, name: str, **kwargs: Any) -> Any:
        """Load a named capability.

        ``None`` from setup means success without a manager object. A setup that
        cannot register must return ``CAPABILITY_UNAVAILABLE`` before adding any
        tools so this wrapper can leave the capability absent from public
        registration surfaces.

        Not directly sealed — but setup() calls add_tool() which checks the seal.
        Must only be called from __init__ (before start()).
        """
        from lingtai.tools.registry import CAPABILITY_UNAVAILABLE, setup_capability

        def _manifest_safe(value: Any) -> Any:
            # Keep configuration containers visible while omitting opaque,
            # identity-bearing injected ports/services from the JSON manifest.
            # Sensitive names are removed at every nesting level and through
            # every container shape accepted here; runtime setup still receives
            # the original kwargs unchanged.
            from collections.abc import Mapping as ABCMapping

            if isinstance(value, ABCMapping):
                clean: dict[Any, Any] = {}
                for key, item in value.items():
                    if key in self._SENSITIVE_KEYS:
                        continue
                    safe_value = _manifest_safe(item)
                    if safe_value is not _UNSERIALIZABLE:
                        clean[key] = safe_value
                return clean
            if isinstance(value, (list, tuple, set, frozenset)):
                clean_items = []
                for item in value:
                    safe_value = _manifest_safe(item)
                    if safe_value is not _UNSERIALIZABLE:
                        clean_items.append(safe_value)
                return clean_items
            if isinstance(value, (str, int, float, bool, type(None))):
                return value
            return _UNSERIALIZABLE

        _UNSERIALIZABLE = object()
        serializable_kw = _manifest_safe(kwargs)
        self._capabilities.append((name, serializable_kw))
        try:
            mgr = setup_capability(self, name, **kwargs)
        except Exception:
            # Roll back the entry so _capabilities only lists registered caps.
            self._capabilities.pop()
            raise
        if mgr is CAPABILITY_UNAVAILABLE:
            self._capabilities.pop()
            return None
        self._capability_managers[name] = mgr
        return mgr

    def _register_declared_plugins(
        self, plugins: Any, capabilities: Any = None
    ) -> None:
        """Register every declared Agent Plugin. Boot/refresh only, never a tool.

        The canonical declaration key is ``init.json`` ``manifest.plugins``; the
        alias ``manifest.capabilities.plugin.paths`` (the key PR #1232 shipped)
        is honored unchanged and means the same thing. Both are resolved by
        ``plugin_registry.declared_plugin_paths``.

        Runs even when nothing is declared — that empty run is exactly the
        uninstall path. Removing a plugin from the declaration list leaves its
        ``source="plugin:<name>"`` records in ``mcp_registry.jsonl`` until
        something prunes them, and this is that something. Skipping the call when
        the list is empty would make uninstalling the *last* plugin silently
        leave its servers registered.

        "Nothing is declared" is a decision, though, and only a caller who said
        something is making it. ``__init__`` therefore calls this only when it was
        given ``capabilities=`` or ``plugins=``; a bare minimal construction
        defers to ``_setup_from_init``, which always calls it. Without that gate
        the CLI boot path ran the uninstall branch first and the real
        registration second, pruning and re-appending every plugin record on
        every boot.

        The results are recorded on the agent rather than returned: the skills
        capability reads ``_plugin_skill_paths`` from every one of its reconcile
        entry points, and the plugin tool's ``info`` reports
        ``_plugin_registration``.
        """
        self._plugin_skill_paths: list[str] = []
        self._plugin_registration: dict = {}
        try:
            from .services.plugin_registry import (
                declared_plugin_paths,
                register_plugins,
            )

            declared = declared_plugin_paths(plugins, capabilities)
            snapshot = register_plugins(self._working_dir, declared)
            self._plugin_skill_paths = list(snapshot.get("skill_paths") or [])
            self._plugin_registration = snapshot
            if declared or snapshot.get("mcp_pruned"):
                self._log(
                    "plugin_register",
                    declared=declared,
                    plugins=[p["name"] for p in snapshot.get("plugins", [])],
                    mcp_appended=snapshot.get("mcp_appended", []),
                    mcp_pruned=snapshot.get("mcp_pruned", []),
                    problems=len(snapshot.get("problems", [])),
                )
        except Exception as e:
            # Registration must never take the agent down. A failure here leaves
            # the agent unmounted (empty snapshot), which the plugin tool reports
            # honestly, rather than half-mounted and silent.
            self._log("plugin_register_failed", reason=str(e))

    def _install_intrinsic_manuals(self) -> None:
        """Wipe and rewrite ``.library/intrinsic/`` from kernel-shipped manuals.

        Runs near the end of ``__init__`` and ``_setup_from_init``. Installs
        every capability's ``manual/`` bundle into
        ``.library/intrinsic/capabilities/<name>/``, **regardless of whether
        this agent enabled the capability**. The library is kernel-shipped
        documentation — agents should be able to read about a capability
        before they configure it.

        Never touches ``.library/custom/``. That is the agent's territory.
        """
        import shutil
        import lingtai.tools as tools_pkg
        import lingtai.intrinsic_skills as skills_pkg

        library_dir = self._working_dir / ".library"
        intrinsic_dir = library_dir / "intrinsic"

        (library_dir / "custom").mkdir(parents=True, exist_ok=True)

        if intrinsic_dir.exists():
            shutil.rmtree(intrinsic_dir)
        (intrinsic_dir / "capabilities").mkdir(parents=True, exist_ok=True)

        # The first installer records which canonical package owns every installed
        # destination so standalone same-name collisions fail closed.
        canonical_manual_owners: dict[tuple[str, str], str] = {}

        def install_tool_plugin(entry: Path, subdir: str) -> None:
            """Install one built-in tool package's sole owned manual skill.

            The Agent Plugins reader remains the only manifest and containment
            validator. Built-in model-facing packages are documentation sources,
            not declared external plugins: a bundled ``mcp.json`` is rejected
            here so this installer cannot become a second route into the
            per-agent MCP registry.
            """
            from .services.plugin_registry import read_plugin

            record, problems = read_plugin(entry)
            for problem in problems:
                self._log(
                    "tool_plugin_problem",
                    plugin=entry.name,
                    reason=str(problem.get("error", "")),
                )
            if record is None:
                return
            if record["mcp_servers"]:
                self._log(
                    "tool_plugin_problem",
                    plugin=record["name"],
                    reason=(
                        "built-in tool plugins must not carry mcp.json; "
                        "their servers are not registered"
                    ),
                )
                return
            skill_paths = record["skill_paths"]
            if len(skill_paths) != 1:
                self._log(
                    "tool_plugin_problem",
                    plugin=record["name"],
                    reason=(
                        "built-in tool plugin must provide exactly one owned "
                        "manual skill"
                    ),
                )
                return
            destination_name = record["name"]
            destination = intrinsic_dir / subdir / destination_name
            owner_key = (subdir, destination_name)
            if destination.exists():
                prior_owner = canonical_manual_owners.get(owner_key, "unknown")
                raise RuntimeError(
                    "intrinsic manual destination collision: built-in tool plugin "
                    f"{entry.name!r} and {prior_owner!r} both target "
                    f"{destination}"
                )
            shutil.copytree(skill_paths[0], destination)
            canonical_manual_owners[owner_key] = entry.name

        def install_from(pkg, subdir: str) -> None:
            pkg_file = getattr(pkg, "__file__", None)
            if not pkg_file:
                return
            pkg_root = Path(pkg_file).parent
            for entry in sorted(pkg_root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("_"):
                    continue
                # Browser is an internal browse subcomponent. Its former manual
                # is retained on disk but must not become a second public model.
                if entry.name == "browser":
                    continue
                if (entry / "plugin.json").is_file():
                    install_tool_plugin(entry, subdir)
                    continue
                src = entry / "manual"
                if not src.is_dir():
                    continue
                destination_name = _TOOL_MANUAL_DESTINATION_NAMES.get(
                    entry.name, entry.name
                )
                destination = intrinsic_dir / subdir / destination_name
                owner_key = (subdir, destination_name)
                if destination.exists():
                    prior_owner = canonical_manual_owners.get(owner_key, "unknown")
                    raise RuntimeError(
                        "intrinsic manual destination collision: canonical tool "
                        f"{entry.name!r} and {prior_owner!r} both target "
                        f"{destination}"
                    )
                shutil.copytree(src, destination)
                canonical_manual_owners[owner_key] = entry.name

        def install_skills_from(pkg, subdir: str) -> None:
            """Install standalone skill bundles with no companion tool package.

            Each ``<pkg>/<entry>/`` directory is copied verbatim into
            ``intrinsic/<subdir>/<entry>/``, including manuals and any sidecar
            scripts/assets such as the ``lingtai-kernel-anatomy`` checker and
            benchmark.
            """
            pkg_file = getattr(pkg, "__file__", None)
            if not pkg_file:
                return
            pkg_root = Path(pkg_file).parent
            for entry in sorted(pkg_root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("_"):
                    continue
                destination = intrinsic_dir / subdir / entry.name
                owner = canonical_manual_owners.get((subdir, entry.name))
                if destination.exists():
                    raise RuntimeError(
                        "intrinsic manual destination collision: standalone skill "
                        f"{entry.name!r} conflicts with canonical owner {owner!r} at "
                        f"{destination}"
                    )
                shutil.copytree(entry, destination)

        # Every tool package with a manual/ installs into
        # intrinsic/capabilities/<name>/ — agents see one flat capability
        # namespace. Scanning the consolidated ``lingtai.tools`` package replaces the
        # former core/ + capabilities/ dual scan.
        install_from(tools_pkg, "capabilities")
        install_skills_from(skills_pkg, "capabilities")

        # If the skills capability is loaded, re-run its reconcile now that
        # the manuals are on disk — so the injected catalog reflects them on
        # the very first turn (skills.setup()'s initial _reconcile ran BEFORE
        # install, when the manual dir was empty).
        for cap_name, cap_kwargs in self._capabilities:
            if cap_name == "skills":
                try:
                    from lingtai.tools import skills as skillsmod
                    skillsmod._reconcile(self, list(cap_kwargs.get("paths", []) or []))
                except Exception as e:
                    self._log("skills_reconcile_failed", reason=str(e))
                break

    _SENSITIVE_KEYS = {"api_key", "api_key_env", "api_secret", "token", "password"}

    #: Safelist for the public ``llm`` block surfaced in .agent.json. Mirrors
    #: ``base_agent.identity._LLM_PUBLIC_KEYS`` and exists at the wrapper layer
    #: as defense-in-depth — init.json's ``manifest.llm`` may carry api_key /
    #: api_key_env values that must never reach the on-disk manifest or the
    #: system prompt's identity section.
    _LLM_PUBLIC_KEYS = (
        "provider",
        "model",
        "base_url",
        "api_compat",
        "context_limit",
        "service_tier",
    )

    #: Safelist for the public ``preset`` block. ``active`` and ``default`` are
    #: path strings, ``allowed`` is a list of path strings — none of these
    #: carry secrets, but pinning the safelist guards against future preset
    #: schema growth that might introduce sensitive fields.
    _PRESET_PUBLIC_KEYS = ("active", "default", "allowed")

    def _build_manifest(self) -> dict:
        """Extend kernel manifest with capabilities, preset, and combo.

        Strips sensitive fields (api_key, etc.) from capability kwargs
        so they don't leak into the system prompt or outgoing mail identity.
        Adds a sanitized ``preset`` block (active/default/allowed) and
        re-applies the ``llm`` safelist for defense-in-depth — even if a
        future LLMService grew a sensitive attribute, the manifest never
        carries anything outside ``_LLM_PUBLIC_KEYS``.
        """
        data = super()._build_manifest()
        caps = getattr(self, "_capabilities", None)
        if caps:
            # Re-apply the recursive redaction at the serialization boundary.
            # _capabilities is normally already sanitized, but keeping this
            # boundary defensive prevents a nested credential from leaking if a
            # caller or future setup path stores a raw mapping.
            from collections.abc import Mapping as ABCMapping
            omitted = object()

            def _safe(value: Any) -> Any:
                if isinstance(value, ABCMapping):
                    clean = {}
                    for key, item in value.items():
                        if key in self._SENSITIVE_KEYS:
                            continue
                        safe_item = _safe(item)
                        if safe_item is not omitted:
                            clean[key] = safe_item
                    return clean
                if isinstance(value, (list, tuple, set, frozenset)):
                    return [safe_item for item in value if (safe_item := _safe(item)) is not omitted]
                if isinstance(value, (str, int, float, bool, type(None))):
                    return value
                return omitted

            data["capabilities"] = [
                (name, _safe(kw)) for name, kw in caps
            ]
        if self._combo_name:
            data["combo"] = self._combo_name

        # Enforce the llm safelist a second time — the kernel layer already
        # filters, but a subclass override or future service shape might add
        # a non-safelisted attribute. Doing it here means anything written
        # to disk is guaranteed safelist-only.
        if isinstance(data.get("llm"), dict):
            data["llm"] = {
                k: v for k, v in data["llm"].items()
                if k in self._LLM_PUBLIC_KEYS
            }
            if not data["llm"]:
                del data["llm"]

        preset = self._read_preset_from_init()
        if preset:
            data["preset"] = preset

        return data

    def _read_preset_from_init(self) -> dict:
        """Read ``manifest.preset`` from init.json and sanitize.

        Returns ``{}`` if init.json is missing, unreadable, or has no preset
        block — bare init.json (e.g. tests) and pre-preset deployments both
        silently fall through. Never raises: a corrupt init.json must not
        break manifest writes.

        Filters to ``_PRESET_PUBLIC_KEYS`` and string/list-of-string values so
        the disk manifest never carries anything the safelist doesn't explicitly
        allow.
        """
        import json

        init_path = self._working_dir / "init.json"
        if not init_path.is_file():
            return {}
        try:
            raw = json.loads(init_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        manifest = raw.get("manifest") if isinstance(raw, dict) else None
        if not isinstance(manifest, dict):
            return {}
        preset = manifest.get("preset")
        if not isinstance(preset, dict):
            return {}
        clean: dict = {}
        for key in self._PRESET_PUBLIC_KEYS:
            if key not in preset:
                continue
            val = preset[key]
            if key in {"active", "default"}:
                if isinstance(val, str) and val:
                    clean[key] = val
                continue
            if key == "allowed" and isinstance(val, list):
                allowed = [item for item in val if isinstance(item, str) and item]
                if allowed:
                    clean[key] = allowed
        return clean

    #: Preference order for picking one display handle per MCP identity —
    #: mirrors the account-identity safelist in ``services.mcp_registry``
    #: (``IDENTITY_SAFE_ACCOUNT_KEYS``); no new field is invented here.
    _HANDLE_KEY_PREFERENCE = ("bot_username", "username", "alias", "display_name")

    def _build_agent_record_extra(self) -> dict:
        """Curate ``handles``/``integrations`` for the Agent record.

        Reads only already-redacted, already-public sources: the MCP
        registry (``services.mcp_registry.read_registry`` — name/summary/
        transport, no credentials) for integration labels, and per-MCP
        identity documents (``read_identities`` — the same
        ``IDENTITY_SAFE_ACCOUNT_KEYS`` allowlist the system prompt itself
        renders) for one verified consumer-facing handle per integration.
        Best-effort: any read failure yields an empty extra rather than
        breaking the record write.
        """
        try:
            from .services.mcp_registry import read_identities, read_registry

            records, _problems = read_registry(self._working_dir)
            identities = read_identities(self._working_dir)
        except Exception:
            return {}

        integrations = []
        handles: dict[str, str] = {}
        for record in records:
            name = record.get("name")
            if not isinstance(name, str) or not name:
                continue
            integrations.append({
                "name": name,
                "transport": record.get("transport"),
                "connected": name in getattr(self, "_mcp_init_specs", {}),
            })
            identity = identities.get(name)
            accounts = identity.get("accounts") if isinstance(identity, dict) else None
            if not accounts or not isinstance(accounts, list):
                continue
            first = accounts[0]
            if not isinstance(first, dict):
                continue
            for key in self._HANDLE_KEY_PREFERENCE:
                value = first.get(key)
                if isinstance(value, str) and value:
                    handles[name] = value
                    break

        return {"handles": handles, "integrations": integrations}

    def _build_system_prompt(self) -> str:
        """Override kernel's prompt builder to inject app tool descriptions.

        ``base_prompt`` is Psyche's third-party (application / recipe / preset)
        injection point, resolved from ``settings/psyche.json`` by
        ``_reload_prompt_sections`` into ``self._base_prompt``. The kernel
        builder renders it right after the raw ``principle`` section and before
        the rest of Batch 1.
        """
        self._refresh_tool_inventory_section()
        _refresh_meta_guidance_section(self)
        return build_system_prompt(
            prompt_manager=self._prompt_manager,
            base_prompt=getattr(self, "_base_prompt", ""),
            language=self._config.language,
            activeness=self._config.activeness,
        )

    def _build_system_prompt_batches(self) -> list[str]:
        """Override kernel's batched builder to inject app tool descriptions."""
        from lingtai.kernel.prompt import build_system_prompt_batches
        self._refresh_tool_inventory_section()
        _refresh_meta_guidance_section(self)
        return build_system_prompt_batches(
            prompt_manager=self._prompt_manager,
            base_prompt=getattr(self, "_base_prompt", ""),
            language=self._config.language,
            activeness=self._config.activeness,
        )

    def _load_mcp_from_workdir(self) -> None:
        """Auto-load MCP servers from two sources, in order:

        1. ``working_dir/mcp/servers.json`` — legacy, ungated. Loaded as-is.
        2. ``init.json`` top-level ``mcp`` field — gated by the per-agent
           registry at ``working_dir/mcp_registry.jsonl``. An init.json mcp
           entry whose name is not in the registry is skipped with a warning.

           If the registry record's ``source`` is exactly ``"lingtai-curated"``
           (imap/telegram/feishu/wechat/whatsapp/cloud_mail), the entry's
           ``init.json`` config is *activation-only*: its presence activates
           the addon, and any non-launch ``env`` it carries (account/config
           keys such as ``LINGTAI_IMAP_CONFIG``) is passed through. The
           complete launcher — transport/``type``, interpreter, module
           ``args``, and the ``lingtai`` import source — is instead derived
           fresh from this Agent's own currently-imported ``mcp_catalog.json``
           (``services.mcp_registry.load_catalog``) and this Agent's own
           ``sys.executable`` / source root, by ``_resolve_curated_launch``.
           A stale ``command``/``args``/``type`` or ``env.PYTHONPATH`` frozen
           into init.json from a prior venv is never read for the launch —
           each is a read-compatible legacy field, ignored with a bounded
           warning if present, never rewritten and never migrated on disk.
           This is necessary, not merely defense in depth: the MCP stdio
           transport does not inherit ``PYTHONPATH`` from the parent process,
           so even the right interpreter could otherwise still resolve
           ``lingtai`` from that interpreter's own site-packages instead of
           the source this Agent actually imported. If the catalog cannot
           supply a safe stdio launcher for a name registered as curated
           (missing entry, non-stdio transport, malformed ``args`` — e.g. a
           kernel/registry mismatch), the entry is skipped with a warning
           rather than falling back to any stale launcher field.

        Both sources accept stdio and HTTP entries:

            {
              "vision-server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@z_ai/mcp-server"],
                "env": {"Z_AI_API_KEY": "...", "Z_AI_MODE": "ZAI"}
              },
              "web-search": {
                "type": "http",
                "url": "https://api.z.ai/api/mcp/web_search_prime/mcp",
                "headers": {"Authorization": "Bearer ..."}
              }
            }

        The ``type`` field defaults to ``"stdio"`` if omitted.

        Side effect: every init.json mcp entry whose name was registered
        is recorded in ``self._mcp_init_specs``. The ``_retry_failed_mcps``
        helper consults this dict on ``system(action="refresh")`` to detect
        and re-spawn MCPs whose subprocess died (issue #34).
        """
        if "mcp" in self._forced_disable:
            self._mcp_init_specs = {}
            return

        import json
        import sys

        from lingtai.kernel.logging import get_logger
        logger = get_logger()

        # The root directory containing this Agent's own currently-imported
        # `lingtai` package (e.g. `<checkout>/src` for an editable/source
        # checkout, or a venv's site-packages for an installed wheel). A
        # curated init.json entry's child gets this as its *entire*
        # PYTHONPATH below (see `_resolve_curated_launch`) — the running
        # kernel's own source, never whatever init.json stored.
        curated_source_root = str(Path(__file__).resolve().parent.parent)

        def _resolve_curated_launch(name: str, cfg: dict) -> dict | None:
            """Build the complete stdio launcher for a curated init.json mcp
            entry from the current kernel's packaged ``mcp_catalog.json``.

            Returns the effective launch cfg, or ``None`` if the catalog
            cannot supply a safe launcher for ``name`` — the caller must
            skip the entry rather than fall back to any field in ``cfg``.

            ``cfg`` remains the activation/account config: only its ``env``
            (minus the legacy ``PYTHONPATH`` key, which this pin owns) is
            carried into the effective launch. ``command``, ``args``, and
            ``type`` are read-compatible legacy inputs — present in older
            configs, or copy-pasted from the addon docs' worked example —
            and are ignored here with a bounded warning, never rewritten and
            never migrated on disk.
            """
            from .services.mcp_registry import load_catalog
            entry = load_catalog().get(name)
            if not isinstance(entry, dict) or entry.get("transport") != "stdio":
                logger.warning(
                    "[%s] init.json mcp %r: registered source is "
                    "lingtai-curated but mcp_catalog.json has no stdio "
                    "entry for it — skipping rather than trusting a stale "
                    "launcher. Update the kernel package.",
                    self.agent_name, name,
                )
                return None
            args = entry.get("args")
            if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
                logger.warning(
                    "[%s] init.json mcp %r: mcp_catalog.json's stdio entry "
                    "has malformed args — skipping rather than trusting a "
                    "stale launcher. Update the kernel package.",
                    self.agent_name, name,
                )
                return None

            legacy_fields = [k for k in ("command", "args", "type") if k in cfg]
            existing_env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
            if "PYTHONPATH" in existing_env:
                legacy_fields.append("env.PYTHONPATH")
            if legacy_fields:
                logger.warning(
                    "[%s] init.json mcp %r: legacy launcher field(s) %s "
                    "ignored — this lingtai-curated entry's launcher (type, "
                    "interpreter, args, import source) always comes from "
                    "the running kernel's own mcp_catalog.json, never from "
                    "init.json. Only non-launch env/account config passes "
                    "through. See reference/curated-addons.md.",
                    self.agent_name, name, ", ".join(legacy_fields),
                )

            account_env = {k: v for k, v in existing_env.items() if k != "PYTHONPATH"}
            return {
                "type": "stdio",
                "command": sys.executable,
                "args": list(args),
                "env": {**account_env, "PYTHONPATH": curated_source_root},
            }

        # Per-name tracking of init.json MCP launches. Populated below and
        # consulted by `_retry_failed_mcps`. Reset on every load so that
        # entries removed from init.json drop out of the retry pool.
        self._mcp_init_specs: dict[str, dict] = {}
        # Parent MCP tool names are tracked only so daemon can prevent
        # tasks[].tools from leaking parent MCP tools. Task-level daemon MCP
        # access must come from complete registrations in tasks[].mcp.
        self._mcp_tool_names: set[str] = set()

        # LICC env injection — every spawned MCP gets these so it can
        # locate the agent's working dir + know its own registry name and
        # write events into the LICC inbox. User-supplied env in cfg wins.
        licc_env = {
            "LINGTAI_AGENT_DIR": str(self._working_dir),
        }

        def _spawn(name: str, cfg: dict, source: str) -> object | None:
            """Return the MCPClient/HTTPMCPClient on success, None on failure."""
            # Snapshot the client list pre-spawn so we can identify the new
            # client connect_mcp* appended (avoids changing connect_mcp's
            # public return contract — it returns tool names, not the client).
            pre_clients = list(getattr(self, "_mcp_clients", []) or [])
            try:
                server_type = cfg.get("type", "stdio")
                if server_type == "http":
                    if "url" not in cfg:
                        return None
                    tools = self.connect_mcp_http(
                        url=cfg["url"],
                        headers=cfg.get("headers"),
                    )
                else:
                    if "command" not in cfg:
                        return None
                    # Merge: LICC defaults < per-MCP env (user-supplied).
                    # Add LINGTAI_MCP_NAME per-spawn so each MCP knows its
                    # own registry name without needing to be told elsewhere.
                    merged_env = {
                        **licc_env,
                        "LINGTAI_MCP_NAME": name,
                        **(cfg.get("env") or {}),
                    }
                    tools = self.connect_mcp(
                        command=cfg["command"],
                        args=cfg.get("args"),
                        env=merged_env,
                    )
                logger.info("[%s] MCP %s (%s): loaded %d tools (%s)",
                            self.agent_name, name, source, len(tools),
                            ", ".join(tools))
                # Identify the client connect_mcp* just appended.
                post_clients = list(getattr(self, "_mcp_clients", []) or [])
                new_clients = post_clients[len(pre_clients):]
                # connect_mcp / connect_mcp_http always append exactly one.
                return new_clients[-1] if new_clients else None
            except Exception as e:
                logger.warning("[%s] MCP %s (%s): failed to load: %s",
                               self.agent_name, name, source, e)
                return None

        # Source 1: legacy mcp/servers.json
        legacy_config = self._working_dir / "mcp" / "servers.json"
        if legacy_config.is_file():
            try:
                servers = json.loads(legacy_config.read_text(encoding="utf-8"))
                if isinstance(servers, dict):
                    for name, cfg in servers.items():
                        if isinstance(cfg, dict):
                            _spawn(name, cfg, source="mcp/servers.json")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("[%s] mcp/servers.json: failed to read: %s",
                               self.agent_name, e)

        # Source 2: init.json top-level mcp section, gated by registry.
        init_path = self._working_dir / "init.json"
        if not init_path.is_file():
            return
        try:
            init_data = json.loads(init_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        init_mcp = init_data.get("mcp")
        if not isinstance(init_mcp, dict) or not init_mcp:
            return

        # Cross-reference against the registry.
        try:
            from .services.mcp_registry import read_registry
            registered, _problems = read_registry(self._working_dir)
            registered_sources = {r["name"]: r.get("source") for r in registered}
        except Exception as e:
            logger.warning("[%s] mcp registry read failed: %s",
                           self.agent_name, e)
            registered_sources = {}

        for name, cfg in init_mcp.items():
            if not isinstance(cfg, dict):
                continue
            if name not in registered_sources:
                logger.warning(
                    "[%s] init.json mcp %r: skipped — not in mcp_registry.jsonl. "
                    "Register it first (see mcp-manual skill).",
                    self.agent_name, name,
                )
                continue
            # Curated MCPs (imap/telegram/feishu/wechat/...) must run the
            # exact launcher the running kernel's own catalog defines, never
            # whatever command/args/type/PYTHONPATH init.json happens to
            # store — see `_resolve_curated_launch` and the docstring above.
            # Registry membership is the "curated" signal (mcp_catalog.json's
            # `source`), not the init.json command string.
            if registered_sources.get(name) == "lingtai-curated":
                cfg = _resolve_curated_launch(name, cfg)
                if cfg is None:
                    # Fail loud/skip: do not spawn and do not track for
                    # retry — there is no safe launcher to retry with.
                    continue
            client = _spawn(name, cfg, source="init.json:mcp")
            # Record every registered init.json mcp entry — failures (client
            # is None) and successes alike — so `_retry_failed_mcps` can
            # tell which ones to re-attempt vs leave alone.
            self._mcp_init_specs[name] = {
                "cfg": cfg,
                "source": "init.json:mcp",
                "client": client,
            }

    def _retry_failed_mcps(self) -> dict:
        """Re-spawn any init.json MCP whose subprocess is dead or never started.

        Walks ``self._mcp_init_specs`` (populated by ``_load_mcp_from_workdir``)
        and, for each entry whose tracked client is missing or visibly
        unhealthy, tears it down and re-attempts the spawn with the original
        config. Returns a report dict ``{retried: [...], recovered: [...],
        still_failed: [...], healthy: [...]}``.

        Why this exists: ``system(action="refresh")`` is the documented
        "fix config → refresh" recovery path for curated addons (imap,
        telegram, feishu, wechat). Without this retry, an MCP that exited
        during initial boot stays dead until full process restart — see
        Lingtai-AI/lingtai#34.

        Health check: missing client (boot-time spawn raised) is the
        clearest signal. For clients that registered but whose subprocess
        later died, ``MCPClient.is_connected()`` is the cheapest probe — it
        returns False when the background loop has exited (which happens
        when the stdio transport closes due to subprocess death).
        """
        from lingtai.kernel.logging import get_logger
        logger = get_logger()

        specs = getattr(self, "_mcp_init_specs", None)
        if not specs:
            return {"retried": [], "recovered": [], "still_failed": [],
                    "healthy": []}

        retried: list[str] = []
        recovered: list[str] = []
        still_failed: list[str] = []
        healthy: list[str] = []

        # LICC env (must mirror _load_mcp_from_workdir).
        licc_env = {
            "LINGTAI_AGENT_DIR": str(self._working_dir),
        }

        for name, spec in list(specs.items()):
            client = spec.get("client")
            cfg = spec.get("cfg") or {}
            source = spec.get("source", "init.json:mcp")

            # Health: client present AND its session is connected.
            if client is not None and getattr(client, "is_connected", lambda: False)():
                healthy.append(name)
                continue

            retried.append(name)
            self._log("mcp_retry_attempt", name=name, source=source)

            # Tear down the dead client (if any). connect_mcp* will append a
            # fresh one. We also remove the dead client from
            # self._mcp_clients so stop()/refresh teardown does not double-
            # close it.
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                clients = getattr(self, "_mcp_clients", None)
                if isinstance(clients, list):
                    try:
                        clients.remove(client)
                    except ValueError:
                        pass
                # Drop this client's reverse routes and advertised metadata now,
                # while we still know which tools were its. A reconnect below
                # may fail (see the `except` branch), and without this the
                # closed client would keep a live-looking route and metadata.
                # Other clients' entries are untouched.
                self._forget_mcp_client_tools(client)

            # Re-attempt the spawn. Mirrors the dispatch in
            # `_load_mcp_from_workdir._spawn` — kept inline (not factored)
            # to avoid leaking the closure-captured `licc_env` / logger.
            pre_clients = list(getattr(self, "_mcp_clients", []) or [])
            new_client: object | None = None
            try:
                server_type = cfg.get("type", "stdio")
                if server_type == "http":
                    if "url" not in cfg:
                        raise ValueError("http transport requires 'url'")
                    self.connect_mcp_http(
                        url=cfg["url"],
                        headers=cfg.get("headers"),
                    )
                else:
                    if "command" not in cfg:
                        raise ValueError("stdio transport requires 'command'")
                    merged_env = {
                        **licc_env,
                        "LINGTAI_MCP_NAME": name,
                        **(cfg.get("env") or {}),
                    }
                    self.connect_mcp(
                        command=cfg["command"],
                        args=cfg.get("args"),
                        env=merged_env,
                    )
                post_clients = list(getattr(self, "_mcp_clients", []) or [])
                new = post_clients[len(pre_clients):]
                new_client = new[-1] if new else None
            except Exception as e:
                logger.warning("[%s] MCP %s (%s): retry failed: %s",
                               self.agent_name, name, source, e)
                self._log("mcp_retry_failed", name=name, error=str(e))
                spec["client"] = None
                still_failed.append(name)
                continue

            spec["client"] = new_client
            if new_client is not None and getattr(
                new_client, "is_connected", lambda: False)():
                logger.info("[%s] MCP %s (%s): retry recovered",
                            self.agent_name, name, source)
                self._log("mcp_retry_recovered", name=name)
                recovered.append(name)
            else:
                # Spawn returned without raising but the client is not
                # connected — treat as still failed. `connect_mcp*` already
                # registered this replacement's tools, so tear it down the same
                # way the pre-reconnect teardown handles the old client:
                # otherwise a server this retry reports as failed would keep a
                # live-looking reverse route and advertised metadata.
                if new_client is not None:
                    try:
                        new_client.close()
                    except Exception:
                        pass
                    clients = getattr(self, "_mcp_clients", None)
                    if isinstance(clients, list):
                        try:
                            clients.remove(new_client)
                        except ValueError:
                            pass
                    self._forget_mcp_client_tools(new_client)
                spec["client"] = None
                self._log("mcp_retry_failed", name=name,
                          error="client not connected after retry")
                still_failed.append(name)

        return {
            "retried": retried,
            "recovered": recovered,
            "still_failed": still_failed,
            "healthy": healthy,
        }

    def _cpr_agent(self, address: str) -> bool | dict | None:
        """Resuscitate a suspended agent by launching it as a detached process.

        Uses the resolved venv Python to run `lingtai run <dir>`. A fresh
        heartbeat confirms the launch; an observed child exit is an explicit
        error. A child still running after the bounded confirmation interval is
        logged as unconfirmed and returns the established truthy sentinel.
        """
        import shlex
        import subprocess
        import time
        from lingtai.adapters.posix.agent_presence import PosixAgentPresenceStoreAdapter
        from lingtai.kernel.agent_presence import (
            is_agent as _presence_is_agent,
            observe_alive as _presence_observe_alive,
        )
        from lingtai.kernel.handshake import resolve_address
        from lingtai.venv_resolve import resolve_venv, venv_python

        base_dir = self._working_dir.parent
        target = resolve_address(address, base_dir)
        target_presence = PosixAgentPresenceStoreAdapter(target)
        if not _presence_is_agent(target_presence.observe_manifest()):
            return None

        init_path = target / "init.json"
        if not init_path.is_file():
            self._log("cpr_no_init", path=str(target))
            return None

        # Clean stale signal files so a CPR'd agent boots cleanly.
        for sig in (".suspend", ".sleep", ".interrupt"):
            sig_file = target / sig
            if sig_file.is_file():
                sig_file.unlink(missing_ok=True)

        # Resolve Python: target's init.json venv_path → global runtime
        try:
            import json as _json
            target_data = _json.loads(init_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            target_data = None
        venv_dir = resolve_venv(target_data)
        python = venv_python(venv_dir)
        cmd = [python, "-m", "lingtai", "run", str(target)]

        def _tail_log(limit: int = 4000) -> str:
            try:
                data = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
            return data[-limit:]

        logs_dir = target / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "cpr_relaunch.log"
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        quoted_cmd = " ".join(shlex.quote(str(part)) for part in cmd)

        with log_path.open("ab", buffering=0) as log_fh:
            log_fh.write(f"\n--- CPR launch {timestamp}: {quoted_cmd} ---\n".encode("utf-8"))
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                **_detached_spawn_kwargs(),
            )

        self._log("cpr_launched", target=str(target), pid=proc.pid, log=str(log_path))

        def _launch_failure(code: int) -> dict:
            tail = _tail_log()
            self._log("cpr_failed", target=str(target), pid=proc.pid, exit_code=code, log=str(log_path))
            message = f"CPR launch exited before heartbeat (exit code {code}); see {log_path}"
            if tail.strip():
                message += f"\n\nLast log output:\n{tail}"
            return {"error": True, "message": message, "exit_code": code, "log": str(log_path)}

        # Poll with the shared liveness window, not a local value: karma's
        # CPR gate and this relaunch poll must use the same threshold, so a
        # heartbeat satisfying the poll is newer than the gate check and was
        # written by the relaunched process. Keep the startup confirmation
        # deadline at least 10 seconds and at least twice that shared window.
        deadline = time.time() + max(10.0, 2 * HEARTBEAT_LIVENESS_SECONDS)
        while time.time() < deadline:
            if _presence_observe_alive(
                target_presence,
                wall_now=time.time(),
            ):
                self._log("cpr_alive", target=str(target), pid=proc.pid)
                return True
            code = proc.poll()
            if code is not None:
                return _launch_failure(code)
            time.sleep(0.2)

        # One final presence observation accepts a heartbeat written at the
        # confirmation boundary. Absent that evidence, only an observed child
        # exit proves CPR launch failure; a still-running detached child may
        # still be initializing and remains unconfirmed rather than failed.
        if _presence_observe_alive(
            target_presence,
            wall_now=time.time(),
        ):
            self._log("cpr_alive", target=str(target), pid=proc.pid)
            return True
        code = proc.poll()
        if code is not None:
            return _launch_failure(code)
        self._log("cpr_launch_unconfirmed", target=str(target), pid=proc.pid, log=str(log_path))
        return True

    def start(self) -> None:
        super().start()
        # LICC poller: watch .mcp_inbox/ for events from out-of-process MCPs.
        from .services.mcp_inbox import MCPInboxPoller
        self._mcp_inbox_poller = MCPInboxPoller(self)
        self._mcp_inbox_poller.start()

    def _dispatch_tool(self, tc: ToolCall) -> dict:
        """Restore canonical reasoning only for mounted strict MCP ToolFamilies.

        ``ToolExecutor`` deliberately renames model-facing ``reasoning`` to the
        private ``_reasoning`` audit key before every dispatch.  Ordinary
        in-process and legacy MCP handlers consume that existing shape.  A native
        MCP ToolFamily instead validates the original closed LTP-v2 envelope, so
        the wrapper composition root restores the public key immediately before
        the inherited handler dispatch.  Third-party MCP tools with flat,
        auto-generated parameter schemas declare no ``reasoning``/``_reasoning``
        at all; forwarding the audit key would fail their strict schema-validated
        calls, so it is dropped instead.  The input ToolCall is never mutated.
        """
        if tc.name in getattr(self, "_mcp_tool_names", set()):
            from .services.mcp import is_strict_ltp_v2_family_schema

            schemas = [schema for schema in self._tool_schemas if schema.name == tc.name]
            if len(schemas) == 1 and "_reasoning" in tc.args:
                params = schemas[0].parameters
                if is_strict_ltp_v2_family_schema(params):
                    args = dict(tc.args)
                    reasoning = args.pop("_reasoning")
                    args.setdefault("reasoning", reasoning)
                    tc = ToolCall(name=tc.name, args=args, id=tc.id)
                elif not _schema_declares_reasoning_param(params):
                    # Third-party MCP tools expose flat, auto-generated parameter
                    # schemas with no LTP-v2 envelope and no reasoning parameter
                    # of their own; the audit key has nowhere to go, so drop it
                    # instead of failing a strict schema-validated call.
                    args = dict(tc.args)
                    args.pop("_reasoning", None)
                    tc = ToolCall(name=tc.name, args=args, id=tc.id)
        return super()._dispatch_tool(tc)

    def _expand_agent_placeholders(self, value):
        """Substitute per-agent placeholders in an MCP launch string.

        Lets a single shared MCP registry template scope each agent to its own
        namespace without per-agent hand-editing — e.g. per-agent data roots
        ``--data-root /agents/{agent_id}/data``. ``{agent_id}`` and
        ``{agent_address}`` resolve to the agent's stable working-dir name (its
        address); ``{agent_dir}`` resolves to the absolute working directory.
        Non-string values and strings without a placeholder pass through
        unchanged, so ordinary MCP args are never touched.
        """
        if not isinstance(value, str) or "{" not in value:
            return value
        agent_id = self._working_dir.name
        return (
            value.replace("{agent_id}", agent_id)
            .replace("{agent_address}", agent_id)
            .replace("{agent_dir}", str(self._working_dir))
        )

    def _validate_external_mcp_tools(self, tools: list[dict]) -> None:
        """Reject a server catalog that advertises a kernel-owned official name.

        The shared ``add_tool`` boundary is authoritative, but preflighting the
        complete external catalog keeps a rejected connection from publishing
        earlier tools or leaving a client/route sidecar behind.
        """
        from lingtai.kernel.tool_plugin import (
            OFFICIAL_TOOL_PLUGIN_NAMES,
            OfficialToolNameCollisionError,
        )

        collisions = [
            tool.get("name")
            for tool in tools
            if isinstance(tool, dict)
            and tool.get("name") in OFFICIAL_TOOL_PLUGIN_NAMES
        ]
        if collisions:
            names = ", ".join(repr(name) for name in collisions)
            raise OfficialToolNameCollisionError(
                f"external MCP advertised reserved official tool name(s): {names}"
            )

    def _discard_mcp_client(self, client: Any) -> None:
        """Close and forget one client after a failed external mount attempt."""
        self._forget_mcp_client_tools(client)
        clients = getattr(self, "_mcp_clients", None)
        if isinstance(clients, list) and client in clients:
            clients.remove(client)
        try:
            client.close()
        except Exception:
            pass

    def connect_mcp(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> list[str]:
        """Connect to an MCP server and auto-register all its tools.

        Args:
            command: Executable to run (e.g., "uvx", "xhelio-spice-mcp").
            args: Arguments to the command.
            env: Environment variables for the subprocess.

        Returns:
            List of registered tool names.
        """
        from .services import mcp as mcp_service
        from .services.mcp import MCPClient
        from lingtai.kernel.logging import get_logger as _get_logger
        logger = _get_logger()

        # Expand per-agent placeholders (e.g. {agent_id}) so a shared registry
        # template gives each agent its own scope. See _expand_agent_placeholders.
        command = self._expand_agent_placeholders(command)
        if args:
            args = [self._expand_agent_placeholders(a) for a in args]
        if env:
            env = {k: self._expand_agent_placeholders(v) for k, v in env.items()}

        # Dedupe by identity: if an identical server is already connected,
        # reuse it instead of spawning a duplicate subprocess. Without this,
        # every boot/refresh/molt respawn adds another stdio pair that close()
        # cannot reliably terminate on Windows (venv shim -> interpreter).
        identity = None
        default_name = getattr(MCPClient, "_default_name", None)
        if default_name is not None:
            identity = default_name(command, args or [])
        if identity is not None:
            for existing in getattr(self, "_mcp_clients", []) or []:
                if (
                    getattr(existing, "name", None) == identity
                    and existing.is_connected()
                ):
                    logger.info("MCP %s already connected; reusing client", identity)
                    return []

        client = MCPClient(command=command, args=args, env=env)
        client.start()

        # Track for cleanup
        if not hasattr(self, "_mcp_clients"):
            self._mcp_clients: list = []
        self._mcp_clients.append(client)

        # List tools and register each one
        try:
            tools = client.list_tools()
            self._validate_external_mcp_tools(tools)
        except Exception:
            self._discard_mcp_client(client)
            raise
        return self._mount_mcp_tools(client, tools, mcp_service)

    def mount_session_mcp_stdio(self, configs):
        """Mount one process-local stdio MCP overlay and return its owner lease."""
        from .services.session_mcp import mount_session_mcp_stdio
        return mount_session_mcp_stdio(self, tuple(configs))

    def _mount_mcp_tools(self, client: Any, tools: list[dict], mcp_service: Any) -> list[str]:
        """Mount one fully preflighted MCP catalog atomically for this client.

        A catalog can pass reserved-name preflight and still fail while a tool
        is mounted (for example, a sealed Agent or malformed later record).
        Snapshot all model-facing and MCP sidecar state, then restore it after
        closing/removing the just-started client on *any* publication failure.
        This is deliberately narrower than a process-wide transaction: it
        protects one connection and preserves earlier live clients unchanged.
        """
        # The caller appends the just-started client before preflight; it is
        # intentionally absent from the pre-connection snapshot restored below.
        clients_before = [
            existing
            for existing in getattr(self, "_mcp_clients", [])
            if existing is not client
        ]
        handlers_before = dict(self._tool_handlers)
        schemas_before = list(self._tool_schemas)
        metadata_before = {
            name: (dict(value) if isinstance(value, dict) else value)
            for name, value in getattr(self, "_mcp_tool_metadata", {}).items()
        }
        names_before = set(getattr(self, "_mcp_tool_names", set()))
        routes_before = dict(getattr(self, "_mcp_clients_by_tool", {}))
        collisions_before = set(getattr(self, "_mcp_tool_collisions", set()))

        try:
            registered: list[str] = []
            metadata: dict[str, dict] = {}
            for tool in tools:
                name = tool["name"]
                schema = tool.get("schema", {})

                def _make_handler(c: Any, tool_name: str, input_schema: Any):
                    def handler(tool_args: dict) -> dict:
                        prepared = mcp_service.prepare_mcp_tool_arguments(
                            tool_args, input_schema
                        )
                        return c.call_tool(tool_name, prepared)
                    return handler

                metadata[name] = mcp_service.tool_metadata(tool)
                self.add_tool(
                    name,
                    schema=schema,
                    handler=_make_handler(client, name, schema),
                    description=tool.get("description", ""),
                )
                registered.append(name)

            self._record_mcp_tool_metadata(metadata)
            if not hasattr(self, "_mcp_tool_names"):
                self._mcp_tool_names = set()
            self._mcp_tool_names.update(registered)
            for name in registered:
                self._record_mcp_tool_owner(name, client)
            self._maybe_setup_task_card_controller()
            return registered
        except Exception:
            # Close/remove the new transport first; then restore every state
            # surface touched by add_tool and the MCP sidecars. This also
            # removes any routes/metadata recorded before a later failure.
            self._discard_mcp_client(client)
            self._mcp_clients = clients_before
            self._tool_handlers.clear()
            self._tool_handlers.update(handlers_before)
            self._tool_schemas = schemas_before
            self._mcp_tool_metadata = metadata_before
            self._mcp_tool_names = names_before
            self._mcp_clients_by_tool = routes_before
            self._mcp_tool_collisions = collisions_before
            try:
                if self._chat is not None:
                    self._chat.update_tools(self._build_tool_schemas())
            except Exception:
                pass
            self._token_decomp_dirty = True
            raise

    def _record_mcp_tool_owner(self, name: str, client: Any) -> None:
        """Map one MCP tool name while retaining same-surface collision provenance."""
        if not hasattr(self, "_mcp_clients_by_tool"):
            self._mcp_clients_by_tool: dict[str, Any] = {}
        if not hasattr(self, "_mcp_tool_collisions"):
            self._mcp_tool_collisions: set[str] = set()
        previous = self._mcp_clients_by_tool.get(name)
        if previous is not None and previous is not client:
            self._mcp_tool_collisions.add(name)
        self._mcp_clients_by_tool[name] = client

    def _fail_closed_task_card_binding(self) -> None:
        """Retired Telegram-controller cleanup hook kept for refresh compatibility."""
        controller = getattr(self, "_task_card_controller", None)
        if controller is None:
            return
        handlers = getattr(self, "_tool_handlers", {})
        handler = handlers.get("task_card")
        if getattr(handler, "__self__", None) is not controller:
            return
        client = getattr(self, "_mcp_clients_by_tool", {}).get("task_card")
        if client is None:
            self._tool_handlers.pop("task_card", None)
            return

        def raw_handler(tool_args: dict, *, _client=client) -> dict:
            return _client.call_tool("task_card", tool_args)

        # Preserve the raw package schema/description; replace only the host-bound
        # handler with the current MCP owner's ordinary public dispatch.
        self.add_tool("task_card", handler=raw_handler)

    def task_card_max_refreshes(self) -> int:
        """Read the shared agent-wide Task Card fuse without owning its controller."""
        default = 1000
        try:
            data = json.loads(
                (self._working_dir / "telegram" / "taskcard.json").read_text(
                    encoding="utf-8"
                )
            )
            value = data.get("max_refreshes") if isinstance(data, dict) else None
            return (
                value
                if type(value) is int and 0 < value <= default
                else default
            )
        except (OSError, ValueError, TypeError):
            return default

    def _record_mcp_tool_metadata(self, metadata: dict[str, dict]) -> None:
        """Store the advertised MCP metadata for newly registered tools.

        Keyed by registered tool name so it shares the lifetime of
        ``_mcp_clients_by_tool``: both are rebuilt from scratch on deep refresh
        (see ``refresh``) and both are dropped by ``stop``, so a torn-down
        client can never leave stale metadata visible. Re-registering a name
        replaces its entry rather than merging into it.
        """
        if not hasattr(self, "_mcp_tool_metadata"):
            self._mcp_tool_metadata: dict[str, dict] = {}
        self._mcp_tool_metadata.update(metadata)

    def _forget_mcp_client_tools(self, client: Any) -> None:
        """Drop the reverse routes and metadata belonging to one MCP client.

        Used when a single client is torn down on its own — the failed-retry
        path — where the whole-surface resets in ``refresh``/``stop`` do not
        apply. Entries owned by other clients are preserved, so tearing down
        one unhealthy server never blinds the rest of the surface.
        """
        routes = getattr(self, "_mcp_clients_by_tool", None)
        if not isinstance(routes, dict):
            return
        owned = [name for name, owner in routes.items() if owner is client]
        metadata = getattr(self, "_mcp_tool_metadata", None)
        collisions = getattr(self, "_mcp_tool_collisions", None)
        for name in owned:
            routes.pop(name, None)
            if isinstance(metadata, dict):
                metadata.pop(name, None)
            # #1081 records a same-surface collision per tool name. Once this
            # client's route is gone the recorded collision no longer describes
            # a live pair, so drop it with the rest of the client's state.
            if isinstance(collisions, set):
                collisions.discard(name)

    def mcp_tool_metadata(self, name: str) -> dict | None:
        """Return advertised MCP metadata for one registered tool, or ``None``.

        The returned mapping is a copy: mutating it cannot reach the stored
        record. Fields absent from the server's advertisement are absent here;
        an empty dict means the tool advertised none.
        """
        stored = getattr(self, "_mcp_tool_metadata", {}).get(name)
        return deepcopy(stored) if stored is not None else None

    def _maybe_setup_task_card_controller(self) -> None:
        """Retired hook.

        ``task_card`` is now owned by the intrinsic tools registry. Telegram MCP
        connections no longer install or replace a host-bound controller here.
        """
        Agent._fail_closed_task_card_binding(self)

    def connect_mcp_http(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> list[str]:
        """Connect to a remote HTTP MCP server and auto-register all its tools.

        Args:
            url: HTTP endpoint of the MCP server.
            headers: HTTP headers (e.g., {"Authorization": "Bearer ..."}).

        Returns:
            List of registered tool names.
        """
        from .services import mcp as mcp_service
        from .services.mcp import HTTPMCPClient
        from lingtai.kernel.logging import get_logger

        logger = get_logger()

        url = self._expand_agent_placeholders(url)
        if headers:
            headers = {
                name: self._expand_agent_placeholders(value)
                for name, value in headers.items()
            }

        # Dedupe by endpoint: an identical HTTP server already connected must
        # not get a second connection (same accumulation class as stdio).
        for existing in getattr(self, "_mcp_clients", []) or []:
            if (
                getattr(existing, "url", None) == url
                and existing.is_connected()
            ):
                logger.info("HTTP MCP %s already connected; reusing client", url)
                return []

        client = HTTPMCPClient(url=url, headers=headers)
        client.start()

        if not hasattr(self, "_mcp_clients"):
            self._mcp_clients: list = []
        self._mcp_clients.append(client)

        try:
            tools = client.list_tools()
            self._validate_external_mcp_tools(tools)
        except Exception:
            self._discard_mcp_client(client)
            raise
        return self._mount_mcp_tools(client, tools, mcp_service)

    def stop(self, timeout: float = 5.0) -> StopResult:
        return super().stop(timeout=timeout)

    def _close_agent_owned_services_after_quiescence(self) -> None:
        """Close LICC/MCP transports only after execution can no longer use them."""

        # Stop LICC poller before closing MCP clients so any in-flight events
        # finish dispatching before subprocess teardown.
        poller = getattr(self, "_mcp_inbox_poller", None)
        if poller is not None:
            try:
                poller.stop()
            except Exception:
                pass

        for lease in list(getattr(self, "_session_mcp_leases", [])):
            try:
                lease.close()
            except Exception:
                pass

        for client in getattr(self, "_mcp_clients", []):
            try:
                client.close()
            except Exception:
                pass
        # Advertised metadata describes those now-closed clients; drop it so a
        # stopped agent cannot report a live-looking MCP tool surface.
        self._mcp_tool_metadata = {}

    def has_capability(self, name: str) -> bool:
        """Check if a capability is registered."""
        from lingtai.tools.registry import canonical_capability_name
        return canonical_capability_name(name) in self._capability_managers

    def get_capability(self, name: str) -> Any:
        """Return a capability manager, accepting the retained ``bash`` alias."""
        from lingtai.tools.registry import canonical_capability_name
        return self._capability_managers.get(canonical_capability_name(name))

    # ------------------------------------------------------------------
    # Deep refresh — full reconstruct from init.json
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_hidden_runtime_manifest_settings(data: dict) -> bool:
        """Remove legacy user-facing runtime knobs that are now kernel-owned.

        `manifest.stamina` used to configure an agent-visible countdown. The
        runtime now keeps only a hidden fixed idle timeout, so persisted init.json
        should not keep presenting stamina as a user-editable setting.
        """
        manifest = data.get("manifest")
        if isinstance(manifest, dict) and "stamina" in manifest:
            manifest.pop("stamina", None)
            return True
        return False

    def _read_init(self) -> dict | None:
        """Read and validate init.json from working directory.

        If ``manifest.preset.active`` is set, materialize the named preset's
        ``llm`` and ``capabilities`` into the manifest before validation. The
        running agent thus always sees a fully resolved manifest.

        On success, the resolved (secret-redacted) manifest is also published
        to ``system/manifest.resolved.json`` via
        ``lingtai.kernel.workdir.write_resolved_manifest`` (issue #259).
        """
        from .init_reader import InitReadStatus, read_init, reader_callbacks

        materialize, prepare = reader_callbacks(
            self._working_dir,
            load_preset=load_preset,
        )
        outcome = read_init(
            self._working_dir,
            materialize=materialize,
            prepare=prepare,
            failure_behavior="KEEP_PREVIOUS_EFFECTIVE",
        )
        self._last_init_read_outcome = outcome
        from lingtai.kernel.workdir import write_resolved_manifest
        if outcome.status is not InitReadStatus.READ_FAILED:
            data = outcome.data
            assert data is not None
            effective_path = write_resolved_manifest(self._working_dir, data)
            if effective_path is not None:
                outcome.effective_config_source = str(effective_path)
            else:
                self._log("resolved_manifest_write_failed")
            self._log("init_read_result", **outcome.log_fields())
            return data

        outcome.fallback_effective = (
            "previous runtime effective config preserved; "
            f"source={outcome.effective_config_source}; freshness=PREVIOUS"
        )
        self._log("init_read_result", **outcome.log_fields())
        return None

    def _activate_preset(self, name: str) -> None:
        """Substitute a preset's llm + capabilities into init.json on disk.

        `name` is the preset's path (absolute, ~-prefixed, or relative to
        working_dir). Substitutes the file's `manifest.llm` and
        `manifest.capabilities` into the agent's init.json, sets
        `manifest.preset.active = name` (storing the path string verbatim —
        no canonicalization), and writes atomically.

        Other manifest fields are preserved, except retired/hidden runtime
        knobs such as ``manifest.stamina``.

        Raises:
            KeyError: the preset file does not exist
            ValueError: the preset file is malformed or the name is invalid
            OSError: the on-disk write failed (init.json untouched)
        """
        import json
        import os

        init_path = self._working_dir / "init.json"
        data = json.loads(init_path.read_text(encoding="utf-8"))
        manifest = data.setdefault("manifest", {})

        # Use the wrapper preset-loader; this explicit activation may write the
        # selected preset, while production preset reads remain migration-free.
        preset = load_preset(name, working_dir=self._working_dir)
        preset_manifest = preset.get("manifest", {})

        preset_llm = dict(preset_manifest.get("llm") or manifest.get("llm") or {})
        # Context limits are System-owned runtime policy, never init.json
        # configuration.  Keep a preset's provider/model selection but do not
        # hand its context value into the activated init document.
        preset_llm.pop("context_limit", None)
        manifest["llm"] = preset_llm
        manifest["capabilities"] = preset_manifest.get(
            "capabilities", manifest.get("capabilities", {}))

        # Set active in the umbrella. Preserve default if already set; otherwise
        # initialize default to the same value as active (first activation).
        # Also ensure `name` appears in `allowed` — _activate_preset is the
        # final gate and the manifest must remain self-consistent. The caller
        # (system._refresh) also validates against `allowed` before invoking
        # us; this is belt-and-braces for direct callers and AED auto-fallback.
        preset_block = manifest.setdefault("preset", {})
        preset_block["active"] = name
        if not preset_block.get("default"):
            preset_block["default"] = name
        allowed = preset_block.get("allowed")
        if not isinstance(allowed, list):
            preset_block["allowed"] = [name]
            self._log("preset_allowed_widened", name=name,
                      reason="allowed_field_initialized")
        elif name not in allowed:
            preset_block["allowed"] = [*allowed, name]
            self._log("preset_allowed_widened", name=name,
                      reason="direct_activate_bypassed_gate")

        self._strip_hidden_runtime_manifest_settings(data)

        # Atomic write
        tmp = init_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(str(tmp), str(init_path))

    def _activate_default_preset(self) -> None:
        """Read manifest.preset.default and activate it. Used by AED auto-fallback."""
        import json
        data = json.loads((self._working_dir / "init.json").read_text(encoding="utf-8"))
        preset = data.get("manifest", {}).get("preset") or {}
        default_name = preset.get("default")
        if not default_name:
            raise RuntimeError("no default preset configured")
        self._activate_preset(default_name)

    def _setup_from_init(self) -> None:
        """Full construct/reconstruct from init.json."""
        self._log("refresh_start")

        data = self._read_init()
        if data is None:
            self._log("refresh_skipped", reason="no valid init.json")
            return

        # Psyche's strict owner source is a refresh precondition, not one of
        # the live objects being rebuilt. Resolve its immutable candidate now,
        # before timers, clients, tools, plugins, prompt state, or sealing are
        # touched, then thread that exact read through final reconstruction.
        from lingtai.tools.psyche.prompt import compose_prompt_plan

        psyche_prompt_plan = compose_prompt_plan(self._working_dir)

        from lingtai.kernel.config_resolve import (
            load_env_file,
            resolve_env_checked,
            resolve_file,
            _resolve_capabilities,
        )
        psyche_pad_file = data.get("pad_file")
        env_file = data.get("env_file")
        import os

        overwrite_env_file = os.environ.get("LINGTAI_REFRESH_ENV_OVERWRITE") == "1"
        if env_file:
            load_env_file(env_file, overwrite=overwrite_env_file)
        if overwrite_env_file:
            os.environ.pop("LINGTAI_REFRESH_ENV_OVERWRITE", None)

        # Resolve only live init-owned Pad and LingTai seed pointers. Psyche's
        # six prompt pairs are compatibility-known but inert in init.json; the
        # prevalidated owner candidate is passed to final reconstruction below.
        # Note: "soul" / "soul_file" were retired in v0.7.6 and remain
        # compatibility-known; they are intentionally not resolved here;
        # the shared reader reports them without rewriting init.json.
        for key in ("pad", "lingtai"):
            file_key = f"{key}_file"
            if file_key in data:
                data[key] = resolve_file(data.get(key), data.pop(file_key))

        m = data["manifest"]

        # Save conversation history
        saved_interface = None
        if self._session.chat is not None:
            saved_interface = self._session.chat.interface

        # Tear down
        # Cancel soul timer to prevent racing on config/service during rebuild
        self._cancel_soul_timer()

        for client in getattr(self, "_mcp_clients", []):
            try:
                client.close()
            except Exception:
                pass
        self._mcp_clients = []
        # Reverse routes belong to the closed clients above. Rebuild this mapping
        # only from successfully connected clients during the following MCP load;
        # a retained Task Card controller must never select a prior runtime route.
        self._mcp_clients_by_tool = {}
        self._mcp_tool_collisions = set()
        # Advertised metadata belongs to those same closed clients. Drop it with
        # them so a rebuilt surface never exposes a prior runtime's metadata.
        self._mcp_tool_metadata = {}

        self._sealed = False
        self._tool_handlers.clear()
        self._tool_schemas.clear()
        # The official claim map describes the live tool surface being cleared
        # one line above, so it is cleared with it: a capability dropped on this
        # refresh (``manifest.disable``) must not leave a claim behind for a
        # tool that is no longer mounted. Capabilities that survive the refresh
        # re-register below and re-claim their names in the same boot, and
        # re-registering the same declaration is idempotent either way.
        self._official_tool_plugins.clear()
        # The declaration anchors survive refresh, but bound results describe
        # the just-cleared live surface and must be rebuilt by registration.
        self._official_tool_bindings.clear()
        self._capabilities.clear()
        self._capability_managers.clear()

        self._intrinsics.clear()
        self._intrinsic_modules.clear()
        self._wire_intrinsics()
        # Refresh rebuilds the official surface from scratch. Soul's injected
        # module remains for lifecycle hooks, while its public root is again
        # mounted only through the static declaration/registrar route.
        if "soul" in self._intrinsics:
            from lingtai.tools import soul as _soul
            self.override_intrinsic("soul")
            _soul.setup(self)

        # Reset capability-owned flags (``email.boot``, run once by
        # ``_boot_official_intrinsics()`` below, resets to "email box"/"email")
        self._mailbox_name = "email box"
        self._mailbox_tool = "email"
        if hasattr(self, "_post_molt_hooks"):
            self._post_molt_hooks.clear()

        # Reset prompt manager
        self._prompt_manager._sections.clear()

        # Reconstruct LLM service if changed
        llm = m["llm"]
        # Warn (not raise) on refresh: a live agent should not be killed by a
        # hot-edited env_file that transiently loses the api_key variable. The
        # diagnostic lands in the agent log so the cause is findable instead of
        # surfacing as an opaque provider auth error on the next turn.
        api_key = resolve_env_checked(
            llm.get("api_key"),
            llm.get("api_key_env"),
            context="manifest.llm.api_key_env",
            warn=lambda m: self._log("env_resolve_warning", message=m),
        )
        new_provider = llm["provider"]
        new_model = llm["model"]
        new_base_url = llm.get("base_url")
        # One System-resolved policy (env > settings/system.json v2 > fixed
        # default) feeds the service, AgentConfig, and the session
        # streaming flag below, so boot and refresh never disagree. The
        # effective manifest ``m`` is read, never mutated, so the published
        # ``system/manifest.resolved.json`` stays a truthful init artifact.
        runtime_policy = self.resolve_runtime_policy()
        new_context_window = runtime_policy.context_limit
        if (
            not isinstance(new_context_window, int)
            or isinstance(new_context_window, bool)
            or new_context_window <= 0
        ):
            new_context_window = CONSERVATIVE_CONTEXT_WINDOW

        # The fixed 60 RPM default cooperatively shares the network-wide cap.
        # Set the System v2 field or environment variable to change it.
        new_max_rpm = runtime_policy.max_rpm
        # Pass working_dir so a Codex agent's per-agent session/thread identity
        # (the agent path) is resolved into the provider defaults. The agent
        # path anchor is stable across refresh, but #406 makes start/refresh one
        # of the two cache-affinity rotate triggers: the Codex adapter stamps a
        # fresh epoch at construction, so a live refresh must REBUILD the adapter
        # to rotate the current id (see codex_force_rebuild below).
        new_provider_defaults = build_provider_defaults_from_manifest_llm(
            llm, max_rpm=new_max_rpm, working_dir=self._working_dir
        )

        new_provider_defaults_bucket = (new_provider_defaults or {}).get(
            new_provider.lower(), {}
        )
        cur_provider_defaults_bucket = getattr(
            self.service, "_provider_defaults", {}
        ).get(new_provider.lower(), {})
        # Codex start/refresh is a cache-affinity rotate trigger (Jason's final
        # #406 semantics): the adapter epoch-stamps the *current* affinity id at
        # construction, so the 8-hit stalled-cache shuffle only becomes active
        # once a live refresh REBUILDS the adapter at a fresh epoch. The agent
        # path anchor is stable across refresh, so the provider-defaults bucket
        # is byte-identical and the boot-epoch adapter (with its stale current
        # id) would otherwise survive untouched — exactly the bug where the
        # shuffle never went live. A *live* refresh is one that replays existing
        # history (``saved_interface is not None``); boot has no prior session
        # and already builds a fresh adapter, so it is excluded to avoid a
        # redundant double-build.
        codex_force_rebuild = (
            new_provider.lower() == "codex" and saved_interface is not None
        )
        # Compare the resolved provider-defaults bucket as a whole so explicit
        # init.json changes (codex_session_anchor, default_headers,
        # compact_threshold, max_rpm, api_compat, etc.) rebuild coherently.
        if (
            codex_force_rebuild
            or new_provider != self.service.provider
            or new_model != self.service.model
            or new_base_url != getattr(self.service, "_base_url", None)
            or new_context_window != getattr(self.service, "_context_window", None)
            or new_provider_defaults_bucket != cur_provider_defaults_bucket
        ):
            rebuilt_service = LLMService(
                provider=new_provider, model=new_model,
                api_key=api_key, base_url=new_base_url,
                context_window=new_context_window,
                provider_defaults=new_provider_defaults,
            )
            # A constrained composition wraps every provider route at Core's
            # service boundary.  Refresh may rebuild the concrete adapter, but
            # must never replace that wrapper with a raw LLMService.
            if self._provider_call_admission_port is None:
                self.service = rebuilt_service
            else:
                from .kernel.provider_admission import ProviderAdmittedLLMService

                self.service = ProviderAdmittedLLMService(
                    rebuilt_service, self._provider_call_admission_port
                )
            self._session._llm_service = self.service

        # Reload admin from init.json (avatars have admin: {}, not inherited from parent)
        self._admin = m.get("admin", {})

        # Reload config by overlaying explicit init.json values onto
        # AgentConfig defaults. Stale max_turns and molt_* manifest values stay
        # deliberately ignored inside build_agent_config.
        new_config = build_agent_config(
            m, max_rpm=new_max_rpm, runtime_policy=runtime_policy
        )
        # Snapshot enable transition on a live agent: ``_start`` initializes
        # the snapshot port only when the policy is on at start, so turning it
        # on by refresh must initialize the (idempotent) port BEFORE the new
        # config becomes visible to the heartbeat tick. If initialization
        # fails, snapshots stay off for this process instead of ticking an
        # uninitialized port; the diagnostic names the cause.
        thread = getattr(self, "_thread", None)
        started = thread is not None and thread.is_alive()
        if (
            started
            and self._config.snapshot_interval is None
            and new_config.snapshot_interval is not None
        ):
            try:
                self._snapshot_port.initialize()
            except Exception as exc:
                self._log(
                    "snapshot_initialize_failed",
                    error=str(exc),
                    snapshot_interval=new_config.snapshot_interval,
                )
                new_config.snapshot_interval = None
        self._config = new_config
        self._soul_delay = max(1.0, self._config.soul_delay)
        self._session._config = self._config
        # Streaming is a per-request session flag, not a constructor-only
        # property: install the resolved value on every boot/refresh setup.
        self._session.streaming = runtime_policy.streaming

        # Reload large-result notification threshold from init.json.
        # Default 3000; 0 disables notifications entirely.  An explicit
        # manifest value overrides the default (config override preserved).
        raw_threshold = m.get("summarize_notification_threshold")
        if isinstance(raw_threshold, int) and not isinstance(raw_threshold, bool) and raw_threshold >= 0:
            self._summarize_notification_threshold = raw_threshold
        else:
            self._summarize_notification_threshold = 3000

        # Email is re-booted exactly once per refresh by
        # ``_boot_official_intrinsics()`` below, together with the other
        # ``official_plugin`` intrinsics. The historical early ``email.boot``
        # here (issue #154's scheduler-thread reset) is gone: the manager no
        # longer owns a scheduler, and a second boot would repeat the
        # manager/grant/bind/mount work for the same declaration.

        # Decompress addons BEFORE capability setup so the `mcp` capability
        # sees the populated registry on its first reconcile.
        addons = data.get("addons") or []
        if addons:
            try:
                from .services.mcp_registry import decompress_addons
                report = decompress_addons(self._working_dir, addons)
                self._log("mcp_decompress", **report)
            except Exception as e:
                self._log("mcp_decompress_failed", reason=str(e))

        # Re-run capability setup. init.json declares overrides/opt-ins;
        # `apply_core_defaults` ensures the always-on tool floor boots even
        # when the manifest omits it. `manifest.disable` and `"name": null`
        # entries are the opt-out channels.
        raw_caps = m.get("capabilities", {}) or {}
        resolved = _resolve_capabilities(raw_caps)
        # Preserve null sentinels through env-resolution (it converts None to {}).
        null_outs = {n for n, v in raw_caps.items() if v is None}

        from lingtai.tools.registry import (
            apply_core_defaults,
            normalize_capabilities,
        )
        expanded: dict[str, Any] = {}
        for name, cap_kwargs in resolved.items():
            if name in null_outs:
                expanded[name] = None
            elif cap_kwargs is None:
                expanded[name] = None
            else:
                expanded[name] = cap_kwargs
        normalized = normalize_capabilities(
            {n: (v if v is not None else {}) for n, v in expanded.items()}
        )
        for n, v in expanded.items():
            if v is None:
                normalized[n] = None  # type: ignore[assignment]

        disable_list = [*(m.get("disable") or []), *self._forced_disable]
        capabilities = apply_core_defaults(normalized, disable=disable_list)

        # Register declared Agent Plugins BEFORE capability setup, next to addon
        # decompression above and for the same reason: `skills` must see each
        # plugin's validated skill directories and `mcp` its plugin-sourced
        # registry records on their first reconcile. This is the one registration
        # the CLI boot path performs — `__init__` defers to it. Also the
        # uninstall path —
        # a plugin dropped from manifest.plugins has its registry records pruned
        # here on the next refresh.
        self._register_declared_plugins(m.get("plugins"), capabilities)

        if capabilities:
            for name, cap_kwargs in capabilities.items():
                try:
                    self._setup_capability(name, **cap_kwargs)
                except (ValueError, ImportError, TypeError) as e:
                    self._log("capability_skipped", capability=name, reason=str(e))

        # Mandatory declared intrinsics are mounted through their own official
        # registrar wiring on refresh as well as on first construction.
        self._boot_official_intrinsics()

        # Install intrinsic manuals (wipe-and-rewrite .library/intrinsic/)
        # from the bundles shipped with each enabled capability.
        self._install_intrinsic_manuals()

        # Molt passively invokes the exact same full reconstruction contract as
        # refresh and active context.rebuild — one hook, no section-specific
        # Pad/LingTai aliases or ordering dependency.
        self._post_molt_hooks = [self._reconstruct_context]

        # Reload MCP
        self._load_mcp_from_workdir()

        # Persist LLM config
        self._persist_llm_config()

        # Re-write manifest and identity
        self._update_identity()

        # Publish the one final reconstructed prompt only after every capability,
        # manual, MCP route, and mechanical identity source is wired. The session
        # replay below must observe this same complete builder state, and the
        # system/system.md mirror must be byte-identical to it.
        self._reconstruct_context(
            data,
            psyche_pad_file=psyche_pad_file,
            psyche_prompt_plan=psyche_prompt_plan,
        )

        # Re-seal
        self._sealed = True

        # Rebuild session with preserved history
        if saved_interface is not None:
            self._session._rebuild_session(saved_interface)

        self._log(
            "refresh_complete",
            capabilities=[name for name, _ in self._capabilities],
            tools=list(self._tool_handlers.keys()),
        )

    def _reconstruct_context(
        self,
        data: dict | None = None,
        *,
        psyche_pad_file: str | None = None,
        psyche_prompt_plan: PromptPlan | None = None,
    ) -> None:
        """Recompose every canonical prompt source, then publish the final prompt.

        This is the one internal reconstruction contract used by active
        ``context.rebuild`` and the passive refresh/molt scenarios. ``data`` is
        the already-resolved init mapping during refresh; active rebuild and molt
        omit it so configured and durable sources are re-read from scratch. The
        final flush is deliberately last, ensuring provider replay observes the
        fully composed prompt rather than an intermediate Pad/LingTai section.
        Refresh supplies its pre-teardown Psyche candidate; other callers resolve
        one candidate before the transaction begins.
        """
        if psyche_prompt_plan is None:
            from lingtai.tools.psyche.prompt import compose_prompt_plan

            psyche_prompt_plan = compose_prompt_plan(self._working_dir)

        prior_sections = deepcopy(self._prompt_manager._sections)
        missing_base_prompt = object()
        prior_base_prompt = getattr(self, "_base_prompt", missing_base_prompt)
        prior_snapshot = self._psyche_settings_snapshot
        prior_prompt_plan = self._psyche_prompt_plan
        prior_token_decomp_dirty = self._token_decomp_dirty
        system_dir = self._working_dir / "system"
        generation_mirrors = (
            system_dir / "base_prompt.md",
            system_dir / "covenant.md",
            *(
                self._working_dir / section.mirror
                for section in psyche_prompt_plan.sections
                if section.mirror is not None
            ),
            system_dir / "system.md",
        )
        prior_mirrors: dict[Path, str | None] = {}
        for path in generation_mirrors:
            try:
                prior_mirrors[path] = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                prior_mirrors[path] = None

        try:
            psyche_settings_snapshot = self._reload_prompt_sections(
                data,
                psyche_pad_file=psyche_pad_file,
                psyche_prompt_plan=psyche_prompt_plan,
            )
            self._token_decomp_dirty = True
            self._flush_system_prompt()
        except Exception:
            # Prompt-manager state, wrapper base prompt, derived fallback
            # mirrors, the published system mirror, and SHOW form one applied
            # generation. A failed candidate must not remain available to a
            # later build or be resurrected through mirror fallback.
            self._prompt_manager._sections.clear()
            self._prompt_manager._sections.update(prior_sections)
            if prior_base_prompt is missing_base_prompt:
                self.__dict__.pop("_base_prompt", None)
            else:
                self._base_prompt = prior_base_prompt
            self._psyche_settings_snapshot = prior_snapshot
            self._psyche_prompt_plan = prior_prompt_plan
            self._token_decomp_dirty = prior_token_decomp_dirty

            from lingtai.kernel._fsutil import atomic_write_text

            for path, content in prior_mirrors.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_text(path, content, encoding="utf-8")
            raise
        self._psyche_settings_snapshot = psyche_settings_snapshot
        self._psyche_prompt_plan = psyche_prompt_plan

    def _reload_prompt_sections(
        self,
        data: dict | None = None,
        *,
        psyche_pad_file: str | None = None,
        psyche_prompt_plan: PromptPlan | None = None,
    ) -> PsycheSettingsSnapshot:
        """Authoritative composer for every configured/durable prompt section."""
        if psyche_prompt_plan is None:
            from lingtai.tools.psyche.prompt import compose_prompt_plan

            psyche_prompt_plan = compose_prompt_plan(self._working_dir)

        resolve_init_files = data is None
        if resolve_init_files:
            data = self._read_init()
            # Directly constructed/testing agents may legitimately have no
            # init.json, but an existing unreadable/invalid file is a failed
            # configured source and must fail loud. Treating both as `{}` would
            # silently delete config-only sections (for example comment) even
            # though the init reader promised KEEP_PREVIOUS_EFFECTIVE.
            if data is None:
                if (self._working_dir / "init.json").is_file():
                    raise RuntimeError("init.json exists but could not be read")
                data = {}

        if "pad_file" in data:
            resolved_pad_file = data["pad_file"]
            if resolved_pad_file is not None and not isinstance(
                resolved_pad_file, str
            ):
                raise RuntimeError("resolved pad_file must be a string or null")
            psyche_pad_file = resolved_pad_file

        if resolve_init_files:
            # Resolve active init-owned Pad and LingTai seed pointers. Psyche's
            # prompt-plan inputs are already resolved and are never copied into
            # this effective init mapping.
            from lingtai.kernel.config_resolve import resolve_file
            for key in ("pad", "lingtai"):
                file_key = f"{key}_file"
                if file_key in data:
                    data[key] = resolve_file(data.get(key), data.pop(file_key))

        # Psyche owns the three configurable prompt pairs. The complete
        # immutable plan was resolved before this composition transaction, then
        # its inputs are overlaid only into this local composition input. `data`
        # remains the effective init mapping and is never mutated with owner
        # fields, so legacy init values cannot regain prompt authority.
        psyche_prompt_inputs = psyche_prompt_plan.inputs
        data = dict(data)
        data.update({
            "base_prompt": psyche_prompt_inputs.base_prompt,
            "covenant": psyche_prompt_inputs.covenant,
            "comment": psyche_prompt_inputs.comment,
        })

        system_dir = self._working_dir / "system"
        system_dir.mkdir(exist_ok=True)

        # Psyche supplies content and metadata; the Agent remains responsible
        # for applying the sections to the kernel manager and for mirroring the
        # exact packaged/fallback source bytes.
        for section in psyche_prompt_plan.sections:
            resident = section.resident
            if section.mirror is not None and section.mirror_text is not None:
                mirror_path = self._working_dir / section.mirror
                try:
                    mirror_path.write_text(section.mirror_text, encoding="utf-8")
                except (FileNotFoundError, ModuleNotFoundError, OSError):
                    if section.fallback_error is not None:
                        raise section.fallback_error
                    resident = section.fallback or ""
            if resident:
                self._prompt_manager.write_section(
                    section.name,
                    resident,
                    protected=section.protected,
                )
            else:
                self._prompt_manager.delete_section(section.name)
            if section.raw:
                self._prompt_manager.set_raw(section.name)

        # --- Base prompt (third-party prompt injection point) ---
        # `base_prompt` is the Psyche-owned third-party (application / recipe /
        # preset) system-prompt injection point — one of the three configurable
        # prompt surfaces (with `covenant` and `comment`).
        # It is NOT a prompt-manager section: the kernel builder renders it right
        # after the raw kernel-owned `principle` section and before the rest of
        # Batch 1 (see lingtai.kernel.prompt.build_system_prompt_batches), so it
        # is threaded through `self._base_prompt` and passed to the builder by
        # `_build_system_prompt` / `_build_system_prompt_batches`.
        #
        # Resolution precedence:
        #   1. data["base_prompt"]      — Psyche owner value (already merged
        #                                 with base_prompt_file by its reader)
        #   2. system/base_prompt.md    — on-disk mirror (fallback)
        # The on-disk mirror lets the resolved injection survive a post-molt
        # reload that re-reads init.json from scratch and lets operators inspect
        # what is actually injected.
        base_prompt = data.get("base_prompt", "")
        base_prompt_file = system_dir / "base_prompt.md"
        if base_prompt:
            base_prompt_file.write_text(base_prompt, encoding="utf-8")
        elif base_prompt_file.is_file():
            base_prompt = base_prompt_file.read_text(encoding="utf-8")
        self._base_prompt = base_prompt or ""

        # --- Covenant (Psyche owner input — covenant.md mirror/fallback) ---
        covenant = data.get("covenant", "")
        covenant_file = system_dir / "covenant.md"
        if covenant:
            covenant_file.write_text(covenant, encoding="utf-8")
        elif covenant_file.is_file():
            covenant = covenant_file.read_text(encoding="utf-8")
        if covenant:
            self._prompt_manager.write_section("covenant", covenant, protected=True)
        else:
            self._prompt_manager.delete_section("covenant")

        # --- Character (configured or self-authored identity — system/lingtai.md alone) ---
        # `lingtai` is the configured 灵台 / character value: a value supplied inline or
        # resolved from `lingtai_file` before this point is authoritative when nonempty and replaces
        # system/lingtai.md during boot, refresh, and post-molt reconstruction.
        # An absent or empty resolved value selects self-evolve mode and leaves
        # the existing system/lingtai.md untouched. In either mode the
        # canonical composer loads that file into `character`. This is the
        # agent's OWN voice — distinct from `covenant` above, from the
        # third-party `base_prompt` injection point, and from the mechanical
        # `identity` section written by BaseAgent. (Renamed from `prompt`; no
        # legacy alias.)
        lingtai_seed = data.get("lingtai", "")
        if lingtai_seed:
            (system_dir / "lingtai.md").write_text(lingtai_seed, encoding="utf-8")
        # Delegate to the single canonical composer so boot/refresh/molt all
        # produce byte-identical `character` content and no longer depend on
        # post-molt hook ordering.
        from lingtai.tools.lingtai import _lingtai_load
        _lingtai_load(self, {}, publish=False)

        # --- Rules (from system/rules.md, not init.json) ---
        rules_md = system_dir / "rules.md"
        if rules_md.is_file():
            try:
                rules_content = rules_md.read_text(encoding="utf-8").strip()
                if rules_content:
                    self._prompt_manager.write_section("rules", rules_content, protected=True)
                else:
                    self._prompt_manager.delete_section("rules")
            except OSError:
                pass
        else:
            self._prompt_manager.delete_section("rules")

        # --- Pad (pad.md + pinned pad_append.json references) ---
        # Configured Pad content is an initial seed, not an authoritative
        # rewrite: preserve a nonempty durable Pad authored after startup.
        pad_seed = data.get("pad") or ""
        if not isinstance(pad_seed, str):
            raise RuntimeError("resolved pad must be a string or null")
        pad_path = system_dir / "pad.md"
        if pad_seed and (
            not pad_path.is_file()
            or pad_path.read_text(encoding="utf-8") == ""
        ):
            pad_path.write_text(pad_seed, encoding="utf-8")

        # Delegate to the single canonical composer rather than re-reading
        # pad.md alone — otherwise the post-molt hook ordering silently drops
        # the pinned append references. `_pad_load` composes both.
        from lingtai.tools.pad import _pad_load
        _pad_load(self, {}, publish=False)

        # --- Skills and Knowledge catalogs ---
        # The four durable substrate domains are Pad, LingTai, Knowledge, and
        # Skills, and this one reconstruction contract must re-read and
        # recompose all four. Pad and LingTai are composed above from their
        # durable files; these two are composed from their on-disk catalogs.
        #
        # Composition only. Knowledge's one-time legacy JSON migration is
        # deliberately NOT reachable from here — it writes inside `knowledge/`
        # and renames the legacy source, so it stays owned by the capability's
        # own setup/refresh lifecycle (`knowledge.setup` -> `_reconcile`). This
        # path calls `_compose_catalog`, the pure-read composer.
        #
        # Each is guarded by whether the capability is enabled, so a disabled
        # domain keeps its existing behavior: no section written and no scan.
        #
        # Composition failures deliberately propagate. `tools/context/CONTRACT.md`
        # "Full reconstruction ordering" requires that ALL canonical sources be
        # recomposed and that a reconstruction failure return
        # `context_reconstruction_failed` WITHOUT applying summaries or requesting
        # provider replay. Catching here and continuing would publish a prompt
        # missing a durable section while still reporting success — exactly the
        # silent half-composed state that contract forbids.
        enabled_capabilities = {name: kwargs for name, kwargs in self._capabilities}
        if "skills" in enabled_capabilities:
            from lingtai.tools import skills as _skills
            _skills._reconcile(
                self,
                list(enabled_capabilities["skills"].get("paths", []) or []),
                publish=False,
            )
        if "knowledge" in enabled_capabilities:
            from lingtai.tools import knowledge as _knowledge
            _knowledge._compose_catalog(self, publish=False)

        # --- Runtime guidance mirror ---
        # `_meta.agent_meta.guidance` is latest-only tool-result metadata, but the TUI
        # also needs a filesystem-visible copy. Runtime guidance is now authored
        # as a skill-style Markdown catalog (lingtai/prompts/meta_guidance/catalog/INDEX.md +
        # <id>.md sections); the kernel assembles it into the same dict shape and
        # we serialize a *derived* `system/guidance.json` here for back-compat
        # with TUI/Portal consumers (schema_version is an int; ids are stable).
        guidance_file = system_dir / "guidance.json"
        try:
            from lingtai.kernel.meta_block import validate_runtime_guidance
            from lingtai.kernel.prompt_catalog import load_guidance_catalog

            guidance_payload = load_guidance_catalog()
            validate_runtime_guidance(guidance_payload)
            guidance_file.write_text(json.dumps(guidance_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            if not guidance_file.is_file():
                guidance_file.write_text("{}\n", encoding="utf-8")
        # --- Brief (secretary-maintained life context — disk only) ---
        # `brief` is not an external init.json prompt override. Psyche owns
        # base_prompt / covenant / comment; legacy init.json `brief` /
        # `brief_file` values remain compatibility-known, are reported by the
        # shared reader, and are ignored here. The `brief` section is now sourced
        # solely from system/brief.md,
        # which the secretary agent writes directly.
        brief_file = system_dir / "brief.md"
        if brief_file.is_file():
            brief = brief_file.read_text(encoding="utf-8")
            if brief:
                self._prompt_manager.write_section("brief", brief, protected=True)
            else:
                self._prompt_manager.delete_section("brief")
        else:
            self._prompt_manager.delete_section("brief")

        # --- Comment ---
        comment = data.get("comment", "")
        if comment:
            self._prompt_manager.write_section("comment", comment)
        else:
            self._prompt_manager.delete_section("comment")

        # Return discovery state to the full reconstruction seam. It publishes
        # this immutable snapshot only after the final prompt flush succeeds.
        from lingtai.tools.psyche.settings import PsycheSettingsSnapshot

        return PsycheSettingsSnapshot(
            pad=pad_seed,
            pad_file=psyche_pad_file,
            base_prompt=psyche_prompt_inputs.base_prompt,
            base_prompt_file=psyche_prompt_inputs.base_prompt_file,
            covenant=psyche_prompt_inputs.covenant,
            covenant_file=psyche_prompt_inputs.covenant_file,
            comment=psyche_prompt_inputs.comment,
            comment_file=psyche_prompt_inputs.comment_file,
        )

    def _build_launch_cmd(self) -> list[str] | None:
        """Return the command to relaunch this agent via lingtai-agent run."""
        from .venv_resolve import resolve_venv, venv_python
        data = self._read_init()
        venv_dir = resolve_venv(data)
        python = venv_python(venv_dir)
        return [python, "-m", "lingtai", "run", str(self._working_dir)]
