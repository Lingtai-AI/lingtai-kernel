"""Stage-3J wrapper bridge: host the *real* ``soul`` inner-voice tool through the SDK
bundle.

Where ``tests/test_sdk_soul_tools.py`` proves the SDK-side declaration + host seam
with dummy handlers (and import purity), this test proves the *wrapper* half —
``lingtai.core.soul_bundle`` — that injects the genuine kernel intrinsic
``soul.handle`` into the SDK ``soul`` inner-voice bundle and so runs the real
behavior through the declared manifest.

The key assertion is **parity**: invoking ``soul`` through the bundle host returns
exactly what the kernel intrinsic returns, because the bridge wires the *same*
``intrinsics.soul.handle`` the live ``BaseAgent._wire_intrinsics`` path dispatches,
bound to the same agent.

**Safety:** no real LLM consultation runs and no lingering thread is spawned. The
exercised paths are: an unknown-action error, an inquiry with ``soul_inquiry``
monkeypatched to the silent ``None`` path (no LLM, no persisted entry), the
``dismiss`` notification-clear (no LLM), and a ``flow`` call *rejected* because the
fire lock is held (returns immediately, spawns no thread). The real ``flow`` fire
(``_run_consultation_fire``) is never invoked.
"""
from __future__ import annotations

import os

from unittest.mock import MagicMock

import pytest

from lingtai_kernel.base_agent import BaseAgent
from lingtai_kernel.intrinsics import soul as soulintr
from lingtai.core import soul_bundle


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


@pytest.fixture
def agent(tmp_path):
    wd = tmp_path / "wd"
    a = BaseAgent(service=make_mock_service(), agent_name="test", working_dir=wd)
    try:
        yield a
    finally:
        a.stop(timeout=1.0)


# --- the bridge builds the native host for the real soul tool --------------


def test_bridge_builds_native_soul_host(agent):
    host = soul_bundle.soul_voice_bundle_host(agent)
    # one tool, native-authority host, privileged native transport, caution posture.
    assert host.tools == ("soul",)
    assert host.manifest.name == "soul"
    assert host.manifest.roles.privileged is True
    assert host.manifest.transport.kind == "native"
    assert host.manifest.security.danger == "caution"


def test_bridge_builds_hosts_mapping(agent):
    hosts = soul_bundle.soul_voice_bundle_hosts(agent)
    assert set(hosts) == {"soul"}
    assert hosts["soul"].tools == ("soul",)


# --- parity: the bundle path runs the real intrinsic, byte-identical -------


def test_bridge_unknown_action_error_parity(agent):
    host = soul_bundle.soul_voice_bundle_host(agent)
    via_bundle = host.invoke("soul", action="on")
    via_intrinsic = soulintr.handle(agent, {"action": "on"})
    assert via_bundle == via_intrinsic
    assert "error" in via_bundle


def test_bridge_inquiry_silent_parity(agent, monkeypatch):
    """An inquiry whose mirror-session yields no voice returns the silent ok — no
    LLM, no persisted entry. Monkeypatching ``soul_inquiry`` to ``None`` exercises
    the real dispatch path without any LLM cost."""
    monkeypatch.setattr(soulintr, "soul_inquiry", lambda agent, q: None)
    host = soul_bundle.soul_voice_bundle_host(agent)
    via_bundle = host.invoke("soul", action="inquiry", inquiry="What am I missing?")
    via_intrinsic = soulintr.handle(agent, {"action": "inquiry", "inquiry": "What am I missing?"})
    assert via_bundle == via_intrinsic
    assert via_bundle == {"status": "ok", "voice": "(silence)"}


def test_bridge_inquiry_requires_text_parity(agent):
    host = soul_bundle.soul_voice_bundle_host(agent)
    via_bundle = host.invoke("soul", action="inquiry", inquiry="   ")
    via_intrinsic = soulintr.handle(agent, {"action": "inquiry", "inquiry": "   "})
    assert via_bundle == via_intrinsic
    assert "error" in via_bundle


def test_bridge_dismiss_parity(agent):
    """``dismiss`` clears the soul notification surface (no LLM). With no live soul
    notification, both paths return the same idempotent result."""
    host = soul_bundle.soul_voice_bundle_host(agent)
    via_bundle = host.invoke("soul", action="dismiss")
    via_intrinsic = soulintr.handle(agent, {"action": "dismiss"})
    assert via_bundle == via_intrinsic


def test_bridge_flow_rejected_when_fire_in_flight_parity(agent):
    """A voluntary ``flow`` is refused while the fire lock is held — through the
    bridge too. This returns immediately and spawns NO consultation thread, so the
    real fire never runs."""
    agent._soul_fire_lock.acquire()
    try:
        host = soul_bundle.soul_voice_bundle_host(agent)
        via_bundle = host.invoke("soul", action="flow")
        via_intrinsic = soulintr.handle(agent, {"action": "flow"})
        assert via_bundle == via_intrinsic
        assert "error" in via_bundle
        assert "ongoing" in via_bundle["error"]
    finally:
        agent._soul_fire_lock.release()


# --- the bridge wires the SAME function the live path dispatches ------------


def test_bridge_uses_the_same_intrinsic_the_live_path_wires(agent):
    """The agent's live ``soul`` intrinsic and the bridge wire the same behavior.

    Both go through ``intrinsics.soul.handle`` — one source of truth — so the
    bundle host cannot drift from the registered intrinsic. The live
    ``_wire_intrinsics`` registration is untouched (``soul`` is still present).
    """
    assert "soul" in agent._intrinsics  # live path unchanged

    live = agent._intrinsics["soul"]({"action": "dismiss"})
    host = soul_bundle.soul_voice_bundle_host(agent)
    bundled = host.invoke("soul", action="dismiss")
    assert bundled == live


def test_bridge_does_not_import_sdk_at_wrapper_module_load():
    """Importing the wrapper bridge module must not eagerly import the SDK.

    The SDK is imported lazily inside the bridge functions (wrapper -> sdk edge),
    so a bare import of the bridge module leaves ``lingtai_sdk`` unloaded until a
    host is actually built.
    """
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "src"
    code = (
        "import sys\n"
        "import lingtai.core.soul_bundle as sb\n"
        "assert 'lingtai_sdk' not in sys.modules, "
        "'bridge import eagerly pulled the SDK'\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env={**os.environ, "PYTHONPATH": str(src)},
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
