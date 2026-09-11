"""Filesystem adapters for fresh creation and read-only Project inspection."""
from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

from lingtai.kernel.project import (
    ProjectCreationError,
    ProjectError,
    ProjectInspectionError,
    ProjectInspectionObservation,
    ProjectInspectionPort,
    ProjectSeed,
    ProjectWorkspacePort,
)
from lingtai.kernel.workdir import workdir_layout

StageValidator = Callable[[Path], None]
_MAILBOXES = ("inbox", "outbox", "sent", "archive", "schedules")
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_INVALID_PROJECT_ROOT = ProjectError(
    "invalid_project_root",
    "project root must be an existing readable directory",
)
_INSPECTION_FAILED = ProjectError(
    "project_inspect_failed",
    "project structure could not be inspected",
)
_UNSAFE_PROJECT_STRUCTURE = ProjectError(
    "unsafe_project_structure",
    "project structure contains an unsafe filesystem entry",
)


class ProjectWorkspaceError(ProjectCreationError):
    pass


def _error(code: str, message: str) -> ProjectWorkspaceError:
    return ProjectWorkspaceError(ProjectError(code, message))


def _inspection_error(error: ProjectError) -> ProjectInspectionError:
    return ProjectInspectionError(error)


def _is_reparse_point(metadata: os.stat_result) -> bool:
    """Recognize Windows reparse metadata without requiring newer pathlib APIs."""
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


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


class FilesystemProjectInspectionAdapter(ProjectInspectionPort):
    """Observe direct Project workdir markers without reading or changing them."""

    def __init__(self, root: Path) -> None:
        self._root = root

    @staticmethod
    def _marker_present(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise _inspection_error(_INSPECTION_FAILED) from exc
        if _is_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise _inspection_error(_UNSAFE_PROJECT_STRUCTURE)
        return True

    @staticmethod
    def _require_caller_root(root: Path) -> None:
        try:
            metadata = root.lstat()
        except (OSError, ValueError) as exc:
            raise _inspection_error(_INVALID_PROJECT_ROOT) from exc
        if _is_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise _inspection_error(_INVALID_PROJECT_ROOT)
        try:
            with os.scandir(root) as stream:
                next(stream, None)
        except (OSError, ValueError) as exc:
            raise _inspection_error(_INVALID_PROJECT_ROOT) from exc

    def inspect(self) -> ProjectInspectionObservation:
        self._require_caller_root(self._root)
        target = self._root / ".lingtai"
        try:
            target_metadata = target.lstat()
        except FileNotFoundError:
            # Distinguish a missing final target from a caller root that raced
            # away or became invalid after the first check.
            self._require_caller_root(self._root)
            return ProjectInspectionObservation(False, 0, 0, 0)
        except OSError as exc:
            raise _inspection_error(_INSPECTION_FAILED) from exc
        if _is_reparse_point(target_metadata) or not stat.S_ISDIR(target_metadata.st_mode):
            raise _inspection_error(_UNSAFE_PROJECT_STRUCTURE)

        total = 0
        with_init = 0
        try:
            with os.scandir(target) as entries:
                for entry in entries:
                    metadata = entry.stat(follow_symlinks=False)
                    if _is_reparse_point(metadata) or not (
                        stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
                    ):
                        raise _inspection_error(_UNSAFE_PROJECT_STRUCTURE)
                    if entry.name == "human":
                        if not stat.S_ISDIR(metadata.st_mode):
                            raise _inspection_error(_UNSAFE_PROJECT_STRUCTURE)
                        continue
                    if not stat.S_ISDIR(metadata.st_mode):
                        continue

                    layout = workdir_layout(Path(entry.path))
                    has_manifest = self._marker_present(layout.agent_manifest)
                    has_init = self._marker_present(layout.init_json)
                    if not (has_manifest or has_init):
                        continue
                    total += 1
                    if has_init:
                        with_init += 1
        except OSError as exc:
            raise _inspection_error(_INSPECTION_FAILED) from exc

        return ProjectInspectionObservation(
            lingtai_root_present=True,
            agent_candidates=total,
            agents_with_init=with_init,
            agents_without_init=total - with_init,
        )


__all__ = [
    "FilesystemProjectInspectionAdapter",
    "FilesystemProjectWorkspaceAdapter",
    "ProjectWorkspaceError",
    "StageValidator",
]
