from __future__ import annotations

from lingtai.tools.daemon import get_schema as daemon_schema
from lingtai.tools.glob import get_schema as glob_schema


def _assert_summary_option(schema: dict) -> None:
    prop = schema["properties"]["summary"]
    assert prop["type"] == "boolean"
    assert prop["default"] is False
    assert "raw result is preserved" in prop["description"]


def test_glob_exposes_apriori_summary_option() -> None:
    _assert_summary_option(glob_schema())


def test_daemon_exposes_apriori_summary_option() -> None:
    _assert_summary_option(daemon_schema())
