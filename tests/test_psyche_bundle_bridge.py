"""Stage-3J wrapper bridge: host the *real* ``psyche`` identity/context tool through
the SDK bundle.

Where ``tests/test_sdk_psyche_tools.py`` proves the SDK-side declaration + host seam
with dummy handlers (and import purity), this test proves the *wrapper* half —
``lingtai.core.psyche_bundle`` — that injects the genuine kernel intrinsic
``psyche.handle`` into the SDK ``psyche`` identity bundle and so runs the real
behavior through the declared manifest.

The key assertion is **parity**: invoking ``psyche`` through the bundle host returns
exactly what the kernel intrinsic returns, because the bridge wires the *same*
``intrinsics.psyche.handle`` the live ``BaseAgent._wire_intrinsics`` path dispatches,
bound to the same agent.

**Safety:** every action exercised here is non-destructive — an unknown-object
error, an invalid-action error, the pure ``pad.load`` / ``lingtai.load`` reads, and
an empty-content ``name.set`` *rejected before any mutation*. Nothing here molts
(sheds conversation context), sets an immutable true name, or writes identity/pad
content.
"""
from __future__ import annotations

import os

from unittest.mock import MagicMock

import pytest

from lingtai_kernel.base_agent import BaseAgent
from lingtai_kernel.intrinsics import psyche as psyintr
from lingtai.core import psyche_bundle


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


# --- the bridge builds the native host for the real psyche tool ------------


def test_bridge_builds_native_psyche_host(agent):
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    # one tool, native-authority host, privileged native transport, destructive posture.
    assert host.tools == ("psyche",)
    assert host.manifest.name == "psyche"
    assert host.manifest.roles.privileged is True
    assert host.manifest.transport.kind == "native"
    assert host.manifest.security.danger == "destructive"


def test_bridge_builds_hosts_mapping(agent):
    hosts = psyche_bundle.psyche_identity_bundle_hosts(agent)
    assert set(hosts) == {"psyche"}
    assert hosts["psyche"].tools == ("psyche",)


# --- parity: the bundle path runs the real intrinsic, byte-identical -------


def test_bridge_unknown_object_error_parity(agent):
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    via_bundle = host.invoke("psyche", object="bogus", action="diff")
    via_intrinsic = psyintr.handle(agent, {"object": "bogus", "action": "diff"})
    assert via_bundle == via_intrinsic
    assert "Unknown object" in via_bundle["error"]


def test_bridge_invalid_action_error_parity(agent):
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    via_bundle = host.invoke("psyche", object="lingtai", action="submit")
    via_intrinsic = psyintr.handle(agent, {"object": "lingtai", "action": "submit"})
    assert via_bundle == via_intrinsic
    assert "Invalid action" in via_bundle["error"]


def test_bridge_pad_load_parity(agent):
    """The pure ``pad.load`` read — no write — matches the intrinsic."""
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    via_bundle = host.invoke("psyche", object="pad", action="load")
    via_intrinsic = psyintr.handle(agent, {"object": "pad", "action": "load"})
    assert via_bundle == via_intrinsic


def test_bridge_lingtai_load_parity(agent):
    """The pure ``lingtai.load`` read — recompose ``character`` — matches."""
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    via_bundle = host.invoke("psyche", object="lingtai", action="load")
    via_intrinsic = psyintr.handle(agent, {"object": "lingtai", "action": "load"})
    assert via_bundle == via_intrinsic


def test_bridge_name_set_empty_rejected_before_mutation_parity(agent):
    """An empty-content ``name.set`` is refused before any mutation — through the
    bridge too. This exercises the irreversible (DESTRUCTIVE-graded) ``name.set``
    pair on its *error* path, so no immutable name is ever written."""
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    via_bundle = host.invoke("psyche", object="name", action="set", content="   ")
    via_intrinsic = psyintr.handle(agent, {"object": "name", "action": "set", "content": "   "})
    assert via_bundle == via_intrinsic
    assert "error" in via_bundle


# --- the bridge wires the SAME function the live path dispatches ------------


def test_bridge_uses_the_same_intrinsic_the_live_path_wires(agent):
    """The agent's live ``psyche`` intrinsic and the bridge wire the same behavior.

    Both go through ``intrinsics.psyche.handle`` — one source of truth — so the
    bundle host cannot drift from the registered intrinsic. The live
    ``_wire_intrinsics`` registration is untouched (``psyche`` is still present).
    """
    assert "psyche" in agent._intrinsics  # live path unchanged

    live = agent._intrinsics["psyche"]({"object": "pad", "action": "load"})
    host = psyche_bundle.psyche_identity_bundle_host(agent)
    bundled = host.invoke("psyche", object="pad", action="load")
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
        "import lingtai.core.psyche_bundle as pb\n"
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
