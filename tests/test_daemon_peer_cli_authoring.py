"""Checkpoint D — CLI sentinel authoring + post-turn parse/routing.

Scope (narrow): prove that for Codex / Claude Code CLI peer authors,

- ``_handle_emanate_cli`` prepends ``peer.build_peer_author_contract`` to the
  initial CLI prompt only when ``peer_author`` is set on an allowed CLI author
  backend (and leaves the run_dir's raw task untouched);
- ``_maybe_handle_cli_peer_intent`` parses the COMPLETE terminal text with the
  strict ``peer.parse_peer_send_contract`` sentinel parser, fails closed on
  every parser error (malformed / multiple / unterminated), and is a silent
  no-op when there is no block / not a peer author / wrong backend;
- it derives source identity from ``entry['run_dir'].run_id`` + live group
  membership, authorizes through the single ``peer.authorize_peer_message``
  gate, and delivers by REUSING ``_handle_ask`` (no parallel queue/outbox);
- the CLI status matrix is stable: sent / busy / not_ready / target_done /
  denial statuses; the per-group counter only moves on ``sent``;
- all logs/events are metadata-only — the message body never leaks.

Tests drive the DaemonManager directly against injected emanation entries and
test doubles (``_handle_ask`` is stubbed for the mapping tests). No real Codex /
Claude CLI, LLM, subprocess, or pool work is required.
"""
import threading
import time
from concurrent.futures import Future
from unittest.mock import MagicMock

import pytest

from lingtai_kernel.config import AgentConfig

from lingtai.core.daemon import (
    _CLI_PEER_AUTHOR_BACKENDS,
    _cli_initial_prompt_with_peer_contract,
)
from lingtai.core.daemon import peer


# ---------------------------------------------------------------------------
# Harness (mirrors test_daemon_peer_delivery)
# ---------------------------------------------------------------------------

def _make_agent(tmp_path, capabilities=None):
    from lingtai.agent import Agent
    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    return Agent(
        svc,
        working_dir=tmp_path / "daemon-agent",
        capabilities=capabilities or ["file", "daemon"],
        config=AgentConfig(),
    )


def _make_mgr(tmp_path):
    agent = _make_agent(tmp_path)
    return agent, agent.get_capability("daemon")


def _inject(mgr, agent, *, em_id, backend="lingtai", peer_author=False,
            done=False, ask_in_flight=False):
    """Register a fake emanation entry; return its stable run_id."""
    from lingtai.core.daemon.run_dir import DaemonRunDir
    run_dir = DaemonRunDir(
        parent_working_dir=agent._working_dir,
        handle=em_id,
        task="test task",
        tools=["file"],
        model="mock-model",
        max_turns=30,
        timeout_s=300.0,
        parent_addr=agent._working_dir.name,
        parent_pid=12345,
        system_prompt="You are a daemon.",
        backend=backend,
    )
    fut: Future = Future()
    if done:
        fut.set_result("done")
    entry = {
        "future": fut,
        "task": "test task",
        "start_time": time.time(),
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
        "backend": backend,
        "peer_author": peer_author,
        "ask_in_flight": ask_in_flight,
    }
    mgr._emanations[em_id] = entry
    return run_dir.run_id


def _member_spec(em_id, handle, *, role=None, author=True, receive=True):
    return {
        "id": em_id,
        "handle": handle,
        "role": role,
        "can_author_peer_send": author,
        "can_receive_peer_message": receive,
    }


def _make_cli_group(mgr, agent, *, source_backend="codex",
                    target_backend="lingtai", target_done=False):
    """Active two-member group: alpha (CLI author) -> beta (receiver)."""
    _inject(mgr, agent, em_id="em-1", backend=source_backend, peer_author=True)
    _inject(mgr, agent, em_id="em-2", backend=target_backend, peer_author=False,
            done=target_done)
    gc = mgr.handle({"action": "group_create", "members": [
        _member_spec("em-1", "alpha", author=True, receive=False),
        _member_spec("em-2", "beta", author=False, receive=True),
    ], "policy": {"allow_pairs": None}})
    assert gc["status"] == "created", gc
    return gc["group_id"]


def _capture_logs(mgr):
    events = []
    orig = mgr._log

    def spy(event_type, **fields):
        events.append((event_type, fields))
        return orig(event_type, **fields)

    mgr._log = spy
    return events


def _sentinel(to="beta", body="ping from alpha", in_reply_to=None):
    import json
    obj = {"to": to, "body": body}
    if in_reply_to is not None:
        obj["in_reply_to"] = in_reply_to
    return (
        "Here is my reply.\n"
        f"{peer.PEER_SEND_OPEN}\n{json.dumps(obj)}\n{peer.PEER_SEND_CLOSE}\n"
    )


# ---------------------------------------------------------------------------
# 1. Prompt assembly — contract prepend (pure helper)
# ---------------------------------------------------------------------------

def test_contract_prepended_for_allowed_cli_author_backends():
    for backend in ("codex", "claude-code"):
        out = _cli_initial_prompt_with_peer_contract(backend, "do the task", True)
        assert out.endswith("do the task")
        assert peer.PEER_SEND_OPEN in out
        assert out.startswith(peer.build_peer_author_contract(backend=backend))


def test_contract_absent_without_peer_author():
    out = _cli_initial_prompt_with_peer_contract("codex", "do the task", False)
    assert out == "do the task"
    assert peer.PEER_SEND_OPEN not in out


def test_contract_absent_for_unsupported_author_backend():
    # claude-p is intentionally NOT an allowed CLI author backend (it is
    # already rejected at emanate time); opencode/cursor never author.
    for backend in ("claude-p", "opencode", "cursor"):
        out = _cli_initial_prompt_with_peer_contract(backend, "task", True)
        assert out == "task", backend
    assert "claude-p" not in _CLI_PEER_AUTHOR_BACKENDS
    assert _CLI_PEER_AUTHOR_BACKENDS == {"claude-code", "codex"}


def test_emanate_cli_prepends_contract_to_spawned_prompt(tmp_path, monkeypatch):
    """`_handle_emanate_cli` must hand the contract-prefixed prompt to the CLI
    runner while leaving the run_dir's recorded task raw."""
    agent, mgr = _make_mgr(tmp_path)

    captured = {}

    def fake_codex(em_id, run_dir, task, cancel_event, timeout_event, backend_argv):
        captured["task"] = task
        captured["run_dir_task"] = run_dir._state["task"]
        return "done"

    monkeypatch.setattr(mgr, "_run_codex_emanation", fake_codex)

    res = mgr.handle({
        "action": "emanate",
        "backend": "codex",
        "tasks": [{"task": "review section 3", "tools": [], "peer_author": True}],
    })
    assert res["status"] == "dispatched", res
    em_id = res["ids"][0]
    mgr._emanations[em_id]["future"].result(timeout=5)

    assert captured["task"].startswith(
        peer.build_peer_author_contract(backend="codex"))
    assert captured["task"].endswith("review section 3")
    # run_dir keeps the raw user task — only the spawned prompt carries contract.
    assert captured["run_dir_task"] == "review section 3"


def test_emanate_cli_no_contract_without_peer_author(tmp_path, monkeypatch):
    agent, mgr = _make_mgr(tmp_path)
    captured = {}

    def fake_codex(em_id, run_dir, task, cancel_event, timeout_event, backend_argv):
        captured["task"] = task
        return "done"

    monkeypatch.setattr(mgr, "_run_codex_emanation", fake_codex)
    res = mgr.handle({
        "action": "emanate", "backend": "codex",
        "tasks": [{"task": "plain task", "tools": []}],
    })
    em_id = res["ids"][0]
    mgr._emanations[em_id]["future"].result(timeout=5)
    assert captured["task"] == "plain task"


@pytest.mark.parametrize("backend", ["claude-p", "opencode", "cursor"])
def test_emanate_rejects_peer_author_for_unsupported_backends(tmp_path, backend):
    """Lock the approved emanate-time rejection of ``peer_author`` for backends
    that cannot author: ``claude-p`` (the print-mode CLI named ``claude-code``
    for authoring) and the receive-only CLIs ``opencode`` / ``cursor`` that the
    task names explicitly. Refused before any run_dir/spawn."""
    agent, mgr = _make_mgr(tmp_path)
    res = mgr.handle({
        "action": "emanate", "backend": backend,
        "tasks": [{"task": "x", "tools": [], "peer_author": True}],
    })
    assert res["status"] == "error", res
    assert res["reason"] == "unsupported_author_backend", res
    # Nothing was scheduled.
    assert not mgr._emanations


# ---------------------------------------------------------------------------
# 2. Parser gating — no-op cases (never deliver, sometimes no event)
# ---------------------------------------------------------------------------

def test_no_op_when_not_peer_author(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    mgr._emanations["em-1"]["peer_author"] = False
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    assert not [e for e in events if "peer" in e[0]]
    assert mgr._emanations["em-2"]["followup_buffer"] == ""


def test_no_op_when_backend_not_allowed(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    # opencode source can never author; even with peer_author + a clean block.
    _inject(mgr, agent, em_id="em-1", backend="opencode", peer_author=True)
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    assert not [e for e in events if "peer" in e[0]]


def test_no_op_when_no_sentinel_block(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], "just a normal answer, no block")
    assert not [e for e in events if "peer" in e[0]]
    assert mgr._emanations["em-2"]["followup_buffer"] == ""


def test_none_entry_is_safe_noop(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    # Registration-after-submit race: entry not yet present -> fail-closed no-op.
    mgr._maybe_handle_cli_peer_intent("em-missing", None, _sentinel())


# ---------------------------------------------------------------------------
# 3. Parser fail-closed — rejection (metadata-only, no body, no delivery)
# ---------------------------------------------------------------------------

def _assert_rejected_body_free(events, body):
    rej = [e for e in events if e[0] == "peer_intent_rejected"]
    assert rej, f"expected peer_intent_rejected; got {[e[0] for e in events]}"
    _etype, fields = rej[-1]
    assert fields.get("reason")
    assert fields.get("source_adapter") == "cli-stdout"
    for v in fields.values():
        assert body not in str(v)


def test_malformed_json_rejected(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    events = _capture_logs(mgr)
    text = f"{peer.PEER_SEND_OPEN}\n{{not json{peer.PEER_SEND_CLOSE}\n"
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], text)
    _assert_rejected_body_free(events, "irrelevant")
    assert mgr._emanations["em-2"]["followup_buffer"] == ""


def test_multiple_blocks_rejected(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    events = _capture_logs(mgr)
    secret = "SECRET-BODY"
    one = _sentinel(body=secret)
    text = one + "\n" + _sentinel(body="second")
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], text)
    _assert_rejected_body_free(events, secret)
    rej = [e for e in events if e[0] == "peer_intent_rejected"][-1]
    assert rej[1]["reason"] == "multiple_blocks"
    assert mgr._emanations["em-2"]["followup_buffer"] == ""
    # No delivery / no parsed-accept event for an ambiguous multi-block turn.
    assert not [e for e in events if e[0] == "peer_send_sent"]


def test_unterminated_block_rejected(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    events = _capture_logs(mgr)
    text = f"{peer.PEER_SEND_OPEN}\n{{\"to\":\"beta\",\"body\":\"hi\"}}\n"  # no END
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], text)
    rej = [e for e in events if e[0] == "peer_intent_rejected"]
    assert rej and rej[-1][1]["reason"] == "unterminated_block"
    assert mgr._emanations["em-2"]["followup_buffer"] == ""


def test_forbidden_identity_keys_rejected(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    events = _capture_logs(mgr)
    text = (
        f"{peer.PEER_SEND_OPEN}\n"
        '{"to":"beta","body":"hi","from":"alpha","run_id":"spoof"}\n'
        f"{peer.PEER_SEND_CLOSE}\n"
    )
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], text)
    rej = [e for e in events if e[0] == "peer_intent_rejected"]
    assert rej and rej[-1][1]["reason"] == "forbidden_keys"
    assert mgr._emanations["em-2"]["followup_buffer"] == ""


# ---------------------------------------------------------------------------
# 4. Delivery mapping — stub _handle_ask (deterministic, no spawn)
# ---------------------------------------------------------------------------

def _stub_handle_ask(mgr, result):
    calls = []

    def fake_ask(target_em_id, message):
        calls.append((target_em_id, message))
        return result

    mgr._handle_ask = fake_ask
    return calls


def test_parsed_block_delivers_via_handle_ask_and_logs_sent(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent)
    calls = _stub_handle_ask(mgr, {"status": "sent", "id": "em-2"})
    events = _capture_logs(mgr)

    body = "please review section 3"
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(body=body))

    # Delivery reused _handle_ask with the provenance-bannered body.
    assert len(calls) == 1
    target_em_id, delivered = calls[0]
    assert target_em_id == "em-2"
    assert "[peer message]" in delivered
    assert "from: @alpha" in delivered
    assert group_id in delivered
    assert body in delivered  # banner payload carries the full body to the peer

    # parsed + sent events, body-free, with cli-stdout provenance.
    etypes = [e[0] for e in events]
    assert "peer_intent_parsed" in etypes
    sent = [e for e in events if e[0] == "peer_send_sent"][-1][1]
    assert sent["status"] == "sent"
    assert sent["from_handle"] == "alpha"
    assert sent["to_handle"] == "beta"
    assert sent["source_adapter"] == "cli-stdout"
    assert sent.get("message_id")
    for v in sent.values():
        assert body not in str(v)
    # counter bumped exactly once on sent.
    assert mgr._groups[group_id].message_count == 1


def test_in_reply_to_threaded_into_delivered_banner(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    calls = _stub_handle_ask(mgr, {"status": "sent"})
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"],
        _sentinel(body="re: hi", in_reply_to="pm-prev-9"))
    assert "pm-prev-9" in calls[0][1]


def test_busy_handle_ask_maps_to_peer_busy(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent, target_backend="codex")
    _stub_handle_ask(mgr, {"status": "busy", "id": "em-2"})
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "busy"
    assert und["reason"] == "peer_busy"
    assert mgr._groups[group_id].message_count == 0


def test_handle_ask_error_maps_to_not_ready(tmp_path):
    """Any non-sent/non-busy _handle_ask result -> not_ready (never string-match;
    never target_done from _handle_ask text)."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent, target_backend="codex")
    _stub_handle_ask(mgr, {"status": "error", "message": "not running"})
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "not_ready"
    assert mgr._groups[group_id].message_count == 0


def test_handle_ask_raise_maps_to_error_terminal(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent, target_backend="codex")

    def boom(target_em_id, message):
        raise RuntimeError("delivery exploded")

    mgr._handle_ask = boom
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "error"
    assert und["reason"] == "ask_raised"


# ---------------------------------------------------------------------------
# 5. Live-target re-check matrix (decided BEFORE _handle_ask)
# ---------------------------------------------------------------------------

def test_completed_lingtai_target_is_target_done(tmp_path):
    """A completed in-process LingTai target is not resumable -> target_done,
    decided by future.done() and never reaching _handle_ask."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent, target_backend="lingtai")
    mgr._emanations["em-2"]["future"].set_result("done")
    calls = _stub_handle_ask(mgr, {"status": "sent"})
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "target_done"
    assert und["reason"] == "target_completed"
    assert calls == []  # never attempted delivery
    assert mgr._groups[group_id].message_count == 0


def test_completed_cli_target_still_attempts_delivery(tmp_path):
    """A completed CLI target MAY receive via resume — delivery is attempted
    (left to _handle_ask), not short-circuited to target_done."""
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent, target_backend="codex", target_done=True)
    calls = _stub_handle_ask(mgr, {"status": "sent"})
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    assert len(calls) == 1
    assert [e for e in events if e[0] == "peer_send_sent"]


def test_missing_target_entry_is_not_ready(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    del mgr._emanations["em-2"]
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "not_ready"
    assert und["reason"] == "target_missing"


def test_stale_target_run_id_is_not_ready(tmp_path):
    from lingtai.core.daemon.run_dir import DaemonRunDir
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    stale = mgr._emanations["em-2"]["run_dir"].run_id
    fresh = DaemonRunDir(
        parent_working_dir=agent._working_dir, handle="em-2", task="t",
        tools=["file"], model="mock-model", max_turns=30, timeout_s=300.0,
        parent_addr=agent._working_dir.name, parent_pid=12345,
        system_prompt="d", backend="lingtai",
    )
    assert fresh.run_id != stale
    mgr._emanations["em-2"]["run_dir"] = fresh
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "not_ready"
    assert und["reason"] == "target_not_live"


def test_cli_target_ask_in_flight_is_busy(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent, target_backend="codex")
    mgr._emanations["em-2"]["ask_in_flight"] = True
    calls = _stub_handle_ask(mgr, {"status": "sent"})
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    und = [e for e in events if e[0] == "peer_send_undelivered"][-1][1]
    assert und["status"] == "busy"
    assert und["reason"] == "peer_busy"
    assert calls == []  # never spawned a second resume
    assert mgr._groups[group_id].message_count == 0


# ---------------------------------------------------------------------------
# 6. Authorization denials flow through the single gate
# ---------------------------------------------------------------------------

def test_unknown_target_handle_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(to="ghost"))
    den = [e for e in events if e[0] == "peer_send_denied"][-1][1]
    assert den["status"] == "unknown_peer"
    assert den["reason"] == "unknown_target_handle"
    assert den["source_adapter"] == "cli-stdout"


def test_source_not_in_group_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    # CLI author exists + peer_author, but no group was ever created.
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=True)
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    den = [e for e in events if e[0] == "peer_send_denied"][-1][1]
    assert den["status"] == "not_in_group"
    assert den["reason"] == "source_not_in_active_group"


def test_reclaimed_group_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent)
    mgr.handle({"action": "group_reclaim", "group_id": group_id})
    events = _capture_logs(mgr)
    mgr._maybe_handle_cli_peer_intent("em-1", mgr._emanations["em-1"], _sentinel())
    den = [e for e in events if e[0] == "peer_send_denied"][-1][1]
    assert den["status"] == "group_reclaimed"


def test_message_too_large_denied(tmp_path):
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent)
    cap = mgr._groups[group_id].policy.max_message_bytes
    events = _capture_logs(mgr)
    big = "X" * (cap + 1)
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(body=big))
    den = [e for e in events if e[0] == "peer_send_denied"][-1][1]
    assert den["status"] == "message_too_large"
    for v in den.values():
        assert big not in str(v)


# ---------------------------------------------------------------------------
# 7. Integration — real live LingTai target via the actual _handle_ask path
# ---------------------------------------------------------------------------

def test_integration_delivers_to_live_lingtai_target_buffer(tmp_path):
    """End-to-end through the real _handle_ask: a live in-process LingTai
    target's follow-up buffer receives the bannered body. _handle_ask
    concatenates (it does not check-and-set like the in-process peer path) —
    asserted here as the deliberate, task-mandated reuse of _handle_ask."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent, target_backend="lingtai")
    # Pre-existing pending text: _handle_ask concatenates onto it (not busy).
    mgr._emanations["em-2"]["followup_buffer"] = "earlier note"

    body = "ping into live target"
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(body=body))

    delivered = mgr._emanations["em-2"]["followup_buffer"]
    assert delivered.startswith("earlier note")  # concatenation, not overwrite
    assert "[peer message]" in delivered
    assert body in delivered
    assert mgr._groups[group_id].message_count == 1


def test_route_wrapper_swallows_exceptions(tmp_path, monkeypatch):
    """The terminal-site wrapper must never raise into the runner."""
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)

    def boom(em_id, entry, terminal_text):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mgr, "_maybe_handle_cli_peer_intent", boom)
    events = _capture_logs(mgr)
    # Must not raise.
    mgr._route_cli_peer_intent_after_turn("em-1", _sentinel())
    assert [e for e in events if e[0] == "peer_intent_error"]


# ---------------------------------------------------------------------------
# 8. CLI ``@handle`` normalization (v0 narrow fix)
#
# Roster notices display peers as ``@handle``, so a real CLI author may echo
# that form in the sentinel's ``to`` field. The CLI sentinel path normalizes
# exactly one leading ``@`` before envelope construction / authorization;
# malformed/ambiguous forms (``@`` / ``@@beta``) stay fail-closed. The native
# in-process ``peer_send`` path is unaffected (strict/bare).
# ---------------------------------------------------------------------------

def test_normalize_cli_peer_to_handle_unit():
    from lingtai.core.daemon import _normalize_cli_peer_to_handle as norm
    # Strip exactly one leading ``@`` when the remainder is non-empty and does
    # not itself start with ``@``.
    assert norm("@beta") == "beta"
    assert norm("beta") == "beta"
    # Fail-closed: leave malformed/ambiguous values untouched so authz denies.
    assert norm("@") == "@"
    assert norm("@@beta") == "@@beta"


def test_at_handle_routes_to_bare_roster_handle(tmp_path):
    """``{"to":"@beta"}`` normalizes to roster handle ``beta``: delivers through
    _handle_ask, logs sent with normalized ``to_handle == "beta"``, bumps the
    per-group counter once, and never leaks the body."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent)
    calls = _stub_handle_ask(mgr, {"status": "sent", "id": "em-2"})
    events = _capture_logs(mgr)

    body = "please review section 3"
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(to="@beta", body=body))

    # Delivery reused _handle_ask, addressed to the bare roster target.
    assert len(calls) == 1
    target_em_id, delivered = calls[0]
    assert target_em_id == "em-2"
    assert "from: @alpha" in delivered
    assert body in delivered

    # sent event carries the NORMALIZED bare handle, body-free.
    sent = [e for e in events if e[0] == "peer_send_sent"][-1][1]
    assert sent["status"] == "sent"
    assert sent["to_handle"] == "beta"
    assert sent["from_handle"] == "alpha"
    assert sent["source_adapter"] == "cli-stdout"
    for v in sent.values():
        assert body not in str(v)

    # parsed event also reports the normalized handle (raw kept only if differs).
    parsed = [e for e in events if e[0] == "peer_intent_parsed"][-1][1]
    assert parsed["to_handle"] == "beta"
    assert parsed.get("raw_to_handle") == "@beta"

    # counter bumped exactly once on sent; no denial occurred.
    assert mgr._groups[group_id].message_count == 1
    assert not [e for e in events if e[0] == "peer_send_denied"]


@pytest.mark.parametrize("bad_to", ["@", "@@beta"])
def test_malformed_at_handle_stays_denied(tmp_path, bad_to):
    """``@`` and ``@@beta`` must not silently route to ``beta``: no _handle_ask
    delivery, no counter bump, denied as unknown_peer/unknown_target_handle."""
    agent, mgr = _make_mgr(tmp_path)
    group_id = _make_cli_group(mgr, agent)
    calls = _stub_handle_ask(mgr, {"status": "sent", "id": "em-2"})
    events = _capture_logs(mgr)

    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(to=bad_to))

    # No delivery, no counter movement.
    assert calls == []
    assert mgr._groups[group_id].message_count == 0
    assert not [e for e in events if e[0] == "peer_send_sent"]

    # Fails closed through the single authz gate.
    den = [e for e in events if e[0] == "peer_send_denied"][-1][1]
    assert den["status"] == "unknown_peer"
    assert den["reason"] == "unknown_target_handle"
    assert den["to_handle"] == bad_to


# ---------------------------------------------------------------------------
# 9. Codex resume race — initial-turn guard in _handle_ask_codex
#
# Codex emits ``thread.started.thread_id`` (captured as ``codex_session_id``)
# *before* the initial rollout is fully resumable. A ``daemon(ask)`` that races
# in between — ``codex_session_id`` present but the initial CLI ``future`` still
# running — must NOT spawn ``codex exec resume`` (it would fail with
# ``thread/resume failed: no rollout found for thread id ...``). The guard
# returns ``busy`` and tells the caller to wait. Once the initial future is
# done, the resume spawn path is reached normally.
# ---------------------------------------------------------------------------

def _spy_popen(monkeypatch, *, proc=None):
    """Record every subprocess.Popen call in the daemon module; return calls."""
    from lingtai.core import daemon as daemon_mod
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if proc is None:
            raise AssertionError(
                "subprocess.Popen must not be called for this case")
        return proc
    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)
    return calls


def test_codex_ask_busy_while_initial_future_running(tmp_path, monkeypatch):
    """codex_session_id present but the initial Codex turn is still running:
    _handle_ask must return busy, must NOT spawn `codex exec resume`, and must
    NOT flip ask_in_flight."""
    agent, mgr = _make_mgr(tmp_path)
    # Codex emanation whose initial future is NOT done.
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=False,
            done=False)
    entry = mgr._emanations["em-1"]
    # Codex emitted thread.started early — session id is captured but the
    # rollout is not yet resumable.
    entry["run_dir"]._state["codex_session_id"] = "codex-thread-early"
    assert not entry["future"].done()

    popen_calls = _spy_popen(monkeypatch, proc=None)

    result = mgr._handle_ask("em-1", "what next?")

    assert result["status"] == "busy"
    assert result["id"] == "em-1"
    # Distinguishing message: this is the initial-turn guard, not concurrent ask.
    assert "initial" in result["message"].lower()
    # No resume spawned, no in-flight flag set, no ask future created.
    assert popen_calls == []
    assert entry.get("ask_in_flight") is not True
    assert entry.get("ask_future") is None


def test_codex_ask_reaches_spawn_after_initial_future_done(tmp_path, monkeypatch):
    """Once the initial Codex future is done, a codex_session_id ask reaches the
    resume spawn path normally (busy guard does not over-fire)."""
    from concurrent.futures import Future as _Future
    agent, mgr = _make_mgr(tmp_path)
    _inject(mgr, agent, em_id="em-1", backend="codex", peer_author=False,
            done=True)
    entry = mgr._emanations["em-1"]
    entry["run_dir"]._state["codex_session_id"] = "codex-thread-ready"
    assert entry["future"].done()

    fake_proc = MagicMock()
    popen_calls = _spy_popen(monkeypatch, proc=fake_proc)
    # Isolate the spawn DECISION from worker behavior: never run the streaming
    # worker against the mock proc — hand back an already-done future.
    done_future: _Future = _Future()
    done_future.set_result({"status": "ok"})
    monkeypatch.setattr(mgr._ask_pool, "submit",
                        lambda *a, **k: done_future)

    result = mgr._handle_ask("em-1", "follow up please")

    assert result["status"] == "sent"
    assert result.get("async") is True
    # Exactly one resume spawn, and it was a `codex exec resume <session>`.
    assert len(popen_calls) == 1
    cmd = popen_calls[0][0]
    assert cmd[:4] == ["codex", "exec", "resume", "codex-thread-ready"]
    assert entry["ask_in_flight"] is True


def test_send_outcome_traced_in_source_run_dir(tmp_path):
    """Because a CLI author gets no synchronous return, the send outcome must
    also land (body-free) in the SOURCE's own run_dir events trace, so it is
    visible via ``daemon(check)``."""
    import json as _json
    agent, mgr = _make_mgr(tmp_path)
    _make_cli_group(mgr, agent)
    _stub_handle_ask(mgr, {"status": "sent"})

    secret = "trace-body-should-not-appear"
    mgr._maybe_handle_cli_peer_intent(
        "em-1", mgr._emanations["em-1"], _sentinel(body=secret))

    source_run_dir = mgr._emanations["em-1"]["run_dir"]
    lines = [_json.loads(l)
             for l in source_run_dir.events_path.read_text().splitlines()]
    events = {e["event"] for e in lines}
    assert "peer_intent_parsed" in events
    assert "peer_send_sent" in events
    # Body-free even in the local trace.
    for e in lines:
        for v in e.values():
            assert secret not in str(v)
