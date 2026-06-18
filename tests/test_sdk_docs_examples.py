"""Docs-examples validation: cheap, offline, no secrets.

This guards the SDK developer docs (`docs/sdk/README.md`, its examples index, and
the committed example scripts) against rot, without booting an agent or making a
network call:

1. every fenced ```python block in the SDK docs *parses* (``ast.parse``), so a
   snippet can never silently become invalid Python;
2. every committed `docs/sdk/examples/*.py` *parses and compiles*;
3. each self-contained example module *imports and runs* its ``main()`` offline
   (they inject a fake runtime or stop before booting an agent), so the public
   `lingtai_sdk` surface they document stays real.

Matches the SDK's existing import-purity test ethos: assert behavior through the
public doorway, cheaply, with no provider SDK or key required.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOCS_SDK = _REPO_ROOT / "docs" / "sdk"
_EXAMPLES_DIR = _DOCS_SDK / "examples"

# Fenced ```python (or ```py) blocks, capturing the body.
_FENCE_RE = re.compile(r"```(?:python|py)\n(.*?)```", re.DOTALL)


def _doc_markdown_files() -> list[Path]:
    return [_DOCS_SDK / "README.md", _EXAMPLES_DIR / "README.md"]


def _example_files() -> list[Path]:
    return sorted(_EXAMPLES_DIR.glob("*.py"))


def _python_snippets(md_path: Path) -> list[tuple[int, str]]:
    """Return (1-based start line, source) for each fenced python block."""
    text = md_path.read_text(encoding="utf-8")
    snippets: list[tuple[int, str]] = []
    for match in _FENCE_RE.finditer(text):
        start_line = text.count("\n", 0, match.start()) + 1
        snippets.append((start_line, match.group(1)))
    return snippets


def test_docs_sdk_layout_exists() -> None:
    """The guide, the examples index, and at least one example are present."""
    assert (_DOCS_SDK / "README.md").is_file()
    assert (_EXAMPLES_DIR / "README.md").is_file()
    assert _example_files(), "expected at least one docs/sdk/examples/*.py"


def test_guide_has_python_snippets() -> None:
    """The main guide carries the quick-example snippets it promises."""
    assert _python_snippets(_DOCS_SDK / "README.md"), (
        "expected python snippets in the SDK guide"
    )


@pytest.mark.parametrize("md_path", _doc_markdown_files(), ids=lambda p: p.name)
def test_doc_python_snippets_parse(md_path: Path) -> None:
    """Every fenced ```python block in the SDK docs is valid Python.

    A doc with no python fences (e.g. the examples index, which only shows a
    shell command) trivially passes.
    """
    for start_line, source in _python_snippets(md_path):
        try:
            ast.parse(source)
        except SyntaxError as exc:  # pragma: no cover - failure path
            pytest.fail(
                f"{md_path}: python snippet starting at line {start_line} "
                f"does not parse: {exc}"
            )


@pytest.mark.parametrize("py_path", _example_files(), ids=lambda p: p.name)
def test_example_file_compiles(py_path: Path) -> None:
    """Every committed example script parses and compiles."""
    source = py_path.read_text(encoding="utf-8")
    compile(source, str(py_path), "exec")


def _load_example_module(py_path: Path):
    spec = importlib.util.spec_from_file_location(
        f"_sdk_doc_example_{py_path.stem}", py_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("py_path", _example_files(), ids=lambda p: p.name)
def test_example_runs_offline(py_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Each example imports and its ``main()`` runs offline (no key, no network).

    The examples either inject a fake runtime or stop before booting an agent, so
    importing and calling ``main()`` exercises the documented public surface
    without provider SDKs or credentials.
    """
    module = _load_example_module(py_path)
    assert hasattr(module, "main"), f"{py_path} has no main()"
    module.main()
    out = capsys.readouterr().out
    assert out.strip(), f"{py_path} main() produced no output"
