"""Fail-open wrapper for the daemon return observer helper."""
from __future__ import annotations

import json
import os
import re
import hashlib
import subprocess
import sys
from pathlib import Path

HELPER_DEADLINE_S = 1.5
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
GENERATION_RE = re.compile(r"(?:g0000|g[1-9][0-9]*-[0-9a-f]{16})\Z")
SAFE_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONIOENCODING": "utf-8",
}


def _generation_for(status: str, state: dict) -> str:
    if str(status).startswith("follow-up"):
        generation = state.get("followup_generation")
        if isinstance(generation, str) and GENERATION_RE.fullmatch(generation):
            return generation
        return ""
    return "g0000"


def observe_return_bounded(run_dir, manifest: dict, *, status: str, state: dict) -> dict | None:
    """Return a safe notification block, or ``None`` on any observer failure."""
    if manifest.get("return_observer_enabled") is not True:
        return None
    try:
        expected_generation = _generation_for(status, state)
        if not expected_generation:
            return None
        env = dict(SAFE_ENV)
        if os.environ.get("PYTHONPATH"):
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "lingtai.tools.daemon.return_observer_helper",
                "--run-dir",
                str(run_dir.path),
                "--manifest-path",
                str(Path(manifest["run_dir"]) / "supervisor_manifest.json"),
                "--generation",
                expected_generation,
                "--terminal-state",
                str(status),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=HELPER_DEADLINE_S,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            return None
        if len(proc.stdout or "") > 8192:
            return None
        data = json.loads(proc.stdout or "{}")
        if not isinstance(data, dict) or data.get("state") != "available":
            return None
        digest = data.get("receipt_digest")
        generation = data.get("generation")
        if not (isinstance(digest, str) and SHA256_RE.fullmatch(digest)):
            return None
        if not isinstance(generation, str) or not GENERATION_RE.fullmatch(generation):
            return None
        if generation != expected_generation:
            return None
        return {
            "schema_version": "lingtai.return-observation-notice.v0",
            "state": "available",
            "generation": generation,
            "receipt_digest": digest,
            "authority": "advisory_only",
            "raw_result_unchanged": True,
        }
    except BaseException:
        return None


def _write_sidecar_json(run_dir, name: str, payload: dict) -> str | None:
    try:
        from lingtai.tools.daemon import return_observer_helper as helper

        root_fd = helper.os.open(
            run_dir.path,
            helper.os.O_RDONLY
            | getattr(helper.os, "O_DIRECTORY", 0)
            | getattr(helper.os, "O_NOFOLLOW", 0),
        )
        try:
            supervisor_fd = helper._open_or_create_dir(root_fd, ".supervisor")
            try:
                side_fd = helper._open_or_create_dir(supervisor_fd, "return-observation")
            finally:
                helper.os.close(supervisor_fd)
            try:
                helper._ensure_final_at(side_fd, name, payload)
                data = helper._json_bytes(payload)
                return "sha256:" + hashlib.sha256(data).hexdigest()
            finally:
                helper.os.close(side_fd)
        finally:
            helper.os.close(root_fd)
    except BaseException:
        return None


def write_dispatch_intent_receipt(run_dir, manifest: dict) -> str | None:
    """Best-effort parent-owned pre-launch receipt for enabled observation."""
    try:
        if manifest.get("return_observer_enabled") is not True:
            return None
        try:
            manifest_bytes = (Path(manifest["run_dir"]) / "supervisor_manifest.json").read_bytes()
        except BaseException:
            manifest_bytes = json.dumps(
                manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
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
        payload = {
            "schema_version": "lingtai.daemon-dispatch-intent.v0",
            "run_id": run_dir.run_id,
            "group_id": manifest.get("group_id"),
            "task": manifest.get("task"),
            "call_parameters": call_parameters,
            "task_sha256": "sha256:" + hashlib.sha256(str(manifest.get("task", "")).encode("utf-8")).hexdigest(),
            "tools": list(manifest.get("tools") or []),
            "manifest": manifest,
            "manifest_sha256": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
            "authority": "parent_owned",
        }
        return _write_sidecar_json(run_dir, "dispatch-intent.v0.json", payload)
    except BaseException:
        return None


def write_dispatch_result_receipt(run_dir, result: dict) -> str | None:
    """Best-effort parent-owned immediate dispatch result receipt."""
    try:
        try:
            state = run_dir.read_state_from_disk(run_dir.path)
        except BaseException:
            state = {}
        if state.get("return_observer_enabled") is not True:
            manifest_path = Path(run_dir.path) / "supervisor_manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except BaseException:
                manifest = {}
            if manifest.get("return_observer_enabled") is not True:
                return None
        data = {
            "status": result.get("status"),
            "count": result.get("count"),
            "ids": result.get("ids"),
            "group_id": result.get("group_id"),
        }
        payload = {
            "schema_version": "lingtai.daemon-dispatch-result.v0",
            "run_id": run_dir.run_id,
            "dispatch_result_sha256": "sha256:" + hashlib.sha256(
                json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "dispatch_result": data,
            "authority": "parent_owned",
        }
        return _write_sidecar_json(run_dir, "dispatch-result.v0.json", payload)
    except BaseException:
        return None
