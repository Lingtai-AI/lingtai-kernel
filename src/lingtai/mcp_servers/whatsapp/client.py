"""Bridge process manager for the LingTai WhatsApp MCP.

Spawns the Node ``bridge/index.js`` child process and speaks the
newline-delimited JSON protocol: outbound events (``qr``, ``ready``,
``message``, ``disconnected``, ``error``) and request/response pairs
(``{id, method, params}`` -> ``{id, result}`` / ``{id, error}``).

Events are dispatched to a single ``on_event`` callback in a reader thread;
requests are dispatched synchronously with a response-latch keyed by id.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

log = logging.getLogger(__name__)

_PROTOCOL_VERSION = 1


def _default_node() -> str:
    return shutil.which("node") or "node"


def _bridge_dir() -> Path:
    # Prefer the on-disk package dir so an editable/venv install picks up the
    # bundled bridge. Fall back to this file's own location.
    here = Path(__file__).resolve().parent
    candidate = here / "bridge"
    return candidate if candidate.joinpath("index.js").is_file() else here


class BridgeError(RuntimeError):
    pass


class WhatsAppBridge:
    """Spawn/manage the Node bridge and synchronize request/response."""

    def __init__(
        self,
        *,
        node_path: str | None = None,
        bridge_dir: str | Path | None = None,
        session_dir: str | Path | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        startup_timeout: float = 15.0,
    ) -> None:
        self.node_path = node_path or _default_node()
        self.bridge_dir = Path(bridge_dir or _bridge_dir())
        self.session_dir = Path(session_dir) if session_dir else None
        self.on_event = on_event
        self.startup_timeout = startup_timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._latches: dict[str, dict[str, Any]] = {}
        self._reader: threading.Thread | None = None
        self._started_at: float | None = None
        self._stop = threading.Event()

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            index = self.bridge_dir / "index.js"
            if not index.is_file():
                raise BridgeError(f"bridge index.js not found at {index}; run npm install in {self.bridge_dir}")
            env = dict(os.environ)
            if self.session_dir is not None:
                env["LINGTAI_WHATSAPP_SESSION_DIR"] = str(self.session_dir)
            try:
                self._proc = subprocess.Popen(
                    [self.node_path, str(index)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    cwd=str(self.bridge_dir),
                )
            except FileNotFoundError as e:
                raise BridgeError(f"node executable not found ({self.node_path}); install Node.js >= 18") from e
            self._stop.clear()
            self._started_at = time.time()
            self._reader = threading.Thread(target=self._read_loop, name="whatsapp-bridge-reader", daemon=True)
            self._reader.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    # -- protocol --------------------------------------------------------

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log.debug("bridge: non-JSON line: %.120s", line)
                continue
            rid = obj.get("id")
            if rid is not None:
                latch = self._latches.pop(str(rid), None)
                if latch:
                    if "error" in obj:
                        latch["error"] = obj["error"]
                    else:
                        latch["result"] = obj.get("result") or {}
                    latch["done"].set()
                continue
            # Outbound event.
            try:
                if self.on_event:
                    self.on_event(obj)
            except Exception:
                log.exception("bridge on_event handler failed")

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        self.start()
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise BridgeError("bridge not running")
        rid = str(uuid4())
        latch: dict[str, Any] = {"done": threading.Event()}
        self._latches[rid] = latch
        payload = {"id": rid, "method": method, "params": params or {}}
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except BrokenPipeError as e:
            self._latches.pop(rid, None)
            raise BridgeError(f"bridge write failed ({method}); is the bridge installed/running?") from e
        if not latch["done"].wait(timeout):
            self._latches.pop(rid, None)
            raise BridgeError(f"bridge request timed out: {method}")
        if "error" in latch:
            raise BridgeError(latch["error"])
        return latch.get("result") or {}
