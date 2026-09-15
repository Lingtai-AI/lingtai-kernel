"""``notification`` schema must survive the Codex Responses wire scrub intact.

Regression for the v1.0.0 blocker: ``_scrub_responses_schema``'s typeless
coercion fired on the ``add`` / ``edit`` hook-registry branches, whose
properties map legitimately contains a property literally named
``description`` (value = nested schema dict). The scrub saw a ``description``
key plus no kind key and injected ``type: "string"`` **into the properties
map as a new property key**, producing ``properties.type = "string"`` — a
node that violates the JSON-Schema meta-schema (every ``properties`` value
must be an object or boolean). The Codex backend rejects the whole function
with ``Invalid schema for function 'notification': 'string' is not of type
'object', 'boolean'.``, which put every codex-provider agent into an AED
stuck loop.

The fix restricts coercion to nodes whose ``description`` value is a plain
string (a true typeless value descriptor); a properties map whose
``description`` key holds a nested schema dict is left alone.
"""
from __future__ import annotations

import copy

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.llm.openai.adapter import (
    _build_responses_tools,
    _build_tools,
    _scrub_responses_schema,
)
from lingtai.tools.notification import get_schema
from tests._tool_family_schema_helpers import action_input_schema, branch_actions


def _notification_schema() -> dict:
    return get_schema("en")


def test_scrub_does_not_inject_type_into_description_property_map():
    """A properties map containing a ``description`` key stays a pure map."""
    # Canonical branch for ``add`` has a property literally named description.
    branch = action_input_schema(_notification_schema(), "add")
    assert "description" in branch["properties"]
    scrubbed = _scrub_responses_schema(copy.deepcopy(branch))
    assert "type" not in scrubbed["properties"], (
        "scrub must not inject type: 'string' into the properties map "
        f"(got properties.type={scrubbed['properties'].get('type')!r})"
    )
    # The nested description schema itself is untouched (a dict, not coerced).
    assert isinstance(scrubbed["properties"]["description"], dict)


def test_scrub_still_coerces_typeless_string_description():
    """Genuine typeless value descriptors keep the existing coercion."""
    node = {"description": "some prose about a field"}
    scrubbed = _scrub_responses_schema(copy.deepcopy(node))
    assert scrubbed == {"description": "some prose about a field", "type": "string"}


def test_notification_responses_wire_is_meta_schema_valid():
    """The whole notification parameters root survives the Responses scrub."""
    schema = FunctionSchema(
        name="notification",
        description="x",
        parameters=_notification_schema(),
    )
    tools = _build_responses_tools([schema])
    params = tools[0]["parameters"]
    # Every value in every properties map must be a dict or bool (meta-schema
    # requirement the Codex backend enforces). Walk the whole tree.
    stack = [params]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties":
                    assert isinstance(value, dict)
                    for prop_name, prop_schema in value.items():
                        assert isinstance(prop_schema, (dict, bool)), (
                            f"properties.{prop_name} must be object/boolean, "
                            f"got {type(prop_schema).__name__}: {prop_schema!r}"
                        )
                        stack.append(prop_schema)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)


def test_notification_chat_wire_parameters_is_object():
    """The Chat Completions wire keeps parameters as an object."""
    schema = FunctionSchema(
        name="notification",
        description="x",
        parameters=_notification_schema(),
    )
    tools = _build_tools([schema])
    assert isinstance(tools[0]["function"]["parameters"], dict)


def test_scrub_preserves_add_edit_branches_without_type_key():
    """The ``add`` and ``edit`` root ``oneOf`` branches keep no stray ``type`` property."""
    schema = FunctionSchema(
        name="notification",
        description="x",
        parameters=_notification_schema(),
    )
    params = _build_responses_tools([schema])[0]["parameters"]
    # The hook-registry branches sit at root-oneOf positions 4 and 6.
    assert branch_actions(params)[4] == "add"
    assert branch_actions(params)[6] == "edit"
    for action in ("add", "edit"):
        branch = action_input_schema(params, action)
        assert "type" not in branch["properties"], (
            f"branch {action} must not gain a type property"
        )
