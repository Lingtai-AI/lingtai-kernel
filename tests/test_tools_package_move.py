"""Post-move validation for the built-in tools relocation (PR3).

After the move, the canonical built-in tools package lives at
``src/lingtai/tools/`` and is imported as ``lingtai.tools``. There must be no
lingering active references to the old top-level ``tools`` package root, no
top-level ``tools`` compatibility shim, and no accidental filesystem-dot
corruption such as ``src/lingtai.tools``.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Active old-root package-reference signatures. These are deliberately narrow
# so the many unrelated English occurrences of the word "tools" (prompt section
# names, MCP method names like tools/list, variable names, the kernel's own
# base_agent/tools.py, English prose such as "absent from tools/list", etc.) are
# not flagged.
#
# 1. statement-position ``import tools`` / ``from tools`` (not ``lingtai.tools``)
# 2. dynamic dotted module strings ``"tools.<sub>`` / ``'tools.<sub>``
# 3. importlib.resources root lookup ``files("tools")``
_PATTERNS = [
    re.compile(r"(?m)^\s*import tools(?![\w])"),
    re.compile(r"(?m)^\s*from tools(?![\w])"),
    re.compile(r"""['"]tools\.[a-z_]"""),
    re.compile(r"""files\(\s*['"]tools['"]\s*\)"""),
]


def _clean_src_env() -> dict[str, str]:
    """Env with ONLY this worktree's src on PYTHONPATH (no inherited shadowing)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(SRC)
    return env

# Paths that may legitimately name the old root to prove its absence, or that
# are immutable historical archives. Everything else must be clean.
_ALLOW_PREFIXES = (
    "reports/",
    "tests/test_tools_package_move.py:",
    "tests/test_kernel_isolation.py:",
    "tests/test_kernel_package_move.py:",
)


def _tracked_text_files() -> list[str]:
    """Return repo-relative paths of tracked text files outside reports/."""
    result = subprocess.run(
        ["git", "ls-files", "src", "tests", "docs", "pyproject.toml",
         "MANIFEST.in", "README.md", "setup.py"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, f"git ls-files failed: {result.stderr}"
    return [p for p in result.stdout.splitlines() if p.strip()]


def test_tools_moved_under_lingtai_namespace():
    """The tools source tree is physically under ``src/lingtai/tools/``."""
    assert (SRC / "lingtai" / "tools" / "__init__.py").is_file()
    assert (SRC / "lingtai" / "tools" / "registry.py").is_file()
    assert (SRC / "lingtai" / "tools" / "i18n" / "en.json").is_file()
    assert not (SRC / "tools").exists()


def test_no_filesystem_dot_corruption():
    """The move produced ``src/lingtai/tools`` (slash), not ``src/lingtai.tools``."""
    assert not (SRC / "lingtai.tools").exists(), (
        "filesystem-dot corruption: src/lingtai.tools exists instead of "
        "src/lingtai/tools"
    )
    # The canonical tools tree must live under the lingtai namespace as a real
    # subdirectory, not a dotted sibling.
    assert (SRC / "lingtai" / "tools").is_dir()


def test_no_top_level_tools_compatibility_shim():
    """``import tools`` must fail: no compatibility shim ships for the old root.

    Uses ``-S`` (skip ``site``) so an unrelated installed/editable ``tools``
    package elsewhere on the machine does not mask the absence of a shim in this
    distribution; only this worktree's ``src`` (on PYTHONPATH) is visible.
    """
    env = _clean_src_env()
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import tools"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
    )
    assert result.returncode != 0, (
        "Bare 'import tools' succeeded — a top-level tools shim still exists.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_pyproject_discovers_tools_via_lingtai_prefix():
    """pyproject.toml discovers only ``lingtai*``; the old ``tools*`` root is gone."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'include = ["lingtai*"]' in pyproject
    assert '"tools*"' not in pyproject
    assert '"lingtai.tools"' in pyproject  # consolidated tool package-data key


def test_manifest_paths_target_lingtai_tools():
    """MANIFEST.in tool resource paths point under ``src/lingtai/tools/``."""
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "src/tools" not in manifest
    assert "recursive-include src/lingtai/tools" in manifest


def test_registry_module_strings_are_canonical():
    """BUILTIN_TOOLS values resolve only under ``lingtai.tools.*``."""
    from lingtai.tools.registry import BUILTIN_TOOLS

    assert BUILTIN_TOOLS, "BUILTIN_TOOLS is empty"
    bad = {k: v for k, v in BUILTIN_TOOLS.items() if not v.startswith("lingtai.tools.")}
    assert not bad, f"registry module strings are not canonical: {bad}"


def test_glossary_resources_resolve_via_lingtai_tools():
    """importlib.resources can find the tools tree as ``lingtai.tools``."""
    from importlib import resources

    root = resources.files("lingtai.tools")
    names = {child.name for child in root.iterdir()}
    # A few representative tool packages must be present as resource dirs.
    for expected in ("bash", "daemon", "email", "system", "registry.py"):
        assert expected in names, f"lingtai.tools missing resource {expected!r}"


def test_facade_tools_objects_are_canonical():
    """Lazy ``lingtai`` facade tool names point at canonical ``lingtai.tools`` sources."""
    import lingtai
    import lingtai.tools.avatar
    import lingtai.tools.bash
    import lingtai.tools.email
    import lingtai.tools.registry

    assert lingtai.setup_capability is lingtai.tools.registry.setup_capability
    assert lingtai.BashManager is lingtai.tools.bash.BashManager
    assert lingtai.AvatarManager is lingtai.tools.avatar.AvatarManager
    assert lingtai.EmailManager is lingtai.tools.email.EmailManager


def test_no_active_old_tools_root_references():
    """No active import/from/dynamic ``tools`` root references outside allowlist."""
    violations: list[str] = []
    for rel in _tracked_text_files():
        if rel.startswith("reports/"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in _PATTERNS:
            for m in pat.finditer(text):
                lineno = text.count("\n", 0, m.start()) + 1
                snippet = text.splitlines()[lineno - 1].strip() if lineno <= len(text.splitlines()) else ""
                entry = f"{rel}:{lineno}: {snippet}"
                if not entry.startswith(_ALLOW_PREFIXES):
                    violations.append(entry)
    assert not violations, (
        "Active source/test/docs/config still reference the old top-level 'tools' "
        "package root (import/from/dynamic/files):\n" + "\n".join(violations)
    )


def test_import_lingtai_tools_registry_does_not_eagerly_load_high_level():
    """``import lingtai.tools.registry`` must not eagerly load agent/providers/services."""
    env = _clean_src_env()
    code = (
        "import sys; import lingtai.tools.registry; "
        "leaked = [k for k in sys.modules "
        "if k.startswith('lingtai.') and k != 'lingtai.kernel' "
        "and not k.startswith('lingtai.kernel.') "
        "and k != 'lingtai.tools' and not k.startswith('lingtai.tools.')]; "
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
        f"lingtai.tools.registry eagerly loaded high-level lingtai modules:\n{result.stdout}"
    )


def test_import_kernel_does_not_load_lingtai_tools():
    """``import lingtai.kernel`` must not pull ``lingtai.tools`` (kernel isolation)."""
    env = _clean_src_env()
    code = (
        "import sys; import lingtai.kernel; "
        "leaked = [k for k in sys.modules "
        "if k == 'lingtai.tools' or k.startswith('lingtai.tools.')]; "
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
        f"lingtai.kernel eagerly loaded lingtai.tools:\n{result.stdout}"
    )
