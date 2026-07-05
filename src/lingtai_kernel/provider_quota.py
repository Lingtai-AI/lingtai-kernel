"""Shared non-secret provider quota diagnostics."""
from __future__ import annotations

import ast
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import time


def exception_payload(exc: BaseException) -> dict | None:
    """Best-effort extraction of a provider error payload."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    try:
        rendered = str(exc)
    except Exception:
        return None
    start = rendered.find("{'error'")
    if start < 0:
        start = rendered.find('{"error"')
    if start < 0:
        return None
    try:
        parsed = ast.literal_eval(rendered[start:])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


QUOTA_HEADER_ALLOWLIST = {
    "retry-after",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "openai-processing-ms",
    "x-should-retry",
    "x-stainless-retry-count",
    "cf-ray",
}


def exception_headers(exc: BaseException) -> dict[str, str]:
    """Return a bounded allowlisted header snapshot for provider errors."""
    candidates = []
    headers = getattr(exc, "headers", None)
    if headers is not None:
        candidates.append(headers)
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            candidates.append(headers)

    out: dict[str, str] = {}
    for candidate in candidates:
        try:
            items = candidate.items()
        except Exception:
            continue
        for raw_key, raw_value in items:
            key = str(raw_key).lower()
            if key not in QUOTA_HEADER_ALLOWLIST:
                continue
            if raw_value is None:
                continue
            out[key] = str(raw_value)[:300]
    return out


def status_code(exc: BaseException) -> int | None:
    for obj in (exc, getattr(exc, "response", None)):
        if obj is None:
            continue
        status = getattr(obj, "status_code", None)
        if status is None:
            continue
        try:
            return int(status)
        except (TypeError, ValueError):
            return None
    return None


def parse_retry_after_seconds(value: object) -> int | None:
    if value is None:
        return None
    rendered = str(value).strip()
    if not rendered:
        return None
    try:
        return max(int(float(rendered)), 0)
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(rendered)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(int(dt.timestamp() - time.time()), 0)
    except Exception:
        return None


def path_label(value: str | None) -> str | None:
    """Return a non-secret, human-usable path label for diagnostics."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        expanded = Path(raw).expanduser()
        home = Path.home()
        try:
            rel = expanded.resolve(strict=False).relative_to(home.resolve(strict=False))
            return "~/" + rel.as_posix()
        except ValueError:
            if expanded.is_absolute():
                digest = hashlib.sha256(str(expanded).encode("utf-8")).hexdigest()[:8]
                name = expanded.name or "path"
                return f"external:{name}:{digest}"
            return raw
    except Exception:
        return raw


def codex_auth_path_diagnostic(defaults: dict | None) -> dict:
    """Return non-secret Codex auth-lane diagnostics."""
    bucket = defaults or {}
    raw = bucket.get("codex_auth_path") if isinstance(bucket, dict) else None
    label = path_label(raw) if raw else "~/.lingtai-tui/codex-auth.json"
    return {
        "codex_auth_path_source": "explicit" if raw else "implicit_default",
        "codex_auth_path_label": label,
    }


def quota_error_extra(
    exc: BaseException,
    *,
    provider: str | None = None,
    service=None,
    session_metadata: dict | None = None,
) -> dict | None:
    """Return bounded structured metadata for provider quota/rate-limit errors."""
    adapter_says_quota = False
    if service is not None:
        try:
            adapter = (
                service.get_adapter(provider)
                if provider
                else service.get_adapter(service.provider)
            )
            adapter_says_quota = bool(adapter.is_quota_error(exc))
        except Exception:
            adapter_says_quota = False

    payload = exception_payload(exc)
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        err = {}

    try:
        rendered = str(exc)
    except Exception:
        rendered = ""
    code_status = status_code(exc)
    headers = exception_headers(exc)
    error_type = err.get("type")
    code = err.get("code")
    message = err.get("message")
    rendered_lower = rendered.lower()
    looks_quota = (
        adapter_says_quota
        or code_status == 429
        or error_type in {"usage_limit_reached", "rate_limit_exceeded"}
        or code in {"usage_limit_reached", "rate_limit_exceeded", "account_stream_cap"}
        or "usage_limit" in rendered_lower
        or "rate limit" in rendered_lower
        or "account_stream_cap" in rendered_lower
        or "429" in rendered
    )
    if not looks_quota:
        return None

    quota: dict = {
        "provider": provider,
        "error_type": error_type or code or type(exc).__name__,
    }
    if code_status is not None:
        quota["status_code"] = code_status
    if message:
        quota["message"] = str(message)[:300]
    for key in ("code", "param", "plan_type", "eligible_promo"):
        if key in err:
            quota[key] = err.get(key)
    for key in ("resets_at", "resets_in_seconds"):
        if key in err and err.get(key) is not None:
            try:
                quota[key] = int(err[key])
            except (TypeError, ValueError):
                quota[key] = err[key]
    if headers:
        quota["headers"] = headers
    if (
        code == "account_stream_cap"
        or error_type == "account_stream_cap"
        or "account_stream_cap" in rendered_lower
    ):
        quota["limit_reason"] = "account_stream_cap"
    if isinstance(quota.get("resets_at"), int):
        try:
            quota["resets_at_iso"] = datetime.fromtimestamp(
                quota["resets_at"], tz=timezone.utc
            ).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    if isinstance(quota.get("resets_in_seconds"), int):
        quota["retry_after_seconds"] = max(quota["resets_in_seconds"], 0)
    else:
        retry_after = parse_retry_after_seconds(headers.get("retry-after"))
        if retry_after is not None:
            quota["retry_after_seconds"] = retry_after

    if str(provider or "").lower() in {"codex", "codex-pool", "codex_pool"}:
        session = session_metadata or {}
        for key in ("codex_auth_path_source", "codex_auth_path_label"):
            value = session.get(key) if isinstance(session, dict) else None
            if value:
                quota[key] = value
    return {"quota": quota}
