"""Stage 4 — a minimal live event bridge for ``NativeRuntimeSession``.

These tests assert that, once a session is started, the agent's *existing*
activity surfaces through the stage-0 ``RuntimeEvent`` contract on
``session.events()`` — **without** rewriting the kernel turn loop. The bridge
is installed by wrapping the agent's overridable hooks
(``_on_tool_result_hook``, ``_post_request``) and observing ``agent._state``,
so a real run needs no new kernel code path.

As with the earlier native-runtime tests, everything runs with no real model,
no API key, and no running agent process: a fake agent that *invokes* the
wrapped hooks (the way the real turn loop would) stands in for the wrapper
``Agent``.
"""
from __future__ import annotations

from pathlib import Path

from lingtai_sdk import native
from lingtai_sdk import runtime as rt


# --------------------------------------------------------------------------
# Fake Agent — like the stage-1 fake, but it also drives the hooks the bridge
# installs, mimicking what the kernel turn loop does during a real turn.
# --------------------------------------------------------------------------
class _HookAgent:
    """A fake agent whose hooks are bridge-wrappable instance attributes.

    The real ``BaseAgent`` defines ``_on_tool_result_hook`` / ``_post_request``
    as methods and ``_state`` as an attribute; the bridge wraps the bound
    methods on the instance. This fake mirrors that surface and lets a test
    *fire* the hooks to simulate turn activity.
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.working_dir = Path(kwargs["working_dir"])
        self.started = False
        self.stopped = False
        self.sent: list[tuple] = []
        self._state = "idle"
        self._usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "api_calls": 0,
        }

    # -- lifecycle the runtime drives --------------------------------------
    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True

    def send(self, content, sender: str = "user") -> None:
        self.sent.append((content, sender))

    # -- overridable hooks the bridge wraps (BaseAgent defaults) -----------
    def _on_tool_result_hook(self, tool_name, tool_args, result):
        return None

    def _post_request(self, msg, result) -> None:
        return None

    def get_token_usage(self) -> dict:
        return dict(self._usage)


def _factory(**kwargs):
    return _HookAgent(**kwargs)


def _active_session(tmp_path, **opts):
    rtm = native.NativeRuntime(agent_factory=_factory)
    session = rtm.create_session(rt.RuntimeOptions(working_dir=tmp_path, **opts))
    session.start()
    return session


# --------------------------------------------------------------------------
# Tool-call / tool-result bridging
# --------------------------------------------------------------------------
def test_tool_result_hook_emits_tool_call_and_tool_result(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    # The kernel turn loop calls this after a tool runs; the bridge wrapper
    # turns it into a TOOL_CALL + TOOL_RESULT pair.
    ret = agent._on_tool_result_hook("read_file", {"path": "a.txt"}, {"ok": True})
    # Bridge must not change the intercept contract: original returned None.
    assert ret is None
    calls = [e for e in session.events() if e.kind is rt.EventKind.TOOL_CALL]
    results = [e for e in session.events() if e.kind is rt.EventKind.TOOL_RESULT]
    assert len(calls) == 1 and len(results) == 1
    assert calls[0].data["name"] == "read_file"
    assert calls[0].data["args"] == {"path": "a.txt"}
    assert results[0].data["name"] == "read_file"
    assert results[0].data["result"] == {"ok": True}
    assert calls[0].source == "native"


def test_tool_result_hook_preserves_original_intercept(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    # If the underlying hook intercepts (returns a string), the bridge must
    # pass that through unchanged rather than swallowing it.
    agent._on_tool_result_hook = lambda *a, **k: "stop here"
    session._install_event_bridge(agent)  # re-wrap the replaced hook
    ret = agent._on_tool_result_hook("x", {}, {})
    assert ret == "stop here"


# --------------------------------------------------------------------------
# Response text / usage / error bridging via _post_request
# --------------------------------------------------------------------------
def test_post_request_emits_text_and_usage(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    agent._usage["total_tokens"] = 42
    agent._usage["api_calls"] = 1
    agent._post_request(object(), {"text": "hello world", "failed": False, "errors": []})
    texts = [e for e in session.events() if e.kind is rt.EventKind.TEXT]
    usages = [e for e in session.events() if e.kind is rt.EventKind.USAGE]
    assert len(texts) == 1
    assert texts[0].data["text"] == "hello world"
    assert len(usages) == 1
    assert usages[0].data["total_tokens"] == 42


def test_post_request_emits_errors(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    agent._post_request(
        object(), {"text": "", "failed": True, "errors": ["boom", "kaboom"]}
    )
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert [e.data["error"] for e in errs] == ["boom", "kaboom"]
    # Turn-level errors are non-fatal: the session keeps running.
    assert all(e.data["fatal"] is False for e in errs)
    assert session.state is rt.RuntimeState.ACTIVE


def test_post_request_empty_text_emits_no_text_event(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    agent._post_request(object(), {"text": "", "failed": False, "errors": []})
    texts = [e for e in session.events() if e.kind is rt.EventKind.TEXT]
    assert texts == []


# --------------------------------------------------------------------------
# Agent state -> RuntimeState bridging (observed on events() reads)
# --------------------------------------------------------------------------
def test_agent_state_change_surfaces_as_state_event(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    # The agent runs its own loop thread; the bridge samples agent._state when
    # events() is read and emits a STATE event when it changed.
    agent._state = "active"
    list(session.events())  # sample once
    agent._state = "asleep"
    states = [e for e in session.events() if e.kind is rt.EventKind.STATE]
    values = [e.data["state"] for e in states]
    # Both the runtime's own ACTIVE transition and the sampled agent states.
    assert "active" in values
    assert "asleep" in values


def test_agent_state_maps_through_runtime_taxonomy(tmp_path):
    """A sampled agent state is mapped onto a ``RuntimeState`` *value*.

    The contract (``runtime.py``) says ``STATE`` event ``data['state']`` is a
    ``RuntimeState`` value, so the native session maps the agent's own life-state
    rather than passing an arbitrary string through. Each of the wrapper
    ``AgentState`` values has a same-named ``RuntimeState``.
    """
    session = _active_session(tmp_path)
    agent = session.agent
    for raw in ("idle", "stuck", "asleep", "suspended"):
        agent._state = raw
        states = [
            e.data["state"]
            for e in session.events()
            if e.kind is rt.EventKind.STATE
        ]
        assert raw in states
        # Every emitted state value is a valid RuntimeState value.
        assert all(v in {s.value for s in rt.RuntimeState} for v in states)


def test_unknown_agent_state_maps_to_stuck_not_leaked(tmp_path):
    """An unrecognized agent life-state must not leak as a raw STATE value.

    The contract guarantees ``data['state']`` is always a ``RuntimeState`` value.
    A backend that grows a new/unknown life-state string must be coerced into the
    taxonomy — ``STUCK`` is the abnormal/unknown bucket — rather than surfacing an
    out-of-taxonomy string to SDK consumers.
    """
    session = _active_session(tmp_path)
    agent = session.agent
    agent._state = "frobnicating"  # not a RuntimeState value
    values = [
        e.data["state"]
        for e in session.events()
        if e.kind is rt.EventKind.STATE
    ]
    assert "frobnicating" not in values
    assert rt.RuntimeState.STUCK.value in values
    assert all(v in {s.value for s in rt.RuntimeState} for v in values)


def test_agent_state_no_duplicate_state_events(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    agent._state = "active"
    session.events()
    session.events()  # second read with no change must not emit again
    states = [
        e
        for e in session.events()
        if e.kind is rt.EventKind.STATE and e.data["state"] == "active"
    ]
    # Exactly one "active" STATE event from the agent sample (the runtime's own
    # activation event carries the RuntimeState value, also "active" — so allow
    # the runtime one plus a single sampled one, but no growth on re-read).
    assert len(states) <= 2


# --------------------------------------------------------------------------
# Opt-out: a host can disable the bridge to get the stage-1 snapshot behavior.
# --------------------------------------------------------------------------
def test_bridge_can_be_disabled(tmp_path):
    rtm = native.NativeRuntime(agent_factory=_factory, bridge_events=False)
    session = rtm.create_session(rt.RuntimeOptions(working_dir=tmp_path))
    session.start()
    agent = session.agent
    agent._on_tool_result_hook("x", {}, {})
    agent._post_request(object(), {"text": "hi", "failed": False, "errors": []})
    assert [e for e in session.events() if e.kind is rt.EventKind.TOOL_CALL] == []
    assert [e for e in session.events() if e.kind is rt.EventKind.TEXT] == []


def test_bridge_is_on_by_default(tmp_path):
    # No explicit bridge_events kwarg → the bridge is installed.
    session = _active_session(tmp_path)
    session.agent._on_tool_result_hook("x", {"a": 1}, {"ok": True})
    assert [e for e in session.events() if e.kind is rt.EventKind.TOOL_CALL]


# --------------------------------------------------------------------------
# Graceful degradation: a slimmer agent missing a hook/accessor must not
# break the bridge — only the observable surface that exists is bridged.
# --------------------------------------------------------------------------
def test_bridge_tolerates_agent_without_usage_accessor(tmp_path):
    class _NoUsageAgent(_HookAgent):
        get_token_usage = None  # not callable → skipped, no crash

    def factory(**kwargs):
        return _NoUsageAgent(**kwargs)

    rtm = native.NativeRuntime(agent_factory=factory)
    session = rtm.create_session(rt.RuntimeOptions(working_dir=tmp_path))
    session.start()
    session.agent._post_request(
        object(), {"text": "hi", "failed": False, "errors": []}
    )
    assert [e for e in session.events() if e.kind is rt.EventKind.TEXT]
    assert [e for e in session.events() if e.kind is rt.EventKind.USAGE] == []


def test_bridge_wraps_real_baseagent_hook_contract(tmp_path):
    """Pin the bridge to the *real* ``BaseAgent`` hook defaults.

    The bridge wraps ``_on_tool_result_hook`` / ``_post_request`` by replacing
    them as instance attributes (shadowing the bound methods). This test borrows
    the genuine kernel hook implementations — not the fake's — so the bridge's
    assumptions about their signatures and return contracts can't silently rot
    if the kernel changes them. We avoid booting a real agent by binding the
    unbound kernel hooks onto a lightweight object the bridge can wrap.
    """
    from lingtai.kernel.base_agent import BaseAgent

    class _Stub:
        # Borrow the genuine kernel hook implementations.
        _on_tool_result_hook = BaseAgent._on_tool_result_hook
        _post_request = BaseAgent._post_request

        def get_token_usage(self):
            return {"total_tokens": 5}

    rtm = native.NativeRuntime(agent_factory=lambda **kw: _Stub())
    session = rtm.create_session(rt.RuntimeOptions(working_dir=tmp_path))
    # Drive start() through a stub that mimics the agent lifecycle the runtime
    # expects; the stub above lacks start()/stop(), so install the bridge and
    # call the hooks directly instead.
    stub = _Stub()
    session._agent = stub
    session._install_event_bridge(stub)

    # Real BaseAgent._on_tool_result_hook returns None (no intercept).
    assert stub._on_tool_result_hook("read_file", {"p": 1}, {"ok": True}) is None
    # Real BaseAgent._post_request returns None and does no I/O on a stub.
    assert stub._post_request(object(), {"text": "hi", "failed": False, "errors": []}) is None

    kinds = {e.kind for e in session.events()}
    assert rt.EventKind.TOOL_CALL in kinds
    assert rt.EventKind.TOOL_RESULT in kinds
    assert rt.EventKind.TEXT in kinds
    assert rt.EventKind.USAGE in kinds


def test_bridged_events_carry_native_source(tmp_path):
    session = _active_session(tmp_path)
    agent = session.agent
    agent._on_tool_result_hook("read", {}, {})
    agent._post_request(object(), {"text": "ok", "failed": False, "errors": []})
    bridged = [
        e
        for e in session.events()
        if e.kind
        in (
            rt.EventKind.TOOL_CALL,
            rt.EventKind.TOOL_RESULT,
            rt.EventKind.TEXT,
            rt.EventKind.USAGE,
        )
    ]
    assert bridged and all(e.source == "native" for e in bridged)
