"""TelegramService forwards an on_idle_tick callback to every account
(issue #111 wiring). build_manager() wires this to the manager's reconcile."""
from __future__ import annotations

from lingtai.mcp_servers.telegram.service import TelegramService


def test_service_forwards_idle_tick_to_accounts(tmp_path):
    ticks: list[int] = []
    svc = TelegramService(
        working_dir=tmp_path,
        accounts_config=[
            {"alias": "main", "bot_token": "a:b", "poll_interval": 0.0},
            {"alias": "alt", "bot_token": "c:d", "poll_interval": 0.0},
        ],
        on_message=lambda alias, update: None,
        on_idle_tick=lambda: ticks.append(1),
    )

    # Each account received the same callback.
    for alias in svc.list_accounts():
        acct = svc.get_account(alias)
        assert acct._on_idle_tick is not None
        acct._on_idle_tick()

    assert ticks == [1, 1]


def test_service_idle_tick_optional(tmp_path):
    svc = TelegramService(
        working_dir=tmp_path,
        accounts_config=[{"alias": "main", "bot_token": "a:b"}],
        on_message=lambda alias, update: None,
    )
    assert svc.get_account("main")._on_idle_tick is None
