"""MCP registry infrastructure — validation, I/O, identities, addon decompression.

This is the non-tool MCP registry machinery — a *service*, not an agent-callable
tool. Plugin's explicit ``mode="mcp"`` signpost (which renders this data into the
system prompt) lives at ``lingtai/tools/plugin/_mcp.py`` and imports these helpers lazily. Other
callers (the Agent initializer's addon decompression, the mcp inbox poller)
import from here directly.

Contents: registry record schema (``validate_record`` / ``validate_registry_line``),
JSONL registry read/append (``read_registry`` / ``_append_record``), the
kernel-shipped catalog loader (``_load_catalog``, publicly exposed as
``load_catalog`` for ``agent.py``'s curated-launcher resolution), the secret-safe identity
projection (``read_identities`` and the ``IDENTITY_SAFE_ACCOUNT_KEYS`` allowlist),
boot-time addon decompression (``decompress_addons``), and the system-prompt XML
renderer (``_build_registry_xml``).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

REGISTRY_FILENAME = "mcp_registry.jsonl"
CATALOG_FILENAME = "mcp_catalog.json"

# Match library's name convention: lowercase, dash-separated, bounded length.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
_VALID_TRANSPORTS = {"stdio", "http"}
_MAX_SUMMARY_LEN = 200


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

    catalog_path = Path(__file__).parent.parent / CATALOG_FILENAME
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


def load_catalog() -> dict[str, dict]:
    """Public read-only accessor for the kernel-shipped MCP catalog.

    Returns a fresh deep copy of the cached catalog on every call so a
    caller can never mutate ``_CATALOG_CACHE`` by editing the returned dict
    or a nested list/dict inside a record. This is the authoritative source
    for a curated (``source == "lingtai-curated"``) main-agent ``init.json``
    entry's complete stdio launcher (transport, module args) — the running
    Agent owns the interpreter and import source itself; see
    ``Agent._load_mcp_from_workdir``.
    """
    import copy
    return copy.deepcopy(_load_catalog())


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


# ---------------------------------------------------------------------------
# Account-identity discovery (read-only, secret-safe).
#
# Curated addon servers (telegram, feishu, wechat, whatsapp, ...) persist a
# non-secret identity document to ``system/mcp_identities/<name>.json`` using
# the shared ``lingtai.mcp.identity.v1`` schema. Those documents let an agent
# tell *which* configured account/bot/channel a registered MCP surface
# represents — without reading private config or making network calls. Before
# this reader they were only reachable through each addon's own ``accounts``
# action, invisible from the generic ``plugin(mode="mcp", action="info")`` surface.
#
# This reader is a strict projection, never a passthrough: it surfaces only an
# explicit allowlist of non-secret keys. Even if an identity file on disk were
# to contain a secret-shaped field (a producer bug, a hand-edited file), the
# projection drops it. Tokens, passwords, app secrets, refresh/access tokens,
# headers, and any unrecognized key never propagate.
# ---------------------------------------------------------------------------

IDENTITY_DIRNAME = "mcp_identities"
IDENTITY_SCHEMA = "lingtai.mcp.identity.v1"

# Allowlist of non-secret per-account identity keys. Anything not listed here
# is dropped by the projection. Keep this list secret-free: no *token,
# *secret, *password, api_key, headers, or authorization keys.
IDENTITY_SAFE_ACCOUNT_KEYS: tuple[str, ...] = (
    # generic identity
    "alias",
    "display_name",
    "username",
    "account_id",
    "last_verified_at",
    "is_bot",
    # telegram
    "bot_id",
    "bot_username",
    "bot_display_name",
    # feishu / lark
    "app_id",
    # email-style addons. These fields may identify a human/account and can
    # surface in always-on prompt XML; keep them only when an addon identity
    # writer intentionally publishes them for routing, never for authentication.
    "address",
    "email",
    # non-secret routing / size hints
    "allowed_users_count",
    "allowed_wa_ids_count",
    "contact_count",
    "channel_count",
    "config_source",
)


def _identities_dir(working_dir: Path) -> Path:
    return Path(working_dir) / "system" / IDENTITY_DIRNAME


def _project_account(raw: object) -> dict:
    """Project one account dict down to the non-secret allowlist."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: raw[k]
        for k in IDENTITY_SAFE_ACCOUNT_KEYS
        if k in raw and not isinstance(raw[k], (dict, list))
    }


def read_identities(working_dir: Path) -> dict[str, dict]:
    """Read all per-MCP identity documents, projected to non-secret fields.

    Returns a mapping ``{mcp_name: summary}`` where each ``summary`` is::

        {
            "mcp": <name>,
            "account_count": <int>,
            "accounts": [ {<safe keys only>}, ... ],
            "generated_at": <iso str>,        # when present
            "last_verified_at": <iso str>,    # when present
        }

    Files that are missing, unreadable, malformed, or that do not carry the
    expected ``lingtai.mcp.identity.v1`` schema are skipped silently — identity
    discovery is best-effort and must never break the ``show`` surface.
    """
    base = _identities_dir(working_dir)
    if not base.is_dir():
        return {}

    out: dict[str, dict] = {}
    for path in sorted(base.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("mcp: skipping unreadable identity file %s: %s", path, e)
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema") != IDENTITY_SCHEMA:
            continue

        name = payload.get("mcp") or path.stem
        if not isinstance(name, str) or not name:
            continue

        raw_accounts = payload.get("accounts")
        accounts = [
            _project_account(a) for a in raw_accounts
        ] if isinstance(raw_accounts, list) else []
        accounts = [a for a in accounts if a]

        summary: dict = {
            "mcp": name,
            "account_count": len(accounts),
            "accounts": accounts,
        }
        gen = payload.get("generated_at")
        if isinstance(gen, str):
            summary["generated_at"] = gen
        verified = payload.get("last_verified_at")
        if isinstance(verified, str):
            summary["last_verified_at"] = verified
        out[name] = summary

    return out


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

    import sys
    substitutions = {"{python}": sys.executable}

    def _substitute(value):
        if isinstance(value, str):
            for k, v in substitutions.items():
                if k in value:
                    value = value.replace(k, v)
            return value
        if isinstance(value, list):
            return [_substitute(x) for x in value]
        if isinstance(value, dict):
            return {k: _substitute(v) for k, v in value.items()}
        return value

    for name in addons:
        if name in existing_names:
            skipped.append(name)
            continue
        if name not in catalog:
            unknown.append(name)
            log.warning("mcp: addon %r not found in catalog", name)
            continue
        record = _substitute(dict(catalog[name]))
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


def merge_plugin_launch(record: dict, overrides: dict) -> dict:
    """One plugin launch merge for read-time preflight and Agent activation."""
    return {
        **{key: record[key] for key in ("command", "args", "url", "env", "headers") if key in record},
        "type": record["transport"],
        **overrides,
    }


def validate_declared_mcps(working_dir: Path, declarations: dict, plugins: list[str] | None = None) -> list[dict]:
    """Validate new explicit declarations and derive metadata without writing.

    Registry membership never enables a server. Existing records keep their
    provenance; plugin names use package metadata registered before mounting.
    """
    if not isinstance(declarations, dict):
        raise ValueError("mcp: expected object")
    catalog = _load_catalog()
    if plugins is not None and (not isinstance(plugins, list)
                               or not all(isinstance(path, str) and path for path in plugins)):
        raise ValueError("plugins: expected a list of nonempty package paths")
    from .plugin_registry import read_plugins, to_registry_record
    packages, _, _ = read_plugins(working_dir, plugins or [])
    plugin_specs = {}
    for package in packages:
        for name, spec in package["mcp_specs"].items():
            record, error = to_registry_record(package["name"], name, spec)
            if record is not None:
                if name in plugin_specs and plugin_specs[name] != record:
                    raise ValueError("mcp: multiple declared plugins own the same server name")
                plugin_specs[name] = record
    derived = []
    for name, config in declarations.items():
        if not isinstance(config, dict):
            raise ValueError("mcp declaration must be an object")
        if "type" in config and (not isinstance(config["type"], str) or config["type"] not in {"stdio", "http"}):
            raise ValueError(f"mcp.{name}.type: expected stdio or http")
        for field in ("env", "headers"):
            value = config.get(field, {})
            if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                      for k, v in value.items()):
                raise ValueError(f"mcp.{name}.{field}: expected string mapping")
        if name in catalog:
            if name in plugin_specs:
                raise ValueError("mcp: curated and plugin metadata conflict; choose one server identity")
            derived.append(dict(catalog[name]))
            continue
        if name in plugin_specs:
            record = plugin_specs[name]
            launch = merge_plugin_launch(record, config)
            effective = {**record, **launch, "transport": launch["type"]}
            valid, error = validate_record(effective)
            launcher = effective.get("command" if launch["type"] == "stdio" else "url")
            if not valid or not launcher:
                raise ValueError(f"mcp.{name}: plugin launcher is invalid: {error or 'empty launcher'}")
            derived.append(record)
            continue
        transport = config.get("type", "stdio")
        record = {"name": name, "summary": f"Explicitly declared MCP {name}",
                  "transport": transport, "source": "init-declared"}
        for key in ("command", "args", "url"):
            if key in config:
                record[key] = config[key]
        valid, error = validate_record(record)
        if not valid or not record.get("command" if transport == "stdio" else "url"):
            raise ValueError(f"mcp.{name}: explicit launcher is invalid: {error or 'empty launcher'}")
        derived.append(record)
    return derived


def register_declared_mcps(working_dir: Path, declarations: dict, plugins: list[str] | None = None) -> list[dict]:
    """Append derived metadata only; never replace authored historical records."""
    derived = validate_declared_mcps(working_dir, declarations, plugins)
    catalog = _load_catalog()
    decompress_addons(working_dir, [name for name in declarations if name in catalog])
    records, _ = read_registry(working_dir)
    existing = {record["name"] for record in records}
    for record in derived:
        if record["name"] not in existing:
            _append_record(working_dir, record)
            existing.add(record["name"])
    return derived


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


#: Safe account keys rendered into the model-facing prompt XML. Excludes
#: ``last_verified_at``: it is a volatile re-verification timestamp that
#: changes on plain refresh with no source/config change, which would
#: otherwise invalidate the prompt-cache prefix. It remains in the disk
#: identity document, `read_identities()`, and the ``plugin(mode="mcp", action="info")``
#: diagnostic payload — only this prompt render omits it.
_PROMPT_ACCOUNT_KEYS = tuple(
    k for k in IDENTITY_SAFE_ACCOUNT_KEYS if k != "last_verified_at"
)


def _build_identity_xml(identity: dict | None, indent: str = "    ") -> list[str]:
    """Render a non-secret <identity> block for one MCP, or [] if absent."""
    if not identity:
        return []
    accounts = identity.get("accounts") or []
    if not accounts:
        return []
    lines = [f"{indent}<identity>"]
    for acct in accounts:
        lines.append(f"{indent}  <account>")
        for key in _PROMPT_ACCOUNT_KEYS:
            if key in acct:
                lines.append(
                    f"{indent}    <{key}>{_escape_xml(str(acct[key]))}</{key}>"
                )
        lines.append(f"{indent}  </account>")
    lines.append(f"{indent}</identity>")
    return lines


def _build_registry_xml(
    records: list[dict], identities: dict[str, dict] | None = None
) -> str:
    # Plugin-sourced MCP servers are presented in the ``plugins`` field (as the
    # plugin's own <mcp_names>), not in the vanilla <registered_mcp> catalog.
    visible = [r for r in records if not str(r.get("source", "")).startswith("plugin:")]
    if not visible:
        return (
            "No standalone MCP servers are registered for this agent. Register "
            "one by adding an entry under `mcp` in your init.json (or a record "
            "in mcp_registry.jsonl) and run system(action=\"refresh\"). MCPs "
            "bundled inside Agent Plugins are listed in the `plugins` field. "
            "Read plugin(mode=\"mcp\", action=\"manual\", input={}) or the mcp-manual skill in .library/intrinsic/capabilities/mcp/ "
            "for the full registration contract."
        )
    identities = identities or {}
    lines = [
        "The following MCP servers are registered for this agent. To activate "
        "one, add an entry under `mcp` in your init.json and run "
        "system(action=\"refresh\"). Read plugin(mode=\"mcp\", action=\"manual\", input={}) or the mcp-manual skill in "
        ".library/intrinsic/capabilities/mcp/ for the full registration "
        "contract. When you need install or config instructions for a "
        "specific MCP, fetch its <homepage> README via web_read or "
        "bash + curl as your first step (unless you have other guidance).",
        "",
        "<registered_mcp>",
    ]
    for r in visible:
        lines.append("  <mcp>")
        lines.append(f"    <name>{_escape_xml(r['name'])}</name>")
        lines.append(f"    <summary>{_escape_xml(r['summary'])}</summary>")
        lines.append(f"    <transport>{_escape_xml(r['transport'])}</transport>")
        lines.append(f"    <source>{_escape_xml(r.get('source', ''))}</source>")
        homepage = r.get("homepage")
        if homepage:
            lines.append(f"    <homepage>{_escape_xml(homepage)}</homepage>")
        lines.extend(_build_identity_xml(identities.get(r["name"])))
        lines.append("  </mcp>")
    lines.append("</registered_mcp>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reconciliation (shared by setup and `show` action)
# ---------------------------------------------------------------------------

def _registered_entry(record: dict, identity: dict | None) -> dict:
    """Build one ``registered`` entry, attaching identity only when present."""
    entry = {"name": record["name"], "summary": record["summary"]}
    if identity and identity.get("accounts"):
        entry["identity"] = identity
    return entry
