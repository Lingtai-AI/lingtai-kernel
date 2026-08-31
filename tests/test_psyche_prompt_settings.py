"""Focused contract tests for Psyche-owned configurable prompt inputs."""
from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.agent import Agent
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

    monkeypatch.setattr(Path, "open", lambda _path, *_args, **_kwargs: (_ for _ in ()).throw(OSError("nope")))
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

        snapshot = build_settings_provider(agent)()
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
        assert [row.current for row in build_settings_provider(agent)()] == [
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
        provider = build_settings_provider(agent)
        agent._reconstruct_context()
        applied = [row.current for row in provider()]

        _write_owner(tmp_path, base_prompt="PENDING BASE", covenant="PENDING COVENANT")

        def fail_final_flush() -> None:
            raise RuntimeError("final prompt publication failed")

        monkeypatch.setattr(agent, "_flush_system_prompt", fail_final_flush)
        with pytest.raises(RuntimeError, match="final prompt publication failed"):
            agent._reconstruct_context()

        assert [row.current for row in provider()] == applied
        assert applied == [
            "", None, "APPLIED BASE", None, "APPLIED COVENANT", None,
            "", None,
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
