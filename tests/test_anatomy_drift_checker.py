"""Tests for the advisory ANATOMY drift checker (issue #509).

The checker is owned by the ``lingtai-kernel-anatomy`` intrinsic skill and
ships at ``src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/``; it
is a skill sidecar (not part of the importable package), so it is loaded by
file path here.
"""

import importlib.util
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SKILL_DIR = ROOT / "src" / "lingtai" / "intrinsic_skills" / "lingtai-kernel-anatomy"
_CHECKER_PATH = _SKILL_DIR / "scripts" / "check_anatomy_drift.py"
_BENCH_PATH = _SKILL_DIR / "scripts" / "bench_agent_session_rebuild.py"


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


def test_checker_lives_in_skill_bundle():
    """The checker's only operational ownership is the anatomy skill bundle."""
    assert _CHECKER_PATH.is_file(), f"checker missing from skill bundle: {_CHECKER_PATH}"
    # No repo-root scripts/ directory should exist (PR #827 ownership contract).
    assert not (ROOT / "scripts").is_dir(), "stale repo-root scripts/ directory"
    assert not (ROOT / "tools").is_dir(), "stale repo-root tools/ directory"


def test_skill_sidecar_modes():
    """Checker is executable (100755); benchmark is not (100644)."""
    assert _BENCH_PATH.is_file(), f"benchmark missing from skill bundle: {_BENCH_PATH}"
    checker_mode = stat.S_IMODE(_CHECKER_PATH.stat().st_mode)
    bench_mode = stat.S_IMODE(_BENCH_PATH.stat().st_mode)
    assert checker_mode & stat.S_IXUSR, f"checker not executable: {oct(checker_mode)}"
    assert not (bench_mode & stat.S_IXUSR), f"benchmark unexpectedly executable: {oct(bench_mode)}"


def _load_bench():
    spec = importlib.util.spec_from_file_location("bench_agent_session_rebuild", _BENCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bench_finds_repo_src_from_repo_root(monkeypatch):
    """Benchmark repository-source locator resolves the in-tree src from cwd=repo root."""
    bench = _load_bench()
    monkeypatch.chdir(ROOT)
    src = bench._find_repo_src()
    assert src is not None and (src / "lingtai_kernel").is_dir()


def test_bench_finds_repo_src_via_ancestor_search(tmp_path, monkeypatch):
    """Even away from the repo root, the ancestor walk still locates in-tree src."""
    bench = _load_bench()
    monkeypatch.chdir(tmp_path)  # no src/lingtai_kernel here
    src = bench._find_repo_src()
    assert src is not None and (src / "lingtai_kernel").is_dir()
