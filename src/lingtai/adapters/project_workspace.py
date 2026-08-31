"""Filesystem adapter for one fresh local Project seed."""
from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from lingtai.kernel.project import (
    ProjectCreationError,
    ProjectError,
    ProjectSeed,
    ProjectWorkspacePort,
)

StageValidator = Callable[[Path], None]
_MAILBOXES = ("inbox", "outbox", "sent", "archive", "schedules")


class ProjectWorkspaceError(ProjectCreationError):
    pass


def _error(code: str, message: str) -> ProjectWorkspaceError:
    return ProjectWorkspaceError(ProjectError(code, message))


class FilesystemProjectWorkspaceAdapter(ProjectWorkspacePort):
    """Create one `.lingtai` tree below the caller-selected existing root."""

    def __init__(self, root: Path, *, validate_agent: StageValidator) -> None:
        self._root = root
        self._validate_agent = validate_agent

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8", newline="\n")

    def _write_seed(self, target: Path, seed: ProjectSeed) -> Path:
        human = target / "human"
        agent = target / seed.agent_name
        for directory in (human, agent):
            for mailbox in _MAILBOXES:
                (directory / "mailbox" / mailbox).mkdir(parents=True)
        self._write(human / ".agent.json", seed.human_manifest_json)
        self._write(agent / ".agent.json", seed.agent_manifest_json)
        self._write(agent / "init.json", seed.init_json)
        (agent / "settings").mkdir()
        self._write(agent / "settings" / "psyche.json", seed.psyche_settings_json)
        return agent

    def create(self, seed: ProjectSeed) -> None:
        if not self._root.is_dir():
            raise _error("invalid_project_root", "project root must be an existing directory")
        target = self._root / ".lingtai"
        try:
            target.mkdir()
        except FileExistsError as exc:
            raise _error("already_initialized", "project already contains a .lingtai directory") from exc
        except OSError as exc:
            raise _error("project_create_failed", "project target could not be created") from exc

        completed = False
        try:
            try:
                agent_dir = self._write_seed(target, seed)
            except OSError as exc:
                raise _error("project_create_failed", "project seed could not be written") from exc
            try:
                self._validate_agent(agent_dir)
            except ProjectCreationError:
                raise
            except Exception as exc:
                raise _error("init_preflight_failed", "generated init could not be read") from exc
            completed = True
        finally:
            if not completed:
                # `target` was created by this call, so cleanup never touches an
                # existing Project tree.
                shutil.rmtree(target, ignore_errors=True)


__all__ = ["FilesystemProjectWorkspaceAdapter", "ProjectWorkspaceError", "StageValidator"]
