"""Multi-row Task Card render: parallel/sequential rows + fixed footer.

Parallel tool calls appear as one row per call id (tool.action + reasoning +
own elapsed); a completed row is frozen with a done marker and its final
elapsed.  Both the running and the frozen last-behavior render carry the fixed
human warning footer, and redaction happens before any truncation so a secret
can never survive a length-pressure trim.
"""

from __future__ import annotations

from lingtai.mcp_servers.telegram.manager import (
    TelegramManager,
    _TASK_CARD_FOOTER,
)


def _fmt(rows):
    return TelegramManager._format_task_card_text("", "", "", rows=rows)


# ---------------------------------------------------------------------------
# Footer — fixed human warning in every render
# ---------------------------------------------------------------------------

def test_footer_constant_exact_text():
    assert "⚠️ Progress only — don't reply to this Task Card." in _TASK_CARD_FOOTER
    # Also redirects the human to the real conversation.
    assert "reply" in _TASK_CARD_FOOTER.lower()


def test_running_render_has_footer():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile",
         "elapsed_s": 3, "done": False},
    ])
    assert _TASK_CARD_FOOTER in text
    assert "📋 TASK CARD" in text


def test_frozen_render_has_footer():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile",
         "elapsed_s": 7, "done": True},
    ])
    assert _TASK_CARD_FOOTER in text


# ---------------------------------------------------------------------------
# One row per call, with tool.action + reasoning + own elapsed
# ---------------------------------------------------------------------------

def test_single_row_shows_tool_action_reasoning_elapsed():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "compile project",
         "elapsed_s": 3, "done": False},
    ])
    assert "bash.run" in text
    assert "compile project" in text
    # Elapsed renders as whole seconds, no decimal point.
    assert "3s" in text
    assert "3.0s" not in text


def test_parallel_rows_all_represented_with_independent_elapsed():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": False},
        {"tool": "read", "tool_action": "", "reasoning": "open file",
         "elapsed_s": 2, "done": False},
        {"tool": "grep", "tool_action": "", "reasoning": "scan",
         "elapsed_s": 8, "done": True},
    ])
    # Each row present with its own tool + whole-second elapsed.
    assert "bash.run" in text
    assert "read" in text
    assert "grep" in text
    assert "5s" in text
    assert "2s" in text
    assert "8s" in text


# ---------------------------------------------------------------------------
# Whole-second display rule (no decimal point)
# ---------------------------------------------------------------------------

def test_no_decimal_point_in_render():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 12, "done": False},
    ])
    assert "12s" in text
    # The elapsed suffix is whole-second, no decimal point in it.
    row_line = next(ln for ln in text.splitlines() if "bash.run" in ln)
    elapsed_suffix = row_line[row_line.rindex("("):]  # "(12s)"
    assert elapsed_suffix == "(12s)"
    assert "." not in elapsed_suffix


def test_float_elapsed_payload_is_floored_to_whole_second():
    """A float elapsed (e.g. from an in-flight value) is floored, not rounded,
    and shows no decimal — 8.99s displays 8s."""
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 8.99, "done": False},
    ])
    assert "8s" in text
    assert "8.99" not in text
    assert "9s" not in text


def test_zero_elapsed_renders_zero_seconds():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 0, "done": False},
    ])
    assert "0s" in text
    assert "0.0s" not in text


def test_done_row_elapsed_is_whole_second():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "x",
         "elapsed_s": 7, "done": True},
    ])
    assert "7s" in text
    assert "7.0s" not in text


def test_done_row_has_marker_and_active_row_does_not():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": True},
        {"tool": "read", "tool_action": "", "reasoning": "open",
         "elapsed_s": 2, "done": False},
    ])
    lines = text.splitlines()
    bash_line = next(ln for ln in lines if "bash.run" in ln)
    read_line = next(ln for ln in lines if "read" in ln and "open" in ln)
    assert "✓" in bash_line
    assert "✓" not in read_line


# ---------------------------------------------------------------------------
# Frozen last-behavior: concrete rows, NOT a generic overall DONE headline
# ---------------------------------------------------------------------------

def test_frozen_render_keeps_concrete_rows_no_generic_done_subject():
    text = _fmt([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 5, "done": True},
        {"tool": "read", "tool_action": "", "reasoning": "open file",
         "elapsed_s": 2, "done": True},
    ])
    # Concrete last-behavior preserved.
    assert "bash.run" in text
    assert "read" in text
    assert "open file" in text
    # No generic overall DONE subject replacing the rows.
    assert "TASK CARD · DONE" not in text
    assert "✅" not in text


# ---------------------------------------------------------------------------
# Redaction BEFORE truncation; every parallel row still represented
# ---------------------------------------------------------------------------

def test_redaction_before_truncation_per_row():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    text = _fmt([
        {"tool": "bash", "tool_action": "run",
         "reasoning": "X" * 480 + " " + secret,
         "elapsed_s": 1, "done": False},
    ])
    assert "ghp_" not in text
    assert "<REDACTED" in text or "github_token" in text


def test_many_rows_bounded_but_each_row_represented():
    """Under many-row length pressure the render stays bounded, yet no parallel
    row is hidden — each call id must remain visible."""
    rows = [
        {"tool": f"tool{i}", "tool_action": "", "reasoning": "Z" * 600,
         "elapsed_s": i, "done": False}
        for i in range(8)
    ]
    text = _fmt(rows)
    # Bounded well under the Telegram 4096-char hard limit.
    assert len(text) <= 4096
    # Each of the 8 rows is still represented by its tool name.
    for i in range(8):
        assert f"tool{i}" in text
    # Footer survives the length pressure.
    assert _TASK_CARD_FOOTER in text


# ---------------------------------------------------------------------------
# Integration: rows route through manager.handle → real dispatch → send/edit
# ---------------------------------------------------------------------------

def _integration_manager(tmp_path):
    from pathlib import Path

    class Acct:
        alias = "mybot"

        def __init__(self):
            self.calls = []
            self._cards = {}

        def send_message(self, chat_id, text, reply_to_message_id=None, **kw):
            self.calls.append(("send_message", chat_id, text))
            return {"message_id": 100}

        def edit_message(self, chat_id, message_id, text, **kw):
            self.calls.append(("edit_message", chat_id, message_id, text))
            return {"ok": True}

        def delete_message(self, chat_id, message_id):
            self.calls.append(("delete_message", chat_id, message_id))
            return {"ok": True}

        def get_task_card(self, chat_id):
            return self._cards.get(str(chat_id))

        def set_task_card(self, chat_id, cid):
            self._cards[str(chat_id)] = cid

        def clear_task_card(self, chat_id):
            self._cards.pop(str(chat_id), None)

    class Svc:
        def __init__(self):
            self.default_account = Acct()

        def get_account(self, alias):
            assert alias == "mybot"
            return self.default_account

    svc = Svc()
    mgr = TelegramManager(svc, working_dir=Path(tmp_path), on_inbound=lambda _: None)
    return mgr, svc.default_account


def test_full_routing_rows_create_renders_multi_row_card(tmp_path):
    mgr, account = _integration_manager(tmp_path)
    r = mgr.handle({
        "action": "_task_card_update",
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "rows": [
            {"tool": "bash", "tool_action": "run", "reasoning": "build",
             "elapsed_s": 2, "done": False},
            {"tool": "read", "tool_action": "", "reasoning": "open",
             "elapsed_s": 1, "done": False},
        ],
    })
    assert r["status"] == "ok"
    sends = [c for c in account.calls if c[0] == "send_message"]
    assert len(sends) == 1
    text = sends[0][2]
    assert "bash.run" in text
    assert "read" in text
    assert _TASK_CARD_FOOTER in text


def test_full_routing_rows_finalize_freezes_without_generic_done(tmp_path):
    mgr, account = _integration_manager(tmp_path)
    r1 = mgr.handle({
        "action": "_task_card_update", "sub_action": "create",
        "account": "mybot", "chat_id": 999,
        "rows": [{"tool": "bash", "tool_action": "run", "reasoning": "build",
                  "elapsed_s": 2, "done": False}],
    })
    card_id = r1["message_id"]
    r2 = mgr.handle({
        "action": "_task_card_update", "sub_action": "finalize",
        "card_message_id": card_id,
        "rows": [{"tool": "bash", "tool_action": "run", "reasoning": "build",
                  "elapsed_s": 5, "done": True}],
    })
    assert r2["status"] == "ok"
    edits = [c for c in account.calls if c[0] == "edit_message"]
    final_text = edits[-1][3]
    assert "bash.run" in final_text
    assert "✓" in final_text
    assert "TASK CARD · DONE" not in final_text
    assert _TASK_CARD_FOOTER in final_text
