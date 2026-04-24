# Tool-Pairing Invariant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ChatInterface` reject mid-tool-call user-message insertions and give recovery paths a way to close dangling tool_calls with synthetic error tool_results, so strict providers (OpenAI, DeepSeek V4) no longer get malformed chat-completions requests.

**Architecture:** Enforce the invariant at construction in `ChatInterface` (raise `PendingToolCallsError` when `add_user_message` / `add_user_blocks` would land on an unanswered `assistant[tool_calls]` tail). Add `close_pending_tool_calls(reason)` that synthesizes placeholder `ToolResultBlock`s with `[aborted: <reason>]` content. Call it from AED recovery (before injecting the revive system message) and session restore (after rehydration from disk).

**Tech Stack:** Python 3.13, `lingtai-kernel` package, pytest, `openai` SDK (for adapter tests).

**Design spec:** `docs/superpowers/specs/2026-04-23-tool-pairing-invariant-design.md`

---

## File Structure

**Modify:**
- `src/lingtai_kernel/llm/interface.py` — add `PendingToolCallsError`, `has_pending_tool_calls()`, `close_pending_tool_calls()`, guard `add_user_message()` / `add_user_blocks()`
- `src/lingtai_kernel/base_agent.py` — AED handler (~line 943-987): replace `pop_orphan_tool_call()` call with `close_pending_tool_calls(err_desc)` before sending the revive message
- `src/lingtai_kernel/session.py` — `restore_chat()` (~line 385): call `close_pending_tool_calls(...)` after `enforce_tool_pairing()` on rehydration

**Create:**
- `tests/test_chat_interface_invariant.py` — unit tests for the new invariant + helpers
- `tests/test_aed_tool_pairing.py` — integration test: simulate AED path against mocked client, verify request wire-shape

**No new files in `src/`** — the fix fits into existing canonical-interface machinery.

---

## Task 1: Add `PendingToolCallsError` and `has_pending_tool_calls()` to ChatInterface

**Files:**
- Modify: `src/lingtai_kernel/llm/interface.py`
- Test: `tests/test_chat_interface_invariant.py` (new file)

- [ ] **Step 1: Create the test file with the first failing test**

Create `tests/test_chat_interface_invariant.py`:

```python
"""Tests for ChatInterface tool-pairing invariant.

DeepSeek V4 and strict OpenAI reject chat-completions requests where an
assistant message with tool_calls is not immediately followed by matching
tool messages. These tests verify the canonical ChatInterface enforces
that invariant at construction time.
"""
from __future__ import annotations

import pytest

from lingtai_kernel.llm.interface import (
    ChatInterface,
    PendingToolCallsError,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def _iface_with_pending_tool_calls() -> ChatInterface:
    """Build an interface whose tail is assistant[tool_calls] with no results."""
    iface = ChatInterface()
    iface.add_system("system prompt")
    iface.add_user_message("hi")
    iface.add_assistant_message(
        [
            TextBlock(text="checking"),
            ToolCallBlock(id="call_A", name="noop", args={}),
        ],
    )
    return iface


class TestHasPendingToolCalls:
    def test_false_on_empty_interface(self):
        iface = ChatInterface()
        assert iface.has_pending_tool_calls() is False

    def test_false_when_tail_is_system(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        assert iface.has_pending_tool_calls() is False

    def test_false_when_tail_is_user(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("hi")
        assert iface.has_pending_tool_calls() is False

    def test_false_when_tail_is_plain_assistant(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message([TextBlock(text="hello")])
        assert iface.has_pending_tool_calls() is False

    def test_true_when_tail_is_assistant_with_tool_calls(self):
        iface = _iface_with_pending_tool_calls()
        assert iface.has_pending_tool_calls() is True

    def test_false_after_tool_results_appended(self):
        iface = _iface_with_pending_tool_calls()
        iface.add_tool_results([ToolResultBlock(id="call_A", name="noop", content="done")])
        assert iface.has_pending_tool_calls() is False
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py -v`

Expected: `ImportError: cannot import name 'PendingToolCallsError'` from `lingtai_kernel.llm.interface`.

- [ ] **Step 3: Add `PendingToolCallsError` and `has_pending_tool_calls()` in the interface module**

Open `src/lingtai_kernel/llm/interface.py`. Directly above the line `@dataclass` that begins the `TextBlock` class (near the top of the file, after imports), add:

```python
class PendingToolCallsError(Exception):
    """Raised when a user entry would be appended while the tail assistant
    turn still has unanswered ToolCallBlocks.

    Callers should close the pending tool_calls first — either by appending
    the real ToolResultBlocks via ``add_tool_results(...)``, or by calling
    ``close_pending_tool_calls(reason)`` to synthesize placeholder results
    (used by recovery paths like AED and session restore).
    """
```

Then inside the `ChatInterface` class, immediately after the `enforce_tool_pairing` method (look for the line `# -- Add methods ----------------------------------------------------------`), add a new method just BEFORE that section header:

```python
    def has_pending_tool_calls(self) -> bool:
        """True iff the tail entry is an assistant with unanswered ToolCallBlocks.

        "Unanswered" is defined positionally: if the very next entry contains
        ToolResultBlocks, the calls are considered answered. The canonical
        pattern is ``assistant[tool_calls] -> user[tool_results]``; anything
        else leaves the calls pending.
        """
        if not self._entries:
            return False
        last = self._entries[-1]
        if last.role != "assistant":
            return False
        return any(isinstance(b, ToolCallBlock) for b in last.content)
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py -v`

Expected: all 6 tests in `TestHasPendingToolCalls` PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/huangzesen/Documents/GitHub/lingtai-kernel
git add src/lingtai_kernel/llm/interface.py tests/test_chat_interface_invariant.py
git commit -m "$(cat <<'EOF'
feat(interface): add has_pending_tool_calls() and PendingToolCallsError

First step of the tool-pairing invariant refactor. The predicate
identifies interfaces whose tail is an assistant turn with unanswered
tool_calls — the shape that causes DeepSeek / OpenAI 400 errors. The
exception will be used by the add_user_message guards in the next task.

Design: docs/superpowers/specs/2026-04-23-tool-pairing-invariant-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `close_pending_tool_calls()` method

**Files:**
- Modify: `src/lingtai_kernel/llm/interface.py`
- Test: `tests/test_chat_interface_invariant.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_interface_invariant.py`:

```python
class TestClosePendingToolCalls:
    def test_noop_on_empty_interface(self):
        iface = ChatInterface()
        iface.close_pending_tool_calls("test")
        assert len(iface.entries) == 0

    def test_noop_when_tail_clean(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("hi")
        before = len(iface.entries)
        iface.close_pending_tool_calls("test")
        assert len(iface.entries) == before

    def test_synthesizes_results_for_pending(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("go")
        iface.add_assistant_message(
            [
                TextBlock(text="running"),
                ToolCallBlock(id="call_A", name="tool1", args={}),
                ToolCallBlock(id="call_B", name="tool2", args={"k": 1}),
            ],
        )
        assert iface.has_pending_tool_calls() is True
        iface.close_pending_tool_calls("network timeout")
        # Now tail should be a user entry with two ToolResultBlocks.
        assert iface.has_pending_tool_calls() is False
        tail = iface.entries[-1]
        assert tail.role == "user"
        assert len(tail.content) == 2
        result_A, result_B = tail.content
        assert isinstance(result_A, ToolResultBlock)
        assert result_A.id == "call_A"
        assert result_A.name == "tool1"
        assert "aborted" in result_A.content
        assert "network timeout" in result_A.content
        assert isinstance(result_B, ToolResultBlock)
        assert result_B.id == "call_B"
        assert result_B.name == "tool2"

    def test_idempotent(self):
        iface = _iface_with_pending_tool_calls()
        iface.close_pending_tool_calls("r1")
        entries_after_first = len(iface.entries)
        iface.close_pending_tool_calls("r2")
        # Second call is a no-op because tail is now clean.
        assert len(iface.entries) == entries_after_first
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py::TestClosePendingToolCalls -v`

Expected: `AttributeError: 'ChatInterface' object has no attribute 'close_pending_tool_calls'`.

- [ ] **Step 3: Implement `close_pending_tool_calls()`**

In `src/lingtai_kernel/llm/interface.py`, directly below the `has_pending_tool_calls()` method you added in Task 1, add:

```python
    def close_pending_tool_calls(self, reason: str) -> None:
        """Synthesize placeholder ToolResultBlocks for any unanswered tool_calls
        on the tail assistant entry. No-op if the tail has no pending calls.

        Used by recovery paths — AED retry, session restore from crashed
        process — to bring the interface into a valid state before appending
        a new user entry. Each placeholder carries the reason string so the
        model has context on the next turn.

        Idempotent: after one call, has_pending_tool_calls() returns False,
        and a second call no-ops.
        """
        if not self.has_pending_tool_calls():
            return
        last = self._entries[-1]
        pending = [b for b in last.content if isinstance(b, ToolCallBlock)]
        placeholders = [
            ToolResultBlock(id=b.id, name=b.name, content=f"[aborted: {reason}]")
            for b in pending
        ]
        self._append("user", placeholders)
```

- [ ] **Step 4: Run tests and verify they pass**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py -v`

Expected: all tests in both `TestHasPendingToolCalls` and `TestClosePendingToolCalls` PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/huangzesen/Documents/GitHub/lingtai-kernel
git add src/lingtai_kernel/llm/interface.py tests/test_chat_interface_invariant.py
git commit -m "$(cat <<'EOF'
feat(interface): add close_pending_tool_calls() for recovery paths

Synthesizes placeholder ToolResultBlocks (content="[aborted: <reason>]")
for unanswered tool_calls on the tail assistant entry. Idempotent.
Will be called by AED recovery and session restore paths in subsequent
tasks to satisfy the chat-completions positional invariant.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Guard `add_user_message()` and `add_user_blocks()` against invariant violations

**Files:**
- Modify: `src/lingtai_kernel/llm/interface.py`
- Test: `tests/test_chat_interface_invariant.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_chat_interface_invariant.py`:

```python
class TestAddUserMessageGuard:
    def test_raises_when_tail_has_pending_tool_calls(self):
        iface = _iface_with_pending_tool_calls()
        with pytest.raises(PendingToolCallsError):
            iface.add_user_message("new message")

    def test_succeeds_after_close(self):
        iface = _iface_with_pending_tool_calls()
        iface.close_pending_tool_calls("test")
        # Should not raise.
        iface.add_user_message("recovery message")
        tail = iface.entries[-1]
        assert tail.role == "user"
        assert len(tail.content) == 1
        assert isinstance(tail.content[0], TextBlock)
        assert tail.content[0].text == "recovery message"

    def test_succeeds_after_tool_results(self):
        iface = _iface_with_pending_tool_calls()
        iface.add_tool_results([ToolResultBlock(id="call_A", name="noop", content="done")])
        # Should not raise.
        iface.add_user_message("next")

    def test_clean_interface_not_affected(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("first")  # should not raise


class TestAddUserBlocksGuard:
    def test_raises_for_text_blocks_when_pending(self):
        iface = _iface_with_pending_tool_calls()
        with pytest.raises(PendingToolCallsError):
            iface.add_user_blocks([TextBlock(text="hi")])

    def test_tool_results_allowed_when_pending(self):
        """ToolResultBlocks ARE the legitimate closing operation."""
        iface = _iface_with_pending_tool_calls()
        # Should not raise.
        iface.add_user_blocks([ToolResultBlock(id="call_A", name="noop", content="ok")])
        assert iface.has_pending_tool_calls() is False

    def test_mixed_blocks_rejected_when_pending(self):
        iface = _iface_with_pending_tool_calls()
        with pytest.raises(PendingToolCallsError):
            iface.add_user_blocks([
                ToolResultBlock(id="call_A", name="noop", content="ok"),
                TextBlock(text="extra"),
            ])
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py::TestAddUserMessageGuard tests/test_chat_interface_invariant.py::TestAddUserBlocksGuard -v`

Expected: all new tests FAIL because `add_user_message` / `add_user_blocks` don't currently raise.

- [ ] **Step 3: Add guards to `add_user_message` and `add_user_blocks`**

In `src/lingtai_kernel/llm/interface.py`, find the existing methods (look for `def add_user_message` and `def add_user_blocks`) and replace them:

```python
    def add_user_message(self, text: str) -> InterfaceEntry:
        if self.has_pending_tool_calls():
            raise PendingToolCallsError(
                "Cannot append user message while the tail assistant turn has "
                "unanswered tool_calls. Call close_pending_tool_calls(reason) "
                "or add_tool_results(...) first."
            )
        return self._append("user", [TextBlock(text=text)])

    def add_user_blocks(self, blocks: list[ContentBlock]) -> InterfaceEntry:
        """Record a user entry with pre-built content blocks (for converters).

        ToolResultBlocks are the legitimate closing op for pending tool_calls
        and are allowed through. Anything else (text, mixed) is rejected when
        the tail has unanswered tool_calls.
        """
        is_tool_result_only = bool(blocks) and all(
            isinstance(b, ToolResultBlock) for b in blocks
        )
        if self.has_pending_tool_calls() and not is_tool_result_only:
            raise PendingToolCallsError(
                "Cannot append non-tool-result user blocks while the tail "
                "assistant turn has unanswered tool_calls."
            )
        return self._append("user", blocks)
```

- [ ] **Step 4: Run the new tests and verify they pass**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py -v`

Expected: all tests pass.

- [ ] **Step 5: Run the full LLM test suite to catch regressions**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_interface.py tests/test_llm_service.py tests/test_llm_utils.py tests/test_adapter_registry.py tests/test_deepseek_adapter.py -v`

Expected: everything passes. If any adapter test fails because it tries to inject a user message over a pending tool_call state, that's a real bug the invariant just caught — investigate the stack trace and fix the caller (do NOT soften the guard).

- [ ] **Step 6: Commit**

```bash
cd /Users/huangzesen/Documents/GitHub/lingtai-kernel
git add src/lingtai_kernel/llm/interface.py tests/test_chat_interface_invariant.py
git commit -m "$(cat <<'EOF'
feat(interface): guard add_user_message/add_user_blocks on pending tool_calls

Raises PendingToolCallsError when the tail assistant turn has unanswered
tool_calls. ToolResultBlocks via add_user_blocks remain allowed (that IS
the closing operation). All other appends force the caller to close the
pending state first.

Converts silent interface corruption (which strict providers like
DeepSeek reject with HTTP 400) into a loud programmer error caught in
tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `close_pending_tool_calls()` into AED recovery

**Files:**
- Modify: `src/lingtai_kernel/base_agent.py` (AED handler, around lines 943-988)
- Test: `tests/test_aed_tool_pairing.py` (new file)

- [ ] **Step 1: Create the integration test file**

Create `tests/test_aed_tool_pairing.py`:

```python
"""Integration test: AED recovery produces a well-formed request after a
tool-loop send fails.

Simulates the scenario that caused the real-world DeepSeek 400 cascade:
tool-call turn → send raises → AED kicks in → next request must not have
a dangling assistant[tool_calls] followed by a plain-text user message.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from lingtai_kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def test_close_pending_before_user_message_produces_valid_wire_format():
    """After close_pending_tool_calls + add_user_message, the canonical
    interface converts to a well-formed OpenAI wire sequence with no
    assistant[tool_calls] stranded before a user text message."""
    from lingtai.llm.interface_converters import to_openai

    iface = ChatInterface()
    iface.add_system("you are helpful")
    iface.add_user_message("start")
    iface.add_assistant_message(
        [
            TextBlock(text="checking"),
            ToolCallBlock(id="call_A", name="tool1", args={}),
            ToolCallBlock(id="call_B", name="tool2", args={}),
        ],
    )
    # Simulate: send(tool_results) raised; AED recovers by closing pending.
    iface.close_pending_tool_calls(reason="simulated: tool send failed")
    # AED then injects revive message — must not raise.
    iface.add_user_message("[system] retry — please continue")

    wire = to_openai(iface)
    roles = [m["role"] for m in wire]

    # The assistant turn with tool_calls must be IMMEDIATELY followed by
    # two 'tool' entries (one per call id), THEN the user message.
    assistant_idx = next(
        i for i, m in enumerate(wire)
        if m["role"] == "assistant" and m.get("tool_calls")
    )
    assert wire[assistant_idx + 1]["role"] == "tool"
    assert wire[assistant_idx + 2]["role"] == "tool"
    # The user turns after the tool entries carry the recovery message.
    assert wire[assistant_idx + 3]["role"] == "user"
    # Every tool_call id is answered before the next assistant/user text.
    answered_ids = {wire[assistant_idx + 1]["tool_call_id"], wire[assistant_idx + 2]["tool_call_id"]}
    assert answered_ids == {"call_A", "call_B"}
```

- [ ] **Step 2: Run the test and verify it passes already**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_aed_tool_pairing.py -v`

Expected: PASS. (This validates the shape — the AED code-path change in step 4 just wires the existing primitive into the kernel.)

- [ ] **Step 3: Read the current AED block**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && grep -n "aed_attempts\|pop_orphan_tool_call\|stuck_revive" src/lingtai_kernel/base_agent.py | head`

Confirm you see the same structure as the plan spec (around lines 938-988). The line `self._session.chat.interface.pop_orphan_tool_call()` is the destructive-pop we're replacing.

- [ ] **Step 4: Replace `pop_orphan_tool_call()` with `close_pending_tool_calls()`**

In `src/lingtai_kernel/base_agent.py`, find these lines (around 947-949):

```python
                        # Pop orphan tool call from interface (idempotent)
                        if self._session.chat is not None:
                            self._session.chat.interface.pop_orphan_tool_call()
```

Replace them with:

```python
                        # Close any dangling tool_calls with synthetic error
                        # tool_results, preserving the assistant turn and the
                        # error context (err_desc). Without this, a subsequent
                        # add_user_message — e.g. the AED revive below —
                        # would violate the chat-completions positional
                        # invariant and strict providers (DeepSeek, OpenAI)
                        # would 400 on the next request.
                        if self._session.chat is not None:
                            self._session.chat.interface.close_pending_tool_calls(
                                reason=err_desc or "aed_recovery"
                            )
```

- [ ] **Step 5: Run the AED integration test and the shape test**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_aed_tool_pairing.py tests/test_chat_interface_invariant.py tests/test_interface.py -v`

Expected: all pass. `test_interface.py` still exercises `pop_orphan_tool_call` directly (it's still a public method on the interface — we didn't remove it, just stopped calling it from AED), so those tests continue to pass.

- [ ] **Step 6: Smoke-test the module**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -c "from lingtai_kernel.base_agent import BaseAgent; print('OK')"`

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
cd /Users/huangzesen/Documents/GitHub/lingtai-kernel
git add src/lingtai_kernel/base_agent.py tests/test_aed_tool_pairing.py
git commit -m "$(cat <<'EOF'
fix(aed): close dangling tool_calls with error results before revive message

Replaces the destructive pop_orphan_tool_call() with
close_pending_tool_calls(err_desc), which preserves the assistant turn
and attaches the error as synthetic ToolResultBlocks. This satisfies
the chat-completions positional invariant that strict providers
(OpenAI, DeepSeek V4) enforce, while still giving the model useful
context on the next turn.

Fixes the DeepSeek 400 cascade ("insufficient tool messages following
tool_calls message") observed when a tool-loop send errored out.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Close dangling tool_calls on session restore

**Files:**
- Modify: `src/lingtai_kernel/session.py` (`restore_chat`, around lines 385-399)
- Test: `tests/test_chat_interface_invariant.py` (extend with restore-path test)

- [ ] **Step 1: Write failing test**

Append to `tests/test_chat_interface_invariant.py`:

```python
class TestRestoreDanglingToolCalls:
    def test_rehydrate_closes_pending_tool_calls(self):
        """A chat_history.jsonl persisted mid-tool-loop (process crashed
        between tool_call emission and tool_result arrival) should
        rehydrate with synthetic tool_results so the first send after
        restore is well-formed."""
        persisted = [
            {"id": 0, "role": "system", "system": "prompt", "timestamp": 0.0},
            {"id": 1, "role": "user",
             "content": [{"type": "text", "text": "go"}],
             "timestamp": 1.0},
            {"id": 2, "role": "assistant",
             "content": [
                 {"type": "text", "text": "checking"},
                 {"type": "tool_call", "id": "call_X", "name": "tool1", "args": {}},
             ],
             "timestamp": 2.0},
        ]
        iface = ChatInterface.from_dict(persisted)
        assert iface.has_pending_tool_calls() is True

        # The restore path will call these two methods in sequence.
        iface.enforce_tool_pairing()
        if iface.has_pending_tool_calls():
            iface.close_pending_tool_calls(
                reason="restored from disk — prior session ended mid-tool-loop"
            )

        # After recovery, no pending tool_calls and a synthetic tool_result entry.
        assert iface.has_pending_tool_calls() is False
        tail = iface.entries[-1]
        assert tail.role == "user"
        assert len(tail.content) == 1
        assert isinstance(tail.content[0], ToolResultBlock)
        assert tail.content[0].id == "call_X"
        assert "restored from disk" in tail.content[0].content
```

- [ ] **Step 2: Run test and confirm it passes already**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py::TestRestoreDanglingToolCalls -v`

Expected: PASS. (This validates the sequence — now we need to wire it into `Session.restore_chat`.)

- [ ] **Step 3: Update `Session.restore_chat`**

In `src/lingtai_kernel/session.py`, find this block (around line 385):

```python
    def restore_chat(self, state: dict) -> None:
        """Restore chat history with current system prompt and tools."""
        from .llm.interface import ChatInterface
        messages = state.get("messages")
        if messages:
            try:
                interface = ChatInterface.from_dict(messages)
                self._rebuild_session(interface)
                return
            except Exception as e:
                logger.warning(
                    f"[{self._display_name}] Failed to restore chat: {e}. Starting fresh.",
                    exc_info=True,
                )
        self.ensure_session()
```

Replace it with:

```python
    def restore_chat(self, state: dict) -> None:
        """Restore chat history with current system prompt and tools.

        Heals two classes of on-disk corruption before building the session:
        1. Set-level orphans (tool_call without result or vice versa) —
           handled by enforce_tool_pairing(), which strips them.
        2. Positional violations (assistant[tool_calls] at tail with no
           matching tool_results) — handled by close_pending_tool_calls(),
           which synthesizes placeholder error results so the next send is
           well-formed for strict providers (DeepSeek, OpenAI).
        """
        from .llm.interface import ChatInterface
        messages = state.get("messages")
        if messages:
            try:
                interface = ChatInterface.from_dict(messages)
                interface.enforce_tool_pairing()
                if interface.has_pending_tool_calls():
                    interface.close_pending_tool_calls(
                        reason="restored from disk — prior session ended mid-tool-loop"
                    )
                self._rebuild_session(interface)
                return
            except Exception as e:
                logger.warning(
                    f"[{self._display_name}] Failed to restore chat: {e}. Starting fresh.",
                    exc_info=True,
                )
        self.ensure_session()
```

- [ ] **Step 4: Run all relevant tests**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py tests/test_aed_tool_pairing.py tests/test_interface.py tests/test_llm_service.py tests/test_llm_utils.py tests/test_adapter_registry.py tests/test_deepseek_adapter.py -v`

Expected: all pass.

- [ ] **Step 5: Smoke-test the module**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -c "from lingtai_kernel.session import Session; print('OK')"`

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
cd /Users/huangzesen/Documents/GitHub/lingtai-kernel
git add src/lingtai_kernel/session.py tests/test_chat_interface_invariant.py
git commit -m "$(cat <<'EOF'
fix(session): heal dangling tool_calls on restore from disk

When a process crashed between tool_call emission and tool_result
persistence, chat_history.jsonl on disk may end in an unanswered
assistant[tool_calls]. On rehydrate, run enforce_tool_pairing() to
strip set-level orphans, then close_pending_tool_calls() to
synthesize placeholder error results for any remaining positional
violations. Ensures the first send after a crashed-mid-tool-loop
restart doesn't 400 on strict providers.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: End-to-end verification against the real DeepSeek API

**Files:**
- No new files — this is a manual verification step that exercises the full stack using a previously-captured failing interface state.

- [ ] **Step 1: Confirm the real failing scenario is now accepted**

Run this script (copy verbatim; it rebuilds the interface from the archived history the original 400 cascade left behind):

```bash
cd /Users/huangzesen/Documents/GitHub/lingtai-kernel
~/.lingtai-tui/runtime/venv/bin/python <<'EOF'
"""End-to-end: reconstruct the real failing session state, apply the restore
path, send to the live DeepSeek API, and confirm we no longer 400."""
import json, os
from openai import OpenAI

# Load API key from the user's env file
with open('/Users/huangzesen/.lingtai-tui/.env') as f:
    for line in f:
        if line.startswith('DEEPSEEK_API_KEY='):
            os.environ['DEEPSEEK_API_KEY'] = line.split('=', 1)[1].strip()

from lingtai_kernel.llm.interface import ChatInterface
from lingtai.llm.deepseek.adapter import DeepSeekChatSession

# Replay the failing state from the archive if it still exists — otherwise
# synthesize the minimal repro.
archive = '/Users/huangzesen/work/lingtai-projects/marco_velli/.lingtai/zhipu_intl/history/chat_history_archive.jsonl'
session_entries = None
if os.path.exists(archive):
    with open(archive) as f:
        entries = [json.loads(l) for l in f if l.strip()]
    # Find last session (id=0 system) boundary
    starts = [i for i, e in enumerate(entries) if e.get('id') == 0 and e.get('role') == 'system']
    if starts:
        session_entries = entries[starts[-1]:]

if session_entries is None:
    # Synthesized minimal repro
    session_entries = [
        {"id": 0, "role": "system", "system": "be helpful", "timestamp": 0.0},
        {"id": 1, "role": "user",
         "content": [{"type": "text", "text": "hi"}], "timestamp": 1.0},
        {"id": 2, "role": "assistant",
         "content": [
             {"type": "text", "text": "checking"},
             {"type": "tool_call", "id": "call_X", "name": "check", "args": {}},
         ],
         "timestamp": 2.0},
    ]

iface = ChatInterface.from_dict(session_entries)

# Apply the full restore path (enforce + close)
iface.enforce_tool_pairing()
if iface.has_pending_tool_calls():
    iface.close_pending_tool_calls(
        reason="restored from disk — prior session ended mid-tool-loop"
    )

assert not iface.has_pending_tool_calls(), "fix did not remove pending state"
print(f"rehydrated interface: {len(iface.entries)} entries, tail role = {iface.entries[-1].role}")

# Wrap in session and send
client = OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com')
session = DeepSeekChatSession(
    client=client, model='deepseek-v4-pro', interface=iface,
    tools=None, tool_choice=None, extra_kwargs={}, client_kwargs={},
)
# Simulate AED injecting a revive message next
response = session.send("[system] process restarted, please continue")
print(f"SUCCESS: response text = {(response.text or '')[:200]}")
EOF
```

Expected: prints `SUCCESS: response text = ...` — no HTTP 400, no traceback. If it 400s, the output will show the exact error; treat that as a bug in the implementation and investigate.

- [ ] **Step 2: Run the complete test suite one final time**

Run: `cd /Users/huangzesen/Documents/GitHub/lingtai-kernel && ~/.lingtai-tui/runtime/venv/bin/python -m pytest tests/test_chat_interface_invariant.py tests/test_aed_tool_pairing.py tests/test_interface.py tests/test_llm_service.py tests/test_llm_utils.py tests/test_adapter_registry.py tests/test_deepseek_adapter.py -v`

Expected: all pass. No regressions, all new tests green.

- [ ] **Step 3: Nothing to commit (no code changes in this task)**

This task is verification-only. If the script succeeds and all tests pass, proceed to Task 7.

---

## Task 7: Clean up stranded agents on disk (one-time migration)

**Files:**
- No new files — one-shot patch script for any pre-existing broken histories.

**Background:** Agents running on old kernel code may have `chat_history.jsonl` on disk ending in an unanswered `assistant[tool_calls]`. With Task 5 deployed, these will auto-heal on next process start. This task just confirms the healing works on the known problem directories.

- [ ] **Step 1: Run a read-only scan to find any agent dirs with dangling state**

Run this to survey the current state across the user's known project roots (adapts if paths don't exist):

```bash
~/.lingtai-tui/runtime/venv/bin/python <<'EOF'
"""Scan all agent history files for the dangling assistant[tool_calls] shape."""
import json
from pathlib import Path

roots = [
    Path.home() / "work/lingtai-projects",
    Path.home() / ".lingtai-tui",
]
dirty = []
for root in roots:
    if not root.exists():
        continue
    for history_file in root.rglob("history/chat_history.jsonl"):
        try:
            entries = [json.loads(l) for l in history_file.read_text().splitlines() if l.strip()]
        except Exception:
            continue
        if not entries:
            continue
        last = entries[-1]
        if last.get("role") != "assistant":
            continue
        has_tc = any(
            isinstance(b, dict) and b.get("type") == "tool_call"
            for b in last.get("content", [])
        )
        if has_tc:
            dirty.append(history_file)

print(f"Found {len(dirty)} agents with dangling tool_calls:")
for p in dirty:
    print(f"  {p}")
EOF
```

Expected: a list of zero or more paths. If zero, no migration needed.

- [ ] **Step 2: Verify auto-heal triggers on process restart**

For any dirty path printed in Step 1, the user can either:
- (Preferred) kill that agent's process and relaunch it via the TUI. On restart, `Session.restore_chat` (updated in Task 5) will run `enforce_tool_pairing` + `close_pending_tool_calls` and save the healed state back to disk on the first `_save_chat_history()` call.
- Or, if the agent is already running with old in-memory state from before the kernel fix was deployed, the user should issue `/refresh` or fully `/suspend` + `/cpr` on it to force a re-import of the updated kernel module.

No code action required here — this task is documentation for the user to know how the migration happens.

- [ ] **Step 3: Nothing to commit**

Migration is self-executing via the restart-and-restore path.

---

## Self-review

**Spec coverage check:**
- Design §1 (ChatInterface invariant enforcement): Tasks 1, 2, 3 ✓
- Design §2 (AED recovery close dangling): Task 4 ✓
- Design §3 (Session restore heal): Task 5 ✓
- Design "Test plan" §1-6 (unit tests): covered in Tasks 1, 2, 3 ✓
- Design "Test plan" §7 (AED replay integration): Task 4 ✓
- Design "Test plan" §8 (session restore rehydrate): Task 5 ✓
- Design "Migration / deployment" (stranded agents): Task 7 ✓
- End-to-end live verification: Task 6 ✓

**Placeholder scan:** No "TBD", "TODO", "implement later", "add appropriate error handling", or similar red-flag patterns. All code blocks are complete and runnable. All commands have explicit expected output.

**Type consistency:** `PendingToolCallsError`, `has_pending_tool_calls()`, and `close_pending_tool_calls(reason)` have consistent names and signatures across all tasks. The exception is defined in Task 1, referenced in Tasks 3 and 4. `close_pending_tool_calls` is defined in Task 2, called in Tasks 4 and 5.
