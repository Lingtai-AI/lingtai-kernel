"""Localized Feishu control-card rendering and callback dedupe."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Callable

from lingtai.kernel._fsutil import atomic_write_json, read_json
from lingtai.mcp_servers.local_commands import (
    DEFAULT_COMMANDS,
    HIDDEN_COMMANDS,
    LocalCommandCore,
    TaskCardSettingsPort,
)

COMMANDS = frozenset(
    {
        "help",
        "status",
        "kanban",
        "system",
        "brief",
        "refresh",
        "sleep",
        "clear",
        "taskcard",
    }
)
_CONTENT_LIMIT = 8_000
_PRIVATE_FILE_MODE = 0o600


def _language(value: object) -> str:
    normalized = str(value or "").lower().replace("_", "-")
    if normalized == "wen" or normalized.startswith("wen-"):
        return "wen"
    if normalized == "zh" or normalized.startswith("zh-"):
        return "zh"
    return "en"


_TEXT = {
    "en": {
        "help": "Controls",
        "status": "Status",
        "kanban": "Kanban",
        "system": "System",
        "brief": "Brief",
        "refresh": "Refresh",
        "sleep": "Sleep",
        "clear": "Clear",
        "taskcard": "Task Card",
        "available": "Available local commands",
        "operations": "Agent controls",
        "back": "Back",
        "all": "All layers",
        "unavailable": "Agent data is unavailable.",
        "not_found": "No briefing was found.",
        "no_system": "No system documents were found.",
        "choose_system": "Choose a system document",
        "truncated": "Content was truncated.",
        "usage": "Usage: `/taskcard on`, `/taskcard off`, or `/taskcard N` (1–10).",
        "update_failed": "The Task Card setting could not be updated.",
        "taskcard_on": "Task Cards are visible.",
        "taskcard_off": "Task Cards are hidden.",
        "rows": "normal rows",
        "pending": "A refresh is already pending.",
        "refresh_sent": "Refresh requested. The Agent will restart shortly.",
        "sleep_sent": "Sleep requested. Send a message to wake the Agent.",
        "clear_sent": "Conversation reset requested.",
        "signal_failed": "The control signal failed.",
        "identity": "Identity",
        "model": "Model",
        "runtime": "Runtime",
        "mind": "Mind",
        "tokens": "Tokens",
        "network": "Network",
        "addons": "Addons",
    },
    "zh": {
        "help": "控制中心",
        "status": "状态",
        "kanban": "看板",
        "system": "系统文件",
        "brief": "任务简报",
        "refresh": "刷新",
        "sleep": "休眠",
        "clear": "清空会话",
        "taskcard": "任务卡片",
        "available": "可用的本地命令",
        "operations": "Agent 控制",
        "back": "返回",
        "all": "全部层级",
        "unavailable": "暂时无法读取 Agent 数据。",
        "not_found": "没有找到任务简报。",
        "no_system": "没有找到系统文档。",
        "choose_system": "选择系统文档",
        "truncated": "内容已截断。",
        "usage": "用法：`/taskcard on`、`/taskcard off` 或 `/taskcard N`（1–10）。",
        "update_failed": "无法更新任务卡片设置。",
        "taskcard_on": "任务卡片已显示。",
        "taskcard_off": "任务卡片已隐藏。",
        "rows": "常规行数",
        "pending": "已有刷新请求等待处理。",
        "refresh_sent": "已请求刷新，Agent 即将重启。",
        "sleep_sent": "已请求休眠，发送新消息即可唤醒 Agent。",
        "clear_sent": "已请求清空会话。",
        "signal_failed": "控制信号执行失败。",
        "identity": "身份",
        "model": "模型",
        "runtime": "运行状态",
        "mind": "心智与记忆",
        "tokens": "Token 用量",
        "network": "Agent 网络",
        "addons": "扩展与配置",
    },
    "wen": {
        "help": "总览",
        "status": "态势",
        "kanban": "全局",
        "system": "系统诸篇",
        "brief": "要旨",
        "refresh": "更始",
        "sleep": "休止",
        "clear": "涤除前言",
        "taskcard": "任务之牒",
        "available": "可行之令",
        "operations": "机心之制",
        "back": "返",
        "all": "尽览七层",
        "unavailable": "机况未可得也。",
        "not_found": "未见要旨。",
        "no_system": "系统无篇可览。",
        "choose_system": "择一篇而观",
        "truncated": "篇幅所限，文有所略。",
        "usage": "法：`/taskcard on`、`/taskcard off`，或 `/taskcard N`（一至十）。",
        "update_failed": "任务之牒未能更定。",
        "taskcard_on": "任务之牒已显。",
        "taskcard_off": "任务之牒已隐。",
        "rows": "常列",
        "pending": "更始之令已在候。",
        "refresh_sent": "更始之令已下，机将复起。",
        "sleep_sent": "休止之令已下；复有来言，则机自醒。",
        "clear_sent": "涤除前言之令已下。",
        "signal_failed": "制令未成。",
        "identity": "名实",
        "model": "模型",
        "runtime": "行止",
        "mind": "心识",
        "tokens": "符数",
        "network": "诸机",
        "addons": "辅具",
    },
}


def _t(language: str, key: str) -> str:
    return _TEXT[language][key]


def _control_value(command: str, argument: str = "") -> dict[str, Any]:
    return {
        "lingtai_control": {
            "version": 1,
            "command": command,
            "argument": argument,
        }
    }


def _button(language: str, command: str, argument: str = "") -> dict[str, Any]:
    label = _t(language, command) if command in _TEXT[language] else command
    if command == "kanban" and argument in set("1234567"):
        label = f"{argument} · {_t(language, _LAYER_KEYS[int(argument) - 1])}"
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "default",
        "value": _control_value(command, argument),
    }


def _card(
    language: str, title: str, markdown: str, buttons: list[dict[str, Any]]
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": markdown}]
    elements.extend(buttons)
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"LingTai · {title}"},
            "template": "blue",
        },
        "body": {"elements": elements},
    }


_LAYER_KEYS = ("identity", "model", "runtime", "mind", "tokens", "network", "addons")


class FeishuControlCards:
    """Execute shared command semantics and render Feishu schema-2.0 cards."""

    def __init__(
        self,
        core: LocalCommandCore,
        taskcard_settings: TaskCardSettingsPort,
        *,
        on_normal_rows_changed: Callable[[], None] | None = None,
    ) -> None:
        self._core = core
        self._taskcard_settings = taskcard_settings
        self._on_normal_rows_changed = on_normal_rows_changed

    @staticmethod
    def parse(text: object) -> tuple[str, str] | None:
        if not isinstance(text, str):
            return None
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None
        parts = stripped.split(maxsplit=1)
        command = parts[0].split("@", 1)[0][1:].lower()
        if command not in COMMANDS:
            return None
        return command, parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def callback_text(value: object) -> str | None:
        if not isinstance(value, dict) or set(value) != {"lingtai_control"}:
            return None
        control = value.get("lingtai_control")
        if not isinstance(control, dict) or set(control) != {
            "version",
            "command",
            "argument",
        }:
            return None
        command = control.get("command")
        argument = control.get("argument")
        if control.get("version") != 1 or command not in COMMANDS:
            return None
        if not isinstance(argument, str) or len(argument) > 256:
            return None
        return f"/{command}" + (f" {argument}" if argument else "")

    def render(self, text: str) -> dict[str, Any]:
        parsed = self.parse(text)
        if parsed is None:
            raise ValueError("unsupported Feishu local command")
        command, argument = parsed
        data = self._core.collect_kanban_data()
        language = _language(data.get("language") if data else None)
        method = getattr(self, f"_render_{command}")
        return method(language, argument, data)

    def _render_help(
        self, language: str, _argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        descriptions = {
            "en": {
                item["command"]: item["description"]
                for item in [*DEFAULT_COMMANDS, *HIDDEN_COMMANDS]
            },
            "zh": {
                "help": "显示控制中心",
                "kanban": "查看完整 Agent 看板",
                "taskcard": "显示或隐藏任务卡片",
                "refresh": "重启 Agent",
                "sleep": "让 Agent 休眠",
                "clear": "清空当前会话",
                "status": "查看简要状态",
                "system": "浏览系统文件",
                "brief": "查看当前任务简报",
            },
            "wen": {
                "help": "陈列诸令",
                "kanban": "览机之全局",
                "taskcard": "显隐任务之牒",
                "refresh": "使机更始",
                "sleep": "使机休止",
                "clear": "涤除前言",
                "status": "察机之态",
                "system": "览系统诸篇",
                "brief": "观其要旨",
            },
        }[language]
        lines = [f"**{_t(language, 'available')}**"]
        for item in [*DEFAULT_COMMANDS, *HIDDEN_COMMANDS]:
            command = item["command"]
            lines.append(f"- `/{command}` — {descriptions[command]}")
        buttons = [
            _button(language, command)
            for command in ("status", "kanban", "system", "brief", "taskcard")
        ]
        buttons.extend(
            _button(language, command) for command in ("refresh", "sleep", "clear")
        )
        return _card(language, _t(language, "help"), "\n".join(lines), buttons)

    def _render_status(
        self, language: str, _argument: str, data: dict[str, Any] | None
    ) -> dict[str, Any]:
        if data is None:
            body = _t(language, "unavailable")
        else:
            ctx = data["ctx"]
            total = ctx.get("total_tokens", 0)
            window = ctx.get("window_size", data["context_limit"])
            body = (
                f"**{data['current_agent']}** · {data['agent_state']}\n"
                f"**{_t(language, 'model')}:** {data['current_model']} ({data['current_provider']})\n"
                f"**Context:** {data['fmt'](total)}/{data['fmt'](window)} ({ctx.get('usage_pct', 0):.1f}%)\n"
                f"**Molts:** {data['molt_count']} · **Uptime:** {data['fmt_duration'](data['uptime'])}"
            )
        return _card(
            language,
            _t(language, "status"),
            body,
            [
                _button(language, "kanban"),
                _button(language, "help"),
            ],
        )

    def _layer(self, language: str, data: dict[str, Any], layer: int) -> str:
        fmt = data["fmt"]
        key = _LAYER_KEYS[layer - 1]
        lines = [f"**{layer} · {_t(language, key)}**"]
        if layer == 1:
            lines.extend(
                [
                    f"Agent: `{data['current_agent']}`",
                    f"ID: `{str(data['agent_id'])[-12:]}`",
                    f"Born: {data['fmt_time'](data['created_at'])}",
                    f"Molts: {data['molt_count']}",
                ]
            )
        elif layer == 2:
            lines.append(
                f"Active: `{data['current_model']}` ({data['current_provider']})"
            )
            lines.extend(
                f"- `{item['model']}` ({item['provider']})"
                for item in data["preset_models"][:8]
            )
        elif layer == 3:
            ctx = data["ctx"]
            lines.extend(
                [
                    f"State: {data['agent_state']}",
                    f"Uptime: {data['fmt_duration'](data['uptime'])}",
                    f"Context: {fmt(ctx.get('total_tokens', 0))}/{fmt(ctx.get('window_size', data['context_limit']))} ({ctx.get('usage_pct', 0):.1f}%)",
                ]
            )
        elif layer == 4:
            lines.extend(
                [
                    f"Language: {data['language']}",
                    f"Knowledge: {data['knowledge_count']} · Skills: {data['skill_count']} · Delegates: {data['delegate_count']}",
                    f"Capabilities: {data['join_limited'](data['capability_names'])}",
                ]
            )
        elif layer == 5:
            for name, item in sorted(data["all_agents"].items()):
                total = item["input"] + item["output"] + item["thinking"]
                lines.append(f"- `{name}`: {fmt(total)} ({item['calls']} calls)")
            lines.append(
                f"Total: {fmt(data['grand_total'])} ({data['total_api_calls']} calls)"
            )
        elif layer == 6:
            for name, item in sorted(data["all_agents"].items()):
                lines.append(
                    f"- `{name}`: {item['state']} · {item['model']} · molts={item['molt_count']}"
                )
        else:
            lines.extend(
                f"- {'✅' if enabled else '⬜'} {name}"
                for name, enabled in sorted(data["addon_status"].items())
            )
            lines.append(f"Context limit: {fmt(data['context_limit'])}")
        return "\n".join(lines)

    def _render_kanban(
        self, language: str, argument: str, data: dict[str, Any] | None
    ) -> dict[str, Any]:
        if data is None:
            body = _t(language, "unavailable")
        elif argument in {str(value) for value in range(1, 8)}:
            body = self._layer(language, data, int(argument))
        elif argument.lower() == "all":
            body = "\n\n".join(
                self._layer(language, data, value) for value in range(1, 8)
            )
        else:
            ctx = data["ctx"]
            body = (
                f"**{data['current_agent']}** · {data['agent_state']}\n"
                f"{data['current_model']} ({data['current_provider']})\n"
                f"Context: {data['fmt'](ctx.get('total_tokens', 0))}/{data['fmt'](ctx.get('window_size', data['context_limit']))} "
                f"({ctx.get('usage_pct', 0):.1f}%) · Uptime: {data['fmt_duration'](data['uptime'])}"
            )
        buttons = [_button(language, "kanban", str(value)) for value in range(1, 8)]
        buttons.extend(
            [
                {
                    **_button(language, "kanban", "all"),
                    "text": {"tag": "plain_text", "content": _t(language, "all")},
                },
                _button(language, "help"),
            ]
        )
        return _card(language, _t(language, "kanban"), body, buttons)

    def _render_system(
        self, language: str, argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        result = self._core.system_documents(argument or None)
        if result.status in {"agent_dir_missing", "directory_missing", "empty"}:
            body = _t(language, "no_system")
            buttons = [_button(language, "help")]
        elif not argument:
            body = f"**{_t(language, 'choose_system')}**\n" + "\n".join(
                f"- `{document.name}` ({document.size} B)"
                for document in result.documents[:15]
            )
            buttons = [
                _button(language, "system", document.name)
                for document in result.documents[:15]
            ]
            buttons.append(_button(language, "help"))
        elif result.status == "no_match":
            body = _t(language, "no_system")
            buttons = [_button(language, "system"), _button(language, "help")]
        else:
            blocks = []
            for document in result.documents:
                content = document.content or ""
                suffix = (
                    f"\n\n_{_t(language, 'truncated')}_"
                    if len(content) > _CONTENT_LIMIT
                    else ""
                )
                blocks.append(
                    f"**{document.name}**\n{content[:_CONTENT_LIMIT]}{suffix}"
                )
            body = "\n\n".join(blocks)
            buttons = [_button(language, "system"), _button(language, "help")]
        return _card(language, _t(language, "system"), body, buttons)

    def _render_brief(
        self, language: str, _argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        result = self._core.read_brief()
        if result.status != "ok" or result.content is None:
            body = _t(language, "not_found")
        else:
            body = result.content[:_CONTENT_LIMIT]
            if len(result.content) > _CONTENT_LIMIT:
                body += f"\n\n_{_t(language, 'truncated')}_"
        return _card(language, _t(language, "brief"), body, [_button(language, "help")])

    def _render_taskcard(
        self, language: str, argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        old_rows = self._taskcard_settings.normal_rows()
        text = "/taskcard" + (f" {argument}" if argument else "")
        result = self._core.apply_taskcard(text, self._taskcard_settings)
        if result.status == "usage":
            body = _t(language, "usage")
        elif result.status == "update_failed":
            body = _t(language, "update_failed")
        else:
            state = _t(language, "taskcard_on" if result.enabled else "taskcard_off")
            body = f"{state}\n**{_t(language, 'rows')}:** {result.normal_rows}\n\n{_t(language, 'usage')}"
            if result.normal_rows != old_rows and self._on_normal_rows_changed:
                self._on_normal_rows_changed()
        return _card(
            language,
            _t(language, "taskcard"),
            body,
            [
                _button(language, "taskcard", "on"),
                _button(language, "taskcard", "off"),
                _button(language, "help"),
            ],
        )

    def _signal_card(self, language: str, command: str) -> dict[str, Any]:
        result = self._core.send_signal(command, source="feishu")
        key = {
            ("refresh", "pending"): "pending",
            ("refresh", "sent"): "refresh_sent",
            ("sleep", "sent"): "sleep_sent",
            ("clear", "sent"): "clear_sent",
        }.get((command, result.status), "signal_failed")
        return _card(
            language,
            _t(language, command),
            _t(language, key),
            [_button(language, "help")],
        )

    def _render_refresh(
        self, language: str, _argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        return self._signal_card(language, "refresh")

    def _render_sleep(
        self, language: str, _argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        return self._signal_card(language, "sleep")

    def _render_clear(
        self, language: str, _argument: str, _data: dict[str, Any] | None
    ) -> dict[str, Any]:
        return self._signal_card(language, "clear")


class FeishuControlEventStore:
    """Durably bind control-card sources and claim callback events by hash."""

    VERSION = 2
    LIMIT = 1_000

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def _event_digest(event_id: str) -> str:
        # Keep v1 event hashes stable so a migrated store retains replay claims.
        return hashlib.sha256(event_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _source_digest(chat_id: str, message_id: str) -> str:
        material = f"source\0{chat_id}\0{message_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _load(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return {"version": self.VERSION, "accounts": {}}
        try:
            payload = read_json(self._path, expect=dict)
        except (OSError, TypeError, ValueError):
            return None
        accounts = payload.get("accounts")
        if not isinstance(accounts, dict):
            return None

        # v1 stored only {account: [event_hash, ...]}. Migrate in memory; the
        # next successful write upgrades the file and intentionally trusts no
        # pre-upgrade source card.
        if payload.get("version") == 1:
            if not all(
                isinstance(values, list)
                and all(isinstance(value, str) for value in values)
                for values in accounts.values()
            ):
                return None
            return {
                "version": self.VERSION,
                "accounts": {
                    account: {
                        "events": values[-self.LIMIT :],
                        "sources": [],
                    }
                    for account, values in accounts.items()
                },
            }
        if payload.get("version") != self.VERSION:
            return None
        for values in accounts.values():
            if not isinstance(values, dict) or set(values) != {
                "events",
                "sources",
            }:
                return None
            if not all(
                isinstance(items, list)
                and all(isinstance(value, str) for value in items)
                for items in values.values()
            ):
                return None
        return payload

    @staticmethod
    def _account_values(
        payload: dict[str, Any],
        account: str,
    ) -> dict[str, list[str]]:
        return payload["accounts"].setdefault(account, {"events": [], "sources": []})

    def register_source(
        self,
        account: str,
        chat_id: str,
        message_id: str,
    ) -> bool:
        """Trust one successfully sent local card at its exact route."""
        if not account or not chat_id or not message_id:
            return False
        digest = self._source_digest(chat_id, message_id)
        with self._lock:
            payload = self._load()
            if payload is None:
                return False
            values = self._account_values(payload, account)
            sources = values["sources"]
            if digest in sources:
                return True
            values["sources"] = [*sources[-(self.LIMIT - 1) :], digest]
            try:
                atomic_write_json(
                    self._path,
                    payload,
                    fsync=True,
                    file_mode=_PRIVATE_FILE_MODE,
                )
            except OSError:
                return False
            return True

    def is_trusted_source(
        self,
        account: str,
        chat_id: str,
        message_id: str,
    ) -> bool:
        """Return whether a callback source is one registered local card."""
        if not account or not chat_id or not message_id:
            return False
        digest = self._source_digest(chat_id, message_id)
        with self._lock:
            payload = self._load()
            if payload is None:
                return False
            values = payload["accounts"].get(account)
            return isinstance(values, dict) and digest in values.get("sources", [])

    def claim(self, account: str, event_id: str) -> bool:
        digest = self._event_digest(event_id)
        with self._lock:
            payload = self._load()
            if payload is None:
                return False
            values = self._account_values(payload, account)
            events = values["events"]
            if digest in events:
                return False
            values["events"] = [*events[-(self.LIMIT - 1) :], digest]
            try:
                atomic_write_json(
                    self._path,
                    payload,
                    fsync=True,
                    file_mode=_PRIVATE_FILE_MODE,
                )
            except OSError:
                return False
            return True
