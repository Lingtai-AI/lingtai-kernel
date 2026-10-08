"""The legacy a-priori ``summary`` boolean on unmigrated tool schemas.

``glob`` used to be covered here too. It later became an action of the
migrated ``file`` family, whose canonical control was the root ``summarize``
boolean, and that whole family has since been removed (durable filesystem
work goes through ``shell``), so nothing ``glob``-shaped is covered here.

``daemon`` used to be covered here as the last unmigrated holder of the literal
``summary`` field. It is now a migrated LTP v2 family whose canonical control is
the root ``summarize`` boolean, so its schema coverage moved to
``tests/test_tool_family_daemon_migration.py`` for the same reason ``glob``'s
did. This file keeps the assertion that still belongs to it: the legacy spelling
remains honored by the central summarizer for any still-unmigrated or
historical/pending caller, so migrating a family never silently drops a
``summary=true`` call already in flight.
"""
from __future__ import annotations

from lingtai.kernel.tool_result_summary import summary_requested
from lingtai.tools.daemon import get_schema as daemon_schema
from lingtai.tools.task_card import get_schema as task_card_schema


def test_daemon_no_longer_advertises_the_legacy_flat_summary_option() -> None:
    """The migrated public ``daemon`` schema exposes the canonical root
    ``summarize``; the pre-migration flat ``summary`` field is gone from it."""
    schema = daemon_schema()

    assert "summary" not in schema["properties"]
    assert schema["properties"]["summarize"]["type"] == "boolean"


def test_task_card_no_longer_advertises_the_legacy_flat_summary_option() -> None:
    schema = task_card_schema()

    assert "summary" not in schema["properties"]
    assert schema["properties"]["summarize"]["type"] == "boolean"


def test_legacy_summary_spelling_is_still_honored_for_any_caller() -> None:
    """The legacy flag is not removed from the summarizer — an unmigrated tool,
    or a historical/pending call carrying the old spelling, still opts in."""
    assert summary_requested({"summary": True}) is True
    assert summary_requested({"summary": True}, "daemon") is True
    assert summary_requested({"summary": False}) is False
    assert summary_requested({}) is False


def test_canonical_summarize_spelling_is_scoped_to_migrated_families() -> None:
    """``summarize`` is recognized only for a migrated family, so an unmigrated
    tool's own domain field named ``summarize`` is never reinterpreted."""
    assert summary_requested({"summarize": True}, "daemon") is True
    assert summary_requested({"summarize": True}, "task_card") is True
    assert summary_requested({"summarize": True}, "some-unmigrated-tool") is False
