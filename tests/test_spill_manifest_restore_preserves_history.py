"""B3 regression: restore/startup must never rewrite persisted spill
manifests in ``chat_history.jsonl``.

Before this fix, ``base_agent/lifecycle.py::_start`` called
``mark_expired_spill_manifests`` on every restore, which rewrote historical
``artifact_state``/``artifact_expired_at``/``warning`` fields (and backfilled
a legacy ``artifact_lifetime``) based on CURRENT sidecar file-existence, then
wrote the mutated JSON back to disk. Separately, ``SessionManager.
restore_chat`` called ``_ensure_spill_manifest_fields``, which backfilled the
same fields into the in-memory ``ChatInterface`` before
``ChatInterface.from_dict``. Both were restore-conditioned canonical
mutations of already-committed ``ToolResultBlock.content`` with no explicit
``summarize`` replacement — forbidden by the provider-context replay
invariant (see ``tool_result_artifacts.py`` module docstring).

This test persists a legacy manifest whose sidecar is absent, then exercises
both restore paths and asserts: the on-disk history JSON is byte-identical
before/after, and all five direct full-history renderers produce identical
output before/after. Current sidecar unavailability may still be observed
by a live, independent, non-provider-facing mechanism (the ``read`` tool's
"Spill artifact expired" message — see
``tests/test_expired_spill_messaging.py``), but never by a rewritten
historical provider item.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock
from lingtai.kernel.session import SessionManager
from lingtai.kernel.tool_result_artifacts import ARTIFACT_MARKER

from lingtai.llm.claude_code.adapter import ClaudeCodeChatSession
from lingtai.llm.interface_converters import (
    to_anthropic,
    to_gemini,
    to_openai,
    to_responses_input,
)


def _legacy_spill_manifest(*, spill_path: str) -> dict:
    """A manifest shaped as it would have been persisted BEFORE issue #192
    (no ``artifact_lifetime``/``artifact_state`` fields) whose sidecar file
    does not exist on disk."""
    return {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "source": "preventive",
        "warning": "Tool result was too large; see the sidecar file.",
        "spill_path": spill_path,
        "spill_path_abs": f"/fake/{spill_path}",
        "tool_name": "bash",
        "tool_call_id": "call_1",
        "original_char_count": 50000,
        "original_byte_count": 50000,
        "cap_chars": 10000,
        "timestamp": "2024-06-01T00:00:00+00:00",
        "preview": "legacy preview...",
        # Deliberately no artifact_lifetime / artifact_state — legacy shape.
    }


def _write_history_jsonl(working_dir: Path, iface: ChatInterface) -> None:
    history_dir = working_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / "chat_history.jsonl"
    lines = [json.dumps(entry, ensure_ascii=False, default=str) for entry in iface.to_dict()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _iface_with_legacy_manifest() -> tuple[ChatInterface, dict]:
    manifest = _legacy_spill_manifest(spill_path="tmp/tool-results/gone.json")
    iface = ChatInterface()
    iface.add_user_message("start")
    iface.add_assistant_message([ToolCallBlock(id="call_1", name="bash", args={})])
    iface.add_tool_results(
        [ToolResultBlock(id="call_1", name="bash", content=manifest)]
    )
    return iface, manifest


def _five_renderer_outputs(iface: ChatInterface) -> dict[str, str]:
    claude_code = ClaudeCodeChatSession(
        adapter=None,
        model="sonnet",
        system_prompt="",
        tools=[],
        interface=iface,
        context_window=100_000,
    )
    return {
        "to_anthropic": json.dumps(to_anthropic(iface), sort_keys=True, default=str),
        "to_openai": json.dumps(to_openai(iface), sort_keys=True, default=str),
        "to_responses_input": json.dumps(to_responses_input(iface), sort_keys=True, default=str),
        "to_gemini": json.dumps(to_gemini(iface), sort_keys=True, default=str),
        "claude_code": claude_code._render_conversation(),
    }


def test_restore_chat_session_manager_does_not_rewrite_legacy_manifest(tmp_path):
    """SessionManager.restore_chat must not backfill/mutate the legacy
    manifest before or after building the interface — content is
    byte/value-identical to what was persisted, and sidecar absence is not
    silently baked into canonical history."""
    iface, manifest = _iface_with_legacy_manifest()
    before_renderers = _five_renderer_outputs(iface)
    messages_before = json.loads(json.dumps(iface.to_dict(), default=str))

    svc = MagicMock()
    svc.model = "test-model"
    svc.create_session.side_effect = lambda **kw: MagicMock(interface=kw["interface"])
    sm = SessionManager(
        llm_service=svc,
        config=AgentConfig(),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "test prompt",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )

    sm.restore_chat({"messages": messages_before})

    restored_iface = sm.chat.interface
    after_renderers = _five_renderer_outputs(restored_iface)

    for key in before_renderers:
        assert after_renderers[key] == before_renderers[key], (
            f"{key} output changed after SessionManager.restore_chat "
            f"(legacy spill manifest must not be rewritten)"
        )

    # The restored manifest itself carries NO backfilled fields — exactly
    # what was persisted, sidecar absence notwithstanding.
    restored_manifest = None
    for entry in restored_iface.entries:
        for block in getattr(entry, "content", []) or []:
            if isinstance(block, ToolResultBlock) and block.id == "call_1":
                restored_manifest = block.content
    assert restored_manifest is not None
    assert restored_manifest == manifest
    assert "artifact_lifetime" not in restored_manifest
    assert "artifact_state" not in restored_manifest
    assert "artifact_expired_at" not in restored_manifest


def test_lifecycle_restore_does_not_rewrite_chat_history_jsonl(tmp_path):
    """The file-based restore path (mirroring base_agent/lifecycle.py::_start,
    minus mark_expired_spill_manifests which has been removed) must leave
    history/chat_history.jsonl byte-identical after reading it back through
    restore_chat, and every renderer output identical before/after."""
    iface, manifest = _iface_with_legacy_manifest()
    _write_history_jsonl(tmp_path, iface)

    history_path = tmp_path / "history" / "chat_history.jsonl"
    before_bytes = history_path.read_bytes()
    before_renderers = _five_renderer_outputs(iface)

    # Mirror lifecycle.py::_start's restore read (mark_expired_spill_manifests
    # call site has been removed — this is now the ENTIRE restore read path).
    messages = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    svc = MagicMock()
    svc.model = "test-model"
    svc.create_session.side_effect = lambda **kw: MagicMock(interface=kw["interface"])
    sm = SessionManager(
        llm_service=svc,
        config=AgentConfig(),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "test prompt",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    sm.restore_chat({"messages": messages})

    # The on-disk file itself was never touched by the restore path.
    after_bytes = history_path.read_bytes()
    assert after_bytes == before_bytes, (
        "history/chat_history.jsonl was rewritten during restore — "
        "no history write is permitted outside an explicit summarize"
    )

    after_renderers = _five_renderer_outputs(sm.chat.interface)
    for key in before_renderers:
        assert after_renderers[key] == before_renderers[key], (
            f"{key} output changed after the lifecycle restore path"
        )
