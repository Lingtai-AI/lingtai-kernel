"""MCP server config contracts: runtime-dict shape per transport; secrets are
redacted in repr and in redacted serialization."""
from __future__ import annotations

from lingtai_sdk import (
    MCPHttpServerConfig,
    MCPSdkServerConfig,
    MCPSSEServerConfig,
    MCPStdioServerConfig,
)


def test_stdio_runtime_dict():
    cfg = MCPStdioServerConfig(command="npx", args=["-y", "srv"], env={"K": "v"})
    assert cfg.to_runtime_dict() == {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "srv"],
        "env": {"K": "v"},
    }


def test_stdio_omits_empty_args_env():
    cfg = MCPStdioServerConfig(command="srv")
    assert cfg.to_runtime_dict() == {"type": "stdio", "command": "srv"}


def test_stdio_redacts_env_values():
    cfg = MCPStdioServerConfig(command="srv", env={"TOKEN": "deadbeef"})
    redacted = cfg.to_runtime_dict(redact=True)
    assert redacted["env"] == {"TOKEN": "***"}
    assert "deadbeef" not in repr(cfg)


def test_http_runtime_dict_and_redaction():
    cfg = MCPHttpServerConfig(url="https://x/mcp", headers={"Authorization": "Bearer s"})
    assert cfg.to_runtime_dict() == {
        "type": "http",
        "url": "https://x/mcp",
        "headers": {"Authorization": "Bearer s"},
    }
    assert cfg.to_runtime_dict(redact=True)["headers"] == {"Authorization": "***"}
    assert "Bearer s" not in repr(cfg)


def test_sse_runtime_dict():
    cfg = MCPSSEServerConfig(url="https://x/sse", headers={"H": "sse-secret-val"})
    out = cfg.to_runtime_dict()
    assert out["type"] == "sse"
    assert out["url"] == "https://x/sse"
    assert out["headers"] == {"H": "sse-secret-val"}
    assert cfg.to_runtime_dict(redact=True)["headers"] == {"H": "***"}
    assert "sse-secret-val" not in repr(cfg)


def test_sdk_config_omits_instance():
    sentinel = object()
    cfg = MCPSdkServerConfig(name="inproc", instance=sentinel)
    out = cfg.to_runtime_dict()
    assert out == {"type": "sdk", "name": "inproc"}
    assert "object" in repr(cfg) or "inproc" in repr(cfg)
