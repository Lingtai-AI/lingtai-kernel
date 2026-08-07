"""Tests for Telegram's read-only projection of the intrinsic Task Card files."""

from __future__ import annotations

from pathlib import Path

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
        self.calls.append(("send", chat_id, text))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.sent[message_id] = text
        self.calls.append(("edit", chat_id, message_id, text))
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


def test_active_intrinsic_body_projects_onto_existing_resident(tmp_path):
    manager, acct, _service = _manager(tmp_path)
    _auto(manager, reasoning="compiling")
    _write_intrinsic_taskcard(tmp_path, status="active", body="# Task Card\n\n- first\n")

    manager._broadcast_programmable_task_card_file()

    text = _current(acct)
    assert "compiling" in text
    assert "— TASK CARD —" in text
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
    assert "next step" in _current(acct)
    assert "watch body" in _current(acct)
    assert automatic_only != _current(acct)


def test_inactive_clears_programmable_frame_but_preserves_resident_and_automatic(tmp_path):
    """`stop`/`remove`-style inactive excludes only the programmable frame.

    Telegram owns the resident message and its automatic content: inactive
    must update the same resident (never delete/send-new), drop the
    programmable ``— TASK CARD —`` section while keeping the automatic content
    intact, and never touch automatic updates going forward. Repeated
    inactive handling must be idempotent, and it must never delete the local
    body file either.
    """
    manager, acct, _service = _manager(tmp_path)
    _auto(manager, reasoning="stay put")
    _write_intrinsic_taskcard(tmp_path, status="active", body="v1\n")
    manager._broadcast_programmable_task_card_file()

    resident_before = acct.get_task_card(55)
    assert resident_before is not None
    assert "v1" in _current(acct)
    assert "— TASK CARD —" in _current(acct)
    assert (tmp_path / "taskcard" / "taskcard.md").exists()

    # `stop` writes inactive but leaves the last body on disk (possibly stale).
    calls_before_inactive = list(acct.calls)
    _write_intrinsic_taskcard(tmp_path, status="inactive", body="v2 (must not render)\n")
    manager._broadcast_programmable_task_card_file()

    new_calls = acct.calls[len(calls_before_inactive):]
    assert not any(call[0] in ("send", "delete") for call in new_calls)
    assert any(call[0] == "edit" for call in new_calls)  # resident updated in place
    assert acct.get_task_card(55) == resident_before  # same resident, not a new/deleted one
    assert 55 in acct.list_task_card_chats()
    assert (tmp_path / "taskcard" / "taskcard.md").exists()  # local body never deleted
    text = _current(acct)
    assert "stay put" in text  # Telegram-owned automatic content preserved
    assert "v1" not in text and "v2" not in text  # programmable frame excluded
    assert "— TASK CARD —" not in text
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
    _auto(manager)
    _write_intrinsic_taskcard(tmp_path, status="active", body="v1\n")
    manager._broadcast_programmable_task_card_file()
    resident_id = acct.get_task_card(55)
    assert "v1" in _current(acct)

    # Old watch retires and its body is removed, exactly as `task_card.remove` does.
    _write_intrinsic_taskcard(tmp_path, status="inactive", body=None)
    manager._broadcast_programmable_task_card_file()
    assert manager._task_card_channels["mybot:55"].get("programmable") is None
    assert "v1" not in _current(acct)

    # A brand-new watch starts: body written first, then status flips to active.
    _write_intrinsic_taskcard(tmp_path, status="active", body="v2 fresh\n")
    manager._broadcast_programmable_task_card_file()

    text = _current(acct)
    assert "v2 fresh" in text
    assert "v1" not in text
    assert manager._task_card_channels["mybot:55"]["programmable"] == "v2 fresh\n"
    assert acct.get_task_card(55) == resident_id
    assert acct.calls[-1][0] == "edit"
