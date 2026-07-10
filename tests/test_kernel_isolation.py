"""Test that lingtai_kernel has no dependencies on the lingtai package.

This test ensures the architectural constraint holds:
  - lingtai_kernel can be used standalone (zero hard dependencies)
  - lingtai_kernel never accidentally pulls in lingtai (capabilities, addons, adapters)

The constraint is enforced two ways:
  1. Runtime assert in src/lingtai_kernel/__init__.py
  2. This test: import lingtai_kernel in a subprocess with a clean sys.modules,
     then assert no 'lingtai' package modules leaked in.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_kernel_import_is_clean():
    """Import lingtai_kernel in a fresh subprocess; verify 'lingtai' is not loaded."""
    result = subprocess.run(
        [sys.executable, "-c", "import lingtai_kernel; print('OK')"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, (
        f"lingtai_kernel failed to import.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "lingtai" not in result.stdout, (
        "Test harness output should not mention 'lingtai'"
    )
    assert "OK" in result.stdout, (
        f"lingtai_kernel import did not print confirmation.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_kernel_has_no_lingtai_submodules():
    """Verify lingtai_kernel's own package tree has no imports of the 'lingtai' package."""
    import ast
    from pathlib import Path

    kernel_src = Path(__file__).parent.parent / "src" / "lingtai_kernel"
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
                        and not node.module.startswith("lingtai_kernel.")
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
                            and not alias.name.startswith("lingtai_kernel.")
                        )
                        or alias.name == "tools"
                        or alias.name.startswith("tools.")
                    ):
                        violations.append(f"{py_file.relative_to(kernel_src)}: import {alias.name}")

    assert not violations, (
        "lingtai_kernel contains imports of the 'lingtai' or 'tools' packages. "
        "This violates the architectural constraint that the kernel must never "
        "depend on lingtai or tools (the DAG is lingtai → tools → lingtai_kernel).\n"
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
from lingtai_kernel.message import Message
'''
    tree = ast.parse(source)
    violations = []
    kernel_src = Path(__file__).parent.parent / "src" / "lingtai_kernel"

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and (
                (
                    node.module.startswith("lingtai.")
                    and not node.module.startswith("lingtai_kernel.")
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
                        and not alias.name.startswith("lingtai_kernel.")
                    )
                    or alias.name == "tools"
                    or alias.name.startswith("tools.")
                ):
                    violations.append(f"import {alias.name}")

    assert "import lingtai" in violations
    assert "from lingtai_kernel.message ..." not in violations
    assert "from .tools ..." not in violations


def test_kernel_import_does_not_pull_lingtai():
    """Confirm that importing lingtai_kernel does NOT make 'lingtai' appear in sys.modules."""
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import lingtai_kernel; "
            "leaked = [k for k in sys.modules if (k == 'lingtai' or k.startswith('lingtai.') or k == 'tools' or k.startswith('tools.'))]; "
            "print('LEAKED:', leaked) if leaked else print('CLEAN')"
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, f"Subprocess error:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"The 'lingtai' or 'tools' package leaked into sys.modules after importing lingtai_kernel.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "LEAKED:" not in result.stdout, (
        f"lingtai_kernel caused the following 'lingtai'/'tools' modules to be loaded:\n"
        f"{result.stdout}"
    )


def test_import_tools_does_not_pull_lingtai():
    """`import tools` must not transitively import the `lingtai` package.

    The DAG is `lingtai → tools → lingtai_kernel`; the only `tools → lingtai`
    edge is lazy (inside setup()/handlers), so importing `tools` (and even
    `tools.registry`, which imports the five intrinsic modules and the tool
    i18n catalog) must stay dependency-light and never eagerly pull `lingtai`.
    """
    import os

    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    # Ensure this worktree's src wins over any editable-install .pth that points
    # at a different checkout — otherwise the subprocess may import a stale tree.
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import tools.registry; "
            "leaked = [k for k in sys.modules if k == 'lingtai' or k.startswith('lingtai.')]; "
            "print('LEAKED:', leaked) if leaked else print('CLEAN')"
        ],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )
    assert result.returncode == 0, f"Subprocess error:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"Importing tools.registry eagerly pulled the 'lingtai' package.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
