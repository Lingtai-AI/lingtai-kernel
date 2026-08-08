import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.account import TelegramAccount
from lingtai.mcp_servers.telegram.bus import SqliteCommandBus
from lingtai.mcp_servers.telegram.gateway import GatewayManifestError, load_gateway_manifest, run_gateway
from lingtai.mcp_servers.telegram.proxy import TelegramGatewayProxy


class _Account:
    def __init__(self, alias):
        self.alias = alias
        self.calls = []
        self._next_id = 100
        self._task_cards = {}

    def send_message(self, chat_id, text, **kwargs):
        message_id = self._next_id
        self._next_id += 1
        self.calls.append(("send_message", chat_id, message_id, text))
        return {"message_id": message_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        return {"ok": True}

    def delete_message(self, chat_id, message_id):
        return {"ok": True}

    def send_chat_action(self, chat_id, action="typing"):
        return {"ok": True}

    def public_identity(self):
        return {"alias": self.alias}

    def set_message_reaction(self, chat_id, message_id, reaction):
        return {"ok": True}

    def get_task_card(self, chat_id):
        return self._task_cards.get(str(chat_id))

    def set_task_card(self, chat_id, message_id):
        self._task_cards[str(chat_id)] = message_id

    def clear_task_card(self, chat_id):
        self._task_cards.pop(str(chat_id), None)

    def list_task_card_chats(self):
        return [int(chat_id) for chat_id in self._task_cards]

    def get_last_message_id(self, chat_id):
        return None


class _Service:
    def __init__(self, account):
        self.default_account = account
        self._account = account

    def get_account(self, alias):
        return self._account

    def list_accounts(self):
        return [self._account.alias]

    def taskcard_enabled(self):
        return False

    def taskcard_normal_rows(self):
        return 1

    def set_taskcard_listener(self, listener):
        self._listener = listener

    def start(self):
        pass

    def stop(self):
        pass


def test_proxy_queues_commands_without_a_network_client():
    commands = []
    proxy = TelegramGatewayProxy(alias="bot", command_sink=commands)
    assert proxy.send_message(123, "你好")["status"] == "queued"
    assert commands[-1]["method"] == "sendMessage"
    assert proxy._network_client is None
    with pytest.raises(NotImplementedError):
        proxy.get_updates()


def test_manifest_references_configs_and_rejects_embedded_secrets(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "stations": [{"name": "control", "config": "telegram.json"}],
    }), encoding="utf-8")
    assert load_gateway_manifest(manifest)[0]["config"] == str(tmp_path / "telegram.json")

    manifest.write_text(json.dumps({
        "stations": [{"name": "control", "config": "x", "bot_token": "secret"}],
    }), encoding="utf-8")
    with pytest.raises(GatewayManifestError):
        load_gateway_manifest(manifest)


def test_polling_and_outbound_clients_are_independent(monkeypatch):
    import lingtai.mcp_servers.telegram.account as account_module

    class _Httpx:
        Timeout = staticmethod(lambda *args, **kwargs: None)
        Client = staticmethod(lambda *args, **kwargs: _Client())

    class _Client:
        def close(self):
            pass

    monkeypatch.setattr(account_module, "httpx", _Httpx)
    account = TelegramAccount("bot", "123:not-real", None)
    account._ensure_client()
    account._ensure_client(polling=True)
    assert account._poll_client is not account._outbound_client
    account.stop()
    assert account._poll_client is account._outbound_client is None


def test_two_station_gateway_routes_inbound_and_bus_commands(tmp_path: Path):
    agents = [tmp_path / "a", tmp_path / "b"]
    configs = [tmp_path / "a.json", tmp_path / "b.json"]
    aliases = ["botA", "botB"]
    for agent, config, alias in zip(agents, configs, aliases):
        (agent / "telegram").mkdir(parents=True)
        config.write_text(json.dumps({
            "accounts": [{"alias": alias, "bot_token": "123:not-real"}],
        }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"stations": [
        {"name": "a", "config": str(configs[0]), "agent_dir": str(agents[0])},
        {"name": "b", "config": str(configs[1]), "agent_dir": str(agents[1])},
    ]}), encoding="utf-8")

    accounts = {alias: _Account(alias) for alias in aliases}

    def service_factory(working_dir, accounts_config, on_message, config_source=None):
        service = _Service(accounts[accounts_config[0]["alias"]])
        service.on_message = on_message
        return service

    host = run_gateway(manifest, service_factory=service_factory)
    update = lambda message_id, chat_id, text: {
        "update_id": message_id,
        "message": {
            "message_id": message_id, "date": 1700000000, "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": 7, "first_name": "Human"},
        },
    }
    host.managers[0].on_incoming("botA", update(1, 111, "hello A"))
    host.managers[1].on_incoming("botB", update(2, 222, "hello B"))
    assert len(list((agents[0] / ".mcp_inbox" / "telegram").glob("*.json"))) == 1
    assert len(list((agents[1] / ".mcp_inbox" / "telegram").glob("*.json"))) == 1

    client = SqliteCommandBus(agents[0] / "telegram" / "gateway.sqlite3")
    result = client.submit({
        "method": "sendMessage",
        "params": {"alias": "botA", "chat_id": 111, "text": "ping"},
    }, timeout=10)
    assert result["message_id"] == 100
    assert len([call for call in accounts["botA"].calls if call[0] == "send_message"]) == 1
    assert accounts["botB"].calls == []
    client.close()

    host.stop()
    assert not any(thread.is_alive() for thread in host._threads)
    assert all(bus._closed for bus in host.buses.values())
