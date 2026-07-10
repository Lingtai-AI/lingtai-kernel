"""Regression tests for the BaseAgent ``.status.json`` write call site."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import lingtai_kernel.base_agent as base_agent_module
from lingtai_kernel import _fsutil
from lingtai_kernel.base_agent import BaseAgent


def test_status_snapshot_atomically_replaces_with_legacy_bytes(tmp_path, monkeypatch):
    payload = {"identity": {"name": "灵台"}, "runtime": {"state": "idle"}}
    agent = SimpleNamespace(
        _working_dir=tmp_path,
        agent_name="test",
        status=lambda: payload,
    )
    replacements = []
    real_replace = _fsutil.os.replace

    def spy_replace(src, dst):
        replacements.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(_fsutil.os, "replace", spy_replace)

    BaseAgent._write_status_snapshot(agent)

    target = tmp_path / ".status.json"
    assert len(replacements) == 1
    temp_path, replaced_target = map(Path, replacements[0])
    assert temp_path.parent == target.parent
    assert temp_path != target
    assert replaced_target == target
    expected = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    assert target.read_bytes() == expected
    assert not expected.endswith(b"\n")
    assert list(tmp_path.iterdir()) == [target]


def test_status_snapshot_write_failure_warns_and_preserves_prior_bytes(
    tmp_path, monkeypatch, caplog
):
    target = tmp_path / ".status.json"
    prior_bytes = b'{"keep": true}'
    target.write_bytes(prior_bytes)
    agent = SimpleNamespace(
        _working_dir=tmp_path,
        agent_name="test",
        status=lambda: {"replacement": True},
    )

    def fail_write(*args, **kwargs):
        raise OSError("representative write failure")

    monkeypatch.setattr(base_agent_module, "atomic_write_json", fail_write)

    with caplog.at_level(logging.WARNING):
        BaseAgent._write_status_snapshot(agent)

    assert "[test] Failed to write .status.json: representative write failure" in caplog.text
    assert target.read_bytes() == prior_bytes
