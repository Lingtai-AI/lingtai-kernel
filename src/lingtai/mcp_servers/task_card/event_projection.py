"""Pure projection of canonical agent events into bounded Task Card text."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Any

from lingtai.kernel.state import AgentState
from lingtai.kernel.trace_redaction import redact_text


_PUBLIC_URL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:https?|wss?|ftp)://[^\s<>{}\[\]()\"'`]+"
)
_QUOTED_LOCAL_PATH_RE = re.compile(
    r"(?i)(?P<quote>[`\"'])(?:[A-Z]:[\\/]|~/|"
    r"/(?:Users|home|root|tmp|private|var|etc|usr|opt|Volumes|mnt|srv|"
    r"workspace|app)(?:/|(?=[`\"']))|\\\\)[^`\"'\r\n]+(?P=quote)"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|"
    r"\\\\[^\\/\s]+[\\/][^\\/\s]+[\\]?)[^\s<>{}\[\]()\"'`]*"
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:~/[^\s<>{}\[\]()\"'`]+|"
    r"/(?:Users|home|root|tmp|private|var|etc|usr|opt|Volumes|mnt|srv|"
    r"workspace|app)(?:/[^\s<>{}\[\]()\"'`]*)?)"
)
_PROVIDER_IDENTIFIER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:"
    r"(?:ou|oc|om|omt|cli)_[A-Za-z0-9_-]{8,}|"
    r"(?:img|file)_v\d+_[A-Za-z0-9_-]{8,}|"
    r"(?:resp|msg|call|thread|run|req)_[A-Za-z0-9_-]{16,}"
    r")(?![A-Za-z0-9_-])"
)


class TaskCardEventProjection:
    """Shared, transport-free event grouping, redaction, and rendering core."""

    REASONING_CAP = 500
    TEXT_LIMIT = 3500
    HEADER = "📋 TASK CARD"
    FOOTER = (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10"
    )
    DEFAULT_NORMAL_ROWS = 1
    METADATA_MAX_CHARS = 150
    METADATA_MAX_LINES = 2
    TIME_PREFIX = "Last Updated: "
    AGENT_STATES = frozenset(state.value for state in AgentState)

    EVENT_WINDOW = 10
    EVENT_REASONING_CAP = 300
    EVENT_TEXT_CAP = 500
    MAX_EVENTS_PER_CALL = 24
    API_CALL_DIVIDER = "──────────"

    @staticmethod
    def sanitize_public_text(value: object) -> str:
        """Remove private locator classes from a cross-route public frame."""
        text = redact_text(str(value))
        text = _PUBLIC_URL_RE.sub("<REDACTED:url>", text)
        text = _QUOTED_LOCAL_PATH_RE.sub("<REDACTED:path>", text)
        text = _WINDOWS_ABSOLUTE_PATH_RE.sub("<REDACTED:path>", text)
        text = _POSIX_ABSOLUTE_PATH_RE.sub("<REDACTED:path>", text)
        return _PROVIDER_IDENTIFIER_RE.sub("<REDACTED:provider_id>", text)

    @classmethod
    def footer(cls, normal_rows: int) -> str:
        return f"{cls.FOOTER}, current: {normal_rows})."

    @staticmethod
    def format_current_time(now: datetime) -> str:
        """Render ``HH:MM:SS UTC±HH`` or empty text for a naive instant."""
        offset = now.utcoffset()
        if offset is None:
            return ""
        total = offset.total_seconds()
        sign = "-" if total < 0 else "+"
        hours = int(abs(total) // 3600)
        return f"{now.strftime('%H:%M:%S')} UTC{sign}{hours:02d}"

    @classmethod
    def format_row_timestamp(cls, ts: object) -> str:
        """Convert a canonical epoch into the Task Card row timestamp."""
        if type(ts) not in (int, float):
            return ""
        if isinstance(ts, float) and not math.isfinite(ts):
            return ""
        try:
            local = datetime.fromtimestamp(ts).astimezone()
        except (OverflowError, OSError, ValueError):
            return ""
        return cls.format_current_time(local)

    @classmethod
    def project_agent_text_event(
        cls,
        event: dict[str, Any],
        *,
        text_cap: int | None = None,
    ) -> dict[str, Any] | None:
        """Project only canonical public ``diary`` text."""
        if event.get("type") != "diary":
            return None
        if event.get("hidden") is True or event.get("visibility") not in (
            None,
            "public",
        ):
            return None
        text = event.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        text = cls.sanitize_public_text(text).strip()
        cap = cls.EVENT_TEXT_CAP if text_cap is None else text_cap
        if len(text) > cap:
            text = text[: cap - 1] + "…"
        return {"kind": "text", "text": text}

    @classmethod
    def project_tool_call_row(
        cls,
        event: dict[str, Any],
        *,
        reasoning_cap: int | None = None,
    ) -> dict[str, Any] | None:
        """Extract the fixed safe-field allowlist from one tool call."""
        if event.get("type") != "tool_call":
            return None
        tool_name = cls.machine_identifier(event.get("tool_name"), limit=64)
        if tool_name is None:
            return None
        tool_args = event.get("tool_args")
        if not isinstance(tool_args, dict):
            return None
        reasoning = tool_args.get("_reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = ""
        reasoning = cls.sanitize_public_text(reasoning)
        cap = cls.EVENT_REASONING_CAP if reasoning_cap is None else reasoning_cap
        if len(reasoning) > cap:
            reasoning = reasoning[: cap - 1] + "…"
        row: dict[str, Any] = {"tool": tool_name, "reasoning": reasoning}
        call_id = event.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            row.update({"_tool_call_id": call_id, "status": "???"})
        action = cls.machine_identifier(tool_args.get("action"), limit=64)
        if action is not None:
            row["tool_action"] = action
        started_at = cls.format_row_timestamp(event.get("ts"))
        if started_at:
            row["started_at"] = started_at
        return row

    @classmethod
    def project_event(
        cls,
        event: dict[str, Any],
        *,
        text_cap: int | None = None,
        reasoning_cap: int | None = None,
    ) -> dict[str, Any] | None:
        text = cls.project_agent_text_event(event, text_cap=text_cap)
        if text is not None:
            return text
        row = cls.project_tool_call_row(event, reasoning_cap=reasoning_cap)
        if row is not None:
            row["kind"] = "tool"
            return row
        return None

    @staticmethod
    def event_group_id(event: dict[str, Any], fallback: int) -> str:
        value = event.get("api_call_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return f"legacy:{fallback}"

    @classmethod
    def group_events(
        cls,
        projected: list[tuple[dict[str, Any], dict[str, Any]]],
        *,
        window: int | None = None,
        max_events_per_call: int | None = None,
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        limit = (
            cls.MAX_EVENTS_PER_CALL
            if max_events_per_call is None
            else max_events_per_call
        )
        for index, (event, row) in enumerate(projected):
            group_id = cls.event_group_id(event, index)
            group = by_id.get(group_id)
            if group is None:
                group = {"api_call_id": group_id, "events": []}
                by_id[group_id] = group
                groups.append(group)
            events = group["events"]
            if len(events) < limit:
                events.append(row)
        count = cls.EVENT_WINDOW if window is None else window
        return groups[-count:]

    @staticmethod
    def flatten_groups(
        groups: list[dict[str, Any]],
        *,
        include_group_id: bool = False,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for group in groups:
            group_id = group.get("api_call_id")
            for event in group.get("events", []):
                row = dict(event)
                if include_group_id:
                    row["group_id"] = group_id
                else:
                    row.pop("group_id", None)
                    row.pop("_tool_call_id", None)
                if row.get("kind") == "tool":
                    row.pop("kind", None)
                rows.append(row)
        return rows

    @staticmethod
    def project_final_carrier_metadata(
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Project safe session telemetry from a final-carrier event."""
        if event.get("type") != "notification_block_injected":
            return None
        envelope = event.get("_meta")
        if not isinstance(envelope, dict):
            return {}
        agent_meta = envelope.get("agent_meta")
        if not isinstance(agent_meta, dict):
            return {}
        state = agent_meta.get("agent_state")
        if not isinstance(state, dict):
            return {}
        token_usage = state.get("token_usage")
        if not isinstance(token_usage, dict):
            return {}
        session = token_usage.get("session")
        if not isinstance(session, dict):
            return {}
        supported = (
            "session_cache_rate",
            "cache_miss_tokens",
            "cache_miss_budget",
            "api_calls",
            "context_tokens",
            "context_window",
            "context_usage",
        )
        return {key: session[key] for key in supported if key in session}

    @staticmethod
    def decode_event_line(raw: bytes) -> dict[str, Any] | None:
        line = raw.strip()
        if not line:
            return None
        try:
            event = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return event if isinstance(event, dict) else None

    @staticmethod
    def apply_tool_results(
        groups: list[dict[str, Any]],
        tool_results: dict[str, dict[str, Any]],
    ) -> bool:
        changed = False
        for group in groups:
            for row in group.get("events", []):
                result = tool_results.get(row.get("_tool_call_id"))
                if result is None:
                    continue
                status = result.get("status")
                row["status"] = (
                    "error"
                    if status == "error"
                    else "success"
                    if isinstance(status, str) and status
                    else "???"
                )
                elapsed_ms = result.get("elapsed_ms")
                if type(elapsed_ms) in (int, float) and elapsed_ms >= 0:
                    row["elapsed_s"] = elapsed_ms / 1000
                changed = True
        return changed

    @classmethod
    def render_event_groups(
        cls,
        groups: list[dict[str, Any]],
        *,
        normal_rows: int,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> str:
        rows: list[dict[str, Any]] = []
        for group in groups[-normal_rows:]:
            rows.append({"kind": "divider", "text": cls.API_CALL_DIVIDER})
            rows.extend(group.get("events", []))
        text = cls.format_task_card_text(
            "",
            "",
            "",
            rows=rows,
            metadata=metadata,
            normal_rows=normal_rows,
            now=now,
        )
        return text[: cls.TEXT_LIMIT] if len(text) > cls.TEXT_LIMIT else text

    @classmethod
    def format_task_card_text(
        cls,
        tool: str,
        action: str,
        reasoning: str,
        *,
        rows: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
        normal_rows: int = DEFAULT_NORMAL_ROWS,
        now: datetime | None = None,
    ) -> str:
        if rows is None:
            return cls.format_scalar_task_card_text(tool, action, reasoning)
        return cls.format_rows_task_card_text(
            rows,
            metadata=metadata,
            normal_rows=normal_rows,
            now=now,
        )

    @classmethod
    def format_scalar_task_card_text(
        cls,
        tool: str,
        action: str,
        reasoning: str,
    ) -> str:
        redacted = cls.sanitize_public_text(reasoning)
        if len(redacted) > cls.REASONING_CAP:
            excerpt = redacted[: cls.REASONING_CAP] + "…"
        else:
            excerpt = redacted
        tool = cls.machine_identifier(tool, limit=64) or ""
        action = cls.machine_identifier(action, limit=64) if tool else None
        label = f"{tool}.{action}" if action else tool
        if label:
            return f"{cls.HEADER}\n{label}: {excerpt}"
        return f"{cls.HEADER}\n{excerpt}" if excerpt else cls.HEADER

    @staticmethod
    def format_count(value: object) -> str | None:
        if type(value) is not int or value < 0:
            return None
        for threshold, suffix in (
            (1_000_000_000_000, "T"),
            (1_000_000_000, "B"),
            (1_000_000, "M"),
            (1_000, "k"),
        ):
            if value >= threshold:
                tenths = (value * 10 + threshold // 2) // threshold
                if suffix == "T":
                    tenths = min(tenths, 9_999)
                return f"{tenths // 10}.{tenths % 10}{suffix}"
        return str(value)

    @classmethod
    def format_metadata(cls, metadata: object) -> list[str]:
        if not isinstance(metadata, dict):
            return []

        session_parts: list[str] = []
        cache_rate = metadata.get("session_cache_rate")
        if (
            type(cache_rate) in {int, float}
            and not isinstance(cache_rate, bool)
            and 0 <= cache_rate <= 1
        ):
            session_parts.append(f"cache {float(cache_rate):.1%}")
        miss = cls.format_count(metadata.get("cache_miss_tokens"))
        budget = cls.format_count(metadata.get("cache_miss_budget"))
        if miss is not None:
            session_parts.append(
                f"miss {miss}/{budget}" if budget is not None else f"miss {miss}"
            )
        calls = cls.format_count(metadata.get("api_calls"))
        if calls is not None:
            session_parts.append(f"calls {calls}")

        context_parts: list[str] = []
        context = cls.format_count(metadata.get("context_tokens"))
        window = cls.format_count(metadata.get("context_window"))
        if context is not None:
            context_parts.append(
                f"{context}/{window}" if window is not None else context
            )
        usage = metadata.get("context_usage")
        if (
            type(usage) in {int, float}
            and not isinstance(usage, bool)
            and 0 <= usage <= 1
        ):
            context_parts.append(f"{float(usage):.0%}")

        agent_line: str | None = None
        lifecycle = metadata.get("agent_lifecycle")
        if lifecycle in (AgentState.STUCK.value, "offline"):
            agent_line = f"agent · {lifecycle} · try /refresh"
        elif lifecycle in cls.AGENT_STATES:
            agent_line = f"agent · {lifecycle}"

        session_line = (
            "session · " + " · ".join(session_parts) if session_parts else None
        )
        context_line = "ctx · " + " · ".join(context_parts) if context_parts else None
        lines: list[str] = []
        if agent_line and session_line:
            lines.append(f"{agent_line} · {session_line}")
        elif agent_line:
            lines.append(agent_line)
        elif session_line:
            lines.append(session_line)
        if context_line:
            lines.append(context_line)
        lines = lines[: cls.METADATA_MAX_LINES]
        if not lines:
            return []
        joined = "\n".join(lines)
        if len(joined) <= cls.METADATA_MAX_CHARS:
            return lines
        first = lines[0][: cls.METADATA_MAX_CHARS]
        remaining = cls.METADATA_MAX_CHARS - len(first) - 1
        if remaining <= 0 or len(lines) == 1:
            return [first]
        return [first, lines[1][:remaining]]

    @classmethod
    def format_rows_task_card_text(
        cls,
        rows: list[Any],
        *,
        metadata: dict[str, Any] | None = None,
        normal_rows: int = DEFAULT_NORMAL_ROWS,
        now: datetime | None = None,
    ) -> str:
        footer = cls.footer(normal_rows)
        tool_prepared: list[tuple[int, str, str, str, bool, str, str | None]] = []
        text_prepared: list[tuple[int, str]] = []
        api_prepared: list[tuple[int, str]] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind == "divider":
                api_prepared.append((idx, cls.API_CALL_DIVIDER))
                continue
            if kind == "text":
                text = cls.sanitize_public_text(row.get("text", "")).strip()
                if text:
                    text_prepared.append((idx, text[: cls.EVENT_TEXT_CAP]))
                continue
            if kind == "api_error":
                api_prepared.append((idx, cls.format_api_error_line(row)))
                continue
            tool = cls.machine_identifier(row.get("tool"), limit=64) or ""
            action = (
                cls.machine_identifier(row.get("tool_action"), limit=64)
                if tool
                else None
            )
            label = f"{tool}.{action}" if action else tool
            redacted = cls.sanitize_public_text(row.get("reasoning", ""))
            elapsed = cls.format_elapsed(row.get("elapsed_s", 0))
            done = bool(row.get("done", False))
            started_at = row.get("started_at", "")
            started_at = started_at if isinstance(started_at, str) else ""
            status = row.get("status")
            status = status if status in {"success", "error", "???"} else None
            tool_prepared.append(
                (idx, label, redacted, elapsed, done, started_at, status)
            )

        metadata_lines = cls.format_metadata(metadata)
        time_line = f"{cls.TIME_PREFIX}{cls.render_time(now)}"
        if not tool_prepared and not text_prepared and not api_prepared:
            lines = [cls.HEADER, "", footer]
            lines.extend(metadata_lines)
            lines.append(time_line)
            return "\n".join(lines)

        api_scaffold = sum(len(line) + 1 for _, line in api_prepared)
        text_scaffold = sum(len(text) + 4 for _, text in text_prepared)
        tool_scaffold = 0
        for (
            _,
            label,
            _redacted,
            elapsed,
            done,
            started_at,
            status,
        ) in tool_prepared:
            marker = "✓ " if done or status == "success" else "• "
            prefix = f"{marker}{label}: " if label else marker
            stamp_suffix = f" · {started_at}" if started_at else ""
            status_suffix = f", {status}" if status else ""
            tool_scaffold += (
                len(prefix)
                + len(f" ({elapsed}s{status_suffix})")
                + len(stamp_suffix)
                + 2
            )
        fixed = (
            len(cls.HEADER)
            + 1
            + 1
            + len(footer)
            + sum(len(line) + 1 for line in metadata_lines)
            + len(time_line)
            + 1
            + api_scaffold
            + text_scaffold
            + tool_scaffold
        )
        budget = cls.TEXT_LIMIT - fixed
        divisor = max(1, len(tool_prepared) + len(text_prepared))
        per_row_cap = max(0, min(cls.REASONING_CAP, budget // divisor))

        by_idx: dict[int, str] = {}
        for idx, label, redacted, elapsed, done, started_at, status in tool_prepared:
            excerpt = (
                redacted[:per_row_cap] + "…"
                if len(redacted) > per_row_cap
                else redacted
            )
            marker = "✓ " if done or status == "success" else "• "
            prefix = f"{marker}{label}: " if label else marker
            stamp_suffix = f" · {started_at}" if started_at else ""
            status_suffix = f", {status}" if status else ""
            by_idx[idx] = f"{prefix}{excerpt} ({elapsed}s{status_suffix}){stamp_suffix}"
        for idx, text in text_prepared:
            excerpt = text[:per_row_cap] + "…" if len(text) > per_row_cap else text
            by_idx[idx] = f"• {excerpt}"
        for idx, line in api_prepared:
            by_idx[idx] = line

        lines = [cls.HEADER]
        lines.extend(by_idx[index] for index in sorted(by_idx))
        lines.append("")
        lines.append(footer)
        lines.extend(metadata_lines)
        lines.append(time_line)
        return "\n".join(lines)

    @classmethod
    def render_time(cls, now: datetime | None) -> str:
        if now is None:
            now = datetime.now().astimezone()
        return cls.format_current_time(now)

    @classmethod
    def machine_identifier(cls, value: object, *, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > limit:
            return None
        if cls.sanitize_public_text(value) != value:
            return None
        safe_punctuation = frozenset("._:/-")
        if not all(
            ch.isascii() and (ch.isalnum() or ch in safe_punctuation) for ch in value
        ):
            return None
        return value

    @classmethod
    def format_api_error_line(cls, row: dict[str, Any]) -> str:
        state = row.get("state")
        parts = ["API error"]
        error_type = cls.machine_identifier(row.get("error_type"), limit=48)
        if error_type is not None:
            parts.append(error_type)
        provider = cls.machine_identifier(row.get("provider"), limit=48)
        model = cls.machine_identifier(row.get("model"), limit=80)
        if provider is not None and model is not None:
            parts.append(f"{provider}/{model}")
        elif provider is not None or model is not None:
            parts.append(provider or model or "")
        status = row.get("status")
        if type(status) is int and 100 <= status <= 599:
            parts.append(f"HTTP {status}")
        code = cls.machine_identifier(row.get("code"), limit=64)
        if code is not None:
            parts.append(code)
        summary = " · ".join(parts)

        if state == "recovered":
            return f"✓ {summary} · recovered"
        if state == "error":
            return f"⚠️ {summary} · failed"
        attempt = row.get("attempt")
        max_attempts = row.get("max_attempts")
        if (
            type(attempt) is int
            and type(max_attempts) is int
            and attempt > 0
            and max_attempts > 0
        ):
            return f"⚠️ {summary} · retrying {attempt}/{max_attempts}"
        if type(attempt) is int and attempt > 0:
            return f"⚠️ {summary} · retrying (attempt {attempt})"
        return f"⚠️ {summary} · retrying"

    @staticmethod
    def format_elapsed(value: object) -> str:
        try:
            return str(max(0, int(float(value))))
        except (TypeError, ValueError):
            return "0"
