"""Regression test: built wheels must ship every built-in tool contract.

The consolidated ``tools`` package ships one ``CONTRACT.md`` per built-in tool
plus the daemon's extended ``DAEMON_CONTRACT.md``, alongside its manual trees.
These reach the wheel only through the ``[tool.setuptools.package-data] tools``
globs in ``pyproject.toml``; a missing glob silently drops the contract while
the tool code still installs (the consolidation blocker this test guards).

Rather than grepping the config text, this test builds a real wheel and inspects
the distribution manifest at the correct boundary — the archive that pip
actually installs. Path handling is layout-aware: a pure-Python wheel places
packages at the archive root (``tools/...``) while a platform wheel that bundles
the Rust sidecar places pure-Python packages under
``<name>-<ver>.data/purelib/tools/...``; both are normalized to the logical
package path before assertion.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The eighteen built-in tools, each owning a top-level CONTRACT.md.
_BUILTIN_TOOLS = [
    "avatar", "bash", "daemon", "edit", "email", "glob", "grep",
    "knowledge", "mcp", "notification", "psyche", "read", "skills",
    "soul", "system", "vision", "web_search", "write",
]

# The nine daemon CLI-backend manuals that must continue to ship unchanged.
_DAEMON_BACKENDS = [
    "claude-p", "codex", "cursor", "kimicode", "lingtai",
    "mimocode", "oh-my-pi", "opencode", "qwen-code",
]

_BACKEND_MANUAL = (
    "tools/daemon/manual/reference/cli-backends/reference/backends"
    "/{backend}/SKILL.md"
)


def _build_wheel(dest: Path) -> Path:
    """Build a pure-Python wheel (Rust sidecar skipped) into ``dest``.

    ``LINGTAI_SKIP_RUST_BUILD=1`` keeps the build fast and Rust-independent:
    the package-data globs are identical with or without the sidecar, and a
    pure wheel exercises the root ``tools/...`` layout. Build isolation lets
    pip pick a setuptools that understands the PEP 639 license expression.
    """
    env = dict(os.environ)
    env["LINGTAI_SKIP_RUST_BUILD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "-w", str(dest), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "wheel build failed (rc=%d):\n%s\n%s"
            % (result.returncode, result.stdout, result.stderr)
        )
    wheels = sorted(dest.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _logical(path: str) -> str:
    """Normalize a wheel archive entry to its logical package path.

    Strips a leading ``<name>-<ver>.data/purelib/`` prefix (platform wheels)
    so entries compare against the same ``tools/...`` paths a pure wheel uses.
    """
    parts = path.split("/")
    for i, segment in enumerate(parts):
        if segment == "tools":
            return "/".join(parts[i:])
    return path


@pytest.fixture(scope="module")
def wheel_entries() -> set[str]:
    with tempfile.TemporaryDirectory(prefix="lingtai-wheel-test-") as tmp:
        wheel = _build_wheel(Path(tmp))
        with zipfile.ZipFile(wheel) as zf:
            return {_logical(name) for name in zf.namelist()}


def test_wheel_ships_every_tool_contract(wheel_entries: set[str]):
    missing = [
        f"tools/{tool}/CONTRACT.md"
        for tool in _BUILTIN_TOOLS
        if f"tools/{tool}/CONTRACT.md" not in wheel_entries
    ]
    assert not missing, "tool contracts missing from wheel: %r" % missing


def test_wheel_ships_exactly_eighteen_tool_contracts(wheel_entries: set[str]):
    # No nested/manual CONTRACT.md should sneak in alongside the 18 top-level
    # ones — guard against an over-broad glob silently widening the manifest.
    contracts = {
        e for e in wheel_entries
        if e.endswith("/CONTRACT.md") and e.startswith("tools/")
    }
    assert len(contracts) == len(_BUILTIN_TOOLS), (
        "expected exactly %d tool contracts, wheel has %d: %r"
        % (len(_BUILTIN_TOOLS), len(contracts), sorted(contracts))
    )


def test_wheel_ships_daemon_contract(wheel_entries: set[str]):
    assert "tools/daemon/DAEMON_CONTRACT.md" in wheel_entries


def test_wheel_keeps_daemon_backend_manuals(wheel_entries: set[str]):
    missing = [
        backend for backend in _DAEMON_BACKENDS
        if _BACKEND_MANUAL.format(backend=backend) not in wheel_entries
    ]
    assert not missing, "daemon backend manuals missing from wheel: %r" % missing
