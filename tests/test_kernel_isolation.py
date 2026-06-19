"""Test that ``lingtai.kernel`` stays self-contained: it never reaches up into
the wrapper layer (capabilities, adapters, services, addons).

The kernel was relocated from the top-level ``lingtai_kernel`` package to
``lingtai.kernel`` (hard cut, no shim). After the move ``import lingtai.kernel``
necessarily initializes the parent ``lingtai`` package — so the old "lingtai
must not appear in sys.modules" invariant no longer holds. The invariant that
DOES still hold, and is the architectural constraint worth enforcing, is:

  - the kernel's own package tree never imports a *wrapper* submodule
    (``lingtai.agent``, ``lingtai.capabilities``, ``lingtai.core``,
    ``lingtai.llm`` adapters, ``lingtai.services``), and
  - importing ``lingtai.kernel`` does not eagerly pull any of those wrapper
    submodules into ``sys.modules``.

This mirrors the boundary asserted from the ``lingtai`` root in
``test_lingtai_import_purity.py``; the two converge on the same wrapper
blocklist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Wrapper submodules the kernel must never depend on. The parent ``lingtai``
# package itself (its thin import-light ``__init__``) and ``lingtai.kernel`` are
# allowed; everything below is the batteries-included layer.
_WRAPPER_SUBMODULES = (
    "lingtai.agent",
    "lingtai.capabilities",
    "lingtai.core",
    "lingtai.llm",
    "lingtai.services",
)


def test_kernel_import_is_clean():
    """Import lingtai.kernel in a fresh subprocess; verify it imports cleanly."""
    result = subprocess.run(
        [sys.executable, "-c", "import lingtai.kernel; print('OK')"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, (
        f"lingtai.kernel failed to import.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "OK" in result.stdout, (
        f"lingtai.kernel import did not print confirmation.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_kernel_has_no_wrapper_submodule_imports():
    """The kernel's package tree must not import any wrapper submodule.

    ``from lingtai.kernel.*`` self-imports are fine. ``from lingtai`` /
    ``from lingtai.<wrapper>`` (anything that is not the kernel subtree) is a
    violation of the one-directional dependency rule.
    """
    import ast

    kernel_src = Path(__file__).parent.parent / "src" / "lingtai" / "kernel"
    violations: list[str] = []

    def _is_violation(dotted: str) -> bool:
        # Allowed: the kernel's own subtree.
        if dotted == "lingtai.kernel" or dotted.startswith("lingtai.kernel."):
            return False
        # A bare ``import lingtai`` (no submodule) only touches the import-light
        # namespace ``__init__`` — e.g. nudge/kernel_version.py reads
        # ``lingtai.__version__``. That is not a wrapper-layer dependency.
        if dotted == "lingtai":
            return False
        # Any *wrapper submodule* under the lingtai namespace is a violation.
        return dotted.startswith("lingtai.")

    for py_file in kernel_src.rglob("*.py"):
        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # Skip relative imports (node.level > 0): they are intra-kernel.
                if node.level == 0 and node.module and _is_violation(node.module):
                    violations.append(
                        f"{py_file.relative_to(kernel_src)}: from {node.module} ..."
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_violation(alias.name):
                        violations.append(
                            f"{py_file.relative_to(kernel_src)}: import {alias.name}"
                        )

    assert not violations, (
        "lingtai.kernel imports a wrapper submodule. This violates the "
        "architectural constraint that the kernel must never depend on the "
        "batteries-included lingtai wrapper.\n" + "\n".join(violations)
    )


def test_kernel_import_does_not_pull_wrapper():
    """Importing lingtai.kernel must not eagerly load any wrapper submodule."""
    wrapper_literal = repr(_WRAPPER_SUBMODULES)
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys; import lingtai.kernel; "
            f"wrapper = {wrapper_literal}; "
            "leaked = [k for k in sys.modules "
            "if any(k == w or k.startswith(w + '.') for w in wrapper)]; "
            "print('LEAKED:', leaked) if leaked else print('CLEAN')"
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, f"Subprocess error:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "CLEAN" in result.stdout, (
        f"A wrapper submodule leaked into sys.modules after importing lingtai.kernel.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "LEAKED:" not in result.stdout, (
        f"lingtai.kernel caused the following wrapper modules to be loaded:\n"
        f"{result.stdout}"
    )
