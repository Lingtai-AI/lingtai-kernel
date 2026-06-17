"""MCP capability — per-agent control plane for MCP servers.

- Per-agent registry lives at ``<agent>/mcp_registry.jsonl`` (sibling to
  ``init.json``). One JSON record per line. Activation lives in
  ``init.json["mcp"]`` (gated by the registry).
- Capability scans the registry on setup, validates each line, renders the
  registry as XML into the system prompt's ``mcp`` section.
- Boot-time decompression: any name in ``init.json``'s ``addons: [...]`` list
  that isn't already in the registry gets appended from the kernel-shipped
  catalog (``lingtai/mcp_catalog.json``). Append-only, idempotent.
- The ``mcp`` tool is a minimal 3-action control plane: ``list`` inspects the
  registry + init activation (secrets redacted); ``add`` registers an MCP and
  writes its init.json activation in one step; ``remove`` deregisters it and
  strips its init.json activation in one step. ``add``/``remove`` return
  ``needs_refresh: true`` and remind the agent to call
  ``system(action="refresh")``. The runtime loader, MCP transport, and the
  post-start tool seal are untouched — desired-state edits never mutate the
  live tool surface. Refresh is owned by the ``system`` tool, not ``mcp``.
  Complex troubleshooting/manual work goes through the MCP manual plus
  bash/file tools, not this tool.

Usage: ``Agent(capabilities=["mcp"])`` or via init.json.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lingtai_kernel.base_agent import BaseAgent

log = logging.getLogger(__name__)

PROVIDERS = {"providers": [], "default": "builtin"}

REGISTRY_FILENAME = "mcp_registry.jsonl"
CATALOG_FILENAME = "mcp_catalog.json"

# Match library's name convention: lowercase, dash-separated, bounded length.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
_VALID_TRANSPORTS = {"stdio", "http"}
_MAX_SUMMARY_LEN = 200

_REFRESH_REMINDER = (
    'Run system(action="refresh") to apply this change — '
    "nothing changes in your live tool surface until you do."
)


# ---------------------------------------------------------------------------
# Catalog (kernel-shipped) — read once, cached on first access.
# ---------------------------------------------------------------------------

_CATALOG_CACHE: dict[str, dict] | None = None


def _load_catalog() -> dict[str, dict]:
    """Read the kernel-shipped MCP catalog. Cached after first call.

    Returns a dict mapping name → record. Entries with leading underscore
    (e.g. ``_comment``) are skipped.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    catalog_path = Path(__file__).parent.parent.parent / CATALOG_FILENAME
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("mcp: failed to load catalog at %s: %s", catalog_path, e)
        _CATALOG_CACHE = {}
        return _CATALOG_CACHE

    _CATALOG_CACHE = {
        name: record for name, record in raw.items()
        if not name.startswith("_") and isinstance(record, dict)
    }
    return _CATALOG_CACHE


# ---------------------------------------------------------------------------
# Validator — single source of truth for registry record schema.
# ---------------------------------------------------------------------------

def validate_record(record: dict) -> tuple[bool, str | None]:
    """Validate a single MCP registry record.

    Returns (is_valid, error_message). On success, error_message is None.
    """
    if not isinstance(record, dict):
        return False, "record must be a JSON object"

    name = record.get("name")
    if not isinstance(name, str):
        return False, "missing or non-string field: name"
    if not _NAME_RE.match(name):
        return False, f"invalid name {name!r}: must match {_NAME_RE.pattern}"

    summary = record.get("summary")
    if not isinstance(summary, str) or not summary:
        return False, "missing or empty field: summary"
    if len(summary) > _MAX_SUMMARY_LEN:
        return False, f"summary too long ({len(summary)} > {_MAX_SUMMARY_LEN} chars)"

    transport = record.get("transport")
    if transport not in _VALID_TRANSPORTS:
        return False, f"invalid transport {transport!r}: must be one of {sorted(_VALID_TRANSPORTS)}"

    if transport == "stdio":
        if not isinstance(record.get("command"), str):
            return False, "stdio transport requires field 'command' (string)"
        args = record.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            return False, "stdio transport requires field 'args' (list of strings)"
    else:  # http
        if not isinstance(record.get("url"), str):
            return False, "http transport requires field 'url' (string)"

    source = record.get("source")
    if not isinstance(source, str) or not source:
        return False, "missing or empty field: source"

    # Optional: homepage must be a string when present.
    homepage = record.get("homepage")
    if homepage is not None and (not isinstance(homepage, str) or not homepage):
        return False, "homepage must be a non-empty string when present"

    return True, None


def validate_registry_line(line: str) -> tuple[bool, str | None, dict | None]:
    """Validate a single JSONL line. Returns (is_valid, error, parsed_record)."""
    line = line.strip()
    if not line:
        return False, "empty line", None
    try:
        record = json.loads(line)
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e}", None
    valid, err = validate_record(record)
    return valid, err, record if valid else None


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------

def _registry_path(working_dir: Path) -> Path:
    return working_dir / REGISTRY_FILENAME


def read_registry(working_dir: Path) -> tuple[list[dict], list[dict]]:
    """Read and validate the registry file.

    Returns (valid_records, problems). Problems is a list of
    {line: int, error: str, raw: str} dicts.
    """
    path = _registry_path(working_dir)
    if not path.is_file():
        return [], []

    valid: list[dict] = []
    problems: list[dict] = []
    seen_names: set[str] = set()

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [], [{"line": 0, "error": f"cannot read registry: {e}", "raw": ""}]

    for i, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        ok, err, record = validate_registry_line(raw)
        if not ok:
            problems.append({"line": i, "error": err or "unknown", "raw": raw})
            continue
        assert record is not None
        if record["name"] in seen_names:
            problems.append({
                "line": i,
                "error": f"duplicate name {record['name']!r}",
                "raw": raw,
            })
            continue
        seen_names.add(record["name"])
        valid.append(record)

    return valid, problems


def _append_record(working_dir: Path, record: dict) -> None:
    """Append a validated record as a JSONL line. Caller must validate first."""
    path = _registry_path(working_dir)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def _remove_record(working_dir: Path, name: str) -> bool:
    """Remove the registry line whose record name == ``name``.

    Rewrites ``mcp_registry.jsonl`` preserving every other line verbatim —
    including comments and blank lines (best effort). Lines that fail to
    parse, or whose parsed ``name`` differs, are kept untouched. Returns True
    if at least one matching record line was dropped.
    """
    path = _registry_path(working_dir)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    kept: list[str] = []
    removed = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped:
            try:
                rec = json.loads(stripped)
            except json.JSONDecodeError:
                rec = None
            if isinstance(rec, dict) and rec.get("name") == name:
                removed = True
                continue
        kept.append(raw)
    if removed:
        body = "\n".join(kept)
        if body and not body.endswith("\n"):
            body += "\n"
        path.write_text(body, encoding="utf-8")
    return removed


# ---------------------------------------------------------------------------
# {python} substitution — shared by decompress_addons and the `add` action.
# ---------------------------------------------------------------------------

def _substitute_placeholders(value):
    """Resolve catalog placeholders (currently ``{python}`` → sys.executable).

    Recurses into lists/dicts. Mirrors the substitution ``decompress_addons``
    applies so the ``add`` action treats catalog entries identically.
    """
    import sys
    substitutions = {"{python}": sys.executable}
    if isinstance(value, str):
        for k, v in substitutions.items():
            if k in value:
                value = value.replace(k, v)
        return value
    if isinstance(value, list):
        return [_substitute_placeholders(x) for x in value]
    if isinstance(value, dict):
        return {k: _substitute_placeholders(v) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Boot-time decompression: addons:[...] → registry
# ---------------------------------------------------------------------------

def decompress_addons(working_dir: Path, addons: list[str]) -> dict:
    """Append catalog entries for any addon name not already in the registry.

    Non-destructive: never modifies existing records, never reorders.
    Idempotent: running multiple times produces the same registry as once.

    Returns a report dict {appended: [...], skipped: [...], unknown: [...],
    invalid: [...]}.
    """
    catalog = _load_catalog()
    existing, _problems = read_registry(working_dir)
    existing_names = {r["name"] for r in existing}

    appended: list[str] = []
    skipped: list[str] = []
    unknown: list[str] = []
    invalid: list[dict] = []

    for name in addons:
        if name in existing_names:
            skipped.append(name)
            continue
        if name not in catalog:
            unknown.append(name)
            log.warning("mcp: addon %r not found in catalog", name)
            continue
        record = _substitute_placeholders(dict(catalog[name]))
        ok, err = validate_record(record)
        if not ok:
            invalid.append({"name": name, "error": err})
            log.warning("mcp: catalog entry %r failed validation: %s", name, err)
            continue
        _append_record(working_dir, record)
        appended.append(name)
        existing_names.add(name)

    return {
        "appended": appended,
        "skipped": skipped,
        "unknown": unknown,
        "invalid": invalid,
    }


# ---------------------------------------------------------------------------
# XML registry builder (rendered into system prompt)
# ---------------------------------------------------------------------------

def _escape_xml(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_registry_xml(records: list[dict]) -> str:
    if not records:
        return ""
    lines = [
        "The following MCP servers are registered for this agent. To activate "
        "one, add an entry under `mcp` in your init.json and run "
        "system(action=\"refresh\"). See the mcp-manual skill in "
        ".library/intrinsic/capabilities/mcp/ for the full registration "
        "contract. When you need install or config instructions for a "
        "specific MCP, fetch its <homepage> README via web_read or "
        "bash + curl as your first step (unless you have other guidance).",
        "",
        "<registered_mcp>",
    ]
    for r in records:
        lines.append("  <mcp>")
        lines.append(f"    <name>{_escape_xml(r['name'])}</name>")
        lines.append(f"    <summary>{_escape_xml(r['summary'])}</summary>")
        lines.append(f"    <transport>{_escape_xml(r['transport'])}</transport>")
        lines.append(f"    <source>{_escape_xml(r.get('source', ''))}</source>")
        homepage = r.get("homepage")
        if homepage:
            lines.append(f"    <homepage>{_escape_xml(homepage)}</homepage>")
        lines.append("  </mcp>")
    lines.append("</registered_mcp>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reconciliation (renders registry into the system prompt on setup)
# ---------------------------------------------------------------------------

def _reconcile(agent: "BaseAgent") -> None:
    """Read the registry and render it into the system prompt's ``mcp`` section."""
    records, _problems = read_registry(agent._working_dir)
    xml = _build_registry_xml(records)
    agent.update_system_prompt("mcp", xml, protected=True)


# ---------------------------------------------------------------------------
# Secret redaction — never echo env/header/token values into tool results,
# the prompt, or audit logs.
# ---------------------------------------------------------------------------

_REDACTED = "<redacted>"
# Field names whose *values* are secrets and must be replaced wholesale.
_SECRET_KEY_DICTS = {"env", "headers"}
# Substrings that mark a scalar field name as secret-bearing.
_SECRET_NAME_HINTS = ("token", "password", "secret", "key", "authorization")


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SECRET_NAME_HINTS)


def _redact_problems(problems: list[dict]) -> list[dict]:
    """Return registry problems without echoing raw registry lines.

    Invalid JSONL lines can contain headers/env/token-like fragments. The line
    number and validation error are enough for diagnosis; never surface raw.
    """
    return [
        {k: v for k, v in problem.items() if k != "raw"}
        for problem in problems
    ]


def _redact_config(value):
    """Return a deep copy of ``value`` with secret-bearing fields redacted.

    - Any value under an ``env`` / ``headers`` key is redacted, even if the
      value has an unexpected non-dict shape.
    - Any scalar field whose name looks token/password/secret/key-like is
      replaced with ``"<redacted>"``.
    Recurses into nested dicts/lists so secrets cannot hide one level down.
    """
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if k in _SECRET_KEY_DICTS:
                out[k] = ({ik: _REDACTED for ik in v}
                          if isinstance(v, dict) else _REDACTED)
            elif _is_secret_name(str(k)) and not isinstance(v, (dict, list)):
                out[k] = _REDACTED
            else:
                out[k] = _redact_config(v)
        return out
    if isinstance(value, list):
        return [_redact_config(x) for x in value]
    return value


# ---------------------------------------------------------------------------
# init.json desired-state I/O (activation layer)
# ---------------------------------------------------------------------------

def _init_path(working_dir: Path) -> Path:
    return working_dir / "init.json"


def _read_init(working_dir: Path) -> dict:
    """Read init.json as a dict for diagnostics. Missing/invalid → empty dict."""
    path = _init_path(working_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_init_for_write(working_dir: Path) -> dict:
    """Read init.json before mutation. Missing → {}, invalid/non-object → error."""
    path = _init_path(working_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot mutate invalid init.json: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("cannot mutate init.json: top-level JSON must be an object")
    return data


def _write_init(working_dir: Path, data: dict) -> None:
    """Write init.json, preserving key order and pretty-printing."""
    path = _init_path(working_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _derive_init_config(record: dict) -> dict:
    """Build an init.json ``mcp`` entry from a validated registry record.

    stdio → ``{type, command, args, [env]}``; http → ``{type, url, [headers]}``.
    Only structural fields are copied; the registry holds no secrets, so this
    never carries credentials (callers pass an explicit ``config`` for those).
    """
    transport = record.get("transport")
    if transport == "http":
        cfg: dict = {"type": "http", "url": record.get("url")}
        if isinstance(record.get("headers"), dict):
            cfg["headers"] = record["headers"]
        return cfg
    cfg = {
        "type": "stdio",
        "command": record.get("command"),
        "args": list(record.get("args", [])),
    }
    if isinstance(record.get("env"), dict):
        cfg["env"] = record["env"]
    return cfg


def _activation_summary(working_dir: Path, records: list[dict]) -> dict:
    """Cross-reference registry vs init.json ``mcp`` (redacted).

    Returns ``{enabled: [...names...], gated: [...names...], entries: {...}}``
    where ``gated`` names are present in init.json but missing from the
    registry (the loader would skip them), and ``entries`` maps each enabled
    name → its redacted config.
    """
    init = _read_init(working_dir)
    init_mcp = init.get("mcp") if isinstance(init.get("mcp"), dict) else {}
    registered = {r["name"] for r in records}
    enabled: list[str] = []
    gated: list[str] = []
    entries: dict[str, dict] = {}
    for name, cfg in init_mcp.items():
        entries[name] = (
            _redact_config(cfg) if isinstance(cfg, dict)
            else {"error": "activation config is not an object"}
        )
        if name in registered:
            enabled.append(name)
        else:
            gated.append(name)
    return {"enabled": enabled, "gated": gated, "entries": entries}


# ---------------------------------------------------------------------------
# Manager action handlers — minimal 3-action surface (list/add/remove).
# Each takes (agent, args) and returns a JSON-serializable result dict.
# ---------------------------------------------------------------------------

def _log_action(agent: "BaseAgent", action: str, name: str | None, status: str) -> None:
    """Audit a control-plane mutation. Never logs config/secrets."""
    log_fn = getattr(agent, "_log", None)
    if callable(log_fn):
        try:
            log_fn("mcp_manager_action", action=action, name=name, status=status)
        except Exception:
            pass


def _handle_list(agent: "BaseAgent") -> dict:
    working_dir = agent._working_dir
    records, problems = read_registry(working_dir)
    return {
        "status": "ok",
        "registry_path": str(_registry_path(working_dir)),
        "registry": [
            {"name": r["name"], "summary": r["summary"],
             "transport": r["transport"]}
            for r in records
        ],
        "problems": _redact_problems(problems),
        "activation": _activation_summary(working_dir, records),
    }


def _handle_add(agent: "BaseAgent", args: dict) -> dict:
    """Register an MCP AND write its init.json activation in one step.

    ``add`` implies enable: it appends the validated record to the registry
    and writes the matching ``init.json["mcp"]`` entry. Pass ``name`` for a
    catalog entry or ``record`` for a full record; pass ``config`` to override
    the derived init.json activation (else derived from the record).
    """
    working_dir = agent._working_dir
    record = args.get("record")
    name_arg = args.get("name")

    if record is None and isinstance(name_arg, str):
        catalog = _load_catalog()
        if name_arg not in catalog:
            _log_action(agent, "add", name_arg, "error")
            return {"status": "error",
                    "message": f"{name_arg!r} not found in catalog"}
        record = _substitute_placeholders(dict(catalog[name_arg]))

    if not isinstance(record, dict):
        _log_action(agent, "add", None, "error")
        return {"status": "error",
                "message": "provide either 'record' (dict) or 'name' (catalog entry)"}

    ok, err = validate_record(record)
    if not ok:
        _log_action(agent, "add", record.get("name"), "error")
        return {"status": "error", "message": f"invalid record: {err}"}

    name = record["name"]
    existing, _problems = read_registry(working_dir)
    if name in {r["name"] for r in existing}:
        _log_action(agent, "add", name, "error")
        return {"status": "error",
                "message": f"registry already has an entry named {name!r} (duplicate)"}

    # Resolve the init.json activation config before touching any file, so an
    # invalid 'config' or invalid init.json aborts cleanly with no partial write.
    config = args.get("config")
    if config is None:
        config = _derive_init_config(record)
    elif not isinstance(config, dict):
        _log_action(agent, "add", name, "error")
        return {"status": "error", "message": "'config' must be a dict when provided"}

    try:
        init = _read_init_for_write(working_dir)
    except ValueError as e:
        _log_action(agent, "add", name, "error")
        return {"status": "error", "message": str(e)}

    _append_record(working_dir, record)
    mcp = init.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
    mcp[name] = config
    init["mcp"] = mcp
    _write_init(working_dir, init)
    _log_action(agent, "add", name, "ok")
    return {
        "status": "ok",
        "name": name,
        "needs_refresh": True,
        "config": _redact_config(config),
        "message": (
            f"registered {name!r} and activated it in init.json. "
            + _REFRESH_REMINDER
        ),
    }


def _handle_remove(agent: "BaseAgent", args: dict) -> dict:
    """Deregister an MCP AND strip its init.json activation in one step.

    ``remove`` implies disable: it drops the registry record, removes
    ``init.json["mcp"][name]``, and drops ``name`` from ``init.json["addons"]``
    if present (so a catalog addon won't be re-decompressed on next boot).
    """
    working_dir = agent._working_dir
    name = args.get("name")
    if not isinstance(name, str) or not name:
        return {"status": "error", "message": "remove requires 'name' (string)"}

    existing, _problems = read_registry(working_dir)
    init_mcp_has = name in (_read_init(working_dir).get("mcp") or {})
    if name not in {r["name"] for r in existing} and not init_mcp_has:
        _log_action(agent, "remove", name, "error")
        return {"status": "error",
                "message": f"no registry entry or init.json activation named {name!r}"}

    # Read init.json for write FIRST — a corrupt init.json aborts before any
    # registry mutation, so we never leave the registry and init out of sync.
    try:
        init = _read_init_for_write(working_dir)
    except ValueError as e:
        _log_action(agent, "remove", name, "error")
        return {"status": "error", "message": str(e)}

    _remove_record(working_dir, name)

    init_changed = False
    mcp = init.get("mcp")
    if isinstance(mcp, dict) and name in mcp:
        del mcp[name]
        init["mcp"] = mcp
        init_changed = True
    addons = init.get("addons")
    if isinstance(addons, list) and name in addons:
        init["addons"] = [a for a in addons if a != name]
        init_changed = True
    if init_changed:
        _write_init(working_dir, init)

    _log_action(agent, "remove", name, "ok")
    return {
        "status": "ok",
        "name": name,
        "needs_refresh": True,
        "message": (
            f"deregistered {name!r} and removed its init.json activation. "
            + _REFRESH_REMINDER
        ),
    }


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

_DESCRIPTION = (
    "MCP control plane for this agent — a minimal three-action surface for "
    "registering and deregistering MCP servers. The <registered_mcp> catalog "
    "in your system prompt lists every server currently registered. For "
    "anything beyond list/add/remove (config fields, troubleshooting, manual "
    "registration), read the `mcp-manual` skill and use bash/file tools. "
    "Actions: `list` (inspect registry + init.json activation, secrets "
    "redacted), `add` (register an MCP and activate it in init.json in one "
    "step — by catalog `name` or full `record`, optional `config`), `remove` "
    "(deregister by `name` and strip its init.json activation in one step). "
    "`add`/`remove` edit desired state only and return `needs_refresh: true`; "
    "nothing changes in the running tool surface until you call "
    "system(action=\"refresh\"). Refresh belongs to the `system` tool, not "
    "`mcp`. Hand-editing mcp_registry.jsonl / init.json then calling "
    "system(action=\"refresh\") still works as an escape hatch."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "add", "remove"],
            "description": (
                "list: registry summary + init.json activation summary "
                "(secrets redacted). add: register a new entry AND activate it "
                "in init.json in one step — pass 'name' (catalog entry) or "
                "'record' (dict); optional 'config' overrides the derived "
                "init.json activation; rejects duplicates; returns "
                "needs_refresh. remove: deregister 'name' AND strip its "
                "init.json activation (and addons entry) in one step; returns "
                "needs_refresh. add/remove require system(action=\"refresh\") "
                "to take effect."
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "Target MCP name. For add: a catalog entry name. For remove: "
                "the registry/activation entry name."
            ),
        },
        "record": {
            "type": "object",
            "description": (
                "A full registry record for add "
                "(name, summary, transport, command/args or url, source)."
            ),
        },
        "config": {
            "type": "object",
            "description": (
                "Optional init.json activation config for add "
                "(type, command/args/env or url/headers). Derived from the "
                "record when omitted."
            ),
        },
    },
    "required": ["action"],
}


def get_description(lang: str = "en") -> str:
    return _DESCRIPTION


def get_schema(lang: str = "en") -> dict:
    return _SCHEMA


def setup(agent: "BaseAgent", **_ignored) -> None:
    """Set up the mcp control-plane capability.

    Reads the registry from disk and renders it into the system prompt, then
    registers the minimal 3-action ``mcp`` tool. ``list`` inspects desired
    state; ``add``/``remove`` edit the desired-state files (mcp_registry.jsonl
    + init.json) in one step each and return ``needs_refresh: true``. Refresh
    is owned by the ``system`` tool; the runtime loader, transport, and seal
    model are untouched. Decompression of init.json's addons: field happens in
    the Agent initializer via decompress_addons() before setup is called.
    """
    _reconcile(agent)

    def handle_mcp(args: dict) -> dict:
        action = args.get("action", "")
        if action == "list":
            return _handle_list(agent)
        if action == "add":
            return _handle_add(agent, args)
        if action == "remove":
            return _handle_remove(agent, args)
        return {
            "status": "error",
            "message": (
                f"unknown action: {action!r}. Valid actions: list, add, remove."
            ),
        }

    agent.add_tool(
        "mcp",
        schema=get_schema(),
        handler=handle_mcp,
        description=get_description(),
    )
