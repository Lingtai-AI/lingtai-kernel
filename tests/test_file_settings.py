"""Source-backed proofs for File's exact SHOW-only settings inventory."""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.adapters.tool_plugin_host import (
    AgentFileIOAdapter,
    StaticConfigurationAdapter,
)
from lingtai.agent import Agent
from lingtai.kernel.tool_plugin import ToolPluginHost
from lingtai.services.file_io import (
    DEFAULT_EXCLUDED_DIRS,
    DEFAULT_GLOB_MAX_RESULTS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_VISITED,
    DEFAULT_WALLTIME_S,
    LocalFileIOService,
)
from lingtai.services.file_io_sidecar import (
    BACKEND_ENV_VAR,
    DEFAULT_SIDECAR_TIMEOUT_SECONDS,
    SIDECAR_ENV_VARS,
    SidecarAdapter,
    default_file_io_service,
    file_io_construction_snapshot,
)
from lingtai.tools.file import DECLARATION, get_schema
from lingtai.tools.file._grep import DEFAULT_GREP_MAX_MATCHES
from lingtai.tools.file._read import (
    DEFAULT_READ_CAP_CHARS,
    DEFAULT_READ_LINE_LIMIT,
    READ_HARD_CAP_CHARS,
)
from lingtai.tools.file.settings import (
    BACKEND_MODE,
    BACKEND_SIDECAR,
    FILE_IO_CONSTRUCTION_SNAPSHOT_KEY,
    GLOB_MAX_RESULTS,
    GREP_DEFAULT_MAX_MATCHES,
    GREP_MAX_FILE_BYTES,
    READ_DEFAULT_LINE_LIMIT,
    READ_DEFAULT_MAX_CHARS,
    READ_RUNTIME_MAX_CHARS,
    SEARCH_EXCLUDED_DIRECTORIES,
    SEARCH_MAX_VISITED,
    SEARCH_SIDECAR_TIMEOUT_SECONDS,
    SEARCH_WALLTIME_SECONDS,
    TEXT_ENCODING,
)
from lingtai.tools.tool_family.settings import MAX_SETTINGS_RESPONSE_BYTES
from tests._service_helpers import make_gemini_mock_service


EXPECTED_KEYS = (
    READ_DEFAULT_LINE_LIMIT,
    READ_DEFAULT_MAX_CHARS,
    READ_RUNTIME_MAX_CHARS,
    GLOB_MAX_RESULTS,
    GREP_DEFAULT_MAX_MATCHES,
    GREP_MAX_FILE_BYTES,
    SEARCH_MAX_VISITED,
    SEARCH_WALLTIME_SECONDS,
    SEARCH_EXCLUDED_DIRECTORIES,
    SEARCH_SIDECAR_TIMEOUT_SECONDS,
    TEXT_ENCODING,
    BACKEND_MODE,
    BACKEND_SIDECAR,
)


def _bound_handler(
    tmp_path: Path,
    *,
    backend: str | None = None,
    runtime_cap: int | None | Callable[[], int | None] = None,
):
    service = default_file_io_service(root=tmp_path, backend=backend)
    file_io = AgentFileIOAdapter(
        read=service.read,
        write=service.write,
        glob=service.glob,
        grep=service.grep,
        last_traversal=lambda: service.last_traversal,
        max_result_chars=(
            runtime_cap if callable(runtime_cap) else lambda: runtime_cap
        ),
    )
    configuration = StaticConfigurationAdapter(
        {
            FILE_IO_CONSTRUCTION_SNAPSHOT_KEY: file_io_construction_snapshot(
                service
            )
        }
    )
    host = ToolPluginHost(
        "file",
        {
            "workdir": SimpleNamespace(path=tmp_path),
            "file_io": file_io,
            "configuration": configuration,
        },
    )
    return DECLARATION.bind(host).handler, service


def _show(handler, action_input):
    return handler(
        {
            "action": "settings",
            "input": action_input,
            "reasoning": "verify File owner settings",
        }
    )


def _rows(result):
    return {row["key"]: row for row in result["settings"]}


def _clear_file_environment(monkeypatch) -> None:
    for name in (BACKEND_ENV_VAR, *SIDECAR_ENV_VARS):
        monkeypatch.delenv(name, raising=False)


def _executable(path: Path) -> Path:
    if os.name == "nt":
        path = path.with_suffix(".cmd")
        body = "@echo off\r\nexit /b 0\r\n"
    else:
        body = "#!/bin/sh\nexit 0\n"
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_settings_is_immediately_before_manual_and_has_no_writer():
    assert DECLARATION.settings is True
    assert DECLARATION.public_actions == (
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "settings",
        "manual",
    )
    assert not ({"set", "reset"} & set(DECLARATION.public_actions))
    schema = get_schema()
    assert schema["properties"]["action"]["enum"] == list(
        DECLARATION.public_actions
    )
    branches = schema["properties"]["input"]["anyOf"]
    settings = next(
        branch
        for branch in branches
        if branch["title"] == "settings inventory input"
    )
    assert settings == {
        "title": "settings inventory input",
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_exact_order_five_fields_values_defaults_and_configurability(
    tmp_path, monkeypatch
):
    _clear_file_environment(monkeypatch)
    handler, _service = _bound_handler(tmp_path, runtime_cap=12_345)
    result = _show(handler, {})
    rows = _rows(result)

    assert tuple(rows) == EXPECTED_KEYS
    assert all(
        tuple(row) == (
            "key",
            "current",
            "default",
            "configurable",
            "comment",
        )
        for row in result["settings"]
    )
    expected = {
        READ_DEFAULT_LINE_LIMIT: (
            DEFAULT_READ_LINE_LIMIT,
            DEFAULT_READ_LINE_LIMIT,
            False,
        ),
        READ_DEFAULT_MAX_CHARS: (
            DEFAULT_READ_CAP_CHARS,
            DEFAULT_READ_CAP_CHARS,
            False,
        ),
        READ_RUNTIME_MAX_CHARS: (12_345, READ_HARD_CAP_CHARS, False),
        GLOB_MAX_RESULTS: (
            DEFAULT_GLOB_MAX_RESULTS,
            DEFAULT_GLOB_MAX_RESULTS,
            False,
        ),
        GREP_DEFAULT_MAX_MATCHES: (
            DEFAULT_GREP_MAX_MATCHES,
            DEFAULT_GREP_MAX_MATCHES,
            False,
        ),
        GREP_MAX_FILE_BYTES: (
            DEFAULT_MAX_FILE_BYTES,
            DEFAULT_MAX_FILE_BYTES,
            False,
        ),
        SEARCH_MAX_VISITED: (
            DEFAULT_MAX_VISITED,
            DEFAULT_MAX_VISITED,
            False,
        ),
        SEARCH_WALLTIME_SECONDS: (
            DEFAULT_WALLTIME_S,
            DEFAULT_WALLTIME_S,
            False,
        ),
        SEARCH_EXCLUDED_DIRECTORIES: (
            sorted(DEFAULT_EXCLUDED_DIRS),
            sorted(DEFAULT_EXCLUDED_DIRS),
            False,
        ),
        SEARCH_SIDECAR_TIMEOUT_SECONDS: (
            DEFAULT_SIDECAR_TIMEOUT_SECONDS,
            DEFAULT_SIDECAR_TIMEOUT_SECONDS,
            False,
        ),
        TEXT_ENCODING: ("utf-8", "utf-8", False),
        BACKEND_MODE: ("auto", "auto", True),
        BACKEND_SIDECAR: ("<redacted>", "<redacted>", True),
    }
    for key, values in expected.items():
        assert (
            rows[key]["current"],
            rows[key]["default"],
            rows[key]["configurable"],
        ) == values

    assert (
        signature(LocalFileIOService.glob).parameters["max_results"].default
        == DEFAULT_GLOB_MAX_RESULTS
    )
    assert (
        signature(SidecarAdapter.__init__).parameters["timeout_s"].default
        == DEFAULT_SIDECAR_TIMEOUT_SECONDS
    )
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= (
        MAX_SETTINGS_RESPONSE_BYTES
    )


def test_comments_target_single_stable_file_manual_heading(tmp_path, monkeypatch):
    _clear_file_environment(monkeypatch)
    handler, _service = _bound_handler(tmp_path, backend="python")
    result = _show(handler, {})
    manual = Path("src/lingtai/tools/file/manual/SKILL.md").read_text(
        encoding="utf-8"
    )
    for row in result["settings"]:
        assert row["comment"] == "file-manual#settings-show-only"
    assert "## Settings SHOW only\n" in manual


def test_runtime_cap_current_is_fresh_on_each_show(tmp_path, monkeypatch):
    _clear_file_environment(monkeypatch)
    cap = [12_345]
    handler, _service = _bound_handler(
        tmp_path,
        backend="python",
        runtime_cap=lambda: cap[0],
    )
    assert _rows(_show(handler, {}))[READ_RUNTIME_MAX_CHARS]["current"] == 12_345
    cap[0] = 6_789
    assert _rows(_show(handler, {}))[READ_RUNTIME_MAX_CHARS]["current"] == 6_789


def test_construction_snapshot_is_explicit_and_not_an_ambient_env_reread(
    tmp_path, monkeypatch
):
    _clear_file_environment(monkeypatch)
    monkeypatch.setenv(BACKEND_ENV_VAR, "rust")
    handler, _service = _bound_handler(tmp_path, backend="python")
    monkeypatch.setenv(BACKEND_ENV_VAR, "auto")

    rows = _rows(_show(handler, {}))
    assert rows[BACKEND_MODE]["current"] == "python"
    assert rows[BACKEND_MODE]["default"] == "auto"


def test_sidecar_alias_precedence_is_snapshotted_and_fully_redacted(
    tmp_path, monkeypatch
):
    _clear_file_environment(monkeypatch)
    primary = _executable(tmp_path / "primary-sidecar")
    legacy = _executable(tmp_path / "legacy-sidecar")
    monkeypatch.setenv(SIDECAR_ENV_VARS[0], str(primary))
    monkeypatch.setenv(SIDECAR_ENV_VARS[1], str(legacy))
    handler, service = _bound_handler(tmp_path)
    snapshot = file_io_construction_snapshot(service)
    assert snapshot is not None
    assert snapshot.sidecar_override_source == SIDECAR_ENV_VARS[0]
    assert snapshot.sidecar_override == str(primary)
    assert str(primary) not in repr(snapshot)

    monkeypatch.delenv(SIDECAR_ENV_VARS[0])
    monkeypatch.setenv(SIDECAR_ENV_VARS[1], str(tmp_path / "later-secret"))
    assert file_io_construction_snapshot(service) == snapshot
    result = _show(handler, {})
    row = _rows(result)[BACKEND_SIDECAR]
    assert row["current"] == row["default"] == "<redacted>"
    serialized = json.dumps(result)
    assert str(primary) not in serialized
    assert str(legacy) not in serialized
    assert str(tmp_path) not in serialized


def test_legacy_sidecar_alias_is_the_same_semantic_row(tmp_path, monkeypatch):
    _clear_file_environment(monkeypatch)
    legacy = _executable(tmp_path / "legacy-sidecar")
    monkeypatch.setenv(SIDECAR_ENV_VARS[1], str(legacy))
    handler, service = _bound_handler(tmp_path)
    snapshot = file_io_construction_snapshot(service)
    assert snapshot is not None
    assert snapshot.sidecar_override_source == SIDECAR_ENV_VARS[1]
    assert snapshot.sidecar_override == str(legacy)
    result = _show(handler, {})
    assert tuple(_rows(result)) == EXPECTED_KEYS
    assert "backend.legacy_search_sidecar" not in repr(result)


def test_unavailable_snapshot_is_one_whole_inventory_failure(tmp_path):
    service = LocalFileIOService(root=tmp_path)
    file_io = AgentFileIOAdapter(
        read=service.read,
        write=service.write,
        glob=service.glob,
        grep=service.grep,
        last_traversal=lambda: service.last_traversal,
        max_result_chars=lambda: None,
    )
    host = ToolPluginHost(
        "file",
        {
            "workdir": SimpleNamespace(path=tmp_path),
            "file_io": file_io,
            "configuration": StaticConfigurationAdapter(),
        },
    )
    assert _show(DECLARATION.bind(host).handler, {}) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_input_is_strict_and_show_does_not_mutate_tree_or_environment(
    tmp_path, monkeypatch
):
    _clear_file_environment(monkeypatch)
    target = tmp_path / "ordinary.txt"
    target.write_text("alpha\n", encoding="utf-8")
    handler, _service = _bound_handler(tmp_path, backend="python")
    before_tree = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    before_environment = dict(os.environ)

    assert "settings" in _show(handler, {})
    for invalid in (None, [], {"set": BACKEND_MODE}, {"reset": BACKEND_MODE}):
        result = _show(handler, invalid)
        assert result["status"] == "failed"
        assert "settings" not in result

    after_tree = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after_tree == before_tree
    assert dict(os.environ) == before_environment


def test_basic_read_and_complete_agent_prompt_build_are_unchanged(
    tmp_path, monkeypatch
):
    _clear_file_environment(monkeypatch)
    monkeypatch.setenv(BACKEND_ENV_VAR, "python")
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="file-settings-real-agent",
        working_dir=tmp_path / "agent",
        capabilities={"file": {}},
    )
    try:
        prompt = agent._build_system_prompt()
        assert isinstance(prompt, str) and prompt
        handler = agent._tool_handlers["file"]
        settings = _show(handler, {})
        assert tuple(_rows(settings)) == EXPECTED_KEYS
        manual = handler({"action": "manual", "input": {}, "reasoning": "installed File guide"})
        installed = Path(manual["structuredContent"]["manual_path"])
        assert manual["content"][0]["text"] == Path("src/lingtai/tools/file/manual/SKILL.md").read_text()
        depth = installed.parent.parent / "read-manual" / "SKILL.md"
        assert depth.read_bytes() == Path("src/lingtai/intrinsic_skills/read-manual/SKILL.md").read_bytes()
        assert "<workdir>/.library/intrinsic/capabilities/read-manual/SKILL.md" in installed.read_text()
        assert "../../../intrinsic_skills/" not in installed.read_text()

        target = agent.working_dir / "ordinary.txt"
        target.write_text("alpha\n", encoding="utf-8")
        read = handler(
            {
                "action": "read",
                "input": {
                    "file_path": str(target),
                    "offset": None,
                    "limit": None,
                    "max_chars": None,
                },
                "reasoning": "ordinary File non-regression",
            }
        )
        assert read == {
            "content": "1\talpha\n",
            "total_lines": 1,
            "lines_shown": 1,
        }
    finally:
        agent.stop(timeout=1.0)


@pytest.mark.parametrize("cap", [100_000, 100])
def test_manual_complete_read_example_covers_line_and_character_windows(tmp_path, cap):
    handler, _ = _bound_handler(tmp_path, backend="python", runtime_cap=cap)
    path = tmp_path / "pages.txt"
    path.write_text("row\n" * 450, encoding="utf-8")
    manual = Path("src/lingtai/intrinsic_skills/read-manual/SKILL.md").read_text()
    example = re.search(r"```python\n(.*?)```", manual, re.S).group(1)
    pages = []
    exec(example, {"file": lambda **kw: handler(kw), "path": str(path), "process": pages.append})
    numbers = [int(line.split("\t", 1)[0]) for page in pages for line in page.splitlines()]
    assert numbers == list(range(1, 451))


def test_manual_complete_read_example_refuses_a_partial_long_line(tmp_path):
    handler, _ = _bound_handler(tmp_path, backend="python", runtime_cap=100)
    path = tmp_path / "long-line.txt"
    path.write_text("x" * 500 + "\n", encoding="utf-8")
    manual = Path("src/lingtai/intrinsic_skills/read-manual/SKILL.md").read_text()
    example = re.search(r"```python\n(.*?)```", manual, re.S).group(1)
    pages = []
    with pytest.raises(RuntimeError, match="targeted"):
        exec(example, {"file": lambda **kw: handler(kw), "path": str(path), "process": pages.append})
    assert pages == []
