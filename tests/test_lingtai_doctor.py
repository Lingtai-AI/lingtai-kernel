"""Tests for the intrinsic lingtai-doctor script."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import typing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "src" / "lingtai" / "intrinsic_skills" / "lingtai-doctor" / "scripts" / "doctor.py"


def load_doctor_module():
    spec = importlib.util.spec_from_file_location("lingtai_doctor", DOCTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_lingtai_doctor_self_test_passes():
    proc = subprocess.run(
        [sys.executable, str(DOCTOR), "--self-test"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "self-test OK" in proc.stdout


def test_lingtai_doctor_json_redacts_env_secrets(tmp_path):
    agent = tmp_path / "project" / ".lingtai" / "mimo"
    agent.mkdir(parents=True)
    (agent / ".agent.json").write_text(
        json.dumps({"name": "mimo", "state": "idle"}), encoding="utf-8"
    )
    (agent / ".agent.heartbeat").write_text("ok", encoding="utf-8")
    (agent / "init.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "telegram": {
                        "type": "stdio",
                        "command": "/definitely/missing/python",
                        "env": {"BOT_TOKEN": "secret-value", "CONFIG_PATH": ".secrets/tg.json"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, str(DOCTOR), "--agent-dir", str(agent), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 1
    assert "secret-value" not in proc.stdout
    data = json.loads(proc.stdout)
    assert data["severity"] == "FAIL"
    assert any(section["name"] == "mcp/addons" for section in data["sections"])


def test_lingtai_doctor_reports_non_object_lifecycle_json(tmp_path):
    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / ".agent.json").write_text("[]", encoding="utf-8")
    (agent / ".status.json").write_text('"idle"', encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-O", str(DOCTOR), "--agent-dir", str(agent), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert proc.returncode == 1
    assert not proc.stderr
    lifecycle = next(
        section for section in json.loads(proc.stdout)["sections"]
        if section["name"] == "lifecycle"
    )
    findings = {finding["title"]: finding for finding in lifecycle["findings"]}
    assert "list" in findings[".agent.json malformed"]["detail"]
    assert "str" in findings[".status.json malformed"]["detail"]


def test_lingtai_doctor_addon_timeout_becomes_warning(monkeypatch):
    doctor = load_doctor_module()
    section = doctor.Section("mcp/addons")

    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(doctor.subprocess, "run", time_out)

    assert doctor.try_addon_imports(section, [], {"example.addon"}) == ["example.addon"]
    assert section.severity == "WARN"
    assert section.findings[0].data["imports"][0]["error"] == [
        "import probe timed out after 10s"
    ]


def test_lingtai_doctor_type_hints_resolve():
    doctor = load_doctor_module()

    hints = typing.get_type_hints(doctor.newest_mtime)

    assert hints["paths"] == doctor.Iterable[Path]
