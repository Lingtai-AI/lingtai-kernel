"""Focused contract evidence for the context-ownership redesign (#1073)."""
from __future__ import annotations

import json

import pytest

from lingtai.agent import Agent
from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock
from lingtai.tools import context as context_tool
from lingtai.tools import psyche as psyche_tool
from lingtai.tools.system.summarize import (
    SUMMARY_STATUS_DONE,
    SUMMARY_STATUS_PENDING,
)
from tests._service_helpers import make_gemini_mock_service as make_mock_service


def _agent(tmp_path, **kwargs):
    return Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=tmp_path / "test",
        **kwargs,
    )


def _actions(module) -> list[str]:
    return module.get_schema("en")["properties"]["action"]["enum"]


def test_public_action_sets_are_the_locked_inventories():
    # The four durable domains now share one read-only root.
    assert _actions(psyche_tool) == [
        "pad", "lingtai", "knowledge", "skills", "settings", "manual",
    ]
    assert psyche_tool.ACTION_ORDER == (
        "pad", "lingtai", "knowledge", "skills", "settings", "manual",
    )
    assert _actions(context_tool) == [
        "molt", "summarize", "rebuild", "settings", "manual",
    ]


def test_retired_actions_are_rejected_without_aliases(tmp_path):
    agent = _agent(tmp_path)
    try:
        for root, action in (
            # Retired domain-mutation actions, now unknown on the one root.
            ("psyche", "update"),
            ("psyche", "load"),
            ("psyche", "edit"),
            ("psyche", "append"),
            ("psyche", "info"),
            ("context", "load"),
        ):
            result = agent._intrinsics[root]({"action": action, "input": {}})
            assert "error" in result
            assert f"Unknown {root} action" in result["error"]
    finally:
        agent.stop(timeout=1.0)


def test_manual_only_lingtai_has_a_strict_ltp_v2_envelope(tmp_path):
    """The 灵台 signpost survives as ``psyche(action='lingtai')``."""
    schema = psyche_tool.get_schema("en")
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert "lingtai" in schema["properties"]["action"]["enum"]
    lingtai_branch = next(
        cond["then"]["properties"]["input"]
        for cond in schema["allOf"]
        if cond["if"]["properties"]["action"]["const"] == "lingtai"
    )
    assert lingtai_branch == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    agent = _agent(tmp_path)
    try:
        result = agent._intrinsics["psyche"](
            {"action": "lingtai", "input": {}, "reasoning": "inspect", "summarize": False}
        )
        assert result["status"] in {"ok", "degraded"}
        bad = agent._intrinsics["psyche"](
            {"action": "lingtai", "input": {"content": "not allowed"}}
        )
        assert bad["status"] == "failed"
        assert bad["error_code"] == "INVALID_ARGUMENT"
    finally:
        agent.stop(timeout=1.0)


def test_file_write_and_edit_change_disk_but_never_hot_load_prompt(tmp_path):
    agent = _agent(tmp_path)
    try:
        agent._prompt_manager.write_section("pad", "CURRENT-PROMPT")
        file_call = agent._tool_handlers["file"]
        path = agent._working_dir / "system" / "pad.md"

        result = file_call({
            "action": "write",
            "input": {"file_path": str(path), "content": "DURABLE-ONE"},
            "reasoning": "update durable pad",
        })
        assert result["status"] == "ok"
        assert path.read_text(encoding="utf-8") == "DURABLE-ONE"
        assert agent._prompt_manager.read_section("pad") == "CURRENT-PROMPT"

        result = file_call({
            "action": "edit",
            "input": {
                "file_path": str(path),
                "old_string": "DURABLE-ONE",
                "new_string": "DURABLE-TWO",
                "replace_all": None,
            },
            "reasoning": "exact durable edit",
        })
        assert result["status"] == "ok"
        assert path.read_text(encoding="utf-8") == "DURABLE-TWO"
        assert agent._prompt_manager.read_section("pad") == "CURRENT-PROMPT"
    finally:
        agent.stop(timeout=1.0)


def test_durable_pinned_reference_edit_does_not_hot_load_but_rebuild_composes(tmp_path):
    """``pad.append`` is gone; the durable list is edited as ordinary text."""
    agent = _agent(tmp_path)
    try:
        agent._prompt_manager.write_section("pad", "CURRENT-PROMPT")
        ref = agent._working_dir / "reference.txt"
        ref.write_text("NEW-PINNED-REFERENCE", encoding="utf-8")

        # Writing the durable source directly must NOT hot-load the prompt.
        system = agent._working_dir / "system"
        system.mkdir(exist_ok=True)
        (system / "pad.md").write_text("PAD-BODY", encoding="utf-8")
        (system / "pad_append.json").write_text(
            json.dumps(["reference.txt"]), encoding="utf-8"
        )
        assert agent._prompt_manager.read_section("pad") == "CURRENT-PROMPT"

        # One explicit rebuild composes body plus the pinned reference.
        agent._reconstruct_context()
        composed = agent._prompt_manager.read_section("pad")
        assert "PAD-BODY" in composed
        assert "NEW-PINNED-REFERENCE" in composed
    finally:
        agent.stop(timeout=1.0)


class _OrderedAgent:
    def __init__(self, working_dir, iface):
        self._working_dir = working_dir
        self.agent_name = "ordered"
        self._summarize_notification_threshold = 3000
        self.events: list[tuple[str, str | None]] = []
        self._chat = self._Chat(self, iface)

    class _Chat:
        def __init__(self, owner, iface):
            self.owner = owner
            self.interface = iface

        def request_history_rebuild(self, reason=""):
            self.owner.events.append(("provider", _history_status(self.interface)))
            return True

    def _reconstruct_context(self):
        self.events.append(("compose", _history_status(self._chat.interface)))

    def _log(self, event, **fields):
        pass


def _history_status(iface) -> str | None:
    for entry in iface._entries:
        for block in entry.content:
            if isinstance(block, ToolResultBlock):
                content = block.content
                if isinstance(content, dict):
                    return content.get("status")
                return "raw"
    return None


def _history(tool_id: str | None = None) -> ChatInterface:
    iface = ChatInterface()
    if tool_id is not None:
        iface.add_assistant_message([ToolCallBlock(id=tool_id, name="file", args={})])
        iface.add_tool_results([
            ToolResultBlock(id=tool_id, name="file", content="large raw result")
        ])
    return iface


def test_rebuild_recomposes_before_new_summaries_and_provider_replay(tmp_path):
    iface = _history("tc-new")
    agent = _OrderedAgent(tmp_path, iface)
    result = context_tool.handle(agent, {
        "action": "rebuild",
        "input": {"items": [{"tool_call_id": "tc-new", "summary": "digested"}]},
    })
    assert result["status"] == "ok"
    assert result["marked_done"] == ["tc-new"]
    assert agent.events == [("compose", "raw"), ("provider", SUMMARY_STATUS_DONE)]


def test_bare_rebuild_recomposes_before_pending_summaries_and_replay(tmp_path):
    iface = _history("tc-pending")
    agent = _OrderedAgent(tmp_path, iface)
    context_tool.handle(agent, {
        "action": "summarize",
        "input": {"items": [{"tool_call_id": "tc-pending", "summary": "digested"}]},
    })
    assert _history_status(iface) == SUMMARY_STATUS_PENDING
    agent.events.clear()

    result = context_tool.handle(agent, {"action": "rebuild", "input": {}})
    assert result["status"] == "ok"
    assert result["marked_done"] == ["tc-pending"]
    assert agent.events == [
        ("compose", SUMMARY_STATUS_PENDING),
        ("provider", SUMMARY_STATUS_DONE),
    ]


def test_bare_rebuild_with_zero_pending_still_recomposes_and_replays(tmp_path):
    agent = _OrderedAgent(tmp_path, _history())
    result = context_tool.handle(agent, {"action": "rebuild", "input": {}})
    assert result["status"] == "ok"
    assert result["marked_done"] == []
    assert agent.events == [("compose", None), ("provider", None)]


def test_canonical_reconstruction_rereads_all_durable_prompt_sources(tmp_path):
    agent = _agent(tmp_path)
    try:
        system = agent._working_dir / "system"
        system.mkdir(exist_ok=True)
        (system / "base_prompt.md").write_text("BASE-SOURCE", encoding="utf-8")
        (system / "covenant.md").write_text("COVENANT-SOURCE", encoding="utf-8")
        (system / "lingtai.md").write_text("LINGTAI-SOURCE", encoding="utf-8")
        (system / "rules.md").write_text("RULES-SOURCE", encoding="utf-8")
        (system / "pad.md").write_text("PAD-SOURCE", encoding="utf-8")
        (system / "brief.md").write_text("BRIEF-SOURCE", encoding="utf-8")
        ref = agent._working_dir / "ref.txt"
        ref.write_text("PINNED-SOURCE", encoding="utf-8")
        (system / "pad_append.json").write_text('["ref.txt"]', encoding="utf-8")
        for section in ("covenant", "character", "rules", "pad", "brief"):
            agent._prompt_manager.write_section(section, f"STALE-{section}")
        agent._base_prompt = "STALE-BASE"

        publish_count = 0
        original_flush = agent._flush_system_prompt

        def capture_flush():
            nonlocal publish_count
            publish_count += 1
            return original_flush()

        agent._flush_system_prompt = capture_flush
        agent._reconstruct_context()

        assert publish_count == 1
        assert agent._base_prompt == "BASE-SOURCE"
        assert agent._prompt_manager.read_section("covenant") == "COVENANT-SOURCE"
        assert "LINGTAI-SOURCE" in agent._prompt_manager.read_section("character")
        assert agent._prompt_manager.read_section("rules") == "RULES-SOURCE"
        assert "PAD-SOURCE" in agent._prompt_manager.read_section("pad")
        assert "PINNED-SOURCE" in agent._prompt_manager.read_section("pad")
        assert agent._prompt_manager.read_section("brief") == "BRIEF-SOURCE"
        rendered = (system / "system.md").read_text(encoding="utf-8")
        for sentinel in (
            "BASE-SOURCE", "COVENANT-SOURCE", "LINGTAI-SOURCE",
            "RULES-SOURCE", "PAD-SOURCE", "PINNED-SOURCE", "BRIEF-SOURCE",
        ):
            assert sentinel in rendered

        # Recomposition is replacement, not overlay: removing a durable source
        # must remove its stale in-memory section on the next full rebuild.
        (system / "covenant.md").unlink()
        agent._reconstruct_context()
        assert publish_count == 2
        assert agent._prompt_manager.read_section("covenant") is None
    finally:
        agent.stop(timeout=1.0)


def test_existing_invalid_init_fails_before_prompt_mutation_or_replay(tmp_path):
    agent = _agent(tmp_path)
    try:
        agent._prompt_manager.write_section("comment", "CURRENT-COMMENT")
        (agent._working_dir / "init.json").write_text("{not-json", encoding="utf-8")

        result = context_tool.handle(agent, {"action": "rebuild", "input": {}})

        assert result["status"] == "error"
        assert result["reason"] == "context_reconstruction_failed"
        assert agent._prompt_manager.read_section("comment") == "CURRENT-COMMENT"
    finally:
        agent.stop(timeout=1.0)


def test_provider_replay_observes_the_newly_composed_system_prompt(tmp_path):
    agent = _agent(tmp_path)

    class CapturingChat:
        def __init__(self):
            self.interface = ChatInterface()
            self.live_prompt = "STALE-PROMPT"
            self.replayed_prompt = None

        def update_system_prompt(self, prompt):
            self.live_prompt = prompt

        def request_history_rebuild(self, reason=""):
            self.replayed_prompt = self.live_prompt
            return True

    chat = CapturingChat()
    agent._chat = chat
    try:
        system = agent._working_dir / "system"
        (system / "rules.md").write_text("PROVIDER-MUST-SEE-THIS", encoding="utf-8")

        result = context_tool.handle(agent, {"action": "rebuild", "input": {}})

        assert result["status"] == "ok"
        assert result["prompt_reconstructed"] is True
        assert "All canonical prompt sources" in result["prompt_reconstruction"]
        assert "PROVIDER-MUST-SEE-THIS" in chat.replayed_prompt
    finally:
        # This test installs a minimal chat stand-in that has no lifecycle API.
        agent._chat = None
        agent.stop(timeout=1.0)


def test_molt_uses_one_canonical_reconstruction_hook(tmp_path):
    agent = _agent(tmp_path)
    try:
        hooks = getattr(agent, "_post_molt_hooks", [])
        assert len(hooks) == 1
        assert hooks[0] == agent._reconstruct_context
    finally:
        agent.stop(timeout=1.0)
