"""Agent-side Telegram gateway proxy (proxy mode).

A ``TelegramGatewayProxy`` exposes the same account surface the manager uses
but serializes each outbound call into a gateway command delivered to the
injected ``command_sink`` — it never calls ``getUpdates``, never constructs a
Telegram network client, and never imports httpx. The real transport stays in
the gateway process (see ``gateway.py``), which owns polling and outbound
Bot API calls for all configured stations.

The production ``command_sink`` is the SQLite WAL command bus
(``SqliteCommandBus.submit``): the proxy waits (bounded) for the gateway
result and returns it to the manager, so compound message ids built from
``message_id`` keep working in proxy mode. A plain ``list`` sink keeps the
older queue-only behavior for tests.

``TelegramGatewayProxyService`` is the minimal proxy-mode service surface: it
reuses ``TelegramService``'s durable taskcard-settings implementation and
adds only the account/service surface the manager really needs — it never
copies the whole service and never builds a network client.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from .service import TelegramService


class TelegramGatewayProxy:
    """Queue-only Telegram account surface for gateway/proxy deployments."""

    def __init__(
        self,
        alias: str,
        command_sink: Callable[[dict], None] | list[dict],
    ) -> None:
        self.alias = alias
        self._sink = command_sink
        # Proxy mode never constructs a Telegram network client.
        self._network_client: Any = None
        self._poll_thread: Any = None

    def _queue(self, method: str, **params: Any) -> dict:
        # The gateway resolves the target account from the alias; a station may
        # own several accounts, so every command carries its own.
        params.setdefault("alias", self.alias)
        command = {"method": method, "params": params}
        if isinstance(self._sink, list):
            self._sink.append(command)
            return {"status": "queued", "method": method}
        result = self._sink(command)
        # Production sink (SqliteCommandBus.submit) returns the gateway result;
        # test doubles may return None, in which case keep the queue-only shape.
        if isinstance(result, dict):
            return result
        return {"status": "queued", "method": method}

    # -- Outbound surface (queued to the gateway) ----------------------------

    def send_message(self, chat_id, text, reply_markup=None, reply_to_message_id=None, **kwargs):
        params: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        params.update(kwargs)
        return self._queue("sendMessage", **params)

    def send_photo(self, chat_id, path, caption=None, reply_to_message_id=None, **kwargs):
        params: dict[str, Any] = {"chat_id": chat_id, "path": path}
        if caption is not None:
            params["caption"] = caption
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        params.update(kwargs)
        return self._queue("sendPhoto", **params)

    def send_document(self, chat_id, path, caption=None, reply_to_message_id=None, **kwargs):
        params: dict[str, Any] = {"chat_id": chat_id, "path": path}
        if caption is not None:
            params["caption"] = caption
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        params.update(kwargs)
        return self._queue("sendDocument", **params)

    def edit_message(self, chat_id, message_id, text, **kwargs):
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        params.update(kwargs)
        return self._queue("editMessageText", **params)

    def edit_message_caption(self, chat_id, message_id, caption=None, **kwargs):
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if caption is not None:
            params["caption"] = caption
        params.update(kwargs)
        return self._queue("editMessageCaption", **params)

    def delete_message(self, chat_id, message_id):
        return self._queue(
            "deleteMessage", chat_id=chat_id, message_id=message_id,
        )

    def set_message_reaction(self, chat_id, message_id, reaction=None, is_big=False):
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reaction is not None:
            params["reaction"] = reaction
        params["is_big"] = is_big
        return self._queue("setMessageReaction", **params)

    def send_chat_action(self, chat_id, action):
        return self._queue("sendChatAction", chat_id=chat_id, action=action)

    def answer_callback_query(self, callback_query_id, **kwargs):
        params: dict[str, Any] = {"callback_query_id": callback_query_id}
        params.update(kwargs)
        return self._queue("answerCallbackQuery", **params)

    def get_me(self):
        return self._queue("getMe")

    def public_identity(self):
        """Non-secret identity via the gateway's getMe (or alias-only fallback)."""
        result = self._queue("getMe")
        if isinstance(result, dict) and result.get("status") != "error":
            if result.get("alias") is not None:
                return result
        return {"alias": self.alias}

    def _request(self, method: str, **kwargs: Any) -> dict:
        """Minimal Bot-API-shaped passthrough for manager best-effort paths.

        The manager's placeholder-typing shortcut calls
        ``acct._request('sendChatAction', json={...})``; proxy mode routes it
        through the gateway like every other outbound call.
        """
        params: dict[str, Any] = {}
        payload = kwargs.get("json")
        if isinstance(payload, dict):
            params.update(payload)
        return self._queue(method, **params)

    # -- Polling is forbidden in proxy mode ----------------------------------

    def get_updates(self):
        raise NotImplementedError(
            "proxy mode never calls getUpdates; the gateway owns polling"
        )

    def start(self) -> None:
        """Proxy mode has no poll thread; the gateway owns the transport."""
        return None

    def stop(self) -> None:
        return None


class TelegramGatewayProxyService(TelegramService):
    """Proxy-mode service: reuses the taskcard settings implementation only.

    Deliberately builds no network account and starts no poll thread: every
    ``get_account`` returns a ``TelegramGatewayProxy`` bound to the station's
    command bus. ``start()``/``stop()`` are no-ops — the gateway owns polling
    and the Task Card/receipt background owners. The manager keeps working
    because the inherited taskcard settings surface
    (``taskcard_enabled``, ``taskcard_normal_rows``, ``set_taskcard_listener``,
    ``set_taskcard_enabled``) is unchanged, and only the account surface the
    manager really needs is added.
    """

    def __init__(
        self,
        working_dir: Path | str,
        aliases: Iterable[str],
        command_sink: Callable[[dict], Any],
        *,
        config_source: str | None = None,
    ) -> None:
        # Reuse TelegramService's durable taskcard state + LocalCommandCore;
        # an empty accounts_config means no network account is ever built.
        super().__init__(
            working_dir=working_dir,
            accounts_config=[],
            on_message=lambda _alias, _update: None,
            config_source=config_source,
        )
        self._proxy_accounts = [
            TelegramGatewayProxy(alias, command_sink) for alias in aliases
        ]
        self._proxy_by_alias = {p.alias: p for p in self._proxy_accounts}
        # Optional bus reference so stop() can close the process-owned bus.
        self._command_bus: Any = None

    def get_account(self, alias: str) -> TelegramGatewayProxy:
        try:
            return self._proxy_by_alias[alias]
        except KeyError:
            raise KeyError(f"unknown account alias: {alias}") from None

    @property
    def default_account(self) -> TelegramGatewayProxy:
        return self._proxy_accounts[0]

    def list_accounts(self) -> list[str]:
        return [proxy.alias for proxy in self._proxy_accounts]

    def account_details(self) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        for proxy in self._proxy_accounts:
            try:
                item = proxy.public_identity()
            except Exception:
                item = {}
            item.setdefault("alias", proxy.alias)
            details.append(item)
        return details

    def start(self) -> None:
        """Proxy mode never polls; the gateway owns the transport."""
        return None

    def stop(self) -> None:
        """Close the process-owned command bus if one was attached."""
        bus = self._command_bus
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass
            self._command_bus = None
