"""agent m003 — retire init.json substrate/brief prompt overrides.

The init-prompt contract narrows the externally changeable system-prompt
surface to exactly ``base_prompt``, ``covenant``, and ``comment``. Two prompt
sections that used to accept init.json overrides are retired here:

- ``substrate`` — kernel-owned architecture model. The packaged
  ``lingtai/prompts/substrate/substrate.md`` is now the sole source.
- ``brief`` — secretary-written life context. Now sourced solely from
  ``system/brief.md`` on disk, not init.json.

This agent-domain migration preserves non-empty legacy ``substrate`` content
under ``<workdir>/system/migrations/`` and removes the inline + ``_file`` fields
from ``init.json`` for both. ``brief`` is already deprecated and is intentionally
ignored: the migration removes ``brief`` / ``brief_file`` without seeding prompt
content. Active brief context must live in ``system/brief.md``.

Idempotency is provided by the versioned migration runner: this migration runs
at most once per agent workdir version. Within the migration itself, missing
fields are a no-op. Mirrors ``agent_m001_init_procedures_override``.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


def _load_json(path: Path) -> dict | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def _append_agent_event(working_dir: Path, event_type: str, **fields) -> None:
    """Best-effort append to the agent JSONL event log before Agent exists.

    BaseAgent._log normally owns this schema. Agent-domain migrations may run
    during boot/refresh while init.json is being normalized, so they write the
    same minimal event shape directly.
    """
    try:
        try:
            init_data = _load_json(working_dir / "init.json") or {}
        except Exception:
            init_data = {}
        manifest = init_data.get("manifest") if isinstance(init_data.get("manifest"), dict) else {}
        agent_name = manifest.get("agent_name")
        log_dir = working_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        event = {
            "type": event_type,
            "address": working_dir.name,
            "agent_name": agent_name,
            "ts": time.time(),
            **fields,
        }
        with (log_dir / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def _archive_inline(working_dir: Path, field: str, value: str) -> str:
    """Archive non-empty inline legacy content; return the relative archive path."""
    raw = value.encode("utf-8")
    content_hash = hashlib.sha256(raw).hexdigest()
    migrations_dir = working_dir / "system" / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    archive_path = migrations_dir / f"init-{field}-{content_hash}.md"
    archive_path.write_text(value, encoding="utf-8")
    return archive_path.relative_to(working_dir).as_posix()


def migrate_init_prompt_contract(working_dir: Path) -> None:
    """Archive/remove legacy substrate/brief overrides from an agent init.json."""
    init_path = working_dir / "init.json"
    if not init_path.is_file():
        return
    data = _load_json(init_path)
    if data is None:
        raise ValueError(f"{init_path} did not contain a JSON object")

    touched: dict[str, dict] = {}

    for field in ("substrate", "brief"):
        file_key = f"{field}_file"
        has_inline = field in data
        has_file = file_key in data
        if not has_inline and not has_file:
            continue

        inline = data.get(field)
        archive_rel: str | None = None
        archive_source: str | None = None
        if field == "substrate" and isinstance(inline, str) and inline != "":
            try:
                archive_rel = _archive_inline(working_dir, field, inline)
            except OSError as e:
                _append_agent_event(
                    working_dir,
                    "init_prompt_contract_migration_failed",
                    field=field,
                    reason=str(e),
                )
                raise
            archive_source = "inline"

        data.pop(field, None)
        data.pop(file_key, None)
        touched[field] = {
            "archive_path": archive_rel,
            "archive_source": archive_source,
            "inline_removed": has_inline,
            "file_removed": has_file,
            "ignored_deprecated": field == "brief",
        }

    if not touched:
        return

    try:
        _write_json_atomic(init_path, data)
    except OSError as e:
        _append_agent_event(
            working_dir,
            "init_prompt_contract_migration_failed",
            reason=str(e),
            touched=touched,
        )
        raise

    _append_agent_event(
        working_dir,
        "init_prompt_contract_migrated",
        touched=touched,
        field_removed=True,
    )
