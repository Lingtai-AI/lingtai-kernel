"""Shared navigation helpers for the compact ``ToolFamily`` composed schema.

``ToolFamily.build_schema()`` composes one root ``oneOf`` discriminated
union: exactly one branch per registered child, each shaped
``{"properties": {"action": {"const": <name>}, "input": <child schema>}}``.
Tests navigate that shape through these helpers rather than re-deriving the
path in every family suite, so a future composition change has one place to
update. Kept dependency-free — no JSON Schema library is installed here.
"""
from __future__ import annotations

from typing import Any, Mapping


def branch_actions(schema: Mapping[str, Any]) -> list[str]:
    """Return the root ``oneOf`` branches' ``action`` consts in schema order."""
    return [branch["properties"]["action"]["const"] for branch in schema["oneOf"]]


def action_input_schemas(schema: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Map each root ``oneOf`` branch's ``action`` const to its ``input`` schema."""
    return {
        branch["properties"]["action"]["const"]: branch["properties"]["input"]
        for branch in schema["oneOf"]
    }


def action_input_schema(schema: Mapping[str, Any], action: str) -> dict[str, Any]:
    """Return the exact ``input`` schema the root ``oneOf`` pairs with ``action``."""
    return action_input_schemas(schema)[action]


def assert_compact_envelope(schema: Mapping[str, Any], actions: list[str]) -> None:
    """Assert the closed LTP v2 root plus one discriminated branch per action.

    Every child schema must appear exactly once: only inside its own root
    ``oneOf`` branch, never duplicated under ``properties.input`` or a root
    ``allOf``.
    """
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(actions)
    root_input = schema["properties"]["input"]
    assert root_input["type"] == "object"
    assert set(root_input) == {"type", "description"}
    for duplicate in ("oneOf", "anyOf", "allOf", "properties"):
        assert duplicate not in root_input, duplicate
    assert "allOf" not in schema and "anyOf" not in schema
    assert branch_actions(schema) == list(actions)
    for branch in schema["oneOf"]:
        assert set(branch) == {"properties"}
        assert set(branch["properties"]) == {"action", "input"}
        assert branch["properties"]["input"]["type"] == "object"
        assert "title" not in branch
