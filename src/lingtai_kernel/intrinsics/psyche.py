"""Psyche intrinsic — bare essentials of agent self.

Objects:
    pad — edit/load system/pad.md (agent's working notes), append pinned files
    context — molt (shed context, keep a briefing)
    name — set true name (once), set/clear nickname
    lingtai — update/load system/lingtai.md (covenant + character → covenant section)

Internal:
    _context_molt — the shed-and-reload machinery (archive chat_history,
        wipe the wire session, reload pad/lingtai, replay the molt itself
        as a real assistant tool_call entry in the fresh session). The
        agent's own summary lives in that replayed ToolCallBlock's args,
        so the agent sees its own briefing on the next turn the same way
        it sees any past tool_use it made. The synthesized result returned
        from this function is the "faint memory upon waking" — counts and
        archive pointer, not the briefing itself.
    context_forget — system-initiated molt, called by base_agent after the
        warning ladder is exhausted. Synthesizes a ToolCallBlock + matching
        ToolResultBlock and replays both into the fresh session directly.
    _write_molt_snapshot — write a per-molt machine-loadable snapshot of
        the pre-molt ChatInterface to <workdir>/history/snapshots/. Each
        snapshot is a discrete file so it can later be loaded as cached
        substrate for past-self consultation.
    boot — boot-time hook: load lingtai + pad into prompt, register
        post-molt reload. Called from base_agent.__init__ after intrinsics
        are wired.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..llm.interface import ToolCallBlock, ToolResultBlock


SNAPSHOT_SCHEMA_VERSION = 1


def _write_molt_summary(
    agent,
    *,
    summary: str,
    source: str,
    molt_count: int,
    before_tokens: int,
    after_tokens: int,
) -> Path | None:
    """Persist the molt summary to system/summaries/ as a durable retrospective.

    Best-effort — a failed write must not block the molt. Returns the path
    on success, or None if the write failed.

    Filename: molt_<molt_count>_<unix_ts>.md — molt_count first so directory
    listings sort chronologically without parsing.

    Format: a small YAML-ish frontmatter block followed by the summary prose.
    Frontmatter is human-readable (so `cat` is useful) and machine-parseable
    (any future digest-injection layer can split on the leading `---`).

    Complementary to `history/snapshots/snapshot_<count>_<ts>.json`:
    - snapshot = frozen substrate (full ChatInterface for past-self consultation)
    - summary  = curated retrospective (agent-authored prose)
    Both share molt_count so they can be paired by index.
    """
    try:
        summaries_dir = agent._working_dir / "system" / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)

        unix_ts = int(time.time())
        path = summaries_dir / f"molt_{molt_count}_{unix_ts}.md"

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        agent_name = getattr(agent, "agent_name", None) or ""

        frontmatter = (
            "---\n"
            f"molt_count: {molt_count}\n"
            f"created_at: {created_at}\n"
            f"source: {source}\n"
            f"agent_name: {agent_name}\n"
            f"before_tokens: {before_tokens}\n"
            f"after_tokens: {after_tokens}\n"
            f"tokens_shed: {max(0, before_tokens - after_tokens)}\n"
            "---\n\n"
        )

        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(frontmatter + summary, encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as e:
        try:
            agent._log("summary_write_failed", error=str(e))
        except Exception:
            pass
        return None


def _write_molt_snapshot(
    agent,
    iface_pre,
    *,
    before_tokens: int,
    summary: str,
    source: str,
    molt_count: int,
    exclude_trailing_call_id: str | None = None,
) -> Path | None:
    """Serialize the pre-molt ChatInterface to a discrete snapshot file.

    The snapshot is the substrate a future "past self" consultation can
    load — full message history at the moment the agent decided to molt,
    minus the molt's own tool_call (which is meta about the molting
    process, not part of the past self's mind). Returns the snapshot
    path on success, or None if the write failed (best-effort — a
    failed snapshot must not block the molt itself).

    Filename: snapshot_<molt_count>_<unix_ts>.json — molt_count first
    so directory listings sort chronologically without parsing.

    ``exclude_trailing_call_id``: if set, the trailing assistant entry
    whose only ToolCallBlock has this id is dropped from the serialized
    interface. Used by the agent-initiated molt path where the molt's
    own tool_call already sits in the tail entry of iface_pre.
    """
    try:
        snapshots_dir = agent._working_dir / "history" / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        entries = iface_pre.to_dict()
        if exclude_trailing_call_id and entries:
            tail = entries[-1]
            if tail.get("role") == "assistant":
                content = tail.get("content") or []
                if (
                    len(content) == 1
                    and content[0].get("type") == "tool_call"
                    and content[0].get("id") == exclude_trailing_call_id
                ):
                    entries = entries[:-1]

        unix_ts = int(time.time())
        path = snapshots_dir / f"snapshot_{molt_count}_{unix_ts}.json"

        payload = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "molt_count": molt_count,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "before_tokens": before_tokens,
            "agent_name": getattr(agent, "agent_name", None),
            "agent_id": getattr(agent, "_agent_id", None),
            "molt_summary": summary,
            "molt_source": source,
            "interface": entries,
        }

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as e:
        try:
            agent._log("snapshot_write_failed", error=str(e))
        except Exception:
            pass
        return None


def get_description(lang: str = "en") -> str:
    from ..i18n import t
    return t(lang, "psyche.description")


def get_schema(lang: str = "en") -> dict:
    from ..i18n import t
    return {
        "type": "object",
        "properties": {
            "object": {
                "type": "string",
                "enum": ["pad", "context", "name", "lingtai"],
                "description": t(lang, "psyche.object_description"),
            },
            "action": {
                "type": "string",
                "description": t(lang, "psyche.action_description"),
            },
            "content": {
                "type": "string",
                "description": t(lang, "psyche.content_description"),
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": t(lang, "psyche.files_description"),
            },
            "summary": {
                "type": "string",
                "description": t(lang, "psyche.summary_description"),
            },
            "keep_tool_calls": {
                "type": "array",
                "items": {"type": "string"},
                "description": t(lang, "psyche.keep_tool_calls_description"),
            },
        },
        "required": ["object", "action"],
        "allOf": [
            {"if": {"properties": {"object": {"const": "lingtai"}}},
             "then": {"properties": {"action": {"enum": ["update", "load"]}}}},
            {"if": {"properties": {"object": {"const": "pad"}}},
             "then": {"properties": {"action": {"enum": ["edit", "load", "append"]}}}},
            {"if": {"properties": {"object": {"const": "context"}}},
             "then": {"properties": {"action": {"enum": ["molt"]}}}},
            {"if": {"properties": {"object": {"const": "name"}}},
             "then": {"properties": {"action": {"enum": ["set", "nickname"]}}}},
        ],
    }


_VALID_ACTIONS: dict[str, set[str]] = {
    "lingtai": {"update", "load"},
    "pad": {"edit", "load", "append"},
    "context": {"molt"},
    "name": {"set", "nickname"},
}


def handle(agent, args: dict) -> dict:
    """Handle psyche tool — dispatch to (object, action) handler."""
    obj = args.get("object", "")
    action = args.get("action", "")

    valid = _VALID_ACTIONS.get(obj)
    if valid is None:
        return {
            "error": f"Unknown object: {obj!r}. "
                     f"Must be one of: {', '.join(sorted(_VALID_ACTIONS))}."
        }
    if action not in valid:
        return {
            "error": f"Invalid action {action!r} for {obj}. "
                     f"Valid actions: {', '.join(sorted(valid))}."
        }

    method_name = f"_{obj}_{action}"
    method = globals().get(method_name)
    if method is None:
        return {"error": f"Internal: handler {method_name} not found."}
    return method(agent, args)


# ---------------------------------------------------------------------------
# Lingtai (identity/character) actions
# ---------------------------------------------------------------------------


def _lingtai_update(agent, args: dict) -> dict:
    """Write content to system/lingtai.md and auto-load into system prompt."""
    content = args.get("content", "")
    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    lingtai_path = system_dir / "lingtai.md"
    lingtai_path.write_text(content)

    agent._log("psyche_lingtai_update", length=len(content))

    _lingtai_load(agent, {})
    return {"status": "ok", "path": str(lingtai_path)}


def _lingtai_load(agent, _args: dict) -> dict:
    """Combine system/covenant.md + system/lingtai.md and write to covenant prompt section."""
    system_dir = agent._working_dir / "system"
    covenant_path = system_dir / "covenant.md"
    lingtai_path = system_dir / "lingtai.md"

    covenant = covenant_path.read_text() if covenant_path.is_file() else ""
    character = lingtai_path.read_text() if lingtai_path.is_file() else ""

    parts = [p for p in [covenant, character] if p.strip()]
    combined = "\n\n".join(parts)

    if combined.strip():
        agent._prompt_manager.write_section(
            "covenant", combined, protected=True,
        )
    else:
        agent._prompt_manager.delete_section("covenant")
    agent._token_decomp_dirty = True
    agent._flush_system_prompt()

    agent._log("psyche_lingtai_load", size_bytes=len(combined.encode("utf-8")))

    return {
        "status": "ok",
        "size_bytes": len(combined.encode("utf-8")),
        "content_preview": combined[:200],
    }


# ---------------------------------------------------------------------------
# Pad actions — edit (with optional files=), load (with append-files layering), append
# ---------------------------------------------------------------------------


def _pad_edit(agent, args: dict) -> dict:
    """Write content + optional file imports to pad.md and reload prompt.

    To clear the pad explicitly, pass content="" (i.e. include the key).
    Calling edit with no content key and no files is rejected — that's
    almost always an LLM mistake.
    """
    if "content" not in args and not args.get("files"):
        return {"error": "Provide content (use empty string to clear), files, or both."}

    content = args.get("content", "")
    files = args.get("files") or []

    parts = [content] if content else []

    not_found: list[str] = []
    for i, fpath in enumerate(files, start=1):
        if os.path.isabs(fpath):
            resolved = Path(fpath)
        else:
            resolved = agent._working_dir / fpath
        if not resolved.is_file():
            not_found.append(fpath)
            continue
        file_content = resolved.read_text()
        parts.append(f"[file-{i}]\n{file_content}")

    if not_found:
        return {"error": f"Files not found: {', '.join(not_found)}"}

    combined = "\n\n".join(parts)

    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    pad_path = system_dir / "pad.md"
    pad_path.write_text(combined)

    agent._log("psyche_pad_edit", length=len(combined), files=len(files))

    _pad_load(agent, {})

    return {"status": "ok", "path": str(pad_path), "size_bytes": len(combined.encode("utf-8"))}


def _pad_load(agent, args: dict) -> dict:
    """Load system/pad.md + appended reference files into the prompt."""
    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    pad_path = system_dir / "pad.md"
    if not pad_path.is_file():
        pad_path.write_text("")

    content = pad_path.read_text()
    size_bytes = len(content.encode("utf-8"))

    if content.strip():
        agent._prompt_manager.write_section("pad", content)
    else:
        agent._prompt_manager.delete_section("pad")

    # Append-files layering — pinned read-only reference appended to pad section
    append_files = _load_append_list(agent)
    append_meta: dict = {}
    if append_files:
        append_content, not_found = _read_append_content(agent, append_files)
        if append_content:
            existing = agent._prompt_manager.read_section("pad") or ""
            combined = existing + "\n\n---\n# 📎 Reference (read-only)\n\n" + append_content
            agent._prompt_manager.write_section("pad", combined)
        if not_found:
            append_meta["append_not_found"] = not_found
        append_meta["append_files"] = append_files
        append_meta["append_count"] = len(append_files)

    agent._token_decomp_dirty = True
    agent._flush_system_prompt()

    agent._log("psyche_pad_load", size_bytes=size_bytes)

    result: dict = {
        "status": "ok",
        "path": str(pad_path),
        "size_bytes": size_bytes,
        "content_preview": content[:200],
    }
    result.update(append_meta)
    return result


# ---------------------------------------------------------------------------
# Pad append — pin files as read-only reference
# ---------------------------------------------------------------------------

_APPEND_LIST_PATH = "system/pad_append.json"
_APPEND_TOKEN_LIMIT = 100_000


def _append_list_file(agent) -> Path:
    return agent._working_dir / _APPEND_LIST_PATH


def _load_append_list(agent) -> list[str]:
    """Read the persisted append file list (empty list if missing)."""
    path = _append_list_file(agent)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return [str(p) for p in data]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_append_list(agent, files: list[str]) -> None:
    """Persist the append file list to disk."""
    path = _append_list_file(agent)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(files, ensure_ascii=False))


def _resolve_path(agent, fpath: str) -> Path:
    if os.path.isabs(fpath):
        return Path(fpath)
    return agent._working_dir / fpath


def _read_append_content(agent, files: list[str]) -> tuple[str, list[str]]:
    """Read all append files. Returns (combined content, not_found list)."""
    parts: list[str] = []
    not_found: list[str] = []
    for fpath in files:
        resolved = _resolve_path(agent, fpath)
        if not resolved.is_file():
            not_found.append(fpath)
            continue
        parts.append(f"[append: {fpath}]\n{resolved.read_text()}")
    return "\n\n".join(parts), not_found


def _is_text_file(path: Path, sample_size: int = 8192) -> bool:
    """Check if a file is a text file by reading the first chunk."""
    try:
        chunk = path.read_bytes()[:sample_size]
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _pad_append(agent, args: dict) -> dict:
    """Set the list of files pinned as read-only pad reference.

    Pass files=[] to clear. Persisted to system/pad_append.json.
    Automatically reloads pad after updating the list. Only text files
    are accepted.
    """
    files = args.get("files")
    if files is None:
        # No files param — return current list
        current = _load_append_list(agent)
        return {"status": "ok", "files": current, "count": len(current)}

    not_found: list[str] = []
    not_text: list[str] = []
    for fpath in files:
        resolved = _resolve_path(agent, fpath)
        if not resolved.is_file():
            not_found.append(fpath)
        elif not _is_text_file(resolved):
            not_text.append(fpath)
    if not_found:
        return {"error": f"Files not found: {', '.join(not_found)}"}
    if not_text:
        return {"error": f"Only text files are accepted. Binary files: {', '.join(not_text)}"}

    if files:
        from ..token_counter import count_tokens
        combined, _ = _read_append_content(agent, files)
        tokens = count_tokens(combined)
        if tokens > _APPEND_TOKEN_LIMIT:
            return {
                "error": f"Append files total {tokens:,} tokens, "
                         f"exceeding the {_APPEND_TOKEN_LIMIT:,} token limit. "
                         f"Reduce the number or size of files.",
            }

    _save_append_list(agent, files)
    _pad_load(agent, {})

    action = "cleared" if not files else "set"
    return {"status": "ok", "action": action, "files": files, "count": len(files)}


# ---------------------------------------------------------------------------
# Context actions — molt
# ---------------------------------------------------------------------------


def _context_molt(agent, args: dict) -> dict:
    """Agent molt: replay the molt's own tool_call as the opening assistant
    entry of the fresh session, return a "faint memory" result.

    The agent's summary lives in ``args.summary`` of its own ToolCallBlock.
    After the wipe we replay that ToolCallBlock into the fresh interface,
    so on the next turn the agent reads its own briefing exactly as it
    reads any past tool_use it has made. The dict returned by this function
    becomes the matching ToolResultBlock's content (paired by the standard
    return path: ToolExecutor.make_tool_result → session.send → adapter
    appends user-role tool_result to the fresh interface). The result is
    deliberately spare — counts and archive pointer, the faint shape of
    "you just woke up; the dream is gone but the briefing you wrote stands."

    ``_tc_id`` is injected by ``base_agent._dispatch_tool`` and carries the
    wire tool_use_id of the molt call. We use it to locate the original
    ToolCallBlock in the pre-molt interface so the replayed assistant entry
    keeps the agent's verbatim args (summary, keep_tool_calls, reasoning).

    Optional ``keep_tool_calls`` is a list of LingTai-issued tool-call ids
    (the ``_tool_call_id`` field stamped into every tool-result content by
    LLMService.make_tool_result). Each named pair survives the wipe and is
    replayed BEFORE the molt's own assistant entry, so chronologically the
    fresh interface reads: kept pairs (older) → molt call (just made) →
    faint-memory result (returned by this fn). Validation runs BEFORE any
    mutation: if any id is unknown the molt is refused and the molt count
    is not incremented.
    """
    summary = args.get("summary")
    if summary is None:
        return {"error": "summary is required — write a briefing to your future self."}
    if not summary.strip():
        return {"error": "summary cannot be empty — write what you need to remember."}

    if agent._chat is None:
        return {"error": "No active chat session to molt."}

    tc_id = args.get("_tc_id")
    if not tc_id:
        # Should never happen for an agent-initiated molt — base_agent always
        # injects _tc_id. Refuse without consuming a molt.
        return {
            "error": (
                "Internal: missing _tc_id for molt. The molt could not be "
                "replayed as a real tool pair into the fresh session. "
                "Molt refused; molt count unchanged."
            ),
        }

    keep_tool_calls = args.get("keep_tool_calls") or []
    if keep_tool_calls and not isinstance(keep_tool_calls, list):
        return {"error": "keep_tool_calls must be a list of LingTai tool-call ids (strings)."}

    iface_pre = agent._chat.interface

    # Locate the molt's own ToolCallBlock in the pre-molt interface so we
    # can replay it verbatim into the fresh session. Walk in reverse — the
    # molt was just emitted, it's in the tail assistant entry.
    molt_call_block = None
    for entry in reversed(iface_pre.entries):
        if entry.role != "assistant":
            continue
        for block in entry.content:
            if isinstance(block, ToolCallBlock) and block.id == tc_id:
                molt_call_block = block
                break
        if molt_call_block is not None:
            break
    if molt_call_block is None:
        return {
            "error": (
                "Internal: could not find the molt's own tool_call in the "
                "live interface. Molt refused; molt count unchanged."
            ),
        }

    # Validate keep-list BEFORE any state mutation so a typo doesn't
    # consume a molt. Walk the live interface, harvest LingTai-issued ids
    # from tool_result content, and confirm every requested id is present.
    keep_pairs: list[tuple] = []  # list of (call_block, result_block) in agent-listed order
    if keep_tool_calls:
        requested = set(keep_tool_calls)
        provider_id_for_lingtai: dict[str, str] = {}
        result_for_provider_id: dict[str, object] = {}
        for entry in iface_pre.entries:
            for block in entry.content:
                if not isinstance(block, ToolResultBlock):
                    continue
                content = block.content
                if not isinstance(content, dict):
                    continue
                lt_id = content.get("_tool_call_id")
                if lt_id in requested:
                    provider_id_for_lingtai[lt_id] = block.id
                    result_for_provider_id[block.id] = block
        unmatched = [tid for tid in keep_tool_calls if tid not in provider_id_for_lingtai]
        if unmatched:
            return {
                "error": (
                    "Some keep_tool_calls ids were not found in the current "
                    "chat history. Molt refused; molt count unchanged. "
                    "Retry with a corrected list."
                ),
                "unmatched_ids": unmatched,
                "matched_count": len(provider_id_for_lingtai),
            }
        call_for_provider_id: dict[str, object] = {}
        for entry in iface_pre.entries:
            for block in entry.content:
                if isinstance(block, ToolCallBlock) and block.id in result_for_provider_id:
                    call_for_provider_id[block.id] = block
        missing_calls = [
            lt_id for lt_id in keep_tool_calls
            if call_for_provider_id.get(provider_id_for_lingtai[lt_id]) is None
        ]
        if missing_calls:
            return {
                "error": (
                    "Some keep_tool_calls ids have a tool_result in history "
                    "but no matching tool_call (the call block was likely "
                    "stripped). Molt refused; molt count unchanged."
                ),
                "missing_call_ids": missing_calls,
            }
        for lt_id in keep_tool_calls:
            pid = provider_id_for_lingtai[lt_id]
            keep_pairs.append((call_for_provider_id[pid], result_for_provider_id[pid]))

    before_tokens = iface_pre.estimate_context_tokens()

    # Snapshot the pre-molt interface to a discrete file so future
    # past-self consultation can load it as cached substrate. Best-effort.
    _write_molt_snapshot(
        agent, iface_pre,
        before_tokens=before_tokens,
        summary=summary,
        source="agent",
        molt_count=agent._molt_count + 1,
        exclude_trailing_call_id=tc_id,
    )

    # Wipe context
    agent._session._chat = None
    agent._session._interaction_id = None

    # Reset molt warnings
    if hasattr(agent._session, "_compaction_warnings"):
        agent._session._compaction_warnings = 0

    # Track molt count and persist to manifest
    agent._molt_count += 1
    agent._workdir.write_manifest(agent._build_manifest())

    # Archive the pre-molt chat history.
    history_dir = agent._working_dir / "history"
    history_dir.mkdir(exist_ok=True)
    current_path = history_dir / "chat_history.jsonl"
    archive_path = history_dir / "chat_history_archive.jsonl"
    try:
        if current_path.is_file():
            with open(archive_path, "a") as archive:
                archive.write(current_path.read_text())
            current_path.unlink()
    except OSError:
        pass

    # Reset soul mirror session
    from .soul import reset_soul_session
    reset_soul_session(agent)

    # Post-molt hooks — reload character/pad into prompt manager BEFORE new session
    for cb in getattr(agent, "_post_molt_hooks", []):
        try:
            cb()
        except Exception:
            pass

    # Now create fresh session with updated prompt manager
    agent._session.ensure_session()

    iface = agent._session._chat.interface

    # Replay kept tool-call pairs first (older than the molt itself).
    for call_block, result_block in keep_pairs:
        iface.add_assistant_message(content=[call_block])
        iface.add_tool_results([result_block])

    # Replay the molt's own tool_call as the LAST assistant entry. The
    # matching tool_result will be appended by the standard return path.
    iface.add_assistant_message(content=[molt_call_block])

    after_tokens = iface.estimate_context_tokens()

    agent._log(
        "psyche_molt",
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        molt_count=agent._molt_count,
        kept_tool_calls=len(keep_pairs),
    )

    # Persist the agent's retrospective to system/summaries/. Best-effort —
    # a failed write surfaces as summary_path=None but does not block the molt.
    summary_path = _write_molt_summary(
        agent,
        summary=summary,
        source="agent",
        molt_count=agent._molt_count,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
    )

    # The faint-memory result.
    from ..i18n import t
    lang = agent._config.language
    return {
        "status": "ok",
        "note": t(lang, "psyche.molt_result_note"),
        "molt_count": agent._molt_count,
        "tokens_before": before_tokens,
        "tokens_after": after_tokens,
        "tokens_shed": max(0, before_tokens - after_tokens),
        "kept_tool_calls": len(keep_pairs),
        "archive_path": str(archive_path.relative_to(agent._working_dir))
            if archive_path.exists() else None,
        "summary_path": str(summary_path.relative_to(agent._working_dir))
            if summary_path is not None else None,
    }


# ---------------------------------------------------------------------------
# Name actions
# ---------------------------------------------------------------------------


def _name_set(agent, args: dict) -> dict:
    """Set the agent's true name."""
    name = args.get("content", "").strip()
    if not name:
        return {"error": "Name cannot be empty. Provide your chosen name in 'content'."}
    try:
        agent.set_name(name)
    except RuntimeError as e:
        return {"error": str(e)}
    return {"status": "ok", "name": name}


def _name_nickname(agent, args: dict) -> dict:
    """Set or change the agent's nickname (别名). Mutable."""
    nickname = args.get("content", "").strip()
    agent.set_nickname(nickname)
    return {"status": "ok", "nickname": nickname or None}


# ---------------------------------------------------------------------------
# System-initiated molt
# ---------------------------------------------------------------------------


def context_forget(agent, *, source: str = "warning_ladder", attempts: int = 0) -> dict:
    """Forced molt with a system-authored summary.

    Called by base_agent from three paths:
      - source="warning_ladder" (default): post-molt-warning exhaustion
      - source="aed": after max AED retries, before declaring ASLEEP
      - source=<name>: a .forget signal file dropped externally (karma-gated)

    Same archive-and-rebuild machinery as agent-called molt, but the molt
    pair is synthesized end-to-end here: we mint a wire id, build a
    ToolCallBlock whose args carry the system-authored summary, and append
    BOTH the call entry and its matching result entry into the fresh
    interface directly (there is no executor following us). On the next
    turn the agent reads this synthesized pair the same way it reads any
    of its own past tool calls — surface honesty about the molt being
    system-initiated lives in the args (``_initiator: "system"``) and the
    result note.
    """
    import uuid
    from ..i18n import t

    lang = agent._config.language
    if source == "warning_ladder":
        summary = t(lang, "psyche.context_forget_summary")
    elif source == "aed":
        summary = t(lang, "psyche.context_forget_summary_aed").replace("{attempts}", str(attempts))
    else:
        summary = t(lang, "psyche.context_forget_summary_signal").replace("{source}", source)

    if agent._chat is None:
        return {"error": "No active chat session to molt."}

    synth_id = f"toolu_synth_{uuid.uuid4().hex[:16]}"
    tool_name = "psyche"
    synth_call = ToolCallBlock(
        id=synth_id,
        name=tool_name,
        args={
            "object": "context",
            "action": "molt",
            "summary": summary,
            "_initiator": "system",
            "_source": source,
        },
    )

    iface_pre = agent._chat.interface
    before_tokens = iface_pre.estimate_context_tokens()

    _write_molt_snapshot(
        agent, iface_pre,
        before_tokens=before_tokens,
        summary=summary,
        source=source,
        molt_count=agent._molt_count + 1,
        exclude_trailing_call_id=None,
    )

    # Wipe context
    agent._session._chat = None
    agent._session._interaction_id = None

    if hasattr(agent._session, "_compaction_warnings"):
        agent._session._compaction_warnings = 0

    agent._molt_count += 1
    agent._workdir.write_manifest(agent._build_manifest())

    history_dir = agent._working_dir / "history"
    history_dir.mkdir(exist_ok=True)
    current_path = history_dir / "chat_history.jsonl"
    archive_path = history_dir / "chat_history_archive.jsonl"
    try:
        if current_path.is_file():
            with open(archive_path, "a") as archive:
                archive.write(current_path.read_text())
            current_path.unlink()
    except OSError:
        pass

    from .soul import reset_soul_session
    reset_soul_session(agent)

    for cb in getattr(agent, "_post_molt_hooks", []):
        try:
            cb()
        except Exception:
            pass

    agent._session.ensure_session()
    iface = agent._session._chat.interface

    iface.add_assistant_message(content=[synth_call])

    after_tokens = iface.estimate_context_tokens()

    # Persist the system-authored summary to system/summaries/. Best-effort —
    # source field captures origin (warning_ladder / aed / signal name) so
    # readers can filter out non-agent-authored entries.
    summary_path = _write_molt_summary(
        agent,
        summary=summary,
        source=source,
        molt_count=agent._molt_count,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
    )

    result_dict = {
        "status": "ok",
        "note": t(lang, "psyche.molt_result_note"),
        "molt_count": agent._molt_count,
        "tokens_before": before_tokens,
        "tokens_after": after_tokens,
        "tokens_shed": max(0, before_tokens - after_tokens),
        "kept_tool_calls": 0,
        "archive_path": str(archive_path.relative_to(agent._working_dir))
            if archive_path.exists() else None,
        "summary_path": str(summary_path.relative_to(agent._working_dir))
            if summary_path is not None else None,
        "_initiator": "system",
        "_source": source,
    }
    iface.add_tool_results([
        ToolResultBlock(id=synth_id, name=tool_name, content=result_dict)
    ])

    agent._log(
        "psyche_molt",
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        molt_count=agent._molt_count,
        kept_tool_calls=0,
        initiator="system",
        source=source,
    )

    return result_dict


# ---------------------------------------------------------------------------
# Boot hook — replaces wrapper's setup()
# ---------------------------------------------------------------------------


def boot(agent) -> None:
    """Boot-time hook: load lingtai + pad into the prompt, register post-molt
    reload. Called from base_agent.__init__ after intrinsics are wired."""
    _pad_load(agent, {})
    _lingtai_load(agent, {})
    if not hasattr(agent, "_post_molt_hooks"):
        agent._post_molt_hooks = []
    agent._post_molt_hooks.append(
        lambda: (_lingtai_load(agent, {}), _pad_load(agent, {}))
    )
