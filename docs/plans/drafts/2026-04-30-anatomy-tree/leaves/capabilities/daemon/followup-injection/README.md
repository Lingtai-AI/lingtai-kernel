# followup-injection — How Daemon Results Return to Parent

## What

The daemon system has three feedback channels from emanation back to parent: (1) intermediate text notifications injected into the parent's inbox during the tool loop, (2) follow-up messages queued by the parent and drained into the emanation's LLM session, and (3) terminal completion notifications after the emanation finishes.

## Contract

### Intermediate notifications (emanation → parent)

- During the tool loop, after each LLM response that contains text, `_notify_parent(em_id, text)` is called.
- `_notify_parent()` builds a `[daemon:em-N]\n\n<text>` string, wraps it via `_make_message(MSG_REQUEST, "daemon", ...)`, and puts it on `self._agent.inbox`.
- These are **asynchronous** — they arrive in the parent's inbox between the parent's turns. The parent sees them as `[daemon:em-N]` messages.

### Follow-up injection (parent → emanation)

- `_handle_ask(em_id, message)` appends `message` to the entry's `followup_buffer` (protected by `followup_lock`).
- In the tool loop, **only after a text-only response** (no `tool_calls`), `_drain_followup(em_id)` is called.
- If the buffer is non-empty, it is cleared and the text is sent as a separate `session.send(followup)` user message.
- Rationale: injecting between tool-call responses would violate the `assistant[tool_calls] → user[results]` pairing invariant.
- If multiple `ask` calls arrive between drains, the messages are concatenated with `\n\n`.

### Terminal notification (emanation → parent)

- `_on_emanation_done(em_id, task_summary, future)` is the `future.add_done_callback`.
- It extracts the result text (or `f"Failed: {e}"` on exception).
- Text > `_max_result_chars` (default 2000) is truncated with a suffix.
- Text < `_NOTIFY_MIN_LEN` (20 chars) is **suppressed** (logged as `suppressed_short`) — prevents notification storms from cancelled or empty runs.
- Otherwise, `_notify_parent(em_id, text)` sends the final result to inbox.

### run_dir recording

- `record_user_send(text, kind)` appends to `history/chat_history.jsonl` with `kind ∈ {"task", "tool_results", "followup"}`.
- Every injection point — initial task, tool results, follow-up — is recorded for forensic replay.

## Source

Anchored by function name; re-locate with `grep -n 'def <func>' <file>` if line numbers drift.

- `__init__.py::_run_emanation()` — intermediate text → `_notify_parent()` call in the tool loop
- `__init__.py::_run_emanation()` — follow-up drain gate: only after text-only responses (no `tool_calls`)
- `__init__.py::_notify_parent()` — inbox injection with `[daemon:em-N]` prefix
- `__init__.py::_drain_followup()` — buffer clear + lock
- `__init__.py::_handle_ask()` — buffer append (concatenates with `\n\n`)
- `__init__.py::_on_emanation_done()` — terminal path: truncation, suppression, final notify
- `__init__.py::_NOTIFY_MIN_LEN` — class constant (20 chars)
- `__init__.py::_max_result_chars` — constructor parameter (default 2000)
- `run_dir.py::DaemonRunDir.record_user_send()` — chat history JSONL append

## Related

- `../verify_daemon_leaves.py` — 11 static checks for this leaf (run against source)
- `daemon-manual` skill — how to tail `history/chat_history.jsonl` for live progress
- `lingtai-kernel-anatomy reference/mail-protocol.md` — inbox message format (`MSG_REQUEST`)
