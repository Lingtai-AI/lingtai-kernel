"""Ordered workflow policy. The worker boundary is an injected batch port."""
from __future__ import annotations

import copy
import json
import math
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Protocol

from lingtai.kernel._fsutil import atomic_write_json


class WorkerBatches(Protocol):
    """One acceptance per batch; inspection is not acceptance or completion."""

    def dispatch(self, root: Path, payload: Path, count: int) -> dict: ...
    def inspect(self, root: Path, worker_id: str) -> dict: ...
    def cancel(self, root: Path) -> dict: ...


class WorkflowError(ValueError):
    def __init__(self, message: str, *, receipt: dict | None = None):
        super().__init__(message)
        self.receipt = receipt


def load_plan(path: Path) -> dict:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise WorkflowError("workflow file exceeds 4 MiB")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or set(plan) - {"stages", "auto_continue"}:
        raise WorkflowError("workflow requires stages and optional auto_continue only")
    if type(plan.get("auto_continue", False)) is not bool:
        raise WorkflowError("auto_continue must be boolean")
    stages = plan.get("stages")
    if not isinstance(stages, list) or not stages:
        raise WorkflowError("stages must be a nonempty ordered array")
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) - {
            "tasks", "backend", "max_turns", "timeout", "pause_after"
        }:
            raise WorkflowError("stage permits only daemon batch fields and pause_after")
        tasks = stage.get("tasks")
        if not isinstance(tasks, list) or not tasks or any(not isinstance(t, dict) for t in tasks):
            raise WorkflowError("each stage needs nonempty daemon task objects")
        if type(stage.get("pause_after", False)) is not bool:
            raise WorkflowError("pause_after must be boolean")
        if "backend" in stage and (not isinstance(stage["backend"], str) or not stage["backend"]):
            raise WorkflowError("backend must be a nonempty string")
        if "max_turns" in stage and (type(stage["max_turns"]) is not int or stage["max_turns"] < 1):
            raise WorkflowError("max_turns must be a positive integer")
        if "timeout" in stage and (type(stage["timeout"]) not in (int, float)
                or not math.isfinite(stage["timeout"]) or stage["timeout"] <= 0):
            raise WorkflowError("timeout must be finite positive seconds")
    return plan


class _Run:
    def __init__(self, state: dict):
        self.state = state
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


class WorkflowManager:
    """One monitor per run, retained across in-process refresh; no replay engine."""

    def __init__(self, workdir: Path, batches: WorkerBatches, notifications: Any,
                 shutdown: Any, *, poll_seconds: float = 1.0):
        self.workdir = Path(workdir)
        self.directory = self.workdir / "workflow"
        self.batches = batches
        self.notifications = notifications
        self.shutdown = shutdown
        self.poll_seconds = poll_seconds
        self._runs: dict[str, _Run] = {}
        self._lock = threading.RLock()

    def rebind(self, notifications: Any, shutdown: Any) -> None:
        self.notifications, self.shutdown = notifications, shutdown

    def _path(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not re.fullmatch(r"wf-[0-9a-f]{32}", run_id):
            raise WorkflowError("invalid workflow run_id")
        return self.directory / run_id

    def _save(self, run: _Run) -> None:
        atomic_write_json(self._path(run.state["run_id"]) / "state.json", run.state, fsync=True)

    def _get(self, run_id: str) -> _Run:
        with self._lock:
            if run_id not in self._runs:
                state = json.loads((self._path(run_id) / "state.json").read_text(encoding="utf-8"))
                run = _Run(state)
                self._runs[run_id] = run
                if state["status"] in {"running", "dispatching", "stopping"}:
                    state.update(status="uncertain", error="monitor lost; inspect daemon refs; no automatic replay")
                    self._save(run)
                    self._notify(run)
            return self._runs[run_id]

    def run(self, file: str, run_id: str | None = None) -> dict:
        path = Path(file).expanduser()
        if not path.is_absolute():
            path = self.workdir / path
        plan = load_plan(path)
        if self.shutdown.is_set():
            raise WorkflowError("host is shutting down")
        if run_id is None:
            run_id = "wf-" + uuid.uuid4().hex
            folder = self._path(run_id)
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "daemon").mkdir()
            run = _Run(dict(run_id=run_id, status="paused", stage_index=0,
                            plan=plan, submissions=[], history=[], error=None))
            with self._lock:
                self._runs[run_id] = run
        else:
            run = self._get(run_id)
        with run.lock:
            state = run.state
            if run.stop.is_set():
                raise WorkflowError("stop intent pending; stopped runs cannot continue")
            if state["status"] != "paused":
                raise WorkflowError("run_id continuation requires a paused boundary; never retries work")
            index = state["stage_index"]
            if plan["stages"][:index] != state["plan"]["stages"][:index]:
                raise WorkflowError("completed stage prefix is immutable")
            if len(plan["stages"]) <= index:
                raise WorkflowError("continuation must include a remaining stage")
            revision = len(state["submissions"])
            snapshot = self._path(run_id) / f"plan-{revision}.json"
            atomic_write_json(snapshot, plan, fsync=True)
            state["submissions"].append(str(snapshot))
            state["plan"] = plan
            state["error"] = None
            state["status"] = "running"
            self._save(run)
            run.thread = threading.Thread(target=self._monitor, args=(run,), daemon=True,
                                          name=f"workflow-{run_id}")
            run.thread.start()
            return copy.deepcopy(state)

    def check(self, run_id: str) -> dict:
        run = self._get(run_id)
        with run.lock:
            return copy.deepcopy(run.state)

    def stop(self, run_id: str) -> dict:
        run = self._get(run_id)
        # Intent precedes waiting for an in-flight CLI call/transition lock.
        run.stop.set()
        with run.lock:
            if run.state["status"] in {"done", "stopped"}:
                return copy.deepcopy(run.state)
            run.state["status"] = "stopping"
            self._save(run)
            try:
                receipt = self.batches.cancel(self._path(run_id) / "daemon")
                run.state.update(status="stopped", stop_receipt=receipt)
            except Exception as exc:
                run.state.update(status="stop_failed", error=str(exc))
                if getattr(exc, "receipt", None) is not None:
                    run.state["error_receipt"] = exc.receipt
            self._save(run)
            self._notify(run)
            return copy.deepcopy(run.state)

    def _notify(self, run: _Run) -> None:
        state = run.state
        ref = f"{state['run_id']}:{state['stage_index']}:{state['status']}"
        try:
            published = self.notifications.publish_system(
                source="workflow", ref_id=ref,
                body=f"Workflow {state['run_id']} {state['status']} at stage {state['stage_index']}; use workflow.check for durable refs.",
                skip_if_ref_id_exists=True)
            state["notification_error"] = None if published else "publication not confirmed"
        except Exception as exc:
            state["notification_error"] = str(exc)
        self._save(run)

    def _monitor(self, run: _Run) -> None:
        try:
            while True:
                with run.lock:
                    state = run.state
                    if run.stop.is_set() or state["status"] not in {"running", "dispatching"}:
                        return
                    if self.shutdown.is_set():
                        raise WorkflowError("host shutdown; worker state uncertain; no continuation")
                    index = state["stage_index"]
                    stage = state["plan"]["stages"][index]
                    if len(state["history"]) == index:
                        payload = {k: v for k, v in stage.items() if k != "pause_after"}
                        batch_file = self._path(state["run_id"]) / f"stage-{index}.json"
                        atomic_write_json(batch_file, payload, fsync=True)
                        record = dict(stage_index=index, ids=[], results={}, acceptance=None)
                        state["history"].append(record)
                        state["status"] = "dispatching"
                        self._save(run)  # intent durable BEFORE any external effects
                        if run.stop.is_set():
                            return  # stop won during snapshot/intent persistence
                        acceptance = self.batches.dispatch(self._path(state["run_id"]) / "daemon",
                                                           batch_file, len(stage["tasks"]))
                        record.update(ids=acceptance["ids"], acceptance=acceptance)
                        state["status"] = "running"
                        self._save(run)
                    record = state["history"][index]
                    for worker_id in record["ids"]:
                        record["results"][worker_id] = self.batches.inspect(
                            self._path(state["run_id"]) / "daemon", worker_id)
                    self._save(run)
                    results = list(record["results"].values())
                    if any(r["state"] in {"failed", "cancelled", "timeout"} for r in results):
                        state.update(status="failed", error="daemon stage failed; siblings may still be active; stop to reclaim")
                    elif results and all(r["state"] == "done" for r in results):
                        state["stage_index"] += 1
                        if state["stage_index"] == len(state["plan"]["stages"]):
                            state["status"] = "done"
                        elif stage.get("pause_after", False) or not state["plan"].get("auto_continue", False):
                            state["status"] = "paused"
                    if state["status"] not in {"running", "dispatching"}:
                        self._save(run)
                        self._notify(run)
                        return
                if run.stop.wait(self.poll_seconds):
                    return
        except Exception as exc:
            with run.lock:
                if run.state["status"] in {"stopped", "stop_failed"}:
                    return
                run.state.update(status="uncertain", error=str(exc))
                if getattr(exc, "receipt", None) is not None:
                    run.state["error_receipt"] = exc.receipt
                self._save(run)
                self._notify(run)
