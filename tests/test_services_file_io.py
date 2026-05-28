"""Tests for FileIOService and LocalFileIOService."""
import os
import tempfile
from pathlib import Path

import pytest

import lingtai.services.file_io as file_io
from lingtai.services.file_io import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_VISITED,
    DEFAULT_WALLTIME_S,
    GrepMatch,
    LocalFileIOService,
    TraversalStats,
)


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def svc(tmp_dir):
    return LocalFileIOService(root=tmp_dir)


class TestLocalFileIOService:
    def test_write_and_read(self, svc, tmp_dir):
        svc.write("hello.txt", "Hello, world!")
        assert svc.read("hello.txt") == "Hello, world!"

    def test_write_creates_parents(self, svc, tmp_dir):
        svc.write("sub/dir/file.txt", "nested")
        assert svc.read("sub/dir/file.txt") == "nested"

    def test_read_nonexistent_raises(self, svc):
        with pytest.raises(FileNotFoundError):
            svc.read("nope.txt")

    def test_edit(self, svc):
        svc.write("edit.txt", "hello world")
        result = svc.edit("edit.txt", "hello", "goodbye")
        assert result == "goodbye world"
        assert svc.read("edit.txt") == "goodbye world"

    def test_edit_not_found_raises(self, svc):
        svc.write("edit.txt", "hello world")
        with pytest.raises(ValueError, match="not found"):
            svc.edit("edit.txt", "missing", "replacement")

    def test_edit_ambiguous_raises(self, svc):
        svc.write("edit.txt", "aaa aaa")
        with pytest.raises(ValueError, match="appears 2 times"):
            svc.edit("edit.txt", "aaa", "bbb")

    def test_glob(self, svc, tmp_dir):
        svc.write("a.py", "# a")
        svc.write("b.py", "# b")
        svc.write("c.txt", "# c")
        results = svc.glob("*.py")
        assert len(results) == 2
        assert all(r.endswith(".py") for r in results)

    def test_glob_nested(self, svc, tmp_dir):
        svc.write("src/main.py", "# main")
        svc.write("src/utils.py", "# utils")
        svc.write("tests/test.py", "# test")
        results = svc.glob("src/*.py")
        assert len(results) == 2

    def test_grep(self, svc, tmp_dir):
        svc.write("a.txt", "hello world\ngoodbye world\nhello again")
        results = svc.grep("hello")
        assert len(results) == 2
        assert results[0].line_number == 1
        assert results[1].line_number == 3

    def test_grep_regex(self, svc, tmp_dir):
        svc.write("a.txt", "foo123\nbar456\nfoo789")
        results = svc.grep(r"foo\d+")
        assert len(results) == 2

    def test_grep_single_file(self, svc, tmp_dir):
        svc.write("a.txt", "match here")
        svc.write("b.txt", "match here too")
        results = svc.grep("match", str(tmp_dir / "a.txt"))
        assert len(results) == 1

    def test_grep_max_results(self, svc, tmp_dir):
        lines = "\n".join(f"line {i}" for i in range(100))
        svc.write("big.txt", lines)
        results = svc.grep("line", max_results=5)
        assert len(results) == 5

    def test_absolute_paths(self, tmp_dir):
        svc = LocalFileIOService()  # no root
        path = str(tmp_dir / "abs.txt")
        svc.write(path, "absolute")
        assert svc.read(path) == "absolute"


class TestTraversalBudgets:
    """Issue #164 — recursive glob/grep must default-prune large
    cache/history dirs and bail out within a wall-clock / visited budget
    instead of wedging the agent on a broad root."""

    def test_glob_skips_default_excluded_dirs(self, svc, tmp_dir):
        # Files that should be visible
        svc.write("src/main.py", "# main")
        svc.write("tests/test_main.py", "# test")
        # Files inside default-excluded dirs that must be pruned
        svc.write(".git/HEAD", "ref: refs/heads/main")
        svc.write("node_modules/foo/index.js", "module.exports = {}")
        svc.write(".venv/lib/python3.11/site-packages/bar.py", "")
        svc.write("__pycache__/x.pyc", "")
        svc.write(".lingtai/agent1/history/chat.jsonl", "{}")
        svc.write("history/old.jsonl", "{}")  # bare `history/` (LingTai workdir layout)
        svc.write("tmp/scratch.txt", "x")
        svc.write("dist/bundle.js", "x")

        results = svc.glob("**/*")
        for r in results:
            assert ".git" not in r
            assert "node_modules" not in r
            assert ".venv" not in r
            assert "__pycache__" not in r
            assert "/history/" not in r and not r.endswith("/history")
            assert "/tmp/" not in r and not r.endswith("/tmp")
            assert "/dist/" not in r and not r.endswith("/dist")
        # Real source files survive
        assert any(r.endswith("/src/main.py") for r in results)
        assert any(r.endswith("/tests/test_main.py") for r in results)

    def test_grep_skips_default_excluded_dirs(self, svc, tmp_dir):
        svc.write("src/main.py", "needle\n")
        svc.write(".git/objects/needle.txt", "needle\n")
        svc.write("node_modules/pkg/index.js", "needle\n")
        svc.write("history/chat.jsonl", "needle\n")

        results = svc.grep("needle")
        files_found = {r.path for r in results}
        assert any(p.endswith("/src/main.py") for p in files_found)
        assert not any(".git" in p for p in files_found)
        assert not any("node_modules" in p for p in files_found)
        assert not any("/history/" in p for p in files_found)

    def test_glob_walltime_budget_returns_partial(self, svc, tmp_dir):
        # Seed many files so the traversal has work to do.
        for i in range(20):
            svc.write(f"sub_{i:03d}/file_{i:03d}.txt", "x")
        # walltime_s=0 forces the budget check to fire on the first
        # directory tick — we should still get back the partial result
        # plus a structured ``truncated_reason``.
        results = svc.glob("**/*", walltime_s=0.0)
        assert isinstance(results, list)
        assert svc.last_traversal.truncated_reason == "walltime"

    def test_glob_walltime_budget_checked_inside_large_file_loop(self, svc, tmp_dir, monkeypatch):
        for i in range(20):
            svc.write(f"file_{i:03d}.txt", "x")

        times = iter([100.0, 100.0, 101.0, 101.0])
        monkeypatch.setattr(file_io.time, "monotonic", lambda: next(times))

        results = svc.glob("**/*", walltime_s=0.5)

        assert results == []
        assert svc.last_traversal.truncated_reason == "walltime"

    def test_grep_visited_budget_returns_partial(self, svc, tmp_dir):
        for i in range(50):
            svc.write(f"f_{i:03d}.txt", "needle\n")
        results = svc.grep("needle", max_results=999, max_visited=5)
        # Either we tripped visited budget or capped on max_results;
        # the contract is "structured partial, agent not wedged".
        assert svc.last_traversal.truncated_reason in {"visited", "max_results"}
        assert svc.last_traversal.elapsed_ms >= 0

    def test_visited_budget_counts_directories(self, svc, tmp_dir):
        for i in range(20):
            svc.write(f"dir_{i:03d}/file.txt", "x")

        results = svc.glob("**/*", max_visited=5)

        assert isinstance(results, list)
        assert svc.last_traversal.truncated_reason == "visited"

    def test_grep_skips_oversized_files(self, svc, tmp_dir):
        svc.write("big.txt", "x" * 50)
        svc.write("small.txt", "needle\n")
        results = svc.grep("needle", max_file_bytes=10)
        # big.txt is skipped; small.txt is read normally.
        files_found = {r.path for r in results}
        assert any(p.endswith("/small.txt") for p in files_found)
        assert not any(p.endswith("/big.txt") for p in files_found)
        assert svc.last_traversal.files_skipped_size >= 1

    def test_last_traversal_resets_per_call(self, svc, tmp_dir):
        svc.write("a.txt", "x")
        svc.glob("**/*", walltime_s=0.0)
        first_reason = svc.last_traversal.truncated_reason
        svc.glob("**/*")  # ample budget
        # second call must reset the stats to a clean state
        assert svc.last_traversal.truncated_reason is None
        assert first_reason == "walltime"

    def test_exclude_dirs_override(self, svc, tmp_dir):
        # Allow the caller to opt back in by passing an empty exclude set.
        svc.write(".git/HEAD", "ref")
        results = svc.glob("**/*", exclude_dirs=set())
        assert any(".git" in r for r in results)



class RecordingBackend:
    """Small backend double proving LocalFileIOService is now a facade."""

    def __init__(self):
        self.last_traversal = TraversalStats(truncated_reason="backend")
        self.calls = []

    def read(self, path: str) -> str:
        self.calls.append(("read", path))
        return "read-result"

    def write(self, path: str, content: str) -> None:
        self.calls.append(("write", path, content))

    def edit(self, path: str, old_string: str, new_string: str) -> str:
        self.calls.append(("edit", path, old_string, new_string))
        return "edited"

    def glob(self, pattern: str, root: str | None = None, **kwargs):
        self.calls.append(("glob", pattern, root, kwargs))
        return ["one.py"]

    def grep(self, pattern: str, path: str | None = None, max_results: int = 50, **kwargs):
        self.calls.append(("grep", pattern, path, max_results, kwargs))
        return [GrepMatch("one.py", 1, "needle")]


def test_local_file_io_service_delegates_all_operations_to_backend():
    backend = RecordingBackend()
    svc = LocalFileIOService(backend=backend)

    assert svc.read("a.txt") == "read-result"
    svc.write("a.txt", "body")
    assert svc.edit("a.txt", "old", "new") == "edited"
    assert svc.glob("*.py", root="src", max_results=7) == ["one.py"]
    assert svc.glob("*.py", root="src") == ["one.py"]
    assert svc.grep("needle", path="src", max_results=3) == [GrepMatch("one.py", 1, "needle")]
    assert svc.last_traversal.truncated_reason == "backend"

    assert backend.calls == [
        ("read", "a.txt"),
        ("write", "a.txt", "body"),
        ("edit", "a.txt", "old", "new"),
        ("glob", "*.py", "src", {
            "exclude_dirs": None,
            "walltime_s": DEFAULT_WALLTIME_S,
            "max_visited": DEFAULT_MAX_VISITED,
            "max_results": 7,
        }),
        ("glob", "*.py", "src", {
            "exclude_dirs": None,
            "walltime_s": DEFAULT_WALLTIME_S,
            "max_visited": DEFAULT_MAX_VISITED,
            "max_results": 2000,
        }),
        ("grep", "needle", "src", 3, {
            "exclude_dirs": None,
            "walltime_s": DEFAULT_WALLTIME_S,
            "max_visited": DEFAULT_MAX_VISITED,
            "max_file_bytes": DEFAULT_MAX_FILE_BYTES,
        }),
    ]
