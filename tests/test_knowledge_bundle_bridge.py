"""Stage-3G wrapper bridge: host the *real* ``knowledge`` tool through the SDK
catalog bundle.

Where ``tests/test_sdk_knowledge_tools.py`` proves the SDK-side declaration + host
seam with a dummy handler (and import purity), this test proves the *wrapper* half
— ``lingtai.core.knowledge_bundle`` — that injects the genuine wrapper
``knowledge.make_handler(agent)`` into the SDK bundle and so runs the real behavior
through the declared manifest.

The key assertion is **parity**: invoking ``knowledge`` through the bundle host
returns exactly what the live path returns, because the bridge wires the *same*
source of truth (``knowledge.make_handler`` the live ``knowledge.setup()``
registers), bound to the same agent.

**Safety:** every action exercised here is contained in temp fixtures. ``info``
usually re-scans ``knowledge/`` and re-renders the prompt section; when legacy JSON
stores exist, the shared live handler may also perform the pre-existing one-time
migration. Unknown actions error before any work.
"""
from __future__ import annotations

import os

from unittest.mock import MagicMock

import pytest

from lingtai.kernel.base_agent import BaseAgent
from lingtai.core import knowledge as knowledgemod
from lingtai.core import knowledge_bundle
from lingtai_sdk import knowledge_tools as kt


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


def _write_knowledge_fixture(working_dir):
    """Create a one-entry knowledge catalog as a test fixture.

    ``_reconcile`` scans ``<agent>/knowledge/<name>/KNOWLEDGE.md``; one valid entry
    must exist for a non-empty catalog snapshot.
    """
    entry = working_dir / "knowledge" / "example"
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "KNOWLEDGE.md").write_text(
        "---\nname: example\ndescription: a fixture knowledge entry\n---\n\nBody.\n",
        encoding="utf-8",
    )


@pytest.fixture
def agent(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir(parents=True, exist_ok=True)
    _write_knowledge_fixture(wd)
    a = BaseAgent(service=make_mock_service(), agent_name="test", working_dir=wd)
    try:
        yield a
    finally:
        a.stop(timeout=1.0)


# --- the bridge builds the right host ---------------------------------------


def test_knowledge_bridge_builds_in_process_host(agent):
    host = knowledge_bundle.knowledge_catalog_bundle_host(agent)
    assert host.tools == ("knowledge",)
    assert host.manifest.name == "knowledge"
    assert host.manifest.roles.privileged is False
    assert host.manifest.transport.kind == "in_process"
    assert host.manifest.security.danger == "caution"


def test_bridge_builds_hosts_mapping(agent):
    hosts = knowledge_bundle.knowledge_catalog_bundle_hosts(agent)
    assert set(hosts) == {"knowledge"}
    assert hosts["knowledge"].tools == ("knowledge",)


def _schema_actions(schema: dict) -> set[str]:
    return set(schema["properties"]["action"]["enum"])


# --- drift guard: SDK declared action set == live schema action enum ---------


def test_knowledge_manifest_actions_match_live_schema():
    """Pin the SDK knowledge declaration to the live wrapper schema action enum."""
    declared = set(kt.knowledge_catalog_manifest().metadata["actions"])
    live = _schema_actions(knowledgemod.get_schema())
    assert declared == live == {"info"}


def test_knowledge_manifest_schema_mirrors_live_get_schema():
    """The declared SDK schema mirrors the *full* live ``get_schema`` shape.

    Beyond the action enum, the bundled schema's property keys and ``required``
    list must track the live wrapper, so an SDK declaration cannot silently drift
    from the live core wrapper when the wrapper grows or renames a property.
    (Descriptions are i18n'd live and intentionally not pinned.)
    """
    declared = kt.knowledge_catalog_manifest().metadata["schema"]
    live = knowledgemod.get_schema()
    assert set(declared["properties"]) == set(live["properties"])
    assert declared["required"] == live["required"] == ["action"]


# --- knowledge parity: the bundle path runs the real handler, byte-identical --


def test_knowledge_info_parity(agent):
    """The read-only ``info`` matches the live handler, byte-identically.

    Both go through ``knowledge.make_handler`` → ``_reconcile`` against the same
    fixture catalog — no write.
    """
    host = knowledge_bundle.knowledge_catalog_bundle_host(agent)
    via_bundle = host.invoke("knowledge", action="info")
    via_live = knowledgemod.make_handler(agent)({"action": "info"})
    assert via_bundle == via_live
    assert via_bundle["status"] == "ok"
    assert via_bundle["catalog_size"] == 1


def test_knowledge_unknown_action_error_parity(agent):
    host = knowledge_bundle.knowledge_catalog_bundle_host(agent)
    via_bundle = host.invoke("knowledge", action="does-not-exist")
    via_live = knowledgemod.make_handler(agent)({"action": "does-not-exist"})
    assert via_bundle == via_live
    assert via_bundle["status"] == "error"
    assert "unknown action" in via_bundle["message"]


def test_knowledge_make_handler_is_setup_single_source(agent):
    """``setup()`` and the bridge build the handler through the same factory.

    ``knowledge.setup()`` registers ``make_handler(agent)`` via ``add_tool``, and
    the bridge hosts a handler from the *same* ``make_handler``, so the bundle host
    cannot drift from the registered tool.
    """
    knowledgemod.setup(agent)
    assert "knowledge" in agent._tool_handlers
    setup_info = agent._tool_handlers["knowledge"]({"action": "info"})
    host = knowledge_bundle.knowledge_catalog_bundle_host(agent)
    bundle_info = host.invoke("knowledge", action="info")
    assert bundle_info == setup_info


# --- the bridge does not eagerly import the SDK at wrapper module load --------


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
        "import lingtai.core.knowledge_bundle as kb\n"
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
