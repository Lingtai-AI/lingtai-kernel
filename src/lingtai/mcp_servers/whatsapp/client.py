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
        # stdin is written from arbitrary caller threads; two concurrent
        # requests must not interleave halves of a frame on one line.
        self._write_lock = threading.Lock()
        # _latches is mutated from the reader thread and from callers.
        self._latch_lock = threading.Lock()
        self._latches: dict[str, dict[str, Any]] = {}
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
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
            proc = self._proc
            self._reader = threading.Thread(
                target=self._read_loop, args=(proc,), name="whatsapp-bridge-reader", daemon=True,
            )
            self._reader.start()
            # Puppeteer/Chromium are prolific stderr writers. An undrained
            # PIPE fills its ~64 KiB kernel buffer and blocks the bridge
            # inside a stderr write, deadlocking the whole channel.
            self._stderr_reader = threading.Thread(
                target=self._drain_stderr, args=(proc,), name="whatsapp-bridge-stderr", daemon=True,
            )
            self._stderr_reader.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc, self._proc = self._proc, None
        self._fail_pending("bridge stopped")
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

    def _drain_stderr(self, proc: subprocess.Popen[str]) -> None:
        """Consume the bridge's stderr into the logger so it can never fill."""
        stream = proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                line = line.rstrip()
                if line:
                    log.warning("bridge stderr: %s", line[:2000])
        except Exception:  # stream closed under us during stop()
            pass

    def _fail_pending(self, error: str) -> None:
        """Release every waiter with a typed failure (bridge died)."""
        with self._latch_lock:
            pending, self._latches = self._latches, {}
        for latch in pending.values():
            latch.setdefault("error", error)
            latch["done"].set()

    def _read_loop(self, proc: subprocess.Popen[str]) -> None:
        if proc is None or proc.stdout is None:
            return
        try:
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
                if not isinstance(obj, dict):
                    log.debug("bridge: non-object frame: %.120s", line)
                    continue
                rid = obj.get("id")
                if rid is not None:
                    with self._latch_lock:
                        latch = self._latches.pop(str(rid), None)
                    if latch:
                        if "error" in obj:
                            latch["error"] = obj["error"]
                        else:
                            # ``.get(..., {})``: a falsy-but-valid result
                            # ([], 0, false) must survive.
                            latch["result"] = obj.get("result", {})
                        latch["done"].set()
                    continue
                if "error" in obj:
                    # {"id": null, "error": ...} is a protocol error the bridge
                    # could not correlate — surface it instead of routing it
                    # into the event handler as an untyped event.
                    log.warning("bridge protocol error: %s", obj.get("error"))
                    continue
                # Outbound event.
                try:
                    if self.on_event:
                        self.on_event(obj)
                except Exception:
                    log.exception("bridge on_event handler failed")
        finally:
            # EOF (or a read error) means the bridge is gone. Waiters must not
            # block for the full timeout after the process has already died.
            if not self._stop.is_set():
                self._fail_pending("bridge exited")

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
        self.start()
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise BridgeError("bridge not running")
        rid = str(uuid4())
        latch: dict[str, Any] = {"done": threading.Event()}
        with self._latch_lock:
            self._latches[rid] = latch
        payload = {"id": rid, "method": method, "params": params or {}}
        try:
            with self._write_lock:
                proc.stdin.write(json.dumps(payload) + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            with self._latch_lock:
                self._latches.pop(rid, None)
            raise BridgeError(f"bridge write failed ({method}); is the bridge installed/running?") from e
        if not latch["done"].wait(timeout):
            with self._latch_lock:
                # The reader may have landed the response between the wait()
                # returning False and this pop; prefer the real response.
                self._latches.pop(rid, None)
            if not latch["done"].is_set():
                raise BridgeError(f"bridge request timed out: {method}")
        if "error" in latch:
            raise BridgeError(latch["error"])
        return latch.get("result", {})
