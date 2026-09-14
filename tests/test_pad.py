"""Pad durable-source and pinned-reference composition tests.

Pad has no public tool root any more: the former ``pad`` root and its ``append``
action were retired into the read-only ``psyche(action='pad')`` manual
loader. What is tested here is what remains — the durable ``system/pad.md``
source, the pinned ``system/pad_append.json`` reference list, and the private
``_pad_load`` composer. Generic body mutation is covered by Shell.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from lingtai.kernel.base_agent import BaseAgent
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from tests._agent_presence_helpers import make_test_presence_store
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._workdir_lease_helpers import make_test_lease


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


def _agent(tmp_path, name="test", **kwargs):
    workdir = tmp_path / name
    return BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name=name,
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(),
        source_revision_port=make_test_source_revision_port(),
        agent_presence=make_test_presence_store(),
        lifecycle_clock=make_test_lifecycle_clock(),
        notification_store=notification_store_for(workdir),
        **kwargs,
    )


def test_pad_exposes_no_public_root_or_append_action():
    from lingtai.tools.registry import BUILTIN_TOOLS, INTRINSICS
    from lingtai.tools import pad as pad_tool

    assert "pad" not in INTRINSICS
    assert "pad" not in BUILTIN_TOOLS
    # No schema, no dispatch, no retired handler survives.
    for attribute in ("get_schema", "get_description", "handle", "ACTION_ORDER"):
        assert not hasattr(pad_tool, attribute)
    assert "psyche" in INTRINSICS


def test_context_and_psyche_are_wired(tmp_path):
    agent = _agent(tmp_path)
    try:
        assert {"context", "psyche"} <= set(agent._intrinsics)
        assert "pad" not in agent._intrinsics
        assert "lingtai" not in agent._intrinsics
    finally:
        agent.stop(timeout=1.0)


def test_covenant_constructor_arg_writes_protected_section(tmp_path):
    agent = _agent(tmp_path, covenant="You are a helpful agent")
    try:
        path = agent.working_dir / "system" / "covenant.md"
        assert path.read_text() == "You are a helpful agent"
        section = next(s for s in agent._prompt_manager.list_sections() if s["name"] == "covenant")
        assert section["protected"] is True
    finally:
        agent.stop(timeout=1.0)


def test_pad_constructor_arg_seeds_disk_and_prompt(tmp_path):
    agent = _agent(tmp_path, pad="initial pad")
    try:
        assert (agent.working_dir / "system" / "pad.md").read_text() == "initial pad"
        assert agent._prompt_manager.read_section("pad") == "initial pad"
    finally:
        agent.stop(timeout=1.0)


def test_pinned_reference_list_still_composes_into_the_pad_section(tmp_path):
    """Retiring ``pad.append`` removed a writer, not the durable list itself."""
    from lingtai.tools.pad import _pad_load

    agent = _agent(tmp_path, pad="BODY")
    try:
        ref = agent.working_dir / "ref.md"
        ref.write_text("reference body", encoding="utf-8")
        # The durable source is ordinary text the agent edits with shell.
        append_path = agent.working_dir / "system" / "pad_append.json"
        append_path.write_text(json.dumps(["ref.md"]), encoding="utf-8")

        _pad_load(agent, {})

        composed = agent._prompt_manager.read_section("pad")
        assert "BODY" in composed
        assert "Reference (read-only)" in composed
        assert "reference body" in composed
    finally:
        agent.stop(timeout=1.0)


def test_pad_load_reports_missing_pinned_references_without_failing(tmp_path):
    from lingtai.tools.pad import _pad_load

    agent = _agent(tmp_path, pad="BODY")
    try:
        (agent.working_dir / "system" / "pad_append.json").write_text(
            json.dumps(["gone.md"]), encoding="utf-8"
        )
        result = _pad_load(agent, {})
        assert result["append_not_found"] == ["gone.md"]
        assert result["status"] == "ok"
    finally:
        agent.stop(timeout=1.0)


def test_stop_does_not_overwrite_pad_md(tmp_path):
    agent = _agent(tmp_path)
    path = agent.working_dir / "system" / "pad.md"
    path.write_text("previous session pad")
    agent.stop()
    assert path.read_text() == "previous session pad"
