"""Tests for the resident Task Card's two-slot composition (Jason #7258/#7259).

One resident message per account+chat carries two independent slots — the
``automatic`` tool-row channel and the ``programmable`` renderer channel.
Updating one slot preserves the other; programmable ``finalize`` clears only its
slot and leaves the automatic channel and the message intact. The manager
renders only validated card objects, redacting secrets.
"""

from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import notification_store_for


class FakeAccount:
    alias = "mybot"

    def __init__(self):
        self.calls: list = []
        self.sent: dict[int, str] = {}
        self._resident: dict[int, str] = {}  # chat_id -> compound id
        self.fail_edit = False  # raise on edit -> update_progress_message False
        self.fail_send = False  # raise on send -> send_progress_message None

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        if self.fail_send:
            raise RuntimeError("simulated send failure")
        msg_id = len(self.calls) + 100
        self.sent[msg_id] = text
        self.calls.append(("send", chat_id, text))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        if self.fail_edit:
            raise RuntimeError("simulated edit failure")
        self.sent[message_id] = text
        self.calls.append(("edit", chat_id, message_id, text))
        return {"ok": True}

    # Resident-card persistence (in-memory) so both slots share one message.
    def get_task_card(self, chat_id):
        return self._resident.get(chat_id)

    def set_task_card(self, chat_id, compound_id):
        self._resident[chat_id] = compound_id


class FakeService:
    def __init__(self):
        self.default_account = FakeAccount()

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account


def _manager(tmp_path):
    service = FakeService()
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=notification_store_for(Path(tmp_path)),
    )
    return manager, service.default_account


def _prog(manager, sub_action, card=None):
    args = {
        "sub_action": sub_action,
        "channel": "programmable",
        "account": "mybot",
        "chat_id": 55,
    }
    if card is not None:
        args["card"] = card
    return manager._handle_task_card_update(args)


def _auto(manager, tool="bash", reasoning="build"):
    return manager._handle_task_card_update(
        {
            "sub_action": "create",
            "account": "mybot",
            "chat_id": 55,
            "tool": tool,
            "tool_action": "run",
            "reasoning": reasoning,
        }
    )


def _current(account: FakeAccount) -> str:
    return account.sent[max(account.sent)]


# -- programmable slot alone -----------------------------------------------


def test_programmable_first_frame_sends_and_persists_resident(tmp_path):
    manager, acct = _manager(tmp_path)
    result = _prog(manager, "create", {"title": "Watch", "lines": ["x"]})
    assert result["status"] == "ok"
    assert acct.calls[0][0] == "send"  # no resident yet -> first send
    text = _current(acct)
    assert "— WATCH —" in text
    assert "Watch" in text and "• x" in text
    # Resident persisted: a second programmable update edits, never re-sends.
    _prog(manager, "update", {"lines": ["y"]})
    assert acct.calls[-1][0] == "edit"


# -- two-slot composition + update isolation -------------------------------


def test_automatic_and_programmable_compose_into_one_message(tmp_path):
    manager, acct = _manager(tmp_path)
    _auto(manager, reasoning="compiling")
    _prog(manager, "update", {"lines": ["watch line"]})
    text = _current(acct)
    assert "compiling" in text  # automatic slot present
    assert "— WATCH —" in text  # programmable slot present
    assert "• watch line" in text
    # Only one resident message was ever sent.
    assert sum(1 for c in acct.calls if c[0] == "send") == 1


def test_automatic_update_preserves_programmable_slot(tmp_path):
    manager, acct = _manager(tmp_path)
    _auto(manager, reasoning="first")
    _prog(manager, "update", {"lines": ["live watch"]})
    # A fresh automatic turn edits its own slot; the programmable slot survives.
    manager._handle_task_card_update(
        {
            "sub_action": "update",
            "card_message_id": "mybot:55:100",
            "tool": "read",
            "tool_action": "open",
            "reasoning": "second",
        }
    )
    text = _current(acct)
    assert "second" in text
    assert "• live watch" in text  # programmable untouched by automatic edit


def test_programmable_update_preserves_automatic_slot(tmp_path):
    manager, acct = _manager(tmp_path)
    _auto(manager, reasoning="stay put")
    _prog(manager, "update", {"lines": ["v1"]})
    _prog(manager, "update", {"lines": ["v2"]})
    text = _current(acct)
    assert "stay put" in text  # automatic untouched by programmable edit
    assert "• v2" in text
    assert "• v1" not in text  # programmable slot replaced, not appended


def test_programmable_finalize_clears_only_its_slot(tmp_path):
    manager, acct = _manager(tmp_path)
    _auto(manager, reasoning="keep me")
    _prog(manager, "update", {"lines": ["temporary"]})
    assert "— WATCH —" in _current(acct)
    result = _prog(manager, "finalize")
    assert result["status"] == "ok"
    text = _current(acct)
    assert "— WATCH —" not in text  # programmable slot gone
    assert "temporary" not in text
    assert "keep me" in text  # automatic slot — and the message — remain


# -- validation + redaction ------------------------------------------------


def test_unknown_channel_is_rejected(tmp_path):
    manager, _ = _manager(tmp_path)
    result = manager._handle_task_card_update(
        {"sub_action": "update", "channel": "bogus", "account": "mybot", "chat_id": 55}
    )
    assert result["status"] == "error"


def test_programmable_card_must_be_object(tmp_path):
    manager, _ = _manager(tmp_path)
    # card omitted -> update with no card object is an error, not a crash.
    assert _prog(manager, "update")["status"] == "error"
    result = manager._handle_task_card_update(
        {
            "sub_action": "update",
            "channel": "programmable",
            "account": "mybot",
            "chat_id": 55,
            "card": "not-an-object",
        }
    )
    assert result["status"] == "error"


def test_programmable_render_redacts_secrets(tmp_path):
    manager, acct = _manager(tmp_path)
    _prog(manager, "create", {"lines": ["deploy key AKIAIOSFODNN7EXAMPLE now"]})
    text = _current(acct)
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "REDACTED" in text


# -- state ordering: commit channel frame only after successful transport ---


def test_failed_edit_does_not_poison_channel_state(tmp_path):
    """A programmable update whose edit AND recovery send both fail must leave
    the committed programmable frame at the last successfully delivered value —
    and a later automatic update must not resurrect the unsent frame."""
    manager, acct = _manager(tmp_path)
    _auto(manager, reasoning="base")
    _prog(manager, "update", {"lines": ["v1"]})  # delivered + committed
    assert "• v1" in _current(acct)
    committed = manager._task_card_channels["mybot:55"]["programmable"]

    # Edit fails, and the recovery replacement send also fails: total delivery
    # failure, so the proposed frame must NOT be committed.
    acct.fail_edit = True
    acct.fail_send = True
    result = _prog(manager, "update", {"lines": ["UNSENT"]})
    assert result["status"] == "error"
    assert manager._task_card_channels["mybot:55"]["programmable"] == committed
    assert "UNSENT" not in committed

    # A later successful automatic update composes only the last delivered
    # programmable frame — the unsent "UNSENT" frame can never be resurrected.
    acct.fail_edit = False
    acct.fail_send = False
    manager._handle_task_card_update(
        {
            "sub_action": "update",
            "card_message_id": manager._get_resident_task_card("mybot", 55),
            "tool": "read",
            "tool_action": "open",
            "reasoning": "later",
        }
    )
    text = _current(acct)
    assert "later" in text
    assert "• v1" in text
    assert "UNSENT" not in text


def test_failed_automatic_edit_does_not_poison_channel_state(tmp_path):
    """Same discipline on the automatic slot: a failed create edit+send leaves no
    committed automatic frame, so a later programmable compose stays clean."""
    manager, acct = _manager(tmp_path)
    _auto(manager, reasoning="delivered")  # resident created + committed
    acct.fail_edit = True
    acct.fail_send = True
    res = _auto(manager, reasoning="POISON")  # edit + recovery send both fail
    assert res["status"] == "error"
    assert manager._task_card_channels["mybot:55"]["automatic"] != ""
    assert "POISON" not in manager._task_card_channels["mybot:55"]["automatic"]
    acct.fail_edit = False
    acct.fail_send = False
    _prog(manager, "update", {"lines": ["w"]})  # succeeds, composes clean
    text = _current(acct)
    assert "delivered" in text and "• w" in text
    assert "POISON" not in text
