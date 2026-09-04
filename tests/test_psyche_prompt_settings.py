"""Focused contract tests for Psyche-owned configurable prompt inputs."""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.adapters.tool_plugin_host import AgentPsycheSettingsAdapter
from lingtai.agent import Agent
from lingtai.tools.psyche import settings as psyche_settings
from lingtai.tools.psyche.settings import (
    PsycheSettingsError,
    build_settings_provider,
    read_prompt_owner_values,
    read_resolved_prompt_inputs,
)
from tests._service_helpers import make_gemini_mock_service as make_mock_service


def _write_init(root: Path, **legacy: object) -> None:
    data: dict[str, object] = {
        "manifest": {"llm": {"provider": "openai", "model": "test"}},
        "pad": "",
    }
    data.update(legacy)
    (root / "init.json").write_text(json.dumps(data), encoding="utf-8")


def _write_owner(root: Path, **values: object) -> Path:
    path = root / "settings" / "psyche.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, **values}), encoding="utf-8"
    )
    return path


def _agent(root: Path) -> Agent:
    return Agent(
        service=make_mock_service(), agent_name="test", working_dir=root,
        capabilities=[],
    )


def _settings_provider(agent: Agent):
    return build_settings_provider(
        AgentPsycheSettingsAdapter(lambda: agent._psyche_settings_snapshot)
    )


@dataclass(frozen=True)
class _AlternateSnapshot:
    """Independent structural implementation of PsycheSettingsSnapshotPort."""

    pad: str
    pad_file: str | None
    base_prompt: str
    base_prompt_file: str | None
    covenant: str
    covenant_file: str | None
    comment: str
    comment_file: str | None


class _AlternateSettingsPort:
    def read_snapshot(self) -> _AlternateSnapshot:
        return _AlternateSnapshot(
            pad="alternate pad",
            pad_file="pad.md",
            base_prompt="alternate base",
            base_prompt_file=None,
            covenant="alternate covenant",
            covenant_file="covenant.md",
            comment="alternate comment",
            comment_file=None,
        )


def test_settings_provider_accepts_an_independent_structural_snapshot() -> None:
    rows = build_settings_provider(_AlternateSettingsPort())()

    assert [row.current for row in rows] == [
        "alternate pad",
        "pad.md",
        "alternate base",
        None,
        "alternate covenant",
        "covenant.md",
        "alternate comment",
        None,
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pad", None),
        ("pad_file", 1),
        ("base_prompt", False),
        ("base_prompt_file", []),
        ("covenant", {}),
        ("covenant_file", object()),
        ("comment", 3.5),
        ("comment_file", True),
    ],
)
def test_settings_provider_validates_every_structural_snapshot_field(
    field: str, value: object,
) -> None:
    values = {
        "pad": "",
        "pad_file": None,
        "base_prompt": "",
        "base_prompt_file": None,
        "covenant": "",
        "covenant_file": None,
        "comment": "",
        "comment_file": None,
    }
    values[field] = value

    @dataclass(frozen=True)
    class InvalidSnapshot:
        pad: object
        pad_file: object
        base_prompt: object
        base_prompt_file: object
        covenant: object
        covenant_file: object
        comment: object
        comment_file: object

    class InvalidSettingsPort:
        def read_snapshot(self) -> InvalidSnapshot:
            return InvalidSnapshot(**values)

    with pytest.raises(RuntimeError, match="snapshot is unavailable"):
        build_settings_provider(InvalidSettingsPort())()


def test_missing_owner_document_defaults_all_six_values(tmp_path: Path) -> None:
    assert read_prompt_owner_values(tmp_path) == {}
    inputs = read_resolved_prompt_inputs(tmp_path)
    assert (
        inputs.base_prompt,
        inputs.base_prompt_file,
        inputs.covenant,
        inputs.covenant_file,
        inputs.comment,
        inputs.comment_file,
    ) == ("", None, "", None, "", None)


def test_owner_document_serializer_round_trips_through_the_owner_reader(
    tmp_path: Path,
) -> None:
    content = psyche_settings.serialize_prompt_owner_document(
        base_prompt="BASE",
        base_prompt_file="base.md",
        covenant="COVENANT",
        covenant_file="covenant.md",
        comment="COMMENT",
        comment_file="comment.md",
    )
    path = tmp_path / "settings" / "psyche.json"
    path.parent.mkdir()
    path.write_text(content, encoding="utf-8")

    assert read_prompt_owner_values(tmp_path) == {
        "base_prompt": "BASE",
        "base_prompt_file": str(tmp_path / "base.md"),
        "covenant": "COVENANT",
        "covenant_file": str(tmp_path / "covenant.md"),
        "comment": "COMMENT",
        "comment_file": str(tmp_path / "comment.md"),
    }
    assert json.loads(content)["schema_version"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "{}",
        '{"schema_version": true}',
        '{"schema_version": 2}',
        '{"schema_version": 1, "base_prompt": 3}',
        '{"schema_version": 1, "unknown": "x"}',
        '{"schema_version": 1, "comment": "a", "comment": "b"}',
        "{not-json}",
    ],
)
def test_owner_document_is_closed_strict_v1(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "settings" / "psyche.json"
    path.parent.mkdir()
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(PsycheSettingsError):
        read_resolved_prompt_inputs(tmp_path)


def test_owner_validation_uses_fixed_field_order(tmp_path: Path) -> None:
    _write_owner(tmp_path, comment=False, covenant=[], base_prompt=3)

    with pytest.raises(PsycheSettingsError, match="base_prompt must be a string"):
        read_resolved_prompt_inputs(tmp_path)


def test_owner_document_rejects_utf8_size_symlink_race_and_read_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    path = tmp_path / "settings" / "psyche.json"
    path.parent.mkdir()

    path.write_bytes(b'\xff')
    with pytest.raises(PsycheSettingsError):
        read_resolved_prompt_inputs(tmp_path)

    path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(PsycheSettingsError):
        read_resolved_prompt_inputs(tmp_path)

    target = tmp_path / "owner.json"
    target.write_text('{"schema_version": 1}', encoding="utf-8")
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("the platform does not permit symlink creation for this test")
    with pytest.raises(PsycheSettingsError):
        read_resolved_prompt_inputs(tmp_path)

    path.unlink()
    path.write_text('{"schema_version": 1}', encoding="utf-8")
    real_lstat = Path.lstat
    calls = 0

    def unstable_lstat(candidate: Path):
        nonlocal calls
        result = real_lstat(candidate)
        if candidate == path:
            calls += 1
            if calls == 2:
                return SimpleNamespace(
                    st_dev=result.st_dev,
                    st_ino=result.st_ino,
                    st_mode=result.st_mode,
                    st_size=result.st_size,
                    st_mtime_ns=result.st_mtime_ns + 1,
                    st_ctime_ns=result.st_ctime_ns,
                )
        return result

    monkeypatch.setattr(Path, "lstat", unstable_lstat)
    with pytest.raises(PsycheSettingsError):
        read_resolved_prompt_inputs(tmp_path)
    monkeypatch.undo()

    forged = tmp_path / "forged.json"
    forged.write_text('{"schema_version": 2}', encoding="utf-8")
    assert forged.stat().st_size == path.stat().st_size
    real_open = os.open

    def open_forged(candidate, flags, *args, **kwargs):
        if Path(candidate) == path:
            return real_open(forged, flags, *args, **kwargs)
        return real_open(candidate, flags, *args, **kwargs)

    monkeypatch.setattr(psyche_settings.os, "open", open_forged)
    with pytest.raises(PsycheSettingsError, match="changed while being read"):
        read_resolved_prompt_inputs(tmp_path)
    monkeypatch.undo()

    monkeypatch.setattr(
        psyche_settings.os,
        "open",
        lambda _path, _flags, *_args, **_kwargs: (
            _ for _ in ()
        ).throw(OSError("nope")),
    )
    with pytest.raises(PsycheSettingsError):
        read_resolved_prompt_inputs(tmp_path)


def test_owner_file_pairs_win_and_relative_paths_anchor_to_agent_workdir(
    tmp_path: Path,
) -> None:
    for name, content in {
        "base.md": "BASE FROM FILE",
        "covenant.md": "COVENANT FROM FILE",
        "comment.md": "COMMENT FROM FILE",
    }.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    _write_owner(
        tmp_path,
        base_prompt="base fallback",
        base_prompt_file="base.md",
        covenant="covenant fallback",
        covenant_file="covenant.md",
        comment="comment fallback",
        comment_file="comment.md",
    )

    inputs = read_resolved_prompt_inputs(tmp_path)
    assert (inputs.base_prompt, inputs.covenant, inputs.comment) == (
        "BASE FROM FILE", "COVENANT FROM FILE", "COMMENT FROM FILE",
    )
    assert (inputs.base_prompt_file, inputs.covenant_file, inputs.comment_file) == (
        str(tmp_path / "base.md"),
        str(tmp_path / "covenant.md"),
        str(tmp_path / "comment.md"),
    )

    _write_owner(tmp_path, base_prompt="base fallback", base_prompt_file="missing.md")
    assert read_resolved_prompt_inputs(tmp_path).base_prompt == "base fallback"


def test_reconstruction_uses_only_psyche_owner_and_preserves_prompt_contract(
    tmp_path: Path,
) -> None:
    _write_init(
        tmp_path,
        base_prompt={"legacy": "not type checked or rendered"},
        covenant="LEGACY COVENANT MUST NOT RENDER",
        comment="LEGACY COMMENT MUST NOT RENDER",
    )
    agent = _agent(tmp_path)
    try:
        agent._reconstruct_context()
        # This is the established builder state for the equivalent pre-owner
        # values. Psyche must feed that exact same state without changing bytes
        # or render order.
        agent._base_prompt = "OWNER BASE"
        agent._prompt_manager.write_section(
            "covenant", "OWNER COVENANT", protected=True,
        )
        agent._prompt_manager.write_section("comment", "OWNER COMMENT")
        expected_prompt = agent._build_system_prompt()
        expected_batches = "\n".join(agent._build_system_prompt_batches())

        _write_owner(
            tmp_path,
            base_prompt="OWNER BASE",
            covenant="OWNER COVENANT",
            comment="OWNER COMMENT",
        )
        agent._reconstruct_context()
        prompt = agent._build_system_prompt()
        assert prompt == expected_prompt
        assert "\n".join(agent._build_system_prompt_batches()) == expected_batches
        assert "LEGACY" not in prompt
        principle = prompt.index("Progressive disclosure principle: each resident prompt layer")
        base = prompt.index("OWNER BASE")
        covenant = prompt.index("OWNER COVENANT")
        comment = prompt.index("OWNER COMMENT")
        assert principle < base < covenant < comment
        assert (tmp_path / "system" / "base_prompt.md").read_text(encoding="utf-8") == "OWNER BASE"
        assert (tmp_path / "system" / "covenant.md").read_text(encoding="utf-8") == "OWNER COVENANT"

        snapshot = _settings_provider(agent)()
        assert [row.key for row in snapshot] == [
            "pad", "pad_file", "base_prompt", "base_prompt_file",
            "covenant", "covenant_file", "comment", "comment_file",
        ]
        assert [row.default for row in snapshot] == ["", None, "", None, "", None, "", None]
        assert all(row.configurable and row._sensitive for row in snapshot)
        assert [row.comment for row in snapshot] == [
            "psyche-manual#setting-pad",
            "psyche-manual#setting-pad-file",
            "psyche-manual#setting-base-prompt",
            "psyche-manual#setting-base-prompt-file",
            "psyche-manual#setting-covenant",
            "psyche-manual#setting-covenant-file",
            "psyche-manual#setting-comment",
            "psyche-manual#setting-comment-file",
        ]

        _write_owner(tmp_path, unknown="invalid pending edit")
        with pytest.raises(PsycheSettingsError):
            agent._reconstruct_context()
        assert agent._build_system_prompt() == prompt
        assert [row.current for row in _settings_provider(agent)()] == [
            "", None, "OWNER BASE", None, "OWNER COVENANT", None,
            "OWNER COMMENT", None,
        ]
    finally:
        agent.stop(timeout=1.0)


def test_show_snapshot_commits_only_after_final_prompt_flush(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _write_init(tmp_path)
    _write_owner(tmp_path, base_prompt="APPLIED BASE", covenant="APPLIED COVENANT")
    agent = _agent(tmp_path)
    try:
        provider = _settings_provider(agent)
        agent._reconstruct_context()
        applied = [row.current for row in provider()]
        applied_sections = {
            name: dict(section)
            for name, section in agent._prompt_manager._sections.items()
        }
        applied_prompt = agent._build_system_prompt()
        applied_system_mirror = (
            tmp_path / "system" / "system.md"
        ).read_text(encoding="utf-8")

        _write_owner(tmp_path, base_prompt="PENDING BASE", covenant="PENDING COVENANT")

        original_flush = agent._flush_system_prompt

        def fail_final_flush() -> None:
            original_flush()
            raise RuntimeError("final prompt publication failed")

        monkeypatch.setattr(agent, "_flush_system_prompt", fail_final_flush)
        with pytest.raises(RuntimeError, match="final prompt publication failed"):
            agent._reconstruct_context()

        assert [row.current for row in provider()] == applied
        assert agent._prompt_manager._sections == applied_sections
        assert agent._base_prompt == "APPLIED BASE"
        assert agent._build_system_prompt() == applied_prompt
        assert (
            tmp_path / "system" / "base_prompt.md"
        ).read_text(encoding="utf-8") == "APPLIED BASE"
        assert (
            tmp_path / "system" / "covenant.md"
        ).read_text(encoding="utf-8") == "APPLIED COVENANT"
        assert (
            tmp_path / "system" / "system.md"
        ).read_text(encoding="utf-8") == applied_system_mirror
        assert applied == [
            "", None, "APPLIED BASE", None, "APPLIED COVENANT", None,
            "", None,
        ]

        # Clearing the rejected generation must fall back to the last-good
        # mirrors, never to values written by the failed candidate.
        monkeypatch.setattr(agent, "_flush_system_prompt", original_flush)
        _write_owner(tmp_path)
        agent._reconstruct_context()
        prompt_after_clear = agent._build_system_prompt()
        assert "APPLIED BASE" in prompt_after_clear
        assert "APPLIED COVENANT" in prompt_after_clear
        assert "PENDING BASE" not in prompt_after_clear
        assert "PENDING COVENANT" not in prompt_after_clear
    finally:
        agent.stop(timeout=1.0)


def test_each_successful_reconstruction_reads_once_and_advances_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _write_init(tmp_path)
    _write_owner(tmp_path, base_prompt="BASE A", comment="COMMENT A")
    agent = _agent(tmp_path)
    provider = _settings_provider(agent)
    real_read = psyche_settings.read_resolved_prompt_inputs
    reads: list[Path] = []

    def counted_read(root):
        reads.append(Path(root))
        return real_read(root)

    monkeypatch.setattr(psyche_settings, "read_resolved_prompt_inputs", counted_read)
    try:
        agent._reconstruct_context()
        assert reads == [tmp_path]
        assert [row.current for row in provider()] == [
            "", None, "BASE A", None, "", None, "COMMENT A", None,
        ]

        _write_owner(tmp_path, base_prompt="BASE B", covenant="COVENANT B")
        agent._reconstruct_context()
        assert reads == [tmp_path, tmp_path]
        assert [row.current for row in provider()] == [
            "", None, "BASE B", None, "COVENANT B", None, "", None,
        ]
    finally:
        agent.stop(timeout=1.0)


def test_base_and_covenant_mirrors_fall_back_but_comment_does_not(tmp_path: Path) -> None:
    _write_init(tmp_path)
    _write_owner(tmp_path, base_prompt="BASE", covenant="COVENANT", comment="COMMENT")
    agent = _agent(tmp_path)
    try:
        agent._reconstruct_context()
        _write_owner(tmp_path)
        agent._reconstruct_context()
        prompt = agent._build_system_prompt()
        assert "BASE" in prompt
        assert "COVENANT" in prompt
        assert "COMMENT" not in prompt
    finally:
        agent.stop(timeout=1.0)


def test_psyche_labt_and_contract_commands_run_both_focused_suites() -> None:
    root = Path(__file__).resolve().parents[1]
    behaviors = (root / "src/lingtai/tools/psyche/BEHAVIORS.md").read_text(
        encoding="utf-8"
    )
    contract = (root / "src/lingtai/tools/psyche/CONTRACT.md").read_text(
        encoding="utf-8"
    )
    command = (
        "python -m pytest -q tests/test_psyche_family.py "
        "tests/test_psyche_prompt_settings.py"
    )

    assert "  - tests/test_psyche_prompt_settings.py" in behaviors
    assert command in behaviors
    assert command in contract
