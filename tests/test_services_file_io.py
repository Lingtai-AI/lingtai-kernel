"""Tests for FileIOService and LocalFileIOService."""
import os
import tempfile
from pathlib import Path

import pytest

import lingtai.services.file_io as file_io
from lingtai.services.file_io import LocalFileIOService, GrepMatch


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


class TestGrepGlobFilter:
    """``glob_filter`` should prune the candidate set *before* stat/read.

    Pre-filtering matters because the previous tool wrapper post-filtered
    full ``GrepMatch`` results — every file under the search root still
    got opened and scanned even when callers narrowed by ``glob`` (e.g.
    ``glob="*.py"`` over a 50k-file repo). The contract these tests pin:

    1. Non-matching files are not opened (no ``open`` / ``read_text``
       calls).
    2. Matching files still yield the same results as an unfiltered run.
    3. ``glob_filter=None`` / ``"*"`` is a no-op (back-compat).
    """

    def test_glob_filter_skips_non_matching_files_before_open(
        self, svc, tmp_dir, monkeypatch
    ):
        # Layout: one .py file that matches the regex, one .log file that
        # would also match — but the glob filter should hide the .log.
        svc.write("good.py", "needle here\n")
        svc.write("noisy.log", "needle here\n")

        opened: list[str] = []
        real_open = file_io.open if hasattr(file_io, "open") else open
        import builtins

        real_builtin_open = builtins.open

        def tracking_open(path, *a, **kw):
            opened.append(str(path))
            return real_builtin_open(path, *a, **kw)

        # Patch the name resolved inside file_io.py so we observe only
        # the grep code's opens, not those from the test harness.
        monkeypatch.setattr(file_io, "open", tracking_open, raising=False)
        # ``open`` isn't a module-level name in file_io.py — patch the
        # builtin so the ``open(f, ...)`` call inside grep is intercepted.
        monkeypatch.setattr("builtins.open", tracking_open)

        results = svc.grep("needle", glob_filter="*.py")

        # Only the .py file's match comes back.
        assert len(results) == 1
        assert results[0].path.endswith("/good.py")
        # And the .log was never opened by grep — pre-filter pruned it.
        opened_by_grep = [p for p in opened if p.endswith(".log")]
        assert opened_by_grep == [], f"unexpected reads of excluded files: {opened_by_grep}"

    def test_glob_filter_none_matches_all(self, svc, tmp_dir):
        svc.write("a.py", "needle\n")
        svc.write("b.txt", "needle\n")
        results = svc.grep("needle", glob_filter=None)
        files = {r.path for r in results}
        assert any(p.endswith("/a.py") for p in files)
        assert any(p.endswith("/b.txt") for p in files)

    def test_glob_filter_star_matches_all(self, svc, tmp_dir):
        # "*" is the schema default for the grep tool; treat it as no-op.
        svc.write("a.py", "needle\n")
        svc.write("b.txt", "needle\n")
        results = svc.grep("needle", glob_filter="*")
        assert len(results) == 2

    def test_glob_filter_works_with_nested_layout(self, svc, tmp_dir):
        svc.write("src/main.py", "needle\n")
        svc.write("src/data.json", "needle\n")
        svc.write("tests/test_main.py", "needle\n")
        results = svc.grep("needle", glob_filter="*.py")
        files = {r.path for r in results}
        assert any(p.endswith("/src/main.py") for p in files)
        assert any(p.endswith("/tests/test_main.py") for p in files)
        assert not any(p.endswith(".json") for p in files)


class TestGrepLineStreaming:
    """Grep streams files line-by-line instead of slurping into memory.

    Two invariants the tests pin:

    1. A genuinely undecodable file is still skipped (no crash) and
       counted in ``files_skipped_binary``.
    2. A *mostly* utf-8 file with a few bad bytes mid-stream is still
       searchable — we use ``errors="replace"`` so one rogue byte does
       not blank the whole file the way ``read_text`` did.
    """

    def test_grep_handles_mixed_utf8_with_bad_bytes(self, svc, tmp_dir):
        # File with a valid utf-8 needle line, plus a line containing an
        # invalid utf-8 continuation byte. ``read_text(utf-8)`` would
        # raise UnicodeDecodeError on the whole file; the streamed
        # ``errors="replace"`` path keeps the good lines searchable.
        path = tmp_dir / "mixed.txt"
        path.write_bytes(b"needle here\n\xff\xfe garbage\nanother needle\n")

        results = svc.grep("needle")
        files = {r.path for r in results}
        assert any(p.endswith("/mixed.txt") for p in files)
        # Both clean ``needle`` lines should match — the garbage line in
        # the middle does not abort the file.
        needle_matches = [r for r in results if r.path.endswith("/mixed.txt")]
        assert len(needle_matches) == 2
        assert needle_matches[0].line_number == 1
        assert needle_matches[1].line_number == 3


    def test_grep_skips_nul_binary_files(self, svc, tmp_dir):
        # NUL-byte prefix sniff mirrors ripgrep-style binary detection: an
        # obvious binary file should be skipped even if it contains bytes
        # that would otherwise decode via errors="replace".
        path = tmp_dir / "binary.bin"
        path.write_bytes(b"\x00needle\x00\xff\xfe\x00")

        results = svc.grep("needle")

        assert not any(r.path.endswith("/binary.bin") for r in results)
        assert svc.last_traversal.files_skipped_binary >= 1

    def test_grep_skips_unreadable_files_without_crashing(
        self, svc, tmp_dir, monkeypatch
    ):
        svc.write("ok.txt", "needle\n")
        svc.write("locked.txt", "needle\n")

        import builtins
        real_open = builtins.open

        def selective_open(path, *a, **kw):
            if str(path).endswith("/locked.txt"):
                raise PermissionError("nope")
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", selective_open)

        results = svc.grep("needle")
        files = {r.path for r in results}
        assert any(p.endswith("/ok.txt") for p in files)
        assert not any(p.endswith("/locked.txt") for p in files)
        assert svc.last_traversal.files_skipped_binary >= 1

    def test_grep_does_not_load_whole_file_into_memory(self, svc, tmp_dir, monkeypatch):
        # Pin the streaming contract: ``read_text`` (whole-file read)
        # must not be called by grep. Anyone reintroducing it would
        # regress memory behavior on large logs / jsonl.
        svc.write("a.txt", "needle\n")

        original = Path.read_text
        calls: list[str] = []

        def spy(self, *a, **kw):
            calls.append(str(self))
            return original(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", spy)
        results = svc.grep("needle")
        assert len(results) >= 1
        assert calls == [], f"grep should stream, not call read_text: {calls}"
