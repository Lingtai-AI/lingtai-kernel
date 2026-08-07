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
    assert "refused" in tc
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
