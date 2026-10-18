"""Existing Project configuration policy; filesystem and owner readers stay outside."""
from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
import re
from dataclasses import dataclass, field, replace

from . import ProjectCreationError, ProjectError


def _initial_agent_tools() -> dict:
    """Shared shelves are creation-owned init config, never model-preset data."""
    return {"skills": {"paths": ["../.library_shared", "~/.lingtai-tui/utilities"]}}


def configuration_error(code: str) -> ProjectCreationError:
    # Static vocabulary: neither input documents nor caught exceptions escape.
    return ProjectCreationError(ProjectError(code, "Project configuration could not complete"))


def safe_member_name(name: str) -> bool:
    return (isinstance(name, str) and bool(name) and name == name.strip()
            and not name.startswith(".") and name != "human"
            and not any(c in name for c in ("/", "\\", ":", "\x00")))


@dataclass(frozen=True, slots=True)
class ConfigurationOptions:
    language: str = "en"
    karma: bool = True
    nirvana: bool = False
    preserve_active: bool = False
    allowed_presets: tuple[str, ...] = ()
    env_file: str = ""
    venv_path: str = ""
    addons: tuple[str, ...] = ()
    mcp: dict[str, object] = field(default_factory=dict)
    pad: str | None = None
    # These are the existing Go writer inputs, not new runtime settings.
    context_limit: int = 500000
    max_rpm: int = 60
    max_aed_attempts: int = 5
    # Legacy Go writer paths: explicit choice, then existing path, then language default.
    covenant_file: str = ""
    default_covenant_file: str = ""


@dataclass(frozen=True, slots=True)
class ConfigureMemberRequest:
    directory_name: str
    agent_name: str
    preset_ref: str  # empty ONLY for Keep current
    keep_current: bool
    preset_manifest: dict[str, object]  # normalized public metadata, not credentials
    options: ConfigurationOptions
    psyche_settings_json: str | None = None  # opaque owner serialization


@dataclass(frozen=True, slots=True)
class MemberDocuments:
    directory_name: str
    init_present: bool
    init_document: dict[str, object] | None
    blueprint_present: bool
    blueprint: dict[str, object] | None
    init_error: bool = False
    blueprint_error: bool = False


@dataclass(frozen=True, slots=True)
class ConfiguredMemberSeed:
    directory_name: str
    init_document: dict[str, object]
    agent_manifest: dict[str, object]
    psyche_settings_json: str | None
    only_absent: bool = False
    pad_seed: str = ""
    legacy_mcp_venv_path: str = ""  # original request seed input, not the preserved host


@dataclass(frozen=True, slots=True)
class ConfigurationWriteReceipt:
    init_written: bool = False
    manifest_written: bool = False
    psyche_written: bool = False
    reader_status: str = "not_read"
    code: str = ""


@dataclass(frozen=True, slots=True)
class ConfigurationResult:
    operation: str
    outcome: str
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    code: str = ""
    source: ConfigurationWriteReceipt = ConfigurationWriteReceipt()

    def to_payload(self) -> dict[str, object]:
        return {"status": "error" if self.code else self.operation,
                "outcome": self.outcome, "updated": self.updated,
                "skipped": self.skipped, "failed": self.failed, "code": self.code,
                "error": "Project configuration could not complete" if self.code else "",
                "source": {"init_written": self.source.init_written,
                           "manifest_written": self.source.manifest_written,
                           "psyche_written": self.source.psyche_written,
                           "reader_status": self.source.reader_status}}


class ProjectConfigurationPort(ABC):
    """One selected Project's documents and ordered, partial configuration writes."""

    @abstractmethod
    def human_manifest_exists(self) -> bool:
        """Observe legacy stat-success early-return evidence, without writing."""

    @abstractmethod
    def save_skeleton(self, human_manifest: dict[str, object],
                      mailboxes: tuple[str, ...], helpers: tuple[str, ...]) -> ConfigurationResult:
        """Write supplied human layout in order; partial writes remain on failure."""

    @abstractmethod
    def observe_member(self, directory_name: str) -> MemberDocuments:
        """Read raw documents with exact integers; do not materialize or repair."""

    @abstractmethod
    def observe_candidates(self) -> tuple[MemberDocuments, ...]:
        """Immediate real directories, sorted; no runtime or fleet observation."""

    @abstractmethod
    def save_configured_member(self, seed: ConfiguredMemberSeed) -> ConfigurationWriteReceipt:
        """Preflight, then init, mailboxes, manifest, optional Psyche; never roll back."""

    @abstractmethod
    def save_policy_init(self, directory_name: str, document: dict[str, object]) -> ConfigurationWriteReceipt:
        """Persist a policy-only edit without requiring unrelated runtime inputs."""

    @abstractmethod
    def save_member_init(self, directory_name: str, document: dict[str, object],
                         *, only_absent: bool = False, pad_seed: str = "") -> ConfigurationWriteReceipt:
        """Write one init and read it using current owners. Absent-only refuses races."""


def _object(value: object) -> dict[str, object]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _reference(value: object) -> str:
    return value if isinstance(value, str) and value else ""


def _strip_explicit_legacy(document: dict[str, object]) -> None:
    # Exactly the retired top fields stripped by the inherited explicit writer.
    document.pop("principle_file", None)
    document.pop("procedures_file", None)


def configuration_seed(request: ConfigureMemberRequest, previous: MemberDocuments) -> ConfiguredMemberSeed:
    if not safe_member_name(request.directory_name) or not request.agent_name.strip():
        raise configuration_error("invalid_project_request")
    if previous.init_error or previous.blueprint_error:
        # Do not erase malformed user data by treating it as fresh.
        raise configuration_error("existing_document_unreadable")
    if not isinstance(request.preset_manifest.get("llm"), dict):
        raise configuration_error("invalid_preset")
    document = _object(previous.init_document)
    fresh = not document
    legacy = "manifest" in document
    if legacy:
        # Legacy remains its authored shape on explicit edits; never infer a migration.
        manifest = _object(document.get("manifest"))
    else:
        from ..config_resolve import normalize_init
        manifest = normalize_init(document)["manifest"] if document else {}
    old_policy = _object(manifest.get("preset"))
    default = request.preset_ref
    active = default
    opts = request.options
    existing_allowed: list[str] = []
    if request.keep_current:
        if request.preset_ref or not opts.preserve_active:
            raise configuration_error("invalid_project_request")
        default = _reference(old_policy.get("default")) or _reference(old_policy.get("active"))
        if not default:
            raise configuration_error("keep_current_reference_missing")
    elif not default:
        raise configuration_error("invalid_preset")
    if opts.preserve_active:
        active = _reference(old_policy.get("active")) or default
        old_allowed = old_policy.get("allowed")
        if isinstance(old_allowed, list):
            existing_allowed = [v for v in old_allowed if isinstance(v, str) and v]
    if request.keep_current and not active:
        active = default
    allowed = list(dict.fromkeys([*(opts.allowed_presets or existing_allowed), default]))
    if active not in allowed:
        if opts.allowed_presets:
            active = default  # explicit revocation is authoritative
        else:
            allowed.append(active)
    policy = {"active": active, "default": default, "allowed": allowed}
    permissions = {"karma": opts.karma, "nirvana": opts.nirvana}
    if legacy:
        manifest.update({"agent_name": request.agent_name, "language": opts.language or "en",
                         "admin": permissions, "preset": policy})
        # References are the only model owner on disk. Never copy preset tools.
        manifest.pop("llm", None)
        _strip_explicit_legacy(document)
        document["manifest"] = manifest
        for key in ("env_file", "venv_path"):
            document[key] = _reference(document.get(key)) or getattr(opts, key)
        covenant = opts.covenant_file or _reference(document.get("covenant_file")) or opts.default_covenant_file
        if covenant:
            document["covenant_file"] = covenant
        # Preserve the inherited explicit legacy addon conversion/seed promise.
        old_addons = document.get("addons")
        if isinstance(old_addons, dict):
            old_addons = sorted(k for k in old_addons if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", k))
        addons = old_addons if isinstance(old_addons, list) else list(opts.addons)
        if isinstance(old_addons, (dict, list)) or addons:
            document["addons"] = addons
        mcp = _object(document.get("mcp"))
        for name in addons:
            if isinstance(name, str) and name not in mcp and name in opts.mcp:
                mcp[name] = deepcopy(opts.mcp[name])
        if mcp:
            document["mcp"] = mcp
        # Preserve legacy seed inputs exactly; explicit new seed is out-of-band.
    else:
        agent = _object(document.get("agent"))
        agent.update({"name": request.agent_name, "language": opts.language or "en", "permissions": permissions})
        document.update({"agent": agent, "llm": {"preset": policy}})
        document.setdefault("tools", _initial_agent_tools() if fresh else {})
        document.setdefault("plugins", [])
        document.setdefault("mcp", deepcopy(opts.mcp))
        host = _object(document.get("host"))
        for key in ("env_file", "venv_path"):
            value = _reference(host.get(key)) or getattr(opts, key)
            if value:
                host[key] = value
        if host:
            document["host"] = host
    blueprint = _object(previous.blueprint)
    if not previous.blueprint_present:
        blueprint["state"] = ""
    blueprint.update({"agent_name": request.agent_name, "address": request.directory_name,
                      "admin": {"karma": opts.karma, "nirvana": opts.nirvana}})
    return ConfiguredMemberSeed(request.directory_name, document, blueprint, request.psyche_settings_json,
                                pad_seed=opts.pad or "", legacy_mcp_venv_path=opts.venv_path)


class ProjectConfigurationUseCase:
    def __init__(self, workspace: ProjectConfigurationPort) -> None:
        self.workspace = workspace

    def initialize(self) -> ConfigurationResult:
        if self.workspace.human_manifest_exists():
            return ConfigurationResult("initialized", "unchanged")
        return self.workspace.save_skeleton(
            {"agent_name": "human", "address": "human", "admin": None},
            ("inbox", "sent", "archive"), (".tui-asset", ".library_shared"),
        )

    def configure(self, request: ConfigureMemberRequest) -> ConfigurationResult:
        if not safe_member_name(request.directory_name):
            raise configuration_error("invalid_project_request")
        seed = configuration_seed(request, self.workspace.observe_member(request.directory_name))
        receipt = self.workspace.save_configured_member(seed)
        outcome = ("rejected" if receipt.code == "configuration_preflight_failed" else "partial") if receipt.code else "complete"
        return ConfigurationResult("configured", outcome,
                                   updated=int(receipt.init_written), failed=int(bool(receipt.code)),
                                   code=receipt.code, source=receipt)

    def propagate_policy(self, selected: str, default: str, allowed: tuple[str, ...]) -> ConfigurationResult:
        if (selected and not safe_member_name(selected)) or not default or default not in allowed:
            raise configuration_error("invalid_project_request")
        updated = skipped = failed = 0
        first_code = ""
        for member in self.workspace.observe_candidates():
            if member.directory_name in {"human", selected} or not member.init_present:
                skipped += 1
                continue
            if member.init_error:
                failed += 1
                first_code = first_code or "member_document_unreadable"
                continue
            document = _object(member.init_document)
            manifest = document.get("manifest")
            policy = (manifest.get("preset") if isinstance(manifest, dict)
                      else document.get("llm", {}).get("preset") if isinstance(document.get("llm"), dict) else None)
            if not isinstance(policy, dict):
                skipped += 1
                continue
            _strip_explicit_legacy(document)
            policy.update({"active": policy.get("active") if policy.get("active") in allowed else default,
                           "default": default, "allowed": list(allowed)})
            receipt = self.workspace.save_policy_init(member.directory_name, document)
            updated += int(receipt.init_written)
            failed += int(bool(receipt.code))
            first_code = first_code or receipt.code
        return ConfigurationResult("policy_propagated", "partial" if failed else "complete",
                                   updated, skipped, failed, first_code)

    def propagate_config(self, selected: str) -> ConfigurationResult:
        if not safe_member_name(selected):
            raise configuration_error("invalid_project_request")
        source = self.workspace.observe_member(selected)
        if source.init_error or not source.init_present:
            raise configuration_error("source_document_unreadable")
        from ..config_resolve import normalize_init
        source_manifest = normalize_init(_object(source.init_document))["manifest"]
        if not isinstance(source_manifest.get("llm"), dict) and not source_manifest.get("preset"):
            return ConfigurationResult("config_propagated", "unchanged")
        updated = skipped = failed = 0
        first_code = ""
        for member in self.workspace.observe_candidates():
            if member.directory_name in {"human", selected} or not member.init_present or member.init_error:
                skipped += 1
                continue
            document = _object(member.init_document)
            legacy = "manifest" in document
            target = document.get("manifest") if legacy else document.setdefault("agent", {})
            if not isinstance(target, dict):
                skipped += 1
                continue
            source_policy = source_manifest.get("preset")
            # Config copying is not model authorization. An existing policy
            # keeps its authoritative source, including revoked/opaque fields.
            # Only legacy mixed inline inputs retain the historical raw-LLM
            # copy beside a legacy target policy (which still wins at read).
            target_llm = document.get("llm")
            target_policy = (target.get("preset") if legacy else
                             target_llm.get("preset") if isinstance(target_llm, dict) else None)
            source_inline = source_manifest.get("llm")
            legacy_inline = "manifest" in source.init_document and bool(source_inline)
            if legacy:
                if not isinstance(target_policy, dict):
                    if source_policy and not legacy_inline:
                        target["preset"] = deepcopy(source_policy)
                        target.pop("llm", None)
                    else:
                        target["llm"] = deepcopy(source_inline)
                elif legacy_inline:
                    target["llm"] = deepcopy(source_inline)
                target["admin"] = {"karma": False, "nirvana": False}
            else:
                if not isinstance(target_policy, dict):
                    document["llm"] = ({"preset": deepcopy(source_policy)} if source_policy and not legacy_inline
                                       else deepcopy(source_inline))
                target["permissions"] = {"karma": False, "nirvana": False}
            # This explicit config propagation operation historically copies
            # init-owned tool configuration. Never obtain it from a preset.
            source_caps = _object(source_manifest.get("capabilities"))
            if legacy:
                target["capabilities"] = source_caps
            elif "manifest" not in source.init_document:
                document["tools"] = deepcopy(source.init_document.get("tools", {}))
            else:
                document["tools"] = {name: False if config is None else deepcopy(config)
                                     for name, config in source_caps.items()}
                for name in source_manifest.get("disable", []):
                    document["tools"][name] = False
            document.pop("addons", None)
            source_env = normalize_init(source.init_document).get("env_file")
            if _reference(source_env):
                if legacy:
                    document["env_file"] = source_env
                else:
                    document.setdefault("host", {})["env_file"] = source_env
            receipt = self.workspace.save_member_init(member.directory_name, document)
            updated += int(receipt.init_written)
            failed += int(bool(receipt.code))
            first_code = first_code or receipt.code
        return ConfigurationResult("config_propagated", "partial" if failed else "complete",
                                   updated, skipped, failed, first_code)

    def rehydrate(self, request: ConfigureMemberRequest) -> ConfigurationResult:
        """Gate before configuring source: all-present unchanged, mixed refused."""
        members = self.workspace.observe_candidates()
        agents: list[MemberDocuments] = []
        for member in members:
            if member.directory_name.startswith(".") or not member.blueprint_present:
                continue
            if member.blueprint_error:
                raise configuration_error("blueprint_unreadable")
            if member.blueprint is not None and member.blueprint.get("admin") is not None:
                agents.append(member)
        if not agents or request.directory_name not in {m.directory_name for m in agents}:
            raise configuration_error("rehydration_source_missing")
        present = sum(m.init_present for m in agents)
        if present == len(agents):
            return ConfigurationResult("rehydrated", "unchanged", skipped=len(agents))
        if present:
            raise configuration_error("mixed_initialization_state")
        selected = next(m for m in agents if m.directory_name == request.directory_name)
        identity = _reference(selected.blueprint.get("agent_name")) or selected.directory_name
        seed = replace(configuration_seed(replace(request, agent_name=identity), selected), only_absent=True)
        receipt = self.workspace.save_configured_member(seed)
        if receipt.code:
            outcome = "rejected" if receipt.code == "configuration_preflight_failed" else "partial"
            return ConfigurationResult("rehydrated", outcome, int(receipt.init_written), 0, 1,
                                       receipt.code, receipt)
        updated = 1
        for member in agents:
            if member.directory_name == request.directory_name:
                continue
            document = deepcopy(seed.init_document)
            _strip_explicit_legacy(document)
            if "manifest" in document:
                document["manifest"]["agent_name"] = _reference(member.blueprint.get("agent_name")) or member.directory_name
                document["manifest"]["admin"] = {}
            else:
                document["agent"]["name"] = _reference(member.blueprint.get("agent_name")) or member.directory_name
                document["agent"]["permissions"] = {}
            document.pop("addons", None)
            result = self.workspace.save_member_init(member.directory_name, document, only_absent=True,
                                                     pad_seed=seed.pad_seed)
            updated += int(result.init_written)
            if result.code:
                return ConfigurationResult("rehydrated", "partial", updated, 0, 1, result.code, receipt)
        return ConfigurationResult("rehydrated", "complete", updated, source=receipt)
