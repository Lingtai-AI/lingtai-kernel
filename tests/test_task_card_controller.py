"""Unit tests for the public kernel ``task_card`` controller (Jason #7258/#7259).

Covers registration, exact-one schema-valid JSON, path containment, synchronous
initial errors (timeout/nonzero/invalid-frame), the async watch lifecycle,
inspect/retry/stop, and the deduped fail-loud LICC error/recovery wakes. No real
Telegram or network — the reverse channel is a fake MCP client.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from lingtai.kernel.base_agent import _TASK_CARD_TOOL
from lingtai.kernel.task_card_controller import (
    TaskCardController,
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
    assert glossary is None  # kernel tool: no lingtai.tools glossary package
    assert schema["properties"]["action"]["enum"] == [
        "start",
        "inspect",
        "retry",
        "stop",
    ]
    assert callable(handler)


def test_schema_requires_action():
    assert get_schema()["required"] == ["action"]


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
    from lingtai.kernel import task_card_controller as tc

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
