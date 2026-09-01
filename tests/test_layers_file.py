"""Tests for the file I/O operations (read, write, edit, glob, grep).

These five are no longer separate model-facing tools: they are the canonical
actions of the one public ``file`` family, whose single package owns both the
envelope and the operation bodies (``src/lingtai/tools/file/_read.py`` and
siblings). These tests exercise the same behavior through the family envelope.
Family-level schema/envelope evidence lives in
``tests/test_file_tool_family.py``.
"""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lingtai.agent import Agent
from tests._service_helpers import make_gemini_mock_service as make_mock_service
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store


def file_call(agent, action, **input_):
    """Invoke one ``file`` family action with the LTP v2 envelope.

    Optional per-action fields are omitted rather than passed as ``null``;
    ``ToolFamily.handle`` accepts any subset of the child's own declared
    properties, and the operation applies its own historical defaults either
    way.
    """
    return agent._tool_handlers["file"](
        {"action": action, "input": input_, "reasoning": "file layer test"}
    )


def test_file_capability_registers_one_family_root(tmp_path):
    """capabilities=["file"] registers the one public ``file`` family tool."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["file"],
    )
    assert "file" in agent._tool_handlers, "file not registered"
    # The five pre-migration model-facing roots are gone; their operations are
    # now canonical actions of the one family.
    for retired in ("read", "write", "edit", "glob", "grep"):
        assert retired not in agent._tool_handlers, f"{retired} must not be a public root"
    agent.stop(timeout=1.0)


def test_file_capability_dict_form(tmp_path):
    """capabilities={"file": {}} (dict form) registers the same one root."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"file": {}},
    )
    assert "file" in agent._tool_handlers, "file not registered (dict form)"
    agent.stop(timeout=1.0)


def test_legacy_file_capability_names_fail_loudly(tmp_path):
    """The five old capability names are gone — not aliased, not silent.

    Before the migration these were five independent capabilities. The
    migration was a clean break with no compatibility alias, so each name is
    now simply unknown: ``setup_capability`` raises with the available set, and
    the Agent records the skip rather than quietly materializing something the
    caller did not ask for.
    """
    from lingtai.tools.registry import canonical_capability_name, setup_capability

    for legacy in ("read", "write", "edit", "glob", "grep"):
        # No alias entry: the name canonicalizes to itself.
        assert canonical_capability_name(legacy) == legacy
        with pytest.raises(ValueError, match=f"Unknown capability: '{legacy}'"):
            setup_capability(object(), legacy)

    # ``file`` remains the one canonical name that does resolve.
    assert canonical_capability_name("file") == "file"


def test_legacy_capability_name_does_not_silently_register_file(tmp_path):
    """An old preset naming ``read`` gets the core-default file tool only.

    ``file`` is in ``CORE_DEFAULTS``, so it boots regardless; what must not
    happen is the retired name being treated as a request for it. The unknown
    name is skipped with a recorded reason (Agent's pre-existing tolerance for
    wizard-written presets), never resolved.
    """
    agent = Agent(
        service=make_mock_service(), agent_name="test",
        working_dir=tmp_path / "legacy", capabilities=["read"],
    )
    try:
        registered = {name for name, _ in agent._capabilities}
        assert "read" not in registered
        assert "read" not in agent._tool_handlers
    finally:
        agent.stop(timeout=1.0)


def test_disable_is_family_grained_and_legacy_names_do_not_disable(tmp_path):
    """`disable` is family-grained, and the retired names disable nothing.

    Collapsing the five roots into one family means a single action can no
    longer be disabled on its own. Because the migration also removed the
    legacy capability aliases, `disable=["edit"]` no longer silently removes
    the whole `file` capability either — `edit` is simply not a capability
    name, so the disable list does not match anything and `file` stays.
    Disabling the real capability by its canonical name still works.
    """
    stale = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "stale",
        disable=["edit"],
    )
    assert "file" in stale._tool_handlers, \
        "a retired operation name must not disable the file capability"
    stale.stop(timeout=1.0)

    canonical = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "canonical",
        disable=["file"],
    )
    assert "file" not in canonical._tool_handlers
    canonical.stop(timeout=1.0)


def test_write_and_read_via_capability(tmp_path):
    """Write and read files through capability handlers."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["file"],
    )
    # Write
    write_result = file_call(
        agent, "write",
        file_path=str(agent.working_dir / "test.txt"), content="hello world",
    )
    assert write_result["status"] == "ok"

    # Read
    read_result = file_call(
        agent, "read", file_path=str(agent.working_dir / "test.txt"),
    )
    assert "hello world" in read_result["content"]
    agent.stop(timeout=1.0)


def test_edit_via_capability(tmp_path):
    """Edit files through capability handler."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["file"],
    )
    (agent.working_dir / "test.txt").write_text("hello world")
    result = file_call(
        agent, "edit",
        file_path=str(agent.working_dir / "test.txt"),
        old_string="hello", new_string="goodbye",
    )
    assert result["status"] == "ok"
    assert (agent.working_dir / "test.txt").read_text() == "goodbye world"
    agent.stop(timeout=1.0)


def test_glob_via_capability(tmp_path):
    """Glob files through capability handler."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["file"],
    )
    (agent.working_dir / "a.py").write_text("pass")
    (agent.working_dir / "b.py").write_text("pass")
    (agent.working_dir / "c.txt").write_text("text")
    result = file_call(agent, "glob", pattern="*.py", path=str(agent.working_dir))
    # Agent init may create library files; assert user files are present.
    matched_names = {Path(p).name for p in result["matches"]}
    assert "a.py" in matched_names
    assert "b.py" in matched_names
    assert "c.txt" not in matched_names
    agent.stop(timeout=1.0)


def test_grep_via_capability(tmp_path):
    """Grep files through capability handler."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["file"],
    )
    (agent.working_dir / "test.py").write_text("def hello():\n    pass\n")
    result = file_call(agent, "grep", pattern="def hello", path=str(agent.working_dir))
    assert result["count"] >= 1
    agent.stop(timeout=1.0)


def test_file_capability_relative_paths_resolve_under_workdir(tmp_path):
    """Relative file tool paths are rooted under the agent working directory."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["file"],
    )
    target = agent.working_dir / "nested" / "target.py"
    try:
        write_result = file_call(
            agent, "write", file_path="nested/target.py", content="needle\nold\n",
        )
        assert write_result == {
            "status": "ok",
            "path": str(target),
            "bytes": len("needle\nold\n".encode("utf-8")),
        }
        assert target.read_text() == "needle\nold\n"

        read_result = file_call(agent, "read", file_path="nested/target.py")
        assert "needle" in read_result["content"]

        edit_result = file_call(
            agent, "edit", file_path="nested/target.py", old_string="old", new_string="new",
        )
        assert edit_result["status"] == "ok"
        assert target.read_text() == "needle\nnew\n"

        glob_result = file_call(agent, "glob", pattern="*.py", path="nested")
        assert str(target) in glob_result["matches"]

        grep_result = file_call(agent, "grep", pattern="needle", path="nested")
        assert any(match["file"] == str(target) for match in grep_result["matches"])
    finally:
        agent.stop(timeout=1.0)


def test_base_agent_has_no_file_intrinsics(tmp_path):
    """BaseAgent should NOT have file intrinsics after phase 2."""
    from lingtai.kernel.base_agent import BaseAgent
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    for name in ("read", "write", "edit", "glob", "grep"):
        assert name not in agent._intrinsics, f"{name} should not be in BaseAgent intrinsics"
    agent.stop(timeout=1.0)


def test_base_agent_kernel_only(tmp_path):
    """BaseAgent's exact intrinsic set.

    ``psyche`` replaced the former ``pad`` and ``lingtai`` roots (and the
    ``knowledge``/``skills`` capability tools) as the one public root for the
    four durable domains.
    """
    from lingtai.kernel.base_agent import BaseAgent
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test", workdir_lease=make_test_lease(), snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test"))
    assert set(agent._intrinsics.keys()) == {
        "email", "system", "context", "psyche", "soul",
    }
    agent.stop(timeout=1.0)


def test_file_capability_uses_file_io_service(tmp_path):
    """File capabilities should use the agent's FileIOService."""
    from lingtai.services.file_io import LocalFileIOService
    svc = LocalFileIOService(root=tmp_path)
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        file_io=svc,
        capabilities=["file"],
    )
    result = file_call(
        agent, "write", file_path=str(tmp_path / "test.txt"), content="via service",
    )
    assert result["status"] == "ok"
    assert (tmp_path / "test.txt").read_text() == "via service"
    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# C2 — error format consistency across file tools.
# Before fix: read returned {"status": "error", "message": ...} but
# write/edit/glob/grep returned {"error": ...}. tool_executor checks
# result.get("status") == "error" to populate collected_errors, so the
# four bare-error tools were silently dropped from kernel error tracking.
# ---------------------------------------------------------------------------


def _file_agent(tmp_path):
    return Agent(
        service=make_mock_service(), agent_name="test",
        working_dir=tmp_path / "test", capabilities=["file"],
    )


def _assert_error_shape(result, *, expect_substring=None):
    assert result.get("status") == "error", \
        f"expected status='error', got {result!r}"
    assert "message" in result, f"missing 'message' key in {result!r}"
    assert "error" not in result, \
        f"legacy 'error' key still present: {result!r}"
    if expect_substring:
        assert expect_substring in result["message"], \
            f"{expect_substring!r} not in message {result['message']!r}"


def test_read_error_shape(tmp_path):
    """read returns {status: error, message: ...} on failure."""
    agent = _file_agent(tmp_path)
    try:
        _assert_error_shape(file_call(agent, "read"),
                            expect_substring="file_path")
        _assert_error_shape(
            file_call(agent, "read", file_path="/nonexistent/x"),
            expect_substring="not found",
        )
    finally:
        agent.stop(timeout=1.0)


def test_write_error_shape(tmp_path):
    """write returns {status: error, message: ...} on failure (was {error: ...})."""
    agent = _file_agent(tmp_path)
    try:
        _assert_error_shape(file_call(agent, "write", content="x"),
                            expect_substring="file_path")
    finally:
        agent.stop(timeout=1.0)


def test_edit_error_shape(tmp_path):
    """edit returns {status: error, message: ...} on each of its failure paths."""
    agent = _file_agent(tmp_path)
    try:
        _assert_error_shape(file_call(agent, "edit"),
                            expect_substring="file_path")
        _assert_error_shape(
            file_call(
                agent, "edit",
                file_path="/nonexistent/x", old_string="a", new_string="b",
            ),
            expect_substring="not found",
        )
        target = tmp_path / "edit_target.txt"
        target.write_text("hello")
        _assert_error_shape(
            file_call(
                agent, "edit",
                file_path=str(target), old_string="missing", new_string="x",
            ),
            expect_substring="not found",
        )
        target.write_text("aaa")
        _assert_error_shape(
            file_call(
                agent, "edit",
                file_path=str(target), old_string="a", new_string="b",
            ),
            expect_substring="replace_all",
        )
    finally:
        agent.stop(timeout=1.0)


def test_glob_error_shape(tmp_path):
    """glob returns {status: error, message: ...} on missing pattern."""
    agent = _file_agent(tmp_path)
    try:
        _assert_error_shape(file_call(agent, "glob"),
                            expect_substring="pattern")
    finally:
        agent.stop(timeout=1.0)


def test_grep_error_shape(tmp_path):
    """grep returns {status: error, message: ...} on missing pattern."""
    agent = _file_agent(tmp_path)
    try:
        _assert_error_shape(file_call(agent, "grep"),
                            expect_substring="pattern")
    finally:
        agent.stop(timeout=1.0)
