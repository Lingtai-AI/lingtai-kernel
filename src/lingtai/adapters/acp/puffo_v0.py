"""Operator-managed runtime registry for the constrained Puffo ACP profile."""
from __future__ import annotations

from contextlib import contextmanager, suppress
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from lingtai.kernel.turns import TurnAdmissionDecision, TurnOrigin
from lingtai.kernel.provider_admission import (
    DerivedLaunchCapability,
    DerivedLaunchDecision,
    ProviderAdmissionParent,
    ProviderAdmissionState,
    ProviderCallClass,
    ProviderCallDecision,
    RootProviderAdmission,
)


PROFILE_NAME = "puffo-v0"
REGISTRY_VERSION = 4
REVOCATION_LOG_REQUIRED = "required"
_RUNTIME_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class PuffoV0RuntimePolicy:
    """The full-tool profile's one remaining provider-turn boundary.

    This is intentionally not a tool sandbox. It admits only authenticated
    driving-adapter turns while leaving the operator-managed LingTai tool
    surface intact. State written by other sources may still be read during a
    later admitted turn; the boundary is initiation, not content provenance.
    """

    policy_version: str = "puffo-v0.full-tool-acp-ingress.v1"
    tool_surface: str = "operator_managed_full"

    def admit_turn_origin(self, origin: TurnOrigin) -> TurnAdmissionDecision:
        allowed = origin is TurnOrigin.AUTHENTICATED_ADAPTER
        return TurnAdmissionDecision(
            allowed=allowed,
            origin=origin,
            policy_version=self.policy_version,
            reason_code="allowed" if allowed else "origin_not_authenticated_adapter",
        )

    def authorize_provider_call(
        self,
        parent: ProviderAdmissionParent,
        call_class: ProviderCallClass,
    ) -> ProviderCallDecision:
        """Provide the Core-only structural half of Puffo provider admission.

        The driver-owned socket adapter will replace this root-only policy with
        a per-call host-mediated implementation for daemon/avatar work.  Until
        then, fail closed rather than allowing a derived provider call to use a
        root turn's typed origin as a transferable authority.
        """

        allowed = (
            call_class is ProviderCallClass.ROOT
            and isinstance(parent, RootProviderAdmission)
            and parent.policy_version == self.policy_version
        )
        return ProviderCallDecision(
            state=(
                ProviderAdmissionState.GRANTED
                if allowed
                else ProviderAdmissionState.INDETERMINATE
            ),
            reason_code=(
                "allowed"
                if allowed
                else "derived_admission_port_unconnected"
            ),
        )

    def authorize_derived_launch(
        self,
        _parent: RootProviderAdmission,
        _capability: DerivedLaunchCapability,
    ) -> DerivedLaunchDecision:
        """Refuse launch until the Driver-owned authority transport is wired."""

        return DerivedLaunchDecision(
            ProviderAdmissionState.INDETERMINATE,
            "derived_launch_admission_port_unconnected",
        )


RUNTIME_POLICY = PuffoV0RuntimePolicy()


class PuffoV0RegistryError(ValueError):
    """A registry failure safe to expose as a bounded local startup error."""


@dataclass(frozen=True, slots=True)
class DirectoryBinding:
    """The stable local filesystem identity of one bound directory."""

    device: int
    inode: int
    owner: int
    group: int


@dataclass(frozen=True, slots=True)
class PuffoV0Runtime:
    """One pre-provisioned local identity selected by an opaque runtime id."""

    runtime_id: str
    agent_dir: Path
    workspace: Path
    entry_digest: str
    agent_dir_binding: DirectoryBinding
    workspace_binding: DirectoryBinding
    policy_version: str


@dataclass(frozen=True, slots=True)
class PuffoV0DiscoveryCandidate:
    """One initialized identity found under an operator-selected directory."""

    agent_dir: Path
    workspace: Path
    display_name: str
    runtime_id: str | None


def default_registry_path() -> Path:
    """Return the one operator-managed registry location for this profile."""

    return Path.home() / ".lingtai" / PROFILE_NAME / "runtime-registry.json"


def _valid_runtime_id(runtime_id: object) -> str:
    if not isinstance(runtime_id, str) or _RUNTIME_ID.fullmatch(runtime_id) is None:
        raise PuffoV0RegistryError("runtime_id must be an opaque local identifier")
    return runtime_id


def _canonical_directory(path: Path, *, field: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PuffoV0RegistryError(f"{field} must be an existing directory") from exc
    if not resolved.is_dir():
        raise PuffoV0RegistryError(f"{field} must be an existing directory")
    return resolved


def _directory_binding(path: Path, *, field: str) -> DirectoryBinding:
    """Read the no-symlink directory identity used by the local binding."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PuffoV0RegistryError(f"{field} must be an existing directory") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise PuffoV0RegistryError(f"{field} must be an existing non-symlink directory")
    return DirectoryBinding(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=metadata.st_uid,
        group=metadata.st_gid,
    )


def _binding_payload(binding: DirectoryBinding) -> dict[str, int]:
    return {
        "device": binding.device,
        "group": binding.group,
        "inode": binding.inode,
        "owner": binding.owner,
    }


def _parse_binding(value: object) -> DirectoryBinding:
    if not isinstance(value, dict) or set(value) != {"device", "group", "inode", "owner"}:
        raise PuffoV0RegistryError("runtime registry entry has an invalid directory binding")
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value.values()):
        raise PuffoV0RegistryError("runtime registry entry has an invalid directory binding")
    return DirectoryBinding(
        device=value["device"],
        inode=value["inode"],
        owner=value["owner"],
        group=value["group"],
    )


def _canonical_entry(
    runtime_id: str,
    agent_dir: Path,
    workspace: Path,
    agent_dir_binding: DirectoryBinding,
    workspace_binding: DirectoryBinding,
) -> dict[str, Any]:
    return {
        "agent_dir": str(agent_dir),
        "agent_dir_binding": _binding_payload(agent_dir_binding),
        "mcp_servers": [],
        "profile": PROFILE_NAME,
        "runtime_id": runtime_id,
        "status": "active",
        "tool_surface": RUNTIME_POLICY.tool_surface,
        "turn_origins": [TurnOrigin.AUTHENTICATED_ADAPTER.value],
        "runtime_policy_version": RUNTIME_POLICY.policy_version,
        "workspace": str(workspace),
        "workspace_binding": _binding_payload(workspace_binding),
    }


def _digest(entry: dict[str, Any]) -> str:
    canonical = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_posix_registry_security() -> None:
    """Fail closed until puffo-v0 has an owner-only Windows ACL adapter.

    POSIX file modes are part of this profile's control-plane confidentiality
    boundary.  Windows cannot provide the equivalent guarantee through chmod,
    so this Phase A registry deliberately has no Windows implementation rather
    than silently creating a broadly readable registry there.
    """

    if os.name != "posix":
        raise PuffoV0RegistryError(
            "puffo-v0 registry requires POSIX owner-only filesystem permissions"
        )


def _secure_registry_directory(path: Path) -> None:
    """Create and harden the registry parent independently of umask."""

    _require_posix_registry_security()
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError as exc:
        raise PuffoV0RegistryError(
            "puffo-v0 runtime registry directory could not be secured"
        ) from exc


def _secure_registry_file(path: Path) -> bool:
    """Harden an existing registry artifact; return false when it is absent."""

    _require_posix_registry_security()
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry is unavailable or invalid") from exc
    if not stat.S_ISREG(mode):
        raise PuffoV0RegistryError("puffo-v0 runtime registry has an invalid file type")
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry could not be secured") from exc
    return True


def _revocation_log_path(path: Path) -> Path:
    """Return the append-only, owner-only tombstone log beside a registry."""

    return path.with_name(f".{path.name}.revocations.jsonl")


def _initialize_revocation_log(path: Path) -> None:
    """Create the mandatory empty tombstone log before first registry write."""

    _secure_registry_directory(path.parent)
    tombstones = _revocation_log_path(path)
    descriptor: int | None = None
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(tombstones, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise PuffoV0RegistryError(
            "puffo-v0 registry initialization found an unexpected revocation log"
        ) from exc
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 revocation log could not be initialized") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _read_revoked_runtime_ids(path: Path) -> frozenset[str]:
    """Read monotonic revocation tombstones, rejecting malformed local state."""

    tombstones = _revocation_log_path(path)
    if not _secure_registry_file(tombstones):
        raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid")
    return _parse_revocation_log(tombstones)


def _parse_revocation_log(tombstones: Path) -> frozenset[str]:
    """Parse a tombstone file without changing its permissions or contents."""

    try:
        lines = tombstones.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid") from exc
    revoked: set[str] = set()
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid") from exc
        if not isinstance(entry, dict) or set(entry) != {"runtime_id"}:
            raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid")
        revoked.add(_valid_runtime_id(entry["runtime_id"]))
    return frozenset(revoked)


def _read_revoked_runtime_ids_read_only(path: Path) -> frozenset[str]:
    """Read tombstones for discovery without creating or hardening artifacts."""

    tombstones = _revocation_log_path(path)
    try:
        mode = tombstones.lstat().st_mode
    except FileNotFoundError as exc:
        raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid") from exc
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid") from exc
    if not stat.S_ISREG(mode):
        raise PuffoV0RegistryError("puffo-v0 revocation log is unavailable or invalid")
    return _parse_revocation_log(tombstones)


def _append_revocation_tombstone(path: Path, runtime_id: str) -> None:
    """Persist a terminal revocation before the mutable registry is updated."""

    _secure_registry_directory(path.parent)
    tombstones = _revocation_log_path(path)
    descriptor: int | None = None
    try:
        flags = os.O_APPEND | os.O_WRONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(tombstones, flags, 0o600)
        os.fchmod(descriptor, 0o600)
        payload = (json.dumps({"runtime_id": runtime_id}, sort_keys=True) + "\n").encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written == 0:
                raise OSError("short write to puffo-v0 revocation log")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 revocation log could not be written") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


@contextmanager
def _registry_mutation_lock(path: Path) -> Iterator[None]:
    """Serialize one registry read-modify-write across local processes."""

    _secure_registry_directory(path.parent)
    lock_path = path.with_name(f".{path.name}.lock")
    try:
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lock_path, flags, 0o600)
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry lock is unavailable") from exc

    try:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        with suppress(OSError):
            os.close(descriptor)
        raise PuffoV0RegistryError("puffo-v0 runtime registry lock is unavailable") from exc
    try:
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(descriptor)


def _read_registry(path: Path) -> dict[str, Any]:
    _secure_registry_file(path)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry is unavailable or invalid") from exc
    if not isinstance(data, dict) or set(data) != {"revocation_log", "runtimes", "version"}:
        raise PuffoV0RegistryError("puffo-v0 runtime registry has an invalid shape")
    if (
        data["version"] != REGISTRY_VERSION
        or data["revocation_log"] != REVOCATION_LOG_REQUIRED
        or not isinstance(data["runtimes"], dict)
    ):
        raise PuffoV0RegistryError("puffo-v0 runtime registry has an unsupported version")
    return data


def _read_registry_read_only(path: Path) -> dict[str, Any]:
    """Read a registry for discovery without mutating its security metadata."""

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry is unavailable or invalid") from exc
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry is unavailable or invalid") from exc
    if not stat.S_ISREG(mode):
        raise PuffoV0RegistryError("puffo-v0 runtime registry has an invalid file type")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry is unavailable or invalid") from exc
    if not isinstance(data, dict) or set(data) != {"revocation_log", "runtimes", "version"}:
        raise PuffoV0RegistryError("puffo-v0 runtime registry has an invalid shape")
    if (
        data["version"] != REGISTRY_VERSION
        or data["revocation_log"] != REVOCATION_LOG_REQUIRED
        or not isinstance(data["runtimes"], dict)
    ):
        raise PuffoV0RegistryError("puffo-v0 runtime registry has an unsupported version")
    return data


def _write_registry(path: Path, data: dict[str, Any]) -> None:
    _secure_registry_directory(path.parent)
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(raw_temporary)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise PuffoV0RegistryError("puffo-v0 runtime registry could not be written") from exc


def _bound_directory(
    path_value: object,
    binding_value: object,
    *,
    field: str,
) -> tuple[Path, DirectoryBinding]:
    """Resolve one stored path and require its present identity to match."""

    if not isinstance(path_value, str):
        raise PuffoV0RegistryError("runtime registry entry has invalid paths")
    expected = _parse_binding(binding_value)
    stored = Path(path_value)
    resolved = _canonical_directory(stored, field=field)
    if str(resolved) != path_value:
        raise PuffoV0RegistryError(f"{field} binding no longer matches its canonical path")
    observed = _directory_binding(resolved, field=field)
    if observed != expected:
        raise PuffoV0RegistryError(f"{field} binding no longer matches its provisioned identity")
    return resolved, expected


def _active_binding_conflicts(
    runtimes: dict[str, Any],
    *,
    agent_dir: Path,
    workspace: Path,
) -> None:
    """Require the Phase A active binding to remain one-to-one."""

    for existing_runtime_id, entry in runtimes.items():
        if not isinstance(existing_runtime_id, str) or not isinstance(entry, dict):
            raise PuffoV0RegistryError("runtime registry entry has an invalid shape")
        if entry.get("status") != "active":
            continue
        if entry.get("agent_dir") == str(agent_dir):
            raise PuffoV0RegistryError("agent_dir is already bound to an active runtime")
        if entry.get("workspace") == str(workspace):
            raise PuffoV0RegistryError("workspace is already bound to an active runtime")


def provision_runtime(
    runtime_id: str,
    agent_dir: Path,
    workspace: Path,
    *,
    registry_path: Path | None = None,
) -> PuffoV0Runtime:
    """Bind one existing persistent agent identity to a local runtime id.

    This is an operator control-plane operation.  The ACP data-plane accepts
    only the resulting id and never accepts either filesystem path.
    """

    runtime_id = _valid_runtime_id(runtime_id)
    agent_dir = _canonical_directory(agent_dir, field="agent_dir")
    workspace = _canonical_directory(workspace, field="workspace")
    agent_dir_binding = _directory_binding(agent_dir, field="agent_dir")
    workspace_binding = _directory_binding(workspace, field="workspace")
    if not (agent_dir / "init.json").is_file():
        raise PuffoV0RegistryError("agent_dir must contain init.json")
    path = registry_path or default_registry_path()
    with _registry_mutation_lock(path):
        if path.exists():
            revoked_runtime_ids = _read_revoked_runtime_ids(path)
            registry = _read_registry(path)
        else:
            _initialize_revocation_log(path)
            revoked_runtime_ids = frozenset()
            registry = {
                "revocation_log": REVOCATION_LOG_REQUIRED,
                "version": REGISTRY_VERSION,
                "runtimes": {},
            }
        if runtime_id in revoked_runtime_ids:
            raise PuffoV0RegistryError("runtime_id is revoked and cannot be provisioned again")
        runtimes = registry["runtimes"]
        if runtime_id in runtimes:
            raise PuffoV0RegistryError("runtime_id is already provisioned")
        _active_binding_conflicts(runtimes, agent_dir=agent_dir, workspace=workspace)
        entry = _canonical_entry(
            runtime_id,
            agent_dir,
            workspace,
            agent_dir_binding,
            workspace_binding,
        )
        entry["entry_digest"] = _digest(entry)
        runtimes[runtime_id] = entry
        _write_registry(path, registry)
    return PuffoV0Runtime(
        runtime_id,
        agent_dir,
        workspace,
        entry["entry_digest"],
        agent_dir_binding,
        workspace_binding,
        RUNTIME_POLICY.policy_version,
    )


def revoke_runtime(runtime_id: str, *, registry_path: Path | None = None) -> None:
    """Mark a provisioned profile identity unavailable for future ACP spawns."""

    runtime_id = _valid_runtime_id(runtime_id)
    path = registry_path or default_registry_path()
    with _registry_mutation_lock(path):
        registry = _read_registry(path)
        entry = registry["runtimes"].get(runtime_id)
        if not isinstance(entry, dict):
            raise PuffoV0RegistryError("runtime_id is not provisioned")
        if runtime_id not in _read_revoked_runtime_ids(path):
            _append_revocation_tombstone(path, runtime_id)
        entry["status"] = "revoked"
        canonical = {key: value for key, value in entry.items() if key != "entry_digest"}
        entry["entry_digest"] = _digest(canonical)
        _write_registry(path, registry)


def _is_active_discovery_entry(runtime_id: str, entry: object) -> bool:
    """Return whether an entry can safely identify an active local binding."""

    if not isinstance(entry, dict):
        return False
    expected_keys = {
        "agent_dir", "agent_dir_binding", "entry_digest", "mcp_servers",
        "profile", "runtime_id", "runtime_policy_version", "status", "tool_surface",
        "turn_origins", "workspace", "workspace_binding",
    }
    if set(entry) != expected_keys:
        return False
    canonical = {key: value for key, value in entry.items() if key != "entry_digest"}
    return (
        entry.get("profile") == PROFILE_NAME
        and entry.get("runtime_id") == runtime_id
        and entry.get("status") == "active"
        and entry.get("mcp_servers") == []
        and entry.get("tool_surface") == RUNTIME_POLICY.tool_surface
        and entry.get("turn_origins") == [TurnOrigin.AUTHENTICATED_ADAPTER.value]
        and entry.get("runtime_policy_version") == RUNTIME_POLICY.policy_version
        and isinstance(entry.get("agent_dir"), str)
        and isinstance(entry.get("workspace"), str)
        and isinstance(entry.get("entry_digest"), str)
        and entry["entry_digest"] == _digest(canonical)
    )


def _active_discovery_bindings(path: Path) -> dict[Path, PuffoV0Runtime]:
    """Load active bindings without creating files, locks, or permission writes."""

    try:
        path.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise PuffoV0RegistryError("puffo-v0 runtime registry is unavailable or invalid") from exc

    revoked_runtime_ids = _read_revoked_runtime_ids_read_only(path)
    registry = _read_registry_read_only(path)
    bindings: dict[Path, PuffoV0Runtime] = {}
    for runtime_id, entry in registry["runtimes"].items():
        if not isinstance(runtime_id, str) or runtime_id in revoked_runtime_ids:
            continue
        if not _is_active_discovery_entry(runtime_id, entry):
            continue
        agent_dir = Path(entry["agent_dir"])
        if agent_dir in bindings:
            raise PuffoV0RegistryError("multiple active runtimes bind the same agent_dir")
        bindings[agent_dir] = PuffoV0Runtime(
            runtime_id=runtime_id,
            agent_dir=agent_dir,
            workspace=Path(entry["workspace"]),
            entry_digest=entry["entry_digest"],
            agent_dir_binding=_parse_binding(entry["agent_dir_binding"]),
            workspace_binding=_parse_binding(entry["workspace_binding"]),
            policy_version=entry["runtime_policy_version"],
        )
    return bindings


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def discover_runtimes(
    root: Path,
    *,
    registry_path: Path | None = None,
) -> list[PuffoV0DiscoveryCandidate]:
    """List initialized agents below one user-selected root without side effects.

    Directory symlinks are never followed.  The registry is read directly rather
    than through its mutation/security-hardening helpers so discovery cannot
    create a lock, initialize a registry, or rewrite permissions.
    """

    canonical_root = _canonical_directory(root, field="root")
    bindings = _active_discovery_bindings(registry_path or default_registry_path())
    candidates: list[PuffoV0DiscoveryCandidate] = []

    def _ignore_walk_error(_error: OSError) -> None:
        return None

    for raw_current, directory_names, _file_names in os.walk(
        canonical_root,
        topdown=True,
        followlinks=False,
        onerror=_ignore_walk_error,
    ):
        current = Path(raw_current)
        directory_names[:] = [
            name
            for name in directory_names
            if not name.startswith(".") and not (current / name).is_symlink()
        ]
        try:
            agent_dir = current.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not _is_within_root(agent_dir, canonical_root):
            continue
        try:
            initialized = (agent_dir / "init.json").is_file()
        except OSError:
            continue
        if not initialized:
            continue
        binding = bindings.get(agent_dir)
        candidates.append(
            PuffoV0DiscoveryCandidate(
                agent_dir=agent_dir,
                workspace=binding.workspace if binding is not None else agent_dir,
                display_name=agent_dir.name,
                runtime_id=binding.runtime_id if binding is not None else None,
            )
        )
    return sorted(candidates, key=lambda candidate: str(candidate.agent_dir))


def resolve_runtime(
    runtime_id: str, *, registry_path: Path | None = None
) -> PuffoV0Runtime:
    """Resolve one active runtime id into an immutable local spawn specification."""

    runtime_id = _valid_runtime_id(runtime_id)
    path = registry_path or default_registry_path()
    _secure_registry_directory(path.parent)
    if runtime_id in _read_revoked_runtime_ids(path):
        raise PuffoV0RegistryError("runtime registry entry is inactive or does not match puffo-v0")
    registry = _read_registry(path)
    entry = registry["runtimes"].get(runtime_id)
    if not isinstance(entry, dict):
        raise PuffoV0RegistryError("runtime_id is not provisioned")
    if not _is_active_discovery_entry(runtime_id, entry):
        raise PuffoV0RegistryError("runtime registry entry is inactive or does not match puffo-v0")
    agent_dir, agent_dir_binding = _bound_directory(
        entry.get("agent_dir"), entry.get("agent_dir_binding"), field="agent_dir"
    )
    workspace, workspace_binding = _bound_directory(
        entry.get("workspace"), entry.get("workspace_binding"), field="workspace"
    )
    if not (agent_dir / "init.json").is_file():
        raise PuffoV0RegistryError("registered agent identity is no longer initialized")
    return PuffoV0Runtime(
        runtime_id,
        agent_dir,
        workspace,
        entry["entry_digest"],
        agent_dir_binding,
        workspace_binding,
        RUNTIME_POLICY.policy_version,
    )


__all__ = [
    "DirectoryBinding",
    "PROFILE_NAME",
    "PuffoV0DiscoveryCandidate",
    "PuffoV0RegistryError",
    "PuffoV0Runtime",
    "PuffoV0RuntimePolicy",
    "RUNTIME_POLICY",
    "default_registry_path",
    "discover_runtimes",
    "provision_runtime",
    "resolve_runtime",
    "revoke_runtime",
]
