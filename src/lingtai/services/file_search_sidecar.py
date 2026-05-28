"""Experimental adapter for an optional Rust file-search sidecar.

This module is intentionally not wired into ``LocalFileIOService`` by default.
It exists to prove that LingTai's Python runtime can call a native backend while
keeping the model-facing tool contract and Python fallback untouched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingtai.services.file_io import GrepMatch


class FileSearchSidecarError(RuntimeError):
    """Raised when the experimental sidecar cannot satisfy a request."""


@dataclass(frozen=True)
class SidecarGrepRequest:
    """Small JSON request understood by the PoC Rust sidecar."""

    root: str
    path: str
    pattern: str
    max_results: int = 50

    def to_payload(self) -> dict[str, Any]:
        return {
            "op": "grep",
            "root": self.root,
            "path": self.path,
            "pattern": self.pattern,
            "max_results": self.max_results,
        }


class RustFileSearchSidecar:
    """Thin subprocess wrapper around the optional Rust sidecar binary.

    The binary is discovered from ``binary_path`` or the
    ``LINGTAI_SEARCH_SIDECAR`` environment variable. No production code uses
    this adapter unless a caller opts in explicitly.
    """

    def __init__(self, binary_path: str | None = None, *, timeout_s: float = 5.0) -> None:
        self.binary_path = binary_path or os.environ.get("LINGTAI_SEARCH_SIDECAR")
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self._resolve_binary())

    def grep(self, request: SidecarGrepRequest) -> list[GrepMatch]:
        binary = self._resolve_binary()
        if not binary:
            raise FileSearchSidecarError(
                "Rust file-search sidecar is not configured; set LINGTAI_SEARCH_SIDECAR"
            )
        completed = subprocess.run(
            [binary],
            input=json.dumps(request.to_payload()),
            text=True,
            capture_output=True,
            timeout=self.timeout_s,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            raise FileSearchSidecarError(f"sidecar failed: {detail}")
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise FileSearchSidecarError("sidecar returned invalid JSON") from exc
        if not envelope.get("ok"):
            error = envelope.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise FileSearchSidecarError(message or "sidecar returned an error")
        matches: list[GrepMatch] = []
        for item in envelope.get("matches", []):
            matches.append(
                GrepMatch(
                    path=str(item["path"]),
                    line_number=int(item["line_number"]),
                    line=str(item["line"]),
                )
            )
        return matches

    def _resolve_binary(self) -> str | None:
        if not self.binary_path:
            return None
        candidate = Path(self.binary_path).expanduser()
        if candidate.is_file():
            return str(candidate)
        return shutil.which(self.binary_path)
