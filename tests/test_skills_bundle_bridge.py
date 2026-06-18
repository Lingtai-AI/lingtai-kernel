"""Stage-3G wrapper bridge: host the *real* ``skills`` tool through the SDK
catalog bundle.

Where ``tests/test_sdk_skill_tools.py`` proves the SDK-side declaration + host
seam with a dummy handler (and import purity), this test proves the *wrapper* half
— ``lingtai.core.skills_bundle`` — that injects the genuine wrapper
``skills.make_handler(agent, paths)`` into the SDK bundle and so runs the real
behavior through the declared manifest.

The key assertion is **parity**: invoking ``skills`` through the bundle host
returns exactly what the live path returns, because the bridge wires the *same*
source of truth (``skills.make_handler`` the live ``skills.setup()`` registers),
bound to the same agent and the same Tier-1 ``paths``.

**Safety:** every action exercised here is side-effect-free — ``info`` (read-only
catalog view: re-scan ``.library/`` + paths and re-render the prompt section, no
write) and an unknown action (errors before any work). The only filesystem writes
are the temp fixture setup below.
"""
from __future__ import annotations

import os

from unittest.mock import MagicMock

import pytest

from lingtai_kernel.base_agent import BaseAgent
from lingtai.core import skills as skillsmod
from lingtai.core import skills_bundle
from lingtai_sdk import skill_tools as st


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


def _write_skills_fixture(working_dir):
    """Create the skills manual + one intrinsic skill as a test fixture.

    ``_reconcile`` reads the skills manual from
    ``.library/intrinsic/capabilities/skills/SKILL.md`` (its presence is the
    non-degraded health signal) and scans ``.library/`` for skill entries.
    """
    skills_cap = (
        working_dir / ".library" / "intrinsic" / "capabilities" / "skills"
    )
    skills_cap.mkdir(parents=True, exist_ok=True)
    (skills_cap / "SKILL.md").write_text(
        "---\nname: skills\ndescription: the skills manual\n---\n\nManual body.\n",
        encoding="utf-8",
    )


@pytest.fixture
def agent(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir(parents=True, exist_ok=True)
    _write_skills_fixture(wd)
    a = BaseAgent(service=make_mock_service(), agent_name="test", working_dir=wd)
    try:
        yield a
    finally:
        a.stop(timeout=1.0)


# --- the bridge builds the right host ---------------------------------------


def test_skills_bridge_builds_in_process_host(agent):
    host = skills_bundle.skills_catalog_bundle_host(agent)
    assert host.tools == ("skills",)
    assert host.manifest.name == "skills"
    assert host.manifest.roles.privileged is False
    assert host.manifest.transport.kind == "in_process"
    assert host.manifest.security.danger == "safe"


def test_bridge_builds_hosts_mapping(agent):
    hosts = skills_bundle.skills_catalog_bundle_hosts(agent)
    assert set(hosts) == {"skills"}
    assert hosts["skills"].tools == ("skills",)


def _schema_actions(schema: dict) -> set[str]:
    return set(schema["properties"]["action"]["enum"])


# --- drift guard: SDK declared action set == live schema action enum ---------


def test_skills_manifest_actions_match_live_schema():
    """Pin the SDK skills declaration to the live wrapper schema action enum."""
    declared = set(st.skills_catalog_manifest().metadata["actions"])
    live = _schema_actions(skillsmod.get_schema())
    assert declared == live == {"info"}


# --- skills parity: the bundle path runs the real handler, byte-identical -----


def test_skills_info_parity(agent):
    """The read-only ``info`` matches the live handler, byte-identically.

    Both go through ``skills.make_handler`` → ``_reconcile`` against the same
    fixture ``.library/`` — no write.
    """
    host = skills_bundle.skills_catalog_bundle_host(agent)
    via_bundle = host.invoke("skills", action="info")
    via_live = skillsmod.make_handler(agent)({"action": "info"})
    assert via_bundle == via_live
    assert via_bundle["status"] == "ok"
    # the fixture manual is present (non-degraded) and the one skill shows up.
    assert via_bundle["catalog_size"] == 1
    assert via_bundle["skills_manual"]


def test_skills_info_parity_with_tier1_paths(agent, tmp_path):
    """Threading Tier-1 ``paths`` to the bridge matches the live handler built with
    the same paths — the bridge passes ``paths`` through to ``make_handler``."""
    extra = tmp_path / "extra-skills" / "extra"
    extra.mkdir(parents=True, exist_ok=True)
    (extra / "SKILL.md").write_text(
        "---\nname: extra\ndescription: an extra-path skill\n---\n\nBody.\n",
        encoding="utf-8",
    )
    paths = [str(tmp_path / "extra-skills")]
    host = skills_bundle.skills_catalog_bundle_host(agent, paths)
    via_bundle = host.invoke("skills", action="info")
    via_live = skillsmod.make_handler(agent, paths)({"action": "info"})
    assert via_bundle == via_live
    # the intrinsic skill + the extra-path skill are both catalogued.
    assert via_bundle["catalog_size"] == 2
    assert paths[0] in via_bundle["paths"]


def test_skills_unknown_action_error_parity(agent):
    host = skills_bundle.skills_catalog_bundle_host(agent)
    via_bundle = host.invoke("skills", action="does-not-exist")
    via_live = skillsmod.make_handler(agent)({"action": "does-not-exist"})
    assert via_bundle == via_live
    assert via_bundle["status"] == "error"
    assert "unknown action" in via_bundle["message"]


def test_skills_make_handler_is_setup_single_source(agent):
    """``setup()`` and the bridge build the handler through the same factory.

    ``skills.setup()`` registers ``make_handler(agent, paths)`` via ``add_tool``,
    and the bridge hosts a handler from the *same* ``make_handler``, so the bundle
    host cannot drift from the registered tool.
    """
    skillsmod.setup(agent)
    assert "skills" in agent._tool_handlers
    setup_info = agent._tool_handlers["skills"]({"action": "info"})
    host = skills_bundle.skills_catalog_bundle_host(agent)
    bundle_info = host.invoke("skills", action="info")
    assert bundle_info == setup_info


# --- the bridge does not eagerly import the SDK at wrapper module load --------


def test_bridge_does_not_import_sdk_at_wrapper_module_load():
    """Importing the wrapper bridge module must not eagerly import the SDK."""
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "src"
    code = (
        "import sys\n"
        "import lingtai.core.skills_bundle as sb\n"
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
