"""Kimi Code adapter — drives the local ``kimi`` CLI as a LingTai brain.

The CLI owns authentication and transport.  LingTai remains authoritative for
canonical history and tool execution; Kimi is asked to return one JSON action
inside its assistant text.  Kimi's prompt-mode ``stream-json`` output also
emits a machine-readable ``session.resume_hint``.  We retain that opaque id and
use ``--session`` on later turns so the CLI/provider can keep a stable cache
identity.  Prompt stdout omits usage, but Kimi persists ``usage.record`` with
cache counters in the private session wire; this adapter reads that durable
record and never fabricates a hit when the wire is unavailable.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from lingtai.kernel.llm.base import (
    ChatSession,
    FunctionSchema,
    LLMResponse,
    ToolCall,
    UsageMetadata,
)
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.kernel.logging import get_logger

from lingtai.llm.base import LLMAdapter

logger = get_logger()

_DEFAULT_TIMEOUT_S = 600
_DEFAULT_CONTEXT_WINDOW = 200_000
# macOS ARG_MAX is commonly 262144.  Keep a conservative explicit limit so a
# growing canonical history fails as a typed context error rather than an
# opaque E2BIG from the host.
_DEFAULT_MAX_PROMPT_BYTES = 128 * 1024

_OVERFLOW_MARKERS = (
    "prompt is too long",
    "context window",
    "context length",
    "too many tokens",
    "maximum context",
    "input is too long",
    "token limit",
    "argument list too long",
    "e2big",
)
_AUTH_MARKERS = (
    "no model configured",
    "not configured",
    "not authenticated",
    "authentication",
    "unauthorized",
    "api key",
    "login",
    "401",
)
_RESERVED_EXTRA_FLAGS = {
    "--yolo",
    "-y",
    "--continue",
    "-c",
    "--session",
    "-S",
}

# Kimi Code has no verified separate system-prompt flag.  Keep the protocol
# explicit in the first prompt and use a short continuation reminder after the
# CLI session has been established.
_PROTOCOL = """\
You are the REASONING CORE of an external agent runtime called LingTai. You do
NOT execute tools yourself; LingTai executes tools and returns their results.

On every turn, read the supplied agent instructions and conversation, decide the
SINGLE next action, and output EXACTLY ONE JSON object on one line — no prose,
markdown fences, or commentary before or after it.

Choose exactly one form:
  {"action": "tool_call", "name": "<tool_name>", "input": { <arguments> }}
  {"action": "tool_calls", "calls": [{"name": "...", "input": {...}}]}
  {"action": "final", "text": "<your reply>"}

Only call a tool listed under AVAILABLE TOOLS and match its input schema. Never
use Kimi Code's own coding, shell, filesystem, web, MCP, or other native tools;
represent the next LingTai tool call as JSON instead. Output ONLY the JSON object.
"""
_CONTINUATION = """\
Continue the LingTai reasoning-core protocol.  Do not use Kimi Code native
coding, shell, filesystem, web, MCP, or other tools.  Output exactly one JSON
action object and no surrounding prose.
"""


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _map_usage(usage: dict | None = None) -> UsageMetadata:
    """Map Kimi's durable wire ``usage.record`` fields.

    Prompt-mode stdout omits usage, but Kimi persists each turn in
    ``agents/main/wire.jsonl`` with camelCase fields. Absent records remain
    honest zero/unknown usage.
    """
    if not isinstance(usage, dict):
        return UsageMetadata(
            input_tokens=0, output_tokens=0, thinking_tokens=0, cached_tokens=0
        )
    cache_read = _safe_int(
        usage.get("inputCacheRead", usage.get("cache_read_input_tokens"))
    )
    cache_creation = _safe_int(
        usage.get("inputCacheCreation", usage.get("cache_creation_input_tokens"))
    )
    other = _safe_int(usage.get("inputOther", usage.get("input_tokens")))
    output = _safe_int(usage.get("output", usage.get("output_tokens")))
    return UsageMetadata(
        input_tokens=cache_read + cache_creation + other,
        output_tokens=output,
        thinking_tokens=0,
        cached_tokens=cache_read,
    )


def _extract_json_object(text: str) -> dict | None:
    """Extract the first balanced JSON object, tolerating fences/prose."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = re.sub(r"^\s*json\s*", "", s, flags=re.IGNORECASE).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _parse_stream_json(stdout: str) -> dict:
    """Parse Kimi prompt-mode JSONL without assuming provider usage fields.

    The installed CLI emits assistant lines shaped like OpenAI messages and a
    final meta line ``{role: meta, type: session.resume_hint, session_id: ...}``.
    Unknown event lines are retained only as a count.  Native Kimi tool calls
    are surfaced so the adapter can fail closed instead of executing them.
    """
    events = []
    assistant_parts: list[str] = []
    native_tool_calls: list[dict] = []
    resume_session_id: str | None = None

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        if event.get("type") == "session.resume_hint" or (
            event.get("role") == "meta" and event.get("session_id")
        ):
            candidate = event.get("session_id")
            if isinstance(candidate, str) and candidate.strip():
                resume_session_id = candidate.strip()
        if event.get("role") == "assistant":
            assistant_parts.append(_content_text(event.get("content")))
            calls = event.get("tool_calls")
            if isinstance(calls, list):
                native_tool_calls.extend(c for c in calls if isinstance(c, dict))

    if not events:
        # Be permissive if a CLI version ignores --output-format and returns
        # plain text; action parsing still remains deterministic, but there is
        # no session/cache identity to retain.
        return {
            "assistant_text": stdout.strip(),
            "resume_session_id": None,
            "native_tool_calls": [],
            "event_count": 0,
        }

    return {
        "assistant_text": "".join(assistant_parts).strip(),
        "resume_session_id": resume_session_id,
        "native_tool_calls": native_tool_calls,
        "event_count": len(events),
    }


class KimiCodeError(RuntimeError):
    """A Kimi Code CLI invocation failed."""


class KimiCodeAuthError(KimiCodeError):
    """The Kimi CLI is unavailable or has no configured model/auth route."""


class KimiCodeContextOverflow(KimiCodeError):
    """The prompt or Kimi context exceeded a supported limit."""


class KimiCodeChatSession(ChatSession):
    """Canonical LingTai session backed by Kimi prompt-mode invocations."""

    def __init__(
        self,
        *,
        adapter: "KimiCodeAdapter",
        model: str,
        system_prompt: str,
        tools: list[FunctionSchema],
        interface: ChatInterface,
        context_window: int,
    ) -> None:
        self._adapter = adapter
        self._model = model
        self._system_prompt = system_prompt
        self._tools = tools
        self._interface = interface
        self._context_window = context_window
        self._kimi_session_id: str | None = None
        self._remote_entry_count = 0
        self._pending_resume_session_id: str | None = None
        # Seeded from the constructor content so the first per-turn resync in
        # ``SessionManager.send`` is already recognised as a no-op.
        self._stable_context_digest = self._system_block_digest()

    @property
    def interface(self) -> ChatInterface:
        return self._interface

    @property
    def kimi_session_id(self) -> str | None:
        """Opaque CLI session identity, not a reported cache-hit metric."""
        return self._kimi_session_id

    def send(self, message) -> LLMResponse:
        restore = None if message is None else self._snapshot_interface()
        try:
            if isinstance(message, str):
                self._interface.add_user_message(message)
            elif isinstance(message, list):
                self._interface.add_tool_results(message)

            if self.pre_request_hook is not None:
                self.pre_request_hook(self._interface)

            self._pending_resume_session_id = None

            def _do_call():
                self._interface.enforce_tool_pairing()
                prompt = self._render_prompt()
                try:
                    result = self._adapter._invoke(
                        prompt,
                        self._model,
                        session_id=self._kimi_session_id,
                    )
                except KimiCodeContextOverflow:
                    # A failed remote turn may or may not have been committed.
                    # Drop the opaque identity and replay canonical history on
                    # the shared overflow retry rather than mixing both states.
                    self._reset_remote_session()
                    raise
                raw = result[2]
                candidate = (
                    raw.get("resume_session_id") if isinstance(raw, dict) else None
                )
                self._pending_resume_session_id = (
                    candidate
                    if isinstance(candidate, str) and candidate
                    else self._kimi_session_id
                )
                return result

            (action, usage, raw), total_dropped, rounds = (
                self._run_with_overflow_recovery(_do_call)
            )
            if rounds > 0:
                self._inject_overflow_notice(total_dropped=total_dropped, rounds=rounds)
            response = self._record_response(action, usage, raw)
            self._kimi_session_id = self._pending_resume_session_id
            self._remote_entry_count = (
                len(self._interface._entries) if self._kimi_session_id else 0
            )
            return response
        except Exception:
            # Canonical history is restored, but remote state is deliberately
            # not trusted after an invocation/parser failure.
            self._reset_remote_session()
            if restore is not None:
                restore()
            raise

    def _snapshot_interface(self):
        iface = self._interface
        entries = list(iface._entries)
        contents = [(entry, list(entry.content)) for entry in entries]
        next_id = iface._next_id

        def restore() -> None:
            iface._entries[:] = entries
            for entry, content in contents:
                entry.content[:] = content
            iface._next_id = next_id

        return restore

    def _reset_remote_session(self) -> None:
        self._kimi_session_id = None
        self._remote_entry_count = 0
        self._pending_resume_session_id = None

    def _system_block_digest(self) -> str:
        """Digest of the stable context the remote session was opened with."""
        payload = json.dumps(
            {
                "system_prompt": self._system_prompt,
                "tools": [t.to_dict() for t in self._tools],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _resync_stable_context(self) -> None:
        """Re-publish the system block, dropping the CLI session only on change.

        ``SessionManager.send`` rebuilds the system prompt and tools on *every*
        turn because they may have changed. Resetting unconditionally here would
        clear ``_kimi_session_id`` before each call, so ``--session`` would never
        be sent and every turn would replay the whole conversation without the
        provider cache identity. The CLI only emits ``session.resume_hint`` on
        the turn that opens a session, so a discarded id is not re-offered.
        """
        digest = self._system_block_digest()
        self._interface.add_system(
            self._system_prompt,
            tools=[t.to_dict() for t in self._tools] if self._tools else None,
        )
        if digest != self._stable_context_digest:
            self._stable_context_digest = digest
            self._reset_remote_session()

    def update_tools(self, tools: list[FunctionSchema] | None) -> None:
        self._tools = list(tools) if tools else []
        self._resync_stable_context()

    def update_system_prompt(self, system_prompt: str) -> None:
        self._system_prompt = system_prompt
        self._resync_stable_context()

    def commit_tool_results(self, tool_results: list) -> None:
        self._interface.add_tool_results(tool_results)

    def context_window(self) -> int:
        return self._context_window

    @staticmethod
    def _is_context_overflow_error(exc: Exception) -> bool:
        return isinstance(exc, KimiCodeContextOverflow)

    def _record_response(
        self, action: dict, usage: UsageMetadata, raw: Any
    ) -> LLMResponse:
        blocks: list = []
        tool_calls: list[ToolCall] = []
        text = ""
        kind = action.get("action")
        if kind == "tool_calls":
            for call in action.get("calls", []) or []:
                name = call.get("name")
                if not name:
                    continue
                args = call.get("input") or call.get("args") or {}
                cid = f"kc_{uuid.uuid4().hex[:24]}"
                blocks.append(ToolCallBlock(id=cid, name=name, args=args))
                tool_calls.append(ToolCall(name=name, args=args, id=cid))
        elif kind == "tool_call" or (kind is None and action.get("name")):
            name = action.get("name")
            args = action.get("input") or action.get("args") or {}
            if name:
                cid = f"kc_{uuid.uuid4().hex[:24]}"
                blocks.append(ToolCallBlock(id=cid, name=name, args=args))
                tool_calls.append(ToolCall(name=name, args=args, id=cid))

        if not tool_calls:
            text = str(
                action.get("text")
                if action.get("text") is not None
                else action.get("_raw", "")
            )
            blocks.append(TextBlock(text=text))

        self._interface.add_assistant_message(
            blocks,
            model=self._model,
            provider="kimi-code",
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thinking_tokens": usage.thinking_tokens,
                "cached_tokens": usage.cached_tokens,
            },
        )
        return LLMResponse(text=text, tool_calls=tool_calls, usage=usage, raw=raw)

    def _render_prompt(self) -> str:
        use_remote = self._kimi_session_id is not None
        start = self._remote_entry_count if use_remote else 0
        parts: list[str] = [_CONTINUATION if use_remote else _PROTOCOL, ""]
        if not use_remote:
            parts.extend(
                ["# AGENT SYSTEM INSTRUCTIONS", self._system_prompt or "(none)", ""]
            )
            parts.append("# AVAILABLE TOOLS")
            if self._tools:
                for tool in self._tools:
                    parts.append(f"## {tool.name}")
                    if tool.description:
                        parts.append(tool.description.strip())
                    try:
                        schema = json.dumps(tool.parameters, ensure_ascii=False)
                    except (TypeError, ValueError):
                        schema = str(tool.parameters)
                    parts.append(f"input schema: {schema}")
                    parts.append("")
            else:
                parts.extend(["(no tools available — respond with a final answer)", ""])
            parts.append("# CONVERSATION")
            parts.append(self._render_conversation(0))
        else:
            parts.append("# CONTINUATION")
            parts.append(self._render_conversation(start))
        parts.extend(
            ["", "# NOW", "Decide your next action and output the single JSON object."]
        )
        return "\n".join(parts)

    def _render_conversation(self, start: int) -> str:
        lines: list[str] = []
        entries = self._interface._entries
        if start >= len(entries):
            return "(no new messages)"
        for entry in entries[start:]:
            if entry.role == "system":
                continue
            for block in entry.content:
                if isinstance(block, ThinkingBlock):
                    continue
                if isinstance(block, TextBlock):
                    prefix = "ASSISTANT" if entry.role == "assistant" else "USER"
                    lines.append(f"{prefix}: {block.text}")
                elif isinstance(block, ToolCallBlock):
                    try:
                        args = json.dumps(block.args, ensure_ascii=False)
                    except (TypeError, ValueError):
                        args = str(block.args)
                    lines.append(
                        f"ASSISTANT_ACTION: called tool `{block.name}` with input {args}"
                    )
                elif isinstance(block, ToolResultBlock):
                    content = block.content
                    if not isinstance(content, str):
                        try:
                            content = json.dumps(content, ensure_ascii=False)
                        except (TypeError, ValueError):
                            content = str(content)
                    lines.append(f"TOOL_RESULT [{block.name}]: {content}")
        return "\n".join(lines) if lines else "(no messages yet)"


class KimiCodeAdapter(LLMAdapter):
    """LLMAdapter that drives Kimi Code's local ``kimi`` CLI."""

    def __init__(
        self,
        *,
        model: str | None = None,
        cli_path: str = "kimi",
        api_key: str | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        max_rpm: int = 0,
        extra_argv: list[str] | None = None,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,
        max_prompt_bytes: int = _DEFAULT_MAX_PROMPT_BYTES,
        cwd: str | Path | None = None,
        kimi_home: str | Path | None = None,
        skills_dir: str | Path | None = None,
    ) -> None:
        self._model = model or "kimi-for-coding"
        self._cli_path = cli_path
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._extra_argv = list(extra_argv or [])
        for arg in self._extra_argv:
            if arg in _RESERVED_EXTRA_FLAGS:
                raise ValueError(f"Kimi Code adapter owns reserved flag {arg!r}")
        self._context_window = context_window
        self._max_prompt_bytes = max_prompt_bytes
        # Number of durable usage.record entries already consumed per opaque
        # Kimi session id. This keeps each ChatSession response turn-local.
        self._wire_usage_cursor: dict[str, int] = {}
        self._setup_gate(max_rpm)

        self._cwd = (
            Path(cwd)
            if cwd
            else Path(tempfile.gettempdir()) / "lingtai-kimi-code-brain"
        )
        self._kimi_home = Path(kimi_home) if kimi_home else self._cwd / "home"
        self._skills_dir = (
            Path(skills_dir) if skills_dir else self._cwd / "empty-skills"
        )
        for path in (self._cwd, self._kimi_home, self._skills_dir):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.warning("[kimi-code] could not create isolated path %s", path)

    def create_chat(
        self,
        model: str,
        system_prompt: str,
        tools: list[FunctionSchema] | None = None,
        *,
        json_schema: dict | None = None,
        force_tool_call: bool = False,
        interface: ChatInterface | None = None,
        thinking: str = "default",
        interaction_id: str | None = None,
        context_window: int = 0,
    ) -> ChatSession:
        iface = interface or ChatInterface()
        tool_list = list(tools) if tools else []
        if interface is None:
            iface.add_system(
                system_prompt,
                tools=[t.to_dict() for t in tool_list] if tool_list else None,
            )
        session = KimiCodeChatSession(
            adapter=self,
            model=model or self._model,
            system_prompt=system_prompt,
            tools=tool_list,
            interface=iface,
            context_window=context_window or self._context_window,
        )
        return self._wrap_with_gate(session)

    def generate(
        self,
        model: str,
        contents: str | list,
        *,
        system_prompt: str | None = None,
        temperature: float | None = None,
        json_schema: dict | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        if isinstance(contents, list):
            user_text = "\n".join(
                c.get("text", "") if isinstance(c, dict) else str(c) for c in contents
            )
        else:
            user_text = str(contents)
        parts = []
        if system_prompt:
            parts.extend([system_prompt, ""])
        parts.append(user_text)
        if json_schema:
            parts.extend(
                [
                    "",
                    "Respond with ONLY a JSON object matching this schema, no prose: "
                    + json.dumps(json_schema, ensure_ascii=False),
                ]
            )
        result, usage, raw = self._invoke_raw("\n".join(parts), model or self._model)
        return LLMResponse(text=result, usage=usage, raw=raw)

    def make_tool_result_message(
        self, tool_name: str, result: dict, *, tool_call_id: str | None = None
    ) -> ToolResultBlock:
        return ToolResultBlock(
            id=tool_call_id or f"kc_{uuid.uuid4().hex[:24]}",
            name=tool_name,
            content=result,
        )

    def is_quota_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(
            marker in msg for marker in ("rate limit", "429", "usage limit", "quota")
        )

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["KIMI_CODE_HOME"] = str(self._kimi_home)
        env["KIMI_DISABLE_TELEMETRY"] = "1"
        env["KIMI_CODE_NO_AUTO_UPDATE"] = "1"
        # Preserve the daemon's canonical fallback: an explicitly supplied
        # KIMI_MODEL_API_KEY wins; otherwise map the operator's existing Kimi
        # aliases without ever logging or returning their values.
        if self._api_key is not None:
            env["KIMI_MODEL_API_KEY"] = self._api_key
        elif not env.get("KIMI_MODEL_API_KEY"):
            for source in (
                "KIMI_CODE_API_KEY",
                "KIMICODE_API_KEY",
                "KIMI_API_KEY",
                "MOONSHOT_API_KEY",
            ):
                value = env.get(source)
                if value:
                    env["KIMI_MODEL_API_KEY"] = value
                    break
        # Kimi's supported env-model synthesis is activated by
        # KIMI_MODEL_NAME + KIMI_MODEL_API_KEY.  Apply the daemon-compatible
        # defaults only when a key is available; with a keyless local login,
        # leave the operator's real config.toml model untouched.
        if env.get("KIMI_MODEL_API_KEY"):
            defaults = {
                "KIMI_MODEL_NAME": self._model,
                "KIMI_MODEL_PROVIDER_TYPE": "kimi",
                "KIMI_MODEL_BASE_URL": "https://api.kimi.com/coding/v1",
                "KIMI_MODEL_MAX_CONTEXT_SIZE": str(self._context_window),
            }
            for key, default in defaults.items():
                if not env.get(key):
                    env[key] = default
        return env

    def _invoke_raw(
        self,
        prompt: str,
        model: str,
        *,
        session_id: str | None = None,
    ) -> tuple[str, UsageMetadata, dict]:
        prompt_bytes = len(prompt.encode("utf-8"))
        if prompt_bytes > self._max_prompt_bytes:
            raise KimiCodeContextOverflow(
                f"Kimi prompt is {prompt_bytes} bytes; configured limit is {self._max_prompt_bytes}"
            )

        env = self._build_env()
        cmd = [self._cli_path]
        # With an API key, Kimi's supported env-model synthesis creates the
        # reserved default alias in memory; passing --model would bypass that
        # synthetic alias and make a private home report "model not configured".
        # Keyless configured homes still receive an explicit --model argument.
        if model and not (env.get("KIMI_MODEL_API_KEY") and env.get("KIMI_MODEL_NAME")):
            cmd += ["--model", model]
        if session_id:
            cmd += ["--session", session_id]
        cmd += [
            "--prompt",
            prompt,
            "--output-format",
            "stream-json",
            "--skills-dir",
            str(self._skills_dir),
        ]
        cmd += self._extra_argv

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=env,
                cwd=str(self._cwd),
                timeout=self._timeout_s,
            )
        except FileNotFoundError as exc:
            raise KimiCodeAuthError(
                f"`{self._cli_path}` not found on PATH; install Kimi Code or configure its local CLI route"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise KimiCodeError(f"kimi CLI timed out after {self._timeout_s}s") from exc
        except OSError as exc:
            if exc.errno == errno.E2BIG or "argument list too long" in str(exc).lower():
                raise KimiCodeContextOverflow(
                    "kimi CLI rejected the prompt as too large"
                ) from exc
            raise KimiCodeError(f"could not launch kimi CLI: {exc}") from exc

        stdout = proc.stdout or ""
        stderr = (proc.stderr or "").strip()
        if proc.returncode != 0:
            self._raise_cli_error(proc.returncode, stderr, stdout)
        if not stdout.strip():
            raise KimiCodeError(
                f"kimi CLI produced no output (exit {proc.returncode}); stderr: {stderr[:300]}"
            )

        parsed = _parse_stream_json(stdout)
        if parsed["native_tool_calls"]:
            raise KimiCodeError(
                "kimi CLI emitted native tool calls; kimi-code provider requires JSON LingTai actions"
            )
        result = parsed["assistant_text"]
        if not result:
            raise KimiCodeError("kimi CLI stream contained no assistant response")
        usage, usage_records = self._read_session_usage(
            parsed["resume_session_id"] or session_id
        )
        if usage is None:
            usage = _map_usage(None)
        raw = {
            "output_format": "stream-json",
            "resume_session_id": parsed["resume_session_id"],
            "event_count": parsed["event_count"],
            "usage_source": "private_session_wire" if usage_records else "unavailable",
            "usage_records": usage_records,
        }
        return result, usage, raw

    def _read_session_usage(
        self, session_id: str | None
    ) -> tuple[UsageMetadata | None, int]:
        """Read newly appended ``usage.record`` entries for one CLI session.

        Kimi's print writer intentionally omits usage from stdout, while the
        session wire persists real cache counters. Restrict reads to the
        adapter's private home and return only aggregated numeric counters.
        """
        if not session_id:
            return None, 0
        index_path = self._kimi_home / "session_index.jsonl"
        if not index_path.is_file():
            return None, 0
        session_dir: Path | None = None
        try:
            for line in index_path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("sessionId") != session_id:
                    continue
                raw_dir = entry.get("sessionDir")
                if isinstance(raw_dir, str) and raw_dir:
                    session_dir = Path(raw_dir)
                    if not session_dir.is_absolute():
                        session_dir = self._kimi_home / session_dir
                    break
        except OSError:
            return None, 0
        if session_dir is None:
            return None, 0
        try:
            home = self._kimi_home.resolve()
            session_dir = session_dir.resolve()
            session_dir.relative_to(home)
        except (OSError, ValueError):
            return None, 0

        wire_path = session_dir / "agents" / "main" / "wire.jsonl"
        if not wire_path.is_file():
            return None, 0
        records: list[dict] = []
        try:
            for line in wire_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                usage = (
                    event.get("usage") if event.get("type") == "usage.record" else None
                )
                if isinstance(usage, dict):
                    records.append(usage)
        except OSError:
            return None, 0

        start = self._wire_usage_cursor.get(session_id, 0)
        new_records = records[start:]
        self._wire_usage_cursor[session_id] = len(records)
        if not new_records:
            return None, 0
        totals = {
            "inputCacheRead": sum(
                _safe_int(r.get("inputCacheRead")) for r in new_records
            ),
            "inputCacheCreation": sum(
                _safe_int(r.get("inputCacheCreation")) for r in new_records
            ),
            "inputOther": sum(_safe_int(r.get("inputOther")) for r in new_records),
            "output": sum(_safe_int(r.get("output")) for r in new_records),
        }
        return _map_usage(totals), len(new_records)

    def _raise_cli_error(self, returncode: int, stderr: str, stdout: str) -> None:
        detail = stderr or stdout[:500] or "(no output)"
        low = detail.lower()
        if any(marker in low for marker in _OVERFLOW_MARKERS):
            raise KimiCodeContextOverflow(detail[:500])
        if any(marker in low for marker in _AUTH_MARKERS):
            raise KimiCodeAuthError(
                "kimi CLI has no configured model/auth route; configure Kimi Code locally "
                f"(detail: {detail[:300]})"
            )
        raise KimiCodeError(f"kimi CLI exited {returncode}: {detail[:500]}")

    def _invoke(
        self,
        prompt: str,
        model: str,
        *,
        session_id: str | None = None,
    ) -> tuple[dict, UsageMetadata, dict]:
        result, usage, raw = self._invoke_raw(prompt, model, session_id=session_id)
        action = _extract_json_object(result)
        if action is None:
            logger.warning("[kimi-code] no JSON action parsed; treating as final text")
            action = {"action": "final", "_raw": result}
        return action, usage, raw

    @property
    def context_window_size(self) -> int:
        return self._context_window
