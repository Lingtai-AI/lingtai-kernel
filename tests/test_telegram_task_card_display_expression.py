"""Telegram Task Card display expression: a small, safe, declarative
composition grammar over already-rendered presentation fragments.

Telegram 14302/14306/14314/14316 (Jason 2026-08-20): the resident automatic
frame's layout becomes a hot-swappable ``display_expression`` durably stored
in the existing agent-wide ``<workdir>/telegram/taskcard.json`` presentation
state owned by ``TelegramService`` (never the bootstrap ``.secrets/telegram.json``
account/token config), with Jason's approved footer-first documented default.
The expression is only an ordered selection from a
fixed allowlist of preformatted fragments the projection already renders
(header/rows/blank/footer/divider/metadata/time/ask_agent) -- it never
evaluates code, interpolates arbitrary workdir/config/event/prompt data, or
scrapes a regex match.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from lingtai.kernel._fsutil import atomic_write_json
from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.service import TelegramService
from tests._notification_store_helpers import notification_store_for


def _service(tmp_path: Path, *aliases: str) -> TelegramService:
    return TelegramService(
        tmp_path,
        [{"alias": alias, "bot_token": f"token-{alias}"} for alias in (aliases or ("main",))],
        lambda *_: None,
    )


def _manager(tmp_path: Path, service: TelegramService) -> TelegramManager:
    return TelegramManager(
        service,
        working_dir=tmp_path,
        on_inbound=lambda _event: None,
        notification_store=notification_store_for(tmp_path),
    )


def _rows() -> list[dict]:
    return [
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False},
    ]


# ---------------------------------------------------------------------------
# Grammar: a fixed, safe allowlist -- no code execution, no data scraping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        (list(TaskCardEventProjection.DISPLAY_SLOTS), tuple(TaskCardEventProjection.DISPLAY_SLOTS)),
        (["footer", "header"], ("footer", "header")),
        ("header", None),  # not a list
        ([], None),  # empty
        (["header", "bogus_slot"], None),  # unknown slot
        (["header", 1], None),  # non-string element
        (["header"] * (TaskCardEventProjection.MAX_DISPLAY_EXPRESSION_LENGTH + 1), None),  # too long
        (["header", "__import__('os').system('x')"], None),  # not a code path -- just an unknown slot
    ],
)
def test_validate_display_expression_grammar(value, expected) -> None:
    assert TaskCardEventProjection.validate_display_expression(value) == expected


# ---------------------------------------------------------------------------
# Approved default: footer-first safe-slot layout
# ---------------------------------------------------------------------------

def test_default_expression_uses_footer_first_layout_with_rows() -> None:
    implicit = TaskCardEventProjection.format_rows_task_card_text(_rows(), normal_rows=1)
    explicit = TaskCardEventProjection.format_rows_task_card_text(
        _rows(), normal_rows=1,
        display_expression=TaskCardEventProjection.DEFAULT_DISPLAY_EXPRESSION,
    )
    assert TaskCardEventProjection.DEFAULT_DISPLAY_EXPRESSION == (
        "footer",
        "header",
        "rows",
        "blank",
        "divider",
        "metadata",
        "time",
        "ask_agent",
    )
    assert explicit == implicit
    assert implicit.startswith("Don't reply to this Task Card.")
    assert implicit.index("Don't reply to this Task Card") < implicit.index("\U0001f4cb ACTIVITIES")
    assert "Ask agent for \"Task Card\"" in implicit


def test_default_expression_uses_footer_first_layout_with_empty_rows() -> None:
    implicit = TaskCardEventProjection.format_rows_task_card_text([], normal_rows=1)
    explicit = TaskCardEventProjection.format_rows_task_card_text(
        [], normal_rows=1,
        display_expression=TaskCardEventProjection.DEFAULT_DISPLAY_EXPRESSION,
    )
    assert explicit == implicit
    assert implicit.startswith("Don't reply to this Task Card.")
    assert implicit.index("Don't reply to this Task Card") < implicit.index("\U0001f4cb ACTIVITIES")


def test_service_display_expression_defaults_to_none(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.taskcard_display_expression() is None
    assert not (tmp_path / "telegram" / "taskcard.json").exists()


# ---------------------------------------------------------------------------
# Valid changed expression: reorders/drops slots, nothing else
# ---------------------------------------------------------------------------

def test_valid_changed_expression_reorders_and_drops_slots() -> None:
    rows = _rows()
    full = TaskCardEventProjection.format_rows_task_card_text(rows, normal_rows=1)
    minimal = TaskCardEventProjection.format_rows_task_card_text(
        rows, normal_rows=1, display_expression=("footer", "header"),
    )
    header = TaskCardEventProjection.header("en")
    footer = TaskCardEventProjection.footer(1, "en")
    assert minimal == f"{footer}\n{header}"
    assert minimal != full
    assert "Ask agent" not in minimal
    assert "Last Updated" not in minimal


def test_service_display_expression_persists_and_reloads(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set_taskcard_display_expression(["footer", "header"])
    assert service.taskcard_display_expression() == ("footer", "header")

    reborn = _service(tmp_path)
    assert reborn.taskcard_display_expression() == ("footer", "header")
    assert reborn.taskcard_enabled() is True
    assert reborn.taskcard_normal_rows() == 1


def test_manager_broadcast_composes_with_custom_display_expression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end wiring: service -> manager -> the real projection path."""
    service = _service(tmp_path, "main")
    service.set_taskcard_display_expression(["footer", "header"])
    manager = _manager(tmp_path, service)
    account = service.get_account("main")
    calls: list[tuple] = []
    monkeypatch.setattr(
        account, "send_message",
        lambda chat_id, text, **_kwargs: calls.append(("send", chat_id, text))
        or {"message_id": 1},
    )
    monkeypatch.setattr(
        account, "edit_message",
        lambda chat_id, message_id, text, **_kwargs:
        calls.append(("edit", chat_id, message_id, text)) or {"ok": True},
    )

    manager._ensure_task_card_resident("main", 123)

    assert calls and calls[0][0] == "send"
    sent_text = calls[0][2]
    header = TaskCardEventProjection.header("en")
    footer = TaskCardEventProjection.footer(1, "en")
    assert sent_text == f"{footer}\n{header}"


# ---------------------------------------------------------------------------
# Hot JSON reload: a direct atomic external edit is visible without restart
# ---------------------------------------------------------------------------

def test_hot_reload_picks_up_external_edit_without_restart(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.taskcard_display_expression() is None

    state_path = tmp_path / "telegram" / "taskcard.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({
            "taskcard": True,
            "normal_rows": 1,
            "max_refreshes": 1000,
            "locale": "en",
            "display_expression": ["footer", "header"],
        }),
        encoding="utf-8",
    )

    # Same live instance, no re-construction: the next read picks it up.
    assert service.taskcard_display_expression() == ("footer", "header")
    # Sibling durable settings loaded by the same reload stay correct.
    assert service.taskcard_enabled() is True
    assert service.taskcard_normal_rows() == 1


def test_hot_reload_reaches_the_live_manager_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    account = service.get_account("main")
    calls: list[tuple] = []
    monkeypatch.setattr(
        account, "send_message",
        lambda chat_id, text, **_kwargs: calls.append(("send", chat_id, text))
        or {"message_id": 1},
    )
    monkeypatch.setattr(
        account, "edit_message",
        lambda chat_id, message_id, text, **_kwargs:
        calls.append(("edit", chat_id, message_id, text)) or {"ok": True},
    )

    manager._ensure_task_card_resident("main", 123)
    assert calls[-1][0] == "send"
    default_text = calls[-1][2]
    assert default_text.startswith("Don't reply to this Task Card.")
    assert default_text.splitlines()[1] == TaskCardEventProjection.header("en")

    state_path = tmp_path / "telegram" / "taskcard.json"
    data = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
        "taskcard": True, "normal_rows": 1, "max_refreshes": 1000, "locale": "en",
    }
    data["display_expression"] = ["footer", "header"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(data), encoding="utf-8")

    calls.clear()
    manager._broadcast_task_card_event_window(force=True)
    assert calls
    header = TaskCardEventProjection.header("en")
    footer = TaskCardEventProjection.footer(1, "en")
    assert calls[-1][-1] == f"{footer}\n{header}"


# ---------------------------------------------------------------------------
# Malformed/unsafe expression: fail closed to the documented default
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_expression",
    [
        "header",
        [],
        ["nonexistent_slot"],
        ["header", 42],
        ["header"] * (TaskCardEventProjection.MAX_DISPLAY_EXPRESSION_LENGTH + 1),
    ],
)
def test_malformed_expression_fails_closed_without_corrupting_siblings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, raw_expression,
) -> None:
    service = _service(tmp_path)
    service.set_taskcard_normal_rows(4)
    state_path = tmp_path / "telegram" / "taskcard.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["display_expression"] = raw_expression
    state_path.write_text(json.dumps(data), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert service.taskcard_display_expression() is None
    assert "display_expression" in caplog.text.lower()
    # A malformed expression never resets or corrupts sibling durable state.
    assert service.taskcard_normal_rows() == 4
    assert service.taskcard_enabled() is True


def test_unreadable_state_file_during_reload_keeps_last_valid_settings(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    service = _service(tmp_path)
    service.set_taskcard_display_expression(["footer", "header"])
    service.set_taskcard_normal_rows(5)
    state_path = tmp_path / "telegram" / "taskcard.json"

    # Corrupt the file directly (never happens via the atomic writer, but a
    # reload must not let a transient bad read wipe the last valid state).
    state_path.write_text("not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert service.taskcard_display_expression() == ("footer", "header")
    assert service.taskcard_normal_rows() == 5
    assert service.taskcard_enabled() is True


def test_malformed_expression_never_reaches_render(tmp_path: Path) -> None:
    """The projection only ever receives a validated expression or ``None``."""
    text = TaskCardEventProjection.format_rows_task_card_text(
        _rows(), normal_rows=1,
        display_expression=TaskCardEventProjection.validate_display_expression(
            ["header", "bogus_slot"]
        ),
    )
    default_text = TaskCardEventProjection.format_rows_task_card_text(_rows(), normal_rows=1)
    assert text == default_text


# ---------------------------------------------------------------------------
# BLOCK repair: a setter must reload before persisting, so an unseen direct
# external edit is never clobbered by this process's stale cached siblings.
# ---------------------------------------------------------------------------

def _external_state() -> dict:
    return {
        "taskcard": True,
        "normal_rows": 7,
        "max_refreshes": 1000,
        "locale": "zh",
        "display_expression": ["footer", "header"],
    }


def test_setter_without_prior_getter_preserves_unseen_external_siblings(
    tmp_path: Path,
) -> None:
    """Reproduces the review's concrete loss and proves it is fixed.

    Sequence: (1) a live service caches constructor-time defaults, (2) an
    operator atomically replaces ``taskcard.json`` with valid changed
    ``normal_rows``/``locale``/``display_expression`` -- this process has not
    called any getter or projection tick yet, so it has not observed the
    edit, (3) an unrelated setter (mirrors a ``/taskcard off`` callback)
    fires. The persisted file, and every subsequent getter, must reflect the
    external siblings unchanged plus only the requested ``taskcard`` flip.
    """
    service = _service(tmp_path)
    state_path = tmp_path / "telegram" / "taskcard.json"
    atomic_write_json(state_path, _external_state(), fsync=True)

    service.set_taskcard_enabled(False)

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted == {
        "taskcard": False,
        "normal_rows": 7,
        "max_refreshes": 1000,
        "locale": "zh",
        "display_expression": ["footer", "header"],
    }
    assert service.taskcard_enabled() is False
    assert service.taskcard_normal_rows() == 7
    assert service.taskcard_locale() == "zh"
    assert service.taskcard_display_expression() == ("footer", "header")


@pytest.mark.parametrize(
    "setter_name, args",
    [
        ("set_taskcard_normal_rows", (3,)),
        ("set_taskcard_max_refreshes", (250,)),
        ("set_taskcard_locale", ("en",)),
        ("set_taskcard_display_expression", (["header"],)),
    ],
)
def test_every_setter_preserves_unseen_external_siblings_without_prior_getter(
    tmp_path: Path, setter_name: str, args: tuple,
) -> None:
    """Every persistence setter -- not just ``set_taskcard_enabled`` -- must
    reload before deriving/persisting, including when the setter's own field
    is the one an external edit also touched: the caller's requested value
    wins for that field, but every *other* externally-edited sibling must
    still survive the write untouched."""
    service = _service(tmp_path)
    state_path = tmp_path / "telegram" / "taskcard.json"
    atomic_write_json(state_path, _external_state(), fsync=True)

    getattr(service, setter_name)(*args)

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    expected = _external_state()
    field = {
        "set_taskcard_normal_rows": "normal_rows",
        "set_taskcard_max_refreshes": "max_refreshes",
        "set_taskcard_locale": "locale",
        "set_taskcard_display_expression": "display_expression",
    }[setter_name]
    expected[field] = args[0]
    assert persisted == expected


def test_setter_preserves_unseen_external_edit_through_manager_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the preserved external siblings actually reach the real
    projection path on the very next tick after the unrelated setter call."""
    service = _service(tmp_path, "main")
    manager = _manager(tmp_path, service)
    account = service.get_account("main")
    calls: list[tuple] = []
    monkeypatch.setattr(
        account, "send_message",
        lambda chat_id, text, **_kwargs: calls.append(("send", chat_id, text))
        or {"message_id": 1},
    )
    monkeypatch.setattr(
        account, "edit_message",
        lambda chat_id, message_id, text, **_kwargs:
        calls.append(("edit", chat_id, message_id, text)) or {"ok": True},
    )

    state_path = tmp_path / "telegram" / "taskcard.json"
    atomic_write_json(
        state_path,
        {
            "taskcard": True,
            "normal_rows": 1,
            "max_refreshes": 1000,
            "locale": "en",
            "display_expression": ["footer", "header"],
        },
        fsync=True,
    )

    # Unrelated setter, no preceding getter/projection tick -- exactly the
    # sequence that previously lost the external display_expression edit.
    service.set_taskcard_max_refreshes(500)

    manager._ensure_task_card_resident("main", 123)

    assert calls and calls[0][0] == "send"
    sent_text = calls[0][2]
    header = TaskCardEventProjection.header("en")
    footer = TaskCardEventProjection.footer(1, "en")
    assert sent_text == f"{footer}\n{header}"

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["max_refreshes"] == 500
    assert persisted["display_expression"] == ["footer", "header"]
    assert persisted["locale"] == "en"
