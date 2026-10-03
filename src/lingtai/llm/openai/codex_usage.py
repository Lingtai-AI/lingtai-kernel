"""Read included Codex usage without a Codex CLI subprocess or billing mutation."""

from __future__ import annotations

import math
from typing import Any

import openai

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


class CodexUsageAuthError(Exception):
    """The usage service rejected the bound token; refresh once before checking."""


def included_usage_allowed(payload: Any) -> bool | None:
    """Read the ordinary allowance, never treating paid credits as allowance.

    The backend's ``rate_limit.allowed`` is its ordinary-usage decision. A
    depleted primary or secondary window also blocks a request, even if credits
    are present. Missing/malformed information is unknown, not permission.
    """
    if not isinstance(payload, dict):
        return None
    limit = payload.get("rate_limit")
    if not isinstance(limit, dict) or type(limit.get("allowed")) is not bool:
        return None
    if not limit["allowed"] or limit.get("limit_reached") is True:
        return False
    for key in ("primary_window", "secondary_window"):
        window = limit.get(key)
        if window is None:
            continue
        if not isinstance(window, dict):
            return None
        used = window.get("used_percent")
        if type(used) not in (int, float) or not math.isfinite(used) or used < 0:
            return None
        if used >= 100:
            return False
    return True


def read_included_usage_allowed(*, client: Any, headers: dict[str, str]) -> bool | None:
    """One authenticated read with the bound account's SDK client; no retries.

    Use the official usage endpoint even when inference uses a proxy: this
    native provider still owns the same OAuth account. Never log response bodies
    or exception text, which may contain account data or credentials.
    """
    try:
        payload = client.with_options(max_retries=0, timeout=10.0).get(
            CODEX_USAGE_URL, cast_to=dict[str, Any], options={"headers": headers}
        )
    except openai.AuthenticationError:
        raise CodexUsageAuthError("Codex usage authentication expired") from None
    except Exception:
        return None
    return included_usage_allowed(payload)
