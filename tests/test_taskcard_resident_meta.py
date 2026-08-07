"""Resident Task Card meta projection tests.

Covers the change-gated ``_meta.agent_meta.taskcard`` axis added for the
"taskcard always resident in agent meta" feature: active-card attachment,
change gating, absent hint, refusal of oversize bodies, and the
``finalize_two_axis_sidecars`` preservation of the new axis.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lingtai.kernel.llm.interface import ToolResultBlock
from lingtai.kernel.meta_block import (
    AGENT_META_INSTRUCTION,
    TASKCARD_ABSENT_HINT,
    TASKCARD_KEY,
    TASKCARD_MAX_CHARS,
    attach_active_taskcard,
    finalize_two_axis_sidecars,
)


def _agent_with_taskcard(tmp_path: Path, *, body: str, status: str = "active"):
    card_dir = tmp_path / "taskcard"
    card_dir.mkdir(exist_ok=True)
    (card_dir / "status").write_text(status, encoding="utf-8")
    (card_dir / "taskcard.md").write_text(body, encoding="utf-8")
    return SimpleNamespace(_working_dir=str(tmp_path), _taskcard_signature=None)


def _final_block() -> ToolResultBlock:
    return ToolResultBlock(
        id="tc-final",
        name="shell",
        content={"status": "ok"},
        metadata={},
    )


def test_attach_active_taskcard_first_active_attaches_body(tmp_path):
    agent = _agent_with_taskcard(tmp_path, body="# Goal\nnext step")
    block = _final_block()
    holder = attach_active_taskcard(agent, [block])
    assert holder is block
    agent_meta = block.metadata.get("agent_meta", {})
    tc = agent_meta.get(TASKCARD_KEY)
    assert tc is not None
    assert tc["present"] is True
    assert tc["status"] == "active"
    assert tc["body"] == "# Goal\nnext step"


def test_attach_active_taskcard_unchanged_not_restamped(tmp_path):
    agent = _agent_with_taskcard(tmp_path, body="stable body")
    first = _final_block()
    attach_active_taskcard(agent, [first])
    second = _final_block()
    holder = attach_active_taskcard(agent, [second])
    assert holder is None or holder is not second
    assert TASKCARD_KEY not in second.metadata.get("agent_meta", {})


def test_attach_active_taskcard_changed_reattaches(tmp_path):
    agent = _agent_with_taskcard(tmp_path, body="v1")
    first = _final_block()
    attach_active_taskcard(agent, [first])
    (tmp_path / "taskcard" / "taskcard.md").write_text("v2", encoding="utf-8")
    second = _final_block()
    holder = attach_active_taskcard(agent, [second])
    assert holder is second
    tc = second.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    assert tc["body"] == "v2"


def test_attach_active_taskcard_absent_hint_attaches_once(tmp_path):
    agent = _agent_with_taskcard(tmp_path, body="gone", status="inactive")
    block = _final_block()
    attach_active_taskcard(agent, [block])
    tc = block.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    assert tc is not None
    assert tc["present"] is False
    assert tc["hint"] == TASKCARD_ABSENT_HINT
    # Same absent state must not re-stamp.
    second = _final_block()
    attach_active_taskcard(agent, [second])
    assert TASKCARD_KEY not in second.metadata.get("agent_meta", {})


def test_attach_active_taskcard_refuses_oversize(tmp_path):
    big = "x" * (TASKCARD_MAX_CHARS + 1)
    agent = _agent_with_taskcard(tmp_path, body=big)
    block = _final_block()
    holder = attach_active_taskcard(agent, [block])
    assert holder is block
    tc = block.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    assert tc is not None
    assert tc["present"] is True
    assert tc["status"] == "refused"
    assert "body" not in tc  # refused payload never carries the oversize body
    assert "2000" in tc["hint"]


def test_finalize_two_axis_sidecars_preserves_taskcard(tmp_path):
    agent = _agent_with_taskcard(tmp_path, body="keep me")
    block = _final_block()
    attach_active_taskcard(agent, [block])
    finalize_two_axis_sidecars([block])
    tc = block.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    assert tc is not None
    assert tc["body"] == "keep me"


# --- Post-review regression + new-behavior tests (fable review #1216) ---


def test_runtime_attach_preserves_taskcard_axis(tmp_path):
    """The true pipeline order: taskcard attaches BEFORE the runtime attacher,
    which rebuilds agent_meta. The taskcard axis must survive the rebuild."""
    from lingtai.kernel.meta_block import attach_active_runtime

    agent = _agent_with_taskcard(tmp_path, body="keep me")
    agent._executor = SimpleNamespace(guard=SimpleNamespace(total_calls=1))
    block = _final_block()
    block._agent_pending = {"agent_state": {"session": "s1"}}
    attach_active_taskcard(agent, [block])
    attach_active_runtime(agent, [block], prior_holder=None)
    finalize_two_axis_sidecars([block])
    tc = block.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    assert tc is not None
    assert tc["present"] is True
    assert tc["body"] == "keep me"


def test_attach_active_taskcard_unreadable_is_not_absent(tmp_path):
    """A card that exists but cannot be read must not masquerade as absent
    (which would tell the agent to create a duplicate)."""
    agent = _agent_with_taskcard(tmp_path, body="present but unreadable")
    body_path = tmp_path / "taskcard" / "taskcard.md"
    body_path.chmod(0o000)
    block = _final_block()
    holder = attach_active_taskcard(agent, [block])
    tc = block.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    # No read permission may not work as root; assert either outcome is sane,
    # but if a payload exists it must be the unreadable sentinel, not absent.
    if tc is None:
        return
    assert tc.get("status") != "absent"
    if tc.get("status") == "unreadable":
        assert tc["present"] is True
        assert "body" not in tc
    body_path.chmod(0o644)


def test_attach_active_taskcard_whitespace_body_is_absent(tmp_path):
    """A whitespace-only body is not a real card: treat as absent so the
    create-one hint can fire."""
    agent = _agent_with_taskcard(tmp_path, body="   \n  ")
    block = _final_block()
    attach_active_taskcard(agent, [block])
    tc = block.metadata.get("agent_meta", {}).get(TASKCARD_KEY)
    assert tc is not None
    assert tc["present"] is False
    assert tc["hint"] == TASKCARD_ABSENT_HINT


def test_task_card_cap_matches_kernel_cap():
    """The tool-side builtin cap and the kernel projection cap must agree;
    drift would silently make every write succeed while projection refuses."""
    from lingtai.tools.task_card import _MAX_BODY_CHARS

    assert _MAX_BODY_CHARS == TASKCARD_MAX_CHARS
