"""TelegramAccount — single bot token, HTTP calls, polling thread.

One daemon thread per account runs the getUpdates long-poll loop.
Constructor stores config only — no threads, no API calls.
start() calls getMe and spawns the polling thread.
stop() signals the thread to stop and joins it.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from lingtai.mcp_servers.local_commands import (
    DEFAULT_COMMANDS,
    HIDDEN_COMMANDS,
    LocalCommandCore,
    TaskCardSettingsPort,
)

from . import updates as tg_updates

logger = logging.getLogger(__name__)

# httpx is lazy-imported to keep the module importable without the optional dep.
# Actual import happens in _ensure_client() on first API call.
httpx: Any = None

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_FILE_BASE = "https://api.telegram.org/file/bot{token}/{file_path}"


class TelegramRateLimitError(RuntimeError):
    """Telegram rejected one request with HTTP 429.

    ``retry_after`` is copied only when Telegram supplied a valid integer number
    of seconds.  Missing or malformed provider metadata stays ``None``; callers
    must never invent a cooldown or schedule an automatic retry.
    """

    error_code = 429

    def __init__(self, retry_after: int | None) -> None:
        self.retry_after = retry_after
        message = "Telegram API rate limited"
        if retry_after is not None:
            message += f"; retry after {retry_after} seconds"
        super().__init__(message)


def _retry_after_from_payload(payload: Any) -> int | None:
    """Return Telegram's valid integer cooldown without coercion or defaults."""
    if not isinstance(payload, dict):
        return None
    parameters = payload.get("parameters")
    if not isinstance(parameters, dict):
        return None
    retry_after = parameters.get("retry_after")
    if type(retry_after) is not int or retry_after < 0:
        return None
    return retry_after


# ---------------------------------------------------------------------------
# Inline keyboard builders
# ---------------------------------------------------------------------------
#
# Telegram's reply_markup for inline keyboards is shaped as
#   {"inline_keyboard": [[{"text": "...", "callback_data": "..."}, ...], ...]}
# i.e. a list of rows, each row a list of buttons. callback_data is what the
# Bot API sends back in the callback_query.data field when the user taps the
# button — keep it short (Telegram caps it at 64 bytes) and dispatch on it
# from the agent side.
#
# These helpers live in account.py rather than a separate keyboards.py
# because reply_markup is the input shape consumed by send_message/edit_message
# right above; keeping them adjacent makes the relationship obvious. They are
# pure dict builders (no Telegram API calls) so they're safe to call without
# a running TelegramAccount.


def inline_keyboard_yes_no(
    yes_text: str = "Yes",
    no_text: str = "No",
    yes_data: str = "yes",
    no_data: str = "no",
) -> dict:
    """Two-button inline keyboard: [yes] [no] in a single row.

    Returned dict is suitable to pass as ``reply_markup`` to
    ``send_message`` / ``edit_message`` (or as the ``reply_markup``
    arg of the ``telegram(action="send")`` MCP tool).
    """
    return {
        "inline_keyboard": [[
            {"text": yes_text, "callback_data": yes_data},
            {"text": no_text, "callback_data": no_data},
        ]],
    }


def inline_keyboard_approve_reject(
    approve_text: str = "Approve",
    reject_text: str = "Reject",
    approve_data: str = "approve",
    reject_data: str = "reject",
) -> dict:
    """Two-button inline keyboard: [approve] [reject] in a single row."""
    return {
        "inline_keyboard": [[
            {"text": approve_text, "callback_data": approve_data},
            {"text": reject_text, "callback_data": reject_data},
        ]],
    }


def inline_keyboard_options(
    options: list[dict],
    columns: int = 1,
) -> dict:
    """N-button inline keyboard from a list of ``{"text", "data"}`` dicts.

    ``options`` items must each contain ``text`` (label shown on button) and
    ``data`` (the ``callback_data`` echoed back when tapped). Buttons are
    laid out left-to-right, top-to-bottom in rows of ``columns`` (default 1
    — one button per row, which is the conventional Telegram option-list
    style). ``columns`` must be >= 1.

    Example::

        inline_keyboard_options([
            {"text": "Tokyo",   "data": "city:tokyo"},
            {"text": "Osaka",   "data": "city:osaka"},
            {"text": "Kyoto",   "data": "city:kyoto"},
        ])
    """
    if columns < 1:
        raise ValueError("columns must be >= 1")
    if not options:
        raise ValueError("options must not be empty")

    buttons = []
    for opt in options:
        if "text" not in opt or "data" not in opt:
            raise ValueError(
                "each option must have 'text' and 'data' keys; got "
                f"{sorted(opt.keys())!r}"
            )
        buttons.append({"text": opt["text"], "callback_data": opt["data"]})

    rows = [buttons[i:i + columns] for i in range(0, len(buttons), columns)]
    return {"inline_keyboard": rows}


class TelegramAccount:
    """Manages a single Telegram bot token — polling + sending."""

    def __init__(
        self,
        alias: str,
        bot_token: str,
        allowed_users: list[int] | None,
        poll_interval: float = 1.0,
        on_message: Callable[[str, dict], None] | None = None,
        state_dir: Path | None = None,
        commands: list[dict[str, str]] | None = None,
        taskcard_enabled: Callable[[], bool] | None = None,
        set_taskcard_enabled: Callable[[bool], None] | None = None,
        taskcard_normal_rows: Callable[[], int] | None = None,
        set_taskcard_normal_rows: Callable[[int], None] | None = None,
        local_command_core: LocalCommandCore | None = None,
    ) -> None:
        self.alias = alias
        self._bot_token = bot_token
        self._allowed_users = set(allowed_users) if allowed_users else None
        self._poll_interval = poll_interval
        self._on_message = on_message
        self._state_dir = state_dir
        # Injected service-owned accessors keep the setting agent-wide even when
        # this account is one of several Telegram bots/chats.
        self._taskcard_enabled = taskcard_enabled or (lambda: True)
        self._set_taskcard_enabled = set_taskcard_enabled
        self._taskcard_normal_rows = taskcard_normal_rows or (lambda: 1)
        self._set_taskcard_normal_rows = set_taskcard_normal_rows
        self._local_commands = local_command_core or LocalCommandCore()
        # If commands is None, fall back to DEFAULT_COMMANDS at registration
        # time. An explicit empty list means "register no commands" and is
        # respected (Telegram clears the menu).
        self._commands: list[dict[str, str]] | None = commands

        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_update_id: int = 0
        self._bot_info: dict | None = None
        self._last_verified_at: str | None = None
        self._client: httpx.Client | None = None

        # Resident Task Card message id per chat: {chat_id_str: compound_id}.
        # Exactly one card stays resident per (account, chat) and ordinary frames
        # edit that id in place. Persisted in state.json so the identity survives a
        # refresh; only confirmed edit-impossible recovery replaces it and retires
        # the tracked stale id. ``_state_lock`` serializes the
        # read-modify-write of state.json across the poll thread and the
        # orchestrating turn thread.
        self._task_cards: dict[str, str] = {}
        # Highest Telegram message_id observed per chat (Jason #5272/#5273/#5275).
        # Telegram message_ids are monotonic per chat, so this high-water mark is
        # "the chat's last message id": bumped by every inbound message the poll
        # loop sees and every outbound message this account sends, but never by an
        # edit or delete (which add no new bottom message). The Task Card manager
        # reads it to decide whether the resident card is still the last message.
        # It is a deliberately EPHEMERAL observation cache — not persisted — so a
        # refresh starts "unknown" (``None``) until the next observed message, and
        # the manager treats unknown conservatively (edit in place, never delete).
        # Keyed by the integer chat id and guarded by ``_state_lock``.
        self._last_message_ids: dict[int, int] = {}
        # Re-entrant so a card mutator can hold the lock across its
        # read-modify-write and the nested ``_save_state`` snapshot.
        self._state_lock = threading.RLock()

        self._load_state()

    # -- API helpers ---------------------------------------------------------

    def _api_url(self, method: str) -> str:
        return _API_BASE.format(token=self._bot_token, method=method)

    def _file_url(self, file_path: str) -> str:
        return _FILE_BASE.format(token=self._bot_token, file_path=file_path)

    def _ensure_client(self) -> None:
        """Lazy-import httpx and create client on first use."""
        global httpx
        if httpx is None or isinstance(httpx, type(None)):
            import httpx as _httpx
            httpx = _httpx
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))

    def _request(self, method: str, **kwargs: Any) -> dict:
        """Make one Bot API request. Returns the result or raises immediately."""
        self._ensure_client()
        resp = self._client.post(self._api_url(method), **kwargs)
        if resp.status_code == 429:
            try:
                data = resp.json()
            except Exception:
                data = None
            retry_after = _retry_after_from_payload(data)
            if retry_after is None:
                logger.warning("Telegram rate limited the request without a valid retry_after")
            else:
                logger.warning(
                    "Telegram rate limited the request; retry_after=%d seconds",
                    retry_after,
                )
            raise TelegramRateLimitError(retry_after)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data.get('description', data)}")
        return data.get("result", {})

    # -- Lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Call getMe, register slash commands, start polling thread."""
        if self._poll_thread is not None:
            return
        self._ensure_client()
        self._bot_info = self._request("getMe")
        self._last_verified_at = datetime.now(timezone.utc).isoformat()
        self._save_state()
        self._register_commands()
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True,
            name=f"telegram-poll-{self.alias}",
        )
        self._poll_thread.start()
        logger.info("Telegram account '%s' started (@%s)",
                     self.alias, self._bot_info.get("username", "?"))

    def _register_commands(self) -> None:
        """Register slash commands with @BotFather via setMyCommands.

        Best-effort: any failure (network, API error, etc.) is logged at
        WARNING level but does not block startup. If the configured list is
        None, falls back to DEFAULT_COMMANDS. An explicit empty list is
        passed through as-is (Telegram interprets that as "clear the menu").
        """
        commands = self._commands if self._commands is not None else DEFAULT_COMMANDS
        try:
            self._request("setMyCommands", json={"commands": commands})
            logger.info(
                "Telegram account '%s' registered %d slash command(s)",
                self.alias, len(commands),
            )
        except Exception as e:
            logger.warning(
                "Telegram account '%s' setMyCommands failed (continuing): %s",
                self.alias, e,
            )

    def stop(self) -> None:
        """Signal polling thread to stop and join it."""
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
            self._poll_thread = None
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- Polling -------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Main loop — getUpdates with long poll, dispatch to on_message."""
        while not self._stop_event.is_set():
            try:
                updates = self._request(
                    "getUpdates",
                    json={
                        "offset": self._last_update_id + 1,
                        "timeout": 30,
                        # Subscribe to every catalogued Update branch at the
                        # acquisition boundary. Bot API semantics: an omitted
                        # allowed_updates reuses the previous server-side
                        # setting, and the default set excludes branches like
                        # chat_member / message_reaction / message_reaction_count
                        # — so without this explicit list those audited
                        # branches would never reach the lossless envelope.
                        # Unknown *future* branches are still preserved when
                        # delivered (open fallback); Telegram only sends
                        # branches it knows this list requested.
                        "allowed_updates": list(tg_updates.KNOWN_UPDATE_BRANCHES),
                    },
                )
                for update in updates:
                    self._process_update(update)
            except Exception as e:
                logger.warning("Telegram poll error (%s): %s", self.alias, e)
                # Backoff before retry
                if self._stop_event.wait(timeout=5.0):
                    return
                continue
            # Brief pause between poll cycles
            if self._stop_event.wait(timeout=self._poll_interval):
                return

    def _process_update(self, update: dict) -> None:
        """Process a single update — filter, dispatch, track offset."""
        update_id = update.get("update_id", 0)
        if update_id > self._last_update_id:
            self._last_update_id = update_id
            self._save_state()

        if "message" in update:
            # A plain message is a new bottom message in the chat; record it so a
            # resident Task Card sent earlier is now known to be superseded. Done
            # before allow-list filtering because the message exists in the chat
            # regardless of whether this bot will act on it, and recording only an
            # integer id leaks nothing. Callback queries (no new message) and
            # edited messages (older id) are intentionally excluded.
            msg = update["message"]
            self._note_chat_message_id(
                msg.get("chat", {}).get("id"), msg.get("message_id"))
        elif "callback_query" in update:
            # Auto-answer callback query to dismiss spinner
            cq_id = update["callback_query"].get("id")
            if cq_id:
                try:
                    self._request("answerCallbackQuery", json={"callback_query_id": cq_id})
                except Exception:
                    pass

        # Admission policy: every Update branch resolves an explicit actor
        # (see lingtai.mcp_servers.telegram.updates). A resolvable triggering
        # user is enforced against allowed_users; chat-actor / actorless /
        # unknown-branch events are admitted under the bot-placement trust
        # boundary with their uncertainty recorded on the envelope.
        actor = tg_updates.resolve_update_actor(update)
        if not tg_updates.is_admitted(actor, self._allowed_users):
            return

        # Intercept slash commands before they reach the agent
        if self._handle_slash_command(update):
            return

        if self._on_message:
            self._on_message(self.alias, update)

    def _handle_slash_command(self, update: dict) -> bool:
        """Handle slash commands locally (no LLM call). Returns True if handled."""
        # Extract text from any non-edit human Message-typed branch (message,
        # business_message, guest_message) via the shared classification, so
        # local commands stay local on every admitted human-content branch.
        # Runs strictly after the allowlist gate in _process_update.
        text = ""
        chat_id = None
        message_id = None
        branch, branch_obj = tg_updates.classify_update(update)
        if branch in tg_updates.LOCAL_COMMAND_BRANCHES and isinstance(branch_obj, dict):
            text = (branch_obj.get("text") or "").strip()
            chat_id = (branch_obj.get("chat") or {}).get("id")
        elif "callback_query" in update:
            text = (update["callback_query"].get("data") or "").strip()
            cq_msg = update["callback_query"].get("message", {})
            chat_id = cq_msg.get("chat", {}).get("id")
            message_id = cq_msg.get("message_id")
        # Handle kb:* callback data from /kanban inline buttons
        if text.startswith("kb:") and chat_id:
            kb = text[3:]  # strip "kb:" prefix
            if kb == "all":
                self._cmd_kanban(chat_id, text="/kanban all", message_id=message_id)
            elif kb in ("1", "2", "3", "4", "5", "6", "7"):
                self._cmd_kanban_layer(chat_id, message_id, kb)
            else:
                # kb:overview, kb:back, or unknown — back to the overview
                self._cmd_kanban(chat_id, text="", message_id=message_id)
            return True
        # Handle sys:* callback data from /system inline buttons
        if text.startswith("sys:") and chat_id:
            file_name = text[4:]  # strip "sys:" prefix
            self._cmd_system(chat_id, f"/system {file_name}")
            return True

        if not text.startswith("/") or not chat_id:
            return False

        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/kanban":
            self._cmd_kanban(chat_id, text)
            return True
        if cmd == "/status":
            self._cmd_status(chat_id)
            return True
        if cmd == "/help":
            self._cmd_help(chat_id)
            return True
        if cmd == "/taskcard":
            self._cmd_taskcard(chat_id, text)
            return True
        if cmd == "/refresh":
            self._cmd_refresh(chat_id)
            return True
        if cmd == "/sleep":
            self._cmd_sleep(chat_id)
            return True
        if cmd == "/system":
            self._cmd_system(chat_id, text)
            return True
        if cmd == "/brief":
            self._cmd_brief(chat_id)
            return True
        if cmd == "/clear":
            self._cmd_clear(chat_id)
            return True
        # Unknown slash command — let it pass through to agent
        return False

    def _cmd_taskcard(self, chat_id: int, text: str) -> None:
        """Report or configure agent-wide Telegram Task Card presentation."""
        usage = "❌ Usage: /taskcard on | /taskcard off | /taskcard N (1-10)"
        result = self._local_commands.apply_taskcard(
            text,
            TaskCardSettingsPort(
                enabled=self._taskcard_enabled,
                set_enabled=self._set_taskcard_enabled,
                normal_rows=self._taskcard_normal_rows,
                set_normal_rows=self._set_taskcard_normal_rows,
            ),
        )
        if result.status == "usage":
            self.send_message(chat_id, usage)
            return
        if result.status == "update_failed":
            logger.warning(
                "Telegram account '%s' could not persist taskcard setting",
                self.alias,
            )
            self.send_message(
                chat_id,
                "⚠️ Could not update taskcard; the previous setting is unchanged.",
            )
            return

        enabled = bool(result.enabled)
        normal_rows = result.normal_rows
        if enabled:
            description = (
                "automatic and programmable Task Cards may be sent for this agent."
            )
        else:
            description = (
                "automatic and programmable Task Cards are hidden for this agent; "
                "internal mechanics still run."
            )
        self.send_message(
            chat_id,
            f"📋 taskcard: {enabled} · normal rows: {normal_rows} — {description}\n"
            "Usage: /taskcard on | /taskcard off | /taskcard N (1-10)",
        )

    def _collect_kanban_data(self) -> dict | None:
        """Read the shared channel-neutral dashboard snapshot."""
        return self._local_commands.collect_kanban_data()

    def _kanban_layer_text(self, data: dict, layer_key: str) -> str:
        """Build one kanban layer's markdown text from ``_collect_kanban_data``."""
        fmt = data["fmt"]
        fmt_duration = data["fmt_duration"]
        fmt_time = data["fmt_time"]
        age_since = data["age_since"]
        join_limited = data["join_limited"]
        current_agent = data["current_agent"]
        lines: list[str] = []

        if layer_key == "1":
            # Layer 1: Identity / lifecycle
            lines.append("🧭 *Layer 1 · Identity & Lifecycle*")
            display_name = f"`{current_agent}`"
            if data["nickname"]:
                display_name += f" ({data['nickname']})"
            lines.append(f"  Agent: {display_name}")
            lines.append(f"  ID: `{str(data['agent_id'])[-12:]}`")
            lines.append(f"  Born: {fmt_time(data['created_at'])} ({age_since(data['created_at'])} ago)")
            lines.append(f"  Session: {fmt_time(data['started_at'])} (uptime {fmt_duration(data['uptime'])})")
            lines.append(f"  Molts: {data['molt_count']}  |  Summaries: {data['summaries_count']}")
            karma = "✅" if data["admin"].get("karma") else "❌"
            nirvana = "✅" if data["admin"].get("nirvana") else "❌"
            lines.append(f"  Admin: karma {karma} / nirvana {nirvana}")
        elif layer_key == "2":
            # Layer 2: Model and presets
            lines.append("🤖 *Layer 2 · Model & Presets*")
            lines.append(f"  Active: `{data['current_model']}` ({data['current_provider']})")
            if data["active_preset_path"]:
                lines.append(f"  Preset: `{Path(data['active_preset_path']).expanduser().name}`")
            if data["default_preset_path"]:
                lines.append(f"  Default: `{Path(data['default_preset_path']).expanduser().name}`")
            if data["preset_models"]:
                for pm in data["preset_models"][:8]:
                    marker = " ←" if pm["active"] else ""
                    bullet = "→" if pm["active"] else "•"
                    lines.append(f"  {bullet} `{pm['model']}` ({pm['provider']}){marker}")
                if len(data["preset_models"]) > 8:
                    lines.append(f"  … {len(data['preset_models']) - 8} more preset(s)")
        elif layer_key == "3":
            # Layer 3: Live runtime
            lines.append("🖥 *Layer 3 · Live Runtime*")
            state_emoji = {
                "active": "🟢", "idle": "🟡", "asleep": "😴",
                "suspended": "🔴", "stuck": "⚠️",
            }.get(str(data["agent_state"]).lower(), "❓")
            lines.append(f"  State: {state_emoji} {data['agent_state']}")
            usage_pct = data["ctx"].get("usage_pct", 0)
            total_t = data["ctx"].get("total_tokens", 0)
            window = data["ctx"].get("window_size", data["context_limit"])
            sys_t = data["ctx"].get("system_tokens", 0)
            hist_t = data["ctx"].get("history_tokens", 0)
            lines.append(f"  Context: {fmt(total_t)}/{fmt(window)} ({usage_pct:.1f}%)")
            lines.append(f"    ↳ fixed/system={fmt(sys_t)}  history={fmt(hist_t)}")
        elif layer_key == "4":
            # Layer 4: Mind / durable stores
            lines.append("🧠 *Layer 4 · Mind & Memory*")
            soul_text = f"{int(float(data['soul_delay']) // 60)}m" if data["soul_delay"] else "off"
            lines.append(f"  Language: {data['language']}  |  Soul delay: {soul_text}")
            store_parts = [
                f"knowledge={data['knowledge_count']}",
                f"custom skills={data['skill_count']}",
                f"delegates={data['delegate_count']}",
            ]
            if data["codex_count"]:
                store_parts.append(f"codex={data['codex_count']}")
            lines.append(f"  Stores: {' | '.join(store_parts)}")
            if data["capability_names"]:
                lines.append(f"  Capabilities ({len(data['capability_names'])}): {join_limited([f'`{c}`' for c in data['capability_names']])}")
        elif layer_key == "5":
            # Layer 5: Token usage
            lines.append("📈 *Layer 5 · Token Usage*")
            for name, agent_data in sorted(data["all_agents"].items()):
                if agent_data["calls"] == 0:
                    continue
                marker = " 👈" if name == current_agent else ""
                total = agent_data["input"] + agent_data["output"] + agent_data["thinking"]
                cached = agent_data.get("cached", 0)
                cache_rate = (cached / agent_data["input"] * 100) if agent_data["input"] else 0
                lines.append(f"  *{name}*: {fmt(total)} tokens ({agent_data['calls']} calls){marker}")
                lines.append(
                    f"    ↳ in={fmt(agent_data['input'])} out={fmt(agent_data['output'])} "
                    f"think={fmt(agent_data['thinking'])} cache={fmt(cached)} "
                    f"({cache_rate:.0f}%)"
                )
            lines.append(f"  *Total*: {fmt(data['grand_total'])} tokens ({data['total_api_calls']} calls)")
            if data["total_input"]:
                lines.append(
                    f"  *Cache rate*: {data['total_cached'] / data['total_input'] * 100:.0f}% "
                    f"({fmt(data['total_cached'])} cached of {fmt(data['total_input'])} input)"
                )
        elif layer_key == "6":
            # Layer 6: Network topology
            lines.append("🌐 *Layer 6 · Network*")
            agent_count = len(data["all_agents"])
            agent_names = [f"`{name}`" for name in sorted(data["all_agents"].keys())]
            lines.append(f"  Agents ({agent_count}): {join_limited(agent_names)}")
            for name, agent_data in sorted(data["all_agents"].items()):
                if name == current_agent:
                    continue
                total = agent_data["input"] + agent_data["output"] + agent_data["thinking"]
                lines.append(
                    f"  • `{name}`: {agent_data['state']}, molts={agent_data['molt_count']}, "
                    f"model=`{agent_data['model']}`, tokens={fmt(total)}"
                )
        elif layer_key == "7":
            # Layer 7: Addons and config
            lines.append("🔌 *Layer 7 · Addons & Config*")
            if data["addon_status"]:
                addon_parts = []
                for addon, ok in data["addon_status"].items():
                    emoji = "✅" if ok else "⬜"
                    addon_parts.append(f"{emoji}{addon}")
                lines.append(f"  Addons: {' | '.join(addon_parts)}")
            lines.append(f"  Context limit: {fmt(data['context_limit'])}")
            lines.append(f"  Max turns: {data['manifest'].get('max_turns', '?')}")
        else:
            lines.append(f"❓ *Layer {layer_key} · Unknown*")

        return "\n".join(lines)

    def _cmd_kanban(self, chat_id: int, text: str = "", message_id: int | None = None) -> None:
        """Handle /kanban — show a layered agent dashboard. Pure filesystem read, no LLM.

        With no args (or a kb: callback) shows a compact overview with inline
        keyboard to drill into each layer; "/kanban all" sends the full layered
        message in one shot.
        """
        data = self._collect_kanban_data()
        if data is None:
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot read data.")
            return

        current_agent = data["current_agent"]
        fmt = data["fmt"]
        layers = {str(k): self._kanban_layer_text(data, str(k)) for k in range(1, 8)}

        if "all" in (text or "").lower():
            # Full layered message: title + all 7 layers + quick-commands footer
            full_text = (
                f"📊 *Kanban — {current_agent}*\n\n"
                + "\n\n".join(layers[str(k)] for k in range(1, 8))
                + "\n\n⚡ /help /kanban /taskcard /refresh /sleep /clear"
            )
            self.send_message(chat_id, full_text, parse_mode="Markdown")
            return

        # Overview / drill-down: compact dashboard + layer buttons
        state_emoji = {
            "active": "🟢", "idle": "🟡", "asleep": "😴",
            "suspended": "🔴", "stuck": "⚠️",
        }.get(str(data["agent_state"]).lower(), "❓")
        usage_pct = data["ctx"].get("usage_pct", 0)
        total_t = data["ctx"].get("total_tokens", 0)
        window = data["ctx"].get("window_size", data["context_limit"])
        total_in = data["total_input"]
        total_out = data["total_output"]
        total_think = data["total_thinking"]
        total_cached = data["total_cached"]
        total_calls = data["total_api_calls"]
        cache_rate = (total_cached / total_in * 100) if total_in else 0
        addon_parts = [
            ("✅" if ok else "⬜") + name
            for name, ok in sorted(data["addon_status"].items())
        ]
        addon_line = " ".join(addon_parts) if addon_parts else "none"
        overview_text = (
            f"📊 *Kanban — {current_agent}*\n\n"
            f"{state_emoji} {data['agent_state']} · {data['current_model']} ({data['current_provider']})\n"
            f"Context: {fmt(total_t)}/{fmt(window)} ({usage_pct:.1f}%) · "
            f"Uptime: {data['fmt_duration'](data['uptime'])} · Molts: {data['molt_count']}\n\n"
            f"📈 *Tokens* ({total_calls} calls)\n"
            f"  Total: {fmt(data['grand_total'])} · In: {fmt(total_in)} · Out: {fmt(total_out)} · "
            f"Think: {fmt(total_think)}\n"
            f"  Cached: {fmt(total_cached)} ({cache_rate:.0f}% cache rate)\n\n"
            f"🧠 Mind: {data['knowledge_count']} knowledge · {data['skill_count']} skills · "
            f"{data['delegate_count']} delegates\n"
            f"🔌 Addons: {addon_line}\n\n"
            "Tap a layer to drill in:"
        )
        keyboard = inline_keyboard_options(
            [
                {"text": "1 · Identity", "data": "kb:1"},
                {"text": "2 · Model", "data": "kb:2"},
                {"text": "3 · Runtime", "data": "kb:3"},
                {"text": "4 · Mind", "data": "kb:4"},
                {"text": "5 · Tokens", "data": "kb:5"},
                {"text": "6 · Network", "data": "kb:6"},
                {"text": "7 · Addons", "data": "kb:7"},
                {"text": "All", "data": "kb:all"},
            ],
            columns=2,
        )
        if message_id is not None:
            self.edit_message(
                chat_id, message_id, overview_text,
                reply_markup=keyboard, parse_mode="Markdown",
            )
        else:
            self.send_message(
                chat_id, overview_text,
                reply_markup=keyboard, parse_mode="Markdown",
            )

    def _cmd_kanban_layer(self, chat_id: int, message_id: int, layer_key: str) -> None:
        """Handle kanban drill-down — edit the overview message in place to one layer."""
        if layer_key not in ("1", "2", "3", "4", "5", "6", "7") or message_id is None:
            # Unknown layer or missing message — fall back to the overview
            self._cmd_kanban(chat_id, text="", message_id=message_id)
            return
        data = self._collect_kanban_data()
        if data is None:
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot read data.")
            return
        layer_text = self._kanban_layer_text(data, layer_key)
        keyboard = inline_keyboard_options(
            [{"text": "← Back", "data": "kb:back"}], columns=1,
        )
        self.edit_message(
            chat_id, message_id, layer_text,
            reply_markup=keyboard, parse_mode="Markdown",
        )

    def _cmd_refresh(self, chat_id: int) -> None:
        """Handle /refresh — trigger agent refresh via signal file. No LLM call."""
        result = self._local_commands.send_signal("refresh", source="telegram")
        if result.status == "agent_dir_missing":
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot send refresh signal.")
            return
        if result.status == "pending":
            self.send_message(chat_id, "⏳ Refresh signal already pending...")
            return
        if result.status == "sent":
            self.send_message(chat_id, "🔄 Refresh signal sent — agent will restart momentarily.")
            return
        self.send_message(chat_id, f"⚠️ Failed to write refresh signal: {result.error}")

    def _cmd_sleep(self, chat_id: int) -> None:
        """Handle /sleep — put agent to sleep via signal file. No LLM call."""
        result = self._local_commands.send_signal("sleep", source="telegram")
        if result.status == "agent_dir_missing":
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot send sleep signal.")
            return
        if result.status == "sent":
            self.send_message(chat_id, "😴 Sleep signal sent — agent is going to sleep. Message me to wake up.")
            return
        self.send_message(chat_id, f"⚠️ Failed to write sleep signal: {result.error}")

    def _cmd_status(self, chat_id: int) -> None:
        """Handle /status — compact one-message agent status. No LLM call."""
        data = self._collect_kanban_data()
        if data is None:
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot read data.")
            return

        fmt = data["fmt"]
        fmt_duration = data["fmt_duration"]
        state_emoji = {
            "active": "🟢", "idle": "🟡", "asleep": "😴",
            "suspended": "🔴", "stuck": "⚠️",
        }.get(str(data["agent_state"]).lower(), "❓")
        usage_pct = data["ctx"].get("usage_pct", 0)
        total_t = data["ctx"].get("total_tokens", 0)
        window = data["ctx"].get("window_size", data["context_limit"])
        text = (
            f"{state_emoji} *{data['current_agent']}* — {data['agent_state']}\n"
            f"Model: {data['current_model']} ({data['current_provider']})\n"
            f"Context: {fmt(total_t)}/{fmt(window)} ({usage_pct:.1f}%)\n"
            f"Molts: {data['molt_count']} · Uptime: {fmt_duration(data['uptime'])}"
        )
        self.send_message(chat_id, text, parse_mode="Markdown")

    def _cmd_help(self, chat_id: int) -> None:
        """Handle /help — list available commands. No LLM call."""
        lines = ["🤖 *Available commands*"]
        for cmd_info in DEFAULT_COMMANDS:
            name = cmd_info.get("command", "?")
            desc = cmd_info.get("description", "")
            lines.append(f"/{name} — {desc} (local)")
        lines.append("")
        lines.append("Hidden (still work if typed):")
        for cmd_info in HIDDEN_COMMANDS:
            name = cmd_info.get("command", "?")
            desc = cmd_info.get("description", "")
            lines.append(f"/{name} — {desc}")
        lines.append("")
        lines.append("Other messages are sent to the agent as normal chat.")
        self.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

    def _cmd_brief(self, chat_id: int) -> None:
        """Handle /brief — show the agent's briefing. Pure filesystem read, no LLM."""
        result = self._local_commands.read_brief()
        if result.status == "agent_dir_missing":
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot read briefing.")
            return
        if result.status == "not_found" or result.content is None:
            self.send_message(chat_id, "📄 No brief found — ask the operator to set one up.")
            return

        # Send the brief, splitting at 4000 chars per message (mirror _cmd_system)
        MAX_MSG = 4000
        content = result.content.strip()
        header = "📋 *Brief*\n"
        available = MAX_MSG - len(header)
        if len(content) <= available:
            self.send_message(chat_id, header + content, parse_mode="Markdown")
            return

        chunks: list[str] = []
        while content:
            if len(content) <= available:
                chunks.append(content)
                break
            split_at = available
            newline = content.rfind("\n", 0, available)
            if newline > available // 2:
                split_at = newline + 1
            chunks.append(content[:split_at])
            content = content[split_at:]

        for i, chunk in enumerate(chunks):
            part_header = f"📋 *Brief* ({i+1}/{len(chunks)})\n"
            self.send_message(chat_id, part_header + chunk, parse_mode="Markdown")

    def _cmd_clear(self, chat_id: int) -> None:
        """Handle /clear — write the .clear forced-molt signal. No LLM call."""
        result = self._local_commands.send_signal("clear", source="telegram")
        if result.status == "agent_dir_missing":
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot send clear signal.")
            return
        if result.status == "sent":
            self.send_message(
                chat_id,
                "🧹 Clear signal sent — conversation will be reset (forced molt) momentarily.",
            )
            return
        self.send_message(chat_id, f"⚠️ Failed to write clear signal: {result.error}")

    def _cmd_system(self, chat_id: int, text: str) -> None:
        """Handle /system — progressive disclosure of system folder.

        /system          → list files with inline keyboard to view each
        /system <name>   → send only that specific file
        """
        parts = text.strip().split(maxsplit=1)
        filter_name = parts[1].strip().lower() if len(parts) > 1 else ""
        result = self._local_commands.system_documents(filter_name or None)
        if result.status == "agent_dir_missing":
            self.send_message(chat_id, "⚠️ LINGTAI_AGENT_DIR not set — cannot read system files.")
            return
        if result.status == "directory_missing":
            self.send_message(chat_id, "⚠️ system/ directory not found.")
            return
        if result.status == "empty":
            self.send_message(chat_id, "📂 No markdown files in system/")
            return

        if not filter_name:
            # List mode: show directory listing with inline keyboard
            lines = ["📂 *system/* 目录\n"]
            buttons = []
            for document in result.documents:
                size = document.size
                if size < 1024:
                    size_str = f"{size}B"
                else:
                    size_str = f"{size / 1024:.1f}KB"
                lines.append(f"• `{document.name}` ({size_str})")
                buttons.append([{"text": f"📄 {document.name} ({size_str})", "callback_data": f"sys:{document.name}"}])

            self.send_message(
                chat_id,
                "\n".join(lines) + "\n\n点击按钮查看对应文件 👇",
                parse_mode="Markdown",
                reply_markup={"inline_keyboard": buttons},
            )
            return

        # Specific file mode
        if result.status == "no_match":
            file_list = ", ".join(document.name for document in result.documents)
            self.send_message(chat_id, f"❌ No match for '{filter_name}'\n\nAvailable: {file_list}")
            return

        # Send matched file(s), splitting if needed
        MAX_MSG = 4000
        for document in result.documents:
            if document.content is None:
                continue
            content = document.content

            header = f"📄 *{document.name}*\n```\n"
            footer = "\n```"
            available = MAX_MSG - len(header) - len(footer)

            if len(content) <= available:
                self.send_message(chat_id, header + content + footer, parse_mode="Markdown")
            else:
                chunks = []
                while content:
                    if len(content) <= available:
                        chunks.append(content)
                        break
                    split_at = available
                    newline = content.rfind("\n", 0, available)
                    if newline > available // 2:
                        split_at = newline + 1
                    chunks.append(content[:split_at])
                    content = content[split_at:]

                for i, chunk in enumerate(chunks):
                    part_header = f"📄 *{document.name}* ({i+1}/{len(chunks)})\n```\n"
                    self.send_message(chat_id, part_header + chunk + footer, parse_mode="Markdown")

    # -- Sending -------------------------------------------------------------

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        entities: list[dict[str, Any]] | None = None,
        link_preview_options: dict[str, Any] | None = None,
        disable_web_page_preview: bool | None = None,
    ) -> dict:
        """Send a text message. Returns the sent Message object.

        Rich-formatting options (``parse_mode``, ``entities``,
        ``link_preview_options``, ``disable_web_page_preview``) are passed
        through to the Bot API only when supplied — omitting them preserves
        the previous plain-text behaviour.
        """
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if entities is not None:
            payload["entities"] = entities
        if link_preview_options is not None:
            payload["link_preview_options"] = link_preview_options
        if disable_web_page_preview is not None:
            payload["disable_web_page_preview"] = disable_web_page_preview
        result = self._request("sendMessage", json=payload)
        self._note_chat_message_id(chat_id, result.get("message_id"))
        return result

    def send_photo(
        self,
        chat_id: int,
        photo_path: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        caption_entities: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Send a photo via multipart upload."""
        with open(photo_path, "rb") as f:
            files = {"photo": (Path(photo_path).name, f, "image/jpeg")}
            data: dict[str, Any] = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption
            if reply_to_message_id:
                data["reply_to_message_id"] = str(reply_to_message_id)
            if parse_mode:
                data["parse_mode"] = parse_mode
            if caption_entities is not None:
                # Multipart fields must be strings; serialize the array.
                data["caption_entities"] = json.dumps(caption_entities)
            result = self._request("sendPhoto", files=files, data=data)
        self._note_chat_message_id(chat_id, result.get("message_id"))
        return result

    def send_document(
        self,
        chat_id: int,
        doc_path: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        caption_entities: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Send a document via multipart upload."""
        with open(doc_path, "rb") as f:
            files = {"document": (Path(doc_path).name, f, "application/octet-stream")}
            data: dict[str, Any] = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption
            if reply_to_message_id:
                data["reply_to_message_id"] = str(reply_to_message_id)
            if parse_mode:
                data["parse_mode"] = parse_mode
            if caption_entities is not None:
                # Multipart fields must be strings; serialize the array.
                data["caption_entities"] = json.dumps(caption_entities)
            result = self._request("sendDocument", files=files, data=data)
        self._note_chat_message_id(chat_id, result.get("message_id"))
        return result

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        is_caption: bool = False,
        parse_mode: str | None = None,
        entities: list[dict[str, Any]] | None = None,
        caption_entities: list[dict[str, Any]] | None = None,
        link_preview_options: dict[str, Any] | None = None,
        disable_web_page_preview: bool | None = None,
    ) -> dict:
        """Edit a sent message's text or caption."""
        if is_caption:
            payload: dict[str, Any] = {
                "chat_id": chat_id, "message_id": message_id, "caption": text,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if caption_entities is not None:
                payload["caption_entities"] = caption_entities
            return self._request("editMessageCaption", json=payload)
        else:
            payload = {
                "chat_id": chat_id, "message_id": message_id, "text": text,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if entities is not None:
                payload["entities"] = entities
            if link_preview_options is not None:
                payload["link_preview_options"] = link_preview_options
            if disable_web_page_preview is not None:
                payload["disable_web_page_preview"] = disable_web_page_preview
            return self._request("editMessageText", json=payload)

    def delete_message(self, chat_id: int, message_id: int) -> dict:
        """Delete a message."""
        return self._request("deleteMessage", json={
            "chat_id": chat_id, "message_id": message_id,
        })

    def send_chat_action(self, chat_id: int, action: str = "typing") -> dict:
        """Send a chat action (typing indicator).

        Telegram shows the action (e.g. "typing...", "uploading photo...") to
        the chat user. The indicator auto-expires after 5 seconds, so callers
        should re-send during long-running tasks.
        """
        return self._request(
            "sendChatAction",
            json={"chat_id": chat_id, "action": action},
        )

    def set_message_reaction(
        self,
        chat_id: int,
        message_id: int,
        reaction: list[dict] | None = None,
        is_big: bool = False,
    ) -> bool:
        """Set a reaction on a message (Bot API 7.0+).

        Args:
            chat_id: Chat ID where the message is.
            message_id: Message ID to react to.
            reaction: List of reaction types. Each is a dict with "type"
                (e.g. "emoji") and "emoji" (e.g. "👀", "⏳", "✅", "❌").
                Pass None or empty list to remove reactions.
            is_big: If True, show a bigger reaction animation.

        Returns:
            True on success (Bot API returns True, not a dict).
        """
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "is_big": is_big,
        }
        if reaction is not None:
            payload["reaction"] = reaction
        return self._request("setMessageReaction", json=payload)

    def get_file(self, file_id: str) -> tuple[str, bytes]:
        """Download a file by file_id. Returns (filename, data)."""
        file_info = self._request("getFile", json={"file_id": file_id})
        file_path = file_info["file_path"]
        filename = Path(file_path).name
        url = self._file_url(file_path)
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        resp = self._client.get(url)
        resp.raise_for_status()
        return filename, resp.content


    @property
    def allowed_users_count(self) -> int | None:
        """Return the allow-list size without exposing user IDs."""
        if self._allowed_users is None:
            return None
        return len(self._allowed_users)

    def public_identity(self) -> dict[str, Any]:
        """Non-secret Bot API identity observed from getMe/state.

        This intentionally exposes only stable public bot metadata. It never
        includes bot tokens, user IDs, chat IDs, messages, or webhook secrets.
        """
        info = self._bot_info or {}
        first_name = info.get("first_name")
        last_name = info.get("last_name")
        display_name = " ".join(
            str(part) for part in (first_name, last_name) if part
        ) or None
        identity = {
            "alias": self.alias,
            "bot_id": info.get("id"),
            "bot_username": info.get("username"),
            "bot_display_name": display_name,
            "is_bot": info.get("is_bot"),
            "last_verified_at": self._last_verified_at,
        }
        return {k: v for k, v in identity.items() if v is not None}

    # -- State persistence ---------------------------------------------------

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
            self._last_update_id = data.get("last_update_id", 0)
            self._bot_info = data.get("bot_info")
            self._last_verified_at = data.get("last_verified_at")
            self._task_cards = self._normalize_task_cards(data.get("task_cards"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load Telegram state: %s", e)

    @staticmethod
    def _normalize_task_cards(raw: object) -> dict[str, str]:
        """Coerce a persisted ``task_cards`` value into a clean {chat: id} map.

        Backward-compatible and defensive: a missing or wrongly-typed value
        becomes ``{}`` (rather than losing unrelated state), and any non-string
        key or value is dropped so a malformed record can never crash a later
        create or resurrect a bogus id.
        """
        if not isinstance(raw, dict):
            return {}
        clean: dict[str, str] = {}
        for chat, compound in raw.items():
            if isinstance(chat, str) and isinstance(compound, str) and compound:
                clean[chat] = compound
        return clean

    def _save_state(self) -> None:
        path = self._state_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize the snapshot+write so a poll-thread offset update and a
        # turn-thread task-card update cannot lose each other's field.
        with self._state_lock:
            data = {
                "last_update_id": self._last_update_id,
                "bot_info": self._bot_info,
                "last_verified_at": self._last_verified_at,
                "task_cards": dict(self._task_cards),
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

    # -- Per-chat last-message-id observation (ephemeral high-water mark) -----

    def _note_chat_message_id(self, chat_id: int, message_id: object) -> None:
        """Record ``message_id`` as the chat's newest observed bottom message.

        Monotonic per chat: a smaller/out-of-order id never lowers the mark, so
        an edit that re-uses an older id cannot make the resident card look stale.
        Defensive: a non-integer id (malformed update) is ignored, and the whole
        recording is best-effort — it must never raise into the poll loop or a
        send path, so a not-fully-constructed account (missing lock/map) is a
        silent no-op rather than an error.
        """
        if (
            not isinstance(chat_id, int)
            or isinstance(chat_id, bool)
            or not isinstance(message_id, int)
            or isinstance(message_id, bool)
        ):
            return
        chat = chat_id
        mid = message_id
        lock = getattr(self, "_state_lock", None)
        marks = getattr(self, "_last_message_ids", None)
        if lock is None or marks is None:
            return
        with lock:
            if mid > marks.get(chat, 0):
                marks[chat] = mid

    def get_last_message_id(self, chat_id: int) -> int | None:
        """Return the highest observed Telegram message id for ``chat_id``.

        ``None`` when no message has been observed for that chat yet (e.g. right
        after a refresh, before any inbound or outbound message). Callers must
        treat ``None`` conservatively — it is not evidence that any particular
        message is or is not the last one.
        """
        try:
            chat = int(chat_id)
        except (TypeError, ValueError):
            return None
        with self._state_lock:
            return self._last_message_ids.get(chat)

    # -- Resident Task Card id (one per account+chat) ------------------------

    def get_task_card(self, chat_id: int) -> str | None:
        """Return the persisted resident Task Card compound id for ``chat_id``.

        ``None`` when no card is tracked for that chat.
        """
        with self._state_lock:
            return self._task_cards.get(str(chat_id))

    def set_task_card(self, chat_id: int, compound_id: str) -> None:
        """Persist ``compound_id`` as the resident Task Card id for ``chat_id``.

        Overwrites any previous id for the same chat and durably saves state so
        the id survives a refresh.  Other chats and unrelated state are left
        untouched.
        """
        with self._state_lock:
            self._task_cards[str(chat_id)] = compound_id
            self._save_state()

    def clear_task_card(self, chat_id: int) -> None:
        """Forget the resident Task Card id for ``chat_id`` and persist.

        No-op when nothing is tracked for that chat.
        """
        with self._state_lock:
            if self._task_cards.pop(str(chat_id), None) is not None:
                self._save_state()

    def list_task_card_chats(self) -> list[int]:
        """Return every chat id with a persisted resident Task Card.

        Read-only enumeration over the same durable ``task_cards`` map
        ``get_task_card``/``set_task_card`` use; a malformed (non-numeric) key
        is skipped rather than raising, since ``_normalize_task_cards`` already
        guarantees string keys but not that they parse as chat ids.
        """
        with self._state_lock:
            keys = list(self._task_cards.keys())
        chats: list[int] = []
        for key in keys:
            try:
                chats.append(int(key))
            except (TypeError, ValueError):
                continue
        return chats
