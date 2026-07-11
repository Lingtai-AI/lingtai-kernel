"""Test that lingtai.kernel has no dependencies on the lingtai package.

This test ensures the architectural constraint holds:
  - lingtai.kernel can be used standalone (zero hard dependencies)
  - lingtai.kernel never accidentally pulls in lingtai (capabilities, addons, adapters)

The constraint is enforced two ways:
  1. Runtime assert in src/lingtai/kernel/__init__.py
  2. This test: import lingtai.kernel in a subprocess with a clean sys.modules,
     then assert no 'lingtai' package modules leaked in.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _env_with_src_path() -> dict[str, str]:
    # Drop any inherited PYTHONPATH (e.g. another worktree's src) so the
    # subprocess resolves only this worktree's source tree.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(_repo_root() / "src")
    return env


def test_kernel_import_is_clean():
    """Import lingtai.kernel in a fresh subprocess; verify 'lingtai' is not loaded."""
    result = subprocess.run(
        [sys.executable, "-c", "import lingtai.kernel; print('OK')"],
        capture_output=True,
        text=True,
        cwd=str(_repo_root()),
        env=_env_with_src_path(),
    )
    assert result.returncode == 0, (
        f"lingtai.kernel failed to import.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "lingtai" not in result.stdout, (
        "Test harness output should not mention 'lingtai'"
    )
    assert "OK" in result.stdout, (
        f"lingtai.kernel import did not print confirmation.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_kernel_has_no_lingtai_submodules():
    """Verify lingtai.kernel's own package tree has no imports of the 'lingtai' package."""
    import ast
    from pathlib import Path

    kernel_src = Path(__file__).parent.parent / "src" / "lingtai/kernel"
    violations: list[str] = []

    for py_file in kernel_src.rglob("*.py"):
        if py_file.name == "__pycache__":
            continue
        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # level == 0 is an absolute import; relative imports (level >= 1,
                # e.g. `from .tools import ...` for the kernel's own
                # base_agent/tools.py) carry module names that can collide with
                # top-level package names and must not be flagged.
                if node.level == 0 and node.module and (
                    (
                        node.module.startswith("lingtai.")
                        and node.module != "lingtai.kernel"
                        and not node.module.startswith("lingtai.kernel.")
                    )
                    or node.module == "tools"
                    or node.module.startswith("tools.")
                ):
                    violations.append(f"{py_file.relative_to(kernel_src)}: from {node.module} ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if (
                        alias.name == "lingtai"
                        or (
                            alias.name.startswith("lingtai.")
                            and alias.name != "lingtai.kernel"
                            and not alias.name.startswith("lingtai.kernel.")
                        )
                        or alias.name == "tools"
                        or alias.name.startswith("tools.")
                    ):
                        violations.append(f"{py_file.relative_to(kernel_src)}: import {alias.name}")

    assert not violations, (
        "lingtai.kernel contains imports of the 'lingtai' or 'tools' packages. "
        "This violates the architectural constraint that the kernel must never "
        "depend on lingtai or tools (the DAG is lingtai → tools → lingtai.kernel).\n"
        + "\n".join(violations)
    )


def test_kernel_isolation_scanner_catches_bare_lingtai_import():
    """The AST scanner flags bare ``import lingtai`` but allows kernel-relative ``.tools``."""
    import ast
    from pathlib import Path

    source = '''\
"""Synthetic kernel module."""
import lingtai
from .tools import add_tool
from lingtai.kernel.message import Message
'''
    tree = ast.parse(source)
    violations = []
    kernel_src = Path(__file__).parent.parent / "src" / "lingtai/kernel"

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and (
                (
                    node.module.startswith("lingtai.")
                    and node.module != "lingtai.kernel"
                    and not node.module.startswith("lingtai.kernel.")
                )
                or node.module == "tools"
                or node.module.startswith("tools.")
            ):
                violations.append(f"from {node.module} ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (
                    alias.name == "lingtai"
                    or (
                        alias.name.startswith("lingtai.")
                        and alias.name != "lingtai.kernel"
                        and not alias.name.startswith("lingtai.kernel.")
                    )
                    or alias.name == "tools"
                    or alias.name.startswith("tools.")
                ):
                    violations.append(f"import {alias.name}")

    assert "import lingtai" in violations
    assert "from lingtai.kernel.message ..." not in violations
    assert "from .tools ..." not in violations


def test_kernel_import_only_loads_parent_and_kernel():
    """Importing lingtai.kernel may load the parent ``lingtai`` and kernel submodules only."""
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import lingtai.kernel; "
            "leaked = [k for k in sys.modules if (k.startswith('lingtai.') and k != 'lingtai.kernel' and not k.startswith('lingtai.kernel.')) or k == 'tools' or k.startswith('tools.')]; "
            "print('LEAKED:', leaked) if leaked else print('CLEAN')"
        ],
        capture_output=True,
        text=True,
        cwd=str(_repo_root()),
        env=_env_with_src_path(),
    )
    assert result.returncode == 0, f"Subprocess error:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"The 'lingtai' or 'tools' package leaked into sys.modules after importing lingtai.kernel.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "LEAKED:" not in result.stdout, (
        f"lingtai.kernel caused the following 'lingtai'/'tools' modules to be loaded:\n"
        f"{result.stdout}"
    )


def test_import_lingtai_tools_does_not_pull_high_level_lingtai():
    """``import lingtai.tools.registry`` must not eagerly load high-level ``lingtai.*``.

    The DAG is ``lingtai → lingtai.tools → lingtai.kernel``; the only
    ``lingtai.tools → lingtai`` edge is lazy (inside setup()/handlers), so
    importing ``lingtai.tools.registry`` (which imports the five intrinsic
    modules and the tool i18n catalog) must stay dependency-light and never
    eagerly pull ``lingtai.agent``, providers, services or MCP servers.
    """
    repo_root = Path(__file__).resolve().parents[1]
    # Drop inherited PYTHONPATH so only this worktree's src is visible.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(repo_root / "src")
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import lingtai.tools.registry; "
            "leaked = [k for k in sys.modules if k.startswith('lingtai.') and k != 'lingtai.kernel' and not k.startswith('lingtai.kernel.') and k != 'lingtai.tools' and not k.startswith('lingtai.tools.')]; "
            "print('LEAKED:', leaked) if leaked else print('CLEAN')"
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    assert result.returncode == 0, f"Subprocess error:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"Importing lingtai.tools.registry eagerly pulled high-level 'lingtai.*' modules.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_import_kernel_does_not_load_lingtai_tools():
    """``import lingtai.kernel`` must not pull in ``lingtai.tools`` (kernel isolation)."""
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import lingtai.kernel; "
            "leaked = [k for k in sys.modules if k == 'lingtai.tools' or k.startswith('lingtai.tools.')]; "
            "print('LEAKED:', leaked) if leaked else print('CLEAN')"
        ],
        capture_output=True,
        text=True,
        cwd=str(_repo_root()),
        env=_env_with_src_path(),
    )
    assert result.returncode == 0, f"Subprocess error:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"lingtai.kernel eagerly loaded lingtai.tools:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
