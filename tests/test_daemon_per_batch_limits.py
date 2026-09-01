"""Tests for per-batch max_turns and timeout overrides on daemon.emanate."""
import pytest

from tests._daemon_helpers import (
    install_fake_detached_owner,
    make_daemon_agent as _make_agent,
    wait_daemon_terminal,
)


@pytest.fixture(autouse=True)
def _isolate_daemon_limit_environment(monkeypatch):
    monkeypatch.delenv("LINGTAI_DAEMON_MAX_TURNS", raising=False)
    monkeypatch.delenv("LINGTAI_DAEMON_MANAGER_POOL_SIZE", raising=False)


def _make_limit_agent(tmp_path, *, with_file: bool = True):
    capabilities = {"daemon": {"manager_pool_size": 0}}
    if with_file:
        capabilities["file"] = {}
    return _make_agent(tmp_path, capabilities)


def test_emanate_default_uses_builtin_5000_ceiling(tmp_path, monkeypatch):
    agent = _make_limit_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    out = mgr.handle({"action": "emanate",
                      "tasks": [{"task": "x", "tools": ["file"]}]})

    assert out["status"] == "dispatched"
    state = wait_daemon_terminal(records[0]["run_dir"])
    assert mgr._max_turns == 5000
    assert records[0]["manifest"]["max_turns"] == 5000
    assert state["max_turns"] == 5000



def test_daemon_schema_advertises_5000_turn_ceiling():
    from lingtai.tools.daemon import get_schema

    from tests._daemon_helpers import daemon_action_input_schema

    max_turns_schema = daemon_action_input_schema("emanate", "en")["properties"]["max_turns"]
    assert max_turns_schema["minimum"] == 1
    assert max_turns_schema["maximum"] == 5000
    assert "5000" in max_turns_schema["description"]

def test_emanate_respects_per_batch_max_turns(tmp_path, monkeypatch):
    agent = _make_limit_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    out = mgr.handle({"action": "emanate", "max_turns": 50,
                      "tasks": [{"task": "x", "tools": ["file"]}]})

    assert out["status"] == "dispatched"
    state = wait_daemon_terminal(records[0]["run_dir"])
    assert records[0]["manifest"]["max_turns"] == 50
    assert state["max_turns"] == 50


def test_emanate_caps_max_turns_at_builtin_5000_ceiling(tmp_path, monkeypatch):
    agent = _make_limit_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    # The built-in ceiling is 5000; ask for more.
    out = mgr.handle({"action": "emanate", "max_turns": 9999,
                      "tasks": [{"task": "x", "tools": ["file"]}]})

    assert out["status"] == "dispatched"
    state = wait_daemon_terminal(records[0]["run_dir"])
    assert mgr._max_turns == 5000
    assert records[0]["manifest"]["max_turns"] == 5000
    assert state["max_turns"] == 5000


def test_emanate_allows_builtin_5000_turn_ceiling(tmp_path, monkeypatch):
    agent = _make_limit_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    out = mgr.handle({"action": "emanate", "max_turns": 5000,
                      "tasks": [{"task": "x", "tools": ["file"]}]})

    assert out["status"] == "dispatched"
    state = wait_daemon_terminal(records[0]["run_dir"])
    assert records[0]["manifest"]["max_turns"] == 5000
    assert state["max_turns"] == 5000


def test_emanate_rejects_zero_max_turns(tmp_path):
    agent = _make_limit_agent(tmp_path, with_file=False)
    mgr = agent.get_capability("daemon")
    out = mgr.handle({"action": "emanate", "max_turns": 0,
                      "tasks": [{"task": "x", "tools": ["read"]}]})
    assert out["status"] == "error"
    assert "max_turns" in out["message"]


def test_emanate_rejects_negative_max_turns(tmp_path):
    agent = _make_limit_agent(tmp_path, with_file=False)
    mgr = agent.get_capability("daemon")
    out = mgr.handle({"action": "emanate", "max_turns": -5,
                      "tasks": [{"task": "x", "tools": ["read"]}]})
    assert out["status"] == "error"
    assert "max_turns" in out["message"]


def test_emanate_respects_per_batch_timeout(tmp_path, monkeypatch):
    agent = _make_limit_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    out = mgr.handle({"action": "emanate", "timeout": 600,
                      "tasks": [{"task": "x", "tools": ["file"]}]})

    assert out["status"] == "dispatched"
    wait_daemon_terminal(records[0]["run_dir"])
    assert records[0]["manifest"]["timeout_s"] == 600.0


def test_emanate_honors_explicit_timeout_above_default_ceiling(tmp_path, monkeypatch):
    """An explicit ``timeout`` is a public, uncapped override (schema advertises
    only ``minimum: 5``, no ``maximum``) — unlike ``max_turns``, which the
    schema caps at ``DEFAULT_MAX_TURNS``. It must reach both the run's
    daemon.json state and the detached supervisor_manifest.json unchanged,
    not get silently clamped down to the parent's default-when-omitted value."""
    agent = _make_limit_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    assert mgr._timeout == 3600.0  # default ceiling, unrelated to this call's override
    out = mgr.handle({"action": "emanate", "timeout": 10800,
                      "tasks": [{"task": "x", "tools": ["file"]}]})

    assert out["status"] == "dispatched"
    state = wait_daemon_terminal(records[0]["run_dir"])
    # supervisor_manifest.json (read via the fake supervisor's request.manifest_path)
    assert records[0]["manifest"]["timeout_s"] == 10800.0
    # daemon.json durable state
    assert state["timeout_s"] == 10800.0
    # the parent's own default-when-omitted ceiling is untouched by this call
    assert mgr._timeout == 3600.0


def test_emanate_rejects_zero_timeout(tmp_path):
    agent = _make_limit_agent(tmp_path, with_file=False)
    mgr = agent.get_capability("daemon")
    out = mgr.handle({"action": "emanate", "timeout": 0,
                      "tasks": [{"task": "x", "tools": ["read"]}]})
    assert out["status"] == "error"
    assert "timeout" in out["message"]


def test_emanate_rejects_negative_timeout(tmp_path):
    agent = _make_limit_agent(tmp_path, with_file=False)
    mgr = agent.get_capability("daemon")
    out = mgr.handle({"action": "emanate", "timeout": -1,
                      "tasks": [{"task": "x", "tools": ["read"]}]})
    assert out["status"] == "error"
    assert "timeout" in out["message"]


def test_emanate_rejects_sub_5s_timeout(tmp_path):
    """Sub-5s timeouts can fire before the emanation thread starts (the
    watchdog ticks at 1s and OS scheduling can delay its first run).
    Refuse rather than silently mark emanations as 'timeout' before they ran."""
    agent = _make_limit_agent(tmp_path, with_file=False)
    mgr = agent.get_capability("daemon")
    out = mgr.handle({"action": "emanate", "timeout": 2,
                      "tasks": [{"task": "x", "tools": ["read"]}]})
    assert out["status"] == "error"
    assert "timeout" in out["message"]
    assert "5" in out["message"]
