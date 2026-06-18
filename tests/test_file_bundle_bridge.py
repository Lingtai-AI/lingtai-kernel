"""Stage-3A wrapper bridge: host the *real* file tools through the SDK bundle.

Where ``tests/test_sdk_file_tools.py`` proves the SDK-side declarations + host
seam with dummy handlers (and import purity), this test proves the *wrapper*
half — ``lingtai.core.file_bundle`` — that injects the genuine ``read`` /
``glob`` / ``grep`` handlers into the SDK file-tool bundle and so runs the real
behavior through the declared manifest.

The key assertion is **parity**: invoking a tool through the bundle host returns
exactly what the agent's registered tool returns, because both wire the same
``make_handler(agent)`` closure against the same ``agent._file_io`` /
``agent._working_dir``. The bundle path changes neither schema nor behavior.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lingtai.agent import Agent
from lingtai.core import edit as edit_cap
from lingtai.core import file_bundle
from lingtai.core import glob as glob_cap
from lingtai.core import grep as grep_cap
from lingtai.core import read as read_cap
from lingtai.core import write as write_cap


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


@pytest.fixture
def agent(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir()
    a = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=wd,
        capabilities=["read", "glob", "grep"],
    )
    try:
        yield a
    finally:
        a.stop(timeout=1.0)


def test_bridge_builds_all_three_hosts(agent):
    hosts = file_bundle.file_tool_bundle_hosts(agent)
    assert set(hosts) == {"read", "glob", "grep"}
    for name, h in hosts.items():
        # each host declares exactly its one tool and is the non-native host.
        assert h.tools == (name,)
        assert h.manifest.roles.privileged is False
        assert h.manifest.transport.kind == "in_process"


def test_bridge_read_runs_real_behavior(agent):
    target = agent._working_dir / "hello.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")

    hosts = file_bundle.file_tool_bundle_hosts(agent)
    via_bundle = hosts["read"].invoke("read", file_path=str(target))

    # parity: identical to the registered handler run directly.
    via_handler = read_cap.make_handler(agent)({"file_path": str(target)})
    assert via_bundle == via_handler

    # and it really read the file (numbered content, total lines).
    assert via_bundle["total_lines"] == 2
    assert "1\tline one\n" in via_bundle["content"]
    assert "2\tline two\n" in via_bundle["content"]


def test_bridge_read_relative_path_uses_working_dir(agent):
    (agent._working_dir / "rel.txt").write_text("x\n", encoding="utf-8")
    hosts = file_bundle.file_tool_bundle_hosts(agent)
    out = hosts["read"].invoke("read", file_path="rel.txt")
    assert out["total_lines"] == 1


def test_bridge_read_missing_file_error_structure(agent):
    hosts = file_bundle.file_tool_bundle_hosts(agent)
    out = hosts["read"].invoke("read", file_path=str(agent._working_dir / "nope.txt"))
    # the wrapper's error structure is preserved unchanged through the bundle.
    assert out["status"] == "error"
    assert "not found" in out["message"].lower()


def test_bridge_glob_runs_real_behavior(agent):
    (agent._working_dir / "a.py").write_text("a\n", encoding="utf-8")
    (agent._working_dir / "b.py").write_text("b\n", encoding="utf-8")
    (agent._working_dir / "c.txt").write_text("c\n", encoding="utf-8")

    hosts = file_bundle.file_tool_bundle_hosts(agent)
    via_bundle = hosts["glob"].invoke("glob", pattern="*.py")
    via_handler = glob_cap.make_handler(agent)({"pattern": "*.py"})
    assert via_bundle == via_handler
    # both .py files are found (the working dir may also hold agent scaffolding,
    # so assert presence, not an exact count).
    names = {str(m).rsplit("/", 1)[-1] for m in via_bundle["matches"]}
    assert {"a.py", "b.py"} <= names


def test_bridge_grep_runs_real_behavior(agent):
    (agent._working_dir / "x.txt").write_text("needle here\nhaystack\n", encoding="utf-8")

    hosts = file_bundle.file_tool_bundle_hosts(agent)
    via_bundle = hosts["grep"].invoke("grep", pattern="needle")
    via_handler = grep_cap.make_handler(agent)({"pattern": "needle"})
    assert via_bundle == via_handler
    assert via_bundle["count"] >= 1
    assert any("needle" in m["text"] for m in via_bundle["matches"])


def test_bridge_grep_missing_pattern_error(agent):
    hosts = file_bundle.file_tool_bundle_hosts(agent)
    out = hosts["grep"].invoke("grep", pattern="")
    assert out["status"] == "error"
    assert "pattern is required" in out["message"]


def test_registered_tool_handler_is_the_same_factory(agent):
    """The agent's live ``read`` tool and the bridge wire the same behavior.

    Both go through ``read_cap.make_handler`` — one source of truth — so the
    bundle host cannot drift from the registered tool.
    """
    # the agent registered a `read` handler (live path unchanged).
    assert "read" in agent._tool_handlers
    target = agent._working_dir / "same.txt"
    target.write_text("z\n", encoding="utf-8")

    registered = agent._tool_handlers["read"]({"file_path": str(target)})
    hosts = file_bundle.file_tool_bundle_hosts(agent)
    bundled = hosts["read"].invoke("read", file_path=str(target))
    assert registered == bundled


# --- stage 3B: the side-effecting write/edit bridge ------------------------


@pytest.fixture
def mut_agent(tmp_path):
    """An agent with the side-effecting file tools registered."""
    wd = tmp_path / "wd"
    wd.mkdir()
    a = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=wd,
        capabilities=["read", "write", "edit"],
    )
    try:
        yield a
    finally:
        a.stop(timeout=1.0)


def test_mutation_bridge_builds_both_hosts(mut_agent):
    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    assert set(hosts) == {"write", "edit"}
    # each host declares exactly its one tool, is non-native in_process, and
    # carries its declared danger posture (write destructive, edit caution).
    assert hosts["write"].tools == ("write",)
    assert hosts["edit"].tools == ("edit",)
    assert hosts["write"].manifest.security.danger == "destructive"
    assert hosts["edit"].manifest.security.danger == "caution"
    for h in hosts.values():
        assert h.manifest.roles.privileged is False
        assert h.manifest.transport.kind == "in_process"


def test_mutation_bridge_write_creates_file_and_parity(mut_agent):
    target = mut_agent._working_dir / "new.txt"
    assert not target.exists()

    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    via_bundle = hosts["write"].invoke(
        "write", file_path=str(target), content="hello\n"
    )

    # the side effect really happened: the file now exists with the content.
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert via_bundle["status"] == "ok"
    assert via_bundle["path"] == str(target)
    assert via_bundle["bytes"] == len("hello\n".encode("utf-8"))


def test_mutation_bridge_write_overwrites_wholesale(mut_agent):
    target = mut_agent._working_dir / "clobber.txt"
    target.write_text("OLD CONTENT THAT SHOULD VANISH\n", encoding="utf-8")

    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    hosts["write"].invoke("write", file_path=str(target), content="new\n")

    # destructive posture is real: the prior content is gone, not appended to.
    assert target.read_text(encoding="utf-8") == "new\n"


def test_mutation_bridge_write_parity_with_registered_handler(mut_agent):
    # the live registered tool and the bridge wire the same make_handler.
    assert "write" in mut_agent._tool_handlers
    target = mut_agent._working_dir / "parity.txt"

    registered = mut_agent._tool_handlers["write"](
        {"file_path": str(target), "content": "via-registered\n"}
    )
    # writing again via the bundle host should report the same shape.
    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    bundled = hosts["write"].invoke(
        "write", file_path=str(target), content="via-registered\n"
    )
    assert registered == bundled

    # and the bridge result matches make_handler called directly (full parity).
    via_handler = write_cap.make_handler(mut_agent)(
        {"file_path": str(target), "content": "via-registered\n"}
    )
    assert bundled == via_handler


def test_mutation_bridge_write_missing_path_error(mut_agent):
    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    out = hosts["write"].invoke("write", file_path="", content="x")
    assert out["status"] == "error"
    assert "file_path is required" in out["message"]


def test_mutation_bridge_edit_runs_real_behavior_and_parity(mut_agent):
    target = mut_agent._working_dir / "doc.txt"
    target.write_text("alpha beta gamma\n", encoding="utf-8")

    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    via_bundle = hosts["edit"].invoke(
        "edit", file_path=str(target), old_string="beta", new_string="BETA"
    )

    # the in-place replacement really happened.
    assert target.read_text(encoding="utf-8") == "alpha BETA gamma\n"
    assert via_bundle == {"status": "ok", "replacements": 1}

    # parity: re-running the exact edit via make_handler directly on a fresh file.
    fresh = mut_agent._working_dir / "doc2.txt"
    fresh.write_text("alpha beta gamma\n", encoding="utf-8")
    via_handler = edit_cap.make_handler(mut_agent)(
        {"file_path": str(fresh), "old_string": "beta", "new_string": "BETA"}
    )
    assert via_bundle == via_handler


def test_mutation_bridge_edit_ambiguity_refusal_preserved(mut_agent):
    target = mut_agent._working_dir / "ambig.txt"
    target.write_text("x x x\n", encoding="utf-8")

    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    out = hosts["edit"].invoke(
        "edit", file_path=str(target), old_string="x", new_string="y"
    )
    # ambiguous edit (3 matches, no replace_all) is refused, file untouched.
    assert out["status"] == "error"
    assert "found 3 times" in out["message"]
    assert target.read_text(encoding="utf-8") == "x x x\n"


def test_mutation_bridge_edit_missing_old_string_error(mut_agent):
    target = mut_agent._working_dir / "nope.txt"
    target.write_text("hello\n", encoding="utf-8")
    hosts = file_bundle.file_mutation_tool_bundle_hosts(mut_agent)
    out = hosts["edit"].invoke(
        "edit", file_path=str(target), old_string="absent", new_string="z"
    )
    assert out["status"] == "error"
    assert "not found" in out["message"].lower()
