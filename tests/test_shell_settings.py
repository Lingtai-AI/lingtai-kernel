"""Focused proofs for Shell's read-only five-field settings inventory."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lingtai.tools.bash import ShellManager, ShellPolicy
from lingtai.tools.bash._shell_dialect import ShellKind
from lingtai.tools.bash._tool_family import (
    DECLARATION,
    ShellFamilyDispatcher,
    get_schema,
)


def _dispatcher(tmp_path: Path) -> tuple[ShellFamilyDispatcher, ShellManager]:
    manager = ShellManager(
        policy=ShellPolicy(allow=["fixture-command-not-for-output"]),
        working_dir=str(tmp_path),
        max_output=1_234,
        shell_kind=ShellKind.POSIX,
        rehydrate=False,
    )
    source = SimpleNamespace(_working_dir=tmp_path)
    return ShellFamilyDispatcher(manager, source), manager


def _settings(dispatcher: ShellFamilyDispatcher, action_input: object) -> dict:
    return dispatcher.handle(
        {
            "action": "settings",
            "input": action_input,
            "reasoning": "inspect applied Shell settings",
        }
    )


def test_shell_opts_in_immediately_before_manual():
    assert DECLARATION.settings is True
    assert DECLARATION.public_actions == (
        "run",
        "poll",
        "cancel",
        "settings",
        "manual",
    )
    schema = get_schema()
    assert schema["properties"]["action"]["enum"] == list(DECLARATION.public_actions)
    assert [
        branch["title"] for branch in schema["properties"]["input"]["anyOf"]
    ] == [
        "run input",
        "poll input",
        "cancel input",
        "settings inventory input",
        "manual input",
    ]


def test_exact_rows_values_flags_redaction_and_manual_targets(tmp_path, monkeypatch):
    monkeypatch.delenv("LINGTAI_TOOL_TIMEOUT_MAX_SECONDS", raising=False)
    dispatcher, manager = _dispatcher(tmp_path)
    before = dict(manager.__dict__)

    def _forbid_policy_render(_policy):
        raise AssertionError("SHOW must not render command policy rules")

    monkeypatch.setattr(ShellPolicy, "describe", _forbid_policy_render)
    result = _settings(dispatcher, {})

    expected = {
        "shell_kind": (
            "posix",
            None,
            True,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
        "sync_timeout_default_seconds": (
            30,
            30,
            False,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
        "sync_timeout_max_seconds": (
            120.0,
            120.0,
            True,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
        "result_max_chars": (
            1_234,
            50_000,
            True,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
        "async_default": (
            False,
            False,
            False,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
        "async_reminder_default_seconds": (
            1_800.0,
            1_800.0,
            False,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
        "command_policy": (
            "<redacted>",
            "<redacted>",
            True,
            "shell-manual#settings-inventory",
            "Settings inventory",
        ),
    }
    rows = result["settings"]
    assert [row["key"] for row in rows] == list(expected)
    assert manager.__dict__ == before
    for row in rows:
        assert list(row) == [
            "key",
            "current",
            "default",
            "configurable",
            "comment",
        ]
        current, default, configurable, comment, _heading = expected[row["key"]]
        assert (row["current"], row["default"]) == (current, default)
        assert row["configurable"] is configurable
        assert row["comment"] == comment
    assert "fixture-command-not-for-output" not in repr(result)

    manual = (
        Path(__file__).parents[1]
        / "src"
        / "lingtai"
        / "tools"
        / "bash"
        / "manual"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    for _current, _default, _configurable, comment, heading in expected.values():
        assert comment == "shell-manual#settings-inventory"
        assert f"## {heading}" in manual


def test_fresh_effective_values_are_re_read(tmp_path, monkeypatch):
    dispatcher, manager = _dispatcher(tmp_path)
    monkeypatch.setenv("LINGTAI_TOOL_TIMEOUT_MAX_SECONDS", "47.5")
    first = _settings(dispatcher, {})
    manager._max_output = 2_345
    monkeypatch.setenv("LINGTAI_TOOL_TIMEOUT_MAX_SECONDS", "63")
    second = _settings(dispatcher, {})

    assert first["settings"][2]["current"] == 47.5
    assert second["settings"][2]["current"] == 63.0
    assert first["settings"][2]["default"] == second["settings"][2]["default"] == 120.0
    assert first["settings"][3]["current"] == 1_234
    assert second["settings"][3]["current"] == 2_345


def test_unavailable_current_returns_one_fixed_failure_without_rows(tmp_path):
    dispatcher, manager = _dispatcher(tmp_path)
    manager._max_output = None

    assert _settings(dispatcher, {}) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_settings_is_strict_empty_and_basic_run_dispatch_is_unchanged(
    tmp_path, monkeypatch
):
    dispatcher, manager = _dispatcher(tmp_path)
    assert _settings(dispatcher, {"set": "result_max_chars"}) == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "unsupported shell input field",
    }
    assert _settings(dispatcher, None) == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "input must be an object",
    }
    assert not {"set", "reset"} & set(DECLARATION.public_actions)

    observed: list[dict] = []
    monkeypatch.setattr(
        manager,
        "handle",
        lambda args: observed.append(dict(args)) or {"status": "ok", "marker": "raw"},
    )
    result = dispatcher.handle(
        {
            "action": "run",
            "input": {"command": "printf unchanged", "async": False},
            "reasoning": "prove ordinary dispatch",
        }
    )
    assert result == {"status": "ok", "marker": "raw"}
    assert observed == [
        {"command": "printf unchanged", "async": False, "action": "run"}
    ]
