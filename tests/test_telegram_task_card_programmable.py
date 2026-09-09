"""Tests for Telegram's read-only projection of the intrinsic Task Card files."""

from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import notification_store_for


class FakeAccount:
    alias = "mybot"

    def __init__(self):
        self.calls: list = []
        self.sent: dict[int, str] = {}
        self._resident: dict[int, str] = {}

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        msg_id = len(self.calls) + 100
        self.sent[msg_id] = text
        self.calls.append(("send", chat_id, text, kwargs))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.sent[message_id] = text
        self.calls.append(("edit", chat_id, message_id, text, kwargs))
        return {"ok": True}

    def delete_message(self, chat_id, message_id, **kwargs):
        self.calls.append(("delete", chat_id, message_id))
        return {"ok": True}

    def get_task_card(self, chat_id):
        return self._resident.get(chat_id)

    def set_task_card(self, chat_id, compound_id):
        self._resident[chat_id] = compound_id

    def list_task_card_chats(self):
        return sorted(self._resident)


class FakeService:
    def __init__(self):
        self.default_account = FakeAccount()
        self._enabled = True

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account

    def list_accounts(self):
        return ["mybot"]

    def taskcard_enabled(self):
        return self._enabled

    def set_taskcard_enabled(self, enabled):
        self._enabled = enabled

    def taskcard_normal_rows(self):
        return 1


def _manager(tmp_path):
    service = FakeService()
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=notification_store_for(Path(tmp_path)),
    )
    return manager, service.default_account, service


def _auto(manager, reasoning="build"):
    return manager._handle_task_card_update(
        {
            "sub_action": "create",
            "account": "mybot",
            "chat_id": 55,
            "tool": "bash",
            "tool_action": "run",
            "reasoning": reasoning,
        }
    )


def _write_intrinsic_taskcard(tmp_path: Path, *, status: str, body: str | None) -> None:
    taskcard_dir = tmp_path / "taskcard"
    taskcard_dir.mkdir(parents=True, exist_ok=True)
    (taskcard_dir / "status").write_text(status, encoding="utf-8")
    body_path = taskcard_dir / "taskcard.md"
    if body is None:
        if body_path.exists():
            body_path.unlink()
    else:
        body_path.write_text(body, encoding="utf-8")


def _current(account: FakeAccount) -> str:
    return account.sent[max(account.sent)]


def _controlled_gate(manager):
    now = [100.0]
    manager._task_card_edit_clock = lambda: now[0]

    def drain():
        now[0] += manager._TASK_CARD_EVENT_POLL_INTERVAL
        manager._flush_pending_task_card_edits()

    return drain


@pytest.mark.parametrize(
    "channels, expected_fragments",
    [
        (("automatic",), ("*ACTIVITIES*",)),
        (("programmable",), ("*Authored watch*",)),
        (("automatic", "programmable"), ("*ACTIVITIES*", "*Authored watch*")),
    ],
)
def test_whole_resident_uses_legacy_markdown_for_every_slot_composition(
    tmp_path, channels, expected_fragments,
):
    manager, acct, _service = _manager(tmp_path)
    for channel in channels:
        frame = (
            manager._format_rows_task_card_text(
                [{"tool": "bash", "tool_action": "run", "reasoning": "build"}],
                normal_rows=1,
            )
            if channel == "automatic"
            else "*Authored watch*\n`trusted code`"
        )
        result = manager._deliver_channel_frame(
            "mybot", 55, channel, frame, error="projection failed",
        )
        assert result["status"] == "ok"

    text = _current(acct)
    for fragment in expected_fragments:
        assert fragment in text
    assert acct.calls[-1][-1] == {"parse_mode": "Markdown"}
    if len(channels) == 2:
        assert acct.calls[0][0] == "send"
        assert acct.calls[-1][0] == "edit"
        assert all(call[-1] == {"parse_mode": "Markdown"} for call in acct.calls)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("\\_", "\\\\_"),
        ("\\*", "\\\\*"),
        ("\\`", "\\\\`"),
        ("\\[", "\\\\["),
        ("\\\\repeated\\", "\\\\repeated\\"),
    ],
)
def test_source_backslashes_are_preserved_before_legacy_specials_and_at_tail(
    tmp_path, source, expected,
):
    manager, _acct, _service = _manager(tmp_path)
    automatic = manager._format_rows_task_card_text(
        [{"kind": "text", "text": source}], normal_rows=1,
    )
    row = next(line for line in automatic.splitlines() if line.startswith("• "))
    assert row == f"• {expected}"


def test_automatic_fragments_are_escaped_but_programmable_markdown_is_trusted(
    tmp_path,
):
    manager, acct, _service = _manager(tmp_path)
    hostile = r"name_*`[label]\\ [link](https://example.com/a_b)"
    metadata_hostile = r"meta_*`[label]\\"
    automatic = manager._format_rows_task_card_text(
        [
            {
                "tool": hostile,
                "tool_action": hostile,
                "reasoning": hostile,
                "status": "success",
            },
            {"kind": "text", "text": hostile},
        ],
        metadata={
            "model": metadata_hostile,
            "thinking": metadata_hostile,
            "service_tier": metadata_hostile,
            "endpoint": metadata_hostile,
            "working_dir": r"C:\work_dir",
            "async_work": {
                "daemon": {
                    "backend_counts": {"bad_backend": 1},
                    "model_counts": {"bad_model": 1},
                },
            },
        },
        normal_rows=1,
    )
    created = manager._deliver_channel_frame(
        "mybot", 55, "automatic", automatic, error="projection failed",
    )
    assert created["status"] == "ok"
    assert acct.calls[-1][-1] == {"parse_mode": "Markdown"}
    assert r"name\_\*\`\[label]\\" in automatic
    assert r"\[link](https://example.com/a\_b)" in automatic
    assert r"meta\_\*\`\[label]\\" in automatic
    assert r"C:\work\_dir" in automatic
    assert r"bad\_backend" in automatic
    assert r"bad\_model" in automatic
    assert "[link](https://example.com/a_b)" not in automatic

    authored = "*Phase one*\n_Operator-authored_ [runbook](https://example.com) `code`"
    projected = manager._deliver_channel_frame(
        "mybot", 55, "programmable", authored, error="projection failed",
    )
    assert projected["status"] == "ok"
    combined = _current(acct)
    assert automatic in combined
    assert authored in combined
    assert combined.count(authored) == 1
    assert r"\*Phase one\*" not in combined
    assert r"\[runbook\]" not in combined
    assert acct.calls[-1][-1] == {"parse_mode": "Markdown"}


@pytest.mark.parametrize(
    ("filler_units", "expected_units", "accepted"),
    [
        (2772, 3499, True),
        (2773, 3500, True),
        (3273, 3501, False),
    ],
)
def test_both_slot_utf16_budget_rerenders_automatic_and_refuses_only_overflow(
    tmp_path, filler_units, expected_units, accepted,
):
    manager, acct, _service = _manager(tmp_path)
    manager._task_card_event_metadata_snapshot = lambda: None
    event = {
        "type": "diary",
        "api_call_id": "call-escape-heavy",
        "text": "_😀[" * 300,
    }
    projected = manager._project_task_card_event(event)
    assert projected is not None
    assert projected["text"].endswith("…")
    manager._task_card_event_groups = [
        {"api_call_id": "call-escape-heavy", "events": [projected]}
    ]
    assert manager._ensure_task_card_resident("mybot", 55)["status"] == "ok"
    initial = _current(acct)
    initial_automatic = manager._task_card_channels["mybot:55"]["automatic"]
    assert "\\_" in initial_automatic and "\\[" in initial_automatic
    assert "😀" in initial_automatic
    assert initial_automatic.count("• ") == 1

    authored = "*Authored 😀_*\n" + ("x" * filler_units)
    calls_before = len(acct.calls)
    result = manager._deliver_channel_frame(
        "mybot", 55, "programmable", authored, error="projection failed",
    )

    if accepted:
        assert result["status"] == "ok"
        final = _current(acct)
        assert manager._task_card_wire_units(final) == expected_units
        assert final.count(authored) == 1
        assert final.endswith(authored)
        assert final.count("• ") == 1
        assert len(acct.calls) == calls_before + 1
        assert manager._task_card_channels["mybot:55"]["programmable"] == authored
    else:
        assert result["status"] == "error"
        assert result["delivery_error"]["class"] == "wire_budget_exceeded"
        assert expected_units > manager._TASK_CARD_TEXT_LIMIT
        assert len(acct.calls) == calls_before
        assert _current(acct) == initial
        assert manager._task_card_channels["mybot:55"] == {
            "automatic": initial_automatic,
        }
        assert manager._task_card_desired_channels["mybot:55"] == {
            "automatic": initial_automatic,
        }


def test_active_intrinsic_body_projects_onto_existing_resident(tmp_path):
    manager, acct, _service = _manager(tmp_path)
    _auto(manager, reasoning="compiling")
    _write_intrinsic_taskcard(tmp_path, status="active", body="# Task Card\n\n- first\n")

    manager._broadcast_programmable_task_card_file()

    text = _current(acct)
    assert "compiling" in text
    assert "*TASK CARD*" in text
    assert "# Task Card" in text
    assert "- first" in text


def test_projection_is_diff_only_against_last_programmable_frame(tmp_path):
    manager, acct, _service = _manager(tmp_path)
    _auto(manager)
    _write_intrinsic_taskcard(tmp_path, status="active", body="same body\n")

    manager._broadcast_programmable_task_card_file()
    calls_after_first = len(acct.calls)
    manager._broadcast_programmable_task_card_file()

    assert len(acct.calls) == calls_after_first
    assert manager._task_card_channels["mybot:55"]["programmable"] == "same body\n"


def test_active_with_blank_body_is_noop_and_preserves_last_good_projection(tmp_path):
    """Only exact ``inactive`` clears the programmable frame; any other
    non-active-with-valid-body state (blank body while still ``active``, here)
    stays the unchanged preserve-last-good no-op."""
    manager, acct, _service = _manager(tmp_path)
    _auto(manager)
    _write_intrinsic_taskcard(tmp_path, status="active", body="v1\n")
    manager._broadcast_programmable_task_card_file()
    calls_after_good = len(acct.calls)

    _write_intrinsic_taskcard(tmp_path, status="active", body="   ")
    manager._broadcast_programmable_task_card_file()
    assert len(acct.calls) == calls_after_good
    assert manager._task_card_channels["mybot:55"]["programmable"] == "v1\n"


def test_missing_body_after_active_status_is_noop_and_keeps_last_good_projection(tmp_path):
    manager, acct, _service = _manager(tmp_path)
    _auto(manager)
    _write_intrinsic_taskcard(tmp_path, status="active", body="v1\n")
    manager._broadcast_programmable_task_card_file()
    calls_after_good = len(acct.calls)

    _write_intrinsic_taskcard(tmp_path, status="active", body=None)
    manager._broadcast_programmable_task_card_file()

    assert len(acct.calls) == calls_after_good
    assert manager._task_card_channels["mybot:55"]["programmable"] == "v1\n"


def test_existing_automatic_channel_behavior_is_preserved_by_programmable_file_updates(tmp_path):
    manager, acct, _service = _manager(tmp_path)
    drain = _controlled_gate(manager)
    _auto(manager, reasoning="stay put")
    automatic_only = _current(acct)
    _write_intrinsic_taskcard(tmp_path, status="active", body="watch body\n")

    manager._broadcast_programmable_task_card_file()
    assert "stay put" in _current(acct)
    assert "watch body" in _current(acct)

    manager._handle_task_card_update(
        {
            "sub_action": "update",
            "card_message_id": "mybot:55:100",
            "tool": "read",
            "tool_action": "open",
            "reasoning": "next step",
        }
    )
    drain()
    assert "next step" in _current(acct)
    assert "watch body" in _current(acct)
    assert automatic_only != _current(acct)


def test_inactive_clears_programmable_frame_but_preserves_resident_and_automatic(tmp_path):
    """`stop`/`remove`-style inactive excludes only the programmable frame.

    Telegram owns the resident message and its automatic content: inactive
    must update the same resident (never delete/send-new), drop the
    programmable ``*TASK CARD*`` section while keeping the automatic content
    intact, and never touch automatic updates going forward. Repeated
    inactive handling must be idempotent, and it must never delete the local
    body file either.
    """
    manager, acct, _service = _manager(tmp_path)
    drain = _controlled_gate(manager)
    _auto(manager, reasoning="stay put")
    _write_intrinsic_taskcard(tmp_path, status="active", body="v1\n")
    manager._broadcast_programmable_task_card_file()

    resident_before = acct.get_task_card(55)
    assert resident_before is not None
    assert "v1" in _current(acct)
    assert "*TASK CARD*" in _current(acct)
    assert (tmp_path / "taskcard" / "taskcard.md").exists()

    # `stop` writes inactive but leaves the last body on disk (possibly stale).
    calls_before_inactive = list(acct.calls)
    _write_intrinsic_taskcard(tmp_path, status="inactive", body="v2 (must not render)\n")
    manager._broadcast_programmable_task_card_file()
    drain()

    new_calls = acct.calls[len(calls_before_inactive):]
    assert not any(call[0] in ("send", "delete") for call in new_calls)
    assert any(call[0] == "edit" for call in new_calls)  # resident updated in place
    assert acct.get_task_card(55) == resident_before  # same resident, not a new/deleted one
    assert 55 in acct.list_task_card_chats()
    assert (tmp_path / "taskcard" / "taskcard.md").exists()  # local body never deleted
    text = _current(acct)
    assert "stay put" in text  # Telegram-owned automatic content preserved
    assert "v1" not in text and "v2" not in text  # programmable frame excluded
    assert "*TASK CARD*" not in text
    assert manager._task_card_channels["mybot:55"].get("programmable") is None

    # Repeated inactive handling (e.g. every 1s poll tick) is idempotent: no
    # further transport calls once the programmable frame is already cleared.
    calls_after_clear = list(acct.calls)
    manager._broadcast_programmable_task_card_file()
    manager._broadcast_programmable_task_card_file()
    assert acct.calls == calls_after_clear

    # Automatic updates keep flowing normally while inactive.
    manager._handle_task_card_update(
        {
            "sub_action": "update",
            "card_message_id": resident_before,
            "tool": "read",
            "tool_action": "open",
            "reasoning": "still going",
        }
    )
    drain()
    assert "still going" in _current(acct)
    assert acct.get_task_card(55) == resident_before

    # `remove` additionally deletes the local body once inactive is durable;
    # Telegram must remain idempotent rather than deleting/hiding anything.
    (tmp_path / "taskcard" / "taskcard.md").unlink()
    calls_before_remove = list(acct.calls)
    manager._broadcast_programmable_task_card_file()
    assert acct.calls == calls_before_remove
    assert acct.get_task_card(55) == resident_before


def test_new_active_watch_after_inactive_renders_without_stale_state_corruption(tmp_path):
    """A fresh watch after `stop`/`remove` must render cleanly, not resurface old content."""
    manager, acct, _service = _manager(tmp_path)
    drain = _controlled_gate(manager)
    _auto(manager)
    _write_intrinsic_taskcard(tmp_path, status="active", body="v1\n")
    manager._broadcast_programmable_task_card_file()
    resident_id = acct.get_task_card(55)
    assert "v1" in _current(acct)

    # Old watch retires and its body is removed, exactly as `task_card.remove` does.
    _write_intrinsic_taskcard(tmp_path, status="inactive", body=None)
    manager._broadcast_programmable_task_card_file()
    drain()
    assert manager._task_card_channels["mybot:55"].get("programmable") is None
    assert "v1" not in _current(acct)

    # A brand-new watch starts: body written first, then status flips to active.
    _write_intrinsic_taskcard(tmp_path, status="active", body="v2 fresh\n")
    manager._broadcast_programmable_task_card_file()
    drain()

    text = _current(acct)
    assert "v2 fresh" in text
    assert "v1" not in text
    assert manager._task_card_channels["mybot:55"]["programmable"] == "v2 fresh\n"
    assert acct.get_task_card(55) == resident_id
    assert acct.calls[-1][0] == "edit"
