"""Focused contract tests for the Task Card loaded-runtime drift diagnostic (issue #987).

When the on-disk checkout gains the non-deletable Task Card self-heal under a
long-lived manager process, the loaded runtime predates the installed source:
``/taskcard`` must stop implying resident health and emit one bounded
``/refresh``-required hint instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram import _runtime as tg_runtime
from tests.test_telegram_task_card_toggle import _service


def _write_source_tree(root: Path) -> None:
    for rel in tg_runtime._TASK_CARD_SOURCE_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel, encoding="utf-8")


# Pure runtime-identity helpers -------------------------------------------------


def test_loaded_digest_is_deterministic_hex_prefix() -> None:
    loaded = tg_runtime.LOADED_TASK_CARD_SOURCE_DIGEST
    assert isinstance(loaded, str)
    assert len(loaded) == 12
    assert all(c in "0123456789abcdef" for c in loaded)


def test_installed_digest_tracks_on_disk_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "pkg"
    _write_source_tree(root)
    monkeypatch.setattr(tg_runtime, "_task_card_source_dir", lambda: root)

    first = tg_runtime.installed_task_card_source_digest()
    assert first is not None
    # The exact #987 drift: the on-disk manager gains the repair without the
    # running process importing it again.
    (root / "manager.py").write_text("repaired\n", encoding="utf-8")
    second = tg_runtime.installed_task_card_source_digest()
    assert second is not None
    assert second != first


def test_drift_hint_none_when_loaded_matches_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = tg_runtime.LOADED_TASK_CARD_SOURCE_DIGEST
    monkeypatch.setattr(tg_runtime, "installed_task_card_source_digest", lambda: loaded)
    assert tg_runtime.task_card_drift_hint() is None


def test_drift_hint_returned_when_loaded_predates_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded = tg_runtime.LOADED_TASK_CARD_SOURCE_DIGEST
    monkeypatch.setattr(tg_runtime, "installed_task_card_source_digest", lambda: "0" * 12)
    hint = tg_runtime.task_card_drift_hint()
    assert hint is not None
    assert "one /refresh is required" in hint
    assert loaded != "0" * 12


def test_drift_hint_silent_when_unprovable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tg_runtime, "installed_task_card_source_digest", lambda: None)
    assert tg_runtime.task_card_drift_hint() is None


# /taskcard surfaces -----------------------------------------------------------


def _account_and_replies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service = _service(tmp_path, "main", allowed_users=[7])
    account = service.get_account("main")
    replies: list[str] = []
    monkeypatch.setattr(
        account,
        "send_message",
        lambda _chat_id, text, **_kwargs: replies.append(text) or {"message_id": 1},
    )
    return account, replies


def test_taskcard_settings_response_carries_drift_hint_only_when_drifting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, replies = _account_and_replies(tmp_path, monkeypatch)

    # Healthy state: loaded matches installed — plain settings response, no hint.
    monkeypatch.setattr(tg_runtime, "task_card_drift_hint", lambda: None)
    account._cmd_taskcard(123, "/taskcard on")
    assert "taskcard: True" in replies[-1]
    assert "one /refresh" not in replies[-1]

    # Drift: loaded predates installed — one bounded, actionable hint appended.
    monkeypatch.setattr(tg_runtime, "task_card_drift_hint", lambda: tg_runtime.DRIFT_HINT)
    account._cmd_taskcard(123, "/taskcard on")
    assert "taskcard: True" in replies[-1]
    assert "one /refresh is required" in replies[-1]
    assert "on | /taskcard off" in replies[-1]  # usage line still intact


def test_taskcard_menu_carries_drift_hint_only_when_drifting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    account, replies = _account_and_replies(tmp_path, monkeypatch)

    monkeypatch.setattr(tg_runtime, "task_card_drift_hint", lambda: None)
    account._cmd_taskcard_menu(123)
    assert "Task Card — settings" in replies[-1]
    assert "one /refresh" not in replies[-1]

    monkeypatch.setattr(tg_runtime, "task_card_drift_hint", lambda: tg_runtime.DRIFT_HINT)
    account._cmd_taskcard_menu(123)
    assert "Task Card — settings" in replies[-1]
    assert "one /refresh is required" in replies[-1]
