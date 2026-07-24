"""Tests for OpenAI adapter schema scrubbing (Moonshot/Kimi compat)."""

import copy

import pytest
from lingtai.llm.openai.adapter import _scrub_openai_schema, _build_tools
from lingtai.kernel.llm.base import FunctionSchema


class TestScrubOpenaiSchema:
    """Pure-function tests for _scrub_openai_schema."""

    def test_passes_through_simple_schema(self):
        """Simple schema passes through unchanged."""
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        assert _scrub_openai_schema(schema) == schema

    def test_converts_oneOf_to_anyOf(self):
        """oneOf is rewritten to anyOf."""
        schema = {"oneOf": [{"type": "string"}, {"type": "number"}]}
        result = _scrub_openai_schema(schema)
        assert "oneOf" not in result
        assert result == {"anyOf": [{"type": "string"}, {"type": "number"}]}

    def test_drops_not(self):
        """not combinator is dropped."""
        schema = {"type": "string", "not": {"type": "number"}}
        result = _scrub_openai_schema(schema)
        assert "not" not in result
        assert result == {"type": "string"}

    def test_pushes_type_into_anyof_items(self):
        """Core fix: anyOf + type coexistence — type pushed into children."""
        schema = {
            "type": "object",
            "anyOf": [
                {"properties": {"kind": {"const": "file"}}},
                {"properties": {"kind": {"const": "dir"}}},
            ],
        }
        result = _scrub_openai_schema(schema)
        assert "type" not in result
        assert result["anyOf"][0] == {
            "type": "object",
            "properties": {"kind": {"const": "file"}},
        }
        assert result["anyOf"][1] == {
            "type": "object",
            "properties": {"kind": {"const": "dir"}},
        }

    def test_does_not_overwrite_child_type(self):
        """Children that already have type are not overwritten."""
        schema = {
            "type": "object",
            "anyOf": [
                {"type": "string"},
                {"properties": {"x": {"type": "number"}}},
            ],
        }
        result = _scrub_openai_schema(schema)
        assert "type" not in result
        assert result["anyOf"][0] == {"type": "string"}  # original preserved
        assert result["anyOf"][1] == {
            "type": "object",
            "properties": {"x": {"type": "number"}},
        }

    def test_handles_nested_anyof(self):
        """Nested anyOf + type is handled correctly."""
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "anyOf": [
                        {"format": "email"},
                        {"format": "uri"},
                    ],
                }
            },
        }
        result = _scrub_openai_schema(schema)
        # Top-level type preserved (no anyOf coexistence)
        assert result.get("type") == "object"
        # Nested anyOf + type handled
        value_schema = result["properties"]["value"]
        assert "type" not in value_schema
        assert value_schema["anyOf"][0] == {"type": "string", "format": "email"}

    def test_does_not_mutate_input(self):
        """Original schema is not mutated."""
        schema = {
            "type": "object",
            "anyOf": [{"properties": {"x": {"type": "string"}}}],
        }
        original = copy.deepcopy(schema)
        _scrub_openai_schema(schema)
        assert schema == original

    def test_handles_empty_schema(self):
        """Empty dict passes through."""
        assert _scrub_openai_schema({}) == {}

    def test_handles_primitive_values(self):
        """Primitive values pass through unchanged."""
        assert _scrub_openai_schema("string") == "string"
        assert _scrub_openai_schema(42) == 42
        assert _scrub_openai_schema(None) is None
        assert _scrub_openai_schema(True) is True

    def test_handles_list_of_schemas(self):
        """List of schemas is processed element-wise."""
        schemas = [
            {"type": "object", "anyOf": [{"properties": {"x": {"type": "string"}}}]},
            {"type": "string"},
        ]
        result = _scrub_openai_schema(schemas)
        assert "type" not in result[0]
        assert result[0]["anyOf"][0] == {
            "type": "object",
            "properties": {"x": {"type": "string"}},
        }
        assert result[1] == {"type": "string"}


class TestBuildTools:
    """Tests for _build_tools with schema scrubbing."""

    def test_scrubs_tool_parameters(self):
        """_build_tools scrubs parameters for Moonshot compat."""
        schemas = [
            FunctionSchema(
                name="test_tool",
                description="A test tool",
                parameters={
                    "type": "object",
                    "anyOf": [
                        {"properties": {"kind": {"const": "file"}}},
                    ],
                },
            )
        ]
        result = _build_tools(schemas)
        assert result is not None
        assert len(result) == 1
        params = result[0]["function"]["parameters"]
        # type should be pushed into anyOf children
        assert "type" not in params
        assert params["anyOf"][0] == {
            "type": "object",
            "properties": {"kind": {"const": "file"}},
        }

    def test_returns_none_for_empty(self):
        """None input returns None."""
        assert _build_tools(None) is None
        assert _build_tools([]) is None

    def test_preserves_original_parameters(self):
        """Original FunctionSchema.parameters is not mutated."""
        params = {
            "type": "object",
            "anyOf": [{"properties": {"x": {"type": "string"}}}],
        }
        schemas = [
            FunctionSchema(name="t", description="d", parameters=params)
        ]
        original = copy.deepcopy(params)
        _build_tools(schemas)
        assert params == original