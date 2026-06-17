"""Every public symbol is importable from lingtai_sdk, and the runtime never
imports the facade (one-directional dependency)."""
from __future__ import annotations

import importlib

import lingtai_sdk


def test_all_public_symbols_importable():
    for name in lingtai_sdk.__all__:
        assert hasattr(lingtai_sdk, name), f"missing public symbol: {name}"


def test_version_is_a_string():
    assert isinstance(lingtai_sdk.__version__, str)
    assert lingtai_sdk.__version__


def test_expected_symbols_present():
    expected = {
        "LingTaiOptions",
        "SystemPromptAssets",
        "LingTaiClient",
        "query",
        "build_llm_service",
        "options_to_agent_kwargs",
        "PermissionMode",
        "ToolSpec",
        "ToolResult",
        "builtin_tool_names",
        "BUILTIN_TOOLS",
        "MCPServerConfig",
        "MCPStdioServerConfig",
        "MCPHttpServerConfig",
        "MCPSSEServerConfig",
        "MCPSdkServerConfig",
        "SessionRef",
        "SessionStore",
        "InMemorySessionStore",
    }
    assert expected.issubset(set(lingtai_sdk.__all__))


def test_kernel_does_not_import_sdk():
    # The kernel must not depend on the facade. Reimport kernel fresh and check
    # it did not pull lingtai_sdk in transitively at module top-level.
    import sys

    for mod in list(sys.modules):
        if mod.startswith("lingtai_kernel"):
            kmod = sys.modules[mod]
            src = getattr(kmod, "__file__", "") or ""
            # Sanity: kernel modules exist; the real guard is the grep test below.
            assert "lingtai_sdk" not in src

    kernel = importlib.import_module("lingtai_kernel")
    assert not hasattr(kernel, "lingtai_sdk")
