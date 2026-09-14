"""Focused evidence for read-only local Project inspection."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.adapters import project_workspace
from lingtai.adapters.project_workspace import (
    FilesystemProjectInspectionAdapter,
    FilesystemProjectWorkspaceAdapter,
)
from lingtai.kernel.project import (
    ProjectCreationError,
    ProjectInspectionError,
    ProjectInspectionObservation,
    ProjectInspectionPort,
    ProjectInspectionUseCase,
    ProjectSeed,
    ProjectState,
)


class _Inspection(ProjectInspectionPort):
    def __init__(self, observation: ProjectInspectionObservation) -> None:
        self.observation = observation
        self.calls = 0

    def inspect(self) -> ProjectInspectionObservation:
        self.calls += 1
        return self.observation


def _observe(
    present: bool,
    total: int = 0,
    with_init: int = 0,
    without_init: int = 0,
) -> ProjectInspectionObservation:
    return ProjectInspectionObservation(present, total, with_init, without_init)


def _inspect(root: Path):
    return ProjectInspectionUseCase(FilesystemProjectInspectionAdapter(root)).inspect()


def _agent(
    lingtai_root: Path,
    name: str,
    *,
    manifest: bool = True,
    init: bool = False,
) -> Path:
    agent = lingtai_root / name
    agent.mkdir(parents=True)
    if manifest:
        (agent / ".agent.json").write_bytes(b"not parsed\xff")
    if init:
        (agent / "init.json").write_bytes(b"not parsed\xfe")
    return agent


def _seed(name: str = "seed") -> ProjectSeed:
    return ProjectSeed(
        agent_name=name,
        preset_ref="preset.json",
        human_manifest_json="{}\n",
        agent_manifest_json="{}\n",
        init_json="{}\n",
        psyche_settings_json="{}\n",
    )


def _tree_bytes(root: Path) -> tuple[tuple[str, str, bytes | str | None], ...]:
    rows: list[tuple[str, str, bytes | str | None]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        mode = path.lstat().st_mode
        relative = path.relative_to(root).as_posix()
        if stat.S_ISREG(mode):
            rows.append((relative, "file", path.read_bytes()))
        elif stat.S_ISLNK(mode):
            rows.append((relative, "symlink", os.readlink(path)))
        else:
            rows.append((relative, "directory", None))
    return tuple(rows)


@pytest.mark.parametrize(
    ("observation", "state"),
    [
        (_observe(False), ProjectState.ABSENT),
        (_observe(True), ProjectState.EMPTY),
        (_observe(True, 2, 1, 1), ProjectState.POPULATED),
    ],
)
def test_core_classifies_only_mechanical_project_observations(
    observation: ProjectInspectionObservation,
    state: ProjectState,
) -> None:
    port = _Inspection(observation)

    result = ProjectInspectionUseCase(port).inspect()

    assert port.calls == 1
    assert result.state is state
    assert result.to_payload() == {
        "status": "inspected",
        "state": state.value,
        "agent_candidates": {
            "total": observation.agent_candidates,
            "with_init": observation.agents_with_init,
            "without_init": observation.agents_without_init,
        },
    }


@pytest.mark.parametrize(
    "observation",
    [
        ProjectInspectionObservation("yes", 0, 0, 0),  # type: ignore[arg-type]
        _observe(False, 1, 0, 1),
        _observe(True, -1, 0, -1),
        _observe(True, 2, 2, 1),
    ],
)
def test_core_rejects_incoherent_adapter_observations(
    observation: ProjectInspectionObservation,
) -> None:
    with pytest.raises(ProjectInspectionError) as exc:
        ProjectInspectionUseCase(_Inspection(observation)).inspect()

    assert exc.value.error.code == "invalid_project_observation"


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("absent", (ProjectState.ABSENT, 0, 0, 0)),
        ("human-and-helpers", (ProjectState.EMPTY, 0, 0, 0)),
        ("stored-blueprint", (ProjectState.POPULATED, 1, 0, 1)),
        ("initialized", (ProjectState.POPULATED, 1, 1, 0)),
        ("mixed", (ProjectState.POPULATED, 3, 2, 1)),
    ],
)
def test_adapter_reports_marker_facts_without_writes_or_content_parsing(
    tmp_path: Path,
    scenario: str,
    expected: tuple[ProjectState, int, int, int],
) -> None:
    sentinel = tmp_path / "caller-owned.bin"
    sentinel.write_bytes(b"unchanged\x00bytes")
    lingtai_root = tmp_path / ".lingtai"
    if scenario != "absent":
        lingtai_root.mkdir()
    if scenario == "human-and-helpers":
        _agent(lingtai_root, "human", init=True)
        (lingtai_root / ".library_shared" / "skills").mkdir(parents=True)
        (lingtai_root / "utilities").mkdir()
        (lingtai_root / "meta.json").write_bytes(b"caller bytes")
    elif scenario == "stored-blueprint":
        _agent(lingtai_root, "stored-agent")
    elif scenario == "initialized":
        _agent(lingtai_root, "seed", init=True)
    elif scenario == "mixed":
        _agent(lingtai_root, "initialized", init=True)
        _agent(lingtai_root, "stored")
        _agent(lingtai_root, "init-only", manifest=False, init=True)
        (lingtai_root / "plain-helper").mkdir()
    before = _tree_bytes(tmp_path)

    result = _inspect(tmp_path)

    assert (
        result.state,
        result.agent_candidates,
        result.agents_with_init,
        result.agents_without_init,
    ) == expected
    assert _tree_bytes(tmp_path) == before
    if scenario == "absent":
        assert not lingtai_root.exists()


def test_fresh_creation_seed_inspects_as_populated_with_init(tmp_path: Path) -> None:
    FilesystemProjectWorkspaceAdapter(
        tmp_path,
        validate_agent=lambda _agent_dir: None,
    ).create(_seed())

    result = _inspect(tmp_path)

    assert (
        result.state,
        result.agent_candidates,
        result.agents_with_init,
        result.agents_without_init,
    ) == (ProjectState.POPULATED, 1, 1, 0)


def test_partial_root_stays_unchanged_and_fresh_creation_still_refuses_it(
    tmp_path: Path,
) -> None:
    _agent(tmp_path / ".lingtai", "init-only", manifest=False, init=True)
    before = _tree_bytes(tmp_path)

    result = _inspect(tmp_path)
    with pytest.raises(ProjectCreationError) as exc:
        FilesystemProjectWorkspaceAdapter(
            tmp_path,
            validate_agent=lambda _agent_dir: None,
        ).create(_seed("new-agent"))

    assert result.state is ProjectState.POPULATED
    assert exc.value.error.code == "already_initialized"
    assert _tree_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("shape", "error_code"),
    [
        ("caller-missing", "invalid_project_root"),
        ("caller-file", "invalid_project_root"),
        ("caller-symlink", "invalid_project_root"),
        ("target-file", "unsafe_project_structure"),
        ("target-symlink", "unsafe_project_structure"),
        ("manifest-directory", "unsafe_project_structure"),
        ("init-directory", "unsafe_project_structure"),
        ("child-symlink", "unsafe_project_structure"),
    ],
)
def test_adapter_fails_closed_for_invalid_or_unsafe_shapes(
    tmp_path: Path,
    shape: str,
    error_code: str,
) -> None:
    root = tmp_path / "project"
    if shape == "caller-missing":
        pass
    elif shape == "caller-file":
        root.write_text("not a directory", encoding="utf-8")
    elif shape == "caller-symlink":
        root.symlink_to(tmp_path, target_is_directory=True)
    else:
        root.mkdir()
        target = root / ".lingtai"
        if shape == "target-file":
            target.write_text("not a directory", encoding="utf-8")
        elif shape == "target-symlink":
            backing = tmp_path / "backing"
            backing.mkdir()
            target.symlink_to(backing, target_is_directory=True)
        else:
            target.mkdir()
            agent = target / "candidate"
            if shape == "child-symlink":
                agent.symlink_to(tmp_path, target_is_directory=True)
            else:
                agent.mkdir()
                marker = ".agent.json" if shape == "manifest-directory" else "init.json"
                (agent / marker).mkdir()
    before = _tree_bytes(tmp_path)

    with pytest.raises(ProjectInspectionError) as exc:
        _inspect(root)

    assert exc.value.error.code == error_code
    assert _tree_bytes(tmp_path) == before


def test_reparse_attribute_detection_is_platform_neutral() -> None:
    assert project_workspace._is_reparse_point(  # noqa: SLF001
        SimpleNamespace(st_file_attributes=0x400)
    )
    assert not project_workspace._is_reparse_point(  # noqa: SLF001
        SimpleNamespace(st_file_attributes=0)
    )
    assert not project_workspace._is_reparse_point(SimpleNamespace())  # noqa: SLF001


@pytest.mark.parametrize(
    ("boundary", "error_code"),
    [
        ("caller", "invalid_project_root"),
        ("target", "unsafe_project_structure"),
        ("child", "unsafe_project_structure"),
        ("marker", "unsafe_project_structure"),
    ],
)
def test_adapter_rejects_simulated_reparse_metadata_at_every_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: str,
    error_code: str,
) -> None:
    root = tmp_path / "project"
    target = root / ".lingtai"
    candidate = _agent(target, "candidate")
    marked = {
        "caller": root,
        "target": target,
        "child": candidate,
        "marker": candidate / ".agent.json",
    }[boundary]
    marked_metadata = marked.lstat()
    marked_identity = (marked_metadata.st_dev, marked_metadata.st_ino)
    real_is_reparse_point = project_workspace._is_reparse_point  # noqa: SLF001

    def simulated_is_reparse_point(metadata: os.stat_result) -> bool:
        return real_is_reparse_point(metadata) or (
            metadata.st_dev,
            metadata.st_ino,
        ) == marked_identity

    monkeypatch.setattr(
        project_workspace,
        "_is_reparse_point",
        simulated_is_reparse_point,
    )
    before = _tree_bytes(tmp_path)

    with pytest.raises(ProjectInspectionError) as exc:
        _inspect(root)

    assert exc.value.error.code == error_code
    assert _tree_bytes(tmp_path) == before


@pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows junctions")
@pytest.mark.parametrize(
    ("boundary", "error_code"),
    [
        ("caller", "invalid_project_root"),
        ("target", "unsafe_project_structure"),
        ("child", "unsafe_project_structure"),
    ],
)
def test_native_windows_junctions_fail_closed(
    tmp_path: Path,
    boundary: str,
    error_code: str,
) -> None:
    root = tmp_path / "project"
    target = root / ".lingtai"
    backing = tmp_path / "junction-backing"
    backing.mkdir()
    if boundary == "caller":
        link = root
    else:
        root.mkdir()
        if boundary == "target":
            link = target
        else:
            target.mkdir()
            link = target / "candidate"
    completed = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(link), str(backing)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert link.lstat().st_file_attributes & 0x400  # type: ignore[attr-defined]

    with pytest.raises(ProjectInspectionError) as exc:
        _inspect(root)

    assert exc.value.error.code == error_code


@pytest.mark.parametrize(
    ("denied_part", "error_code"),
    [
        ("caller", "invalid_project_root"),
        ("target", "project_inspect_failed"),
    ],
)
def test_adapter_returns_typed_traversal_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    denied_part: str,
    error_code: str,
) -> None:
    target = tmp_path / ".lingtai"
    target.mkdir()
    denied = tmp_path if denied_part == "caller" else target
    real_scandir = os.scandir

    def fail_selected(path: os.PathLike[str] | str):
        if Path(path) == denied:
            raise PermissionError("test-only traversal denial")
        return real_scandir(path)

    monkeypatch.setattr(project_workspace.os, "scandir", fail_selected)

    with pytest.raises(ProjectInspectionError) as exc:
        _inspect(tmp_path)

    assert exc.value.error.code == error_code


def test_adapter_maps_embedded_nul_root_to_invalid_project_root() -> None:
    inspector: ProjectInspectionPort = FilesystemProjectInspectionAdapter(Path("\x00"))

    with pytest.raises(ProjectInspectionError) as exc:
        inspector.inspect()

    assert exc.value.error.code == "invalid_project_root"


@pytest.mark.parametrize(
    "malformed_root",
    ["\x00", "~lingtai-project-inspect-user-that-must-not-exist-20260911"],
)
def test_cli_maps_root_normalization_failures_to_invalid_project_root(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    malformed_root: str,
) -> None:
    from lingtai import cli

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lingtai-agent",
            "project",
            "inspect",
            "--root",
            malformed_root,
            "--json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "error",
        "code": "invalid_project_root",
        "error": "project root must be an existing readable directory",
    }


def test_cli_preserves_generic_error_for_unexpected_inspection_internals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from lingtai import cli

    def fail_unexpectedly(_self: FilesystemProjectInspectionAdapter) -> None:
        raise AssertionError("test-only unexpected internal failure")

    monkeypatch.setattr(FilesystemProjectInspectionAdapter, "inspect", fail_unexpectedly)
    monkeypatch.setattr(
        sys,
        "argv",
        ["lingtai-agent", "project", "inspect", "--root", str(tmp_path), "--json"],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "error",
        "code": "project_inspect_failed",
        "error": "project structure could not be inspected",
    }


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_success_output_is_deterministic_and_path_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    as_json: bool,
) -> None:
    from lingtai import cli

    _agent(tmp_path / ".lingtai", "alpha", init=True)
    argv = ["lingtai-agent", "project", "inspect", "--root", str(tmp_path)]
    if as_json:
        argv.append("--json")
    monkeypatch.setattr(sys, "argv", argv)

    cli.main()

    captured = capsys.readouterr()
    assert captured.err == ""
    if as_json:
        assert json.loads(captured.out) == {
            "status": "inspected",
            "state": "populated",
            "agent_candidates": {"total": 1, "with_init": 1, "without_init": 0},
        }
    else:
        assert captured.out == (
            "Project state: populated\n"
            "Agent workdir candidates: 1 (with init: 1, without init: 0)\n"
        )
    assert str(tmp_path) not in captured.out


def test_cli_json_error_is_stable_and_path_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from lingtai import cli

    (tmp_path / ".lingtai").write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["lingtai-agent", "project", "inspect", "--root", str(tmp_path), "--json"],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "error",
        "code": "unsafe_project_structure",
        "error": "project structure contains an unsafe filesystem entry",
    }
    assert str(tmp_path) not in captured.err
