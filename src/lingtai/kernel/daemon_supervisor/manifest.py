"""Pure, secret-free durable manifest for one detached daemon supervisor.

The manifest is a public routing/configuration record.  Runtime-only values are
carried through the supervisor's one-shot inherited capsule; they are never
replaced by ``<redacted>`` in the inputs consumed by a runner.  This module is
kept dependency-free so it remains a Core schema/validation boundary.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MANIFEST_SCHEMA = "lingtai.daemon_supervisor_manifest.v2"
MANIFEST_FILENAME = "supervisor_manifest.json"
_REQUIRED_FIELDS = (
    "schema", "run_id", "backend", "parent_working_dir", "run_dir", "task",
    "tools", "max_turns", "timeout_s", "group_id",
)
_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|token|password|secret|authorization|cookie|private[_-]?key|credential|passphrase)",
    re.IGNORECASE,
)
_SECRET_ARG_RE = re.compile(
    r"(?:api[-_]?key|token|password|secret|authorization|header|cookie|credential)",
    re.IGNORECASE,
)
_REFERENCE_KEY_RE = re.compile(
    r"(?:^|[_-])(?:env|ref|reference|path)$|(?:^|_)api[_-]?key[_-]?env$",
    re.IGNORECASE,
)
_SENSITIVE_CONTAINER_RE = re.compile(
    r"(?:provider[_-]?defaults?|backend[_-]?options?|harness|headers?|env)$",
    re.IGNORECASE,
)


def manifest_path_for(run_dir: Path) -> Path:
    return Path(run_dir) / MANIFEST_FILENAME


def _is_reference_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    # A registration's ``env``/``headers`` are value containers, not a
    # reference to a secret-bearing file or environment variable.  Treating
    # exact ``env`` as a reference would drop the whole container before its
    # keys could be retained with redacted values.
    if key.lower() in {"env", "headers", "default_headers"}:
        return False
    return key.lower() in {
        "api_key_env", "base_url", "codex_auth_path", "codex_auth_pool_path"
    } or bool(_REFERENCE_KEY_RE.search(key))


def _safe_url(value: object) -> object:
    """Keep URL routing metadata while removing userinfo/query/fragment secrets."""
    if not isinstance(value, str):
        return value
    try:
        parsed = urlsplit(value)
        query = [(key, "<redacted>") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)]
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ""))
    except ValueError:
        return "<redacted-url>"


def _safe_json(value, *, field: str = "", sensitive: bool = False):
    """Return useful metadata without copying arbitrary secret-bearing scalars.

    ``api_key_env`` and other references are deliberately preserved.  Values in
    provider-default/option/env/header containers are not trusted merely because
    their individual key is innocuous, so the public representation uses a
    marker and the supervisor overlays the one-shot capsule at runtime.
    """
    if _is_reference_key(field):
        return str(value) if isinstance(value, (str, Path)) else None
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            if _is_reference_key(key):
                if isinstance(item, str):
                    out[key] = item
                continue
            lower = key.lower()
            compact = "".join(ch for ch in lower if ch.isalnum())
            # Numeric snake_case/camelCase token counters are usage telemetry,
            # not bearer tokens. A literal ``tokens`` usage container is safe
            # to recurse into without treating the container name as a secret;
            # credential-shaped children are still redacted normally.
            if (
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and compact.endswith("tokens")
            ):
                out[key] = item
                continue
            if lower == "tokens" and isinstance(item, (dict, list)):
                out[key] = _safe_json(item)
                continue
            if lower in {"env", "headers", "default_headers"}:
                if isinstance(item, dict):
                    out[key] = {str(k): "<redacted>" for k in item}
                continue
            child_sensitive = sensitive or bool(_SENSITIVE_CONTAINER_RE.search(key))
            if _SECRET_KEY_RE.search(key):
                # ``None`` means no credential was configured; preserving that
                # absence is safer and avoids turning an optional value into a
                # literal runtime marker.  Non-empty secret values remain
                # redacted in the public manifest.
                out[key] = None if item is None else "<redacted>"
                continue
            safe = _safe_json(item, field=key, sensitive=child_sensitive)
            if safe is not None:
                out[key] = safe
        return out
    if isinstance(value, list):
        return [_safe_json(item, field=field, sensitive=sensitive) for item in value]
    if isinstance(value, str):
        if sensitive:
            return "<redacted>"
        return _safe_url(value) if ("url" in field.lower() or "://" in value) else value
    if sensitive and value is not None:
        return "<redacted>"
    return value


def _safe_llm(llm: dict | None) -> dict | None:
    if not isinstance(llm, dict):
        return None
    safe = _safe_json(llm)
    return safe if isinstance(safe, dict) else None


def _safe_argv(argv: list[str] | None) -> list[str] | None:
    """Redact credential-shaped argv while preserving ordinary CLI routing.

    The manager-created path also carries the exact argv in the ephemeral
    capsule.  Standalone manifests retain harmless executable/flag values for
    diagnostics and compatibility; obvious sentinel/credential values never
    enter durable state.
    """
    if argv is None:
        return None
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ValueError("backend_argv must be an array of strings")
    out: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            out.append("<redacted>")
            redact_next = False
            continue
        if token.startswith("--") and "=" in token:
            flag, value = token.split("=", 1)
            if _SECRET_ARG_RE.search(flag) or re.search(r"SECRET|TOKEN|PASSWORD|CREDENTIAL", value, re.I):
                out.append(flag + "=<redacted>")
            else:
                out.append(token)
        elif token.startswith("--") and _SECRET_ARG_RE.search(token):
            out.append(token)
            redact_next = True
        elif re.search(r"SECRET|TOKEN|PASSWORD|CREDENTIAL", token, re.I):
            out.append("<redacted>")
        else:
            out.append(token)
    return out


def _redact_command_text(value: str) -> str:
    """Redact an argv rendered as a command string for durable diagnostics."""
    try:
        return shlex.join(_safe_argv(shlex.split(value)) or [])
    except (ValueError, TypeError):
        # The command is diagnostic-only.  Preserve its shape if it is not a
        # valid shell rendering, while still removing the common inline form.
        return re.sub(
            r"(?i)(--(?:api[-_]?key|token|password|secret|authorization|header|cookie|credential))=[^\s]+",
            r"\1=<redacted>", value,
        )


def redact_durable_event_fields(fields: dict) -> dict:
    """Redact all known command/argv event renderings in one boundary."""
    result = {}
    for key, value in fields.items():
        lower = str(key).lower()
        if lower in {"argv", "backend_argv", "harness_argv", "command_argv"}:
            result[key] = _safe_argv(value) if isinstance(value, list) else value
        elif lower in {"cmd", "command", "cmd_head", "command_line"} and isinstance(value, str):
            result[key] = _redact_command_text(value)
        else:
            result[key] = _safe_json(value, field=str(key))
    return result


def secret_argv_values(argv: list[str] | None) -> list[str]:
    """Return secret-bearing option values for ephemeral runtime scrubbing."""
    if not isinstance(argv, list):
        return []
    values: list[str] = []
    expect = False
    for token in argv:
        if expect:
            values.append(token)
            expect = False
            continue
        if not isinstance(token, str):
            continue
        if token.startswith("--") and "=" in token:
            flag, value = token.split("=", 1)
            if _SECRET_ARG_RE.search(flag):
                values.append(value)
        elif token.startswith("--") and _SECRET_ARG_RE.search(token):
            expect = True
    return [value for value in values if value]


def _safe_mcp(mcp: list[dict] | None) -> list[dict]:
    if mcp is None:
        return []
    if not isinstance(mcp, list) or not all(isinstance(item, dict) for item in mcp):
        raise ValueError("mcp must be an array of MCP registration objects")
    result = []
    for item in mcp:
        safe = _safe_json(item)
        if isinstance(safe, dict):
            result.append(safe)
    return result


def build_manifest(
    *, run_id: str, backend: str, parent_working_dir: str, run_dir: str,
    task: str, tools: list[str], max_turns: int, timeout_s: float,
    prompt: str | None = None,
    group_id: str | None, mcp: list[dict] | None = None,
    context_token_limit: int | None = None, llm: dict | None = None,
    backend_argv: list[str] | None = None, language: str = "en",
    preset_name: str | None = None, preset_llm: dict | None = None,
    preset_capabilities: dict | None = None,
) -> dict:
    """Build the supervisor input record without resolved credentials."""
    return {
        "schema": MANIFEST_SCHEMA,
        "run_id": run_id,
        "backend": backend,
        "parent_working_dir": parent_working_dir,
        "run_dir": run_dir,
        "task": task,
        "prompt": prompt,
        "tools": list(tools or []),
        "mcp": _safe_mcp(mcp),
        "max_turns": max_turns,
        "timeout_s": timeout_s,
        "context_token_limit": context_token_limit,
        "llm": _safe_llm(llm),
        "backend_argv": _safe_argv(backend_argv),
        "group_id": group_id,
        "language": language if isinstance(language, str) else "en",
        "preset_name": preset_name,
        "preset_llm": _safe_llm(preset_llm),
        "preset_capabilities": _safe_json(preset_capabilities) if isinstance(preset_capabilities, dict) else None,
    }


def write_manifest(run_dir: Path, manifest: dict) -> Path:
    """Atomically write a restrictive-mode manifest."""
    from lingtai.kernel._fsutil import atomic_write_json
    path = manifest_path_for(run_dir)
    atomic_write_json(path, manifest, ensure_ascii=False, indent=2)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def read_manifest(path: Path) -> dict:
    """Read and validate identity-bearing manifest fields."""
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"daemon supervisor manifest at {path} is not a JSON object")
    if data.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(
            f"daemon supervisor manifest at {path} has unexpected schema "
            f"{data.get('schema')!r}, expected {MANIFEST_SCHEMA!r}"
        )
    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"daemon supervisor manifest at {path} missing fields: {missing!r}")
    for field in ("run_id", "backend", "parent_working_dir", "run_dir", "task", "group_id"):
        if field != "group_id" and not isinstance(data[field], str):
            raise ValueError(f"manifest field {field!r} must be a string")
    if "prompt" in data and data["prompt"] is not None and not isinstance(data["prompt"], str):
        raise ValueError("manifest field 'prompt' must be a string or null")
    if not isinstance(data["tools"], list) or not all(isinstance(x, str) for x in data["tools"]):
        raise ValueError("manifest tools must be an array of strings")
    if isinstance(data["max_turns"], bool) or not isinstance(data["max_turns"], int) or data["max_turns"] < 1:
        raise ValueError("manifest max_turns must be a positive integer")
    if isinstance(data["timeout_s"], bool) or not isinstance(data["timeout_s"], (int, float)) or data["timeout_s"] <= 0:
        raise ValueError("manifest timeout_s must be positive")
    run_dir = Path(data["run_dir"]).resolve()
    if run_dir != path.parent or run_dir.name == "":
        raise ValueError("manifest run_dir does not match its canonical parent directory")
    if data["run_id"] != run_dir.name:
        raise ValueError("manifest run_id does not match run directory name")
    return data


def redact_durable_value(value, *, field: str = ""):
    """Expose the same public metadata policy to outer composition code."""
    return _safe_json(value, field=field)


def redact_durable_argv(argv: list[str] | None) -> list[str] | None:
    return _safe_argv(argv)


__all__ = [
    "MANIFEST_SCHEMA", "MANIFEST_FILENAME", "manifest_path_for", "build_manifest",
    "write_manifest", "read_manifest", "redact_durable_value", "redact_durable_argv",
    "redact_durable_event_fields", "secret_argv_values",
]
