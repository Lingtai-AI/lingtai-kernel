"""Channel-neutral data/control core for local messaging commands."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingtai.kernel.session_stats import read_agent_record


DEFAULT_COMMANDS: list[dict[str, str]] = [
    {"command": "help", "description": "List available commands"},
    {
        "command": "kanban",
        "description": "Show full agent dashboard (model, tokens, network, config)",
    },
    {"command": "taskcard", "description": "Show or hide Task Cards"},
    {"command": "refresh", "description": "Restart agent"},
    {"command": "sleep", "description": "Put agent to sleep"},
    {"command": "clear", "description": "Clear conversation"},
]

HIDDEN_COMMANDS: list[dict[str, str]] = [
    {"command": "status", "description": "Show agent status (also in /kanban)"},
    {"command": "system", "description": "Browse system files (tap to view)"},
]


@dataclass(frozen=True)
class SignalResult:
    status: str
    error: str | None = None


@dataclass(frozen=True)
class SystemDocument:
    name: str
    size: int
    content: str | None = None


@dataclass(frozen=True)
class SystemDirectoryResult:
    status: str
    documents: tuple[SystemDocument, ...] = ()


@dataclass(frozen=True)
class TaskCardSettingsPort:
    enabled: Callable[[], bool]
    set_enabled: Callable[[bool], None] | None
    normal_rows: Callable[[], int]
    set_normal_rows: Callable[[int], None] | None
    # Optional locale surface (en|zh) for the shared Task Card projection;
    # None/absent keeps the channel on its own default without touching locale.
    locale: Callable[[], str] | None = None
    set_locale: Callable[[str], None] | None = None


@dataclass(frozen=True)
class TaskCardCommandResult:
    status: str
    enabled: bool | None = None
    normal_rows: int | None = None
    locale: str | None = None


class LocalCommandCore:
    """Own local command reads/control while channels own presentation."""

    def __init__(self, agent_dir: Path | None = None) -> None:
        self._explicit_agent_dir = Path(agent_dir) if agent_dir is not None else None

    def _agent_path(self) -> Path | None:
        if self._explicit_agent_dir is not None:
            return self._explicit_agent_dir
        value = os.environ.get("LINGTAI_AGENT_DIR", "")
        return Path(value) if value else None

    def apply_taskcard(
        self,
        text: str,
        settings: TaskCardSettingsPort,
    ) -> TaskCardCommandResult:
        """Parse/update Task Card preferences without rendering a response.

        Supports ``on|off``, ``N`` (1-10), and ``lang zh|en`` (when the
        settings port exposes a locale getter/setter; otherwise the lang
        sub-command degrades to ``usage`` so an older double stays honest).
        """
        args = text.split()[1:]
        if args and args[0].lower() == "lang":
            if len(args) != 2 or settings.set_locale is None or settings.locale is None:
                return TaskCardCommandResult("usage")
            locale = args[1].lower()
            if locale not in {"en", "zh"}:
                return TaskCardCommandResult("usage")
            try:
                settings.set_locale(locale)
            except Exception:  # noqa: BLE001 - provider-owned setter boundary
                return TaskCardCommandResult("update_failed")
            return TaskCardCommandResult(
                "ok",
                enabled=settings.enabled(),
                normal_rows=settings.normal_rows(),
                locale=settings.locale(),
            )
        if len(args) > 1:
            return TaskCardCommandResult("usage")
        if args:
            argument = args[0].lower()
            if argument in {"on", "off"}:
                setter = settings.set_enabled
                value: bool | int = argument == "on"
            elif (
                argument.isascii() and argument.isdecimal() and 1 <= int(argument) <= 10
            ):
                setter = settings.set_normal_rows
                value = int(argument)
            else:
                return TaskCardCommandResult("usage")
            if setter is None:
                return TaskCardCommandResult("update_failed")
            try:
                setter(value)
            except Exception:  # noqa: BLE001 - provider-owned setter boundary
                return TaskCardCommandResult("update_failed")
        locale: str | None = None
        if settings.locale is not None:
            try:
                locale = settings.locale()
            except Exception:  # noqa: BLE001 - provider-owned getter boundary
                locale = None
        return TaskCardCommandResult(
            "ok",
            enabled=settings.enabled(),
            normal_rows=settings.normal_rows(),
            locale=locale,
        )

    def send_signal(self, signal: str, *, source: str) -> SignalResult:
        """Write one existing Agent signal with channel-neutral outcomes."""
        agent_path = self._agent_path()
        if agent_path is None:
            return SignalResult("agent_dir_missing")
        if signal == "refresh":
            target = agent_path / ".refresh"
            taken = agent_path / ".refresh.taken"
            if taken.exists():
                try:
                    taken.unlink()
                except OSError:
                    pass
            if target.exists():
                return SignalResult("pending")
            content = ""
        elif signal == "sleep":
            target = agent_path / ".sleep"
            content = ""
        elif signal == "clear":
            target = agent_path / ".clear"
            content = f"{source}\n"
        else:
            raise ValueError(f"unsupported local command signal: {signal}")
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return SignalResult("failed", str(exc))
        return SignalResult("sent")

    def system_documents(self, query: str | None = None) -> SystemDirectoryResult:
        """List or read Markdown documents from the Agent system directory."""
        agent_path = self._agent_path()
        if agent_path is None:
            return SystemDirectoryResult("agent_dir_missing")
        system_dir = agent_path / "system"
        if not system_dir.exists():
            return SystemDirectoryResult("directory_missing")
        paths = sorted(system_dir.glob("*.md"))
        if not paths:
            return SystemDirectoryResult("empty")
        available = tuple(
            SystemDocument(name=path.stem, size=path.stat().st_size) for path in paths
        )
        if not query:
            return SystemDirectoryResult("ok", available)
        needle = query.lower()
        matched = [path for path in paths if needle in path.stem.lower()]
        if not matched:
            return SystemDirectoryResult("no_match", available)
        documents: list[SystemDocument] = []
        for path in matched:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            documents.append(
                SystemDocument(
                    name=path.stem,
                    size=path.stat().st_size,
                    content=content,
                )
            )
        return SystemDirectoryResult("ok", tuple(documents))

    def collect_kanban_data(self) -> dict[str, Any] | None:
        """Collect the established channel-neutral Agent dashboard values."""
        agent_path = self._agent_path()
        if agent_path is None:
            return None
        lingtai_dir = agent_path.parent
        current_agent = agent_path.name

        def fmt(n: int | float | None) -> str:
            try:
                value = int(n or 0)
            except (TypeError, ValueError):
                value = 0
            if value >= 1_000_000:
                return f"{value / 1_000_000:.1f}M"
            if value >= 1_000:
                return f"{value / 1_000:.1f}K"
            return str(value)

        def fmt_duration(seconds: int | float | None) -> str:
            try:
                total = int(seconds or 0)
            except (TypeError, ValueError):
                total = 0
            if total <= 0:
                return "0m"
            days, rem = divmod(total, 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            parts: list[str] = []
            if days:
                parts.append(f"{days}d")
            if hours:
                parts.append(f"{hours}h")
            if minutes or not parts:
                parts.append(f"{minutes}m")
            return "".join(parts[:3])

        def fmt_time(value: str | None) -> str:
            if not value:
                return "?"
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                local = dt.astimezone()
                return local.strftime("%Y-%m-%d %H:%M %Z")
            except (TypeError, ValueError):
                return str(value)

        def age_since(value: str | None) -> str:
            if not value:
                return "?"
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                elapsed = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
                return fmt_duration(elapsed.total_seconds())
            except (TypeError, ValueError):
                return "?"

        def read_json(path: Path) -> dict[str, Any]:
            if not path.exists():
                return {}
            try:
                with path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                return {}

        def count_matching(root: Path, pattern: str) -> int:
            if not root.exists():
                return 0
            try:
                return sum(1 for _ in root.rglob(pattern))
            except OSError:
                return 0

        def join_limited(items: list[str], max_chars: int = 220) -> str:
            out: list[str] = []
            used = 0
            for item in items:
                piece = item if not out else ", " + item
                if used + len(piece) > max_chars:
                    remaining = len(items) - len(out)
                    out.append(f"…(+{remaining})")
                    break
                out.append(piece if not out else piece[2:])
                used += len(piece)
            return ", ".join(out) if out else "—"

        init = read_json(agent_path / "init.json")
        agent_meta = read_json(agent_path / ".agent.json")
        manifest = init.get("manifest", {})
        meta_llm = agent_meta.get("llm", {})
        init_llm = manifest.get("llm", {})
        current_model = meta_llm.get("model") or init_llm.get("model", "?")
        current_provider = meta_llm.get("provider") or init_llm.get("provider", "?")
        live_context_limit = meta_llm.get("context_limit")
        context_limit = (
            live_context_limit
            if type(live_context_limit) is int and live_context_limit > 0
            else 0
        )
        language = agent_meta.get("language") or manifest.get("language", "?")
        soul_delay = agent_meta.get("soul_delay") or manifest.get("soul", {}).get(
            "delay", 0
        )
        created_at = agent_meta.get("created_at")
        started_at = agent_meta.get("started_at")
        agent_id = agent_meta.get("agent_id") or "?"
        nickname = agent_meta.get("nickname")
        molt_count = int(agent_meta.get("molt_count") or 0)
        summaries_count = count_matching(
            agent_path / "system" / "summaries", "molt_*.md"
        )
        if not molt_count:
            molt_count = summaries_count
        admin = agent_meta.get("admin") or manifest.get("admin", {})

        raw_caps = agent_meta.get("capabilities") or manifest.get("capabilities", {})
        capability_names: list[str] = []
        if isinstance(raw_caps, dict):
            capability_names = sorted(str(key) for key in raw_caps)
        elif isinstance(raw_caps, list):
            for item in raw_caps:
                if isinstance(item, (list, tuple)) and item:
                    capability_names.append(str(item[0]))
                elif isinstance(item, str):
                    capability_names.append(item)
            capability_names = sorted(set(capability_names))

        preset_info = agent_meta.get("preset") or manifest.get("preset", {})
        active_preset_path = preset_info.get("active", "")
        default_preset_path = preset_info.get("default", "")
        allowed_presets = preset_info.get("allowed", [])
        preset_models: list[dict[str, Any]] = []
        for preset_path in allowed_presets:
            path = Path(preset_path).expanduser()
            if path.exists():
                try:
                    with path.open("r", encoding="utf-8") as handle:
                        preset = json.load(handle)
                    preset_llm = preset.get("manifest", {}).get("llm", {})
                    description = preset.get("description", {}).get("summary", "")
                    is_active = str(path) == str(Path(active_preset_path).expanduser())
                    preset_models.append(
                        {
                            "name": preset.get("name", path.stem),
                            "model": preset_llm.get("model", "?"),
                            "provider": preset_llm.get("provider", "?"),
                            "desc": description,
                            "active": is_active,
                        }
                    )
                except (json.JSONDecodeError, OSError):
                    pass

        # Own live state/usage — curated from this agent's published Agent
        # record (lingtai.kernel.session_stats) rather than re-collected
        # from .status.json: /kanban renders the record, it does not
        # reconstruct normal live state itself.
        record = read_agent_record(agent_path)
        record_session = (record or {}).get("session") if isinstance(record, dict) else None
        record_usage = (record or {}).get("usage") if isinstance(record, dict) else None
        if not isinstance(record_session, dict):
            record_session = {}
        if not isinstance(record_usage, dict):
            record_usage = {}
        agent_state = record_session.get("state", agent_meta.get("state", "?"))
        uptime = record_session.get("uptime_seconds", 0)
        started_at = record_session.get("started_at") or started_at
        ctx = {
            "total_tokens": record_usage.get("context_used_tokens", 0),
            "window_size": record_usage.get("context_limit_tokens"),
            "usage_pct": record_usage.get("context_usage_pct", 0) or 0,
            "system_tokens": record_usage.get("context_system_tokens", 0),
            "tools_tokens": record_usage.get("context_tools_tokens", 0),
            "history_tokens": record_usage.get("context_history_tokens", 0),
        }

        all_agents: dict[str, dict[str, Any]] = {}
        total_input = total_output = total_thinking = total_cached = 0
        total_api_calls = 0
        for child in sorted(lingtai_dir.iterdir()):
            if not child.is_dir():
                continue
            agent_json = child / ".agent.json"
            if not agent_json.exists():
                continue
            child_meta = read_json(agent_json)
            # Sibling agent's own live state + usage — curated from its
            # published Agent record instead of an independent .status.json
            # read plus a raw token_ledger.jsonl scan. This is a deliberate
            # scope change from "lifetime ledger total" to "current session
            # usage" (the record's usage block), matching the record's own
            # session/molt-boundary semantics rather than re-deriving a
            # second, possibly-disagreeing total.
            child_record = read_agent_record(child)
            child_session = (child_record or {}).get("session") if isinstance(child_record, dict) else None
            child_usage = (child_record or {}).get("usage") if isinstance(child_record, dict) else None
            if not isinstance(child_session, dict):
                child_session = {}
            if not isinstance(child_usage, dict):
                child_usage = {}
            all_agents[child.name] = {
                "input": child_usage.get("input_tokens", 0),
                "output": child_usage.get("output_tokens", 0),
                "thinking": child_usage.get("thinking_tokens", 0),
                "cached": child_usage.get("cached_tokens", 0),
                "calls": child_usage.get("api_calls", 0),
                "state": child_session.get("state") or child_meta.get("state", "?"),
                "molt_count": int(child_meta.get("molt_count") or 0),
                "model": child_meta.get("llm", {}).get("model", "?"),
            }
            agent_input = child_usage.get("input_tokens", 0) or 0
            agent_output = child_usage.get("output_tokens", 0) or 0
            agent_thinking = child_usage.get("thinking_tokens", 0) or 0
            agent_cached = child_usage.get("cached_tokens", 0) or 0
            agent_calls = child_usage.get("api_calls", 0) or 0
            total_input += agent_input
            total_output += agent_output
            total_thinking += agent_thinking
            total_cached += agent_cached
            total_api_calls += agent_calls

        addons = init.get("addons", [])
        addon_status: dict[str, bool] = {}
        for addon in addons:
            if addon == "telegram":
                addon_status[addon] = (
                    agent_path / ".secrets" / "telegram.json"
                ).exists()
            elif addon == "imap":
                addon_status[addon] = (agent_path / ".secrets" / "imap.json").exists()
            elif addon == "feishu":
                addon_status[addon] = (agent_path / ".secrets" / "feishu.json").exists()
            elif addon == "wechat":
                addon_status[addon] = (
                    agent_path / ".secrets" / "wechat" / "config.json"
                ).exists()
            else:
                addon_status[addon] = addon in init.get("mcp", {})

        delegates_path = agent_path / "delegates" / "ledger.jsonl"
        delegate_count = 0
        if delegates_path.exists():
            try:
                with delegates_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if line.strip():
                            delegate_count += 1
            except OSError:
                pass
        knowledge_count = count_matching(agent_path / "knowledge", "KNOWLEDGE.md")
        skill_count = count_matching(agent_path / ".library" / "custom", "SKILL.md")
        codex_dir = agent_path / "codex"
        codex_count = 0
        if codex_dir.exists():
            codex_count = len(
                [path for path in codex_dir.iterdir() if path.suffix == ".md"]
            )

        grand_total = total_input + total_output + total_thinking
        return {
            "current_agent": current_agent,
            "nickname": nickname,
            "agent_id": agent_id,
            "created_at": created_at,
            "started_at": started_at,
            "uptime": uptime,
            "molt_count": molt_count,
            "summaries_count": summaries_count,
            "admin": admin,
            "current_model": current_model,
            "current_provider": current_provider,
            "active_preset_path": active_preset_path,
            "default_preset_path": default_preset_path,
            "preset_models": preset_models,
            "agent_state": agent_state,
            "ctx": ctx,
            "context_limit": context_limit,
            "language": language,
            "soul_delay": soul_delay,
            "knowledge_count": knowledge_count,
            "skill_count": skill_count,
            "delegate_count": delegate_count,
            "codex_count": codex_count,
            "capability_names": capability_names,
            "all_agents": all_agents,
            "total_input": total_input,
            "total_output": total_output,
            "total_thinking": total_thinking,
            "total_cached": total_cached,
            "total_api_calls": total_api_calls,
            "addon_status": addon_status,
            "manifest": manifest,
            "grand_total": grand_total,
            "fmt": fmt,
            "fmt_duration": fmt_duration,
            "fmt_time": fmt_time,
            "age_since": age_since,
            "join_limited": join_limited,
        }
