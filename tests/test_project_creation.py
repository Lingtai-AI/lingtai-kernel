"""Focused evidence for fresh local Project creation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lingtai.adapters.project_workspace import FilesystemProjectWorkspaceAdapter
from lingtai.kernel.project import (
    ProjectCreateRequest,
    ProjectCreationError,
    ProjectCreationUseCase,
    ProjectSeed,
    ProjectWorkspacePort,
)


class _Workspace(ProjectWorkspacePort):
    def __init__(self) -> None:
        self.seed: ProjectSeed | None = None

    def create(self, seed: ProjectSeed) -> None:
        self.seed = seed


def _request(name: str = "alpha") -> ProjectCreateRequest:
    return ProjectCreateRequest(
        agent_name=name,
        preset_ref="/presets/local.json",
        llm={"provider": "openai", "model": "test-model"},
        capabilities={"shell": {"yolo": False}},
        covenant="caller covenant",
    )


def test_core_builds_one_seed_through_its_port() -> None:
    workspace = _Workspace()
    result = ProjectCreationUseCase(workspace).create(_request())

    assert workspace.seed is not None
    init = json.loads(workspace.seed.init_json)
    psyche = json.loads(workspace.seed.psyche_settings_json)
    assert "covenant" not in init
    assert psyche == {"covenant": "caller covenant", "schema_version": 1}
    assert init["manifest"]["preset"]["allowed"] == ["/presets/local.json"]
    assert "context_limit" not in init["manifest"]
    assert result.to_payload()["status"] == "created"


@pytest.mark.parametrize("name", ["", "human", "a/b", "a\\b"])
def test_core_rejects_non_project_agent_names(name: str) -> None:
    with pytest.raises(ProjectCreationError, match="agent name"):
        ProjectCreationUseCase(_Workspace()).create(_request(name))


def test_adapter_creates_complete_seed_and_refuses_existing_project(tmp_path: Path) -> None:
    checked: list[Path] = []
    workspace = FilesystemProjectWorkspaceAdapter(tmp_path, validate_agent=checked.append)

    ProjectCreationUseCase(workspace).create(_request())

    assert [path.name for path in checked] == ["alpha"]
    assert (tmp_path / ".lingtai" / "human" / "mailbox" / "inbox").is_dir()
    assert (tmp_path / ".lingtai" / "alpha" / "init.json").is_file()
    assert json.loads((tmp_path / ".lingtai" / "alpha" / "settings" / "psyche.json").read_text(encoding="utf-8")) == {
        "covenant": "caller covenant", "schema_version": 1,
    }
    with pytest.raises(ProjectCreationError) as exc:
        ProjectCreationUseCase(workspace).create(_request())
    assert exc.value.error.code == "already_initialized"


def test_root_cli_creates_reader_accepted_data_without_starting_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from lingtai import cli
    from lingtai.agent import Agent

    preset = tmp_path / "preset.json"
    preset.write_text(json.dumps({
        "description": {"summary": "test preset"},
        "manifest": {
            "llm": {"provider": "openai", "model": "test-model", "context_limit": 8192},
            "capabilities": {"shell": {"yolo": False}},
        },
    }), encoding="utf-8")
    covenant = tmp_path / "covenant.md"
    covenant.write_text("caller covenant", encoding="utf-8")
    monkeypatch.setattr(Agent, "start", lambda *_args, **_kwargs: pytest.fail("Project create must not start Agent"))
    monkeypatch.setattr(sys, "argv", [
        "lingtai-agent", "project", "create", "--dir", str(tmp_path), "--name", "alpha",
        "--preset", str(preset), "--covenant-file", str(covenant), "--json",
    ])

    cli.main()

    assert json.loads(capsys.readouterr().out)["agent_name"] == "alpha"
    init = json.loads((tmp_path / ".lingtai" / "alpha" / "init.json").read_text(encoding="utf-8"))
    psyche = json.loads((tmp_path / ".lingtai" / "alpha" / "settings" / "psyche.json").read_text(encoding="utf-8"))
    assert "context_limit" not in init["manifest"]
    assert "context_limit" not in init["manifest"]["llm"]
    assert psyche == {"covenant": "caller covenant", "schema_version": 1}
    assert json.loads(preset.read_text(encoding="utf-8"))["manifest"]["llm"]["context_limit"] == 8192
