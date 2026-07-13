"""Unit tests for the public Telegram-owned ``task_card`` controller (Jason #7258/#7259).

Covers registration, exact-one schema-valid JSON, path containment, synchronous
initial errors (timeout/nonzero/invalid-frame), the async watch lifecycle,
inspect/retry/stop (including the truthful, retryable failed-stop path and the
last-valid timestamp), and the deduped fail-loud LICC error/recovery wakes. No
real Telegram or network — the reverse channel is a fake MCP client.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from lingtai.kernel.base_agent import _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.task_card import (
    TaskCardController,
    get_description,
    get_schema,
    setup,
)


class _FakeClient:
    """Records reverse calls; ``fail`` flips the backend to an error result."""

    def __init__(self) -> None:
        self.calls: list = []
        self.fail = False
        self.result = None

    def call_tool(self, name, args, timeout=None):
        self.calls.append((name, dict(args), timeout))
        assert name == _TASK_CARD_TOOL
        assert "action" not in args  # server forces the private action
        assert args.get("channel") == "programmable"
        if self.fail:
            return {"status": "error", "error": "backend down"}
        if self.result is not None:
            return dict(self.result)
        return {"status": "ok", "message_id": "acct:42:100"}


class _FakeAgent:
    def __init__(self, working_dir: Path) -> None:
        self._working_dir = working_dir
        self._client = _FakeClient()
        self._mcp_clients_by_tool = {"telegram": self._client}
        self._telegram_task_card_context = {"account": "acct", "chat_id": 42}
        self._shutdown = threading.Event()
        self.wakes: list = []
        self.added_tools: list = []

    def _enqueue_system_notification(self, **kwargs):
        self.wakes.append(kwargs)
        return "notif-id"

    def add_tool(
        self,
        name,
        *,
        schema=None,
        handler=None,
        description="",
        glossary_package="__unset__",
        **_,
    ):
        self.added_tools.append((name, schema, handler, description, glossary_package))


def _write_renderer(workdir: Path, body: str, name: str = "r.py") -> str:
    path = workdir / name
    path.write_text(body)
    return str(path)


_OK_BODY = "import json; print(json.dumps({'title': 'T', 'lines': ['a', 'b']}))"


@pytest.fixture
def agent(tmp_path):
    return _FakeAgent(tmp_path)


@pytest.fixture
def controller(agent):
    ctrl = TaskCardController(agent)
    yield ctrl
    ctrl.shutdown_for_agent_stop()


# -- registration ----------------------------------------------------------


def test_setup_registers_public_tool(agent):
    mgr = setup(agent)
    assert isinstance(mgr, TaskCardController)
    name, schema, handler, _desc, glossary = agent.added_tools[0]
    assert name == "task_card"
    assert glossary is None  # Telegram-owned tool: no lingtai.tools glossary package
    assert schema["properties"]["action"]["enum"] == [
        "start",
        "inspect",
        "retry",
        "stop",
    ]
    assert callable(handler)


def test_schema_requires_action():
    assert get_schema()["required"] == ["action"]


def test_description_routes_to_the_telegram_manual():
    """The public tool description must discoverably route the model to the
    Telegram manual and onward to the co-located Task Card manual."""
    desc = get_description()
    assert "manual" in desc.lower()
    assert "telegram(action='manual')" in desc
    assert "task_card/SKILL.md" in desc
    # It still advertises the concrete action surface.
    for action in ("start", "inspect", "retry", "stop"):
        assert action in desc


def test_wiring_registers_only_with_telegram_and_is_idempotent():
    """The composition-root hook registers ``task_card`` exactly once, and only
    when a Telegram reverse channel is present."""
    from lingtai.agent import Agent

    class _Stub:
        def __init__(self, telegram):
            self._mcp_clients_by_tool = {"telegram": object()} if telegram else {}
            self.added: list = []

        def add_tool(self, name, **_):
            self.added.append(name)

    no_tg = _Stub(telegram=False)
    Agent._maybe_setup_task_card_controller(no_tg)
    assert no_tg.added == []
    assert not hasattr(no_tg, "_task_card_controller")

    tg = _Stub(telegram=True)
    Agent._maybe_setup_task_card_controller(tg)
    assert tg.added == ["task_card"]
    assert hasattr(tg, "_task_card_controller")
    Agent._maybe_setup_task_card_controller(tg)  # idempotent
    assert tg.added == ["task_card"]


# -- start: happy path + projection ---------------------------------------


def test_start_projects_first_frame_and_returns_watch(agent, controller):
    body = _OK_BODY
    result = controller.handle(
        {
            "action": "start",
            "renderer_path": _write_renderer(agent._working_dir, body),
            "interval_s": 3600,
        }
    )
    assert result["status"] == "ok"
    assert result["state"] == "watching"
    wid = result["watch_id"]
    # First frame was projected synchronously with sub_action="create".
    sub_actions = [c[1]["sub_action"] for c in agent._client.calls]
    assert sub_actions == ["create"]
    frame = agent._client.calls[0][1]["card"]
    assert frame == {"lines": ["a", "b"], "title": "T"}
    inspect = controller.handle({"action": "inspect", "watch_id": wid})
    assert inspect["state"] == "watching"
    assert inspect["last_valid_frame"] == frame
    controller.handle({"action": "stop", "watch_id": wid})


# -- synchronous initial errors -------------------------------------------


def test_start_rejects_path_outside_workdir(agent, controller):
    result = controller.handle({"action": "start", "renderer_path": "../../etc/passwd"})
    assert result["status"] == "error"
    assert "working directory" in result["message"]
    assert agent._client.calls == []  # nothing projected


@pytest.mark.parametrize(
    "body,kwargs,name",
    [
        ("import json; print('{}\\n{}')", {}, "two.py"),  # multi-object
        ("print('[1,2,3]')", {}, "arr.py"),  # non-object
        ("import json; print(json.dumps({'lines': [1]}))", {}, "badlines.py"),
        ("pass", {}, "empty.py"),  # empty stdout
        ("import sys; sys.exit(3)", {}, "boom.py"),  # nonzero exit
        ("import time; time.sleep(5)", {"timeout_s": 0.3}, "slow.py"),  # timeout
    ],
)
def test_start_synchronous_frame_errors_create_no_watch(
    agent, controller, body, kwargs, name
):
    args = {
        "action": "start",
        "renderer_path": _write_renderer(agent._working_dir, body, name),
    }
    args.update(kwargs)
    assert controller.handle(args)["status"] == "error"
    assert controller._watches == {}  # no bogus watch handle survives


def test_start_rejects_missing_renderer(agent, controller):
    assert (
        controller.handle({"action": "start", "renderer_path": "nope.py"})["status"]
        == "error"
    )


def test_start_discards_watch_when_backend_rejects_first_frame(agent, controller):
    agent._client.fail = True
    result = controller.handle(
        {
            "action": "start",
            "renderer_path": _write_renderer(agent._working_dir, _OK_BODY),
            "interval_s": 3600,
        }
    )
    assert result["status"] == "error"
    # No watch handle survives a failed first projection.
    assert controller._watches == {}


# -- watch requires a Telegram route --------------------------------------


def test_start_without_route_errors(tmp_path):
    agent = _FakeAgent(tmp_path)
    agent._telegram_task_card_context = None
    controller = TaskCardController(agent)
    result = controller.handle(
        {"action": "start", "renderer_path": _write_renderer(tmp_path, _OK_BODY)}
    )
    assert result["status"] == "error"




def test_project_surfaces_partial_telegram_failure(agent, controller):
    start = controller.handle({
        "action": "start",
        "renderer_path": _write_renderer(agent._working_dir, _OK_BODY),
        "interval_s": 3600,
    })
    watch = controller._watches[start["watch_id"]]
    agent._client.result = {
        "status": "ok",
        "message_id": "acct:42:101",
        "resident_persist_failed": True,
    }

    result = controller._project(watch, "update", {"title": "T"})

    assert result == {
        "status": "error",
        "partial": True,
        "resident_persist_failed": True,
    }
    agent._client.result = None
    controller.handle({"action": "stop", "watch_id": watch.watch_id})


def test_project_rejects_impossible_stale_delete_success_payload(agent, controller):
    start = controller.handle({
        "action": "start",
        "renderer_path": _write_renderer(agent._working_dir, _OK_BODY),
        "interval_s": 3600,
    })
    watch = controller._watches[start["watch_id"]]
    agent._client.result = {
        "status": "ok",
        "message_id": "acct:42:101",
        "stale_delete_failed": True,
    }

    result = controller._project(watch, "update", {"title": "T"})

    assert result == {"status": "error"}
    agent._client.result = None
    controller.handle({"action": "stop", "watch_id": watch.watch_id})


# -- unknown action / watch -----------------------------------------------


def test_unknown_action_and_watch(agent, controller):
    assert controller.handle({"action": "bogus"})["status"] == "error"
    assert (
        controller.handle({"action": "inspect", "watch_id": "missing"})["status"]
        == "error"
    )


# -- retry + fail-loud dedup + recovery -----------------------------------


def test_tick_error_recovery_emits_deduped_wakes(agent, controller):
    renderer = agent._working_dir / "flip.py"
    renderer.write_text(_OK_BODY)
    start = controller.handle(
        {"action": "start", "renderer_path": str(renderer), "interval_s": 3600}
    )
    wid = start["watch_id"]
    watch = controller._watches[wid]
    accepted_at = watch.last_valid_at  # UTC timestamp of the accepted first frame
    assert accepted_at is not None

    # Flip the renderer to a failing one and tick twice: identical failure state
    # emits exactly one fail-loud wake (deduped by error code).
    renderer.write_text("import sys; sys.exit(1)")
    controller._tick(watch)
    controller._tick(watch)
    err_wakes = [w for w in agent.wakes if w["extra"]["state"] == "error"]
    assert len(err_wakes) == 1
    assert err_wakes[0]["source"] == "task_card.error"
    assert err_wakes[0]["priority"] == "high"
    assert err_wakes[0]["skip_if_idempotency_key_exists"] is True
    # The fail-loud wake carries the real accepted-frame timestamp.
    assert err_wakes[0]["extra"]["last_valid_frame_at"] == accepted_at
    assert controller.handle({"action": "inspect", "watch_id": wid})["state"] == "error"

    # Recover: a good frame clears the error and emits one recovery wake.
    renderer.write_text(_OK_BODY)
    controller._tick(watch)
    rec_wakes = [w for w in agent.wakes if w["extra"]["state"] == "recovered"]
    assert len(rec_wakes) == 1
    assert (
        controller.handle({"action": "inspect", "watch_id": wid})["state"] == "watching"
    )
    controller.handle({"action": "stop", "watch_id": wid})


def test_same_code_refails_after_recovery_emits_new_durable_wake(agent, controller):
    """Back-to-back identical failures dedupe within one episode, but the SAME
    code re-failing AFTER a recovery must emit a fresh durable wake with a
    distinct (per-episode) idempotency key — never suppressed by the prior
    episode's still-stored notification."""
    renderer = agent._working_dir / "flip.py"
    renderer.write_text(_OK_BODY)
    wid = controller.handle(
        {"action": "start", "renderer_path": str(renderer), "interval_s": 3600}
    )["watch_id"]
    watch = controller._watches[wid]

    renderer.write_text("import sys; sys.exit(1)")
    controller._tick(watch)  # episode 1: error
    controller._tick(watch)  # identical -> deduped within the episode
    renderer.write_text(_OK_BODY)
    controller._tick(watch)  # recovery
    renderer.write_text("import sys; sys.exit(1)")
    controller._tick(watch)  # episode 2: SAME code, must re-fire

    err_wakes = [w for w in agent.wakes if w["extra"]["state"] == "error"]
    assert len(err_wakes) == 2
    assert {w["extra"]["code"] for w in err_wakes} == {"renderer_nonzero_exit"}
    keys = [w["idempotency_key"] for w in err_wakes]
    assert keys[0] != keys[1]  # distinct per-episode idempotency keys
    assert any(w["extra"]["state"] == "recovered" for w in agent.wakes)
    controller.handle({"action": "stop", "watch_id": wid})


def test_join_timeout_is_truthful_against_reverse_call_timeout():
    """Stop/shutdown must be able to actually join a tick blocked in the reverse
    call, so the join budget must exceed the reverse-call timeout."""
    from lingtai.mcp_servers.telegram.task_card import controller as tc

    assert tc._JOIN_TIMEOUT_S > tc._REVERSE_CALL_TIMEOUT_S


def test_retry_action_reruns_now(agent, controller):
    renderer = agent._working_dir / "retry.py"
    renderer.write_text("import sys; sys.exit(1)")
    # Seed a watch by driving start with a good frame, then break the renderer.
    renderer.write_text(_OK_BODY)
    wid = controller.handle(
        {"action": "start", "renderer_path": str(renderer), "interval_s": 3600}
    )["watch_id"]
    renderer.write_text("import sys; sys.exit(1)")
    out = controller.handle({"action": "retry", "watch_id": wid})
    assert out["state"] == "error"
    controller.handle({"action": "stop", "watch_id": wid})


# -- stop finalizes only the programmable slot ----------------------------


def test_stop_finalizes_and_forgets_watch(agent, controller):
    wid = controller.handle(
        {
            "action": "start",
            "renderer_path": _write_renderer(agent._working_dir, _OK_BODY),
            "interval_s": 3600,
        }
    )["watch_id"]
    result = controller.handle({"action": "stop", "watch_id": wid})
    assert result["state"] == "stopped"
    assert wid not in controller._watches
    # A finalize (card=None) cleared only the programmable slot.
    last = agent._client.calls[-1][1]
    assert last["sub_action"] == "finalize"
    assert "card" not in last
    # The watch is gone; a second stop is a clean error, not a crash.
    assert controller.handle({"action": "stop", "watch_id": wid})["status"] == "error"


def test_failed_stop_is_truthful_retryable_and_retains_watch(agent, controller):
    """A failed programmable ``finalize`` must not report ``stopped`` or drop the
    watch — the resident may still show the frame, so ``stop`` stays retryable."""
    wid = controller.handle(
        {
            "action": "start",
            "renderer_path": _write_renderer(agent._working_dir, _OK_BODY),
            "interval_s": 3600,
        }
    )["watch_id"]
    watch = controller._watches[wid]

    # Backend rejects the finalize projection.
    agent._client.fail = True
    result = controller.handle({"action": "stop", "watch_id": wid})
    assert result["status"] == "error"
    assert result["state"] == "stop_failed"
    assert result["error"]["code"] == "stop_finalize_failed"
    assert result["error"]["retryable"] is True
    # The watch is retained so stop can be retried...
    assert wid in controller._watches
    # ...but the renderer thread is already stopped (not "watching" with a live thread).
    assert watch.thread is None or not watch.thread.is_alive()
    inspect = controller.handle({"action": "inspect", "watch_id": wid})
    assert inspect["state"] == "stop_failed"
    assert inspect["error"]["code"] == "stop_finalize_failed"

    # Retry only re-attempts finalization; on an accepted clear the watch is
    # removed and ``stopped`` is returned.
    agent._client.fail = False
    retry = controller.handle({"action": "stop", "watch_id": wid})
    assert retry["status"] == "ok"
    assert retry["state"] == "stopped"
    assert wid not in controller._watches
    last = agent._client.calls[-1][1]
    assert last["sub_action"] == "finalize"
    assert "card" not in last


def test_stop_with_in_flight_renderer_never_finalizes_while_alive(
    agent, controller, monkeypatch
):
    """A renderer still running past the join budget must not let ``stop``
    finalize/remove/report ``stopped``; the late frame must not project an
    ``update``; and ``inspect`` must stay ``stop_failed`` (never fall back to a
    non-error ``stopping``) until the thread is actually quiescent. Deterministic:
    the join budget is shrunk and the renderer blocks on an Event (no real wait)."""
    from lingtai.mcp_servers.telegram.task_card import controller as tc

    monkeypatch.setattr(tc, "_JOIN_TIMEOUT_S", 0.05)

    # Seed a started-like watch with an accepted first frame.
    watch = tc._Watch("tc_1", agent._working_dir / "r.py", 0.01, 1.0, "acct", 42)
    with watch.lock:
        watch.last_valid_frame = {"lines": ["ok"]}
        watch.last_valid_at = "2020-01-01T00:00:00+00:00"
    controller._watches["tc_1"] = watch

    entered = threading.Event()
    release = threading.Event()

    def _blocking_render(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)  # blocks well past the shrunk join budget
        return {"lines": ["LATE"]}

    monkeypatch.setattr(controller, "_run_renderer", _blocking_render)
    watch.thread = threading.Thread(
        target=controller._tick, args=(watch,), daemon=True
    )
    watch.thread.start()
    assert entered.wait(2)  # the renderer is now in-flight

    calls_before = len(agent._client.calls)
    result = controller.handle({"action": "stop", "watch_id": "tc_1"})
    # Truthful while the thread is alive: no finalize, no removal, no ``stopped``.
    assert result["status"] == "error"
    assert result["state"] == "stop_failed"
    assert result["error"]["code"] == "stop_thread_alive"
    assert result["error"]["retryable"] is True
    assert "tc_1" in controller._watches
    assert watch.thread.is_alive()
    assert not any(
        c[1]["sub_action"] == "finalize" for c in agent._client.calls[calls_before:]
    )
    assert (
        controller.handle({"action": "inspect", "watch_id": "tc_1"})["state"]
        == "stop_failed"
    )

    # Release the blocked renderer: it must NOT project a late ``update``, and
    # ``inspect`` must remain ``stop_failed`` (no stop_failed -> stopping regress).
    release.set()
    watch.thread.join(2)
    assert not watch.thread.is_alive()
    subs_after_stop = [c[1]["sub_action"] for c in agent._client.calls[calls_before:]]
    assert "update" not in subs_after_stop
    inspect_after = controller.handle({"action": "inspect", "watch_id": "tc_1"})
    assert inspect_after["state"] == "stop_failed"
    # The seeded last-valid frame/timestamp and the stop error code are preserved
    # verbatim after the dropped renderer returns (Contract's three explicit
    # post-render claims, not inferred from "no projection" alone).
    assert inspect_after["error"]["code"] == "stop_thread_alive"
    assert inspect_after["last_valid_frame"] == {"lines": ["ok"]}
    assert inspect_after["last_valid_frame_at"] == "2020-01-01T00:00:00+00:00"

    # A later stop retry now finds the thread quiescent, finalizes exactly once,
    # and removes the watch.
    retry = controller.handle({"action": "stop", "watch_id": "tc_1"})
    assert retry["status"] == "ok"
    assert retry["state"] == "stopped"
    assert "tc_1" not in controller._watches
    assert agent._client.calls[-1][1]["sub_action"] == "finalize"


def test_public_retry_after_failed_stop_continues_stop_only(
    agent, controller, monkeypatch
):
    """A public ``retry`` after a failed stop must continue the stop path only —
    never re-run the renderer or project a fresh ``update`` — and a later
    successful retry finalizes once and removes the watch."""
    from lingtai.mcp_servers.telegram.task_card import controller as tc

    # Quiescent watch (no thread) with an accepted frame, already in the failed
    # stop state via a rejected finalize.
    watch = tc._Watch("tc_1", agent._working_dir / "r.py", 3600, 1.0, "acct", 42)
    with watch.lock:
        watch.last_valid_frame = {"lines": ["ok"]}
    controller._watches["tc_1"] = watch

    agent._client.fail = True
    stop_result = controller.handle({"action": "stop", "watch_id": "tc_1"})
    assert stop_result["state"] == "stop_failed"
    assert stop_result["error"]["code"] == "stop_finalize_failed"
    assert "tc_1" in controller._watches

    ran = {"count": 0}

    def _forbidden_render(*_args, **_kwargs):
        ran["count"] += 1
        return {"lines": ["RESURRECTED"]}

    monkeypatch.setattr(controller, "_run_renderer", _forbidden_render)

    # Public ``retry`` while finalize still fails: renderer never runs, no
    # ``update`` is projected, only ``finalize`` is retried, watch retained.
    calls_before = len(agent._client.calls)
    retry_failed = controller.handle({"action": "retry", "watch_id": "tc_1"})
    assert ran["count"] == 0
    assert retry_failed["state"] == "stop_failed"
    subs = [c[1]["sub_action"] for c in agent._client.calls[calls_before:]]
    assert "update" not in subs
    assert subs == ["finalize"]
    assert "tc_1" in controller._watches

    # A later successful retry finalizes once and removes the watch — still no
    # renderer execution.
    agent._client.fail = False
    retry_ok = controller.handle({"action": "retry", "watch_id": "tc_1"})
    assert ran["count"] == 0
    assert retry_ok["state"] == "stopped"
    assert "tc_1" not in controller._watches
    assert agent._client.calls[-1][1]["sub_action"] == "finalize"


def test_late_update_after_stop_timeout_is_dropped_and_compensated(
    agent, controller, monkeypatch
):
    """Post-guard/in-flight-``update`` race: an ``update`` authorized just before
    stop blocks past the join budget (the reverse call has no total-time bound).
    Stop returns retained ``stop_failed``/``stop_thread_alive`` with OLD
    frame/timestamp preserved and no finalize while alive. When the update finally
    returns, its state mutation is dropped and the live watcher thread compensates
    by clearing the slot; ``inspect`` never regresses to ``stopping``/error-null/
    LATE, and a later retry removes the watch without rerunning the renderer or a
    duplicate reverse clear. Deterministic: shrunk join budget + Event-blocked
    projection (no multi-second success path)."""
    from lingtai.mcp_servers.telegram.task_card import controller as tc

    monkeypatch.setattr(tc, "_JOIN_TIMEOUT_S", 0.05)

    watch = tc._Watch("tc_1", agent._working_dir / "r.py", 0.01, 1.0, "acct", 42)
    with watch.lock:
        watch.last_valid_frame = {"lines": ["OLD"]}
        watch.last_valid_at = "2020-01-01T00:00:00+00:00"
    controller._watches["tc_1"] = watch

    monkeypatch.setattr(
        controller, "_run_renderer", lambda *_a, **_k: {"lines": ["LATE"]}
    )

    projected: list[str] = []
    update_entered = threading.Event()
    update_release = threading.Event()

    def _fake_project(_w, sub_action, _frame):
        projected.append(sub_action)
        if sub_action == "update":
            update_entered.set()
            assert update_release.wait(5)  # blocks AFTER the pre-projection guard
            return {"status": "ok"}  # the late update lands
        return {"status": "ok"}

    monkeypatch.setattr(controller, "_project", _fake_project)

    watch.thread = threading.Thread(target=controller._tick, args=(watch,), daemon=True)
    watch.thread.start()
    assert update_entered.wait(2)  # the update projection is in flight

    # Stop times out with the update in flight: retained stop_failed, no finalize.
    result = controller.handle({"action": "stop", "watch_id": "tc_1"})
    assert result["state"] == "stop_failed"
    assert result["error"]["code"] == "stop_thread_alive"
    assert "tc_1" in controller._watches
    assert watch.thread.is_alive()
    assert "finalize" not in projected
    before = controller.handle({"action": "inspect", "watch_id": "tc_1"})
    assert before["last_valid_frame"] == {"lines": ["OLD"]}
    assert before["last_valid_frame_at"] == "2020-01-01T00:00:00+00:00"
    assert before["error"]["code"] == "stop_thread_alive"

    # Release the late update: it lands, the tick drops its state mutation, and the
    # live thread compensates by finalizing (clearing the late frame).
    update_release.set()
    watch.thread.join(2)
    assert not watch.thread.is_alive()
    assert projected == ["update", "finalize"]  # exactly one compensating clear
    after = controller.handle({"action": "inspect", "watch_id": "tc_1"})
    assert after["state"] == "stop_failed"  # never stopping/error-null
    assert after["error"]["code"] == "stop_thread_alive"
    assert after["last_valid_frame"] == {"lines": ["OLD"]}  # never overwritten to LATE
    assert after["last_valid_frame_at"] == "2020-01-01T00:00:00+00:00"

    # A later retry removes the watch without rerunning the renderer or a second
    # reverse clear (the slot was already compensated).
    retry = controller.handle({"action": "stop", "watch_id": "tc_1"})
    assert retry["state"] == "stopped"
    assert "tc_1" not in controller._watches
    assert projected == ["update", "finalize"]  # no duplicate finalize on retry


def test_late_update_compensating_finalize_failure_is_retryable(
    agent, controller, monkeypatch
):
    """Same post-guard interleaving, but the compensating finalize fails/unknown:
    the watch stays a precise retryable ``stop_finalize_failed`` with OLD state
    preserved, and a later retry (clear now accepted) removes it truthfully
    without rerunning the renderer."""
    from lingtai.mcp_servers.telegram.task_card import controller as tc

    monkeypatch.setattr(tc, "_JOIN_TIMEOUT_S", 0.05)

    watch = tc._Watch("tc_1", agent._working_dir / "r.py", 0.01, 1.0, "acct", 42)
    with watch.lock:
        watch.last_valid_frame = {"lines": ["OLD"]}
        watch.last_valid_at = "2020-01-01T00:00:00+00:00"
    controller._watches["tc_1"] = watch

    ran = {"count": 0}

    def _render(*_a, **_k):
        ran["count"] += 1
        return {"lines": ["LATE"]}

    monkeypatch.setattr(controller, "_run_renderer", _render)

    projected: list[str] = []
    update_entered = threading.Event()
    update_release = threading.Event()
    finalize_fail = {"on": True}

    def _fake_project(_w, sub_action, _frame):
        projected.append(sub_action)
        if sub_action == "update":
            update_entered.set()
            assert update_release.wait(5)
            return {"status": "ok"}
        if sub_action == "finalize" and finalize_fail["on"]:
            return {"status": "error"}  # compensating clear rejected/unknown
        return {"status": "ok"}

    monkeypatch.setattr(controller, "_project", _fake_project)

    watch.thread = threading.Thread(target=controller._tick, args=(watch,), daemon=True)
    watch.thread.start()
    assert update_entered.wait(2)
    ran_after_render = ran["count"]

    assert (
        controller.handle({"action": "stop", "watch_id": "tc_1"})["state"]
        == "stop_failed"
    )

    # Release: the compensating finalize is rejected -> precise retryable state.
    update_release.set()
    watch.thread.join(2)
    assert not watch.thread.is_alive()
    failed = controller.handle({"action": "inspect", "watch_id": "tc_1"})
    assert failed["state"] == "stop_failed"
    assert failed["error"]["code"] == "stop_finalize_failed"
    assert failed["last_valid_frame"] == {"lines": ["OLD"]}
    assert failed["last_valid_frame_at"] == "2020-01-01T00:00:00+00:00"
    assert "tc_1" in controller._watches

    # A later retry (clear now accepted) finalizes and removes — no renderer rerun.
    finalize_fail["on"] = False
    retry = controller.handle({"action": "stop", "watch_id": "tc_1"})
    assert retry["state"] == "stopped"
    assert "tc_1" not in controller._watches
    assert ran["count"] == ran_after_render  # renderer never rerun after stop


def test_last_valid_frame_at_recorded_preserved_and_updated(agent, controller, monkeypatch):
    """``last_valid_frame_at`` is a real UTC ISO-8601 timestamp: set on the first
    accepted frame, unchanged across failures, and updated on recovery."""
    from datetime import datetime

    from lingtai.mcp_servers.telegram.task_card import controller as tc

    stamps = [
        "2020-01-01T00:00:00+00:00",
        "2020-01-01T00:00:05+00:00",
        "2020-01-01T00:00:09+00:00",
    ]
    box = {"i": 0}

    def _fake_now() -> str:
        value = stamps[min(box["i"], len(stamps) - 1)]
        box["i"] += 1
        return value

    monkeypatch.setattr(tc, "_utc_now_iso", _fake_now)

    renderer = agent._working_dir / "ts.py"
    renderer.write_text(_OK_BODY)
    wid = controller.handle(
        {"action": "start", "renderer_path": str(renderer), "interval_s": 3600}
    )["watch_id"]
    watch = controller._watches[wid]

    # Initial accepted frame stamped, and it is a real UTC ISO-8601 value.
    first = controller.handle({"action": "inspect", "watch_id": wid})["last_valid_frame_at"]
    assert first == "2020-01-01T00:00:00+00:00"
    assert datetime.fromisoformat(first).tzinfo is not None

    # A failed renderer attempt must NOT change it (and stamps nothing).
    renderer.write_text("import sys; sys.exit(1)")
    controller._tick(watch)
    after_fail = controller.handle({"action": "inspect", "watch_id": wid})
    assert after_fail["state"] == "error"
    assert after_fail["last_valid_frame_at"] == first

    # A recovered frame updates it to a strictly later stamp.
    renderer.write_text(_OK_BODY)
    controller._tick(watch)
    after_recovery = controller.handle({"action": "inspect", "watch_id": wid})
    assert after_recovery["state"] == "watching"
    assert after_recovery["last_valid_frame_at"] == "2020-01-01T00:00:05+00:00"

    controller.handle({"action": "stop", "watch_id": wid})
