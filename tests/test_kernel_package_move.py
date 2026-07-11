"""Post-move validation for the kernel package relocation (PR2).

After the move, the canonical kernel package lives at ``src/lingtai/kernel/``
and is imported as ``lingtai.kernel``. There must be no lingering active
references to the old top-level ``lingtai_kernel`` package root.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def test_kernel_moved_under_lingtai_namespace():
    """The kernel source tree is physically under ``src/lingtai/kernel/``."""
    assert (SRC / "lingtai" / "kernel" / "__init__.py").is_file()
    assert (SRC / "lingtai" / "kernel" / "i18n" / "en.json").is_file()
    assert not (SRC / "lingtai_kernel").exists()


def test_pyproject_discovers_kernel_via_lingtai_prefix():
    """pyproject.toml includes lingtai* and tools* but not the old root."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'include = ["lingtai*", "tools*"]' in pyproject
    assert "lingtai_kernel*" not in pyproject
    assert '"lingtai.kernel" = ["i18n/*.json"]' in pyproject


def test_no_active_lingtai_kernel_references():
    """Only historical release archives retain the old package root name."""
    result = subprocess.run(
        ["git", "grep", "-n", "lingtai_kernel"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode in (0, 1), f"git grep failed: {result.stderr}"
    matches = [line for line in result.stdout.splitlines() if line.strip()]
    # This checker file intentionally names the old root to prove it is gone.
    allowed_prefixes = ("reports/", "tests/test_kernel_package_move.py:")
    active = [m for m in matches if not any(m.startswith(p) for p in allowed_prefixes)]
    assert not active, (
        "Active source/test/docs/metadata still reference the old 'lingtai_kernel' root:\n"
        + "\n".join(active)
    )


def test_facade_objects_are_canonical_kernel_objects():
    """Lazy ``lingtai`` facade names point at the canonical ``lingtai.kernel`` sources."""
    import lingtai  # noqa: F401
    import lingtai.kernel.base_agent
    import lingtai.kernel.config
    import lingtai.kernel.message
    import lingtai.kernel.services.logging
    import lingtai.kernel.services.mail
    import lingtai.kernel.state
    import lingtai.kernel.types

    assert lingtai.BaseAgent is lingtai.kernel.base_agent.BaseAgent
    assert lingtai.AgentConfig is lingtai.kernel.config.AgentConfig
    assert lingtai.AgentState is lingtai.kernel.state.AgentState
    assert lingtai.Message is lingtai.kernel.message.Message
    assert lingtai.MSG_REQUEST is lingtai.kernel.message.MSG_REQUEST
    assert lingtai.MSG_USER_INPUT is lingtai.kernel.message.MSG_USER_INPUT
    assert lingtai.UnknownToolError is lingtai.kernel.types.UnknownToolError
    assert lingtai.MailService is lingtai.kernel.services.mail.MailService
    assert lingtai.FilesystemMailService is lingtai.kernel.services.mail.FilesystemMailService
    assert lingtai.LoggingService is lingtai.kernel.services.logging.LoggingService
    assert lingtai.JSONLLoggingService is lingtai.kernel.services.logging.JSONLLoggingService


def test_kernel_i18n_files_present():
    """Kernel i18n catalogs moved with the package."""
    i18n_dir = SRC / "lingtai" / "kernel" / "i18n"
    for lang in ("en", "zh", "wen"):
        assert (i18n_dir / f"{lang}.json").is_file()


def test_kernel_import_does_not_load_high_level_modules():
    """import lingtai.kernel in a fresh process loads only the parent and kernel."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import sys; import lingtai.kernel; "
        "leaked = [k for k in sys.modules "
        "if k.startswith('lingtai.') and k != 'lingtai.kernel' and not k.startswith('lingtai.kernel.')]; "
        "print('LEAKED:', leaked) if leaked else print('CLEAN')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stdout}\n{result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"lingtai.kernel pulled high-level lingtai modules:\n{result.stdout}"
    )


def test_tools_import_does_not_load_high_level_lingtai():
    """Importing ``tools`` may load ``lingtai.kernel`` but no high-level ``lingtai.*``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    code = (
        "import sys; import tools.registry; "
        "leaked = [k for k in sys.modules "
        "if k.startswith('lingtai.') and k != 'lingtai.kernel' and not k.startswith('lingtai.kernel.')]; "
        "print('LEAKED:', leaked) if leaked else print('CLEAN')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stdout}\n{result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"tools.registry pulled high-level lingtai modules:\n{result.stdout}"
    )
