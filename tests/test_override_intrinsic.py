"""Tests for BaseAgent.override_intrinsic() — capability upgrade mechanism."""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

import pytest

from lingtai.kernel.base_agent import BaseAgent
from tests._service_helpers import make_gemini_mock_service as make_mock_service
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._notification_store_helpers import notification_store_for




def test_override_intrinsic_removes_from_dict(tmp_path):
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    assert "psyche" in agent._intrinsics
    agent.override_intrinsic("psyche")
    assert "psyche" not in agent._intrinsics
    agent.stop(timeout=1.0)


def test_override_intrinsic_returns_original_handler(tmp_path):
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    original = agent._intrinsics["psyche"]
    returned = agent.override_intrinsic("psyche")
    assert returned is original
    agent.stop(timeout=1.0)


def test_override_intrinsic_raises_after_start(tmp_path):
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    agent.start()
    try:
        with pytest.raises(RuntimeError, match="Cannot modify tools after start"):
            agent.override_intrinsic("psyche")
    finally:
        agent.stop(timeout=2.0)


def test_override_intrinsic_raises_unknown(tmp_path):
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    with pytest.raises(KeyError):
        agent.override_intrinsic("nonexistent")
    agent.stop(timeout=1.0)


def test_override_intrinsic_tool_no_longer_visible(tmp_path):
    """After override, the intrinsic should not appear in tool schemas."""
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    agent.override_intrinsic("psyche")
    schemas = agent._build_tool_schemas()
    schema_names = [s.name for s in schemas]
    assert "psyche" not in schema_names
    agent.stop(timeout=1.0)
