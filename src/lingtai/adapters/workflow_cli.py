"""Workflow worker-batch adapter over the public standalone daemon CLI only."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from lingtai.kernel._fsutil import atomic_write_json
from lingtai.tools.workflow.core import WorkflowError


class DaemonCliBatches:
    def __init__(self, cwd: Path):
        self.cwd = Path(cwd)

    def _call(self, root: Path, *arguments: str) -> dict:
        try:
            process = subprocess.run(
                [sys.executable, "-m", "lingtai", "daemon", *arguments,
                 "--state-root", str(root)], cwd=self.cwd,
                capture_output=True, text=True, encoding="utf-8", timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkflowError(f"daemon CLI outcome uncertain: {type(exc).__name__}; do not replay") from exc
        try:
            result = json.loads(process.stdout)
        except ValueError as exc:
            raise WorkflowError("daemon CLI returned malformed JSON; do not replay") from exc
        if not isinstance(result, dict) or process.returncode != 0 or result.get("status") == "error":
            raise WorkflowError(f"daemon CLI refused or outcome uncertain (exit {process.returncode}); do not replay",
                                receipt=result if isinstance(result, dict) else None)
        return result

    def _stage_task_files(self, root: Path, payload: Path) -> Path:
        """Bridge host-contained inputs only; CLI still validates/snapshots tasks.

        Original stage/plan JSON stays untouched. Malformed collections go to
        canonical CLI validation without copying (notably its 50-file limit).
        """
        batch = json.loads(payload.read_text(encoding="utf-8"))
        tasks = batch.get("tasks", [])
        for task in tasks:
            files = task.get("task_files")
            if files is not None and (
                not isinstance(files, list) or len(files) > 50
                or any(not isinstance(item, dict)
                       or not isinstance(item.get("path"), str)
                       or not item["path"].strip() for item in files)
            ):
                return payload
        sources = []
        host = self.cwd.resolve()
        store = root / "workflow-inputs"
        for task_index, task in enumerate(tasks):
            for file_index, item in enumerate(task.get("task_files") or []):
                original = item["path"]
                source = Path(original.strip()).expanduser()
                if not source.is_absolute():
                    source = host / source
                source = source.resolve()
                if not source.is_relative_to(host):
                    raise WorkflowError(f"task file path is outside the host working directory: {original}")
                if not source.is_file():
                    raise WorkflowError(f"task file path does not resolve to a file: {original}")
                # Read at most the daemon cap plus one, even for growing files.
                with source.open("rb") as stream:
                    data = stream.read(1_000_001)
                if len(data) > 1_000_000:
                    raise WorkflowError(f"task file {original!r} exceeds the 1000000-byte limit")
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WorkflowError(f"task file {original!r} is not valid UTF-8 text") from exc
                if b"\x00" in data:
                    raise WorkflowError(f"task file {original!r} contains NUL byte")
                digest = hashlib.sha256(data).hexdigest()
                blob = store / digest
                if not blob.exists():
                    store.mkdir(parents=True, exist_ok=True)
                    # Binary writes preserve exact bytes on Windows as well.
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=store, delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(data)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, blob)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                item["path"] = str(blob.resolve())
                sources.append(dict(task_index=task_index, file_index=file_index,
                                    path=original, resolved=str(source), staged=item["path"],
                                    sha256=digest, size=len(data)))
        if not sources:
            return payload
        # Content-addressed derived payload + source map, never mutable originals.
        digest = hashlib.sha256(json.dumps([str(payload), batch, sources], sort_keys=True).encode("utf-8")).hexdigest()
        derived = root / "workflow-inputs" / f"batch-{digest}.json"
        mapping = derived.with_suffix(".sources.json")
        if not mapping.exists():
            atomic_write_json(mapping, dict(source_payload=str(payload), files=sources), fsync=True)
        if not derived.exists():
            atomic_write_json(derived, batch, fsync=True)
        return derived

    def dispatch(self, root: Path, payload: Path, count: int) -> dict:
        payload = self._stage_task_files(root, payload)
        result = self._call(root, "emanate", "--tasks", str(payload))
        ids = result.get("ids")
        if (result.get("status") != "dispatched" or result.get("count") != count
                or not isinstance(ids, list) or len(ids) != count
                or any(not isinstance(i, str) or not i for i in ids)
                or len(set(ids)) != count):
            raise WorkflowError("ambiguous daemon acceptance; inspect isolated root; do not replay", receipt=result)
        return result

    def inspect(self, root: Path, worker_id: str) -> dict:
        result = self._call(root, "check", worker_id)
        if (result.get("id") != worker_id or not result.get("run_id")
                or result.get("state") not in {"running", "active", "done", "failed", "cancelled", "timeout"}):
            raise WorkflowError("ambiguous daemon check result; no continuation", receipt=result)
        return result

    def cancel(self, root: Path) -> dict:
        result = self._call(root, "reclaim")
        if result.get("status") != "reclaimed":
            raise WorkflowError("daemon reclaim not confirmed; no direct-kill fallback", receipt=result)
        return result
