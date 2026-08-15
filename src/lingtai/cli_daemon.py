"""``lingtai-agent daemon`` — programmatic entry point to the daemon machinery.

Shell / Python / CI callers need to dispatch and inspect daemon runs without
scripting the model-facing ``daemon`` tool by hand.  This module is a thin CLI
skin over the *existing* engine: it builds a minimal agent-shaped facade
(:class:`_CliDaemonAgent`), hands it to the production
:class:`lingtai.tools.daemon.DaemonManager`, and dispatches through the same
:class:`~lingtai.tools.daemon._tool_family.DaemonFamilyDispatcher` envelope the
model uses.  No emanation, preset, run-directory, supervisor, or notification
logic is reimplemented here.

Three actions are exposed, deliberately fewer than the tool's six:

``emanate``
    Dispatch a batch from a tasks JSON file.  Preview-by-default; ``--yes`` is
    required before anything is spawned.  Everything that decides whether a
    dispatch is legal runs *before* the preview, so an invalid batch never
    prints a preview it could not honor: the tasks file is validated against
    the tool's own ``emanate`` schema, and the preset allowlist and effective
    capability policy are both checked fail-closed.  The agent's configuration
    comes from the canonical :func:`lingtai.init_reader.read_init` — the same
    effective view boot resolves — not from a raw parse of ``init.json``.
``list`` / ``check``
    Read-only inspection, categorically.  Two engine paths write, and both are
    neutralized: ``DaemonManager.__init__``'s startup reconciliation (reaping
    stale ``daemon.json`` records, replaying pending terminal notifications) is
    avoided by never constructing a manager, and its lazy ``daemon.json``
    repair is overridden to reconstruct in memory instead.  Both are correct
    for an agent booting and wrong for an inspection command run by another
    process.  :class:`_ReadOnlyDaemonView` otherwise binds the manager's own
    unmodified ``_handle_list`` / ``_handle_check`` units, the same forwarding
    shape ``daemon/execution_host.py`` uses inside a supervisor process.

``ask`` and ``reclaim`` are intentionally absent: this surface performs no
side effect beyond spawning daemons exactly as the tool would.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lingtai.kernel.config import AgentConfig
from lingtai.kernel.daemon_supervisor.manifest import redact_durable_value
from lingtai.kernel.risky_action_gate import build_risky_action_check
from lingtai.kernel.tool_call_guard import ToolCallGuard

#: Refused outright rather than read into memory — a tasks file this large is a
#: mistake (a log, a dump, a wrong path), never a hand-written batch.
_MAX_TASKS_FILE_BYTES = 4 * 1024 * 1024

#: Task text shown per row in the ``emanate`` preview.
_PREVIEW_TASK_CHARS = 120


class CliDaemonError(Exception):
    """A user-facing refusal: printed to stderr, exits non-zero."""


# --------------------------------------------------------------------------
# Agent facade
# --------------------------------------------------------------------------


class _CliDaemonAgent:
    """Smallest object satisfying ``DaemonManager``'s parent-agent surface.

    ``DaemonManager`` reads a narrow, mostly ``getattr``-defensive slice of its
    parent agent.  Exactly that slice is provided here — never a real
    :class:`lingtai.agent.Agent`, which would take the working directory's
    lease, write a second ``.agent.heartbeat``, and trip the duplicate-process
    guard against a live agent in the same directory.  This is the same
    reasoning (and the same shape) as
    :class:`~lingtai.kernel.daemon_supervisor.agent_stub.DaemonSupervisorAgentStub`,
    which cannot be reused verbatim because the CLI additionally needs a real
    ``service`` (``_default_model`` and the implicit parent preset) and the
    parent's ``preset.allowed`` block.

    Surface, and who reads it:

    ``_working_dir``
        Everywhere — run directories, history scans, preset resolution.
    ``service``
        ``DaemonManager.__init__`` (``_default_model``) and
        ``_implicit_parent_preset_llm`` (provider/credential/base_url/context
        window for a task that names no preset).  Built lazily from
        ``init.json`` so read-only commands never need an API key.
    ``_config``
        ``_build_emanation_prompt`` / detached manifests read ``.language``.
    ``_tool_schemas`` / ``_tool_handlers`` / ``_mcp_tool_names``
        ``_build_tool_surface`` validates each task's ``tools`` against them.
        Populated on demand by :meth:`install_tool_surface`.
    ``_file_io``
        Dereferenced by the ``file`` family's handlers.
    ``_tool_call_guard``
        Keeps an opted-in risky-action gate rooted at this working directory.
    ``load_preset`` / ``_read_preset_from_init``
        The preset authorization gate and preset resolution.
    ``_log``
        Structured events; ``None`` journal for read-only commands.
    ``_enqueue_system_notification``
        Terminal daemon wake signals.  Refused here — see the method.
    """

    # One implementation, not a copy: the allowlist sanitizer the live Agent
    # uses to read ``manifest.preset`` out of init.json is the same one the
    # authorization gate must see.
    from lingtai.agent import Agent as _Agent

    _PRESET_PUBLIC_KEYS = _Agent._PRESET_PUBLIC_KEYS
    _read_preset_from_init = _Agent._read_preset_from_init
    del _Agent

    @classmethod
    def for_dispatch(cls, agent_dir: Path, *, journal=None) -> "_CliDaemonAgent":
        """Build a facade backed by the agent's **effective** configuration.

        Dispatch decides a daemon's model, credentials, capability surface, and
        file paths, so it must see what the live agent sees: JSONC parsed,
        active preset materialized, provider ``inherit`` sentinels expanded,
        schema validated, and every relative path resolved against
        ``agent_dir``.  That is :func:`lingtai.init_reader.read_init` — the one
        canonical reader boot and live refresh share.
        """
        return cls(agent_dir, _read_effective_init(agent_dir), journal=journal)

    @classmethod
    def for_inspection(cls, agent_dir: Path) -> "_CliDaemonAgent":
        """Build a facade for ``list``/``check``, which read no manifest at all.

        Inspection needs only ``_working_dir``: it never resolves a model, a
        credential, a capability, or a path.  Deliberately skipping the
        canonical reader keeps an agent whose active preset went missing still
        *inspectable* — refusing to list a broken agent's daemon history is
        exactly backwards.
        """
        return cls(agent_dir, {})

    def __init__(self, working_dir: Path, init_data: dict, *, journal=None) -> None:
        self._working_dir = Path(working_dir)
        self._init_data = init_data
        manifest = init_data.get("manifest") or {}
        self._config = AgentConfig(language=manifest.get("language", "en") or "en")
        self._session = None
        self._intrinsics: dict = {}
        self._intrinsic_modules: dict = {}
        self._tool_schemas: list = []
        self._tool_handlers: dict = {}
        self._file_io = None
        self._mcp_tool_names: set[str] = set()
        self._tool_call_guard = ToolCallGuard([
            build_risky_action_check(self._working_dir),
        ])
        self._journal = journal
        self._service = None

    # -- LLM service -------------------------------------------------------

    @property
    def service(self):
        """The parent's ``LLMService``, built on first read.

        Lazy on purpose: ``list``/``check`` never touch it, so an agent whose
        ``api_key_env`` is unset in this shell can still be inspected.
        """
        if self._service is None:
            from lingtai.cli import build_llm_service
            from lingtai.kernel.config_resolve import load_env_file

            env_file = self._init_data.get("env_file")
            if env_file:
                load_env_file(env_file)
            self._service = build_llm_service(self._init_data, self._working_dir)
        return self._service

    # -- preset resolution -------------------------------------------------

    def load_preset(self, name: str, working_dir: "str | Path | None" = None) -> dict:
        """Load a preset through the wrapper-level loader the Agent uses."""
        from lingtai.agent import load_preset as _load_preset

        wd = Path(working_dir) if working_dir is not None else self._working_dir
        return _load_preset(name, wd)

    # -- host services -----------------------------------------------------

    def effective_capabilities(self) -> dict[str, dict]:
        """This agent's effective capability set, resolved exactly as boot does.

        ``apply_core_defaults`` is the one canonical resolver: core floor,
        overlaid by ``manifest.capabilities`` (authored kwargs win key-by-key,
        an explicit ``null`` opts out), minus every name in
        ``manifest.disable``.  The manifest it reads has already been through
        active-preset materialization, so a preset that swaps the capability
        set is honored too.
        """
        from lingtai.tools.registry import apply_core_defaults

        manifest = self._init_data.get("manifest") or {}
        disable = manifest.get("disable")
        return apply_core_defaults(
            manifest.get("capabilities") if isinstance(manifest.get("capabilities"), dict) else None,
            list(disable) if isinstance(disable, list) else None,
        )

    def install_tool_surface(self, requested: set[str]) -> None:
        """Register the requested tools that this agent's policy actually grants.

        The engine's no-preset path resolves a task's ``tools`` against the
        *parent's registered surface* (``_build_tool_surface``), so what the
        facade registers is what the daemon may use.  Registering every
        requested built-in with default kwargs — as this did before — would
        hand a daemon a capability the agent disabled and would drop the
        agent's authored configuration for the ones it kept.

        So the surface is the intersection of what the batch asked for and
        :meth:`effective_capabilities`, set up with each capability's
        *effective kwargs* through ``_ToolCollector`` + ``setup_capability``
        (the composition ``execution_host.DetachedDaemonExecutionHost`` uses).
        A requested-but-not-granted tool is simply never registered, and the
        engine then refuses the whole batch with its own ``Unknown tools for
        emanation`` error — fail-closed, no silent downgrade.  Only the
        intersection is instantiated rather than the whole effective set: it
        yields the identical filter while avoiding setup work (and a second
        ``DaemonManager``) for capabilities this batch never names.

        A capability whose ``setup()`` raises is skipped, matching
        ``Agent.__init__``'s own tolerance; the result is one fewer available
        tool, which again fails closed at the engine.
        """
        from lingtai.tools.daemon import _ToolCollector
        from lingtai.tools.registry import (
            BUILTIN_TOOLS,
            canonical_capability_name,
            setup_capability,
        )

        granted = self.effective_capabilities()
        install = {
            canonical: granted[canonical]
            for canonical in {canonical_capability_name(name) for name in requested}
            if canonical in granted and canonical in BUILTIN_TOOLS
        }

        if "file" in install:
            from lingtai.services.file_io_sidecar import default_file_io_service

            self._file_io = default_file_io_service(root=self._working_dir)

        collector = _ToolCollector(self)
        for name in sorted(install):
            kwargs = install[name] if isinstance(install[name], dict) else {}
            try:
                setup_capability(collector, name, **kwargs)
            except (ValueError, ImportError, TypeError) as exc:
                self._log("cli_daemon_capability_skipped", capability=name, reason=str(exc))
        self._tool_schemas = list(collector.schemas.values())
        self._tool_handlers = dict(collector.handlers)

    def add_tool(self, name, *, schema=None, handler=None, description: str = "",
                 system_prompt: str = "", glossary_package: str | None = None) -> None:
        """Accept tool registration so ``daemon.setup()`` can compose here."""
        if schema is None:
            return
        from lingtai.kernel.llm.base import FunctionSchema

        self._tool_handlers[name] = handler
        self._tool_schemas = [s for s in self._tool_schemas if s.name != name]
        self._tool_schemas.append(FunctionSchema(
            name=name, description=description, parameters=schema,
            system_prompt=system_prompt, glossary_package=glossary_package,
        ))

    # -- observability -----------------------------------------------------

    def _log(self, event_type: str, **fields) -> None:
        if self._journal is None:
            return
        try:
            self._journal.append({"event": event_type, **fields})
        except Exception:
            pass

    def _enqueue_system_notification(self, **kwargs) -> None:
        """Refuse to publish daemon wake signals from a one-shot CLI process.

        Terminal daemon notifications exist to wake the *agent* that owns this
        working directory, and their durable receipt is written only after a
        successful publish.  Silently accepting one here would mark it
        delivered while no agent ever saw it, so this raises: the engine's
        ``_publish_daemon_notification`` catches it, returns ``False``, and
        leaves the pending receipt intact for the real agent to retry.
        """
        raise RuntimeError(
            "daemon notifications are published by the owning agent process, "
            "not by the lingtai-agent daemon CLI"
        )


class _ReadOnlyDaemonView:
    """``list``/``check`` bound to the manager's own units, with no writes.

    ``_handle_list`` and ``_handle_check`` read exactly two instance
    attributes: ``_agent`` (for the working directory) and ``_emanations``
    (empty — a CLI process tracks no in-flight run and both handlers already
    fall back to the durable ``daemons/`` history).  Everything else they call
    is forwarded to the unmodified ``DaemonManager``, so this view stays a
    binding, never a second implementation.

    Two write paths are neutralized here rather than inherited:

    * ``DaemonManager.__init__``'s startup reconciliation (reaping stale
      records, replaying pending terminal notifications) — sidestepped by not
      constructing a manager at all.
    * ``_load_or_rebuild_daemon_state``'s lazy repair, overridden below.

    Between them, inspection is *categorically* read-only: no run directory is
    created, repaired, reaped, or renotified, whatever shape the durable state
    is in.
    """

    def __init__(self, agent: _CliDaemonAgent) -> None:
        from lingtai.tools.daemon import DaemonManager

        self._agent = agent
        self._emanations: dict = {}
        self._manager_pool_size = 100
        self._manager_type = DaemonManager
        #: Run directories whose ``daemon.json`` the engine would have rewritten
        #: (missing, unparseable, or written by an older ``data_version``).
        #: Reported, never repaired.
        self.needs_rebuild: list[str] = []

    def _load_or_rebuild_daemon_state(self, run_path: Path) -> dict | None:
        """Load durable state; reconstruct in memory instead of repairing on disk.

        The engine's version of this (``daemon/__init__.py``) rewrites
        ``daemon.json`` when it is missing, unparseable, or stamped with an
        older ``data_version``. That self-heal is right for the owning agent
        and wrong for an inspection command run by a different process — a
        `list` must not be able to rewrite a live agent's durable records, and
        a CI job reading status must not mutate what it observes.

        The reconstruction itself is the engine's own
        ``_build_reconstructed_daemon_state`` (a pure function), so the rows
        this returns are identical to the repaired ones; only the
        ``_atomic_write_daemon_json`` call is dropped.  Affected runs are
        recorded in :attr:`needs_rebuild` so the CLI can say so out loud
        instead of silently presenting reconstructed data as durable truth.
        """
        daemon_json_path = run_path / "daemon.json"
        existing: dict | None = None
        reason: str | None = None
        try:
            loaded = json.loads(daemon_json_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
            else:
                reason = "daemon_json_not_object"
        except FileNotFoundError:
            reason = "daemon_json_missing"
        except (json.JSONDecodeError, UnicodeDecodeError):
            reason = "daemon_json_invalid"
        except OSError:
            return None
        if reason is None and existing is not None:
            if self._manager_type._has_current_daemon_data_version(existing):
                return existing
            reason = "daemon_json_data_version_mismatch"
        self.needs_rebuild.append(run_path.name)
        return self._manager_type._build_reconstructed_daemon_state(
            self, run_path, existing, reason=reason or "daemon_json_rebuild",
        )

    def __getattr__(self, name: str):
        manager = self.__dict__.get("_manager_type")
        if manager is not None:
            attr = getattr(manager, name, None)
            if callable(attr):
                raw = manager.__dict__.get(name)
                if isinstance(raw, staticmethod):
                    return attr
                return lambda *args, **kwargs: attr(self, *args, **kwargs)
            if attr is not None:
                return attr
        raise AttributeError(name)


# --------------------------------------------------------------------------
# Input loading and validation
# --------------------------------------------------------------------------


def _resolve_agent_dir(raw: Path | None) -> Path:
    """Resolve and validate the agent working directory."""
    agent_dir = (raw if raw is not None else Path.cwd()).resolve()
    if not agent_dir.is_dir():
        raise CliDaemonError(f"{agent_dir} is not a directory")
    if not (agent_dir / "init.json").is_file():
        raise CliDaemonError(
            f"{agent_dir} does not contain init.json — --agent-dir must point "
            "at a LingTai agent working directory"
        )
    return agent_dir


def _read_effective_init(agent_dir: Path) -> dict:
    """Resolve the agent's effective configuration through the canonical reader.

    :func:`lingtai.init_reader.read_init` is the single parse → materialize →
    prepare → validate → resolve path that boot (``cli.load_init``) and live
    refresh both use.  Reading ``init.json`` with a bare ``json.loads``
    instead — as this did before — silently skipped all five stages: JSONC
    comments failed to parse, an active preset never materialized (so a
    daemon launched on the *raw* provider/model rather than the preset's
    effective one), ``provider: "inherit"`` sentinels stayed unexpanded, the
    schema was never checked, and a relative ``env_file`` resolved against the
    caller's CWD instead of the agent directory.

    ``working_dir`` is ``agent_dir``, so ``resolve_paths`` makes ``env_file``,
    ``venv_path``, and every ``*_file`` absolute under the agent directory
    regardless of where the CLI was invoked from.

    Unlike ``cli.load_init`` this deliberately does **not** call
    ``write_resolved_manifest``: publishing ``system/manifest.resolved.json``
    is the booting agent's job, not a CLI's.  ``read_init`` itself only
    mutates its in-memory copy, so this stays read-only on disk.
    """
    from lingtai.agent import load_preset
    from lingtai.init_reader import InitReadStatus, read_init, reader_callbacks

    materialize, prepare = reader_callbacks(agent_dir, load_preset=load_preset)
    outcome = read_init(
        agent_dir, materialize=materialize, prepare=prepare, failure_behavior="STOP",
    )
    if outcome.status is InitReadStatus.READ_FAILED:
        payload = outcome.to_payload()
        raise CliDaemonError(
            f"{agent_dir / 'init.json'} is not usable "
            f"(stage {payload.get('stage')}): {payload.get('safe_excerpt') or payload.get('next_step')}"
        )
    assert outcome.data is not None
    return outcome.data


def _load_tasks_file(path: Path) -> dict:
    """Read and shape-check the tasks file into an ``emanate`` input object.

    Accepts either the full input object (``{"tasks": [...], "backend": ...}``)
    or a bare array of task objects, which is the same thing with only
    ``tasks`` supplied.
    """
    if not path.is_file():
        raise CliDaemonError(f"--tasks file does not exist: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise CliDaemonError(f"cannot stat --tasks file {path}: {exc}") from exc
    if size == 0:
        raise CliDaemonError(f"--tasks file is empty: {path}")
    if size > _MAX_TASKS_FILE_BYTES:
        raise CliDaemonError(
            f"--tasks file is {size} bytes, over the {_MAX_TASKS_FILE_BYTES} "
            f"byte limit: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CliDaemonError(f"cannot parse --tasks file {path}: {exc}") from exc

    if isinstance(payload, list):
        payload = {"tasks": payload}
    if not isinstance(payload, dict):
        raise CliDaemonError(
            f"--tasks file must contain an object or an array of tasks: {path}"
        )
    return payload


#: JSON Schema keywords the daemon ``emanate`` schemas actually use. Asserted
#: at validation time so a future schema keyword cannot be silently ignored by
#: the interpreter below — an unknown keyword fails loudly instead.
_SUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "type", "enum", "minimum", "maximum", "items", "properties", "required",
    "additionalProperties", "anyOf", "propertyNames", "pattern", "description",
    "title",
})

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _matches_json_type(value: Any, name: str) -> bool:
    """One JSON Schema ``type`` check, with JSON's bool/int distinction."""
    expected = _JSON_TYPES.get(name)
    if expected is None:
        return False
    if name in ("integer", "number"):
        return isinstance(value, expected) and not isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    return isinstance(value, expected)


def _check_schema(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    """Validate ``value`` against the subset of JSON Schema these schemas use.

    Deliberately an interpreter over the canonical schema *objects* rather
    than a restatement of their rules: the backend enum, ``max_turns``'
    1..5000 window, ``timeout``'s 5-second floor, and
    ``context_token_limit``'s ``minimum: 1`` are all read from
    ``_tool_family``, so they cannot drift from what the tool enforces at
    dispatch.  ``jsonschema`` is not a declared dependency of this
    distribution, so pulling one in for a CLI preview is not an option.
    """
    unsupported = set(schema) - _SUPPORTED_SCHEMA_KEYWORDS
    if unsupported:
        raise CliDaemonError(
            f"internal: daemon schema at {path or '$'} uses unsupported keyword(s) "
            f"{sorted(unsupported)}; the CLI validator needs updating"
        )

    if "anyOf" in schema:
        if not any(
            not _collect(value, branch, path) for branch in schema["anyOf"]
        ):
            errors.append(f"{path}: does not match any allowed shape")
        return

    declared = schema.get("type")
    if declared is not None:
        names = declared if isinstance(declared, list) else [declared]
        if not any(_matches_json_type(value, name) for name in names):
            errors.append(
                f"{path or '$'} must be {' or '.join(names)} (got {type(value).__name__})"
            )
            return

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(v) for v in schema["enum"])
        errors.append(f"{path or '$'}: {value!r} is not one of {allowed}")
        return

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path or '$'} must be >= {schema['minimum']} (got {value})")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path or '$'} must be <= {schema['maximum']} (got {value})")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _check_schema(item, schema["items"], f"{path}[{index}]", errors)

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key} is required" if path else f"{key} is required")
        properties = schema.get("properties") or {}
        additional = schema.get("additionalProperties")
        names_pattern = (schema.get("propertyNames") or {}).get("pattern")
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if names_pattern is not None:
                import re

                if not isinstance(key, str) or not re.match(names_pattern, key):
                    errors.append(f"{child}: name does not match {names_pattern}")
                    continue
            if key in properties:
                _check_schema(item, properties[key], child, errors)
            elif additional is False:
                errors.append(f"{child} is an unsupported field")
            elif isinstance(additional, dict):
                _check_schema(item, additional, child, errors)


def _collect(value: Any, schema: dict, path: str) -> list[str]:
    """Run :func:`_check_schema` into a fresh error list (used by ``anyOf``)."""
    errors: list[str] = []
    _check_schema(value, schema, path, errors)
    return errors


def _validate_emanate_input(payload: dict) -> list[dict]:
    """Validate the tasks file against the daemon tool's own ``emanate`` schema.

    This runs at **preview** time, before ``--yes`` is even considered, so a
    malformed batch is refused without a manager ever being constructed. It
    used to check only field names and ``task``/``tools`` shape, deferring
    backend enum, optional types, and every numeric bound to family dispatch —
    which only runs with ``--yes``. A file naming backend ``not-a-backend``
    with ``max_turns: 0`` and ``timeout: 1`` therefore printed a clean preview
    and exited 0, promising a dispatch the engine would immediately refuse.

    Optional fields are filled with ``null`` before validating because the
    schema declares them required-nullable (what strict tool wires demand) and
    ``DaemonFamilyDispatcher._strip_nulls`` treats null as absent — the same
    normalization the dispatch path applies.

    Two CLI-only tightenings sit on top of the schema, both guardrails rather
    than reinterpretations: ``tasks`` must be non-empty, and a task's unknown
    fields are rejected. The nested task schema is deliberately left open by
    ``_tool_family`` so the engine can return domain-specific errors, but a
    typo'd key in a CI tasks file should not be silently ignored; the property
    list is read from the schema, so it tracks the schema automatically.
    """
    from lingtai.tools.daemon import _BACKEND_SCHEMA_ENUM
    from lingtai.tools.daemon._tool_family import (
        _emanate_input_schema,
        _emanate_task_schema,
    )

    input_schema = _emanate_input_schema(list(_BACKEND_SCHEMA_ENUM))
    task_props = set(_emanate_task_schema()["properties"])

    candidate = {
        key: payload.get(key) for key in input_schema["properties"]
    }
    candidate.update({k: v for k, v in payload.items() if k not in candidate})

    errors: list[str] = []
    _check_schema(candidate, input_schema, "", errors)
    if errors:
        raise CliDaemonError(
            "--tasks file does not match the daemon emanate schema:\n  - "
            + "\n  - ".join(errors)
        )

    tasks = candidate["tasks"]
    if not tasks:
        raise CliDaemonError("--tasks file defines no tasks")

    for i, spec in enumerate(tasks):
        unknown = sorted(set(spec) - task_props)
        if unknown:
            hint = (
                " — system_prompt is obsolete; put the complete daemon system "
                "instruction in task"
                if "system_prompt" in unknown else ""
            )
            raise CliDaemonError(
                f"tasks[{i}] has unsupported field(s): {', '.join(unknown)}{hint}"
            )
        if not spec["task"].strip():
            raise CliDaemonError(f"tasks[{i}].task must be a non-empty string")
    return tasks


def _validate_backend_flag(backend: str) -> None:
    """Hold ``--backend`` to the same enum the tasks file is held to.

    The flag overrides the file's value, so without this check it would be the
    one way to smuggle an unknown backend past schema validation and into
    dispatch.
    """
    from lingtai.tools.daemon import _BACKEND_SCHEMA_ENUM

    if backend not in _BACKEND_SCHEMA_ENUM:
        raise CliDaemonError(
            f"--backend {backend!r} is not one of: {', '.join(_BACKEND_SCHEMA_ENUM)}"
        )


def _enforce_preset_allowlist(agent: _CliDaemonAgent, tasks: list[dict]) -> None:
    """Refuse the whole batch unless every named preset is allowed.

    Fail-closed and identical in mechanism to the engine's own gate
    (``_handle_emanate``): the same ``_preset_ref_in`` membership test against
    the same sanitized ``manifest.preset.allowed`` block, so a missing or
    malformed allowlist refuses rather than admits.  Running it here as well
    means a disallowed preset is reported by the preview, before ``--yes`` is
    ever considered; the engine's copy remains the authoritative gate and is
    still reached on the dispatch path.
    """
    from lingtai.kernel.presets import _preset_ref_in

    requested_any = [spec for spec in tasks if spec.get("preset")]
    if not requested_any:
        return
    try:
        raw_preset_block = agent._read_preset_from_init()
    except Exception:
        raw_preset_block = {}
    allowed = raw_preset_block.get("allowed") if isinstance(raw_preset_block, dict) else None

    for spec in requested_any:
        requested = spec["preset"]
        if not _preset_ref_in(requested, allowed, working_dir=agent._working_dir):
            raise CliDaemonError(
                f"preset {requested!r} is not in this agent's allowed list "
                f"(manifest.preset.allowed in init.json); the whole batch is refused"
            )


def _enforce_capability_policy(agent: _CliDaemonAgent, tasks: list[dict]) -> None:
    """Refuse the whole batch if it asks for a capability this agent does not grant.

    ``install_tool_surface`` is the mechanism — an ungranted capability is
    never registered, so ``_build_tool_surface`` refuses the batch with
    ``Unknown tools for emanation``. This gate exists only to say *why*, and to
    say it at preview time rather than only under ``--yes``: "unknown tool"
    reads as a typo when the real cause is ``manifest.disable``.

    Deliberately narrow, so it can never refuse something the engine would
    have allowed. It fires only for a name that is a *known capability*
    (``BUILTIN_TOOLS``) which this agent does not grant. Every other name is
    left entirely to the engine, because the engine's availability set is
    wider than the capability registry: ``email`` is auto-mounted as MCP
    (``_DAEMON_AUTO_MCP_TOOL_NAMES``), ``compact`` comes from the daemon
    intrinsic surface, and blacklisted names are silently dropped by
    ``_expand_requested_tools``.

    A task naming an explicit ``preset`` is skipped outright: the preset
    supplies its own capability sandbox plus the narrow parent host floor, so
    this agent's capability set is not the authority for it (see
    ``_build_tool_surface``'s preset branch).
    """
    from lingtai.tools.registry import BUILTIN_TOOLS, canonical_capability_name

    granted = set(agent.effective_capabilities())
    for i, spec in enumerate(tasks):
        if spec.get("preset"):
            continue
        for raw in spec.get("tools") or []:
            name = canonical_capability_name(raw)
            if name not in BUILTIN_TOOLS or name in granted:
                continue
            raise CliDaemonError(
                f"tasks[{i}] requests tool {raw!r}, which this agent does not "
                f"grant (not in the effective manifest.capabilities, or listed "
                f"in manifest.disable); the whole batch is refused"
            )


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _restore_nulls(original: Any, redacted: Any) -> Any:
    """Walk the redacted tree beside the original, re-adding null-only drops.

    Strictly a parallel walk: every value returned comes from ``redacted``, so
    the policy's key-context-sensitive decisions (``env`` is redacted because
    of its *parent* key, not its children) are never re-derived and never
    weakened.  The single edit is re-adding a key that the redactor omitted
    solely because its value was already ``None``.
    """
    if isinstance(original, dict) and isinstance(redacted, dict):
        restored: dict = {}
        for key, item in original.items():
            if key in redacted:
                restored[key] = _restore_nulls(item, redacted[key])
            elif item is None:
                restored[key] = None
        return restored
    if (
        isinstance(original, list) and isinstance(redacted, list)
        and len(original) == len(redacted)
    ):
        return [_restore_nulls(o, r) for o, r in zip(original, redacted)]
    return redacted


def _redact_preserving_nulls(value: Any) -> Any:
    """Redact, then restore keys the redactor dropped purely for being null.

    ``redact_durable_value`` omits a key whose sanitized value is ``None``,
    which is right for a durable manifest and wrong for a CLI whose output is
    parsed by a script: ``check``'s snapshot legitimately reports
    ``result_path: null`` / ``current_tool: null``, and a consumer should read
    those as null rather than as a missing key.
    """
    return _restore_nulls(value, redact_durable_value(value))


def _emit_json(data: object) -> None:
    """Print a result, with secret-bearing containers redacted.

    ``redact_durable_value`` is the same policy the durable daemon manifest
    applies, so ``backend_options.env``, MCP ``env``/``headers``, and
    credential-shaped keys never reach stdout.
    """
    print(json.dumps(
        _redact_preserving_nulls(data), ensure_ascii=False, indent=2, default=str
    ))


def _print_list_table(result: dict) -> None:
    """Render the engine's ``list`` payload as a fixed-width table."""
    emanations = result.get("emanations")
    if not isinstance(emanations, list) or not emanations:
        print("no daemon runs")
        return
    rows = [
        (
            str(entry.get("id") or entry.get("run_id") or "?"),
            str(entry.get("status") or "?"),
            str(entry.get("backend") or "lingtai"),
            str(entry.get("started_at") or ""),
            str(entry.get("task") or "").replace("\n", " ")[:_PREVIEW_TASK_CHARS],
        )
        for entry in emanations
        if isinstance(entry, dict)
    ]
    headers = ("ID", "STATUS", "BACKEND", "STARTED", "TASK")
    widths = [
        max(len(headers[i]), max((len(row[i]) for row in rows), default=0))
        for i in range(len(headers))
    ]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip())
    for row in rows:
        print("  ".join(col.ljust(widths[i]) for i, col in enumerate(row)).rstrip())
    running = result.get("running")
    if running is not None:
        print(f"\n{len(rows)} shown, {running} running")


def _build_preview(agent_dir: Path, backend: str, tasks: list[dict]) -> dict:
    """Describe what ``--yes`` would dispatch, without dispatching it."""
    presets = sorted({
        spec["preset"] for spec in tasks
        if isinstance(spec.get("preset"), str) and spec["preset"]
    })
    return {
        "status": "preview",
        "dispatched": False,
        "agent_dir": str(agent_dir),
        "backend": backend,
        "count": len(tasks),
        "presets": presets,
        "tasks": [
            {
                "index": i,
                "task": spec["task"].replace("\n", " ")[:_PREVIEW_TASK_CHARS],
                "tools": list(spec.get("tools") or []),
                # Omitted rather than null when absent: no preset is the
                # documented parent-derived path, not an unset field.
                **({"preset": spec["preset"]} if spec.get("preset") else {}),
            }
            for i, spec in enumerate(tasks)
        ],
        "note": "nothing was dispatched; re-run with --yes to spawn these daemons",
    }


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _dispatch_through_tool_family(agent: _CliDaemonAgent, action: str,
                                  action_input: dict) -> dict:
    """Send one action through the daemon tool's own envelope dispatcher.

    Going through ``DaemonFamilyDispatcher`` rather than calling
    ``DaemonManager.handle`` directly means the CLI is validated by, and
    behaves exactly as, the model-facing surface: same per-action input
    allowlist, same engine call, same result dict.
    """
    from lingtai.tools.daemon import _BACKEND_SCHEMA_ENUM, setup as daemon_setup
    from lingtai.tools.daemon._tool_family import DaemonFamilyDispatcher

    manager = daemon_setup(agent)
    dispatcher = DaemonFamilyDispatcher(manager, agent, list(_BACKEND_SCHEMA_ENUM))
    return dispatcher.handle({
        "action": action,
        "input": action_input,
        "reasoning": "lingtai-agent daemon CLI invocation",
    })


def _handle_emanate(args) -> int:
    agent_dir = _resolve_agent_dir(args.agent_dir)
    payload = _load_tasks_file(args.tasks.resolve())
    tasks = _validate_emanate_input(payload)

    backend = args.backend or payload.get("backend") or "lingtai"
    if args.backend is not None:
        _validate_backend_flag(args.backend)

    journal = None
    if args.yes:
        from lingtai.adapters.posix.event_journal import PosixJsonlEventJournalAdapter

        journal = PosixJsonlEventJournalAdapter(agent_dir)
    agent = _CliDaemonAgent.for_dispatch(agent_dir, journal=journal)

    # Both gates are fail-closed and run before anything is previewed or
    # spawned; the engine re-checks each one on the dispatch path.
    _enforce_preset_allowlist(agent, tasks)
    _enforce_capability_policy(agent, tasks)

    if not args.yes:
        _emit_json(_build_preview(agent_dir, backend, tasks))
        print(
            "not dispatched: re-run with --yes to spawn these daemons",
            file=sys.stderr,
        )
        return 0

    requested_tools: set[str] = set()
    for spec in tasks:
        requested_tools.update(spec.get("tools") or [])
    agent.install_tool_surface(requested_tools)

    result = _dispatch_through_tool_family(agent, "emanate", {
        "tasks": tasks,
        "backend": backend,
        "max_turns": payload.get("max_turns"),
        "timeout": payload.get("timeout"),
    })
    _emit_json(result)
    return 0 if result.get("status") == "dispatched" else 1


def _handle_list(args) -> int:
    agent_dir = _resolve_agent_dir(args.agent_dir)
    view = _ReadOnlyDaemonView(_CliDaemonAgent.for_inspection(agent_dir))
    result = view._handle_list(
        contains="",
        status_filter=args.status or "all",
        include_done=True,
        limit=None,
    )
    if result.get("status") == "error":
        raise CliDaemonError(str(result.get("message", "list failed")))
    _print_list_table(result)
    _warn_needs_rebuild(view)
    return 0


def _warn_needs_rebuild(view: _ReadOnlyDaemonView) -> None:
    """Say out loud which records were shown from unrepaired reconstruction."""
    if not view.needs_rebuild:
        return
    count = len(view.needs_rebuild)
    names = ", ".join(sorted(view.needs_rebuild))
    print(
        f"note: {count} run director{'y has' if count == 1 else 'ies have'} a missing, "
        f"unreadable, or outdated daemon.json ({names}). Shown from in-memory "
        "reconstruction and NOT repaired on disk — this CLI is read-only. Run "
        "daemon(action='list') as the owning agent to repair.",
        file=sys.stderr,
    )


def _handle_check(args) -> int:
    agent_dir = _resolve_agent_dir(args.agent_dir)
    view = _ReadOnlyDaemonView(_CliDaemonAgent.for_inspection(agent_dir))
    result = view._handle_check(args.id)
    _emit_json(result)
    _warn_needs_rebuild(view)
    return 0 if result.get("status") != "error" else 1


_HANDLERS = {
    "emanate": _handle_emanate,
    "list": _handle_list,
    "check": _handle_check,
}


def add_daemon_parser(sub: "argparse._SubParsersAction") -> None:
    """Register the ``daemon`` subcommand tree on the root parser."""
    daemon_parser = sub.add_parser(
        "daemon",
        help="Dispatch and inspect daemon runs programmatically",
    )
    daemon_sub = daemon_parser.add_subparsers(dest="daemon_command", required=True)

    emanate = daemon_sub.add_parser(
        "emanate",
        help="Preview (default) or dispatch a daemon batch from a tasks JSON file",
    )
    emanate.add_argument(
        "--tasks",
        type=Path,
        required=True,
        help="JSON file: the daemon tool's emanate input, or a bare array of task objects",
    )
    emanate.add_argument(
        "--agent-dir",
        type=Path,
        required=True,
        help="Agent working directory containing init.json",
    )
    emanate.add_argument(
        "--backend",
        default=None,
        help="Execution backend (default: the file's backend, else 'lingtai')",
    )
    emanate.add_argument(
        "--yes",
        action="store_true",
        help="Actually dispatch; without it the batch is only previewed",
    )

    listing = daemon_sub.add_parser("list", help="Show daemon run status (read-only)")
    listing.add_argument(
        "--status",
        default=None,
        help="Filter by status: running, done, failed, cancelled, timeout, or all",
    )
    listing.add_argument(
        "--agent-dir",
        type=Path,
        default=None,
        help="Agent working directory containing init.json (default: cwd)",
    )

    check = daemon_sub.add_parser("check", help="Inspect one daemon run (read-only)")
    check.add_argument("id", help="Daemon id, e.g. em-1 or a full run id")
    check.add_argument(
        "--agent-dir",
        type=Path,
        default=None,
        help="Agent working directory containing init.json (default: cwd)",
    )


def handle_daemon_command(args) -> None:
    """Run one ``daemon`` subcommand, exiting non-zero on refusal."""
    handler = _HANDLERS.get(getattr(args, "daemon_command", None))
    if handler is None:
        print("error: missing daemon subcommand", file=sys.stderr)
        sys.exit(1)
    try:
        code = handler(args)
    except CliDaemonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if code:
        sys.exit(code)


__all__ = ["add_daemon_parser", "handle_daemon_command", "CliDaemonError"]
