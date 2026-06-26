"""Tests for the advisory ANATOMY drift checker (issue #509).

The checker lives in tools/ (a dev script, not part of the importable package),
so it is loaded by file path here.
"""

import importlib.util
from pathlib import Path

_CHECKER_PATH = Path(__file__).resolve().parents[1] / "tools" / "check_anatomy_drift.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_anatomy_drift", _CHECKER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


checker = _load_checker()


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_flags_out_of_range_citation(tmp_path):
    _write(tmp_path / "src" / "mod" / "f.py", "a\nb\nc\n")  # 3 lines
    anatomy = _write(
        tmp_path / "src" / "mod" / "ANATOMY.md",
        "- see `mod/f.py:99` for the thing.",
    )
    problems = checker.check_citations(anatomy, tmp_path)
    assert any("out-of-range" in p for p in problems)


def test_flags_missing_citation_target(tmp_path):
    anatomy = _write(
        tmp_path / "src" / "mod" / "ANATOMY.md",
        "- see `mod/gone.py:1` for the thing.",
    )
    problems = checker.check_citations(anatomy, tmp_path)
    assert any("missing citation target" in p for p in problems)


def test_in_range_citation_is_clean(tmp_path):
    _write(tmp_path / "src" / "mod" / "f.py", "a\nb\nc\nd\ne\n")  # 5 lines
    anatomy = _write(
        tmp_path / "src" / "mod" / "ANATOMY.md",
        "- see `mod/f.py:3-5` for the thing.",
    )
    assert checker.check_citations(anatomy, tmp_path) == []


def test_resolve_path_searches_upward(tmp_path):
    target = _write(tmp_path / "src" / "pkg" / "sub" / "x.py", "a\n")
    anatomy = tmp_path / "src" / "pkg" / "sub" / "ANATOMY.md"
    anatomy.parent.mkdir(parents=True, exist_ok=True)
    anatomy.write_text("`sub/x.py`", encoding="utf-8")
    # cited as "sub/x.py" from inside sub/ resolves by walking up to pkg/.
    assert checker.resolve_path("sub/x.py", anatomy, tmp_path) == target


def test_check_mode_exit_code(tmp_path, monkeypatch):
    _write(tmp_path / "src" / "mod" / "f.py", "x\n")
    _write(tmp_path / "src" / "mod" / "ANATOMY.md", "- see `mod/f.py:2`.")
    monkeypatch.chdir(tmp_path)
    assert checker.main(["--root", "src", "--check"]) == 1
    assert checker.main(["--root", "src"]) == 0  # advisory mode never fails
