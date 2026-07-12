"""TelegramAccount resident Task Card state — one card per account+chat.

Pins the durable ``state.json`` seam that lets a post-refresh card delete the
prior one (Jason #6667: "when the new one appears, delete the previous one").
The id is persisted per chat via the account's existing atomic state file.
"""

from __future__ import annotations

import json
from pathlib import Path

from lingtai.mcp_servers.telegram.account import TelegramAccount


def _account(state_dir: Path) -> TelegramAccount:
    return TelegramAccount(
        alias="mybot",
        bot_token="123:ABC",
        allowed_users=None,
        state_dir=state_dir,
    )


def test_get_task_card_absent_is_none(tmp_path):
    acct = _account(tmp_path)
    assert acct.get_task_card(999) is None


def test_set_task_card_persists_per_chat(tmp_path):
    acct = _account(tmp_path)
    acct.set_task_card(999, "mybot:999:100")
    assert acct.get_task_card(999) == "mybot:999:100"
    # A different chat is isolated.
    assert acct.get_task_card(555) is None


def test_task_card_survives_fresh_instance(tmp_path):
    """Refresh simulation: a new account instance reads the persisted id."""
    acct = _account(tmp_path)
    acct.set_task_card(999, "mybot:999:100")

    reborn = _account(tmp_path)
    assert reborn.get_task_card(999) == "mybot:999:100"


def test_set_task_card_does_not_disturb_other_state(tmp_path):
    acct = _account(tmp_path)
    acct._last_update_id = 4242
    acct._save_state()
    acct.set_task_card(999, "mybot:999:100")

    reborn = _account(tmp_path)
    assert reborn.get_task_card(999) == "mybot:999:100"
    assert reborn._last_update_id == 4242


def test_clear_task_card_removes_only_matching_chat(tmp_path):
    acct = _account(tmp_path)
    acct.set_task_card(999, "mybot:999:100")
    acct.set_task_card(555, "mybot:555:200")
    acct.clear_task_card(999)
    assert acct.get_task_card(999) is None
    assert acct.get_task_card(555) == "mybot:555:200"


def test_legacy_state_without_task_cards_loads(tmp_path):
    """A pre-feature state.json (no ``task_cards`` key) loads without error."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"last_update_id": 7, "bot_info": {"id": 1}}),
        encoding="utf-8",
    )
    acct = _account(tmp_path)
    assert acct.get_task_card(999) is None
    assert acct._last_update_id == 7
    # Writing a card preserves the legacy fields.
    acct.set_task_card(999, "mybot:999:100")
    reborn = _account(tmp_path)
    assert reborn._last_update_id == 7
    assert reborn._bot_info == {"id": 1}


def test_malformed_task_cards_normalized(tmp_path):
    """A malformed ``task_cards`` (wrong type / junk values) loads as empty and
    never loses the unrelated last_update_id."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "last_update_id": 9,
            "task_cards": ["not", "a", "dict"],
        }),
        encoding="utf-8",
    )
    acct = _account(tmp_path)
    assert acct.get_task_card(999) is None
    assert acct._last_update_id == 9


def test_malformed_task_card_values_dropped(tmp_path):
    """Non-string ids inside task_cards are dropped; valid ones survive."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "task_cards": {"999": "mybot:999:100", "555": 12345, "777": None},
        }),
        encoding="utf-8",
    )
    acct = _account(tmp_path)
    assert acct.get_task_card(999) == "mybot:999:100"
    assert acct.get_task_card(555) is None
    assert acct.get_task_card(777) is None
