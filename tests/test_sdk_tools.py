"""Tool contract metadata: builtin names track the live registry; dataclass
defaults are sane; permission modes are stable strings."""
from __future__ import annotations

from lingtai_sdk import (
    BUILTIN_TOOLS,
    PermissionMode,
    ToolResult,
    ToolSpec,
    builtin_tool_names,
)


def test_builtin_tool_names_match_registry():
    from lingtai.capabilities import _BUILTIN, _GROUPS

    expected = tuple(sorted(set(_BUILTIN) | set(_GROUPS)))
    assert builtin_tool_names() == expected
    assert BUILTIN_TOOLS == expected


def test_builtin_tool_names_include_known_entries():
    names = builtin_tool_names()
    for known in ("read", "write", "edit", "bash", "file"):
        assert known in names


def test_toolspec_defaults():
    spec = ToolSpec(name="read")
    assert spec.name == "read"
    assert spec.description == ""
    assert spec.input_schema == {}
    assert spec.source == "capability"


def test_toolresult_defaults():
    res = ToolResult(tool="read")
    assert res.tool == "read"
    assert res.content is None
    assert res.is_error is False


def test_permission_mode_constants():
    assert PermissionMode.DEFAULT == "default"
    assert "plan" in PermissionMode.ALL
    assert PermissionMode.ACCEPT_ALL in PermissionMode.ALL
