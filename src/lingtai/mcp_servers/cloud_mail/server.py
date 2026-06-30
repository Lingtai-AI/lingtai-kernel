"""LingTai Cloud Mail MCP server.

Exposes a single omnibus ``cloud_mail`` MCP tool that dispatches to
``CloudMailManager`` (check/read/search/send/accounts/add_user). Inbound mail
flows into the host agent's inbox via LICC.

Configuration:
    LINGTAI_CLOUD_MAIL_CONFIG  — path to a JSON config file (required).
        Resolved relative to LINGTAI_AGENT_DIR (or cwd) when not absolute.

Config schema (plaintext, no env-indirection):

    {
      "accounts": [
        {
          "alias": "cloudmail",
          "base_url": "https://mail.example.com",
          "admin_email": "admin@example.com",
          "admin_password": "...",
          "user_email": "admin@example.com",     // optional (send only)
          "user_password": "...",                 // optional (send only)
          "send_account_id": 1,                    // optional (send only)
          "allowed_senders": ["only@example.com"], // optional allow-list
          "poll_interval": 30,                     // optional, seconds
          "notify_existing": false                 // optional, default false
        }
      ]
    }

Env vars injected by the LingTai kernel for LICC:
    LINGTAI_AGENT_DIR — host agent's working directory.
    LINGTAI_MCP_NAME  — this MCP's registry name (typically "cloud_mail").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .. import _config
from .licc import push_inbox_event
from .manager import CloudMailManager, DESCRIPTION, SCHEMA

log = logging.getLogger("lingtai_cloud_mail")

_SERVER_INSTRUCTIONS = (
    "lingtai-cloud-mail: REST email via a self-hosted Cloud Mail deployment "
    "(Cloudflare Workers). Configure via the LINGTAI_CLOUD_MAIL_CONFIG env var "
    "pointing at a JSON file. Inbound mail flows into the host agent's inbox "
    "via LICC polling. Setup, config schema, and troubleshooting: "
    "https://github.com/maillab/cloud-mail"
)

CONFIG_ENV = "LINGTAI_CLOUD_MAIL_CONFIG"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Read config from the path in LINGTAI_CLOUD_MAIL_CONFIG.

    Path is resolved relative to LINGTAI_AGENT_DIR (or cwd as fallback)
    when not absolute. Plaintext only — no *_env indirection.
    """
    return _config.load_config_file(CONFIG_ENV, label="Cloud Mail")[0]


def accounts_from_config(cfg: dict) -> list[dict]:
    """Normalize config into the accounts list CloudMailManager expects.

    Accepts the canonical ``{accounts: [...]}`` shape or a flat
    single-account dict for convenience.
    """
    if isinstance(cfg, dict) and "accounts" in cfg:
        accounts = cfg["accounts"]
        if not isinstance(accounts, list) or not accounts:
            raise ValueError("config 'accounts' must be a non-empty list")
        return list(accounts)
    if isinstance(cfg, dict) and "base_url" in cfg:
        return [cfg]
    raise ValueError(
        "config must contain 'accounts' (list) or a single-account dict "
        "with 'base_url'"
    )


# ---------------------------------------------------------------------------
# Manager construction
# ---------------------------------------------------------------------------

def build_manager() -> tuple[CloudMailManager, Path]:
    """Construct the Cloud Mail manager from env + config.

    Returns (manager, working_dir). Inbound rows discovered by polling are
    pushed to the host agent inbox via LICC.
    """
    cfg = load_config()
    accounts = accounts_from_config(cfg)

    agent_dir_raw = os.environ.get("LINGTAI_AGENT_DIR")
    working_dir = Path(agent_dir_raw) if agent_dir_raw else Path.cwd()
    working_dir.mkdir(parents=True, exist_ok=True)

    def _on_inbound(event: dict) -> None:
        push_inbox_event(
            sender=event["from"],
            subject=event["subject"],
            body=event["body"],
            metadata=event.get("metadata"),
            wake=event.get("wake", True),
        )

    mgr = CloudMailManager(
        accounts=accounts,
        working_dir=working_dir,
        on_inbound=_on_inbound,
    )
    return mgr, working_dir


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def build_server(manager: CloudMailManager | None) -> Server:
    """Construct the MCP server. ``manager`` is None when eager start failed;
    in that case every tool call returns an error explaining why."""
    server: Server = Server("lingtai-cloud-mail", instructions=_SERVER_INSTRUCTIONS)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="cloud_mail",
                description=DESCRIPTION,
                inputSchema=SCHEMA,
            ),
        ]

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any],
    ) -> list[types.TextContent]:
        if name != "cloud_mail":
            raise ValueError(f"unknown tool: {name!r}")
        if manager is None:
            result = {
                "status": "error",
                "error": (
                    "Cloud Mail manager not initialized — server boot failed. "
                    "Check stderr for the underlying exception (most often a "
                    f"missing {CONFIG_ENV} or invalid config)."
                ),
            }
        else:
            try:
                result = await asyncio.to_thread(manager.handle, arguments)
            except Exception as e:
                result = {
                    "status": "error",
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
        return [types.TextContent(
            type="text", text=json.dumps(result, ensure_ascii=False),
        )]

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def serve() -> None:
    """Run the MCP server over stdio. Eagerly starts the manager so the
    polling loop is up before the host expects mail."""
    manager: CloudMailManager | None = None
    try:
        manager, _wd = build_manager()
        manager.start()
        log.info("Cloud Mail polling running")
    except Exception as e:
        log.error(
            "eager start failed; tool calls will return errors until fixed: %s", e,
        )
        manager = None

    server = build_server(manager)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                pass
