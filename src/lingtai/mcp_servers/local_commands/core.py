"""Channel-neutral data/control core for local messaging commands."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    {"command": "brief", "description": "Show current briefing"},
]


@dataclass(frozen=True)
class SignalResult:
    status: str
    error: str | None = None


@dataclass(frozen=True)
class BriefResult:
    status: str
    content: str | None = None


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


@dataclass(frozen=True)
class TaskCardCommandResult:
    status: str
    enabled: bool | None = None
    normal_rows: int | None = None


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
        """Parse/update Task Card preferences without rendering a response."""
        args = text.split()[1:]
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
        return TaskCardCommandResult(
            "ok",
            enabled=settings.enabled(),
            normal_rows=settings.normal_rows(),
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

    def read_brief(self) -> BriefResult:
        """Read the established briefing fallback chain."""
        agent_path = self._agent_path()
        if agent_path is None:
            return BriefResult("agent_dir_missing")
        content: str | None = None
        brief_path = agent_path / "system" / "brief.md"
        if brief_path.is_file():
            try:
                content = brief_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = None
        if not content:
            for path in sorted((agent_path / "knowledge").glob("*/brief.md")):
                try:
                    content = path.read_text(encoding="utf-8")
                    break
                except (OSError, UnicodeDecodeError):
                    continue
        if not content:
            try:
                init = json.loads(
                    (agent_path / "init.json").read_text(encoding="utf-8")
                )
                content = init.get("brief") or init.get("manifest", {}).get("brief")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                content = None
        if not content or not content.strip():
            return BriefResult("not_found")
        return BriefResult("ok", content)

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
            except (json.JSONDecodeError, OSError):
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
        context_limit = meta_llm.get("context_limit") or manifest.get(
            "context_limit", 0
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

        status = read_json(agent_path / ".status.json")
        runtime = status.get("runtime", {})
        tokens_status = status.get("tokens", {})
        ctx = tokens_status.get("context", {})
        agent_state = runtime.get("state", agent_meta.get("state", "?"))
        uptime = runtime.get("uptime_seconds", 0)
        started_at = runtime.get("started_at") or started_at

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
            child_status = read_json(child / ".status.json")
            child_runtime = child_status.get("runtime", {})
            ledger_path = child / "logs" / "token_ledger.jsonl"
            agent_input = agent_output = agent_thinking = agent_cached = 0
            agent_calls = 0
            if ledger_path.exists():
                try:
                    with ledger_path.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                                agent_input += entry.get("input", 0)
                                agent_output += entry.get("output", 0)
                                agent_thinking += entry.get("thinking", 0)
                                agent_cached += entry.get("cached", 0)
                                agent_calls += 1
                            except json.JSONDecodeError:
                                continue
                except OSError:
                    pass
            all_agents[child.name] = {
                "input": agent_input,
                "output": agent_output,
                "thinking": agent_thinking,
                "cached": agent_cached,
                "calls": agent_calls,
                "state": child_runtime.get("state") or child_meta.get("state", "?"),
                "molt_count": int(child_meta.get("molt_count") or 0),
                "model": child_meta.get("llm", {}).get("model", "?"),
            }
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
