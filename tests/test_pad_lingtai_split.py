"""Pad/LingTai internal composition after the psyche migration.

The public two-root split this file originally pinned is gone: ``pad`` and
``lingtai`` are no longer model-facing roots, and the one public root is
``psyche`` (see ``tests/test_psyche_family.py`` for the public inventory,
schema, dispatch, and retirement evidence). What remains here is the part that
did NOT move — the two packages' private canonical composers, their durable
sources, and the one post-molt reconstruction hook that recomposes both.
"""
from __future__ import annotations

import pytest

from lingtai.agent import Agent
from lingtai.tools import context as context_tool
from lingtai.tools import lingtai as lingtai_tool
from lingtai.tools import pad as pad_tool
from tests._service_helpers import make_gemini_mock_service as make_mock_service


def _agent(tmp_path, **kwargs):
    return Agent(
        service=make_mock_service(), agent_name="test",
        working_dir=tmp_path / "test", **kwargs,
    )


def test_private_composers_remain_the_packages_own_exports():
    """The composers stayed put; only the public roots were retired."""
    assert callable(pad_tool._pad_load)
    assert callable(lingtai_tool._lingtai_load)
    # They never migrated into the context department.
    assert not hasattr(context_tool, "_pad_load")
    assert not hasattr(context_tool, "_lingtai_load")


def test_neither_package_registers_a_public_root():
    from lingtai.tools.registry import BUILTIN_TOOLS, INTRINSICS

    for name in ("pad", "lingtai"):
        assert name not in INTRINSICS
        assert name not in BUILTIN_TOOLS


def test_context_inventory_still_carries_no_pad_or_lingtai_aliases():
    assert context_tool.ACTION_ORDER == (
        "molt", "summarize", "rebuild", "settings", "manual",
    )
    actions = context_tool.get_schema()["properties"]["action"]["enum"]
    for retired in (
        "pad_edit", "pad_load", "pad_append", "lingtai_update", "lingtai_load",
    ):
        assert retired not in actions


def test_allowlist_blacklist_and_glossary_boundaries():
    from lingtai.kernel.tool_result_summary import _LTP_V2_MIGRATED_FAMILIES
    from lingtai.tools.daemon import EMANATION_BLACKLIST
    from lingtai.tools.glossary_validator import validate_package

    # The public boundary follows the surviving public root.
    assert "psyche" in _LTP_V2_MIGRATED_FAMILIES
    assert "psyche" in EMANATION_BLACKLIST
    assert "context" in _LTP_V2_MIGRATED_FAMILIES
    assert "context" in EMANATION_BLACKLIST
    # Retired roots are not recognized anywhere as public families.
    for retired in ("pad", "lingtai"):
        assert retired not in _LTP_V2_MIGRATED_FAMILIES
    # Glossary ownership follows the shipped package, which still exists.
    for package in ("pad", "lingtai", "psyche"):
        assert validate_package(package) == []


@pytest.mark.parametrize(
    "source,section,body",
    [("pad.md", "pad", "pad body"), ("lingtai.md", "character", "who I am")],
)
def test_each_composer_writes_only_its_own_section(tmp_path, source, section, body):
    agent = _agent(tmp_path)
    try:
        system = agent._working_dir / "system"
        system.mkdir(exist_ok=True)
        (system / source).write_text(body, encoding="utf-8")
        agent._prompt_manager.delete_section(section)

        composer = pad_tool._pad_load if section == "pad" else lingtai_tool._lingtai_load
        composer(agent, {})

        assert body in agent._prompt_manager.read_section(section)
    finally:
        agent.stop(timeout=1.0)


def test_one_post_molt_hook_reconstructs_both_internal_sections(tmp_path):
    agent = _agent(tmp_path)
    try:
        system = agent._working_dir / "system"
        system.mkdir(exist_ok=True)
        (system / "pad.md").write_text("pad body", encoding="utf-8")
        (system / "lingtai.md").write_text("who I am", encoding="utf-8")
        agent._prompt_manager.delete_section("pad")
        agent._prompt_manager.delete_section("character")
        assert agent._post_molt_hooks == [agent._reconstruct_context]
        agent._post_molt_hooks[0]()
        assert "pad body" in agent._prompt_manager.read_section("pad")
        assert "who I am" in agent._prompt_manager.read_section("character")
    finally:
        agent.stop(timeout=1.0)


def test_boot_runs_both_composers_without_a_public_root(tmp_path):
    """``psyche.boot`` carries the composition the retired roots used to."""
    agent = _agent(tmp_path, pad="seeded pad")
    try:
        assert agent._prompt_manager.read_section("pad") == "seeded pad"
        # character is composed too (empty lingtai.md deletes the section).
        assert "character" not in agent._prompt_manager._sections
        (agent._working_dir / "system" / "lingtai.md").write_text("I am", encoding="utf-8")
        agent._reconstruct_context()
        assert "I am" in agent._prompt_manager.read_section("character")
    finally:
        agent.stop(timeout=1.0)
