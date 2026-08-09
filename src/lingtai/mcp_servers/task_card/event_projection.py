"""Pure projection of canonical agent events into bounded Task Card text."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from lingtai.kernel.state import AgentState
from lingtai.kernel.trace_redaction import redact_text


class TaskCardEventProjection:
    """Shared, transport-free event grouping, redaction, and rendering core."""

    REASONING_CAP = 500
    TEXT_LIMIT = 3500
    HEADER = "📋 ACTIVITIES"
    FOOTER = (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10"
    )
    DEFAULT_NORMAL_ROWS = 1
    METADATA_MAX_CHARS = 500
    METADATA_MAX_LINES = 2
    TIME_PREFIX = "Last Updated: "
    AGENT_STATES = frozenset(state.value for state in AgentState)

    EVENT_WINDOW = 10
    EVENT_REASONING_CAP = 300
    EVENT_TEXT_CAP = 500
    MAX_EVENTS_PER_CALL = 24
    MAX_ELAPSED_MS = 9_999_999
    API_CALL_DIVIDER = "──────────"

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
        text = redact_text(text).strip()
        cap = cls.EVENT_TEXT_CAP if text_cap is None else text_cap
        if len(text) > cap:
            text = text[: cap - 1] + "…"
        row: dict[str, Any] = {"kind": "text", "text": text}
        raw_ts = event.get("ts")
        if type(raw_ts) in (int, float) and not isinstance(raw_ts, bool):
            try:
                ts = float(raw_ts)
            except (OverflowError, ValueError):
                ts = None
            if ts is not None and math.isfinite(ts):
                row["_ts"] = ts
        api_call_id = event.get("api_call_id")
        if isinstance(api_call_id, str) and api_call_id:
            row["_api_call_id"] = api_call_id
        return row

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
        tool_name = event.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            return None
        tool_args = event.get("tool_args")
        if not isinstance(tool_args, dict):
            return None
        reasoning = tool_args.get("_reasoning", "")
        if not isinstance(reasoning, str):
            reasoning = ""
        reasoning = redact_text(reasoning)
        cap = cls.EVENT_REASONING_CAP if reasoning_cap is None else reasoning_cap
        if len(reasoning) > cap:
            reasoning = reasoning[: cap - 1] + "…"
        row: dict[str, Any] = {"tool": tool_name, "reasoning": reasoning}
        call_id = event.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            row.update({"_tool_call_id": call_id, "status": "???"})
        # Preserve the provider round's api_call_id so a pure-tool turn (no
        # notification_block_injected carrier) can still match the llm_response
        # per-call usage through the _api_call_id lookup. Jason 2026-08-08.
        api_call_id = event.get("api_call_id")
        if isinstance(api_call_id, str) and api_call_id:
            row["_api_call_id"] = api_call_id
        action = tool_args.get("action")
        if isinstance(action, str) and action:
            row["tool_action"] = action
        # No per-row wall-clock stamp: each API-call group renders exactly one
        # timestamp above its API metadata line (Jason 2026-08-09), so tool rows
        # stay compact and the API-call timeline is uncluttered.
        raw_ts = event.get("ts")
        if type(raw_ts) in (int, float) and not isinstance(raw_ts, bool):
            try:
                ts = float(raw_ts)
            except (OverflowError, ValueError):
                ts = None
            if ts is not None and math.isfinite(ts):
                row["_ts"] = ts
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
        last_tool_ts: float | None = None
        for index, (event, row) in enumerate(projected):
            group_id = cls.event_group_id(event, index)
            group = by_id.get(group_id)
            if group is None:
                group = {"api_call_id": group_id, "events": []}
                by_id[group_id] = group
                groups.append(group)
            events = group["events"]
            # The LLM API round trip Jason watches as ``active (N sec)`` is the
            # gap between consecutive progress events: the previous tool_call's
            # own ``ts`` (stream order, so a group's first row still sees the
            # previous group's last tool call; only the very first tool row of
            # the stream has no prior progress and reads 0.0).
            raw_ts = row.get("_ts")
            if type(raw_ts) in (int, float) and not isinstance(raw_ts, bool):
                ts = float(raw_ts)
                if last_tool_ts is None:
                    row["api_delay_s"] = 0.0
                else:
                    row["api_delay_s"] = max(0.0, round(ts - last_tool_ts, 2))
                last_tool_ts = ts
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
                    row.pop("_ts", None)
                    row.pop("_usage", None)
                    row.pop("_api_call_id", None)
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
    def project_current_call_usage(
        event: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        """Extract per-call LLM usage from a ``notification_block_injected``
        carrier. Returns ``(call_id, {output, cache_miss, cache_rate,
        context})`` so the tailer can attach it to the matching tool row.
        ``context`` is the session's actual ``context_tokens`` count riding the
        same carrier (never derived from a percentage); absent/invalid values
        simply omit the key."""
        if event.get("type") != "notification_block_injected":
            return None
        call_id = event.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return None
        envelope = event.get("_meta")
        if not isinstance(envelope, dict):
            return None
        agent_meta = envelope.get("agent_meta")
        if not isinstance(agent_meta, dict):
            return None
        state = agent_meta.get("agent_state")
        if not isinstance(state, dict):
            return None
        token_usage = state.get("token_usage")
        if not isinstance(token_usage, dict):
            return None
        current = token_usage.get("current_call")
        if not isinstance(current, dict):
            return None
        supported = ("output", "cache_miss", "cache_rate")
        usage = {key: current[key] for key in supported if key in current}
        session = token_usage.get("session")
        if usage and isinstance(session, dict):
            context = session.get("context_tokens")
            if type(context) is int and context >= 0:
                usage["context"] = context
        return (call_id, usage) if usage else None

    @staticmethod
    def project_llm_response_usage(
        event: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        """Extract per-call LLM usage from a ``llm_response`` event.

        Pure-text turns (an assistant reply with no tool call) never carry a
        ``notification_block_injected`` carrier, so their per-call token usage
        has to come from the ``llm_response`` event itself. Returns
        ``(api_call_id, {output, cache_miss, cache_rate, context})``; estimated
        token counts are still shown (they are the only signal a pure-text turn
        has) but missing/zero input degrades to ``None``. ``context`` is the
        event's ``input_tokens`` — the exact value the kernel records as that
        round's session ``context_tokens`` (``ctx_total_tokens``), an actual
        count, never reconstructed from the cache percentage.
        """
        if event.get("type") != "llm_response":
            return None
        call_id = event.get("api_call_id")
        if not isinstance(call_id, str) or not call_id:
            return None
        output = event.get("output_tokens")
        cached = event.get("cached_tokens")
        total = event.get("input_tokens")
        if (
            type(output) is not int
            or type(cached) is not int
            or type(total) is not int
            or total <= 0
        ):
            return None
        usage: dict[str, Any] = {
            "output": output,
            "cache_miss": max(total - cached, 0),
            "context": total,
        }
        if cached > 0:
            usage["cache_rate"] = min(cached / total, 1.0)
        return (call_id, usage)

    @staticmethod
    def apply_tool_usages(
        groups: list[dict[str, Any]],
        usages: dict[str, dict[str, Any]],
    ) -> bool:
        """Attach per-call usage to rows by their ``_tool_call_id``.

        Pure-text rows carry ``_api_call_id`` instead, which the tailer keys
        from ``llm_response`` events; fall back to it so a text-only turn still
        gets its divider usage arrows. Tool rows now preserve the event's own
        ``api_call_id`` as ``_api_call_id``, so a pure-tool turn (e.g.
        ``context.molt``) with no carrier also matches the same ``llm_response``
        usage through that key. Jason 2026-08-08.
        """
        changed = False
        for group in groups:
            for row in group.get("events", []):
                usage = usages.get(row.get("_tool_call_id"))
                if usage is None:
                    usage = usages.get(row.get("_api_call_id"))
                if usage is None:
                    continue
                if row.get("_usage") == usage:
                    continue
                row["_usage"] = usage
                changed = True
        return changed

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
                    # Keep the raw ms so sub-second durations render exactly
                    # (e.g. ``412ms``) instead of rounding to ``0s``.
                    row["elapsed_ms"] = elapsed_ms
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
            # The group's API delay is the LLM round-trip gap since the previous
            # progress; the per-call usage rides the same divider line, both kept
            # out of tool rows.
            api_delay_s: float | None = None
            usage: dict[str, Any] | None = None
            group_ts: float | None = None
            for row in group.get("events", []):
                if group_ts is None:
                    v = row.get("_ts")
                    if type(v) in (int, float) and not isinstance(v, bool):
                        group_ts = float(v)
                v = row.get("api_delay_s")
                if api_delay_s is None and type(v) in (int, float) and not isinstance(v, bool) and v >= 0:
                    api_delay_s = float(v)
                u = row.get("_usage")
                if usage is None and isinstance(u, dict) and u:
                    usage = u
                if api_delay_s is not None and usage is not None:
                    break
            # One wall-clock stamp per API call, above the API metadata line
            # (Jason 2026-08-09): the first progress row of the group marks the
            # round trip's start; Jason later asked for the stamp to sit above
            # the metadata line and carry a leading symbol (2026-08-09
            # follow-up).
            if group_ts is not None:
                stamp = cls.format_row_timestamp(group_ts)
                if stamp:
                    rows.append({"kind": "api_ts", "text": f"· {stamp}"})
            info = cls.format_divider_info(api_delay_s, usage)
            if info:
                rows.append({"kind": "api_info", "text": info})
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
    def format_divider_info(
        cls,
        api_delay_s: float | None,
        usage: dict[str, Any] | None,
    ) -> str:
        """Compact divider line: `↻ x s` plus `↓out ↑miss ◌ ctx | cache%`.

        The down arrow denotes output tokens, the up arrow denotes cache miss
        (the two token flows that grow with each call); ``◌`` is the current
        context length (an actual token count) and the trailing percentage is
        that call's cache rate, joined as ``◌ <context> | <rate>``. Any piece
        missing from the event degrades silently — no dangling marker or
        separator — so old events without usage still render the delay alone.
        """
        parts: list[str] = []
        if api_delay_s is not None and api_delay_s > 0:
            parts.append(f"↻ {api_delay_s:.1f}s")
        if isinstance(usage, dict) and usage:
            out = usage.get("output")
            miss = usage.get("cache_miss")
            rate = usage.get("cache_rate")
            if type(out) is int and out >= 0:
                parts.append(f"\u2193{cls.format_count(out)}")
            if type(miss) is int and miss >= 0:
                parts.append(f"\u2191{cls.format_count(miss)}")
            context = cls.format_count(usage.get("context"))
            rate_text = (
                f"{float(rate):.1%}"
                if type(rate) in {int, float}
                and not isinstance(rate, bool)
                and 0 <= rate <= 1
                else None
            )
            if context is not None and rate_text is not None:
                parts.append(f"\u25cc {context} | {rate_text}")
            elif context is not None:
                parts.append(f"\u25cc {context}")
            elif rate_text is not None:
                parts.append(rate_text)
        return " ".join(parts)

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
        redacted = redact_text(reasoning)
        if len(redacted) > cls.REASONING_CAP:
            excerpt = redacted[: cls.REASONING_CAP] + "…"
        else:
            excerpt = redacted
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
        model = metadata.get("model")
        if isinstance(model, str) and model.strip():
            session_parts.append(model.strip())
        thinking = metadata.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            session_parts.append(thinking.strip())
        endpoint = metadata.get("endpoint")
        if isinstance(endpoint, str) and endpoint.strip():
            session_parts.append(f"@{endpoint.strip()}")
        context = cls.format_count(metadata.get("context_tokens"))
        window = cls.format_count(metadata.get("context_window"))
        usage = metadata.get("context_usage")
        if (
            type(usage) in {int, float}
            and not isinstance(usage, bool)
            and 0 <= usage <= 1
        ):
            if context is not None:
                session_parts.append(
                    f"ctx {float(usage):.0%} · {context}/{window}"
                    if window is not None
                    else f"ctx {float(usage):.0%}"
                )
            else:
                session_parts.append(f"ctx {float(usage):.0%}")
        elif context is not None:
            session_parts.append(
                f"ctx {context}/{window}" if window is not None else f"ctx {context}"
            )
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

        agent_part: str | None = None
        lifecycle = metadata.get("agent_lifecycle")
        if lifecycle in (AgentState.STUCK.value, "offline"):
            agent_part = f"agent · {lifecycle} · try /refresh"
        elif lifecycle == AgentState.ACTIVE.value:
            active_seconds = metadata.get("agent_active_seconds")
            if (
                type(active_seconds) in {int, float}
                and not isinstance(active_seconds, bool)
                and active_seconds >= 0
            ):
                agent_part = f"active ({float(active_seconds):.0f}s)"
            else:
                agent_part = "active"
        elif lifecycle in cls.AGENT_STATES:
            agent_part = f"agent · {lifecycle}"

        # Line 1 = running state: agent lifecycle + session identity/usage,
        # compact (no per-part prefixes; effort/endpoint/ctx/cache/miss are
        # self-evident from position). Line 2 = identity: device + short path.
        line1_parts: list[str] = []
        if agent_part:
            line1_parts.append(agent_part)
        line1_parts.extend(session_parts)
        line1 = " · ".join(line1_parts) if line1_parts else None

        device = cls.machine_identifier(
            metadata.get("device_short_name"), limit=64
        )
        shell_name = cls.machine_identifier(metadata.get("shell_name"), limit=48)
        line2_parts: list[str] = []
        if device is not None or shell_name is not None:
            parts: list[str] = []
            if device is not None:
                parts.append(device)
            if shell_name is not None:
                parts.append(f"shell {shell_name}")
            line2_parts.append("device · " + " · ".join(parts))
        working_dir = cls.machine_identifier(metadata.get("working_dir"), limit=220)
        if working_dir is not None:
            line2_parts.append(f"path · {cls._short_working_dir(working_dir)}")
        line2 = " | ".join(line2_parts) if line2_parts else None

        lines = [ln for ln in (line1, line2) if ln]
        if not lines:
            return []
        joined = "\n".join(lines)
        if len(joined) <= cls.METADATA_MAX_CHARS:
            return lines
        # Prefer preserving the session line; truncate from the identity line.
        budget1 = cls.METADATA_MAX_CHARS
        if len(lines) > 1:
            budget1 = min(budget1, len(lines[0]) + 1)
        first = lines[0][: cls.METADATA_MAX_CHARS]
        if len(lines) == 1:
            return [first]
        remaining = cls.METADATA_MAX_CHARS - len(first) - 1
        if remaining <= 0:
            return [first]
        return [first, lines[1][:remaining]]

    @classmethod
    def _short_working_dir(cls, working_dir: str) -> str:
        """Trim an absolute agent path to ``<project>/.lingtai/<agent>``."""
        idx = working_dir.find("/.lingtai/")
        if idx > 0:
            prev = working_dir.rfind("/", 0, idx)
            if prev >= 0:
                return working_dir[prev + 1 :]
            return working_dir
        return working_dir

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
        tool_prepared: list[tuple[int, str, str, str, bool, str | None]] = []
        text_prepared: list[tuple[int, str]] = []
        api_prepared: list[tuple[int, str]] = []
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            kind = row.get("kind")
            if kind == "divider":
                api_prepared.append((idx, cls.API_CALL_DIVIDER))
                continue
            if kind == "api_info":
                info = redact_text(str(row.get("text", ""))).strip()
                if info:
                    api_prepared.append((idx, info[: cls.EVENT_TEXT_CAP]))
                continue
            if kind == "api_ts":
                stamp = redact_text(str(row.get("text", ""))).strip()
                if stamp:
                    api_prepared.append((idx, stamp[: cls.EVENT_TEXT_CAP]))
                continue
            if kind == "text":
                text = redact_text(str(row.get("text", ""))).strip()
                if text:
                    text_prepared.append((idx, text[: cls.EVENT_TEXT_CAP]))
                continue
            if kind == "api_error":
                api_prepared.append((idx, cls.format_api_error_line(row)))
                continue
            tool = str(row.get("tool", ""))
            action = str(row.get("tool_action", ""))
            label = f"{tool}.{action}" if action else tool
            redacted = redact_text(str(row.get("reasoning", "")))
            done = bool(row.get("done", False))
            status = row.get("status")
            status = status if status in {"success", "error", "???"} else None
            # Duration in whole milliseconds (sub-second tool results were
            # useless as ``0s``). The LLM API round-trip gap since the previous
            # progress is rendered on the group divider, not here.
            elapsed = cls.format_elapsed_ms(cls.row_elapsed_ms(row))
            if status == "???":
                # A tool call with no result yet is genuinely running; showing
                # ``???`` wasted the slot without saying anything real.
                status_suffix = ""
                suffix = f" ({elapsed}, running)" if elapsed else " (running)"
            else:
                status_suffix = f", {status}" if status else ""
                suffix = f" ({elapsed}{status_suffix})"
            tool_prepared.append((idx, label, redacted, suffix, done, status))

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
            suffix,
            done,
            status,
        ) in tool_prepared:
            marker = "✓ " if done or status == "success" else "• "
            prefix = f"{marker}{label}: " if label else marker
            tool_scaffold += len(prefix) + len(suffix) + 2
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
        for idx, label, redacted, suffix, done, status in tool_prepared:
            excerpt = (
                redacted[:per_row_cap] + "…"
                if len(redacted) > per_row_cap
                else redacted
            )
            marker = "✓ " if done or status == "success" else "• "
            prefix = f"{marker}{label}: " if label else marker
            by_idx[idx] = f"{prefix}{excerpt}{suffix}"
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

    @staticmethod
    def machine_identifier(value: object, *, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > limit:
            return None
        safe_punctuation = frozenset("._:/\\-")
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
        code = row.get("code")
        if isinstance(code, str) and code:
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

    @staticmethod
    def row_elapsed_ms(row: dict[str, Any]) -> float:
        """Resolve a tool row's elapsed duration in whole milliseconds.

        Prefers the raw ``elapsed_ms`` captured from the tool result so
        sub-second durations are preserved exactly; falls back to converting
        the legacy ``elapsed_s`` field. Missing/malformed values degrade to 0.
        """
        raw_ms = row.get("elapsed_ms")
        if (
            type(raw_ms) in (int, float)
            and not isinstance(raw_ms, bool)
            and raw_ms >= 0
        ):
            return float(raw_ms)
        raw_s = row.get("elapsed_s")
        if (
            type(raw_s) in (int, float)
            and not isinstance(raw_s, bool)
            and raw_s >= 0
        ):
            return float(raw_s) * 1000
        return 0.0

    @classmethod
    def format_elapsed_ms(cls, value: object) -> str:
        """Render a tool row's elapsed duration adaptively.

        Under 0.1s (Jason 2026-08-08): whole milliseconds (``31ms``); at or above
        0.1s: one decimal second (``2.3s``). Milliseconds for sub-0.1s tools stay
        useful instead of rounding to ``0s``, and seconds read naturally for
        longer tools instead of a wide millisecond wall. Coerces defensively
        (floored, junk/non-finite/negative degrade to ``0ms``) and caps the
        display at ``MAX_ELAPSED_MS`` so a runaway timer cannot widen the row
        unboundedly.
        """
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "0ms"
        if not math.isfinite(number) or number < 0:
            return "0ms"
        if number < 100:
            return f"{min(int(number), cls.MAX_ELAPSED_MS)}ms"
        seconds = min(number, cls.MAX_ELAPSED_MS) / 1000
        return f"{seconds:.1f}s"
