"""Focused coverage for built-in tools that expose installed manual skills."""
from __future__ import annotations

from pathlib import Path

from lingtai.tools import daemon as daemon_tool
from lingtai.tools import edit as edit_tool
from lingtai.tools import email as email_tool
from lingtai.tools import glob as glob_tool
from lingtai.tools import grep as grep_tool
from lingtai.tools import psyche as psyche_tool
from lingtai.tools import read as read_tool
from lingtai.tools import soul as soul_tool
from lingtai.tools import system as system_tool
from lingtai.tools import write as write_tool
from lingtai.tools import web_search as web_search_tool
from lingtai.tools import bash as shell_tool


class _StubAgent:
    def __init__(self, working_dir: Path):
        self._working_dir = working_dir
        self.handlers: dict[str, object] = {}

    def add_tool(self, name: str, *, handler=None, **_kwargs) -> None:
        self.handlers[name] = handler


def _install_manual(workdir: Path, skill_name: str) -> tuple[str, Path]:
    path = (
        workdir
        / ".library"
        / "intrinsic"
        / "capabilities"
        / skill_name
        / "SKILL.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"---\nname: {skill_name}\n---\n\n# {skill_name} sentinel\n"
    path.write_text(body, encoding="utf-8")
    return body, path


def test_manual_actions_return_their_installed_skills(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    expected = {
        skill: _install_manual(tmp_path, skill)
        for skill in (
            "shell",
            "daemon",
            "email",
            "psyche-manual",
            "read-manual",
            "soul-manual",
            "system-manual",
            "web_search",
            "file-manual",
        )
    }

    read_tool.setup(agent)
    write_tool.setup(agent)
    edit_tool.setup(agent)
    glob_tool.setup(agent)
    grep_tool.setup(agent)

    shell_manager = shell_tool.ShellManager.__new__(shell_tool.ShellManager)
    shell_manager._agent = agent
    daemon_manager = daemon_tool.DaemonManager.__new__(daemon_tool.DaemonManager)
    daemon_manager._agent = agent
    web_search_manager = web_search_tool.WebSearchManager.__new__(web_search_tool.WebSearchManager)
    web_search_manager._agent = agent

    calls = {
        "shell": ("shell", lambda: shell_manager.handle({"action": "manual"})),
        "daemon": ("daemon", lambda: daemon_manager.handle({"action": "manual"})),
        "email": ("email", lambda: email_tool.handle(agent, {"action": "manual"})),
        "psyche": ("psyche-manual", lambda: psyche_tool.handle(agent, {"action": "manual"})),
        "read": ("read-manual", lambda: agent.handlers["read"]({"action": "manual"})),
        "soul": ("soul-manual", lambda: soul_tool.handle(agent, {"action": "manual"})),
        "system": ("system-manual", lambda: system_tool.handle(agent, {"action": "manual"})),
        "web_search": ("web_search", lambda: web_search_manager.handle({"action": "manual"})),
        "write": ("file-manual", lambda: agent.handlers["write"]({"action": "manual"})),
        "edit": ("file-manual", lambda: agent.handlers["edit"]({"action": "manual"})),
        "glob": ("file-manual", lambda: agent.handlers["glob"]({"action": "manual"})),
        "grep": ("file-manual", lambda: agent.handlers["grep"]({"action": "manual"})),
    }

    for tool_name, (skill_name, call) in calls.items():
        body, path = expected[skill_name]
        assert call() == {
            "status": "ok",
            "manual": body,
            "manual_path": str(path),
        }, tool_name


def test_manual_schemas_preserve_runtime_checks_for_ordinary_file_calls(
    tmp_path: Path,
) -> None:
    modules = (
        shell_tool,
        daemon_tool,
        email_tool,
        psyche_tool,
        read_tool,
        soul_tool,
        system_tool,
        web_search_tool,
        write_tool,
        edit_tool,
        glob_tool,
        grep_tool,
    )
    for module in modules:
        schema = module.get_schema()
        action = schema["properties"]["action"]
        assert "manual" in action.get("enum", ()) or "manual" in action["description"]

    assert shell_tool.get_schema()["required"] == []
    assert psyche_tool.get_schema()["required"] == ["action"]
    assert web_search_tool.get_schema()["required"] == []
    for module in (read_tool, write_tool, edit_tool, glob_tool, grep_tool):
        assert module.get_schema()["required"] == []

    agent = _StubAgent(tmp_path)
    for module in (read_tool, write_tool, edit_tool, glob_tool, grep_tool):
        module.setup(agent)

    assert agent.handlers["read"]({})["message"] == "file_path is required"
    assert agent.handlers["write"]({"file_path": str(tmp_path / "x")})["message"] == "content is required"
    assert agent.handlers["edit"]({"file_path": str(tmp_path / "x"), "old_string": "a"})["message"] == "new_string is required"
    assert agent.handlers["glob"]({})["message"] == "pattern is required"
    assert agent.handlers["grep"]({})["message"] == "pattern is required"
    assert not (tmp_path / "x").exists()


def test_missing_installed_manual_degrades_without_side_effects(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    expected_path = (
        tmp_path
        / ".library"
        / "intrinsic"
        / "capabilities"
        / "system-manual"
        / "SKILL.md"
    )

    assert system_tool.handle(agent, {"action": "manual"}) == {
        "status": "degraded",
        "manual": "",
        "manual_path": str(expected_path),
        "error": (
            "system-manual manual missing — initializer may have failed or "
            "capability not installed correctly"
        ),
    }
    assert not (tmp_path / ".library").exists()


class _ActionFileIO:
    def __init__(self, root: Path):
        self.root = root
        self.last_traversal = None

    def read(self, path):
        return Path(path).read_text(encoding="utf-8")

    def write(self, path, content):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def glob(self, pattern, *, root):
        return sorted(str(path) for path in Path(root).glob(pattern))

    def grep(self, pattern, *, path, max_results, glob_filter):
        import re
        result = []
        target = Path(path)
        paths = [target] if target.is_file() else sorted(target.rglob(glob_filter or "*"))
        for current in paths:
            if not current.is_file():
                continue
            for number, line in enumerate(current.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(pattern, line):
                    result.append(type("Match", (), {"path": str(current), "line_number": number, "line": line})())
                    if len(result) >= max_results:
                        return result
        return result


def test_file_action_modes_keep_omission_and_fail_loudly(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    agent._file_io = _ActionFileIO(tmp_path)
    modules = (read_tool, write_tool, edit_tool, glob_tool, grep_tool)
    for module in modules:
        schema = module.get_schema()
        tool_name = module.__name__.rsplit(".", 1)[-1]
        assert schema["properties"]["action"]["enum"] == [tool_name, "manual"]
        description = module.get_description()
        assert "omit action" in description
        assert f"action='{tool_name}'" in description
        assert "after the manual result" in description
        assert "error loop" in description
        module.setup(agent)

    source = tmp_path / "source.txt"
    source.write_text("alpha\n", encoding="utf-8")
    assert agent.handlers["read"]({"file_path": str(source)})["total_lines"] == 1
    assert agent.handlers["read"]({"action": "read", "file_path": str(source)})["total_lines"] == 1
    assert agent.handlers["write"]({"action": "write", "file_path": str(tmp_path / "written.txt"), "content": "beta"})["status"] == "ok"
    assert agent.handlers["edit"]({"action": "edit", "file_path": str(source), "old_string": "alpha", "new_string": "gamma"})["status"] == "ok"
    assert agent.handlers["glob"]({"action": "glob", "pattern": "*.txt", "path": str(tmp_path)})["count"] >= 2
    assert agent.handlers["grep"]({"action": "grep", "pattern": "gamma", "path": str(source)})["count"] == 1

    for name in ("read", "write", "edit", "glob", "grep"):
        result = agent.handlers[name]({"action": "unsupported"})
        assert result["status"] == "error"
        assert "Unsupported action" in result["message"]


def test_file_manual_bodies_explain_one_time_dual_mode_guidance() -> None:
    file_body = Path("src/lingtai/intrinsic_skills/file-manual/SKILL.md").read_text(encoding="utf-8")
    read_body = Path("src/lingtai/intrinsic_skills/read-manual/SKILL.md").read_text(encoding="utf-8")
    for body in (file_body, read_body):
        assert "backward compatibility" in body
        assert "ordinary" in body
        assert "one-time" in body
        assert "After" in body
        assert "error loop" in body
