"""LingTai WhatsApp MCP (personal-account mode)."""
from __future__ import annotations

from .licc import push_inbox_event  # noqa: F401
from .manager import WhatsAppManager, load_config  # noqa: F401
from .server import serve  # noqa: F401

__version__ = "0.2.0"
