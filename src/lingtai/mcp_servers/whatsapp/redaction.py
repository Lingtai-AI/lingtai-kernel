"""Status redaction for the WhatsApp MCP (personal-account mode)."""
from __future__ import annotations

from typing import Any

# Personal-account mode has no Meta access tokens; keep a fail-closed safe
# status builder that drops any unexpected secret-looking key.

_SENSITIVE_KEY = ("token", "secret", "password", "credential", "auth")


def safe_status(status: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in status.items() if not any(s in k.lower() for s in _SENSITIVE_KEY)}
