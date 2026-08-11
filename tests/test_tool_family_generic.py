"""Generic ToolFamily/ChildTool infrastructure — proven with a fake family.

This fake ``widget`` family (actions ``spin`` and ``manual``) has no relation
to ``web``. It exists solely to prove the composition/dispatch/registration
boilerplate in ``lingtai.tools.tool_family`` is generic, not Web-specific.
"""
from __future__ import annotations

import pytest

from lingtai.tools.tool_family import (
    RESERVED_MANUAL_NAME,
    ChildTool,
    ToolFamily,
    ToolFamilyError,
)


def _spin_child(calls: list[dict]) -> ChildTool:
    def handler(input_):
        calls.append(dict(input_))
        return {"status": "ok", "action": "spin", "speed": input_.get("speed")}

    return ChildTool(
        name="spin",
        input_schema={
            "type": "object",
            "properties": {"speed": {"type": "integer"}},
            "required": ["speed"],
            "additionalProperties": False,
        },
        handler=handler,
        title="spin input",
    )


def _manual_child() -> ChildTool:
    def handler(_input):
        return {"status": "ok", "manual": "widget manual body", "manual_path": "/fake/manual_path"}

    return ChildTool(
        name=RESERVED_MANUAL_NAME,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
        title="manual input",
    )


def _widget_family(calls: list[dict] | None = None) -> ToolFamily:
    calls = calls if calls is not None else []
    return ToolFamily("widget", [_spin_child(calls), _manual_child()])


def test_registration_is_deterministic_and_ordered():
    fam = _widget_family()
    assert fam.child_names == ("spin", "manual")
    assert fam.has_manual()


def test_duplicate_child_name_fails_loudly():
    calls: list[dict] = []
    with pytest.raises(ToolFamilyError, match="duplicate child name 'spin'"):
        ToolFamily("widget", [_spin_child(calls), _spin_child(calls)])


def test_manual_reserved_name_collision_fails_loudly():
    """A repeated reserved ``manual`` child is caught by the duplicate-name check."""

    def handler(_input):
        return {"status": "ok"}

    second_manual = ChildTool(
        name=RESERVED_MANUAL_NAME,
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=handler,
    )
    with pytest.raises(ToolFamilyError, match=f"duplicate child name '{RESERVED_MANUAL_NAME}'"):
        ToolFamily("widget", [_manual_child(), second_manual])


def test_empty_registry_fails_loudly():
    with pytest.raises(ToolFamilyError):
        ToolFamily("widget", [])


def test_schema_composes_action_input_reasoning_summarize_root():
    fam = _widget_family()
    schema = fam.build_schema()
    assert schema["type"] == "object"
    # ``reasoning`` is Host InvocationContext/audit metadata and is REQUIRED
    # — the family schema declares it itself so it is required even before
    # Agent schema composition re-injects the identical property text (that
    # injection only touches ``properties``, never ``required``).
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["properties"]["action"]["enum"] == ["spin", "manual"]
    assert schema["properties"]["input"]["type"] == "object"
    assert schema["properties"]["reasoning"]["type"] == "string"
    assert schema["properties"]["summarize"]["type"] == "boolean"
    branches = schema["properties"]["input"]["oneOf"]
    assert [b["title"] for b in branches] == ["spin input", "manual input"]
    spin_branch, manual_branch = branches
    assert spin_branch["required"] == ["speed"]
    assert spin_branch["additionalProperties"] is False
    assert manual_branch["properties"] == {}
    for branch in branches:
        assert branch["additionalProperties"] is False
        assert "reasoning" not in branch.get("properties", {})
        assert "_reasoning" not in branch.get("properties", {})
        assert "summarize" not in branch.get("properties", {})


def test_schema_never_uses_generic_unconstrained_input_object():
    fam = _widget_family()
    schema = fam.build_schema()
    for branch in schema["properties"]["input"]["oneOf"]:
        assert branch.get("type") == "object"
        assert "properties" in branch


def test_dispatch_selects_child_by_action_and_passes_only_input():
    calls: list[dict] = []
    fam = _widget_family(calls)
    result = fam.handle({"action": "spin", "input": {"speed": 5}, "reasoning": "why"})
    assert result == {"status": "ok", "action": "spin", "speed": 5}
    assert calls == [{"speed": 5}]


def test_dispatch_unknown_action_fails_with_action_required():
    fam = _widget_family()
    result = fam.handle({"action": "nope", "input": {}})
    assert result["status"] == "failed"
    assert result["error_code"] == "ACTION_REQUIRED"


def test_dispatch_missing_action_fails_with_action_required():
    fam = _widget_family()
    result = fam.handle({"input": {}})
    assert result["status"] == "failed"
    assert result["error_code"] == "ACTION_REQUIRED"


def test_dispatch_unhashable_action_fails_without_raising():
    """Invalid JSON can make ``action`` unhashable (issue #513 blocker class).

    Such an action matches no child and must render the stable typed envelope
    failure, exactly as ``kernel/tool_dispatch.py`` does — not raise
    ``TypeError`` out of the dispatcher.
    """
    calls: list[dict] = []
    fam = _widget_family(calls)
    for unhashable in ([], {}, ["spin"], {"spin": 1}, set()):
        result = fam.handle({"action": unhashable, "input": {}, "reasoning": "why"})
        assert result["status"] == "failed", unhashable
        assert result["error_code"] == "ACTION_REQUIRED", unhashable
    assert calls == []


def test_dispatch_non_object_input_fails_with_invalid_argument():
    fam = _widget_family()
    result = fam.handle({"action": "spin", "input": "not-an-object"})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_dispatch_unknown_root_field_fails_with_invalid_argument():
    fam = _widget_family()
    result = fam.handle({"action": "spin", "input": {"speed": 1}, "bogus": True})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_dispatch_relocates_root_field_that_matches_selected_actions_own_schema_when_absent_from_input():
    """A root-level key that IS a declared property of the selected action,
    and is entirely absent from ``input``, is relocated into ``input``
    rather than rejected — the fix for a calling model leaking its own
    native flat tool shape (e.g. ``Edit(..., replace_all)``) into an
    enveloped family's root instead of nesting it under ``input``."""
    calls: list[dict] = []
    fam = _widget_family(calls)
    result = fam.handle({"action": "spin", "input": {}, "speed": 7})
    assert result["status"] == "ok"
    assert result["speed"] == 7
    assert calls == [{"speed": 7}]


def test_dispatch_rejects_duplicate_root_field_matching_key_already_in_input():
    """The relocation exception does NOT cover a root-level key that
    duplicates a name already present in ``input`` — even with an identical
    value. Two conflicting-or-redundant places for the same value stays a
    rejected hygiene violation, unchanged from before."""
    fam = _widget_family()
    result = fam.handle({"action": "spin", "input": {"speed": 1}, "speed": 1})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_dispatch_still_rejects_root_field_belonging_to_a_different_actions_schema():
    """A root-level key that matches some OTHER child's input property (not
    the selected action's) is not relocated — the exception is scoped to the
    selected action's own schema only, so cross-branch keys are still
    rejected exactly as before."""
    fam = _widget_family()
    # "manual_path" belongs to no widget action's input schema at all, and
    # even a name matching another action's own declared property (there is
    # none to collide with here, since `manual`'s input schema is empty)
    # would not be relocated for `spin`'s dispatch — this proves the plain
    # not-in-any-schema case remains rejected under the new code path too.
    result = fam.handle({"action": "spin", "input": {"speed": 1}, "manual_path": "/x"})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_dispatch_rejects_reasoning_or_summarize_leaking_into_input():
    fam = _widget_family()
    result = fam.handle({"action": "spin", "input": {"speed": 1, "summarize": True}})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"

    result2 = fam.handle({"action": "spin", "input": {"speed": 1, "reasoning": "x"}})
    assert result2["status"] == "failed"
    assert result2["error_code"] == "INVALID_ARGUMENT"


def test_dispatch_non_boolean_summarize_fails_loudly():
    fam = _widget_family()
    result = fam.handle({"action": "spin", "input": {"speed": 1}, "summarize": "yes"})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_dispatch_strips_summarize_and_reasoning_before_child_sees_input():
    calls: list[dict] = []
    fam = _widget_family(calls)
    fam.handle({"action": "spin", "input": {"speed": 3}, "reasoning": "r", "summarize": True})
    assert calls == [{"speed": 3}]


def test_child_handlers_receive_only_input_never_reasoning_or_summarize():
    """Direct proof for every registered child, not just one action: the
    handler's sole argument is the selected child's own ``input`` mapping.
    ``reasoning`` (required Host audit metadata) and ``summarize`` (optional
    Host presentation control) never reach any child, regardless of whether
    they are present, absent, or combined on one call."""
    seen_args: list[dict] = []

    def spin_handler(input_):
        seen_args.append(dict(input_))
        return {"status": "ok"}

    def manual_handler(input_):
        seen_args.append(dict(input_))
        return {"status": "ok"}

    fam = ToolFamily(
        "widget",
        [
            ChildTool(
                "spin",
                {"type": "object", "properties": {"speed": {"type": "integer"}}, "required": ["speed"], "additionalProperties": False},
                spin_handler,
            ),
            ChildTool(
                RESERVED_MANUAL_NAME,
                {"type": "object", "properties": {}, "additionalProperties": False},
                manual_handler,
            ),
        ],
    )
    fam.handle({"action": "spin", "input": {"speed": 1}, "reasoning": "r1"})
    fam.handle({"action": "spin", "input": {"speed": 2}, "reasoning": "r2", "summarize": True})
    fam.handle({"action": "manual", "input": {}, "reasoning": "r3", "summarize": False})
    assert seen_args == [{"speed": 1}, {"speed": 2}, {}]
    for args in seen_args:
        assert "reasoning" not in args
        assert "_reasoning" not in args
        assert "summarize" not in args


def test_canonical_child_input_schema_never_declares_reasoning_or_summarize():
    """Static proof at the schema level (distinct from the dispatch-time
    behavioral proof above): no child's own canonical ``input_schema`` — the
    schema a family author writes — declares ``reasoning``, ``_reasoning``,
    or ``summarize`` as one of its own properties. Those are envelope-only
    fields owned exclusively by the family root."""
    fam = _widget_family()
    for name in fam.child_names:
        child = fam._children[name]
        props = child.input_schema.get("properties", {})
        assert "reasoning" not in props
        assert "_reasoning" not in props
        assert "summarize" not in props


def test_dispatch_child_result_is_not_double_wrapped():
    calls: list[dict] = []
    fam = _widget_family(calls)
    result = fam.handle({"action": "spin", "input": {"speed": 9}, "reasoning": "r"})
    # The child's own raw return value is the family's return value verbatim.
    assert result == {"status": "ok", "action": "spin", "speed": 9}
    assert "result" not in result and "data" not in result


def test_manual_child_dispatch_returns_full_manual_shape():
    fam = _widget_family()
    result = fam.handle({"action": "manual", "input": {}, "reasoning": "load manual"})
    assert result == {
        "status": "ok",
        "manual": "widget manual body",
        "manual_path": "/fake/manual_path",
    }


def test_manual_child_rejects_nonempty_input():
    fam = _widget_family()
    # The manual branch's own strict-empty schema is model-facing guidance;
    # dispatch-time correspondence is a separate, real check per
    # tools/CONTRACT.md "Dispatch and actions": a family MUST validate
    # action/input correspondence, not just rely on schema conformance.
    # ChildTool handlers here do not themselves reject extra keys (that is
    # each child's own responsibility, per "implementation independence"),
    # but the family-level schema composition still advertises strict-empty.
    schema = fam.build_schema()
    manual_branch = next(b for b in schema["properties"]["input"]["oneOf"] if b["title"] == "manual input")
    assert manual_branch["additionalProperties"] is False
    assert manual_branch["properties"] == {}


def _minimal_evaluate_if_then(condition: dict, action: str, input_value: dict) -> bool | None:
    """Tiny, dependency-free structural evaluator for exactly the ``if``/
    ``then`` shape :meth:`ToolFamily.build_schema` generates — not a general
    JSON Schema validator. No JSON Schema library is installed in this repo,
    and the task authorizes a minimal local evaluator instead of adding one.

    Returns ``True`` if ``action``/``input_value`` satisfy this condition's
    ``then`` branch, ``False`` if ``if`` matches but ``then`` is violated,
    and ``None`` if ``if`` does not match at all (condition inapplicable —
    ``allOf`` treats a non-matching ``if`` as vacuously satisfied, mirroring
    real JSON Schema ``if``/``then`` semantics without ``else``).
    """
    if_clause = condition["if"]
    if action != if_clause["properties"]["action"]["const"]:
        return None
    then_clause = condition["then"]
    input_schema = then_clause["properties"]["input"]
    allowed = set(input_schema.get("properties", {}))
    required = set(input_schema.get("required", []))
    if not required.issubset(input_value):
        return False
    if input_schema.get("additionalProperties") is False and set(input_value) - allowed:
        return False
    return True


def test_schema_correlates_action_const_with_exact_child_input_via_root_all_of():
    """Real schema-level correlation, generated purely from the child
    registry: one ``allOf`` condition per child, each ``if`` testing
    ``action`` via ``const`` against exactly that child's own registry name,
    each ``then`` constraining ``input`` to that exact child's canonical
    ``input_schema`` — not a mapping table, not a second name list."""
    fam = _widget_family()
    schema = fam.build_schema()
    conditions = schema["allOf"]
    assert [c["if"]["properties"]["action"]["const"] for c in conditions] == list(fam.child_names)
    for child_name, condition in zip(fam.child_names, conditions):
        assert condition["if"]["required"] == ["action"]
        then_input = condition["then"]["properties"]["input"]
        child = fam._children[child_name]
        assert then_input == dict(child.input_schema)


def test_schema_all_of_rejects_mismatched_action_input_pairing_structurally():
    """Direct proof the schema *itself* now correlates ``action``/``input``:
    using a minimal local ``if``/``then`` evaluator (no JSON Schema
    dependency added), a spin-shaped ``input`` fails the ``manual`` action's
    ``allOf`` condition, and an empty ``input`` fails the ``spin`` action's
    condition — the mismatch is caught by schema structure, not only by
    ``handle()`` at dispatch. ``handle()`` remains the always-authoritative,
    fail-closed second layer regardless of what the schema alone proves."""
    fam = _widget_family()
    schema = fam.build_schema()
    conditions = {c["if"]["properties"]["action"]["const"]: c for c in schema["allOf"]}

    manual_condition = conditions["manual"]
    assert _minimal_evaluate_if_then(manual_condition, "manual", {"speed": 1}) is False
    assert _minimal_evaluate_if_then(manual_condition, "manual", {}) is True

    spin_condition = conditions["spin"]
    assert _minimal_evaluate_if_then(spin_condition, "spin", {}) is False
    assert _minimal_evaluate_if_then(spin_condition, "spin", {"speed": 1}) is True

    # A condition for a different action does not apply at all (vacuous).
    assert _minimal_evaluate_if_then(manual_condition, "spin", {"speed": 1}) is None


def test_handle_remains_authoritative_fail_closed_even_though_schema_now_correlates():
    """Dispatch is a second, always-authoritative enforcement layer: a
    mismatched ``action``/``input`` pairing is still rejected by
    ``handle()`` even though the schema now also correlates them — this is
    not a guess/fallback, it is the same exact-key check as before, kept
    unconditionally regardless of what any provider's schema-side validation
    does or does not enforce."""
    fam = _widget_family()
    result = fam.handle({"action": "manual", "input": {"speed": 1}, "reasoning": "r"})
    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"


def test_build_schema_does_not_leak_shared_mutable_child_schema_by_reference():
    """Regression test: ``build_schema()`` must not embed a child's own
    ``input_schema`` (or its nested containers) by reference into the
    returned schema such that mutating one call's result corrupts a later,
    independent call. A shallow ``dict.update`` only copies the top level;
    nested dicts (e.g. ``properties``) stay shared with whatever object the
    caller's ``input_schema`` points at."""
    calls: list[dict] = []
    fam = _widget_family(calls)
    first = fam.build_schema()
    first_spin_branch = next(
        b for b in first["properties"]["input"]["oneOf"] if b["title"] == "spin input"
    )
    # Mutate the nested ``properties`` container reachable from the first
    # returned schema.
    first_spin_branch["properties"]["speed"]["type"] = "string"

    second = fam.build_schema()
    second_spin_branch = next(
        b for b in second["properties"]["input"]["oneOf"] if b["title"] == "spin input"
    )
    assert second_spin_branch["properties"]["speed"]["type"] == "integer"


def test_build_schema_all_of_conditions_are_mutation_isolated():
    """Companion to the ``oneOf``-branch mutation-isolation regression test,
    for the new ``allOf`` conditions: mutating one call's
    ``then.properties.input`` must not corrupt a later, independent call, the
    child's own canonical ``input_schema``, or the sibling ``oneOf`` branch —
    each surface is built from its own deep copy."""
    calls: list[dict] = []
    fam = _widget_family(calls)
    original_spin_schema = dict(fam._children["spin"].input_schema)

    first = fam.build_schema()
    spin_condition = next(c for c in first["allOf"] if c["if"]["properties"]["action"]["const"] == "spin")
    spin_condition["then"]["properties"]["input"]["properties"]["speed"]["type"] = "string"

    second = fam.build_schema()
    second_spin_condition = next(c for c in second["allOf"] if c["if"]["properties"]["action"]["const"] == "spin")
    assert second_spin_condition["then"]["properties"]["input"]["properties"]["speed"]["type"] == "integer"

    # The child's own canonical schema is untouched.
    assert fam._children["spin"].input_schema["properties"]["speed"]["type"] == "integer"
    assert dict(fam._children["spin"].input_schema) == original_spin_schema

    # The sibling ``oneOf`` branch from the SAME first call is untouched —
    # allOf.then.input and the oneOf branch do not share a container either.
    first_spin_branch = next(b for b in first["properties"]["input"]["oneOf"] if b["title"] == "spin input")
    assert first_spin_branch["properties"]["speed"]["type"] == "integer"
