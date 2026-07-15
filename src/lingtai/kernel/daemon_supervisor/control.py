"""Atomic, identity-bound control spool for one detached supervisor."""
from __future__ import annotations

import json
import secrets
import time
import uuid
from pathlib import Path

from lingtai.kernel._fsutil import atomic_write_json

CONTROL_DIRNAME = "control"
CONTROL_SCHEMA = "lingtai.daemon_supervisor_control.v1"


def control_dir(run_dir: Path) -> Path:
    return Path(run_dir) / CONTROL_DIRNAME


def _new_request_path(run_dir: Path, kind: str) -> Path:
    request_id = uuid.uuid4().hex
    return control_dir(run_dir) / f"{kind}-{request_id}.json"


def submit_request(run_dir: Path, kind: str, payload: dict) -> Path:
    """Atomically write an identity-bound request with a long unique ID."""
    if kind not in {"ask", "reclaim"}:
        raise ValueError(f"unsupported control request kind: {kind!r}")
    if not isinstance(payload, dict):
        raise ValueError("control request payload must be an object")
    cdir = control_dir(run_dir)
    cdir.mkdir(exist_ok=True)
    try:
        cdir.chmod(0o700)
    except OSError:
        pass
    path = _new_request_path(run_dir, kind)
    request_id = path.stem.split("-", 1)[1]
    body = {
        "schema": CONTROL_SCHEMA,
        "request_id": request_id,
        "run_id": Path(run_dir).resolve().parent.name if Path(run_dir).name == "" else Path(run_dir).name,
        "kind": kind,
        "submitted_at": time.time(),
        **payload,
    }
    atomic_write_json(path, body, ensure_ascii=False, indent=2)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def done_path(request_path: Path) -> Path:
    return request_path.with_name(request_path.stem + ".done.json")


def is_done(request_path: Path) -> bool:
    return done_path(request_path).exists()


def pending_requests(run_dir: Path) -> list[Path]:
    cdir = control_dir(run_dir)
    if not cdir.is_dir():
        return []
    return [p for p in sorted(cdir.glob("*.json")) if not p.name.endswith(".done.json")]


def read_request(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != CONTROL_SCHEMA:
        raise ValueError("invalid control request schema")
    required = ("request_id", "run_id", "kind")
    if any(not isinstance(data.get(k), str) or not data[k] for k in required):
        raise ValueError("control request identity is invalid")
    if data["kind"] not in {"ask", "reclaim"}:
        raise ValueError("unknown control request kind")
    if data["kind"] == "ask" and not isinstance(data.get("message"), str):
        raise ValueError("ask request message must be a string")
    return data


def mark_request_done(request_path: Path, result: dict) -> None:
    if not isinstance(result, dict):
        raise ValueError("control request result must be an object")
    payload = {"request_id": request_path.stem.split("-", 1)[1], **result}
    path = done_path(request_path)
    atomic_write_json(path, payload, ensure_ascii=False, indent=2)
    try:
        path.chmod(0o600)
    except OSError:
        pass


__all__ = ["CONTROL_DIRNAME", "CONTROL_SCHEMA", "control_dir", "submit_request", "done_path", "is_done", "pending_requests", "read_request", "mark_request_done"]
