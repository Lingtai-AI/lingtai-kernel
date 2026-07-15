"""Automatic Task Card as a broadcast projection of the agent's ``events.jsonl``.

The automatic slot mechanically consumes the agent's authoritative
``logs/events.jsonl`` in file order, keeps the most recent N provider-call groups
of canonical ``diary`` text and safe tool fields, and projects
current session telemetry only from the latest final-carrier ``notification_block_injected``,
and broadcasts the same projection to every resident Task Card for the agent
(no per-route correlation — this is an agent-behavior broadcast, not per-chat
visibility).

These tests exercise ``TelegramManager``'s tailer directly: no durable
cursor/checkpoint file, no BaseAgent pre-dispatch/result callback dependency,
no full-file scan on every poll, and restart rehydration from the same
durable file only.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
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

    def set_message_reaction(self, *_args, **_kwargs):
        return None


class FakeService:
    def __init__(self, accounts):
        self._accounts = {a.alias: a for a in accounts}
        self._order = [a.alias for a in accounts]
        self.default_account = accounts[0]
        self.normal_rows = 1

    def get_account(self, alias):
        return self._accounts[alias]

    def list_accounts(self):
        return list(self._order)

    def taskcard_enabled(self):
        return True

    def taskcard_normal_rows(self):
        return self.normal_rows


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


def test_first_real_inbound_establishes_one_resident(tmp_path):
    acct = FakeAccount()
    manager, _ = _manager(tmp_path, acct)
    manager.on_incoming("mybot", {"message": {
        "message_id": 8, "date": 1781600000,
        "from": {"username": "alice"},
        "chat": {"id": 123, "type": "private"}, "text": "hello",
    }})
    assert acct.get_task_card(123) == "mybot:123:100"
    assert [call[0] for call in acct.calls] == ["send_message"]


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
    acct.calls.clear()
    manager2._broadcast_task_card_event_window()
    assert acct.get_task_card(555) == "mybot:555:1"
    assert [call[0:3] for call in acct.calls] == [("edit_message", 555, 1)]


# ---------------------------------------------------------------------------
# Non-whitelisted / malformed / partial-line rows are skipped safely
# ---------------------------------------------------------------------------


def test_provider_groups_count_calls_and_exclude_private_fields(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)
    service.normal_rows = 2
    visible = "text two " + ("x" * 2000)
    events = [
        {"type": "diary", "api_call_id": "api-1", "text": "text one"},
        {"type": "tool_call", "api_call_id": "api-1", "tool_name": "bash",
         "tool_args": {"action": "ACTION_SECRET_ONE", "_reasoning": "safe one"}},
        {"type": "assistant_text", "api_call_id": "api-1", "text": "ALIAS_SECRET"},
        {"type": "diary", "api_call_id": "api-2", "text": visible},
        {"type": "tool_call", "api_call_id": "api-2", "tool_name": "read",
         "tool_args": {"action": "ACTION_SECRET_TWO", "command": "ARG_SECRET",
                       "_reasoning": "safe two"}},
        {"type": "thinking", "api_call_id": "api-2", "text": "THINKING_SECRET"},
        {"type": "tool_result", "api_call_id": "api-2", "result": "RESULT_SECRET"},
    ]
    _write_lines(_events_path(tmp_path), [json.dumps(event) for event in events])
    manager._poll_event_tail()
    rendered = [call for call in acct.calls if call[0] == "edit_message"][-1][3]
    divider = manager._TASK_CARD_API_CALL_DIVIDER
    assert rendered.count(divider) == 2
    assert all(value in rendered for value in ("text one", "• bash:", "text two", "• read:"))
    assert len(rendered) <= manager._TASK_CARD_TEXT_LIMIT
    assert all(secret not in rendered for secret in (
        "ACTION_SECRET_ONE", "ACTION_SECRET_TWO", "ARG_SECRET", "ALIAS_SECRET",
        "THINKING_SECRET", "RESULT_SECRET",
    ))

    service.normal_rows = 1
    manager._broadcast_task_card_event_window()
    latest = acct.calls[-1][3]
    assert latest.count(divider) == 1 and "text two" in latest and "• read:" in latest
    assert "text one" not in latest and "• bash:" not in latest


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
    assert set(row.keys()) <= {"tool", "reasoning", "started_at"}
    assert row["tool"] == "bash"
    assert "tool_action" not in row
    assert row["reasoning"] == "safe text"

    manager._poll_event_tail()
    edits = [c for c in acct.calls if c[0] == "edit_message"]
    rendered = edits[-1][3]
    assert "sk-should-never-appear" not in rendered
    assert "/very/secret/path" not in rendered
    assert "--token=abc123" not in rendered


# ---------------------------------------------------------------------------
# Row timestamp: derived from the event's own canonical ``ts``, never raw
# tool args, never the current render instant, safely omitted when malformed
# ---------------------------------------------------------------------------


def test_row_started_at_is_derived_from_event_ts_and_rendered(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    event_ts = 1752600000.0
    events_path = _events_path(tmp_path)
    _write_lines(events_path, [_tool_call_line(ts=event_ts)])

    manager._poll_event_tail()

    local = datetime.fromtimestamp(event_ts).astimezone()
    expected = f"{local:%H:%M:%S} UTC{local:%z}"[:-2]
    window = manager._task_card_event_window()
    assert len(window) == 1
    assert window[0]["started_at"] == expected
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    assert edits
    assert f" · {expected}" in edits[-1][3]


def test_event_log_final_carrier_projects_session_telemetry_into_final_render(tmp_path):
    """Rows and the footer telemetry come from their authoritative events.

    The final-carrier ``notification_block_injected`` owns the current whole ``agent_meta``
    snapshot; a retired ``tool_meta`` snapshot and row arguments are present as
    decoys and must not affect the automatic card.
    """
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    event_ts = 1752600000.0
    session = {
        "session_cache_rate": 0.87803,
        "cache_miss_tokens": 170631,
        "cache_miss_budget": 1_000_000,
        "api_calls": 13,
        "context_tokens": 171246,
        "context_window": 272000,
        "context_usage": 0.62958,
    }
    events_path = _events_path(tmp_path)
    row_event = json.loads(_tool_call_line(ts=event_ts, reasoning="event-log row"))
    row_event["tool_args"]["metadata"] = {"api_calls": 999}
    _write_lines(events_path, [
        json.dumps(row_event),
        # A plain tool_result is the live decoy immediately before the real
        # notification carrier; even if it fabricates agent_meta, it must not
        # own the current snapshot.
        json.dumps({
            "type": "tool_result",
            "tool_name": "bash",
            "tool_call_id": "c1",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 888},
            }}}},
        }),
        json.dumps({
            "type": "notification_block_injected",
            "tool_name": "bash",
            "tool_call_id": "c1",
            "ts": event_ts + 1,
            "_meta": {
                "tool_meta": {"token_usage": {"session": {
                    "session_cache_rate": 0.01,
                    "api_calls": 1,
                }}},
                "agent_meta": {
                    "agent_state": {"token_usage": {
                        "current_call": {"api_calls": 999},
                        "session": session,
                    }},
                    "notifications": {"persistent": {"api_calls": 777}},
                },
            },
        }),
    ])

    manager._poll_event_tail()

    local = datetime.fromtimestamp(event_ts).astimezone()
    expected_stamp = f"{local:%H:%M:%S} UTC{local:%z}"[:-2]
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    assert edits
    rendered = edits[-1][3]
    assert "• bash: event-log row" in rendered
    assert f" · {expected_stamp}" in rendered
    assert "session · cache 87.8% · miss 170.6k/1.0M · calls 13" in rendered
    assert "ctx · 171.2k/272.0k · 63%" in rendered
    assert "calls 888" not in rendered
    assert "calls 999" not in rendered
    assert "calls 777" not in rendered


def test_malformed_current_telemetry_carrier_clears_previous_snapshot(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(),
        json.dumps({
            "type": "notification_block_injected",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 7},
            }}}},
        }),
    ])
    manager._poll_event_tail()
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    assert "calls 7" in edits[-1][3]

    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": "malformed"},
        }}}},
    })])
    manager._poll_event_tail()

    edits = [call for call in acct.calls if call[0] == "edit_message"]
    assert "calls 7" not in edits[-1][3]
    assert "session ·" not in edits[-1][3]


def _programmable_update(manager, account, chat_id, lines):
    return manager._handle_task_card_update({
        "sub_action": "update",
        "channel": "programmable",
        "account": account,
        "chat_id": chat_id,
        "card": {"lines": lines},
    })


def test_programmable_edit_re_reads_telemetry_appended_since_last_broadcast(tmp_path):
    """A programmable edit must not compose against a stale automatic footer.

    The automatic slot's footer metadata is baked into whatever text the tail
    last broadcast. If new session telemetry lands in ``events.jsonl`` after
    that broadcast but before a programmable ``task_card`` edit — and no
    automatic poll has re-broadcast in between — the programmable edit must
    still show the newer telemetry, not the frozen snapshot from the last
    automatic broadcast (Telegram 8482/8485/8487).
    """
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(),
        json.dumps({
            "type": "notification_block_injected",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 1},
            }}}},
        }),
    ])
    manager._poll_event_tail()
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    assert "calls 1" in edits[-1][3]

    # New telemetry lands on the authoritative event log, but the automatic
    # tail worker has not polled again yet — the stored automatic frame is
    # still the "calls 1" snapshot at this point.
    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": 42},
        }}}},
    })])

    result = _programmable_update(manager, acct.alias, 555, ["watch line"])
    assert result["status"] == "ok"

    edits = [call for call in acct.calls if call[0] == "edit_message"]
    rendered = edits[-1][3]
    assert "watch line" in rendered  # programmable content still present
    assert "calls 42" in rendered  # fresh telemetry re-read at this edit
    assert "calls 1" not in rendered  # stale snapshot must not survive


def test_second_programmable_edit_picks_up_telemetry_changed_between_edits(tmp_path):
    """Freshness holds across *consecutive* programmable edits, not just the first."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(),
        json.dumps({
            "type": "notification_block_injected",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 5},
            }}}},
        }),
    ])
    manager._poll_event_tail()

    first = _programmable_update(manager, acct.alias, 555, ["v1"])
    assert first["status"] == "ok"
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    assert "calls 5" in edits[-1][3]

    # Telemetry mutates again between the two programmable edits, with no
    # automatic poll in between.
    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": 99},
        }}}},
    })])

    second = _programmable_update(manager, acct.alias, 555, ["v2"])
    assert second["status"] == "ok"
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    rendered = edits[-1][3]
    assert "v2" in rendered
    assert "calls 99" in rendered
    assert "calls 5" not in rendered


def test_programmable_edit_does_not_fabricate_automatic_footer(tmp_path):
    """A programmable-only card must not gain an automatic footer from a refresh."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": 3},
        }}}},
    })])
    # No automatic frame has ever been broadcast for this route (no poll, and
    # no _task_card_create/_update ever ran) — only the programmable slot is
    # ever written.
    result = _programmable_update(manager, acct.alias, 555, ["only watch content"])
    assert result["status"] == "ok"

    edits = [call for call in acct.calls if call[0] == "edit_message"]
    rendered = edits[-1][3] if edits else manager._compose_channels(acct.alias, 555)
    assert "only watch content" in rendered
    assert "session ·" not in rendered
    assert "calls 3" not in rendered


def test_failed_programmable_edit_does_not_commit_refreshed_automatic_frame(tmp_path):
    """A failed programmable transport must not poison the committed automatic slot.

    The pre-transport telemetry refresh renders a fresher automatic frame as a
    transaction-local proposal only. If the programmable edit's own transport
    then fails (an unknown/transient ``edit_message`` failure), neither that
    fresher automatic frame nor the failed programmable frame may be
    committed — the manager's central invariant is that composed slot state,
    and its ``tail_driven`` provenance, represent only successfully delivered
    content. A concurrently queued automatic broadcast (or the next successful
    programmable edit) must still see the last genuinely delivered automatic
    frame, not one Telegram never received.
    """
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(),
        json.dumps({
            "type": "notification_block_injected",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 1},
            }}}},
        }),
    ])
    manager._poll_event_tail()
    edits = [call for call in acct.calls if call[0] == "edit_message"]
    committed_before = edits[-1][3]
    assert "calls 1" in committed_before
    key = manager._channel_key(acct.alias, 555)
    assert manager._resident.is_automatic_tail_driven(acct.alias, 555)
    committed_automatic_before = manager._resident.frames[key]["automatic"]

    # Fresher telemetry lands, but the upcoming programmable edit's own
    # transport is about to fail with an unknown/transient error (NOT a
    # provider-confirmed "message to edit not found", which would instead
    # take the old-first delete+resend recovery path and could still
    # succeed). An unknown/transient failure must fail closed with neither
    # resident nor slot state touched.
    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": 42},
        }}}},
    })])

    real_edit_message = acct.edit_message

    def _transient_failure(chat_id, message_id, text, **kwargs):
        acct.calls.append(("edit_message", chat_id, message_id, text))
        raise RuntimeError("simulated transient failure")

    acct.edit_message = _transient_failure

    result = _programmable_update(manager, acct.alias, 555, ["watch line"])
    assert result["status"] == "error"
    acct.edit_message = real_edit_message

    # The failed edit attempt did reach transport (proving the fresh compose
    # was attempted), but nothing from it may be committed.
    failed_attempt = [c for c in acct.calls if c[0] == "edit_message"][-1]
    assert "calls 42" in failed_attempt[3]  # the attempted text was fresh

    # Committed state must be byte-for-byte unchanged: same automatic frame,
    # same tail-driven provenance, and no programmable slot ever appeared.
    assert manager._resident.frames[key]["automatic"] == committed_automatic_before
    assert manager._resident.is_automatic_tail_driven(acct.alias, 555)
    assert "programmable" not in manager._resident.frames[key]
    assert "calls 42" not in manager._resident.frames[key]["automatic"]
    assert "watch line" not in manager._compose_channels(acct.alias, 555)


def test_retry_after_failed_programmable_edit_commits_fresh_telemetry(tmp_path):
    """Once the transport recovers, the retried edit commits fresh telemetry.

    Continues the prior failure scenario: after one failed programmable
    attempt commits nothing, a retried edit (transport now healthy) must both
    succeed and deliver/commit the currently fresh telemetry — proving the
    failure did not leave the tail-driven refresh path permanently disabled
    or stuck on a poisoned snapshot.
    """
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(),
        json.dumps({
            "type": "notification_block_injected",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 1},
            }}}},
        }),
    ])
    manager._poll_event_tail()

    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": 42},
        }}}},
    })])

    real_edit_message = acct.edit_message

    def _transient_failure(chat_id, message_id, text, **kwargs):
        acct.calls.append(("edit_message", chat_id, message_id, text))
        raise RuntimeError("simulated transient failure")

    acct.edit_message = _transient_failure
    failed = _programmable_update(manager, acct.alias, 555, ["watch line"])
    assert failed["status"] == "error"
    acct.edit_message = real_edit_message

    key = manager._channel_key(acct.alias, 555)
    assert "calls 42" not in manager._resident.frames[key]["automatic"]

    retried = _programmable_update(manager, acct.alias, 555, ["watch line"])
    assert retried["status"] == "ok"

    edits = [call for call in acct.calls if call[0] == "edit_message"]
    rendered = edits[-1][3]
    assert "watch line" in rendered
    assert "calls 42" in rendered
    assert "calls 42" in manager._resident.frames[key]["automatic"]
    assert manager._resident.frames[key]["programmable"] != ""


def test_failed_programmable_edit_does_not_starve_the_automatic_broadcast(tmp_path):
    """A failed programmable edit's pre-transport tail sync must not silently
    consume the automatic broadcaster's only signal to re-broadcast.

    ``_deliver_channel_frame_locked`` calls ``_sync_event_tail_state()`` to
    refresh the automatic frame it proposes. That sync advances the shared,
    manager-owned event-tail offset/metadata/groups even though the refreshed
    *frame* is never committed on a failed transport. If nothing preserves a
    "there is fresher telemetry the automatic card hasn't shown yet" signal,
    a later plain ``_poll_event_tail()`` sees no new bytes past the
    already-advanced offset and skips the broadcast — leaving the automatic
    card frozen on stale telemetry indefinitely, even though no automatic
    broadcast ever actually delivered the fresher snapshot.
    """
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    _write_lines(events_path, [
        _tool_call_line(),
        json.dumps({
            "type": "notification_block_injected",
            "_meta": {"agent_meta": {"agent_state": {"token_usage": {
                "session": {"api_calls": 1},
            }}}},
        }),
    ])
    manager._poll_event_tail()
    key = manager._channel_key(acct.alias, 555)
    assert "calls 1" in manager._resident.frames[key]["automatic"]

    # Fresher telemetry lands, but no automatic poll consumes it yet.
    _write_lines(events_path, [json.dumps({
        "type": "notification_block_injected",
        "_meta": {"agent_meta": {"agent_state": {"token_usage": {
            "session": {"api_calls": 42},
        }}}},
    })])

    real_edit_message = acct.edit_message

    def _transient_failure(chat_id, message_id, text, **kwargs):
        acct.calls.append(("edit_message", chat_id, message_id, text))
        raise RuntimeError("simulated transient failure")

    acct.edit_message = _transient_failure
    result = _programmable_update(manager, acct.alias, 555, ["watch line"])
    assert result["status"] == "error"
    acct.edit_message = real_edit_message

    # Committed state correctly did not change (existing #955 invariant).
    assert "calls 1" in manager._resident.frames[key]["automatic"]
    assert "calls 42" not in manager._resident.frames[key]["automatic"]

    # No new event is appended. A plain automatic poll must still broadcast
    # the fresh telemetry the failed edit's pre-transport sync already
    # consumed from the event tail — it must not be silently lost.
    acct.calls.clear()
    manager._poll_event_tail()

    edits = [c for c in acct.calls if c[0] == "edit_message"]
    assert len(edits) == 1, (
        "automatic poll did not broadcast pending fresh telemetry after a "
        "failed programmable edit consumed it from the event tail"
    )
    assert "calls 42" in edits[-1][3]
    assert "calls 42" in manager._resident.frames[key]["automatic"]


def test_row_started_at_omitted_when_ts_missing(tmp_path):
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    line = json.dumps({
        "type": "tool_call", "tool_name": "bash",
        "tool_args": {"action": "run", "_reasoning": "no ts here"},
    })
    _write_lines(events_path, [line])

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert len(window) == 1
    assert "started_at" not in window[0]


def test_row_started_at_omitted_when_ts_malformed(tmp_path):
    """Bool, non-numeric, non-finite, and out-of-range ``ts`` all safely omit
    the row's stamp rather than crashing or fabricating one."""
    acct = FakeAccount()
    manager, service = _manager(tmp_path, acct)
    _pre_resident(acct, 555, manager)

    events_path = _events_path(tmp_path)
    bad_values = [True, "not-a-number", float("nan"), float("inf"), 1e20, 10**400]
    lines = [
        json.dumps({
            "type": "tool_call", "tool_name": f"tool{i}",
            "tool_args": {"action": "run", "_reasoning": "x"},
            "ts": v,
        })
        for i, v in enumerate(bad_values)
    ]
    _write_lines(events_path, lines)

    manager._poll_event_tail()

    window = manager._task_card_event_window()
    assert len(window) == len(bad_values)
    for row in window:
        assert "started_at" not in row


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
