"""Tests for auto-fallback to default preset on AED exhaustion."""
import json
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store


def _make_test_agent(tmp_path):
    """BaseAgent with init.json that has a non-default active preset."""
    from lingtai.kernel.base_agent import BaseAgent
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "x"
    svc.model = "y"
    wd = tmp_path / "test"
    wd.mkdir()
    init = {
        "manifest": {
            "agent_name": "alice",
            "language": "en",
            "preset": {
                "active": "fancy",
                "default": "boring",
                "path": str(tmp_path / "presets"),
            },
            "llm": {"provider": "x", "model": "y",
                    "api_key": None, "api_key_env": "X"},
            "capabilities": {},
            "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "lingtai": "",
        "soul": "",
    }
    (wd / "init.json").write_text(json.dumps(init))
    return BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="alice", working_dir=wd, workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(wd))


def test_can_fallback_preset_true_when_active_differs_from_default(tmp_path):
    agent = _make_test_agent(tmp_path)
    assert agent._can_fallback_preset() is True


def test_can_fallback_preset_false_when_active_equals_default(tmp_path):
    """Already on the fallback target — no fallback possible."""
    agent = _make_test_agent(tmp_path)
    # Rewrite init.json with active == default
    init_path = agent._working_dir / "init.json"
    data = json.loads(init_path.read_text())
    data["manifest"]["preset"]["active"] = "boring"  # same as default
    init_path.write_text(json.dumps(data))
    assert agent._can_fallback_preset() is False


def test_can_fallback_preset_false_when_no_preset_block(tmp_path):
    from lingtai.kernel.base_agent import BaseAgent
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "x"
    svc.model = "y"
    wd = tmp_path / "test"
    wd.mkdir()
    init = {
        "manifest": {
            "agent_name": "alice", "language": "en",
            "llm": {"provider": "x", "model": "y", "api_key": None, "api_key_env": "X"},
            "capabilities": {}, "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "lingtai": "", "soul": "",
    }
    (wd / "init.json").write_text(json.dumps(init))
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=svc, agent_name="alice", working_dir=wd, workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(wd))
    assert agent._can_fallback_preset() is False


def test_activate_default_preset_stub_raises_on_baseagent(tmp_path):
    """BaseAgent's stub raises NotImplementedError."""
    agent = _make_test_agent(tmp_path)
    with pytest.raises(NotImplementedError):
        agent._activate_default_preset()


def test_preset_fallback_attempted_initialized_false(tmp_path):
    agent = _make_test_agent(tmp_path)
    assert agent._preset_fallback_attempted is False
