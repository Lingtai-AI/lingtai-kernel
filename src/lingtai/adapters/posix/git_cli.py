"""Fixed-command POSIX Git CLI adapter for snapshot and revision Ports."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lingtai.kernel.snapshot import SnapshotPort, SourceRevisionPort

_GITIGNORE = (
    "# Secrets — MCP addon credentials (bot tokens, API keys)\n"
    ".secrets/\n"
    "\n"
    "# Transient lifecycle signal files\n"
    ".sleep\n"
    ".suspend\n"
    ".agent.heartbeat\n"
    ".timemachine.pid\n"
)


class PosixGitCliAdapter(SnapshotPort, SourceRevisionPort):
    """One-directory implementation of both narrow Core-owned Ports.

    Every public operation maps to a fixed Git command family.  No arbitrary
    command runner, argv, subprocess value, or Git-specific result crosses the
    boundary.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def _ensure_system_files(self) -> None:
        system_dir = self._directory / "system"
        system_dir.mkdir(exist_ok=True)
        for name in ("covenant.md", "principle.md", "pad.md"):
            path = system_dir / name
            if not path.is_file():
                path.write_text("")

    def initialize(self) -> None:
        if (self._directory / ".git").is_dir():
            return
        (self._directory / ".gitignore").write_text(_GITIGNORE)
        self._ensure_system_files()
        try:
            subprocess.run(
                ["git", "init"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "agent@lingtai"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "灵台 Agent"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "add", ".gitignore", "system/"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "init: agent working directory"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            self._ensure_system_files()

    def snapshot(self) -> str | None:
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
            status = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self._directory,
                capture_output=True,
            )
            if status.returncode == 0:
                return None
            if status.returncode != 1:
                return None
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            subprocess.run(
                ["git", "commit", "-m", f"snapshot {timestamp}"],
                cwd=self._directory,
                capture_output=True,
                check=True,
            )
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self._directory,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return None
            return result.stdout.strip() or None
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

    def collect_garbage(self) -> None:
        try:
            subprocess.run(
                ["git", "gc", "--auto"],
                cwd=self._directory,
                capture_output=True,
                timeout=60,
            )
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            pass

    def current_revision(
        self, short_length: int | None, timeout_seconds: float
    ) -> str | None:
        short_option = "--short" if short_length is None else f"--short={short_length}"
        try:
            result = subprocess.run(
                ["git", "rev-parse", short_option, "HEAD"],
                cwd=self._directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def is_dirty(self, timeout_seconds: float) -> bool | None:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=self._directory,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return bool(result.stdout.strip())


__all__ = ["PosixGitCliAdapter"]
