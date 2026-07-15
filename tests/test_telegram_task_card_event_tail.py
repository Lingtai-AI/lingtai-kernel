"""Automatic Task Card as a broadcast projection of the agent's ``events.jsonl``.

Jason's final contract (Telegram 8266-8295): the automatic slot mechanically
consumes the agent's authoritative ``logs/events.jsonl`` in file order, skips
every non-whitelisted event type, keeps the most recent N matching
``tool_call`` events using only safe bounded fields, and broadcasts the same
projection to every resident Task Card for the agent (no per-route
correlation — this is an agent-behavior broadcast, not per-chat visibility).

These tests exercise ``TelegramManager``'s tailer directly: no durable
cursor/checkpoint file, no BaseAgent pre-dispatch/result callback dependency,
no full-file scan on every poll, and restart rehydration from the same
durable file only.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class FakeAccount:
    """Mimics the real TelegramAccount API needed for automatic broadcast."""

    def __init__(self, alias="mybot", *, fail_send=False, fail_edit=False):
        self.alias = alias
        self.calls: list = []
        self._task_cards: dict[str, str] = {}
        self._next_id = 100
        self._fail_send = fail_send
        self._fail_edit = fail_edit

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        if self._fail_send:
            raise RuntimeError("send failed")
        msg_id = self._next_id
        self._next_id += 1
        self.calls.append(("send_message", chat_id, msg_id, text))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.calls.append(("edit_message", chat_id, message_id, text))
        if self._fail_edit:
            raise RuntimeError(
                "Telegram API error: Bad Request: message to edit not found"
            )
        return {"ok": True}

    def delete_message(self, chat_id, message_id):
        self.calls.append(("delete_message", chat_id, message_id))
        return {"ok": True}

    def get_task_card(self, chat_id):
        return self._task_cards.get(str(chat_id))

    def set_task_card(self, chat_id, compound_id):
        self._task_cards[str(chat_id)] = compound_id

    def clear_task_card(self, chat_id):
        self._task_cards.pop(str(chat_id), None)

    def list_task_card_chats(self):
        out = []
        for key in self._task_cards:
            try:
                out.append(int(key))
            except (TypeError, ValueError):
                continue
        return out

    def get_last_message_id(self, chat_id):
        return None


class FakeService:
    def __init__(self, accounts):
        self._accounts = {a.alias: a for a in accounts}
        self._order = [a.alias for a in accounts]
        self.default_account = accounts[0]

    def get_account(self, alias):
        return self._accounts[alias]

    def list_accounts(self):
        return list(self._order)

    def taskcard_enabled(self):
        return True

    def taskcard_normal_rows(self):
        return 1


def _manager(tmp_path, *accounts):
    if not accounts:
        accounts = (FakeAccount(),)
    service = FakeService(list(accounts))
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, service


def _events_path(tmp_path: Path) -> Path:
    path = Path(tmp_path) / "logs" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_lines(path: Path, lines: list[str]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _tool_call_line(
    tool_name="bash", action="run", reasoning="doing a thing", ts=1.0,
    call_id="c1",
) -> str:
    return json.dumps({
        "type": "tool_call",
        "address": "agent-1",
        "agent_name": "agent-1",
        "ts": ts,
        "tool_name": tool_name,
        "tool_call_id": call_id,
        "tool_trace_id": "t1",
        "tool_args": {"action": action, "_reasoning": reasoning, "secret": "sh"},
    })


def _pre_resident(account: FakeAccount, chat_id: int, manager: TelegramManager) -> None:
    """Seed a resident Task Card target the way an existing account would have one."""
    account.set_task_card(chat_id, f"{account.alias}:{chat_id}:1")


# ---------------------------------------------------------------------------
# Broadcast with no Telegram notification file present at all
# ---------------------------------------------------------------------------


def test_broadcast_requires_no_notification_file(tmp_path):
    """The tailer must project from events.jsonl alone, no notification store read."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line()])

    manager._poll_event_tail()

    edits = [c for c in acct.calls if c[0] == "edit_message"]
    assert len(edits) == 1
    assert "bash" in edits[0][3]
    assert "doing a thing" in edits[0][3]


# ---------------------------------------------------------------------------
# Restart rehydration: reverse-tail to find latest N without a durable cursor
# ---------------------------------------------------------------------------


def test_restart_rehydrates_latest_n_from_tail_without_checkpoint_file(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    lines = [
        _tool_call_line(tool_name=f"tool{i}", reasoning=f"reason{i}", call_id=f"c{i}")
        for i in range(5)
    ]
    _write_lines(events_path, lines)

    # No file anywhere except events.jsonl itself carries this state.
    working = Path(tmp_path)
    before = {p for p in working.rglob("*") if p.is_file()}

    manager2, service2 = _manager(tmp_path, acct)
    manager2._init_event_tail()

    after = {p for p in working.rglob("*") if p.is_file()}
    assert after == before, "restart must not create a new durable checkpoint file"

    window = manager2._task_card_event_window()
    # Latest-N (default window) ends with the most recent event.
    assert window[-1]["tool"] == "tool4"
    assert window[-1]["reasoning"] == "reason4"


# ---------------------------------------------------------------------------
# Non-whitelisted / malformed / partial-line rows are skipped safely
# ---------------------------------------------------------------------------


def test_non_whitelisted_event_types_are_skipped(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        json.dumps({"type": "tool_result", "tool_name": "bash", "ts": 1.0}),
        json.dumps({"type": "llm_call", "ts": 1.0}),
        _tool_call_line(tool_name="only-this-one"),
    ])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert [row["tool"] for row in window] == ["only-this-one"]


def test_malformed_json_line_is_skipped(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        "{not valid json",
        _tool_call_line(tool_name="after-garbage"),
    ])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert [row["tool"] for row in window] == ["after-garbage"]


def test_partial_trailing_line_is_not_consumed_until_complete(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="first")])

    manager._poll_event_tail()
    assert [row["tool"] for row in manager._task_card_event_window()] == ["first"]

    # Append a partial line with no trailing newline (simulates a writer mid-write).
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "tool_call", "tool_name": "second",
            "tool_args": {"action": "x", "_reasoning": "y"}, "ts": 2.0,
        })[:20])  # deliberately truncated, no newline

    manager._poll_event_tail()
    # The partial line must not be consumed as a complete row yet.
    assert [row["tool"] for row in manager._task_card_event_window()] == ["first"]

    # Complete the line.
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "tool_call", "tool_name": "second",
            "tool_args": {"action": "x", "_reasoning": "y"}, "ts": 2.0,
        })[20:] + "\n")

    manager._poll_event_tail()
    assert [row["tool"] for row in manager._task_card_event_window()] == ["first", "second"]


def test_startup_rehydrate_does_not_consume_unterminated_final_line(tmp_path):
    """A restart/refresh must not treat the file's in-progress final line as
    already consumed: the forward offset must land at the start of that
    incomplete tail, not at the current EOF, so completion is read as one
    whole row later instead of being silently dropped forever."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="first")])
    complete_size = events_path.stat().st_size

    # Append a partial line with no trailing newline, simulating a writer
    # mid-append at the moment of a manager restart/refresh.
    partial = json.dumps({
        "type": "tool_call", "tool_name": "second",
        "tool_args": {"action": "x", "_reasoning": "y"}, "ts": 2.0,
    })[:20]
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(partial)

    manager._init_event_tail()

    # Only the complete "first" row is rehydrated; "second" is not consumed.
    assert [row["tool"] for row in manager._task_card_event_window()] == ["first"]
    # The offset must be at the start of the unterminated tail, not at the
    # current (larger) EOF, or the completed line would never be read.
    assert manager._event_tail_offset() == complete_size

    # Complete the line and poll: it must be read as one whole new row.
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "tool_call", "tool_name": "second",
            "tool_args": {"action": "x", "_reasoning": "y"}, "ts": 2.0,
        })[20:] + "\n")

    manager._poll_event_tail()
    assert [row["tool"] for row in manager._task_card_event_window()] == ["first", "second"]


# ---------------------------------------------------------------------------
# Latest-N order and bounded window
# ---------------------------------------------------------------------------


def test_window_keeps_only_latest_n_in_order(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)
    manager._TASK_CARD_EVENT_WINDOW = 3

    events_path = _events_path(tmp_path)
    lines = [_tool_call_line(tool_name=f"t{i}", call_id=f"c{i}") for i in range(7)]
    _write_lines(events_path, lines)

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert [row["tool"] for row in window] == ["t4", "t5", "t6"]


def test_reasoning_cap_includes_the_ellipsis_not_just_the_prefix(tmp_path):
    """The final displayed reasoning (prefix + ellipsis) must not exceed
    ``_TASK_CARD_EVENT_REASONING_CAP`` characters."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)
    cap = manager._TASK_CARD_EVENT_REASONING_CAP

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(reasoning="x" * (cap + 50))])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert len(window) == 1
    reasoning = window[0]["reasoning"]
    assert reasoning.endswith("…")
    assert len(reasoning) == cap


# ---------------------------------------------------------------------------
# Safe field whitelist: never forward raw tool_args
# ---------------------------------------------------------------------------


def test_only_safe_bounded_fields_are_projected(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [json.dumps({
        "type": "tool_call",
        "tool_name": "bash",
        "tool_call_id": "c1",
        "tool_trace_id": "t1",
        "ts": 1.0,
        "tool_args": {
            "action": "run",
            "_reasoning": "safe text",
            "command": "rm -rf /very/secret/path --token=abc123",
            "env": {"API_KEY": "sk-should-never-appear"},
        },
    })])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert len(window) == 1
    row = window[0]
    assert set(row.keys()) <= {"tool", "tool_action", "reasoning", "ts"}
    assert row["tool"] == "bash"
    assert row["tool_action"] == "run"
    assert row["reasoning"] == "safe text"

    manager._poll_event_tail()
    edits = [c for c in acct.calls if c[0] == "edit_message"]
    rendered = edits[-1][3]
    assert "sk-should-never-appear" not in rendered
    assert "/very/secret/path" not in rendered
    assert "--token=abc123" not in rendered


def test_events_missing_expected_fields_are_skipped_fail_closed(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        json.dumps({"type": "tool_call"}),  # no tool_name/tool_args at all
        json.dumps({"type": "tool_call", "tool_name": 123, "tool_args": {}}),  # wrong type
        _tool_call_line(tool_name="valid-one"),
    ])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert [row["tool"] for row in window] == ["valid-one"]


# ---------------------------------------------------------------------------
# Broadcast to multiple resident Task Card targets, no route correlation
# ---------------------------------------------------------------------------


def test_broadcasts_same_projection_to_every_resident_target_across_accounts(tmp_path):
    acct1 = FakeAccount(alias="bot1")
    acct2 = FakeAccount(alias="bot2")
    manager, service = _manager(tmp_path, acct1, acct2)
    _pre_resident(acct1, 111, manager)
    _pre_resident(acct1, 222, manager)
    _pre_resident(acct2, 333, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="broadcast-me")])

    manager._poll_event_tail()

    for acct, chat_id in ((acct1, 111), (acct1, 222), (acct2, 333)):
        edits = [c for c in acct.calls if c[0] == "edit_message" and c[1] == chat_id]
        assert len(edits) == 1, f"expected exactly one edit for {acct.alias}:{chat_id}"
        assert "broadcast-me" in edits[0][3]


def test_normal_rows_limits_rendered_event_tail_without_shrinking_buffer(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(tool_name="older"),
        _tool_call_line(tool_name="newest", call_id="c2"),
    ])

    manager._poll_event_tail()

    # /taskcard 1 limits the render, while the fixed rehydration buffer retains
    # both recent events for a later increase to the normal-row setting.
    assert manager._taskcard_normal_rows() == 1
    assert [row["tool"] for row in manager._task_card_event_window()] == [
        "older", "newest",
    ]
    edits = [c for c in acct.calls if c[0] == "edit_message"]
    assert len(edits) == 1
    rendered = edits[0][3]
    assert "newest" in rendered
    assert "older" not in rendered
    assert "current: 1" in rendered


def test_no_resident_targets_means_no_transport_calls(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    # No resident card anywhere.

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line()])

    manager._poll_event_tail()

    assert acct.calls == []


# ---------------------------------------------------------------------------
# No full-file scan: seek from the in-memory offset, not JSONLLoggingService
# ---------------------------------------------------------------------------


def test_poll_only_reads_appended_bytes_not_whole_file(tmp_path, monkeypatch):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="old")])
    manager._poll_event_tail()
    assert [row["tool"] for row in manager._task_card_event_window()] == ["old"]

    offset_before = manager._event_tail_offset()
    assert offset_before == events_path.stat().st_size

    _write_lines(events_path, [_tool_call_line(tool_name="new", call_id="c2")])
    manager._poll_event_tail()

    assert [row["tool"] for row in manager._task_card_event_window()] == ["old", "new"]
    assert manager._event_tail_offset() == events_path.stat().st_size


def test_truncated_or_replaced_file_reinitializes_from_new_eof(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="before-truncate")])
    manager._poll_event_tail()
    assert manager._event_tail_offset() == events_path.stat().st_size

    # Simulate log rotation: file replaced with a smaller one.
    events_path.write_text("", encoding="utf-8")
    _write_lines(events_path, [_tool_call_line(tool_name="after-truncate")])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert [row["tool"] for row in window] == ["after-truncate"]


def test_truncation_to_empty_still_broadcasts_the_now_empty_window(tmp_path):
    """A non-empty -> empty truncation must be reflected honestly: the stale
    (larger) window must not keep being displayed just because the freshly
    rehydrated window happens to be empty."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="before-truncate")])
    manager._poll_event_tail()
    edits_before = [c for c in acct.calls if c[0] == "edit_message"]
    assert len(edits_before) == 1
    assert "before-truncate" in edits_before[0][3]

    # Replace with an empty file — same primitive as log rotation, but the
    # new tail has zero matching rows.
    events_path.write_text("", encoding="utf-8")

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert window == []
    edits_after = [c for c in acct.calls if c[0] == "edit_message"]
    assert len(edits_after) == 2, "the now-empty window must still be broadcast"
    assert "before-truncate" not in edits_after[-1][3]


# ---------------------------------------------------------------------------
# Reverse-scan read failure must fail closed, not silently jump to EOF
# ---------------------------------------------------------------------------


def test_reverse_tail_read_failure_does_not_advance_offset_to_eof(tmp_path, monkeypatch):
    """If the reverse-tail scan itself fails (I/O error mid-read), the offset
    must NOT be advanced to EOF as though the file had actually been read —
    that would silently drop real history and make the failure unretryable."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="unreachable")])

    monkeypatch.setattr(
        manager, "_reverse_tail_latest_rows", lambda path, size: None,
    )

    manager._init_event_tail()

    assert manager._task_card_event_window() == []
    # Offset must NOT be the file's real size (which would silently mark
    # "unreachable" as consumed history it never actually read).
    assert manager._event_tail_offset() == 0


# ---------------------------------------------------------------------------
# Manager start/stop exactly once — one worker thread joined with the
# Telegram MCP manager lifecycle
# ---------------------------------------------------------------------------


class _ThreadCountingService:
    def __init__(self, accounts):
        self._accounts = {a.alias: a for a in accounts}
        self._order = [a.alias for a in accounts]
        self.default_account = accounts[0]
        self.start_calls = 0
        self.stop_calls = 0

    def get_account(self, alias):
        return self._accounts[alias]

    def list_accounts(self):
        return list(self._order)

    def taskcard_enabled(self):
        return True

    def taskcard_normal_rows(self):
        return 1

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1


def test_start_stop_run_exactly_one_tail_worker(tmp_path):
    acct = FakeAccount()
    service = _ThreadCountingService([acct])
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )

    before_threads = set(threading.enumerate())
    manager.start()
    try:
        assert service.start_calls == 1
        new_threads = set(threading.enumerate()) - before_threads
        tail_threads = [
            t for t in new_threads if "task_card" in t.name.lower()
            or "event_tail" in t.name.lower() or "tail" in t.name.lower()
        ]
        assert len(tail_threads) == 1, f"expected exactly one tail worker, got {new_threads}"
    finally:
        manager.stop()
        assert service.stop_calls == 1

    # Give the thread a moment to actually exit.
    for t in list(threading.enumerate()):
        if t in new_threads:
            t.join(timeout=2.0)
            assert not t.is_alive()


def test_start_called_twice_does_not_start_a_second_worker(tmp_path):
    acct = FakeAccount()
    service = _ThreadCountingService([acct])
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )

    before_threads = set(threading.enumerate())
    manager.start()
    manager.start()
    try:
        new_threads = set(threading.enumerate()) - before_threads
        tail_threads = [t for t in new_threads if "tail" in t.name.lower()]
        assert len(tail_threads) == 1
    finally:
        manager.stop()


# ---------------------------------------------------------------------------
# Programmable slot unchanged: automatic broadcast never touches the
# programmable channel's committed frame.
# ---------------------------------------------------------------------------


def test_programmable_slot_untouched_by_automatic_broadcast(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    # Commit a programmable frame first, the way the controller would.
    manager._set_channel_frame(acct.alias, 555, "programmable", "— WATCH —\nprogrammable content")

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(tool_name="automatic-row")])

    manager._poll_event_tail()

    edits = [c for c in acct.calls if c[0] == "edit_message"]
    assert len(edits) == 1
    rendered = edits[-1][3]
    assert "automatic-row" in rendered
    assert "programmable content" in rendered
