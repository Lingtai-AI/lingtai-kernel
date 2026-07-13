"""Singleton resident Task Card at the manager layer.

Jason #6665/#6667 established one resident card per account+chat.  Jason
#6894/#6899 (this change) makes ``create`` **update-first**: a normal repeated
automatic ``create`` for a chat that already has a valid persisted resident edits
that card in place and returns the SAME compound id — it sends nothing new and
deletes nothing, so the card never flickers as later BaseAgent tool batches call
``create`` again.  A replacement send/delete happens only as fail-open recovery:
when there is no persisted resident, or the persisted message genuinely cannot be
edited (stale/deleted).  In recovery the discipline is unchanged — send the
replacement first, persist the new id, then best-effort delete the exact stale
id; a failed replacement send preserves the old card and its id and deletes
nothing, and a delete failure never rolls the new id back.
"""

from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class FakeAccount:
    """Mimics the real TelegramAccount singleton API + send/edit/delete."""

    def __init__(
        self, alias="mybot", *, fail_send=False, fail_delete=False, fail_edit=False,
    ):
        self.alias = alias
        self.calls: list = []
        self._task_cards: dict[str, str] = {}
        self._next_id = 100
        self._fail_send = fail_send
        self._fail_delete = fail_delete
        self._fail_edit = fail_edit

    # -- messaging --
    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        if self._fail_send:
            raise RuntimeError("send failed")
        msg_id = self._next_id
        self._next_id += 1
        self.calls.append(("send_message", chat_id, msg_id, text))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.calls.append(("edit_message", chat_id, message_id, text))
        if self._fail_edit:
            raise RuntimeError(
                "Telegram API error: Bad Request: message to edit not found"
            )
        return {"ok": True}

    def delete_message(self, chat_id, message_id):
        self.calls.append(("delete_message", chat_id, message_id))
        if self._fail_delete:
            raise RuntimeError("delete failed")
        return {"ok": True}

    # -- singleton state (same signatures as TelegramAccount) --
    def get_task_card(self, chat_id):
        return self._task_cards.get(str(chat_id))

    def set_task_card(self, chat_id, compound_id):
        self._task_cards[str(chat_id)] = compound_id

    def clear_task_card(self, chat_id):
        self._task_cards.pop(str(chat_id), None)


class FakeService:
    def __init__(self, accounts):
        self._accounts = {a.alias: a for a in accounts}
        self.default_account = accounts[0]

    def get_account(self, alias):
        return self._accounts[alias]


def _manager(tmp_path, *accounts):
    if not accounts:
        accounts = (FakeAccount(),)
    service = FakeService(list(accounts))
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, service


def _create(manager, account="mybot", chat_id=999, reasoning="do a thing"):
    return manager._handle_task_card_update({
        "sub_action": "create",
        "account": account,
        "chat_id": chat_id,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": reasoning,
    })


def _deletes(account):
    return [c for c in account.calls if c[0] == "delete_message"]


def _sends(account):
    return [c for c in account.calls if c[0] == "send_message"]


def _edits(account):
    return [c for c in account.calls if c[0] == "edit_message"]


# ---------------------------------------------------------------------------
# First create (no resident) sends once, stores the id, deletes nothing
# ---------------------------------------------------------------------------

def test_first_create_stores_id_and_deletes_nothing(tmp_path):
    manager, service = _manager(tmp_path)
    acct = service.default_account

    r = _create(manager)
    assert r["status"] == "ok"
    assert len(_sends(acct)) == 1
    assert not _deletes(acct)
    assert not _edits(acct)
    assert acct.get_task_card(999) == r["message_id"]


# ---------------------------------------------------------------------------
# Repeated create with a valid resident EDITS it in place: same id, no
# new send, no delete — the card never flickers (Jason #6894/#6899).
# ---------------------------------------------------------------------------

def test_second_create_edits_resident_in_place_no_send_no_delete(tmp_path):
    manager, service = _manager(tmp_path)
    acct = service.default_account

    r1 = _create(manager, reasoning="first")
    first_id = r1["message_id"]

    r2 = _create(manager, reasoning="second")
    # Same resident id is returned and kept — no replacement card.
    assert r2["status"] == "ok"
    assert r2["message_id"] == first_id
    assert acct.get_task_card(999) == first_id

    # Exactly one send (the first create); the second create edits in place.
    assert len(_sends(acct)) == 1
    assert not _deletes(acct)

    # The resident message id was edited with the second render.
    edits = _edits(acct)
    assert edits, "repeated create must edit the resident card"
    _, edit_chat, edit_msg, edit_text = edits[-1]
    assert edit_chat == 999
    assert edit_msg == int(first_id.split(":")[2])
    assert "second" in edit_text


def test_many_repeated_creates_never_send_or_delete_again(tmp_path):
    """The steady state: many automatic creates in a chat with a resident only
    ever edit — one send total, zero deletes, id stable throughout."""
    manager, service = _manager(tmp_path)
    acct = service.default_account

    r = _create(manager, reasoning="r0")
    resident = r["message_id"]
    for i in range(1, 6):
        ri = _create(manager, reasoning=f"r{i}")
        assert ri["message_id"] == resident

    assert len(_sends(acct)) == 1
    assert not _deletes(acct)
    assert len(_edits(acct)) == 5  # one edit per repeated create
    assert acct.get_task_card(999) == resident


# ---------------------------------------------------------------------------
# Create edit failure recovers: send-new, persist-new, delete-stale
# ---------------------------------------------------------------------------

def test_create_edit_failure_recovers_by_send_new_persist_new_delete_stale(tmp_path):
    acct = FakeAccount(fail_edit=True)
    manager, service = _manager(tmp_path, acct)

    # Seed a resident id whose message can no longer be edited (stale/deleted).
    acct.set_task_card(999, "mybot:999:50")

    r = _create(manager, reasoning="recover")
    assert r["status"] == "ok"
    new_id = r["message_id"]
    assert new_id != "mybot:999:50"

    # Edit was attempted first, then the replacement send, then the stale delete.
    kinds = [c[0] for c in acct.calls]
    assert kinds == ["edit_message", "send_message", "delete_message"]

    # New id is persisted as the resident; the exact stale id was retired.
    assert acct.get_task_card(999) == new_id
    _, del_chat, del_msg = _deletes(acct)[0]
    assert del_chat == 999
    assert del_msg == 50


# ---------------------------------------------------------------------------
# Recovery replacement send failure preserves the stale resident, no delete
# ---------------------------------------------------------------------------

def test_recovery_send_failure_preserves_stale_resident_and_no_delete(tmp_path):
    acct = FakeAccount(fail_edit=True, fail_send=True)
    manager, service = _manager(tmp_path, acct)

    acct.set_task_card(999, "mybot:999:50")

    r = _create(manager, reasoning="recover")
    assert r["status"] == "error"

    # The stale resident id and its (unusable) card survive; nothing deleted.
    assert acct.get_task_card(999) == "mybot:999:50"
    assert not _deletes(acct)


# ---------------------------------------------------------------------------
# Recovery delete failure leaves the new card authoritative (fail-open)
# ---------------------------------------------------------------------------

def test_recovery_delete_failure_is_fail_open(tmp_path):
    acct = FakeAccount(fail_edit=True, fail_delete=True)
    manager, service = _manager(tmp_path, acct)

    acct.set_task_card(999, "mybot:999:50")

    r = _create(manager, reasoning="recover")
    # Even though the stale delete raised, recovery succeeded and the new id is
    # authoritative — state is never rolled back.
    assert r["status"] == "ok"
    assert acct.get_task_card(999) == r["message_id"]
    assert len(_deletes(acct)) == 1  # the delete was attempted


# ---------------------------------------------------------------------------
# First create still sends when there is no persisted resident to edit
# ---------------------------------------------------------------------------

def test_create_with_no_resident_sends_first_card(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)

    r = _create(manager, reasoning="fresh")
    assert r["status"] == "ok"
    assert len(_sends(acct)) == 1
    assert not _edits(acct)
    assert not _deletes(acct)
    assert acct.get_task_card(999) == r["message_id"]


# ---------------------------------------------------------------------------
# Cross-chat and cross-account isolation
# ---------------------------------------------------------------------------

def test_different_chats_do_not_delete_each_other(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)

    _create(manager, chat_id=111, reasoning="chatA")
    _create(manager, chat_id=222, reasoning="chatB")

    # Two independent residents; neither deletes the other.
    assert acct.get_task_card(111) is not None
    assert acct.get_task_card(222) is not None
    assert not _deletes(acct)


def test_different_accounts_do_not_delete_each_other(tmp_path):
    acct_a = FakeAccount(alias="bot_a")
    acct_b = FakeAccount(alias="bot_b")
    manager, service = _manager(tmp_path, acct_a, acct_b)

    _create(manager, account="bot_a", chat_id=999, reasoning="A")
    _create(manager, account="bot_b", chat_id=999, reasoning="B")

    assert acct_a.get_task_card(999) is not None
    assert acct_b.get_task_card(999) is not None
    assert not _deletes(acct_a)
    assert not _deletes(acct_b)


# ---------------------------------------------------------------------------
# Update stale-card recovery (unchanged): a deleted active card is re-created,
# the recovered id becomes resident, and the replaced id is retired.
# ---------------------------------------------------------------------------

def test_update_recovery_repersists_and_retires_replaced_card(tmp_path):
    """When update must recover by re-creating (the active card was deleted), the
    recovered id becomes the resident id and the replaced id is retired."""
    acct = FakeAccount(fail_edit=True)
    manager, service = _manager(tmp_path, acct)

    # Seed a prior resident id (as if a card existed then got deleted).
    acct.set_task_card(999, "mybot:999:50")

    r = manager._handle_task_card_update({
        "sub_action": "update",
        "card_message_id": "mybot:999:50",
        "rows": [{"tool": "bash", "tool_action": "run", "reasoning": "x",
                  "elapsed_s": 1, "done": False}],
    })
    assert r["status"] == "ok"
    new_id = r["message_id"]
    assert new_id != "mybot:999:50"
    # Recovered id is now the resident id...
    assert acct.get_task_card(999) == new_id
    # ...and the replaced (stale) card id was retired via delete.
    assert any(c[0] == "delete_message" and c[2] == 50 for c in acct.calls)


# ---------------------------------------------------------------------------
# Refresh simulation: a persisted resident survives a fresh account+manager
# process and the next create EDITS it in place (no re-send, no delete).
# ---------------------------------------------------------------------------

def test_persisted_resident_is_edited_in_place_after_refresh(tmp_path):
    """A brand-new manager + account (refresh) reads the persisted resident id
    from the account's state.json seam and edits that same card — it does not
    re-send or delete, so the card does not flicker across a refresh."""
    from lingtai.mcp_servers.telegram.account import TelegramAccount

    state_dir = tmp_path / "telegram" / "mybot"

    class RealStateAccount(TelegramAccount):
        """Real state persistence, but network send/edit/delete are faked."""

        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls: list = []
            self._next_id = 100

        def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
            msg_id = self._next_id
            self._next_id += 1
            self.calls.append(("send_message", chat_id, msg_id, text))
            return {"message_id": msg_id}

        def edit_message(self, chat_id, message_id, text, **kwargs):
            self.calls.append(("edit_message", chat_id, message_id, text))
            return {"ok": True}

        def delete_message(self, chat_id, message_id):
            self.calls.append(("delete_message", chat_id, message_id))
            return {"ok": True}

    acct1 = RealStateAccount(
        alias="mybot", bot_token="1:x", allowed_users=None, state_dir=state_dir,
    )
    svc1 = FakeService([acct1])
    mgr1 = TelegramManager(
        svc1,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    r1 = mgr1._handle_task_card_update({
        "sub_action": "create", "account": "mybot", "chat_id": 999,
        "tool": "bash", "reasoning": "first",
    })
    first_id = r1["message_id"]

    # Simulate a refresh: fresh account (reloads state.json) + fresh manager.
    acct2 = RealStateAccount(
        alias="mybot", bot_token="1:x", allowed_users=None, state_dir=state_dir,
    )
    # Telegram never reuses message ids across the bot's lifetime; the fresh
    # account continues the id sequence rather than restarting it.
    acct2._next_id = 200
    assert acct2.get_task_card(999) == first_id  # persisted across instances

    svc2 = FakeService([acct2])
    mgr2 = TelegramManager(
        svc2,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    r2 = mgr2._handle_task_card_update({
        "sub_action": "create", "account": "mybot", "chat_id": 999,
        "tool": "bash", "reasoning": "second",
    })

    # After the refresh, the persisted resident is edited in place — same id,
    # no replacement send, no delete.
    assert r2["message_id"] == first_id
    assert acct2.get_task_card(999) == first_id
    edits = [c for c in acct2.calls if c[0] == "edit_message"]
    assert edits and edits[-1][2] == int(first_id.split(":")[2])
    assert not [c for c in acct2.calls if c[0] == "send_message"]
    assert not [c for c in acct2.calls if c[0] == "delete_message"]
