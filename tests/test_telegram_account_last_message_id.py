"""TelegramAccount per-chat last-message-id observation (Jason #5272/#5273).

The account is the owning layer for "what is the latest message id in this
chat" — it sees every inbound update through the poll loop and every outbound
message through its send methods. The Task Card manager reads this high-water
mark to decide whether the resident card is still the chat's last message.

This is an ephemeral observation cache (not persisted): on restart it is
unknown until the next observed message, and unknown is treated conservatively
by the manager (edit in place, never delete).
"""

from __future__ import annotations

from pathlib import Path

from lingtai.mcp_servers.telegram.account import TelegramAccount


def _account(state_dir: Path, on_message=None) -> TelegramAccount:
    return TelegramAccount(
        alias="mybot",
        bot_token="123:ABC",
        allowed_users=None,
        state_dir=state_dir,
        on_message=on_message,
    )


def test_last_message_id_unknown_before_any_observation(tmp_path):
    acct = _account(tmp_path)
    assert acct.get_last_message_id(555) is None


def test_inbound_message_bumps_last_message_id(tmp_path):
    seen = []
    acct = _account(tmp_path, on_message=lambda alias, upd: seen.append(upd))
    acct._process_update({
        "update_id": 1,
        "message": {"message_id": 4242, "chat": {"id": 555},
                    "from": {"id": 9}, "text": "hi"},
    })
    assert acct.get_last_message_id(555) == 4242
    # Dispatched to the agent, and the id was recorded.
    assert seen


def test_last_message_id_is_monotonic_per_chat(tmp_path):
    acct = _account(tmp_path)
    acct._note_chat_message_id(555, 10)
    acct._note_chat_message_id(555, 30)
    acct._note_chat_message_id(555, 20)  # out-of-order/older, must not lower it
    assert acct.get_last_message_id(555) == 30


def test_last_message_id_is_per_chat_isolated(tmp_path):
    acct = _account(tmp_path)
    acct._note_chat_message_id(555, 10)
    acct._note_chat_message_id(777, 99)
    assert acct.get_last_message_id(555) == 10
    assert acct.get_last_message_id(777) == 99


def test_edited_message_does_not_bump_last_message_id(tmp_path):
    acct = _account(tmp_path)
    acct._note_chat_message_id(555, 50)
    # An edit re-uses an existing (lower) message id and adds no bottom message.
    acct._process_update({
        "update_id": 2,
        "edited_message": {"message_id": 40, "chat": {"id": 555},
                           "from": {"id": 9}, "text": "typo fix"},
    })
    assert acct.get_last_message_id(555) == 50
