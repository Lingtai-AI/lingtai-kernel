"""Gateway/proxy mode: one gateway-owned Telegram transport for stations.

The gateway process owns polling and outbound Bot API calls for every
configured station by reusing the standalone ``TelegramService``/account
machinery. Agent-side stations run in proxy mode
(``lingtai.mcp_servers.telegram.proxy.TelegramGatewayProxy``): they queue
commands into a sink and never call ``getUpdates`` or construct a network
client.

The gateway manifest is free of secrets: it only names each station and
references that station's existing configuration path (which holds the bot
tokens). ``load_gateway_manifest`` rejects any manifest that tries to carry
inline secret material.

``run_gateway`` builds a complete ``TelegramManager`` + ``TelegramService``
per manifest station (not a bare service) and returns a ``GatewayHost`` whose
``stop()`` closes every manager and command bus. Each manager delivers inbound
updates to the correct Agent via
``lingtai.services.mcp_licc.push_inbox_event(..., agent_dir=<station>,
mcp_name='telegram')`` and uses that station's ``agent_dir`` with a
``PosixNotificationStoreAdapter``, so resident Task Card state, receipts, and
the event tail stay per-station.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.services.mcp_licc import push_inbox_event

from .bus import SqliteCommandBus

log = logging.getLogger(__name__)

# Keys that are never allowed inside the gateway manifest (secrets-free).
_FORBIDDEN_MANIFEST_KEYS = frozenset({
    "bot_token", "token", "api_key", "api_token", "access_token",
    "secret", "password", "oauth",
})

# Default per-station command bus file (relative to the station agent_dir).
_DEFAULT_BUS_FILENAME = "gateway.sqlite3"


class GatewayManifestError(ValueError):
    """Raised when a gateway manifest is malformed or carries secrets."""


def load_gateway_manifest(manifest_path: Path | str) -> list[dict[str, Any]]:
    """Load and validate a secrets-free gateway manifest.

    Schema:

    .. code-block:: json

        {
          "stations": [
            {"name": "control-total", "config": "<absolute-or-relative config path>"}
          ]
        }

    Returns the normalized station list with absolute config paths. Raises
    ``GatewayManifestError`` for malformed manifests, unknown top-level keys,
    or any attempt to embed secret material.
    """
    path = Path(manifest_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GatewayManifestError(f"cannot read gateway manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GatewayManifestError(f"invalid gateway manifest JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GatewayManifestError("gateway manifest must be a JSON object")
    unknown = set(data) - {"stations"}
    if unknown:
        raise GatewayManifestError(f"unknown gateway manifest keys: {sorted(unknown)}")
    forbidden = set(data) & _FORBIDDEN_MANIFEST_KEYS
    if forbidden:
        raise GatewayManifestError(
            f"gateway manifest must stay secrets-free; remove {sorted(forbidden)}"
        )
    stations = data.get("stations")
    if not isinstance(stations, list) or not stations:
        raise GatewayManifestError("gateway manifest needs a non-empty 'stations' list")

    base = path.parent
    normalized: list[dict[str, Any]] = []
    for index, station in enumerate(stations):
        if not isinstance(station, dict):
            raise GatewayManifestError(f"stations[{index}] must be an object")
        forbidden = set(station) & _FORBIDDEN_MANIFEST_KEYS
        if forbidden:
            raise GatewayManifestError(
                f"stations[{index}] must stay secrets-free; remove {sorted(forbidden)}"
            )
        name = station.get("name")
        config = station.get("config")
        if not isinstance(name, str) or not name.strip():
            raise GatewayManifestError(f"stations[{index}] needs a 'name'")
        if not isinstance(config, str) or not config.strip():
            raise GatewayManifestError(f"stations[{index}] needs a 'config' path")
        config_path = Path(config)
        if not config_path.is_absolute():
            config_path = base / config_path
        agent_dir = station.get("agent_dir")
        if agent_dir is not None and not isinstance(agent_dir, str):
            raise GatewayManifestError(f"stations[{index}].agent_dir must be a path string")
        normalized.append(
            {
                "name": name.strip(),
                "config": str(config_path),
                "agent_dir": str(agent_dir) if agent_dir else str(config_path.parent),
            }
        )
    return normalized


def _load_station_config(config_path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GatewayManifestError(f"cannot read station config {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GatewayManifestError(f"station config {config_path} must be an object")
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise GatewayManifestError(f"station config {config_path} needs 'accounts'")
    return accounts


def _read_gateway_marker(agent_dir: Path) -> dict | None:
    """Read the optional ``<agent_dir>/telegram/gateway.json`` marker (proxy mode).

    The marker may carry a ``"bus"`` path (absolute or relative to the
    agent_dir) pointing at the station's shared command bus; anything else is
    ignored. Malformed markers degrade to ``None`` so a station still runs.
    """
    marker = agent_dir / "telegram" / "gateway.json"
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("unreadable gateway marker %s; ignoring it", marker)
        return None
    return data if isinstance(data, dict) else None


def _station_bus_path(agent_dir: Path, marker: dict | None) -> Path:
    """Resolve the per-station command bus file path."""
    default = agent_dir / "telegram" / _DEFAULT_BUS_FILENAME
    if not marker:
        return default
    value = marker.get("bus")
    if not isinstance(value, str) or not value.strip():
        return default
    path = Path(value)
    return path if path.is_absolute() else agent_dir / path


def _default_on_inbound(agent_dir: Path) -> Callable[[dict], None]:
    """Build the manager inbound callback: LICC push into this station's Agent.

    ``agent_dir`` and ``mcp_name='telegram'`` are explicit so the event lands
    in the right station's inbox regardless of the process environment.
    """

    def _on_inbound(event: dict) -> None:
        push_inbox_event(
            sender=event.get("from") or "telegram",
            subject=event.get("subject") or "telegram update",
            body=event.get("body") or "",
            metadata=event.get("metadata"),
            wake=event.get("wake", True),
            agent_dir=str(agent_dir),
            mcp_name="telegram",
        )

    return _on_inbound


class GatewayCommandRouter:
    """Consume proxy-queued commands and dispatch to the owning service account."""

    def __init__(self, services: Iterable[Any]) -> None:
        self._by_alias: dict[str, Any] = {}
        for service in services:
            for alias in service.list_accounts():
                self._by_alias[alias] = service.get_account(alias)

    def route(self, command: dict[str, Any], alias: str | None = None) -> dict[str, Any]:
        """Dispatch one queued gateway command; returns the account result.

        Unknown methods fail fast with an explicit error result — they are
        never silently ignored or routed elsewhere.
        """
        if not isinstance(command, dict):
            return {"status": "error", "error": "command must be an object"}
        method = command.get("method")
        params = command.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return {"status": "error", "error": "command needs 'method' and 'params'"}
        params = dict(params)
        target = alias or params.pop("alias", None) or params.pop("account", None)
        account = self._by_alias.get(target) if isinstance(target, str) else None
        if account is None:
            return {"status": "error", "error": f"unknown account alias: {target}"}
        handler = _COMMAND_HANDLERS.get(method)
        if handler is None:
            return {"status": "error", "error": f"unsupported gateway command: {method}"}
        return handler(account, params)


def _handle_send_message(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.send_message(
        params["chat_id"],
        params["text"],
        reply_markup=params.get("reply_markup"),
        reply_to_message_id=params.get("reply_to_message_id"),
        **{k: v for k, v in params.items() if k not in {
            "chat_id", "text", "reply_markup", "reply_to_message_id"}},
    )


def _handle_edit_message(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.edit_message(
        params["chat_id"], params["message_id"], params["text"],
        **{k: v for k, v in params.items() if k not in {"chat_id", "message_id", "text"}},
    )


def _handle_edit_caption(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.edit_message(
        params["chat_id"], params["message_id"], params.get("caption", ""),
        is_caption=True,
        **{k: v for k, v in params.items() if k not in {"chat_id", "message_id", "caption"}},
    )


def _handle_delete_message(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.delete_message(params["chat_id"], params["message_id"])


def _handle_reaction(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.set_message_reaction(
        params["chat_id"], params["message_id"],
        reaction=params.get("reaction"),
        is_big=bool(params.get("is_big", False)),
    )


def _handle_chat_action(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.send_chat_action(params["chat_id"], params["action"])


def _handle_callback(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    # The real TelegramAccount auto-answers callbacks during polling and has no
    # public answer_callback_query surface; the manager never routes this
    # command. Guard instead of crashing the consumer loop with AttributeError.
    handler = getattr(account, "answer_callback_query", None)
    if not callable(handler):
        return {
            "status": "error",
            "error": "account does not support answerCallbackQuery",
        }
    return handler(
        params["callback_query_id"],
        **{k: v for k, v in params.items() if k != "callback_query_id"},
    )


def _handle_get_me(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.public_identity()


def _handle_send_photo(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.send_photo(
        params["chat_id"],
        params["path"],
        caption=params.get("caption"),
        reply_to_message_id=params.get("reply_to_message_id"),
        **{k: v for k, v in params.items() if k not in {
            "chat_id", "path", "caption", "reply_to_message_id"}},
    )


def _handle_send_document(account: Any, params: dict[str, Any]) -> dict[str, Any]:
    return account.send_document(
        params["chat_id"],
        params["path"],
        caption=params.get("caption"),
        reply_to_message_id=params.get("reply_to_message_id"),
        **{k: v for k, v in params.items() if k not in {
            "chat_id", "path", "caption", "reply_to_message_id"}},
    )


_COMMAND_HANDLERS: dict[str, Callable[[Any, dict[str, Any]], dict[str, Any]]] = {
    "sendMessage": _handle_send_message,
    "sendPhoto": _handle_send_photo,
    "sendDocument": _handle_send_document,
    "editMessageText": _handle_edit_message,
    "editMessageCaption": _handle_edit_caption,
    "deleteMessage": _handle_delete_message,
    "setMessageReaction": _handle_reaction,
    "sendChatAction": _handle_chat_action,
    "answerCallbackQuery": _handle_callback,
    "getMe": _handle_get_me,
}


class GatewayHost:
    """A running single-process Gateway: managers + command buses + router.

    ``start()`` recovers stale claims and spawns one consumer thread per
    station bus (the gateway is the single owner). ``stop()`` joins the
    consumer threads, stops every manager, and closes every command bus so no
    poll thread or consumer is orphaned.
    """

    def __init__(
        self,
        *,
        stations: list[dict[str, Any]],
        managers: list[Any],
        services: list[Any],
        router: GatewayCommandRouter,
        buses: dict[str, SqliteCommandBus],
    ) -> None:
        self.stations = stations
        self.managers = managers
        self.services = services
        self.router = router
        self.buses = buses
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        """Recover stale claims and start one consumer thread per station bus."""
        for bus in self.buses.values():
            try:
                bus.reset_stale()
            except Exception as exc:
                log.warning("gateway stale-claim recovery failed: %s", exc)
        for name, bus in self.buses.items():
            thread = threading.Thread(
                target=self._consume_loop,
                args=(name, bus),
                name=f"gateway-consumer-{name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _consume_loop(self, name: str, bus: SqliteCommandBus) -> None:
        """Single-owner loop: claim one command, route it, write back the result."""
        while not self._stop.is_set():
            try:
                claimed = bus.claim()
            except Exception as exc:
                log.warning("gateway claim failed (%s): %s", name, exc)
                claimed = None
            if claimed is None:
                self._stop.wait(0.05)
                continue
            command_id, command = claimed
            try:
                result = self.router.route(command)
            except Exception as exc:
                result = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            try:
                bus.complete(command_id, result)
            except Exception as exc:
                log.warning("gateway complete failed (%s): %s", name, exc)

    def stop(self) -> None:
        """Close consumer threads, managers, and command buses (idempotent)."""
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5.0)
        self._threads = []
        for manager in self.managers:
            try:
                manager.stop()
            except Exception as exc:
                log.warning("gateway manager stop failed: %s", exc)
        for bus in self.buses.values():
            bus.close()


def run_gateway(
    manifest_path: Path | str,
    *,
    on_inbound: Callable[[dict], None] | None = None,
    command_source: Iterable[dict[str, Any]] | None = None,
    service_factory: Callable[..., Any] | None = None,
    start: bool = True,
) -> GatewayHost:
    """Start one complete ``TelegramManager`` + ``TelegramService`` per station.

    Each manager uses the station's ``agent_dir``, a
    ``PosixNotificationStoreAdapter``, and delivers inbound to that station's
    Agent via ``push_inbox_event(..., agent_dir=<station>, mcp_name='telegram')``.
    Returns a ``GatewayHost`` whose ``stop()`` closes every manager and command
    bus. ``service_factory`` is an injectable seam for tests; production uses
    the real ``TelegramService``.
    """
    from .manager import TelegramManager
    from .service import TelegramService

    stations = load_gateway_manifest(manifest_path)
    managers: list[TelegramManager] = []
    services: list[Any] = []
    buses: dict[str, SqliteCommandBus] = {}
    factory = service_factory or TelegramService

    for station in stations:
        accounts_config = _load_station_config(Path(station["config"]))
        agent_dir = Path(station["agent_dir"])
        agent_dir.mkdir(parents=True, exist_ok=True)
        marker = _read_gateway_marker(agent_dir)
        bus = SqliteCommandBus(_station_bus_path(agent_dir, marker))
        buses[station["name"]] = bus

        inbound = on_inbound or _default_on_inbound(agent_dir)
        # Forward declare the manager so the service's on_message callback can
        # reach it (same pattern as server.build_manager).
        mgr_ref: list[TelegramManager | None] = [None]
        svc = factory(
            working_dir=agent_dir,
            accounts_config=accounts_config,
            on_message=lambda alias, update, ref=mgr_ref: ref[0].on_incoming(
                alias, update,
            ),
            config_source=station["config"],
        )
        notification_store = PosixNotificationStoreAdapter(agent_dir)
        mgr = TelegramManager(
            service=svc,
            working_dir=agent_dir,
            notification_store=notification_store,
            on_inbound=inbound,
        )
        mgr_ref[0] = mgr
        managers.append(mgr)
        services.append(svc)

    router = GatewayCommandRouter(services)
    host = GatewayHost(
        stations=stations,
        managers=managers,
        services=services,
        router=router,
        buses=buses,
    )
    if start:
        for mgr in managers:
            mgr.start()
        host.start()
    if command_source is not None:
        for command in command_source:
            host.router.route(command)
    return host
