"""Eigen intrinsic — bare essentials of agent self.

Objects:
    pad — edit/load system/pad.md (agent's working notes)
    context — molt (shed context, keep a briefing)
    name — set true name (once), set/clear nickname

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
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..llm.interface import ToolCallBlock, ToolResultBlock


SNAPSHOT_SCHEMA_VERSION = 1


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

    Notes for the future consultation layer
    ---------------------------------------
    Snapshots are model-agnostic: the consulting LLM is chosen at read
    time, independent of whatever provider/model produced the snapshot.
    Snapshots can therefore be larger than a given consultant's context
    window (e.g. a 1M-token Gemini molt consulted by a 200k-window
    Sonnet). The rule when loading is:

      budget = consulting_model.context_window * 0.8
      if before_tokens <= budget: load full
      else: tail-trim to budget (keep the system entry + the most recent
            entries that fit; older entries fall off the front)

    The 0.8 factor is headroom for the cue prompt, response space, and
    tokenizer drift between providers. No skip threshold — the past
    self degrades gracefully into "recent context with frozen system
    prompt + tool schema," which still produces useful hooks for
    events it can still see and honestly stays silent about events it
    can't.
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
    return t(lang, "eigen.description")


def get_schema(lang: str = "en") -> dict:
    from ..i18n import t
    return {
        "type": "object",
        "properties": {
            "object": {
                "type": "string",
                "enum": ["pad", "context", "name"],
                "description": t(lang, "eigen.object_description"),
            },
            "action": {
                "type": "string",
                "enum": ["edit", "load", "molt", "set", "nickname"],
                "description": t(lang, "eigen.action_description"),
            },
            "content": {
                "type": "string",
                "description": t(lang, "eigen.content_description"),
            },
            "summary": {
                "type": "string",
                "description": t(lang, "eigen.summary_description"),
            },
            "keep_tool_calls": {
                "type": "array",
                "items": {"type": "string"},
                "description": t(lang, "eigen.keep_tool_calls_description"),
            },
        },
        "required": ["object", "action"],
    }


def handle(agent, args: dict) -> dict:
    """Handle eigen tool — pad, context, and name."""
    obj = args.get("object", "")
    action = args.get("action", "")

    if obj == "pad":
        if action == "edit":
            return _pad_edit(agent, args)
        elif action == "load":
            return _pad_load(agent, args)
        else:
            return {"error": f"Unknown pad action: {action}. Use edit or load."}
    elif obj == "context":
        if action == "molt":
            return _context_molt(agent, args)
        else:
            return {"error": f"Unknown context action: {action}. Use molt."}
    elif obj == "name":
        if action == "set":
            return _name_set(agent, args)
        elif action == "nickname":
            return _name_nickname(agent, args)
        else:
            return {"error": f"Unknown name action: {action}. Use set (true name) or nickname."}
    else:
        return {"error": f"Unknown object: {obj}. Use pad, context, or name."}


def _pad_edit(agent, args: dict) -> dict:
    """Write content to system/pad.md and auto-load into system prompt."""
    content = args.get("content", "")

    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    pad_path = system_dir / "pad.md"
    pad_path.write_text(content)

    agent._log("eigen_pad_edit", length=len(content))

    # Auto-load into system prompt
    _pad_load(agent, {})

    return {"status": "ok", "path": str(pad_path), "size_bytes": len(content.encode("utf-8"))}


def _pad_load(agent, args: dict) -> dict:
    """Load system/pad.md into the system prompt."""
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
    agent._token_decomp_dirty = True
    agent._flush_system_prompt()

    agent._log("eigen_pad_load", size_bytes=size_bytes)

    return {
        "status": "ok",
        "path": str(pad_path),
        "size_bytes": size_bytes,
        "content_preview": content[:200],
    }


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
    # Pairs are replayed in the order the agent listed them — the agent
    # chose that order for a reason (chronological, by relevance, leading
    # with the punchline, etc.) and the kernel does not second-guess it.
    keep_pairs: list[tuple] = []  # list of (call_block, result_block) in agent-listed order
    if keep_tool_calls:
        requested = set(keep_tool_calls)
        # First pass: find tool_results whose content carries a matching
        # _tool_call_id, capture the wire id (provider's tool_use_id).
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
        # Second pass: for each matched provider id, find the corresponding
        # tool_call (assistant) block. tool_use and tool_result share the
        # same id on the wire — that's the pairing key.
        call_for_provider_id: dict[str, object] = {}
        for entry in iface_pre.entries:
            for block in entry.content:
                if isinstance(block, ToolCallBlock) and block.id in result_for_provider_id:
                    call_for_provider_id[block.id] = block
        # Refuse if any matched result lacks its companion call block (could
        # happen if enforce_tool_pairing earlier stripped a call from a
        # non-tail assistant entry while the result survived). Better to
        # fail loudly than silently drop a pair the agent asked to keep.
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
        # Build the pair list in the order the agent requested.
        for lt_id in keep_tool_calls:
            pid = provider_id_for_lingtai[lt_id]
            keep_pairs.append((call_for_provider_id[pid], result_for_provider_id[pid]))

    before_tokens = iface_pre.estimate_context_tokens()

    # Snapshot the pre-molt interface to a discrete file so future
    # past-self consultation can load it as cached substrate. Best-effort —
    # a failed snapshot must not block the molt. The molt's own tool_call
    # is excluded: a snapshot represents the agent's mind right before it
    # decided to shed, not the shedding act itself. Use the about-to-be
    # molt_count (current+1) so the snapshot's filename matches the molt
    # it captures.
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

    # Archive the pre-molt chat history:
    #   chat_history.jsonl (current molt) → chat_history_archive.jsonl (all past molts)
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

    # Replay kept tool-call pairs first — chronologically these are older
    # than the molt itself, so they belong before the molt's assistant entry.
    # The wire ids carry over so any provider that pairs by id stays
    # consistent with its own transcript invariants.
    for call_block, result_block in keep_pairs:
        iface.add_assistant_message(content=[call_block])
        iface.add_tool_results([result_block])

    # Replay the molt's own tool_call as the LAST assistant entry. The
    # matching tool_result will be appended by the standard return path
    # (ToolExecutor.make_tool_result_fn → session.send → adapter calls
    # iface.add_tool_results), pairing by the same wire id we just kept.
    iface.add_assistant_message(content=[molt_call_block])

    after_tokens = iface.estimate_context_tokens()

    agent._log(
        "eigen_molt",
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        molt_count=agent._molt_count,
        kept_tool_calls=len(keep_pairs),
    )

    # The faint-memory result. Spare on purpose: the briefing the agent
    # wrote is already visible in its own ToolCallBlock args, so the
    # result here is just the shape of what was lost and where to recover.
    from ..i18n import t
    lang = agent._config.language
    return {
        "status": "ok",
        "note": t(lang, "eigen.molt_result_note"),
        "molt_count": agent._molt_count,
        "tokens_before": before_tokens,
        "tokens_after": after_tokens,
        "tokens_shed": max(0, before_tokens - after_tokens),
        "kept_tool_calls": len(keep_pairs),
        "archive_path": str(archive_path.relative_to(agent._working_dir))
            if archive_path.exists() else None,
    }


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
    from ..llm.interface import ToolCallBlock, ToolResultBlock

    lang = agent._config.language
    if source == "warning_ladder":
        summary = t(lang, "eigen.context_forget_summary")
    elif source == "aed":
        summary = t(lang, "eigen.context_forget_summary_aed").replace("{attempts}", str(attempts))
    else:
        summary = t(lang, "eigen.context_forget_summary_signal").replace("{source}", source)

    if agent._chat is None:
        return {"error": "No active chat session to molt."}

    # Mint a synthesized wire id. Prefix conveys the system origin without
    # confusing wire-format-strict providers (they accept any opaque id
    # that is consistent across the matching tool_use/tool_result pair).
    synth_id = f"toolu_synth_{uuid.uuid4().hex[:16]}"

    # Synthesize the assistant's tool_call. The agent will see this on the
    # next turn the same way it sees its own past tool calls. ``psyche`` is
    # the agent-facing tool name in the wrapper layer; pure-kernel agents
    # see ``eigen`` instead, so pick whichever is registered.
    tool_name = "psyche" if "psyche" in agent._intrinsics else "eigen"
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

    # Snapshot the pre-molt interface. System-initiated path: the
    # synthesized molt call hasn't been spliced into iface_pre yet (it's
    # built above and added to the *fresh* interface below), so no
    # trailing-call exclusion is needed.
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

    # Replay the synthesized molt pair as the opening of the fresh session.
    iface.add_assistant_message(content=[synth_call])

    after_tokens = iface.estimate_context_tokens()

    result_dict = {
        "status": "ok",
        "note": t(lang, "eigen.molt_result_note"),
        "molt_count": agent._molt_count,
        "tokens_before": before_tokens,
        "tokens_after": after_tokens,
        "tokens_shed": max(0, before_tokens - after_tokens),
        "kept_tool_calls": 0,
        "archive_path": str(archive_path.relative_to(agent._working_dir))
            if archive_path.exists() else None,
        "_initiator": "system",
        "_source": source,
    }
    iface.add_tool_results([
        ToolResultBlock(id=synth_id, name=tool_name, content=result_dict)
    ])

    agent._log(
        "eigen_molt",
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        molt_count=agent._molt_count,
        kept_tool_calls=0,
        initiator="system",
        source=source,
    )

    return result_dict
