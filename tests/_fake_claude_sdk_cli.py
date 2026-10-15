#!/usr/bin/env python3
"""Fake `claude` CLI speaking just enough of the Agent SDK wire protocol.

Used only by tests to prove the Claude Agent SDK *runner* (not a mock of the
SDK itself) actually drives a real ``claude_agent_sdk.ClaudeSDKClient``
connection end to end through the daemon's detached execution boundary.
Appends every received stdin line to ``FAKE_CLAUDE_SDK_CALLS`` (if set) for
assertions.

Protocol (NDJSON over stdio, matching the stream-json wire the real CLI
uses and the SDK's ``_internal/message_parser.py`` parses):

- ``-v`` / ``--version``: print a version string and exit (the SDK's
  best-effort version probe).
- Otherwise: read one JSON line per loop iteration. The first line of the
  whole session gets a ``system``/``init`` message with a session id first.
- Every ``{"type": "control_request", "request_id": R, ...}`` line (the SDK
  sends an ``initialize`` one immediately on ``connect()``, before any
  query) gets one ``{"type": "control_response", "response": {"subtype":
  "success", "request_id": R, "response": {}}}`` reply.
- Every ``{"type": "user", ...}`` line gets one ``assistant`` text message
  plus one terminal ``result`` message. The reply text echoes the received
  user content so a test can assert exactly what the runner sent.
- ``FAKE_CLAUDE_SDK_DELAY_MS`` (optional): before replying to a ``user``
  message, wait up to this many milliseconds (via ``select`` on stdin, so
  real control traffic can still be observed meanwhile) instead of replying
  immediately — this is what makes a genuine mid-turn
  ``client.interrupt()`` test possible: the test can be certain the turn is
  still "in flight" when it fires the interrupt. If an
  ``{"type": "control_request", ..., "request": {"subtype": "interrupt"}}``
  line arrives during that wait, this CLI answers it immediately and then
  emits an *aborted* result (``is_error: true``,
  ``terminal_reason: "aborted_streaming"``) instead of the normal reply —
  matching the real CLI's own interrupt contract as documented by the SDK
  (``ResultMessage.terminal_reason`` for an interrupted turn).
"""
import json
import os
import select
import sys


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _log_call(calls_path: str | None, line: str) -> None:
    if calls_path:
        with open(calls_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _respond_control_request(event: dict) -> None:
    _emit({
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": event.get("request_id"),
            "response": {},
        },
    })


def _wait_for_interrupt_or_timeout(calls_path: str | None, timeout_s: float) -> bool:
    """Return True iff an interrupt control_request arrived before the deadline.

    Any other line that arrives during the wait (another control_request,
    stray input) is answered/logged the same as the main loop would, and the
    wait continues with whatever time remains.
    """
    import time

    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ready, _, _ = select.select([sys.stdin], [], [], remaining)
        if not ready:
            return False
        raw_line = sys.stdin.readline()
        if raw_line == "":
            return False  # EOF
        line = raw_line.strip()
        if not line:
            continue
        _log_call(calls_path, line)
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "control_request":
            continue
        request = event.get("request") or {}
        if request.get("subtype") == "interrupt":
            _respond_control_request(event)
            return True
        _respond_control_request(event)


def main() -> int:
    if any(arg in ("-v", "--version") for arg in sys.argv[1:]):
        print("2.1.284 (Claude Code)")
        return 0

    calls_path = os.environ.get("FAKE_CLAUDE_SDK_CALLS")
    delay_ms = float(os.environ.get("FAKE_CLAUDE_SDK_DELAY_MS", "0") or "0")
    delay_s = delay_ms / 1000.0
    session_id = "fake-claude-sdk-session"
    sent_init = False
    turn = 0
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        _log_call(calls_path, line)
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not sent_init:
            _emit({"type": "system", "subtype": "init", "session_id": session_id})
            sent_init = True
        if event.get("type") == "control_request":
            _respond_control_request(event)
            continue
        if event.get("type") != "user":
            continue
        turn += 1
        message = event.get("message") or {}
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content)

        if os.environ.get("FAKE_CLAUDE_SDK_CRASH_AFTER_USER") == "1":
            # Simulate a crash/EOF: exit with no assistant/result at all for
            # this turn, so the SDK's receive_messages() generator just ends.
            sys.stdout.flush()
            return 1

        if delay_s > 0 and _wait_for_interrupt_or_timeout(calls_path, delay_s):
            _emit({
                "type": "result",
                "subtype": "success",
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": True,
                "num_turns": turn,
                "session_id": session_id,
                "result": None,
                "terminal_reason": "aborted_streaming",
            })
            continue

        reply = f"fake reply {turn} to: {text}"
        _emit({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": reply}],
            },
            "session_id": session_id,
        })
        _emit({
            "type": "result",
            "subtype": "success",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "num_turns": turn,
            "session_id": session_id,
            "result": reply,
            "usage": {
                "input_tokens": 10, "output_tokens": 5,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 2,
            },
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
