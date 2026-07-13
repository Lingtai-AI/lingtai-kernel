"""Task Card resides as the chat's last message (Jason #5272/#5273/#5275).

Behavior B: when the resident Task Card is already the chat's last message the
addon keeps editing that exact message in place (preserves #891). When a newer
chat message exists below the resident — an inbound user message or an ordinary
outbound bot/final reply — the addon rotates: it sends a fresh Task Card so the
card becomes the last message, then deletes only the exact old resident card.

Sequencing is create-new-first, delete-old-after: the addon never destroys the
only resident card before a replacement is confirmed sent, so a partial failure
can never leave the chat with zero cards. Unknown/malformed latest-message state
and transient failures never authorize a delete.
"""

from __future__ import annotations

import threading
from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class FakeAccount:
    """Mirrors the real account's high-water-mark contract.

    ``get_last_message_id`` returns the highest Telegram message_id observed in a
    chat. Every outbound ``send_message`` bumps it (a new bottom message), while
    ``edit_message``/``delete_message`` do not. ``observe_incoming`` simulates a
    user message arriving from the poll loop.
    """

    alias = "mybot"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.messages: dict[int, str] = {}
        self.resident: dict[int, str] = {}
        self.next_id = 100
        self.edit_error: Exception | None = None
        self.send_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.persist_error: Exception | None = None
        self._last_message_ids: dict[int, int] = {}
        self.report_unknown_latest = False

    # -- high-water mark ---------------------------------------------------
    def _note(self, chat_id, message_id):
        prev = self._last_message_ids.get(chat_id, 0)
        if message_id > prev:
            self._last_message_ids[chat_id] = message_id

    def observe_incoming(self, chat_id, message_id):
        self._note(chat_id, message_id)

    def get_last_message_id(self, chat_id):
        if self.report_unknown_latest:
            return None
        return self._last_message_ids.get(chat_id)

    # -- transport ---------------------------------------------------------
    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        if self.send_error is not None:
            raise self.send_error
        # Telegram message ids are monotonic per chat: a new send always exceeds
        # the chat's current latest id.
        message_id = max(self.next_id, self._last_message_ids.get(chat_id, 0) + 1)
        self.next_id = message_id + 1
        self.messages[message_id] = text
        self.calls.append(("send", chat_id, message_id, text))
        self._note(chat_id, message_id)
        return {"message_id": message_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.calls.append(("edit", chat_id, message_id, text))
        if self.edit_error is not None:
            raise self.edit_error
        self.messages[message_id] = text
        return {"ok": True}

    def delete_message(self, chat_id, message_id):
        self.calls.append(("delete", chat_id, message_id))
        if self.delete_error is not None:
            raise self.delete_error
        self.messages.pop(message_id, None)
        return {"ok": True}

    def get_task_card(self, chat_id):
        return self.resident.get(chat_id)

    def set_task_card(self, chat_id, compound_id):
        self.resident[chat_id] = compound_id
        if self.persist_error is not None:
            raise self.persist_error


class FakeService:
    def __init__(self, account: FakeAccount, taskcard: bool | None = None) -> None:
        self.default_account = account
        self._taskcard = taskcard

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account

    # Only defined when a test wants an explicit setting; default-absent means
    # the manager falls back to enabled-by-default (#892 semantics).
    def taskcard_enabled(self):  # noqa: D401 - simple accessor
        return True if self._taskcard is None else self._taskcard


def _manager(tmp_path, taskcard=None):
    account = FakeAccount()
    manager = TelegramManager(
        FakeService(account, taskcard=taskcard),
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, account


def _automatic(manager, sub_action, *, chat_id=55, card_message_id=None,
               reasoning="work", done=False):
    args = {
        "sub_action": sub_action,
        "account": "mybot",
        "chat_id": chat_id,
        "rows": [{
            "tool": "bash",
            "tool_action": "run",
            "reasoning": reasoning,
            "elapsed_s": 1,
            "done": done,
        }],
    }
    if card_message_id is not None:
        args["card_message_id"] = card_message_id
    return manager._handle_task_card_update(args)


def _calls(account, kind):
    return [c for c in account.calls if c[0] == kind]


# ---------------------------------------------------------------------------
# 1. resident id == latest -> edit in place, no delete/send churn (#891)
# ---------------------------------------------------------------------------
def test_resident_is_latest_edits_in_place(tmp_path):
    manager, account = _manager(tmp_path)
    created = _automatic(manager, "create", reasoning="first")
    resident_id = created["message_id"]  # mybot:55:100

    # No newer message: the card is still the last message in the chat.
    updated = _automatic(
        manager, "update", card_message_id=resident_id, reasoning="second")

    assert updated == {"status": "ok", "message_id": resident_id}
    assert len(_calls(account, "send")) == 1
    assert not _calls(account, "delete")
    assert account.get_task_card(55) == resident_id
    assert "second" in account.messages[100]


# ---------------------------------------------------------------------------
# 2. newer user message -> rotate to a new last card, delete only the old one
# ---------------------------------------------------------------------------
def test_newer_user_message_rotates_and_deletes_only_old(tmp_path):
    manager, account = _manager(tmp_path)
    created = _automatic(manager, "create", reasoning="first")
    assert created["message_id"] == "mybot:55:100"

    # A user message arrives below the resident card.
    account.observe_incoming(55, 200)

    rotated = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="second")

    # New card sent (id 201, since 200 was only observed, not sent), old deleted.
    assert rotated["status"] == "ok"
    assert rotated["message_id"] == "mybot:55:201"
    assert _calls(account, "send") and account.calls[-1][0] == "delete"
    assert account.calls[-1] == ("delete", 55, 100)
    # Exactly one delete, of exactly the old resident.
    assert _calls(account, "delete") == [("delete", 55, 100)]
    assert account.get_task_card(55) == "mybot:55:201"
    # The new card is now the latest message.
    assert account.get_last_message_id(55) == 201


# ---------------------------------------------------------------------------
# 3. newer ordinary bot/final reply -> same rotation invariant
# ---------------------------------------------------------------------------
def test_newer_bot_final_reply_rotates_on_finalize(tmp_path):
    manager, account = _manager(tmp_path)
    created = _automatic(manager, "create", reasoning="running")
    assert created["message_id"] == "mybot:55:100"

    # The agent sends its durable final reply through the normal send path,
    # which pushes the resident card off the bottom of the chat.
    account.send_message(55, "here is your answer")  # id 101

    final = _automatic(
        manager, "finalize", card_message_id="mybot:55:100",
        reasoning="running", done=True)

    assert final["status"] == "ok"
    assert final["message_id"] == "mybot:55:102"
    assert _calls(account, "delete") == [("delete", 55, 100)]
    assert account.get_task_card(55) == "mybot:55:102"


# ---------------------------------------------------------------------------
# 4. create-new failure -> do NOT delete old card
# ---------------------------------------------------------------------------
def test_create_new_failure_preserves_old_card(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.observe_incoming(55, 200)

    account.send_error = RuntimeError("connection reset")
    result = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="second")

    assert result["status"] == "error"
    # The only resident card is never destroyed when the replacement send fails.
    assert not _calls(account, "delete")
    assert account.get_task_card(55) == "mybot:55:100"


# ---------------------------------------------------------------------------
# 5. delete-old failure after successful creation -> fail loud, no rollback
# ---------------------------------------------------------------------------
def test_delete_old_failure_after_creation_fails_loud(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.observe_incoming(55, 200)

    account.delete_error = RuntimeError("bad request: message can't be deleted")
    result = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="second")

    # The new latest card was delivered and tracked — do NOT roll back to a card
    # that is no longer the last message — but the stale-delete failure is loud.
    assert result["status"] == "ok"
    assert result["message_id"] == "mybot:55:201"
    assert result.get("stale_delete_failed") is True
    assert account.get_task_card(55) == "mybot:55:201"
    # The delete of exactly the old resident was attempted.
    assert ("delete", 55, 100) in account.calls


# ---------------------------------------------------------------------------
# 6. unknown latest-message state -> conservative, no unauthorized deletion
# ---------------------------------------------------------------------------
def test_unknown_latest_message_state_is_conservative(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.report_unknown_latest = True

    result = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="second")

    assert result == {"status": "ok", "message_id": "mybot:55:100"}
    assert not _calls(account, "delete")
    assert len(_calls(account, "send")) == 1
    assert account.get_task_card(55) == "mybot:55:100"


# ---------------------------------------------------------------------------
# 7. `message is not modified` -> keep resident id, no replace/delete
# ---------------------------------------------------------------------------
def test_message_not_modified_keeps_resident(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="steady")  # mybot:55:100

    account.edit_error = RuntimeError(
        "Telegram API error: Bad Request: message is not modified: specified new "
        "message content and reply markup are exactly the same as the current content"
    )
    result = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="steady")

    assert result == {"status": "ok", "message_id": "mybot:55:100"}
    assert not _calls(account, "delete")
    assert len(_calls(account, "send")) == 1


# ---------------------------------------------------------------------------
# 8. taskcard off / suppressed -> no create/edit/delete side effects
# ---------------------------------------------------------------------------
def test_taskcard_off_no_side_effects_even_when_superseded(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.observe_incoming(55, 200)

    # Now disable delivery and re-issue an update while the card is superseded.
    manager._service._taskcard = False
    result = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="second")

    assert result == {"status": "ok", "suppressed": True, "taskcard": False}
    # No transport past the first create.
    assert len(_calls(account, "send")) == 1
    assert not _calls(account, "delete")
    assert not [c for c in account.calls if c[0] == "edit"]


# ---------------------------------------------------------------------------
# 9. cross-chat isolation: a newer message in one chat never rotates another
# ---------------------------------------------------------------------------
def test_cross_chat_isolation(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", chat_id=55, reasoning="a")   # mybot:55:100
    _automatic(manager, "create", chat_id=77, reasoning="b")   # mybot:77:101

    # A newer message only in chat 55.
    account.observe_incoming(55, 300)

    # Chat 77's card is still latest — edit in place, no rotation/delete.
    updated_77 = _automatic(
        manager, "update", chat_id=77, card_message_id="mybot:77:101",
        reasoning="b2")
    assert updated_77 == {"status": "ok", "message_id": "mybot:77:101"}
    assert not [c for c in account.calls if c[0] == "delete"]

    # Chat 55's card rotates.
    updated_55 = _automatic(
        manager, "update", chat_id=55, card_message_id="mybot:55:100",
        reasoning="a2")
    assert updated_55["message_id"] == "mybot:55:301"
    assert ("delete", 55, 100) in account.calls
    assert ("delete", 77, 101) not in account.calls


# ---------------------------------------------------------------------------
# 10. cross-bound resident id -> never edit/delete another account or chat
# ---------------------------------------------------------------------------
def test_cross_bound_resident_id_never_touches_other_channel(tmp_path):
    for sub_action in ("update", "finalize"):
        for resident_id in ("otherbot:55:100", "mybot:77:100"):
            manager, account = _manager(tmp_path)
            _automatic(manager, "create", reasoning="first")  # mybot:55:100
            account.observe_incoming(55, 200)
            account.resident[55] = resident_id

            result = _automatic(
                manager, sub_action, card_message_id=resident_id, reasoning="second")

            assert result["status"] == "error"
            assert not _calls(account, "delete")
            assert not _calls(account, "edit")
            assert len(_calls(account, "send")) == 1


# ---------------------------------------------------------------------------
# 11. malformed resident id -> conservative, never an unauthorized delete
# ---------------------------------------------------------------------------
def test_malformed_resident_id_never_deletes(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.observe_incoming(55, 200)

    # A malformed persisted resident id cannot be proven superseded, so the
    # addon must not rotate/delete on it.
    account.resident[55] = "not-a-compound-id"
    result = _automatic(
        manager, "update", card_message_id="not-a-compound-id", reasoning="second")

    assert result["status"] == "error"
    assert not _calls(account, "delete")


# ---------------------------------------------------------------------------
# 12. resident persistence must be acknowledged before old-card deletion
# ---------------------------------------------------------------------------
def test_persistence_failure_never_deletes_acknowledged_old_resident(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.observe_incoming(55, 200)
    account.persist_error = RuntimeError("injected state write failure")

    result = _automatic(
        manager, "update", card_message_id="mybot:55:100", reasoning="second")

    assert result == {
        "status": "ok",
        "message_id": "mybot:55:201",
        "resident_persist_failed": True,
    }
    assert account.resident[55] == "mybot:55:201"  # in-memory current
    assert not _calls(account, "delete")            # old visible card retained
    assert len(_calls(account, "send")) == 2


# ---------------------------------------------------------------------------
# 13. automatic/programmatic delivery shares one per-route transaction lock
# ---------------------------------------------------------------------------
def test_concurrent_channels_serialize_one_rotation(tmp_path):
    manager, account = _manager(tmp_path)
    _automatic(manager, "create", reasoning="first")  # mybot:55:100
    account.observe_incoming(55, 200)

    class InstrumentedLock:
        def __init__(self):
            self.arrived = threading.Barrier(2)
            self.gate = threading.Lock()
            self.state = threading.Lock()
            self.active = 0
            self.max_active = 0

        def __enter__(self):
            self.arrived.wait(timeout=2)
            self.gate.acquire()
            with self.state:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            return self

        def __exit__(self, *_):
            with self.state:
                self.active -= 1
            self.gate.release()

    observed = InstrumentedLock()
    manager._task_card_delivery_locks["mybot:55"] = observed
    results = []

    def automatic():
        results.append(_automatic(
            manager, "update", card_message_id="mybot:55:100", reasoning="auto"))

    def programmable():
        results.append(manager._handle_task_card_update({
            "sub_action": "update",
            "channel": "programmable",
            "account": "mybot",
            "chat_id": 55,
            "card": {"title": "Watch", "lines": ["steady"]},
        }))

    threads = [threading.Thread(target=automatic), threading.Thread(target=programmable)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

    assert observed.max_active == 1
    assert len(_calls(account, "send")) == 2  # initial + exactly one rotation
    assert _calls(account, "delete") == [("delete", 55, 100)]
    assert account.resident[55] == "mybot:55:201"
    assert all(result["message_id"] == "mybot:55:201" for result in results)
