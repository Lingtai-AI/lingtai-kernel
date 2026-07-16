"""Regression test: built wheels must ship every built-in tool contract.

The consolidated ``lingtai.tools`` package ships one ``CONTRACT.md`` per
built-in tool and the daemon's intentional interactive-terminal component
contract, alongside its manual trees.
These reach the wheel only through the ``"lingtai.tools"`` entry
in ``[tool.setuptools.package-data]`` in ``pyproject.toml``; a missing glob
silently drops the contract while the tool code still installs (the
consolidation blocker this test guards).

Rather than grepping the config text, this test builds a real wheel and inspects
the distribution manifest at the correct boundary — the archive that pip
actually installs. Both the pure-Python wheel (built here) and the native
sidecar wheel place packages at the archive root (``lingtai/tools/...``): the native
wheel is platlib-compliant, so it does *not* bury packages under
``<name>-<ver>.data/purelib/`` — that placement was the auditwheel release
blocker fixed in ``setup.py`` and is guarded by
``tests/test_wheel_platlib_layout.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The eighteen built-in tools, each owning a top-level CONTRACT.md.
_BUILTIN_TOOLS = [
    "avatar",
    "bash",
    "daemon",
    "edit",
    "email",
    "glob",
    "grep",
    "knowledge",
    "mcp",
    "notification",
    "psyche",
    "read",
    "skills",
    "soul",
    "system",
    "vision",
    "web_search",
    "write",
]

# The nine daemon CLI-backend manuals that must continue to ship unchanged.
_DAEMON_BACKENDS = [
    "claude-p",
    "codex",
    "cursor",
    "kimicode",
    "lingtai",
    "mimocode",
    "oh-my-pi",
    "opencode",
    "qwen-code",
]

_BACKEND_MANUAL = (
    "lingtai/tools/daemon/manual/reference/cli-backends/reference/backends/{backend}/SKILL.md"
)

_NOTIFICATION_MANUAL_FILES = (
    "lingtai/intrinsic_skills/notification-manual/SKILL.md",
    "lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md",
    "lingtai/intrinsic_skills/notification-manual/reference/dismissal-safety/SKILL.md",
)

# The three per-tool glossary languages that each package must ship.
_GLOSSARY_LANGS = ("en", "zh", "wen")


def _build_wheel(dest: Path) -> Path:
    """Build a pure-Python wheel (Rust sidecar skipped) into ``dest``.

    ``LINGTAI_SKIP_RUST_BUILD=1`` keeps the build fast and Rust-independent:
    the package-data globs are identical with or without the sidecar, and a
    pure wheel exercises the root ``lingtai/tools/...`` layout. Build isolation lets
    pip pick a setuptools that understands the PEP 639 license expression.
    """
    env = dict(os.environ)
    env["LINGTAI_SKIP_RUST_BUILD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "-w",
            str(dest),
            str(REPO_ROOT),
        ],
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
    """Return an archive entry rooted at the canonical ``lingtai/tools`` path.

    Wheel entries already start with ``lingtai/tools``. Sdist entries add the
    distribution root plus ``src/``; those prefixes are stripped only after the
    exact ``lingtai`` / ``tools`` pair is found. A ``*.data/{purelib,platlib}/``
    prefix is *not* normalized away — it is the auditwheel-rejected layout the
    packaging fix eliminated. If one ever reappears it must surface as a broken
    path, not be silently accepted; ``test_wheel_platlib_layout.py`` asserts the
    native wheel never produces one.
    """
    parts = path.split("/")
    for i, segment in enumerate(parts):
        if segment.endswith(".data") and i + 1 < len(parts) and parts[i + 1] in ("purelib", "platlib"):
            # Fail loud rather than normalize: this placement is the release
            # blocker, not an acceptable alternate layout.
            raise AssertionError(
                "wheel entry under *.data/%s is the auditwheel-rejected layout: %r"
                % (parts[i + 1], path)
            )
    for i in range(len(parts) - 1):
        if parts[i : i + 2] == ["lingtai", "tools"]:
            return "/".join(parts[i:])
    return path


@pytest.fixture(scope="module")
def wheel_archive(tmp_path_factory) -> Path:
    """Build one real wheel and keep it for archive and installed-runtime tests."""
    dest = tmp_path_factory.mktemp("lingtai-wheel-test")
    return _build_wheel(dest)


@pytest.fixture(scope="module")
def wheel_entries(wheel_archive: Path) -> set[str]:
    with zipfile.ZipFile(wheel_archive) as zf:
        return {_logical(name) for name in zf.namelist()}


def test_wheel_ships_every_tool_contract(wheel_entries: set[str]):
    missing = [
        f"lingtai/tools/{tool}/CONTRACT.md"
        for tool in _BUILTIN_TOOLS
        if f"lingtai/tools/{tool}/CONTRACT.md" not in wheel_entries
    ]
    assert not missing, "tool contracts missing from wheel: %r" % missing


def test_wheel_ships_vision_manual(wheel_entries: set[str]):
    assert "lingtai/tools/vision/manual/SKILL.md" in wheel_entries


def test_wheel_ships_exact_expected_tool_contracts(wheel_entries: set[str]):
    # Keep the manifest closed: the 18 top-level tool contracts plus the one
    # intentional daemon component contract. No other nested/manual contract
    # may sneak in through an over-broad package-data glob.
    expected = {
        f"lingtai/tools/{tool}/CONTRACT.md" for tool in _BUILTIN_TOOLS
    } | {"lingtai/tools/daemon/interactive_terminal/CONTRACT.md"}
    contracts = {
        e
        for e in wheel_entries
        if e.endswith("/CONTRACT.md") and e.startswith("lingtai/tools/")
    }
    assert contracts == expected, (
        "expected exact tool contract manifest %r, wheel has %r"
        % (sorted(expected), sorted(contracts))
    )


def test_wheel_keeps_daemon_backend_manuals(wheel_entries: set[str]):
    missing = [
        backend
        for backend in _DAEMON_BACKENDS
        if _BACKEND_MANUAL.format(backend=backend) not in wheel_entries
    ]
    assert not missing, "daemon backend manuals missing from wheel: %r" % missing


def test_wheel_ships_first_level_notification_manual(wheel_entries: set[str]):
    missing = [path for path in _NOTIFICATION_MANUAL_FILES if path not in wheel_entries]
    assert not missing, "notification manual files missing from wheel: %r" % missing


# ---------------------------------------------------------------------------
# Glossary resources (54 files: 18 packages × 3 languages)
# ---------------------------------------------------------------------------


def test_wheel_ships_every_glossary_resource(wheel_entries: set[str]):
    missing = []
    for tool in _BUILTIN_TOOLS:
        for lang in _GLOSSARY_LANGS:
            path = f"lingtai/tools/{tool}/glossary-{lang}.md"
            if path not in wheel_entries:
                missing.append(path)
    assert not missing, "glossary resources missing from wheel: %r" % missing


def test_wheel_ships_exactly_54_glossary_resources(wheel_entries: set[str]):
    # Exactly 18 packages × 3 languages = 54. The narrowed package-data globs
    # (glossary-en.md, glossary-zh.md, glossary-wen.md — not glossary-*.md)
    # must include exactly these files and nothing more.
    glossary_files = {
        e
        for e in wheel_entries
        if e.startswith("lingtai/tools/") and "/glossary-" in e and e.endswith(".md")
    }
    assert len(glossary_files) == 54, (
        "expected exactly 54 glossary resources, wheel has %d: %r"
        % (len(glossary_files), sorted(glossary_files))
    )


def test_installed_wheel_validator_reads_package_resources(
    wheel_archive: Path, tmp_path: Path
):
    target = tmp_path / "site"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(wheel_archive),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        pytest.fail(
            "wheel install failed (rc=%d):\n%s\n%s"
            % (install.returncode, install.stdout, install.stderr)
        )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(target)
    result = subprocess.run(
        [sys.executable, "-m", "lingtai.tools.glossary_validator", "--check"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "54 glossary resources across 18 packages" in result.stdout


@pytest.fixture(scope="module")
def sdist_entries(tmp_path_factory) -> set[str]:
    """Build one real sdist and return its logical archive entries."""
    import tarfile

    tmp = tmp_path_factory.mktemp("lingtai-sdist-test")
    outdir = tmp / "sdist"
    env = dict(os.environ)
    env["LINGTAI_SKIP_RUST_BUILD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--outdir",
            str(outdir),
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "sdist build failed (rc=%d):\n%s\n%s"
            % (result.returncode, result.stdout, result.stderr)
        )
    sdists = sorted(outdir.glob("*.tar.gz"))
    assert len(sdists) == 1, sdists
    with tarfile.open(sdists[0]) as tf:
        return {_logical(name) for name in tf.getnames()}


def test_sdist_ships_every_glossary_resource(sdist_entries: set[str]):
    missing = []
    for tool in _BUILTIN_TOOLS:
        for lang in _GLOSSARY_LANGS:
            path = f"lingtai/tools/{tool}/glossary-{lang}.md"
            if path not in sdist_entries:
                missing.append(path)
    assert not missing, "glossary resources missing from sdist: %r" % missing


def test_sdist_ships_exactly_54_glossary_resources(sdist_entries: set[str]):
    glossary_files = {
        e
        for e in sdist_entries
        if e.startswith("lingtai/tools/") and "/glossary-" in e and e.endswith(".md")
    }
    assert len(glossary_files) == 54, (
        "expected exactly 54 glossary resources in sdist, got %d" % len(glossary_files)
    )
