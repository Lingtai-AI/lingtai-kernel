"""Tests that the removed secondary channel is absent from tool schemas."""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

from unittest.mock import MagicMock

from lingtai.kernel.base_agent import BaseAgent
from tests._service_helpers import make_gemini_mock_service as make_mock_service
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store




def _schema_by_name(agent: BaseAgent) -> dict[str, dict]:
    return {schema.name: schema.parameters for schema in agent._build_tool_schemas()}


def test_secondary_schema_not_injected_into_dynamic_or_intrinsic_tools(tmp_path):
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    agent.add_tool(
        "long_work",
        schema={"type": "object", "properties": {"path": {"type": "string"}}},
        handler=lambda args: {"status": "ok"},
        description="long work",
    )

    schemas = _schema_by_name(agent)

    for schema in schemas.values():
        props = schema.get("properties", {})
        assert "secondary" not in props
    assert "reasoning" in schemas["long_work"]["properties"]
    assert "reasoning" in schemas["email"]["properties"]
    agent.stop(timeout=1.0)
