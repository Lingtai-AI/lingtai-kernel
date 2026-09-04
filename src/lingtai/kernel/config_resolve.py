"""Shared config resolution helpers — env vars, capabilities, paths."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


_TRAILING_COMMA_RE = re.compile(r',\s*([}\]])')


def _strip_comments(text: str) -> str:
    """Strip ``//`` comments while preserving string literals verbatim.

    Implemented as a small state machine rather than splitting around string
    literals: JSONC comments may themselves contain quoted examples (for
    example ``// copy as "init.json"``); those quotes must not become JSON data
    or hide the real delimiters on the following lines.
    """
    parts: list[str] = []
    in_string = False
    escaped = False
    in_comment = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                parts.append(ch)
            i += 1
            continue
        if in_string:
            parts.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            parts.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            in_comment = True
            i += 2
            continue
        parts.append(ch)
        i += 1
    return "".join(parts)


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas without touching string literals.

    Equivalent to applying ``re.sub(r',\\s*([}\\]]])', r'\\1', text)`` only to the
    non-string spans of *text*, so a string value containing `, ]` or `, }`
    (or `,]` / `,}`) survives byte-for-byte instead of being silently
    rewritten. Genuine trailing commas always sit in the same non-string span
    as their closing bracket, so this is semantically identical to the old
    whole-text pass for every parseable input.
    """
    out: list[str] = []
    pos = 0
    in_string = False
    escaped = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)  # copy the string literal verbatim
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                pos = i + 1
            i += 1
            continue
        if ch == '"':
            out.append(_TRAILING_COMMA_RE.sub(r"\1", text[pos:i]))
            out.append(ch)  # opening quote starts the string literal
            in_string = True
            i += 1
            continue
        i += 1
    out.append(_TRAILING_COMMA_RE.sub(r"\1", text[pos:]))
    return "".join(out)


def parse_jsonc(text: str) -> dict:
    """Parse JSON or JSONC text (strips // comments and trailing commas).

    Pure text→object transform with no I/O, so callers holding raw text (e.g.
    migration transforms) use it directly. Both normalisations are string-aware:
    // inside a quoted string is never a comment (URLs like "https://host/..."
    survive), and a string value containing `, ]` or `, }` is left intact.
    """
    text = _strip_comments(text)
    text = _strip_trailing_commas(text)
    return json.loads(text)


def load_jsonc(path: str | Path) -> dict:
    """Load a JSON or JSONC file (strips // comments and trailing commas).

    Thin I/O wrapper: reads UTF-8 text from *path* and delegates the parse to
    :func:`parse_jsonc`, which owns the comment/trailing-comma stripping rules.
    """
    return parse_jsonc(Path(path).read_text(encoding="utf-8"))


def resolve_env(value: str | None, env_name: str | None) -> str | None:
    """Resolve a value from env var name, falling back to raw value."""
    if env_name:
        env_val = os.environ.get(env_name)
        if env_val:
            return env_val
    return value


def resolve_env_checked(
    value: str | None,
    env_name: str | None,
    *,
    context: str = "",
    warn=None,
) -> str | None:
    """``resolve_env`` plus a diagnostic when *env_name* is named but misses.

    ``resolve_env`` itself stays silent because the generic ``*_env`` paths
    (capability kwargs via ``_resolve_env_fields``, preset resolution) treat an
    absent variable as routine. Callers where a miss is always a
    misconfiguration (the LLM api-key path at boot/refresh) pass a ``warn``
    callback; the default prints a warning to stderr.

    ``warn`` receives one message argument. Returns the same value as
    ``resolve_env`` (possibly ``None``).
    """
    resolved = resolve_env(value, env_name)
    if env_name and resolved is None:
        msg = (
            f"{context}: environment variable {env_name!r} is unset or empty "
            "and no fallback value is configured"
        )
        (warn or (lambda m: print(f"warning: {m}", file=sys.stderr)))(msg)
    return resolved


def _strip_matched_quotes(val: str) -> str:
    """Strip exactly one layer of symmetric matching quotes (dotenv semantics).

    ``'"v"'`` keeps its inner double quotes (``"v"``) rather than collapsing
    both layers; an unmatched leading or trailing quote is preserved verbatim.
    """
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        return val[1:-1]
    return val


def load_env_file(path: str | Path, *, overwrite: bool = False) -> None:
    """Load a .env file into os.environ.

    By default, existing process environment variables are preserved so a
    caller's explicit shell environment wins at initial boot. Pass
    ``overwrite=True`` for deliberate config reloads (notably
    ``system(action="refresh")``) so edits to the agent's env_file replace
    stale values inherited by the relaunched process.
    """
    env_path = Path(path).expanduser()
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Copy-pasted shell lines (`export KEY=val`) are the most common way
        # env files are produced; every mainstream dotenv implementation
        # accepts the `export ` prefix.
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[len("export"):].lstrip()
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        # A malformed key would otherwise write a bogus name (e.g. the old
        # `export KEY` behavior) that no lookup can ever find.
        if not key or " " in key:
            continue
        val = _strip_matched_quotes(val.strip())
        if overwrite or key not in os.environ:
            os.environ[key] = val


def resolve_file(value: str | None, file_path: str | None) -> str | None:
    """Resolve a value from a file path, falling back to raw value."""
    if file_path:
        p = Path(file_path).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return value


def _resolve_env_fields(d: dict) -> dict:
    """Resolve ``*_env`` keys in a dict using ``resolve_env``."""
    result = dict(d)
    env_keys = [k for k in result if k.endswith("_env")]
    for env_key in env_keys:
        base_key = env_key[: -len("_env")]
        result[base_key] = resolve_env(result.get(base_key), result.pop(env_key))
    return result


def resolve_paths(data: dict, working_dir: str | Path) -> None:
    """Make every path field in init.json absolute, resolved against working_dir.

    Mutates *data* in place. Handles live init-owned top-level paths:
    env_file, venv_path, pad_file, and lingtai_file. Psyche prompt pointers
    have their own closed owner reader.

    MCP-related paths (init.json's `mcp.<name>.env.LINGTAI_*_CONFIG`) are
    intentionally left relative — each MCP server resolves its own config
    path against LINGTAI_AGENT_DIR at startup, which the kernel injects.
    """
    wd = Path(working_dir)

    # Psyche-owned prompt pairs and the retired kernel/secretary prompt pairs
    # are compatibility-known init fields, deliberately left out of active path
    # resolution. They must not become observable sources simply because an old
    # init.json carries them.
    for key in ("env_file", "venv_path",
                "pad_file",
                "lingtai_file"):
        if key in data and isinstance(data[key], str) and data[key]:
            p = Path(data[key]).expanduser()
            if not p.is_absolute():
                p = wd / p
            data[key] = str(p)


def _resolve_capabilities(capabilities: dict) -> dict:
    """Resolve ``*_env`` fields in each capability's kwargs."""
    resolved = {}
    for name, kwargs in capabilities.items():
        if isinstance(kwargs, dict) and kwargs:
            resolved[name] = _resolve_env_fields(kwargs)
        else:
            resolved[name] = kwargs
    return resolved
