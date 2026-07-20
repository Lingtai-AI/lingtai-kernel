"""Focused safety checks for the POSIX name-change helper."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py"
spec = importlib.util.spec_from_file_location("change_name", SCRIPT)
change_name = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = change_name
assert spec.loader is not None
spec.loader.exec_module(change_name)


def test_missing_old_never_writes_existing_destination(tmp_path: Path):
    destination = tmp_path / "new"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_text("unchanged", encoding="utf-8")
    assert change_name.main([str(tmp_path / "missing"), "new"]) == 1
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_process_scan_failure_is_not_absence(monkeypatch):
    monkeypatch.setattr(
        change_name.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(["ps"], 2)),
    )
    with pytest.raises(change_name.ChangeNameError, match="refusing to infer absence"):
        change_name._processes(Path("/tmp/agent"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_no_replace_rename_keeps_a_racing_destination(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    with pytest.raises(OSError):
        change_name._rename_no_replace(old, new)
    assert old.is_dir() and new.is_dir()


def test_runtime_probe_does_not_create_pyc(tmp_path: Path, monkeypatch):
    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(venv)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    site = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    package = site / "lingtai"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(site))
    change_name._probe(venv / "bin" / "python", tmp_path)
    assert not (package / "__pycache__").exists()
