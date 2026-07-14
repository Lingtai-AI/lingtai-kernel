"""Tests for tool-result spill (issue #144) and the AED over-window path.

Covers:
- The shared kernel module ``tool_result_artifacts`` — preventive cap,
  shared manifest shape, ``is_spill_manifest`` detector. Preventive spill
  only ever applies to a FRESH tool result at first construction (via
  ``ToolExecutor``), before it is ever canonical history — never a
  rebuild/replay-time mutation.
- The literal provider-context rebuild/replay invariant: canonical
  ``ToolResultBlock.content`` already committed to history is never
  retroactively rewritten by the AED recovery path. An unresolved
  over-window error now fails loud (deterministically exhausts AED into the
  existing preset-fallback / ASLEEP path) instead of being silently
  compacted — see ``lingtai.kernel.base_agent.turn._is_over_window_error``.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lingtai.kernel.tool_result_artifacts import (
    PREVENTIVE_MAX_CHARS,
    is_spill_manifest,
    spill_oversized_result,
)


# -- Shared helper: manifest shape & detection ------------------------------

def test_constants_match_spec():
    assert PREVENTIVE_MAX_CHARS == 200_000


def test_is_spill_manifest_detects_dict_shape():
    from lingtai.kernel.tool_result_artifacts import ARTIFACT_MARKER

    # Preferred shape: explicit namespaced artifact marker.
    manifest_with_marker = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "spill_path": "tmp/tool-results/foo.json",
        "cap_chars": 10000,
        "original_char_count": 50000,
    }
    assert is_spill_manifest(manifest_with_marker)
    # spill_path may legitimately be None when the workdir write failed —
    # the manifest is still a manifest.
    manifest_with_marker_failed_write = {
        "artifact": ARTIFACT_MARKER,
        "status": "spilled",
        "spill_path": None,
        "cap_chars": 10000,
        "original_char_count": 50000,
    }
    assert is_spill_manifest(manifest_with_marker_failed_write)

    # Backward-compat: legacy manifests lacking ``artifact`` are still
    # accepted as long as the structural quadruple is present.
    legacy_manifest = {
        "status": "spilled",
        "spill_path": "tmp/tool-results/x.json",
        "cap_chars": 10000,
        "original_char_count": 50000,
    }
    assert is_spill_manifest(legacy_manifest)

    # Conservative refusals — arbitrary business dicts must NOT match:
    assert not is_spill_manifest({"status": "ok", "spill_path": "x"})  # wrong status
    assert not is_spill_manifest({"status": "spilled"})  # no spill_path key
    # Two-field "spilled" + spill_path business dict — refused without the
    # marker AND without the structural quadruple.
    assert not is_spill_manifest({"status": "spilled", "spill_path": "/x"})
    assert not is_spill_manifest("just a string")
    assert not is_spill_manifest(None)
    assert not is_spill_manifest({})


def test_is_spill_manifest_accepts_failed_spill_with_none_path():
    """When the spill write fails the manifest still has spill_path=None
    plus a spill_error field; that's still a manifest."""
    manifest = spill_oversized_result(
        "X" * (PREVENTIVE_MAX_CHARS * 2),
        max_chars=PREVENTIVE_MAX_CHARS,
        tool_name="read",
        tool_call_id="tc1",
        working_dir=None,  # forces spill_path = None
    )
    assert is_spill_manifest(manifest)
    assert manifest["spill_path"] is None
    assert "spill_error" in manifest


def test_shared_helper_artifact_contains_full_payload(tmp_path):
    big = "Z" * (PREVENTIVE_MAX_CHARS * 3)
    out = spill_oversized_result(
        big,
        max_chars=PREVENTIVE_MAX_CHARS,
        tool_name="bash",
        tool_call_id="tc-shared",
        working_dir=tmp_path,
    )
    assert is_spill_manifest(out)
    assert out["source"] == "preventive"  # default
    artifact = tmp_path / out["spill_path"]
    assert artifact.read_text(encoding="utf-8") == big


def test_shared_helper_idempotent_on_manifest(tmp_path):
    """Calling spill on an already-spilled manifest returns it unchanged."""
    big = "M" * (PREVENTIVE_MAX_CHARS + 1)  # just over the cap
    first = spill_oversized_result(
        big, max_chars=PREVENTIVE_MAX_CHARS, tool_name="read",
        tool_call_id="tc1", working_dir=tmp_path,
    )
    assert is_spill_manifest(first)
    second = spill_oversized_result(
        first, max_chars=PREVENTIVE_MAX_CHARS, tool_name="read",
        tool_call_id="tc1", working_dir=tmp_path,
    )
    # No new artifact written — same object returned
    assert second is first


# -- ToolExecutor preventive path -------------------------------------------

def test_executor_preventive_spill_still_works_through_refactor(tmp_path):
    """Smoke test: the preventive cap path via ToolExecutor decides a fresh
    tool result's canonical content ONCE, at first construction — never a
    later rebuild/replay mutation."""
    from lingtai.kernel.llm.base import ToolCall
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.tool_executor import ToolExecutor

    big = "P" * (PREVENTIVE_MAX_CHARS * 2)
    captured = MagicMock(side_effect=lambda name, result, **kw: {"name": name, "result": result})
    executor = ToolExecutor(
        dispatch_fn=lambda tc: big,
        make_tool_result_fn=captured,
        guard=LoopGuard(max_total_calls=50),
        working_dir=tmp_path,
    )
    executor.execute([ToolCall(name="read", args={}, id="tc-prev")])

    name, payload = captured.call_args.args
    assert name == "read"
    assert is_spill_manifest(payload)
    assert payload["source"] == "preventive"
    artifact = tmp_path / payload["spill_path"]
    assert artifact.read_text(encoding="utf-8") == big


def test_manifest_carries_namespaced_artifact_marker(tmp_path):
    """Every freshly produced manifest stamps the namespaced marker so
    consumers don't have to rely on the structural quadruple."""
    from lingtai.kernel.tool_result_artifacts import ARTIFACT_MARKER

    big = "A" * (PREVENTIVE_MAX_CHARS + 1)  # just over the cap
    out = spill_oversized_result(
        big, max_chars=PREVENTIVE_MAX_CHARS, tool_name="read",
        tool_call_id="tc-mark", working_dir=tmp_path,
    )
    assert out["artifact"] == ARTIFACT_MARKER


def test_is_spill_manifest_refuses_arbitrary_business_dict():
    """A business dict that uses status='spilled' and spill_path keys for
    its own unrelated purpose must NOT be classified as a manifest."""
    business_dict = {
        "status": "spilled",
        "spill_path": "/data/business/spilled-2026-05-23.csv",
        "rows": 1234,
        "notes": "user dumped overflow to disk during ETL",
    }
    assert not is_spill_manifest(business_dict)


# -- No retroactive compaction remains ---------------------------------------

def test_compact_oversized_history_no_longer_exists():
    """The former retroactive rewriter is gone, not merely disconnected —
    it mutated already-committed canonical ``ToolResultBlock.content`` in
    place with no explicit ``summarize`` replacement, which the literal
    provider-context rebuild/replay invariant forbids."""
    import lingtai.kernel.tool_result_artifacts as tra

    assert not hasattr(tra, "compact_oversized_history")
    assert not hasattr(tra, "CompactionStats")
    assert not hasattr(tra, "RETROACTIVE_MAX_CHARS")

    import lingtai.kernel.base_agent.turn as turn

    assert not hasattr(turn, "_compact_history_before_retry")


# -- Refinement 4: over-window classifier ------------------------------------

@pytest.mark.parametrize("phrase", [
    "context window exceeded",
    "context_window_exceeded",
    "context length exceeded",
    "context_length_exceeded",
    "maximum context length is 200000 tokens",
    "the input exceeds the maximum context length",
    "prompt is too long for this model",
    "prompt too long",
    "input is too long",
    "input token count of 250000 exceeds",
    "tokens in the input are above the limit",
    "request too large",
    "too many tokens",
])
def test_is_over_window_error_matches_provider_phrasing(phrase):
    from lingtai.kernel.base_agent.turn import _is_over_window_error
    assert _is_over_window_error(RuntimeError(phrase))


def test_is_over_window_error_does_not_match_unrelated_errors():
    from lingtai.kernel.base_agent.turn import _is_over_window_error
    assert not _is_over_window_error(RuntimeError("connection reset"))
    assert not _is_over_window_error(RuntimeError("rate limit hit"))
    assert not _is_over_window_error(RuntimeError("auth failed"))
    assert not _is_over_window_error(RuntimeError(""))


# -- AED integration: over-window fails loud without mutating history -------

def _build_interface_with_pair(*, tool_id: str, result_content):
    """Build a ChatInterface containing a pre-existing tool_call / tool_result pair."""
    from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock

    iface = ChatInterface()
    iface._append(
        "assistant",
        [ToolCallBlock(id=tool_id, name="bash", args={"command": "ls"})],
    )
    iface._append(
        "user",
        [ToolResultBlock(id=tool_id, name="bash", content=result_content)],
    )
    return iface


def _make_run_loop_agent_with_oversized_history(tmp_path, big_payload):
    """Build a fake agent with a real ChatInterface holding an oversized
    tool result, matching the shape the AED loop expects."""
    import queue
    import threading
    from dataclasses import dataclass, field
    from types import SimpleNamespace

    from lingtai.kernel.message import _make_message, MSG_REQUEST
    from lingtai.kernel.state import AgentState

    iface = _build_interface_with_pair(tool_id="tc-aed-int", result_content=big_payload)
    iface.has_pending_tool_calls = lambda: False  # type: ignore[method-assign]
    iface.close_pending_tool_calls = lambda *, reason, tool_completed=False: None  # type: ignore[method-assign]

    @dataclass
    class _Agent:
        _working_dir: object
        agent_name: str = "test"
        _state: AgentState = AgentState.ACTIVE
        _asleep: threading.Event = field(default_factory=threading.Event)
        _logs: list = field(default_factory=list)
        _states: list = field(default_factory=list)
        _chat: object = None

        def _log(self, event_type, **fields):
            self._logs.append((event_type, fields))

        def _cancel_soul_timer(self):
            import lingtai.tools.soul.flow as soul_flow
            soul_flow._cancel_soul_timer(self)

        def _set_state(self, new_state, reason=""):
            self._state = new_state
            self._states.append(new_state)
            self._log("agent_state", new=new_state.value, reason=reason)

    agent = _Agent(tmp_path)
    agent._shutdown = threading.Event()
    agent._cancel_event = threading.Event()
    agent._inbox_timeout = 0.01
    agent._reset_uptime = lambda: None
    agent.save_history_calls = []
    def _record_save(*a, ledger_source: str = "main", **kw):
        agent.save_history_calls.append(ledger_source)
    agent._save_chat_history = _record_save
    agent._config = SimpleNamespace(
        insights_interval=0,
        max_aed_attempts=2,
        language="en",
        time_awareness=True,
        timezone_awareness=True,
    )
    agent._session = SimpleNamespace(
        chat=SimpleNamespace(interface=iface),
        _rebuild_session=lambda interface: None,
    )
    agent.inbox = queue.Queue()
    agent.inbox.put(_make_message(MSG_REQUEST, "human", "go"))
    agent._preset_fallback_attempted = False
    agent._can_fallback_preset = lambda: False
    return agent, iface


def test_aed_over_window_takes_deterministic_branch_not_transient(tmp_path, monkeypatch):
    """An over-window error must NOT take the transient retry loop —
    retrying on the same wire would just refire the same error. It falls
    through to the deterministic branch every time, since nothing shrinks
    the wire, until AED exhausts."""
    from lingtai.kernel.base_agent import turn

    big = "OW" * 5_000
    agent, iface = _make_run_loop_agent_with_oversized_history(tmp_path, big)

    def fake_handle(_agent, _msg):
        raise RuntimeError("context length exceeded: prompt is too long")

    monkeypatch.setattr(turn, "_handle_message", fake_handle)
    monkeypatch.setattr(turn.time, "sleep", lambda _seconds: None)
    import lingtai.tools.soul.flow as soul_flow
    # AED exhaustion sets the agent ASLEEP but does not itself terminate
    # _run_loop (real behavior: it waits on the next inbox message). The
    # ASLEEP branch calls _cancel_soul_timer() before blocking on the
    # inbox, so use that as the seam to end the test.
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda _a: _a._shutdown.set())

    turn._run_loop(agent)

    # Detected on every AED attempt (max_aed_attempts=2 here) — deterministic,
    # not a hang: nothing shrinks the wire between attempts, so it fires
    # identically each time until AED exhausts.
    detected = [e for e in agent._logs if e[0] == "aed_over_window_detected"]
    assert len(detected) == 2
    assert all("context length exceeded" in e[1]["error"].lower() for e in detected)

    transient_logs = [e for e in agent._logs if e[0] == "aed_transient_retry"]
    assert len(transient_logs) == 0

    # No retroactive compaction event exists anymore.
    assert not any(e[0] == "aed_history_compacted" for e in agent._logs)
    assert "retroactive_compaction" not in agent.save_history_calls

    # Fails loud: AED exhausts and the agent goes ASLEEP instead of hanging
    # or silently rewriting history.
    exhausted = [e for e in agent._logs if e[0] == "aed_exhausted"]
    assert len(exhausted) == 1


def test_aed_over_window_never_mutates_canonical_history(tmp_path, monkeypatch):
    """The oversized tool-result content must be byte-identical after AED
    exhausts on a persistent over-window condition — the kernel has no
    license to shrink historical content without an explicit summarize."""
    from lingtai.kernel.base_agent import turn

    big = "W" * 20_000
    agent, iface = _make_run_loop_agent_with_oversized_history(tmp_path, big)

    def fake_handle(_agent, _msg):
        raise RuntimeError("Anthropic: prompt is too long for the context window")

    monkeypatch.setattr(turn, "_handle_message", fake_handle)
    monkeypatch.setattr(turn.time, "sleep", lambda _seconds: None)
    import lingtai.tools.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda _a: _a._shutdown.set())

    turn._run_loop(agent)

    block = iface._entries[1].content[0]
    assert block.content == big  # untouched — byte-identical
    assert not is_spill_manifest(block.content)


def test_worker_still_running_does_not_touch_history(tmp_path, monkeypatch):
    """When _handle_message raises WorkerStillRunningError the AED loop
    must put the agent ASLEEP without touching ChatInterface at all — the
    worker future may still be mutating the interface from another thread."""
    from lingtai.kernel.base_agent import turn
    from lingtai.kernel.llm_utils import WorkerStillRunningError

    big = "S" * 20_000
    agent, iface = _make_run_loop_agent_with_oversized_history(tmp_path, big)

    def fake_handle(_agent, _msg):
        raise WorkerStillRunningError(elapsed=300.0, grace=5.0, agent_name="test")

    monkeypatch.setattr(turn, "_handle_message", fake_handle)
    monkeypatch.setattr(turn.time, "sleep", lambda _seconds: None)
    import lingtai.tools.soul.flow as soul_flow
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer",
                        lambda _a: _a._shutdown.set())

    turn._run_loop(agent)

    assert any(e[0] == "llm_worker_still_running" for e in agent._logs)
    assert not any(e[0] == "aed_history_compacted" for e in agent._logs)
    block = iface._entries[1].content[0]
    assert block.content == big  # untouched
    assert not is_spill_manifest(block.content)
