"""One-shot daemon return observation helper."""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = "lingtai.daemon-return-observation.v0"
STATUS_SCHEMA_VERSION = "lingtai.daemon-return-observation-status.v0"
SIDE_DIR = Path(".supervisor/return-observation")
GENERATION_RE = re.compile(r"(?:g0000|g[1-9][0-9]*-[0-9a-f]{16})\Z")
# Bounded local sample on 2026-08-14: 80 existing daemon run dirs / 309
# candidate files had max daemon.json=2194B, artifacts.json=1268B,
# supervisor_manifest.json=810B, result.txt=15B.  These caps leave room for
# real result previews/events while keeping the helper unable to scan arbitrary
# large artifacts.
MAX_FILES = 32
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
KNOWN_FILES = (
    ("state", "daemon.json"),
    ("result", "result.txt"),
    ("artifacts_manifest", "artifacts.json"),
    ("events", "events.jsonl"),
    ("supervisor_manifest", "supervisor_manifest.json"),
)
DISPATCH_INTENT_NAME = "dispatch-intent.v0.json"
DISPATCH_RESULT_NAME = "dispatch-result.v0.json"


class Unavailable(Exception):
    def __init__(self, reason_code: str, details: dict | None = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = details or {}


def _is_regular(mode: int) -> bool:
    return (mode & 0o170000) == 0o100000


def _safe_rel(value: str) -> str:
    rel = PurePosixPath(value)
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise Unavailable("path_escape")
    return rel.as_posix()


def _open_beneath(root_fd: int, rel: str) -> int:
    flags_no_follow = getattr(os, "O_NOFOLLOW", 0)
    parts = _safe_rel(rel).split("/")
    current_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | flags_no_follow,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
                    raise Unavailable("path_escape")
                raise
            os.close(current_fd)
            current_fd = next_fd
        try:
            return os.open(parts[-1], os.O_RDONLY | flags_no_follow, dir_fd=current_fd)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EACCES, errno.EPERM}:
                raise Unavailable("path_escape")
            raise
    finally:
        os.close(current_fd)


def _open_or_create_dir(parent_fd: int, name: str) -> int:
    if "/" in name or name in {"", ".", ".."}:
        raise Unavailable("path_escape")
    flags_no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | flags_no_follow,
            dir_fd=parent_fd,
        )
        os.fchmod(fd, 0o700)
        return fd
    except FileNotFoundError:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | flags_no_follow,
            dir_fd=parent_fd,
        )
        try:
            os.fchmod(fd, 0o700)
        except OSError:
            pass
        return fd
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
            raise Unavailable("unsafe_sidecar")
        raise


def _read_fd_stable(fd: int, *, cap: int = MAX_FILE_BYTES, chmod_mode: int | None = None) -> tuple[bytes, os.stat_result]:
    if chmod_mode is not None:
        os.fchmod(fd, chmod_mode)
    first = os.fstat(fd)
    if not _is_regular(first.st_mode):
        raise Unavailable("not_regular_file")
    if first.st_size > cap:
        raise Unavailable("per_file_cap")
    chunks: list[bytes] = []
    remaining = first.st_size
    while remaining > 0:
        chunk = os.read(fd, min(64 * 1024, remaining))
        if not chunk:
            raise Unavailable("short_read")
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    second = os.fstat(fd)
    if (
        first.st_dev != second.st_dev
        or first.st_ino != second.st_ino
        or first.st_size != second.st_size
        or first.st_mtime_ns != second.st_mtime_ns
        or first.st_ctime_ns != second.st_ctime_ns
    ):
        raise Unavailable("mutation_detected")
    return data, first


def _read_file_beneath(root_fd: int, rel: str) -> tuple[bytes, os.stat_result]:
    fd = _open_beneath(root_fd, rel)
    try:
        return _read_fd_stable(fd)
    finally:
        os.close(fd)


def _row_for_bytes(rel: str, data: bytes, st: os.stat_result) -> dict:
    return {
        "relative_ref": rel,
        "size": st.st_size,
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "stability": "stable",
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
    }


def _hash_file_with_bytes(root_fd: int, rel: str) -> tuple[dict, bytes]:
    data, st = _read_file_beneath(root_fd, rel)
    return _row_for_bytes(rel, data, st), data


def _hash_file(root_fd: int, rel: str) -> dict:
    row, _ = _hash_file_with_bytes(root_fd, rel)
    return row


def _exists_beneath(root_fd: int, rel: str) -> bool:
    try:
        fd = _open_beneath(root_fd, rel)
    except FileNotFoundError:
        return False
    except NotADirectoryError:
        return False
    else:
        os.close(fd)
        return True


def _read_json_file(root_fd: int, rel: str) -> dict:
    raw, _ = _read_file_beneath(root_fd, rel)
    return _json_object_from_bytes(raw)


def _json_object_from_bytes(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_json_at(dir_fd: int, name: str, payload: dict) -> bytes:
    if "/" in name or name.startswith(".tmp-"):
        raise Unavailable("path_escape")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tmp = f".tmp-{name}-{os.getpid()}-{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
    try:
        view = memoryview(data)
        written = 0
        while written < len(data):
            count = os.write(fd, view[written:])
            if count <= 0:
                raise Unavailable("short_write")
            written += count
        os.fsync(fd)
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    try:
        try:
            os.link(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except FileExistsError:
            existing = _read_file_at(dir_fd, name)
            if existing == data:
                return data
            raise Unavailable("receipt_conflict")
        return data
    finally:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass


def _read_file_at(dir_fd: int, name: str) -> bytes | None:
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EACCES, errno.EPERM}:
            raise Unavailable("unsafe_sidecar")
        raise
    try:
        data, _ = _read_fd_stable(fd, chmod_mode=0o600)
        return data
    finally:
        os.close(fd)


def _digest_sidecar_if_present(side_fd: int, name: str) -> dict:
    data = _read_file_at(side_fd, name)
    if data is None:
        return {"status": "missing_binding"}
    return {
        "status": "observed",
        "relative_ref": f".supervisor/return-observation/{name}",
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def _read_sidecar_json_if_present(side_fd: int, name: str) -> tuple[dict | None, dict]:
    data = _read_file_at(side_fd, name)
    if data is None:
        return None, {"status": "missing_binding"}
    try:
        payload = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Unavailable("dispatch_binding_unreadable")
    if not isinstance(payload, dict):
        raise Unavailable("dispatch_binding_unreadable")
    return payload, {
        "status": "observed",
        "relative_ref": f".supervisor/return-observation/{name}",
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def _generation_index(generation: str) -> int:
    if generation == "g0000":
        return 0
    return int(generation[1:].split("-", 1)[0])


def _predecessor_ref(side_fd: int, generation: str) -> dict | None:
    index = _generation_index(generation)
    if index == 0:
        return None
    if index == 1:
        candidates = ["g0000.status.json"]
    else:
        prefix = f"g{index - 1}-"
        suffix = ".status.json"
        candidates = [
            name for name in os.listdir(side_fd)
            if name.startswith(prefix)
            and name.endswith(suffix)
            and GENERATION_RE.fullmatch(name.removesuffix(suffix))
        ]
    if not candidates:
        raise Unavailable("missing_predecessor")
    if len(candidates) != 1:
        raise Unavailable("conflicting_predecessor")
    status_name = candidates[0]
    status_data = _read_file_at(side_fd, status_name)
    if status_data is None:
        raise Unavailable("missing_predecessor")
    try:
        payload = json.loads(status_data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Unavailable("conflicting_predecessor")
    predecessor_generation = status_name.removesuffix(".status.json")
    portable_name = f"{predecessor_generation}.portable.json"
    if not isinstance(payload, dict):
        raise Unavailable("conflicting_predecessor")
    if payload.get("generation") != predecessor_generation:
        raise Unavailable("conflicting_predecessor")
    if payload.get("portable_ref") != f".supervisor/return-observation/{portable_name}":
        raise Unavailable("conflicting_predecessor")
    digest = payload.get("portable_digest") if isinstance(payload, dict) else None
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
        or any(ch not in "0123456789abcdef" for ch in digest.removeprefix("sha256:"))
    ):
        raise Unavailable("conflicting_predecessor")
    portable_bytes = _read_file_at(side_fd, portable_name)
    if portable_bytes is None:
        raise Unavailable("missing_predecessor")
    current_digest = "sha256:" + hashlib.sha256(portable_bytes).hexdigest()
    if current_digest != digest:
        raise Unavailable("predecessor_tamper")
    try:
        portable_payload = json.loads(portable_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise Unavailable("predecessor_tamper")
    if not isinstance(portable_payload, dict) or portable_payload.get("generation_key") != predecessor_generation:
        raise Unavailable("predecessor_tamper")
    return {
        "generation": predecessor_generation,
        "portable_digest": digest,
        "status_ref": f".supervisor/return-observation/{status_name}",
        "portable_ref": f".supervisor/return-observation/{portable_name}",
    }


def _declared_artifact_rows(
    root_fd: int, artifacts_data: dict, observed: list[dict], total: int
) -> tuple[list[dict], list[dict], int]:
    if not isinstance(artifacts_data, dict):
        return [], [], total
    declared = artifacts_data.get("artifacts")
    if declared is None:
        return [], [], total
    if artifacts_data.get("truncated") is True:
        raise Unavailable("artifact_manifest_truncated")
    if not isinstance(declared, list):
        raise Unavailable("artifact_manifest_invalid")
    rows: list[dict] = []
    differences: list[dict] = []
    seen = {row["relative_ref"] for row in observed}
    for item in declared:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise Unavailable("artifact_manifest_invalid")
        rel = _safe_rel(item["path"])
        if not _exists_beneath(root_fd, rel):
            raise Unavailable("artifact_missing")
        row = _hash_file(root_fd, rel)
        row["role"] = item.get("role") if isinstance(item.get("role"), str) else "declared_artifact"
        row["declared_path"] = rel
        manifest_size = item.get("size")
        if isinstance(manifest_size, int) and manifest_size != row["size"]:
            differences.append({
                "path": rel,
                "field": "size",
                "declared": manifest_size,
                "observed": row["size"],
            })
        rows.append(row)
        if rel not in seen:
            observed.append(row)
            seen.add(rel)
            total += int(row["size"])
            if len(observed) > MAX_FILES:
                raise Unavailable("file_count_cap")
            if total > MAX_TOTAL_BYTES:
                raise Unavailable("total_byte_cap")
    return rows, differences, total


def _expected_intent_from_manifest(manifest: dict, manifest_digest: str) -> dict:
    call_parameters = {
        "backend": manifest.get("backend"),
        "task": manifest.get("task"),
        "tools": manifest.get("tools"),
        "mcp": manifest.get("mcp"),
        "max_turns": manifest.get("max_turns"),
        "timeout_s": manifest.get("timeout_s"),
        "context_token_limit": manifest.get("context_token_limit"),
        "llm": manifest.get("llm"),
        "backend_argv": manifest.get("backend_argv"),
        "language": manifest.get("language"),
        "preset_name": manifest.get("preset_name"),
        "preset_llm": manifest.get("preset_llm"),
        "preset_capabilities": manifest.get("preset_capabilities"),
        "group_id": manifest.get("group_id"),
    }
    return {
        "schema_version": "lingtai.daemon-dispatch-intent.v0",
        "run_id": manifest.get("run_id"),
        "group_id": manifest.get("group_id"),
        "task": manifest.get("task"),
        "tools": manifest.get("tools"),
        "call_parameters": call_parameters,
        "manifest": manifest,
        "manifest_sha256": manifest_digest,
        "authority": "parent_owned",
    }


def _dispatch_mismatches(
    *, side_fd: int, manifest: dict, manifest_digest: str, state_data: dict
) -> tuple[dict, dict, list[dict]]:
    intent_payload, intent_ref = _read_sidecar_json_if_present(side_fd, DISPATCH_INTENT_NAME)
    result_payload, result_ref = _read_sidecar_json_if_present(side_fd, DISPATCH_RESULT_NAME)
    mismatches: list[dict] = []
    if intent_payload is not None:
        expected = _expected_intent_from_manifest(manifest, manifest_digest)
        for key, expected_value in expected.items():
            if intent_payload.get(key) != expected_value:
                mismatches.append({"receipt": "dispatch_intent", "field": key})
    if result_payload is not None:
        result = result_payload.get("dispatch_result")
        if not isinstance(result, dict):
            mismatches.append({"receipt": "dispatch_result", "field": "dispatch_result"})
        else:
            ids = result.get("ids")
            if not isinstance(ids, list) or manifest.get("run_id") not in ids:
                mismatches.append({"receipt": "dispatch_result", "field": "ids"})
            if result.get("group_id") != manifest.get("group_id"):
                mismatches.append({"receipt": "dispatch_result", "field": "group_id"})
            if result.get("status") != "dispatched":
                mismatches.append({"receipt": "dispatch_result", "field": "status"})
        if result_payload.get("run_id") != manifest.get("run_id"):
            mismatches.append({"receipt": "dispatch_result", "field": "run_id"})
    if state_data.get("run_id") not in {None, manifest.get("run_id")}:
        mismatches.append({"receipt": "daemon_state", "field": "run_id"})
    return intent_ref, result_ref, mismatches


def _ensure_final_at(dir_fd: int, name: str, payload: dict) -> bytes:
    desired = _json_bytes(payload)
    existing = _read_file_at(dir_fd, name)
    if existing is None:
        return _atomic_json_at(dir_fd, name, payload)
    if existing == desired:
        return desired
    raise Unavailable("receipt_conflict")


def _write_generation(side_fd: int, generation: str, host: dict, portable: dict, status: dict) -> tuple[str, str]:
    host_name = f"{generation}.host.json"
    portable_name = f"{generation}.portable.json"
    status_name = f"{generation}.status.json"
    portable_bytes = _json_bytes(portable)
    digest = "sha256:" + hashlib.sha256(portable_bytes).hexdigest()
    status["portable_digest"] = digest

    existing_status = _read_file_at(side_fd, status_name)
    if existing_status is not None:
        if (
            _read_file_at(side_fd, host_name) == _json_bytes(host)
            and _read_file_at(side_fd, portable_name) == portable_bytes
            and existing_status == _json_bytes(status)
        ):
            return "available", digest
        raise Unavailable("receipt_conflict")
    _ensure_final_at(side_fd, host_name, host)
    _ensure_final_at(side_fd, portable_name, portable)
    _atomic_json_at(side_fd, status_name, status)
    return "available", digest


def _validate_generation(generation: str) -> str:
    if not isinstance(generation, str) or not GENERATION_RE.fullmatch(generation):
        raise Unavailable("invalid_generation")
    return generation


def _check_authoritative_state(state_data: dict, *, generation: str, terminal_state: str) -> None:
    if generation == "g0000":
        if state_data.get("state") != terminal_state:
            raise Unavailable("terminal_state_mismatch")
        return
    if not str(terminal_state).startswith("follow-up "):
        raise Unavailable("generation_state_mismatch")
    expected_status = terminal_state.removeprefix("follow-up ")
    if state_data.get("followup_generation") != generation:
        raise Unavailable("generation_state_mismatch")
    if state_data.get("followup_status") != expected_status:
        raise Unavailable("terminal_state_mismatch")


def observe(run_dir: Path, manifest_path: Path, generation: str, terminal_state: str) -> dict:
    generation = _validate_generation(generation)
    run_dir_abs = os.path.abspath(os.fspath(run_dir))
    expected_manifest_path = os.path.join(run_dir_abs, "supervisor_manifest.json")
    if os.path.abspath(os.fspath(manifest_path)) != expected_manifest_path:
        raise Unavailable("manifest_path_mismatch")
    root_fd = os.open(
        run_dir,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        supervisor_fd = _open_or_create_dir(root_fd, ".supervisor")
        try:
            side_fd = _open_or_create_dir(supervisor_fd, "return-observation")
        finally:
            os.close(supervisor_fd)
        keep_side_fd = False
        try:
            _read_file_at(side_fd, f"{generation}.host.json")
            observed = []
            stable_bytes: dict[str, bytes] = {}
            total = 0
            for role, rel in KNOWN_FILES:
                if not _exists_beneath(root_fd, rel):
                    continue
                row, raw = _hash_file_with_bytes(root_fd, rel)
                row["role"] = role
                observed.append(row)
                stable_bytes[rel] = raw
                total += int(row["size"])
                if len(observed) > MAX_FILES:
                    raise Unavailable("file_count_cap")
                if total > MAX_TOTAL_BYTES:
                    raise Unavailable("total_byte_cap")
            state_data = _json_object_from_bytes(stable_bytes["daemon.json"]) if "daemon.json" in stable_bytes else {}
            artifacts_data = _json_object_from_bytes(stable_bytes["artifacts.json"]) if "artifacts.json" in stable_bytes else {}
            if "supervisor_manifest.json" not in stable_bytes:
                raise Unavailable("manifest_missing")
            manifest_data = _json_object_from_bytes(stable_bytes["supervisor_manifest.json"])
            if manifest_data.get("run_dir") != run_dir_abs or manifest_data.get("run_id") != os.path.basename(run_dir_abs):
                raise Unavailable("manifest_identity_mismatch")
            manifest_row = next((row for row in observed if row["relative_ref"] == "supervisor_manifest.json"), None)
            if manifest_row is None:
                raise Unavailable("manifest_missing")
            declared_rows, artifact_differences, total = _declared_artifact_rows(
                root_fd, artifacts_data, observed, total
            )
            _check_authoritative_state(state_data, generation=generation, terminal_state=terminal_state)
            dispatch_intent_ref, dispatch_result_ref, dispatch_mismatches = _dispatch_mismatches(
                side_fd=side_fd,
                manifest=manifest_data,
                manifest_digest=manifest_row["sha256"],
                state_data=state_data,
            )
            if dispatch_mismatches:
                raise Unavailable("dispatch_binding_mismatch", {"mismatches": dispatch_mismatches})
            predecessor = _predecessor_ref(side_fd, generation)
            side_fd_for_write = side_fd
            keep_side_fd = True
        finally:
            if not keep_side_fd:
                os.close(side_fd)
    finally:
        os.close(root_fd)

    host = {
        "schema_version": SCHEMA_VERSION + ".host",
        "run_dir": str(run_dir),
        "manifest_path": str(manifest_path),
        "observed_files": observed,
        "declared_artifact_files": declared_rows,
    }
    host_digest = "sha256:" + hashlib.sha256(
        json.dumps(host, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    declared_artifacts = artifacts_data.get("artifacts") if isinstance(artifacts_data, dict) else None
    portable = {
        "schema_version": SCHEMA_VERSION,
        "safe_observation_id": "return-observation:" + hashlib.sha256(
            f"{generation}:{terminal_state}:{host_digest}".encode("utf-8")
        ).hexdigest()[:24],
        "host_mapping_digest": host_digest,
        "dispatch_intent_ref": dispatch_intent_ref,
        "dispatch_result_ref": dispatch_result_ref,
        "generation_key": generation,
        "terminal_state": terminal_state,
        "snapshot_status": "stable",
        "observed_files": [
            {k: row[k] for k in ("role", "relative_ref", "size", "sha256", "stability")}
            for row in observed
        ],
        "declared_artifacts": {
            "status": "observed" if isinstance(declared_artifacts, list) else "missing_or_unavailable",
            "count": len(declared_artifacts) if isinstance(declared_artifacts, list) else 0,
            "truncated": bool(artifacts_data.get("truncated")) if isinstance(artifacts_data, dict) else False,
            "differences": artifact_differences,
        },
        "observed_artifacts": {
            "roles": [row["role"] for row in observed],
            "count": len(observed),
            "declared_file_count": len(declared_rows),
            "caps": {
                "max_files": MAX_FILES,
                "max_file_bytes": MAX_FILE_BYTES,
                "max_total_bytes": MAX_TOTAL_BYTES,
            },
        },
        "mechanically_observed": ["terminal_state", "observed_file_size_sha256_stability", "artifact_manifest_presence"],
        "daemon_declared": [
            key for key in ("task", "result_preview", "error", "followup_status", "followup_generation")
            if key in state_data
        ],
        "parent_verification_required": ["semantic_truth", "source_completeness", "side_effect_completeness", "remaining_gates"],
        "raw_fallback_refs": [
            {"role": row["role"], "relative_ref": row["relative_ref"]}
            for row in observed
        ],
        "supersedes": predecessor,
        "superseded_by": None,
        "notification_publication_state": "not_yet_attempted",
        "authority": "advisory_only",
        "raw_result_unchanged": True,
    }
    status = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "state": "available",
        "generation": generation,
        "reason_code": None,
        "portable_ref": f".supervisor/return-observation/{generation}.portable.json",
        "authority": "advisory_only",
    }
    try:
        state, digest = _write_generation(side_fd_for_write, generation, host, portable, status)
    finally:
        os.close(side_fd_for_write)
    return {
        "state": state,
        "generation": generation,
        "receipt_digest": digest,
        "portable_ref": status["portable_ref"],
        "authority": "advisory_only",
        "raw_result_unchanged": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--terminal-state", required=True)
    args = parser.parse_args(argv)
    try:
        result = observe(Path(args.run_dir), Path(args.manifest_path), args.generation, args.terminal_state)
    except Unavailable as exc:
        result = {"state": "unavailable", "reason_code": exc.reason_code}
        if exc.details:
            result["details"] = exc.details
    except BaseException:
        result = {"state": "unavailable", "reason_code": "unexpected_exception"}
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
