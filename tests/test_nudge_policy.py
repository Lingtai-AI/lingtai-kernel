from __future__ import annotations

import json
import time

from lingtai.init_reader import read_init
from lingtai.kernel.nudge import effective_policy, run_checks, upsert
from lingtai.kernel.nudge.init_config import check as check_init_config
from lingtai.kernel.notifications import dismiss_channel
from tests._notification_store_helpers import notification_store_for, snapshot_notifications


class _Agent:
    def __init__(self, workdir):
        self._working_dir = workdir
        self._notification_store = notification_store_for(workdir)
        self._notification_fp = ()
        self.logs = []

    def _log(self, event, **fields):
        self.logs.append((event, fields))


def _entries(workdir):
    payload = snapshot_notifications(workdir).get("nudge", {})
    return payload.get("data", {}).get("nudges", [])


def test_global_policy_defaults_and_invalid_values(monkeypatch):
    monkeypatch.delenv("LINGTAI_NUDGE_ENABLED", raising=False)
    monkeypatch.delenv("LINGTAI_NUDGE_REPEAT_INTERVAL", raising=False)
    policy = effective_policy()
    assert policy.enabled is True
    assert policy.repeat_interval_value == "24h"

    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "wat")
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "-2h")
    policy = effective_policy()
    assert policy.enabled is True
    assert policy.repeat_interval_value == "24h"
    assert len(policy.invalid_values) == 2


def test_emitted_finding_describes_effective_global_policy(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "on")
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "2h")

    upsert(agent, "example", {"title": "Needs attention", "detail": "read it"})

    entry = _entries(tmp_path)[0]
    assert entry["policy"] == {
        "enabled": "on",
        "repeat_after_dismiss": "2h",
        "env": {
            "enabled": "LINGTAI_NUDGE_ENABLED",
            "repeat_interval": "LINGTAI_NUDGE_REPEAT_INTERVAL",
        },
        "documentation": "system-manual/reference/environment-variables/SKILL.md",
    }
    assert "LINGTAI_NUDGE_ENABLED=on" in entry["detail"]
    assert "LINGTAI_NUDGE_REPEAT_INTERVAL=2h" in entry["detail"]
    assert "system-manual/reference/environment-variables/SKILL.md" in entry["detail"]


def test_disabled_policy_suppresses_all_nudge_kinds(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "off")
    upsert(agent, "example", {"title": "Needs attention"})
    assert _entries(tmp_path) == []


def test_run_checks_off_clears_visible_nudge_before_producer_gates(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    upsert(agent, "already-visible", {"title": "old finding"})
    assert len(_entries(tmp_path)) == 1

    monkeypatch.setenv("LINGTAI_NUDGE_ENABLED", "off")
    run_checks(agent)

    assert _entries(tmp_path) == []


def test_dismissal_mutes_unresolved_finding_then_global_interval_allows_repeat(monkeypatch, tmp_path):
    agent = _Agent(tmp_path)
    monkeypatch.setenv("LINGTAI_NUDGE_REPEAT_INTERVAL", "0.001s")
    body = {"title": "Needs attention", "detail": "same unresolved finding"}
    upsert(agent, "example", body)
    assert len(_entries(tmp_path)) == 1

    result = dismiss_channel(agent, "nudge", invoked_by="notification", force=True)
    assert result["status"] == "ok"
    assert _entries(tmp_path) == []

    # Dismissal is mute, so the producer cannot immediately recreate it.
    upsert(agent, "example", body)
    assert _entries(tmp_path) == []
    time.sleep(0.01)
    upsert(agent, "example", body)
    assert len(_entries(tmp_path)) == 1


def test_config_shape_nudge_consumes_outcome_and_clears_after_explicit_repair(tmp_path):
    agent = _Agent(tmp_path)
    required = {
        "manifest": {
            "llm": {"provider": "openai", "model": "gpt-4o"},
            "capabilities": {"bash": {"yolo": True}},
        },
        "covenant": "operator contract",
        "pad": "durable state",
    }
    init = tmp_path / "init.json"
    init.write_text(json.dumps(required), encoding="utf-8")
    outcome = read_init(tmp_path)
    check_init_config(agent, outcome)

    entry = _entries(tmp_path)[0]
    assert entry["kind"] == "init_config_shape"
    assert entry["shape_decision"] == "NUDGE"
    assert entry["compatibility_paths"][0]["raw_path"] == "manifest.capabilities.bash"
    assert entry["effective_outcome"]["effective_config"]["redacted"] is True
    assert "LINGTAI_NUDGE_ENABLED" in entry["policy"]["env"]["enabled"]

    required["manifest"]["capabilities"] = {"shell": {"yolo": True}}
    init.write_text(json.dumps(required), encoding="utf-8")
    repaired = read_init(tmp_path)
    check_init_config(agent, repaired)
    assert _entries(tmp_path) == []
