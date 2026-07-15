"""Deterministic fake LLM adapter for detached-supervisor real-process tests.

Registered into `LLMService`'s process-global adapter registry only inside a
spawned supervisor subprocess, gated by the
`LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM` env var (see
`lingtai.kernel.daemon_supervisor.supervisor.maybe_register_test_fake_llm`).
A monkeypatch cannot cross the process boundary the way every other daemon
test fakes an LLM (`tests/test_daemon.py`'s `FakeService`), so this registers
a real (if trivial) `LLMAdapter`/`ChatSession` pair instead — the smallest
deterministic stand-in that satisfies `LLMService.create_session` /
`ChatSession.send` / `LLMService.make_tool_result`.

Optional `LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP` env var (seconds)
makes `.send()` block before returning — used to prove the supervisor's own
deadline/reclaim watcher can interrupt an in-flight "LLM call".
"""
from __future__ import annotations

import os
import time

from lingtai.kernel.llm.base import ChatSession, LLMResponse, ToolCall, UsageMetadata
from lingtai.kernel.llm.interface import ChatInterface, ToolResultBlock
from lingtai.llm.base import LLMAdapter

PROVIDER_NAME = "lingtai-supervisor-test-fake"


class _FakeChatSession(ChatSession):
    def __init__(self, *, has_runtime_key: bool = False):
        self._interface = ChatInterface()
        self._send_count = 0
        # The manager-created acceptance path requires the capsule-delivered
        # credential before this deterministic provider will emit completion.
        # This proves the runtime received a value without persisting/printing it.
        self._has_runtime_key = has_runtime_key

    @property
    def interface(self):
        return self._interface

    def send(self, message):
        sleep_s = os.environ.get("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SLEEP")
        if sleep_s:
            time.sleep(float(sleep_s))
        self._send_count += 1
        usage_extra = {
            "codex_auth_path_sha8": "a1b2c3d4",
            "codex_pool_source_index": 1,
            "codex_pool_size": 2,
            "codex_pool_weight": 1,
            "codex_pool_model_scope": "gpt-5.6",
            "codex_pool_source_ref": "must-not-copy",
            "unsafe": "secret",
        }
        if os.environ.get("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SCENARIO") == "artifact":
            if self._send_count == 1:
                return LLMResponse(
                    text="Checking...",
                    tool_calls=[ToolCall(
                        name="read", args={"file_path": "/dev/null"}, id="fake-read",
                    )],
                    usage=UsageMetadata(input_tokens=100, output_tokens=20,
                                        thinking_tokens=5, cached_tokens=10,
                                        extra=usage_extra),
                )
            if self._send_count == 2:
                return LLMResponse(
                    text="Finishing...",
                    tool_calls=[ToolCall(
                        name="finish",
                        args={"status": "done", "summary": "Found 3 TODOs."},
                        id="fake-finish",
                    )],
                )
            return LLMResponse(
                text="Task done. Found 3 TODOs.",
                usage=UsageMetadata(input_tokens=80, output_tokens=15,
                                    thinking_tokens=3, cached_tokens=5,
                                    extra=usage_extra),
            )
        if (
            os.environ.get("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH") == "1"
            and self._has_runtime_key
            and self._send_count == 1
        ):
            return LLMResponse(
                text="finalizing fake task",
                tool_calls=[ToolCall(
                    name="finish",
                    args={"status": "done", "summary": "fake finished"},
                    id="fake-finish",
                )],
            )
        if (
            os.environ.get("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH") == "1"
            and self._has_runtime_key
        ):
            return LLMResponse(text="Task done. Summarized architecture.")
        return LLMResponse(text="fake-response: task complete", tool_calls=[])


class _FakeAdapter(LLMAdapter):
    def __init__(self, **kwargs):
        self._api_key = kwargs.get("api_key")

    def create_chat(self, **kwargs):
        return _FakeChatSession(has_runtime_key=isinstance(self._api_key, str) and bool(self._api_key))

    def generate(self, model, contents, **kwargs):  # pragma: no cover - unused by daemon path
        return LLMResponse(text="fake-response")

    def make_tool_result_message(self, tool_name, result, *, tool_call_id=None):
        return ToolResultBlock(id=tool_call_id or "fake-tool-call", name=tool_name, content=result)

    def is_quota_error(self, exc):  # pragma: no cover - unused by daemon path
        return False


def register() -> None:
    from lingtai.llm.service import LLMService

    LLMService.register_adapter(PROVIDER_NAME, lambda **kwargs: _FakeAdapter(**kwargs))


def maybe_register_from_env() -> None:
    if os.environ.get("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM") == "1":
        register()
