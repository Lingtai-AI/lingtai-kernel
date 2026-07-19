"""The CLI composition-root import graph must survive missing POSIX mechanisms.

Windows has no ``fcntl``/``termios``/``pty``/``grp``. Booting an agent there
dies at *import* time — before any selector can fail loudly — if any module on
the ``lingtai.cli`` / ``lingtai.agent`` composition path imports one of those
eagerly. This subprocess test blocks them and imports the full boot-path
module set, pinning the construction-gate import graph on every platform.
Modules are imported fresh in a clean interpreter so no previously cached
import can mask an eager dependency.
"""
from __future__ import annotations

import subprocess
import sys

_SNIPPET = """
import builtins

real_import = builtins.__import__
BLOCKED = {"fcntl", "termios", "pty", "grp"}

def guard(name, *args, **kwargs):
    if name in BLOCKED or name.split(".")[0] in BLOCKED:
        raise ModuleNotFoundError(f"No module named {name!r} (simulated Windows)")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guard
import lingtai.cli
import lingtai.agent
import lingtai.adapters.workdir_lease
import lingtai.adapters.refresh_watcher
import lingtai.adapters.process_scan
import lingtai.adapters.avatar_launcher
import lingtai.adapters.windows.workdir_lease
import lingtai.adapters.windows.refresh_watcher
import lingtai.adapters.windows.refresh_watcher_process
import lingtai.adapters.windows.refresh_watcher_entrypoint
import lingtai.adapters.windows.process_scan
import lingtai.adapters.windows.avatar_launcher
import lingtai.adapters.windows._win32
print("WINDOWS-IMPORT-GRAPH-OK")
"""


def test_boot_path_imports_survive_missing_posix_mechanisms():
    result = subprocess.run(
        [sys.executable, "-c", _SNIPPET],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "WINDOWS-IMPORT-GRAPH-OK" in result.stdout
