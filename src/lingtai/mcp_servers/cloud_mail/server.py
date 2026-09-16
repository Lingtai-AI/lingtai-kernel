"""LingTai Cloud Mail MCP server.

Exposes a single ``cloud_mail`` MCP tool with the strict LTP-v2 envelope
(``action``/``input``/``reasoning``/``summarize``, see ``_family.py``) that
dispatches through ``CloudMailManager`` (check/read/search/send/accounts/
add_user). Reserved settings/manual actions stay manager-independent. Inbound
mail flows into the host agent's inbox via LICC.

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
import logging
import os
from pathlib import Path

import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from .._results import json_tool_result as _tool_result
from .._results import unknown_tool_error as _unknown_tool

from .. import _config
from .licc import push_inbox_event
from .manager import CloudMailManager, DESCRIPTION, SCHEMA
from ._family import handle_cloud_mail
from .plugin import CLOUD_MAIL_PLUGIN
from .settings import CONFIG_ENV, CloudMailSettingsProvider

log = logging.getLogger("lingtai_cloud_mail")

_MANAGER_UNAVAILABLE = {
    "status": "error",
    "error": (
        "Cloud Mail manager not initialized — server boot failed. "
        "Check stderr for the exception class, then verify the environment "
        f"and configuration (most often a missing {CONFIG_ENV} or invalid config)."
    ),
}

_SERVER_INSTRUCTIONS = (
    "lingtai-cloud-mail: REST email via a self-hosted Cloud Mail deployment "
    "(Cloudflare Workers). Configure via the LINGTAI_CLOUD_MAIL_CONFIG env var "
    "pointing at a JSON file. Inbound mail flows into the host agent's inbox "
    "via LICC polling. Setup, config schema, and troubleshooting: "
    "https://github.com/maillab/cloud-mail"
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config_with_path() -> tuple[dict, Path]:
    """Load the document and retain the exact successfully resolved path."""
    return _config.load_config_file(CONFIG_ENV, label="Cloud Mail")


def load_config() -> dict:
    """Read config from the path in LINGTAI_CLOUD_MAIL_CONFIG.

    Path is resolved relative to LINGTAI_AGENT_DIR (or cwd as fallback)
    when not absolute. Plaintext only — no *_env indirection.
    """
    return _load_config_with_path()[0]


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
    cfg, config_path = _load_config_with_path()
    accounts = accounts_from_config(cfg)

    agent_dir_raw = os.environ.get("LINGTAI_AGENT_DIR")
    working_dir = Path(agent_dir_raw) if agent_dir_raw else Path.cwd()
    working_dir.mkdir(parents=True, exist_ok=True)

    def _on_inbound(event: dict) -> bool:
        return push_inbox_event(
            sender=event["from"],
            subject=event["subject"],
            body=event["body"],
            metadata=event.get("metadata"),
            wake=event.get("wake", True),
        )

    mgr = CloudMailManager(
        accounts=accounts,
        working_dir=working_dir,
        config_path=config_path,
        on_inbound=_on_inbound,
    )
    return mgr, working_dir


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

def build_server(manager: CloudMailManager | None) -> Server:
    """Construct the MCP server. ``manager`` is None when eager start failed;
    operational calls and settings fail closed while manual remains usable."""
    settings_provider = CloudMailSettingsProvider(manager)

    async def _list_tools(
        _ctx: ServerRequestContext,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=CLOUD_MAIL_PLUGIN.name,
                    description=DESCRIPTION,
                    input_schema=SCHEMA,
                ),
            ],
        )

    async def _call_tool(
        _ctx: ServerRequestContext,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        if params.name != CLOUD_MAIL_PLUGIN.name:
            raise _unknown_tool(params.name)
        arguments = params.arguments or {}
        if manager is None and arguments.get("action") not in {"settings", "manual"}:
            result = dict(_MANAGER_UNAVAILABLE)
        else:
            try:
                result = await asyncio.to_thread(
                    handle_cloud_mail,
                    manager,
                    arguments,
                    settings_provider=settings_provider,
                )
            except Exception as e:
                result = {
                    "status": "error",
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
        return _tool_result(result)

    server: Server = Server(
        CLOUD_MAIL_PLUGIN.server_name,
        instructions=_SERVER_INSTRUCTIONS,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )
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
            "eager start failed; operational tool calls will return errors "
            "until fixed (%s)",
            type(e).__name__,
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
