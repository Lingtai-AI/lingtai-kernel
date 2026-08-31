"""Psyche's closed prompt-owner document and read-only SHOW provider."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..tool_family import SettingRow, SettingsProvider

if TYPE_CHECKING:
    from lingtai.kernel.tool_plugin import PsycheSettingsPort

__all__ = [
    "PSYCHE_SETTINGS_RELATIVE_PATH",
    "PsychePromptInputs",
    "PsycheSettingsError",
    "PsycheSettingsSnapshot",
    "build_settings_provider",
    "read_prompt_owner_values",
    "read_resolved_prompt_inputs",
]


PSYCHE_SETTINGS_RELATIVE_PATH = Path("settings") / "psyche.json"
_SCHEMA_VERSION = 1
_MAX_SETTINGS_BYTES = 64 * 1024
_PROMPT_FIELDS = ("base_prompt", "covenant", "comment")
_OWNER_VALUE_KEYS = tuple(
    field for name in _PROMPT_FIELDS for field in (name, f"{name}_file")
)
_OWNER_KEYS = frozenset(("schema_version", *_OWNER_VALUE_KEYS))


class PsycheSettingsError(RuntimeError):
    """A closed owner-document read/parse/validation failure."""


@dataclass(frozen=True, slots=True)
class PsychePromptInputs:
    """One resolved Psyche owner read, before mirror fallback composition."""

    base_prompt: str = ""
    base_prompt_file: str | None = None
    covenant: str = ""
    covenant_file: str | None = None
    comment: str = ""
    comment_file: str | None = None


@dataclass(frozen=True, slots=True)
class PsycheSettingsSnapshot:
    """Last completely applied Pad + Psyche prompt-owner inputs for SHOW."""

    pad: str = ""
    pad_file: str | None = None
    base_prompt: str = ""
    base_prompt_file: str | None = None
    covenant: str = ""
    covenant_file: str | None = None
    comment: str = ""
    comment_file: str | None = None


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _document_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _stable_document_bytes(path: Path) -> bytes | None:
    """Read one bounded regular owner file through an identity-bound descriptor."""
    try:
        path_before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PsycheSettingsError("Psyche settings could not be inspected") from exc

    if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
        raise PsycheSettingsError("Psyche settings must be a regular non-symlink file")
    if path_before.st_size > _MAX_SETTINGS_BYTES:
        raise PsycheSettingsError("Psyche settings exceeds the 64 KiB limit")

    flags = os.O_RDONLY
    for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag_name, 0)

    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode):
            raise PsycheSettingsError("Psyche settings must be a regular non-symlink file")
        if (
            opened_before.st_size > _MAX_SETTINGS_BYTES
            or _document_identity(opened_before) != _document_identity(path_before)
        ):
            raise PsycheSettingsError("Psyche settings changed while being read")

        chunks: list[bytes] = []
        remaining = _MAX_SETTINGS_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        opened_after = os.fstat(descriptor)
        path_after = path.lstat()
    except PsycheSettingsError:
        raise
    except OSError as exc:
        raise PsycheSettingsError("Psyche settings could not be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    if len(raw) > _MAX_SETTINGS_BYTES:
        raise PsycheSettingsError("Psyche settings exceeds the 64 KiB limit")
    if (
        not stat.S_ISREG(opened_after.st_mode)
        or stat.S_ISLNK(path_after.st_mode)
        or not stat.S_ISREG(path_after.st_mode)
        or len(raw) != opened_before.st_size
        or _document_identity(opened_before) != _document_identity(opened_after)
        or _document_identity(path_before) != _document_identity(path_after)
    ):
        raise PsycheSettingsError("Psyche settings changed while being read")
    return raw


def _read_owner_values(working_dir: str | Path) -> dict[str, str]:
    """Return strict v1 owner values, resolving pointer paths against workdir."""
    root = Path(working_dir)
    raw = _stable_document_bytes(root / PSYCHE_SETTINGS_RELATIVE_PATH)
    if raw is None:
        return {}
    try:
        text = raw.decode("utf-8")
        data = json.loads(text, object_pairs_hook=_closed_object)
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PsycheSettingsError("Psyche settings must be valid UTF-8 JSON") from exc

    if not isinstance(data, dict):
        raise PsycheSettingsError("Psyche settings top level must be an object")
    if set(data) - _OWNER_KEYS:
        raise PsycheSettingsError("Psyche settings contains an unknown field")
    if "schema_version" not in data:
        raise PsycheSettingsError("Psyche settings requires schema_version")
    if type(data["schema_version"]) is not int or data["schema_version"] != _SCHEMA_VERSION:
        raise PsycheSettingsError("Psyche settings schema_version must be integer 1")

    values: dict[str, str] = {}
    for key in _OWNER_VALUE_KEYS:
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, str):
            raise PsycheSettingsError(f"Psyche settings {key} must be a string")
        if key.endswith("_file") and value:
            try:
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = root / path
            except (OSError, RuntimeError) as exc:
                raise PsycheSettingsError("Psyche settings pointer could not be resolved") from exc
            values[key] = str(path)
        else:
            values[key] = value
    return values


def read_prompt_owner_values(working_dir: str | Path) -> dict[str, str]:
    """Read the strict owner document without resolving pointer contents.

    Avatar creation uses this narrowly to carry only the base-prompt and
    covenant owner inputs into a child document. Returned file pointers are
    already anchored to the parent workdir, preserving their meaning from the
    child's different workdir without importing any unrelated settings owner.
    """
    return _read_owner_values(working_dir)


def read_resolved_prompt_inputs(working_dir: str | Path) -> PsychePromptInputs:
    """Read Psyche's owner document once and apply existing file precedence."""
    from lingtai.kernel.config_resolve import resolve_file

    values = _read_owner_values(working_dir)
    resolved: dict[str, str | None] = {}
    for key in _PROMPT_FIELDS:
        file_key = f"{key}_file"
        pointer = values.get(file_key)
        inline = values.get(key, "")
        # Keep the existing prompt helper's missing-file, UTF-8 and OSError
        # behavior. The owner document supplies the only active pair.
        value = resolve_file(inline, pointer) if pointer is not None else inline
        if value is not None and not isinstance(value, str):
            raise PsycheSettingsError(f"Psyche settings {key} resolved unexpectedly")
        resolved[key] = value or ""
        resolved[file_key] = pointer
    return PsychePromptInputs(**resolved)


def build_settings_provider(settings: "PsycheSettingsPort") -> SettingsProvider:
    """Bind Psyche SHOW to the host's last applied owner configuration."""

    def provide() -> list[SettingRow]:
        snapshot = settings.read_snapshot()
        if not isinstance(snapshot, PsycheSettingsSnapshot):
            raise RuntimeError("Psyche configuration snapshot is unavailable")
        return [
            SettingRow(
                "pad",
                snapshot.pad,
                "",
                True,
                "psyche-manual#setting-pad",
                _sensitive=True,
            ),
            SettingRow(
                "pad_file",
                snapshot.pad_file,
                None,
                True,
                "psyche-manual#setting-pad-file",
                _sensitive=True,
            ),
            SettingRow(
                "base_prompt",
                snapshot.base_prompt,
                "",
                True,
                "psyche-manual#setting-base-prompt",
                _sensitive=True,
            ),
            SettingRow(
                "base_prompt_file",
                snapshot.base_prompt_file,
                None,
                True,
                "psyche-manual#setting-base-prompt-file",
                _sensitive=True,
            ),
            SettingRow(
                "covenant",
                snapshot.covenant,
                "",
                True,
                "psyche-manual#setting-covenant",
                _sensitive=True,
            ),
            SettingRow(
                "covenant_file",
                snapshot.covenant_file,
                None,
                True,
                "psyche-manual#setting-covenant-file",
                _sensitive=True,
            ),
            SettingRow(
                "comment",
                snapshot.comment,
                "",
                True,
                "psyche-manual#setting-comment",
                _sensitive=True,
            ),
            SettingRow(
                "comment_file",
                snapshot.comment_file,
                None,
                True,
                "psyche-manual#setting-comment-file",
                _sensitive=True,
            ),
        ]

    return provide
