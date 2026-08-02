"""FeishuAccount — single app credential, WebSocket listener + REST sender.

One daemon thread per account runs the lark-channel-sdk WebSocket client.
Constructor stores config only — no connections, no threads.
start() spawns the WebSocket thread and initialises the REST client.
stop() signals the thread to stop and joins it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# lark_channel is lazy-imported so the module stays importable without
# the optional dependency installed.
lark: Any = None

# The lark_channel.ws.client module, captured lazily. Stored globally so tests
# can inject a fake SDK module (see test_feishu_channel_sdk_account.py). The real
# SDK keeps its own module-level ``loop`` attribute that ``Client.start()`` uses
# directly — see ``_ThreadLocalLoop`` and ``_ws_loop`` for why that matters.
_sdk_ws_client_module: Any = None

_lark_logging_lock = threading.Lock()
_LARK_CREDENTIAL_QUERY = re.compile(
    r"(?i)([?&](?:access_key|ticket|token|tenant_access_token|app_access_token)=)"
    r"[^&\s]+"
)


class _RedactLarkCredentials(logging.Filter):
    """Remove credential-bearing query values before any handler renders them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        redacted = _LARK_CREDENTIAL_QUERY.sub(r"\1[REDACTED]", rendered)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


_lark_credential_filter = _RedactLarkCredentials()


def _route_lark_stdout_handlers_to_stderr() -> None:
    """Keep Lark's existing stdout handlers off the MCP protocol stream."""
    lark_logger = logging.getLogger("Lark")
    with _lark_logging_lock:
        if _lark_credential_filter not in lark_logger.filters:
            lark_logger.addFilter(_lark_credential_filter)
        for handler in lark_logger.handlers:
            if _lark_credential_filter not in handler.filters:
                handler.addFilter(_lark_credential_filter)
            if not isinstance(handler, logging.StreamHandler):
                continue
            if handler.stream is sys.stdout or handler.stream is sys.__stdout__:
                handler.setStream(sys.stderr)


def _import_lark() -> Any:
    global lark
    if lark is None:
        import lark_channel as _lark
        lark = _lark
    _route_lark_stdout_handlers_to_stderr()
    return lark


def _get_sdk_ws_client_module() -> Any:
    """Return the ``lark_channel.ws.client`` module (or an injected fake).

    Tests set ``_sdk_ws_client_module`` directly to a stand-in that exposes the
    same ``loop`` attribute contract as the real SDK module.
    """
    global _sdk_ws_client_module
    if _sdk_ws_client_module is None:
        import lark_channel.ws.client as _ws_client
        _sdk_ws_client_module = _ws_client
    return _sdk_ws_client_module


class _ThreadLocalLoop:
    """Per-thread proxy that stands in for ``lark_channel.ws.client.loop``.

    The SDK captures ``loop = asyncio.get_event_loop()`` at *import time* into a
    module global, and ``Client.start()`` calls ``loop.run_until_complete(...)``
    on that global. When the SDK is imported on the main MCP thread (while
    ``asyncio.run(serve())`` is active), the global captures the already-running
    main loop, so ``run_until_complete`` raises
    ``RuntimeError: This event loop is already running`` and inbound messages
    never arrive (issue #113).

    Setting a thread-current loop does not help: ``start()`` ignores it and uses
    the module global. So we replace the module global with this proxy, which
    forwards every attribute access to the *calling thread's* bound loop. Each WS
    thread binds its own fresh loop, so concurrent accounts never share or
    clobber a loop, even though the SDK exposes a single module-global name.

    The original loop is preserved as a fallback so any code path that touches
    the global from an unbound thread keeps working unchanged.
    """

    def __init__(self, fallback: Any) -> None:
        self._local = threading.local()
        self._fallback = fallback

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._local.loop = loop

    def unbind(self) -> None:
        self._local.loop = None

    def _resolve(self) -> Any:
        loop = getattr(self._local, "loop", None)
        return loop if loop is not None else self._fallback

    def __getattr__(self, name: str) -> Any:
        # __getattr__ only fires for names not found normally, so our own
        # attributes (_local, _fallback, bind, ...) are unaffected.
        return getattr(self._resolve(), name)


_install_lock = threading.Lock()


def _install_thread_local_sdk_loop(sdk: Any) -> _ThreadLocalLoop:
    """Ensure ``sdk.loop`` is a ``_ThreadLocalLoop`` and return it.

    Idempotent and thread-safe: the first caller swaps the module-global loop
    for a proxy (preserving the original as the fallback); subsequent callers
    reuse the same proxy. Returns the proxy so the caller can ``bind``/``unbind``
    its own thread loop.
    """
    with _install_lock:
        current = getattr(sdk, "loop", None)
        if isinstance(current, _ThreadLocalLoop):
            return current
        proxy = _ThreadLocalLoop(fallback=current)
        sdk.loop = proxy
        return proxy


@dataclass(frozen=True)
class FeishuInboundEvent:
    """SDK-normalized message paired with its complete Feishu event envelope."""

    message: Any
    feishu: dict[str, Any]


@dataclass(frozen=True)
class FeishuInboundChannelEvent:
    """One normalized non-message channel event with its raw envelope."""

    event_type: str
    event: Any


class FeishuAccount:
    """Manages a single Feishu (Lark) app credential — WS polling + REST sending."""

    def __init__(
        self,
        alias: str,
        app_id: str,
        app_secret: str,
        allowed_users: list[str] | None,
        on_message: Callable[[str, Any], None] | None = None,
        on_event: Callable[[str, Any], None] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.alias = alias
        self._app_id = app_id
        self._app_secret = app_secret
        self._allowed_users: set[str] | None = (
            set(allowed_users) if allowed_users else None
        )
        self._on_message = on_message
        self._on_event = on_event
        self._state_dir = state_dir

        self._ws_thread: threading.Thread | None = None
        self._ws_client: Any = None
        self._ws_event_loop: asyncio.AbstractEventLoop | None = None
        self._ws_loop_lock = threading.Lock()
        self._channel: Any = None
        self._rest_client: Any = None
        self._stop_event = threading.Event()
        self._bot_info: dict | None = None
        self._bot_open_id: str | None = None
        self._last_verified_at: str | None = None
        self._raw_envelopes: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._raw_envelopes_lock = threading.Lock()
        self._raw_envelopes_limit = 1000

        self._load_state()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Build REST client, register WS event handler, start polling thread."""
        if self._ws_thread is not None:
            return

        _lark = _import_lark()

        # Use the SDK channel facade for inbound normalization while retaining
        # the established low-level WS thread and generated REST actions. The
        # facade owns the dispatcher/normalizer; its transport is deliberately
        # not started here.
        security = _lark.SecurityConfig(mode="compat")
        self._channel = _lark.FeishuChannel(
            app_id=self._app_id,
            app_secret=self._app_secret,
            inbound=_lark.InboundConfig(
                expand_merge_forward=False,
                fetch_interactive_card=False,
                include_raw=True,
                emit_raw_events=True,
                reaction_notifications="all",
            ),
            # The account-level gate below owns LingTai's legacy allowlist and
            # exact DM/group routing semantics. Let ordinary group events reach
            # it after normalization so a cached bot identity still works when
            # the live identity lookup is temporarily unavailable. The SDK
            # continues to reject @all-only messages before this boundary.
            policy=_lark.PolicyConfig(
                require_mention=False,
                respond_to_mention_all=False,
            ),
            outbound=_lark.OutboundConfig(
                retry=_lark.RetryConfig(max_attempts=1),
            ),
            # LingTai persists one durable record per Feishu event. Disable the
            # SDK's default 600 ms text merge while retaining its per-chat
            # serialization queue; otherwise rapid messages lose their own
            # compound IDs and raw envelopes before manager persistence.
            safety=_lark.SafetyConfig(
                text_batch=_lark.TextBatchConfig(max_messages=1),
            ),
            security=security,
        )
        self._rest_client = self._channel.client

        # Preserve a previously resolved open_id for conservative group
        # mention gating if the identity endpoint is temporarily unavailable.
        self._bot_info = dict(self._bot_info or {})
        self._bot_info["app_id"] = self._app_id
        self._last_verified_at = datetime.now(timezone.utc).isoformat()
        self._save_state()

        def _handle_message(message: Any) -> None:
            try:
                self._process_event(message)
            except Exception as exc:
                logger.warning(
                    "Feishu event processing error (%s): %s", self.alias, exc
                )

        self._channel.on(_lark.Events.RAW, self._capture_raw_envelope)
        self._channel.on(_lark.Events.MESSAGE, _handle_message)
        for sdk_event, event_type in (
            (_lark.Events.REACTION, "reaction"),
            (_lark.Events.MESSAGE_READ, "message_read"),
            (_lark.Events.BOT_ADDED, "bot_added"),
            (_lark.Events.BOT_LEAVE, "bot_leave"),
        ):
            self._channel.on(
                sdk_event,
                lambda event, event_type=event_type: self._process_channel_event(
                    event_type, event
                ),
            )
        event_handler = self._channel.dispatcher

        # WebSocket client — start() blocks, run in daemon thread
        self._stop_event.clear()
        self._ws_client = _lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=_lark.LogLevel.INFO,
            security=security,
        )

        self._ws_thread = threading.Thread(
            target=self._ws_loop,
            daemon=True,
            name=f"feishu-ws-{self.alias}",
        )
        self._ws_thread.start()
        logger.info(
            "Feishu account '%s' started (app_id=%s)",
            self.alias,
            self._app_id,
        )

    def _ws_loop(self) -> None:
        """Run the blocking WebSocket client in a background thread.

        lark-channel-sdk captures ``loop = asyncio.get_event_loop()`` into a
        *module global* (``lark_channel.ws.client.loop``) at import time, and
        ``Client.start()`` calls ``loop.run_until_complete(...)`` on that global
        — it never re-reads the thread-current loop. Imported on the main MCP
        thread under ``asyncio.run(serve())``, that global is the already-running
        main loop, so ``run_until_complete`` raises
        ``RuntimeError: This event loop is already running`` and inbound messages
        are never delivered (issue #113).

        Fix: give this thread a fresh loop and make the SDK's module-global
        ``loop`` resolve to it for the duration of ``start()`` via a per-thread
        proxy (``_ThreadLocalLoop``). The proxy is installed once and shared, so
        multiple accounts each running their own WS thread get an independent
        loop without clobbering a single global. The thread binding is removed in
        ``finally`` and the fresh loop is closed.
        """
        sdk = _get_sdk_ws_client_module()
        proxy = _install_thread_local_sdk_loop(sdk)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        proxy.bind(loop)
        with self._ws_loop_lock:
            self._ws_event_loop = loop
        set_loop = getattr(self._ws_client, "_set_loop", None)
        if callable(set_loop):
            set_loop(loop)
        try:
            if self._stop_event.is_set():
                return
            self._resolve_bot_identity(loop)
            self._ws_client.start()
        except Exception as e:
            if not self._stop_event.is_set():
                logger.warning(
                    "Feishu WS client exited unexpectedly (%s): %s",
                    self.alias, e,
                )
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending and not loop.is_closed():
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            proxy.unbind()
            with self._ws_loop_lock:
                if self._ws_event_loop is loop:
                    self._ws_event_loop = None
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)

    def stop(self) -> None:
        """Signal the WebSocket thread to stop."""
        self._stop_event.set()
        with self._ws_loop_lock:
            loop = self._ws_event_loop
        if loop is not None and not loop.is_closed():
            disconnect = getattr(self._ws_client, "_disconnect", None)
            if callable(disconnect) and loop.is_running():
                try:
                    future = asyncio.run_coroutine_threadsafe(disconnect(), loop)
                    future.result(timeout=2.0)
                except Exception:
                    pass
            loop.call_soon_threadsafe(loop.stop)
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=5.0)
            if not self._ws_thread.is_alive():
                self._ws_thread = None
        if self._channel is not None:
            try:
                # The facade owns a normalization background loop but not this
                # account's WS client, so stopping it cannot duplicate the
                # transport shutdown above.
                self._channel.stop()
            except Exception as exc:
                logger.debug("Feishu channel cleanup failed (%s): %s", self.alias, exc)

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    def _resolve_bot_identity(self, loop: asyncio.AbstractEventLoop) -> None:
        """Resolve the bot open_id before WS delivery enables group @ gating."""
        if self._channel is None:
            return
        try:
            identity = loop.run_until_complete(self._channel.resolve_bot_identity())
        except Exception as exc:
            logger.warning(
                "Feishu bot identity resolution failed (%s); group messages "
                "remain mention-gated using cached identity when available: %s",
                self.alias,
                exc,
            )
            identity = None
        if identity is None:
            return
        self._bot_open_id = identity.open_id
        self._bot_info = {
            "app_id": identity.app_id or self._app_id,
            "open_id": identity.open_id,
            "user_id": identity.user_id,
            "name": identity.name,
        }
        self._last_verified_at = datetime.now(timezone.utc).isoformat()
        self._save_state()

    @staticmethod
    def _raw_message_id(envelope: dict[str, Any]) -> str:
        event = envelope.get("event")
        if not isinstance(event, dict):
            return ""
        message = event.get("message")
        if not isinstance(message, dict):
            return ""
        value = message.get("message_id")
        return value if isinstance(value, str) else ""

    def _capture_raw_envelope(self, envelope: dict[str, Any]) -> None:
        """Retain the SDK raw envelope until its normalized message is emitted."""
        if not isinstance(envelope, dict):
            return
        message_id = self._raw_message_id(envelope)
        if not message_id:
            return
        with self._raw_envelopes_lock:
            self._raw_envelopes[message_id] = envelope
            self._raw_envelopes.move_to_end(message_id)
            while len(self._raw_envelopes) > self._raw_envelopes_limit:
                self._raw_envelopes.popitem(last=False)

    def _take_raw_envelope(self, message_id: str) -> dict[str, Any]:
        with self._raw_envelopes_lock:
            return self._raw_envelopes.pop(message_id, {})

    def _process_event(self, message: Any) -> None:
        """Apply legacy allowlist + DM/group routing to one normalized message."""
        message_id = getattr(message, "id", "") or ""
        raw_envelope = self._take_raw_envelope(message_id)
        sender = getattr(message, "sender", None)
        open_id = getattr(sender, "open_id", "") if sender else ""

        if self._allowed_users is not None and open_id not in self._allowed_users:
            return

        conversation = getattr(message, "conversation", None)
        chat_type = getattr(conversation, "chat_type", "unknown")
        if chat_type != "p2p":
            mentioned_bot = bool(getattr(message, "mentioned_bot", False))
            if not mentioned_bot and self._bot_open_id:
                mentioned_bot = any(
                    getattr(mention, "open_id", None) == self._bot_open_id
                    for mention in (getattr(message, "mentions", None) or [])
                )
            if not mentioned_bot:
                return

        if self._on_message:
            self._on_message(
                self.alias,
                FeishuInboundEvent(
                    message=message,
                    feishu=raw_envelope,
                ),
            )

    def _process_channel_event(self, event_type: str, event: Any) -> None:
        """Apply the account allowlist before projecting a passive event."""
        actor = (
            getattr(event, "reader", None)
            if event_type == "message_read"
            else getattr(event, "operator", None)
        )
        actor_open_id = getattr(actor, "open_id", "") if actor else ""
        if (
            self._allowed_users is not None
            and actor_open_id not in self._allowed_users
        ):
            return
        if self._on_event:
            self._on_event(
                self.alias,
                FeishuInboundChannelEvent(event_type=event_type, event=event),
            )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send_text(
        self,
        receive_id: str,
        receive_id_type: str,
        text: str,
    ) -> dict:
        """Send a plain-text message. Returns created Message fields as dict."""
        from lark_channel.api.im.v1.model.create_message_request import (
            CreateMessageRequest,
        )
        from lark_channel.api.im.v1.model.create_message_request_body import (
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self._rest_client.im.v1.message.create(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu send_text failed: code={response.code} msg={response.msg}"
            )
        data = response.data
        return {
            "message_id": getattr(data, "message_id", ""),
            "chat_id": getattr(data, "chat_id", ""),
            "create_time": getattr(data, "create_time", ""),
        }

    def reply_text(self, message_id: str, text: str) -> dict:
        """Reply to a specific message by Feishu message_id."""
        from lark_channel.api.im.v1.model.reply_message_request import (
            ReplyMessageRequest,
        )
        from lark_channel.api.im.v1.model.reply_message_request_body import (
            ReplyMessageRequestBody,
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self._rest_client.im.v1.message.reply(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu reply_text failed: code={response.code} msg={response.msg}"
            )
        data = response.data
        return {
            "message_id": getattr(data, "message_id", ""),
            "chat_id": getattr(data, "chat_id", ""),
        }

    # ------------------------------------------------------------------
    # File download (voice, audio, images, documents)
    # ------------------------------------------------------------------

    def get_message_resource(
        self,
        message_id: str,
        file_key: str,
        resource_type: str = "file",
    ) -> tuple[str, bytes]:
        """Download a resource file from a message.

        Args:
            message_id: Feishu message ID (om_xxx).
            file_key: The file_key from the message content.
            resource_type: Logical resource type: ``image``, ``file``,
                ``audio``, ``video``, or ``sticker``. Feishu's download API
                accepts only ``image`` and ``file``; the adapter maps the
                logical type without changing the persisted descriptor.

        Returns:
            (filename, content_bytes) tuple.
        """
        from lark_channel.api.im.v1.model.get_message_resource_request import (
            GetMessageResourceRequest,
        )

        api_resource_type = (
            "image" if resource_type in {"image", "sticker"} else "file"
        )
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(api_resource_type)
            .build()
        )
        response = self._rest_client.im.v1.message_resource.get(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu get_message_resource failed: "
                f"code={response.code} msg={response.msg}"
            )
        filename = response.file_name or ""
        content = response.file.read()
        return filename, content

    # ------------------------------------------------------------------
    # Reactions (emoji responses on messages)
    # ------------------------------------------------------------------

    def add_reaction(self, message_id: str, emoji_type: str) -> bool:
        """Add an emoji reaction to a message.

        Args:
            message_id: Feishu message ID (om_xxx).
            emoji_type: Emoji type string (e.g. "OK", "THUMBSUP", "SMILE").

        Returns:
            True on success.
        """
        from lark_channel.api.im.v1.model.create_message_reaction_request import (
            CreateMessageReactionRequest,
        )
        from lark_channel.api.im.v1.model.create_message_reaction_request_body import (
            CreateMessageReactionRequestBody,
        )
        from lark_channel.api.im.v1.model.emoji import (
            Emoji,
        )

        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(
                    Emoji.builder().emoji_type(emoji_type).build()
                )
                .build()
            )
            .build()
        )
        response = self._rest_client.im.v1.message_reaction.create(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu add_reaction failed: "
                f"code={response.code} msg={response.msg}"
            )
        return True

    # ------------------------------------------------------------------
    # Message editing & deletion
    # ------------------------------------------------------------------

    def update_message(self, message_id: str, text: str) -> dict:
        """Edit a sent text message with new content.

        Uses the PATCH endpoint to update message content.
        Only text messages can be edited this way.

        Args:
            message_id: Feishu message ID (om_xxx).
            text: New text content.

        Returns:
            Response dict (empty on success since PATCH returns no body).
        """
        from lark_channel.api.im.v1.model.patch_message_request import (
            PatchMessageRequest,
        )
        from lark_channel.api.im.v1.model.patch_message_request_body import (
            PatchMessageRequestBody,
        )

        request = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        response = self._rest_client.im.v1.message.patch(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu update_message failed: "
                f"code={response.code} msg={response.msg}"
            )
        return {}

    def delete_message(self, message_id: str) -> bool:
        """Delete a message sent by the bot.

        Args:
            message_id: Feishu message ID (om_xxx).

        Returns:
            True on success.
        """
        from lark_channel.api.im.v1.model.delete_message_request import (
            DeleteMessageRequest,
        )

        request = (
            DeleteMessageRequest.builder()
            .message_id(message_id)
            .build()
        )
        response = self._rest_client.im.v1.message.delete(request)
        if not response.success():
            raise RuntimeError(
                f"Feishu delete_message failed: "
                f"code={response.code} msg={response.msg}"
            )
        return True

    @property
    def allowed_users_count(self) -> int | None:
        """Return the allow-list size without exposing user IDs."""
        if self._allowed_users is None:
            return None
        return len(self._allowed_users)

    def public_identity(self) -> dict[str, Any]:
        """Non-secret Feishu app identity observed from config/state.

        This intentionally exposes only stable public app metadata. It never
        includes app secrets, individual open_ids/user_ids, chat IDs, messages,
        or webhook/encryption secrets.
        """
        info = self._bot_info or {}
        identity = {
            "alias": self.alias,
            "app_id": info.get("app_id") or self._app_id,
            "last_verified_at": self._last_verified_at,
        }
        return {k: v for k, v in identity.items() if v is not None}

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _state_path(self) -> Path | None:
        if self._state_dir is None:
            return None
        return self._state_dir / "state.json"

    def _load_state(self) -> None:
        path = self._state_path()
        if path is None or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._bot_info = data.get("bot_info")
            if isinstance(self._bot_info, dict):
                open_id = self._bot_info.get("open_id")
                self._bot_open_id = open_id if isinstance(open_id, str) else None
            self._last_verified_at = data.get("last_verified_at")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load Feishu state: %s", e)

    def _save_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "bot_info": self._bot_info,
            "last_verified_at": self._last_verified_at,
        }
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
