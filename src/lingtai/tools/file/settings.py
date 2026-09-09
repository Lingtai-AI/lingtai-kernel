"""Source-backed SHOW rows for the public File family."""
from __future__ import annotations

from typing import TYPE_CHECKING

from lingtai.services.file_io import (
    DEFAULT_EXCLUDED_DIRS,
    DEFAULT_GLOB_MAX_RESULTS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_VISITED,
    DEFAULT_WALLTIME_S,
)
from lingtai.services.file_io_sidecar import (
    DEFAULT_SIDECAR_TIMEOUT_SECONDS,
    SIDECAR_ENV_VARS,
    FileIOConstructionSnapshot,
)
from lingtai.tools.tool_family import SettingRow

from ._grep import DEFAULT_GREP_MAX_MATCHES
from ._read import (
    DEFAULT_READ_CAP_CHARS,
    DEFAULT_READ_LINE_LIMIT,
    READ_HARD_CAP_CHARS,
    _runtime_hard_cap,
)

if TYPE_CHECKING:
    from lingtai.kernel.tool_plugin import FileIOPort


READ_DEFAULT_LINE_LIMIT = "read.default_line_limit"
READ_DEFAULT_MAX_CHARS = "read.default_max_chars"
READ_RUNTIME_MAX_CHARS = "read.runtime_max_chars"
GLOB_MAX_RESULTS = "glob.max_results"
GREP_DEFAULT_MAX_MATCHES = "grep.default_max_matches"
GREP_MAX_FILE_BYTES = "grep.max_file_bytes"
SEARCH_MAX_VISITED = "search.max_visited"
SEARCH_WALLTIME_SECONDS = "search.walltime_seconds"
SEARCH_EXCLUDED_DIRECTORIES = "search.excluded_directories"
SEARCH_SIDECAR_TIMEOUT_SECONDS = "search.sidecar_timeout_seconds"
TEXT_ENCODING = "text.encoding"
BACKEND_MODE = "backend.mode"
BACKEND_SIDECAR = "backend.sidecar"

FILE_IO_CONSTRUCTION_SNAPSHOT_KEY = "file_io_construction_snapshot"
TEXT_ENCODING_VALUE = "utf-8"
_VALID_BACKEND_MODES = frozenset({"auto", "rust", "python"})
_VALID_SIDECAR_SOURCES = frozenset(SIDECAR_ENV_VARS)


class FileSettingsProvider:
    """Project File's live limits and applied construction snapshot."""

    def __init__(
        self,
        file_io: FileIOPort | None,
        snapshot: FileIOConstructionSnapshot | None,
    ) -> None:
        self._file_io = file_io
        self._snapshot = snapshot

    def __call__(self) -> tuple[SettingRow, ...]:
        snapshot = self._snapshot
        if (
            self._file_io is None
            or not isinstance(snapshot, FileIOConstructionSnapshot)
            or snapshot.backend_mode not in _VALID_BACKEND_MODES
            or snapshot.sidecar_override_source
            not in _VALID_SIDECAR_SOURCES | {None}
            or (snapshot.sidecar_override is None)
            != (snapshot.sidecar_override_source is None)
            or (
                snapshot.sidecar_override is not None
                and (
                    not isinstance(snapshot.sidecar_override, str)
                    or not snapshot.sidecar_override
                )
            )
        ):
            raise RuntimeError("File settings current truth is unavailable")

        excluded = sorted(DEFAULT_EXCLUDED_DIRS)
        runtime_cap = _runtime_hard_cap(self._file_io)
        return (
            SettingRow(
                READ_DEFAULT_LINE_LIMIT,
                DEFAULT_READ_LINE_LIMIT,
                DEFAULT_READ_LINE_LIMIT,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                READ_DEFAULT_MAX_CHARS,
                DEFAULT_READ_CAP_CHARS,
                DEFAULT_READ_CAP_CHARS,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                READ_RUNTIME_MAX_CHARS,
                runtime_cap,
                READ_HARD_CAP_CHARS,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                GLOB_MAX_RESULTS,
                DEFAULT_GLOB_MAX_RESULTS,
                DEFAULT_GLOB_MAX_RESULTS,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                GREP_DEFAULT_MAX_MATCHES,
                DEFAULT_GREP_MAX_MATCHES,
                DEFAULT_GREP_MAX_MATCHES,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                GREP_MAX_FILE_BYTES,
                DEFAULT_MAX_FILE_BYTES,
                DEFAULT_MAX_FILE_BYTES,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                SEARCH_MAX_VISITED,
                DEFAULT_MAX_VISITED,
                DEFAULT_MAX_VISITED,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                SEARCH_WALLTIME_SECONDS,
                DEFAULT_WALLTIME_S,
                DEFAULT_WALLTIME_S,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                SEARCH_EXCLUDED_DIRECTORIES,
                list(excluded),
                list(excluded),
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                SEARCH_SIDECAR_TIMEOUT_SECONDS,
                DEFAULT_SIDECAR_TIMEOUT_SECONDS,
                DEFAULT_SIDECAR_TIMEOUT_SECONDS,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                TEXT_ENCODING,
                TEXT_ENCODING_VALUE,
                TEXT_ENCODING_VALUE,
                False,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                BACKEND_MODE,
                snapshot.backend_mode,
                "auto",
                True,
                "file-manual#settings-show-only",
            ),
            SettingRow(
                BACKEND_SIDECAR,
                snapshot.sidecar_override,
                None,
                True,
                "file-manual#settings-show-only",
                _sensitive=True,
            ),
        )


__all__ = [
    "BACKEND_MODE",
    "BACKEND_SIDECAR",
    "FILE_IO_CONSTRUCTION_SNAPSHOT_KEY",
    "FileSettingsProvider",
    "GLOB_MAX_RESULTS",
    "GREP_DEFAULT_MAX_MATCHES",
    "GREP_MAX_FILE_BYTES",
    "READ_DEFAULT_LINE_LIMIT",
    "READ_DEFAULT_MAX_CHARS",
    "READ_RUNTIME_MAX_CHARS",
    "SEARCH_EXCLUDED_DIRECTORIES",
    "SEARCH_MAX_VISITED",
    "SEARCH_SIDECAR_TIMEOUT_SECONDS",
    "SEARCH_WALLTIME_SECONDS",
    "TEXT_ENCODING",
]
