"""Singleton resident Task Card at the manager layer.

Jason #6665/#6667: keep exactly one resident card per account+chat; when the new
one appears, delete the previous one.  Ordering is strict — send new first, then
delete old — so the new card is visible before the old vanishes, and a failed new
send preserves the old card and its persisted id.  Delete is best-effort/fail-open
and must never roll state back from the new card.
"""

from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class FakeAccount:
    """Mimics the real TelegramAccount singleton API + send/delete."""

    def __init__(self, alias="mybot", *, fail_send=False, fail_delete=False):
        self.alias = alias
        self.calls: list = []
        self._task_cards: dict[str, str] = {}
        self._next_id = 100
        self._fail_send = fail_send
        self._fail_delete = fail_delete

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


# ---------------------------------------------------------------------------
# First create stores current id and deletes nothing
# ---------------------------------------------------------------------------

def test_first_create_stores_id_and_deletes_nothing(tmp_path):
    manager, service = _manager(tmp_path)
    acct = service.default_account

    r = _create(manager)
    assert r["status"] == "ok"
    assert len(_sends(acct)) == 1
    assert not _deletes(acct)
    assert acct.get_task_card(999) == r["message_id"]


# ---------------------------------------------------------------------------
# Second create sends new BEFORE deleting old, and stores the new id
# ---------------------------------------------------------------------------

def test_second_create_sends_new_then_deletes_old(tmp_path):
    manager, service = _manager(tmp_path)
    acct = service.default_account

    r1 = _create(manager, reasoning="first")
    first_id = r1["message_id"]

    r2 = _create(manager, reasoning="second")
    second_id = r2["message_id"]
    assert second_id != first_id

    # New id is now the resident card.
    assert acct.get_task_card(999) == second_id

    # Strict ordering: the new send happens before the old delete.
    kinds = [c[0] for c in acct.calls]
    assert kinds == ["send_message", "send_message", "delete_message"]

    # Only the specifically tracked prior message is deleted.
    _, del_chat, del_msg = _deletes(acct)[0]
    assert del_chat == 999
    assert del_msg == int(first_id.split(":")[2])


# ---------------------------------------------------------------------------
# Failed new send preserves old card + persisted id, performs no delete
# ---------------------------------------------------------------------------

def test_failed_new_send_preserves_old_and_no_delete(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)

    r1 = _create(manager, reasoning="first")
    first_id = r1["message_id"]

    acct._fail_send = True
    r2 = _create(manager, reasoning="second")
    assert r2["status"] == "error"

    # Old card and its persisted id survive; nothing deleted.
    assert acct.get_task_card(999) == first_id
    assert not _deletes(acct)


# ---------------------------------------------------------------------------
# Old-delete failure leaves the new card authoritative (fail-open)
# ---------------------------------------------------------------------------

def test_old_delete_failure_is_fail_open(tmp_path):
    acct = FakeAccount(fail_delete=True)
    manager, service = _manager(tmp_path, acct)

    r1 = _create(manager, reasoning="first")
    r2 = _create(manager, reasoning="second")

    # Even though the delete raised, the create succeeded and the new id is
    # authoritative — state is never rolled back.
    assert r2["status"] == "ok"
    assert acct.get_task_card(999) == r2["message_id"]
    assert len(_deletes(acct)) == 1  # the delete was attempted


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
# Refresh simulation: persisted prior id drives deletion after reconstruction
# ---------------------------------------------------------------------------

def test_update_recovery_repersists_and_retires_replaced_card(tmp_path):
    """When update must recover by re-creating (the active card was deleted), the
    recovered id becomes the resident id and the replaced id is retired."""
    class DeletedCardAccount(FakeAccount):
        def edit_message(self, chat_id, message_id, text, **kwargs):
            self.calls.append(("edit_message", chat_id, message_id, text))
            raise RuntimeError("message to edit not found")

    acct = DeletedCardAccount()
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


def test_persisted_prior_drives_deletion_after_refresh(tmp_path):
    """A brand-new manager + account (refresh) still deletes the prior card
    because the prior id was persisted in the account's state.json seam."""
    from lingtai.mcp_servers.telegram.account import TelegramAccount

    state_dir = tmp_path / "telegram" / "mybot"

    class RealStateAccount(TelegramAccount):
        """Real state persistence, but network send/delete are faked."""

        def __init__(self, **kw):
            super().__init__(**kw)
            self.calls: list = []
            self._next_id = 100

        def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
            msg_id = self._next_id
            self._next_id += 1
            self.calls.append(("send_message", chat_id, msg_id, text))
            return {"message_id": msg_id}

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

    # The prior card (from before the refresh) is deleted after the new one.
    dels = [c for c in acct2.calls if c[0] == "delete_message"]
    assert len(dels) == 1
    assert dels[0][2] == int(first_id.split(":")[2])
    assert acct2.get_task_card(999) == r2["message_id"]
