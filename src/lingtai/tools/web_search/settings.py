"""Strict per-Agent settings for the ``web`` capability.

``settings/web.search.json`` is intentionally a tiny selector, not a provider
configuration file.  Operators configure engines and credentials at setup;
the hot ``LINGTAI_WEB_ENGINE`` peer or an Agent-owned, action-owned
``settings/web.search.json`` may select only one admitted engine.

``LINGTAI_WEB_MAX_CHARS`` and the separate family-owned ``settings/web.json``
file resolve the shared ``max_chars`` inline-vs-artifact delivery threshold consumed identically by
both ``search`` and ``browse``.  It is read by neither ``manual`` nor by the
engine-selector reader above; the two files are never merged or cross-read.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..tool_family import SettingRow, SettingsProvider

MAX_SETTINGS_BYTES = 64 * 1024
_ENGINE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
CHANGE_HINT = (
    "Use web(action='settings', input={}, reasoning='inspect web settings'); "
    "engine/output changes apply on the next applicable web call; use "
    "web(action='manual', input={}, reasoning='load web guidance') for schema."
)

WEB_ENGINE_ENV = "LINGTAI_WEB_ENGINE"
WEB_MAX_CHARS_ENV = "LINGTAI_WEB_MAX_CHARS"

PROVIDER_KEY = "provider"
MODEL_KEY = "model"
API_KEY = "api_key"
ENGINES_KEY = "engines"
SEARCH_ENGINE_KEY = "search.engine"
OUTPUT_MAX_CHARS_KEY = "output.max_chars"
OPENAI_API_KEY = "credentials.openai_api_key"
ANTHROPIC_API_KEY = "credentials.anthropic_api_key"

DEFAULT_ENGINE_NAMES = ("anthropic", "duckduckgo", "openai")


def valid_engine_name(value: Any) -> bool:
    """Return whether *value* uses the one bounded engine-selector grammar."""
    return isinstance(value, str) and _ENGINE_NAME.fullmatch(value) is not None


def bounded_env_name(value: Any) -> str | None:
    """Return a safe environment-variable name, or None for unbounded input."""
    if isinstance(value, str) and _ENV_NAME.fullmatch(value):
        return value
    return None


class SettingsError(ValueError):
    """A settings snapshot was absent, unstable, malformed, or disallowed."""


@dataclass(frozen=True, slots=True)
class SettingsSnapshot:
    engine: str | None
    source: str
    revision: str
    digest: str | None
    error: str | None = None


def _working_dir_path(source: Any) -> Path:
    """Resolve a Web workdir port, retaining legacy test-source compatibility."""
    path = getattr(source, "path", None)
    if isinstance(path, Path):
        return path
    return Path(source._working_dir)


def settings_path(source: Any) -> Path:
    """Return the one fixed, action-owned settings path for ``search``."""
    return _working_dir_path(source) / "settings" / "web.search.json"


DEFAULT_OUTPUT_MAX_CHARS = 50_000
MIN_OUTPUT_MAX_CHARS = 1
MAX_OUTPUT_MAX_CHARS = 100_000


def output_settings_path(source: Any) -> Path:
    """Return the one fixed, family-owned output-delivery settings path."""
    return _working_dir_path(source) / "settings" / "web.json"


@dataclass(frozen=True, slots=True)
class OutputSettingsSnapshot:
    max_chars: int | None
    source: str
    revision: str
    digest: str | None
    error: str | None = None


def _default_output_snapshot() -> "OutputSettingsSnapshot":
    """Return the deterministic snapshot for a missing ``settings/web.json``.

    ``revision``/``digest`` are not ``None``/a bare sentinel string: they are
    computed the same way a present, valid file's own canonical serialization
    would be hashed, over the *effective default document*
    (``{"schema_version": 1, "max_chars": DEFAULT_OUTPUT_MAX_CHARS}``). This
    makes "default applied" a truthful, stable, independently-verifiable
    diagnostic fact rather than an opaque absence — a caller can recompute
    the same digest from the documented default and confirm it matches.
    """
    canonical = json.dumps(
        {"schema_version": 1, "max_chars": DEFAULT_OUTPUT_MAX_CHARS},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:32]
    return OutputSettingsSnapshot(DEFAULT_OUTPUT_MAX_CHARS, "default", "default", digest)


def _bounded_error(exc: Exception) -> str:
    # OS errors commonly include the absolute path passed to ``open``. The
    # result contract exposes only the agent-relative settings path, never the
    # host filesystem location.
    if isinstance(exc, OSError):
        return f"settings/web.search.json could not be read ({type(exc).__name__})"
    text = str(exc).replace("\n", " ").strip()
    return (text or "invalid settings")[:240]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SettingsError("duplicate settings field")
        result[key] = value
    return result


def _read_stable_named(path: Path, *, filename: str) -> tuple[bytes, str]:
    """Bounded, race-checked read of one settings file, named for error text.

    Shared by both settings readers so ``settings/web.json`` gets the exact
    same symlink/non-regular/size/stability guarantees as
    ``settings/web.search.json`` without duplicating the check logic.
    """
    try:
        first = path.lstat()
    except FileNotFoundError:
        return b"", "missing"
    if stat.S_ISLNK(first.st_mode) or not stat.S_ISREG(first.st_mode):
        raise SettingsError(f"{filename} must be a regular file")
    if first.st_size > MAX_SETTINGS_BYTES:
        raise SettingsError(f"{filename} exceeds the bounded size")
    # Open by path only after lstat: a changed link/non-regular file is rejected
    # rather than followed.  A second stat closes ordinary replacement races.
    with path.open("rb") as handle:
        raw = handle.read(MAX_SETTINGS_BYTES + 1)
    try:
        second = path.lstat()
    except FileNotFoundError as exc:
        raise SettingsError("settings snapshot changed during read") from exc
    if stat.S_ISLNK(second.st_mode) or not stat.S_ISREG(second.st_mode):
        raise SettingsError("settings snapshot changed during read")
    if (
        len(raw) > MAX_SETTINGS_BYTES
        or first.st_size != second.st_size
        or first.st_ino != second.st_ino
        or first.st_mtime_ns != second.st_mtime_ns
    ):
        raise SettingsError("settings snapshot changed during read")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return raw, digest


def _read_stable(path: Path) -> tuple[bytes, str]:
    return _read_stable_named(path, filename="settings/web.search.json")


def _environment_digest(name: str, value: str) -> str:
    """Return a stable revision for one public environment setting."""
    return hashlib.sha256(f"{name}={value}".encode("utf-8")).hexdigest()[:32]


def read_settings(
    agent: Any,
    admitted: Mapping[str, Any],
    default_engine: str | None,
    default_source: str = "built_in_default",
) -> SettingsSnapshot:
    """Read and validate one complete settings snapshot for the current call.

    ``default_source`` is computed once while composing the operator config and
    is passed through unchanged; this reader must not infer it from the engine.
    """
    path = settings_path(agent)
    env_engine = os.environ.get(WEB_ENGINE_ENV)
    if env_engine is not None:
        if not valid_engine_name(env_engine) or env_engine not in admitted:
            return SettingsSnapshot(
                None,
                "environment_error",
                "error",
                None,
                f"{WEB_ENGINE_ENV} must name one bounded owner-admitted engine",
            )
        digest = _environment_digest(WEB_ENGINE_ENV, env_engine)
        return SettingsSnapshot(env_engine, "environment", "environment", digest)
    try:
        raw, revision = _read_stable(path)
        if revision == "missing":
            return SettingsSnapshot(default_engine, default_source, "missing", None)
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or set(value) != {"schema_version", "engine"}:
            raise SettingsError("settings schema must contain only schema_version and engine")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise SettingsError("settings schema_version must be integer 1")
        engine = value["engine"]
        if not isinstance(engine, str) or not _ENGINE_NAME.fullmatch(engine):
            raise SettingsError("settings engine must be a bounded engine name")
        if engine not in admitted:
            raise SettingsError("settings selected engine is not operator-admitted")
        return SettingsSnapshot(engine, "settings/web.search.json", revision, revision)
    except (OSError, UnicodeError, json.JSONDecodeError, SettingsError) as exc:
        # A malformed/changed file is never silently treated as missing.
        digest: str | None = None
        try:
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as handle:
                    digest = hashlib.sha256(handle.read(MAX_SETTINGS_BYTES)).hexdigest()[:32]
        except OSError:
            pass
        return SettingsSnapshot(
            None,
            "settings_error",
            digest or "error",
            digest,
            _bounded_error(exc if isinstance(exc, Exception) else SettingsError("invalid settings")),
        )


def read_output_settings(agent: Any) -> OutputSettingsSnapshot:
    """Read and validate the shared ``settings/web.json`` output-delivery snapshot.

    Consumed identically by ``search`` and ``browse`` for the inline-vs-artifact
    ``max_chars`` threshold. Missing file uses the built-in default
    (``DEFAULT_OUTPUT_MAX_CHARS``); a present-but-invalid file (malformed JSON,
    unknown/duplicate fields, wrong schema_version, non-int/bool/out-of-range
    ``max_chars``, symlink, non-regular, oversize, bad UTF-8, unstable snapshot)
    fails loud via ``OutputSettingsSnapshot.error`` — never silently clamped or
    coerced, and never treated as missing. Neither ``manual`` nor
    ``read_settings`` calls this reader.
    """
    path = output_settings_path(agent)
    env_max_chars = os.environ.get(WEB_MAX_CHARS_ENV)
    if env_max_chars is not None:
        stripped = env_max_chars.strip()
        try:
            max_chars = int(stripped)
        except ValueError:
            max_chars = 0
        if (
            not stripped
            or not (MIN_OUTPUT_MAX_CHARS <= max_chars <= MAX_OUTPUT_MAX_CHARS)
        ):
            return OutputSettingsSnapshot(
                None,
                "environment_error",
                "error",
                None,
                f"{WEB_MAX_CHARS_ENV} must be an integer between "
                f"{MIN_OUTPUT_MAX_CHARS} and {MAX_OUTPUT_MAX_CHARS}",
            )
        digest = _environment_digest(WEB_MAX_CHARS_ENV, env_max_chars)
        return OutputSettingsSnapshot(
            max_chars, "environment", "environment", digest
        )
    try:
        raw, revision = _read_stable_named(path, filename="settings/web.json")
        if revision == "missing":
            return _default_output_snapshot()
        text = raw.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or set(value) != {"schema_version", "max_chars"}:
            raise SettingsError("settings/web.json must contain only schema_version and max_chars")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise SettingsError("settings/web.json schema_version must be integer 1")
        max_chars = value["max_chars"]
        if (
            isinstance(max_chars, bool)
            or not isinstance(max_chars, int)
            or not (MIN_OUTPUT_MAX_CHARS <= max_chars <= MAX_OUTPUT_MAX_CHARS)
        ):
            raise SettingsError(
                f"settings/web.json max_chars must be an integer between "
                f"{MIN_OUTPUT_MAX_CHARS} and {MAX_OUTPUT_MAX_CHARS}"
            )
        return OutputSettingsSnapshot(max_chars, "settings/web.json", revision, revision)
    except (OSError, UnicodeError, json.JSONDecodeError, SettingsError) as exc:
        digest: str | None = None
        try:
            if path.is_file() and not path.is_symlink():
                with path.open("rb") as handle:
                    digest = hashlib.sha256(handle.read(MAX_SETTINGS_BYTES)).hexdigest()[:32]
        except OSError:
            pass
        message = str(exc).replace("\n", " ").strip()
        if isinstance(exc, OSError):
            message = f"settings/web.json could not be read ({type(exc).__name__})"
        return OutputSettingsSnapshot(
            None,
            "settings_error",
            digest or "error",
            digest,
            (message or "invalid settings")[:240],
        )


def current_setting(snapshot: SettingsSnapshot, admitted: Mapping[str, Any], statuses: Mapping[str, str]) -> dict[str, Any]:
    """Build a bounded, secret-free diagnostic block."""
    available = []
    for name, status in statuses.items():
        item = {"name": str(name)[:64], "status": str(status)[:40]}
        # An env-var *name* is configuration metadata, not a credential. Show it
        # only for a missing credential and only when bounded; never inspect or
        # expose its value here.
        if item["status"] == "credential_missing":
            env_name = bounded_env_name(getattr(admitted.get(name), "api_key_env", None))
            if env_name:
                item["api_key_env"] = env_name
        available.append(item)
    available.sort(key=lambda item: item["name"])
    block: dict[str, Any] = {
        "engine": snapshot.engine,
        "search_engine": snapshot.engine,
        "selected_engine": snapshot.engine,
        "source": snapshot.source,
        "available_engines": available,
        "available_engine_names": [item["name"] for item in available],
        "available_engine_status": {item["name"]: item["status"] for item in available},
        "settings_revision": snapshot.revision,
        "settings_hash": snapshot.digest,
        "change_hint": CHANGE_HINT,
    }
    if snapshot.error:
        block["settings_error"] = snapshot.error
    return block


def build_settings_provider(
    workdir: Any,
    admitted: Mapping[str, Any],
    default_engine: Callable[[], str | None],
    default_source: str,
    *,
    provider_current: str | None,
    model_current: str | None,
    api_key_configured: bool | None,
    credential_configured: Callable[[str], bool],
) -> SettingsProvider:
    """Bind Web's applied and hot-read facts to the five-field SHOW seam."""

    def provide() -> tuple[SettingRow, ...]:
        engine_default = default_engine()
        engine = read_settings(
            workdir,
            admitted,
            engine_default,
            default_source,
        )
        output = read_output_settings(workdir)
        if (
            engine.error
            or engine.engine is None
            or output.error
            or output.max_chars is None
        ):
            raise RuntimeError("Web settings current truth is unavailable")
        return (
            SettingRow(
                PROVIDER_KEY,
                provider_current,
                "automatic",
                True,
                "web-manual#provider",
            ),
            SettingRow(
                MODEL_KEY,
                model_current,
                "provider-default",
                True,
                "web-manual#model",
            ),
            SettingRow(
                API_KEY,
                api_key_configured,
                None,
                True,
                "web-manual#api-key",
                _sensitive=True,
            ),
            SettingRow(
                ENGINES_KEY,
                sorted(admitted),
                list(DEFAULT_ENGINE_NAMES),
                True,
                "web-manual#engines",
            ),
            SettingRow(
                SEARCH_ENGINE_KEY,
                engine.engine,
                engine_default,
                True,
                "web-manual#search-engine",
            ),
            SettingRow(
                OUTPUT_MAX_CHARS_KEY,
                output.max_chars,
                DEFAULT_OUTPUT_MAX_CHARS,
                True,
                "web-manual#output-max-chars",
            ),
            *(
                SettingRow(
                    key,
                    credential_configured(provider),
                    None,
                    True,
                    f"web-manual#{provider}-api-key",
                    _sensitive=True,
                )
                for key, provider in (
                    (OPENAI_API_KEY, "openai"),
                    (ANTHROPIC_API_KEY, "anthropic"),
                )
            ),
        )

    return provide
