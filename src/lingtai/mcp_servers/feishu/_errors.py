"""Stable Feishu tool failures above SDK/generated REST error shapes."""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any


# Feishu's reaction endpoint returns this code when ``message_id`` is not a
# valid existing open_message_id. lark-channel-sdk 1.x does not yet include it
# in its target-gone bucket, so keep the compatibility override local here.
_REST_ERROR_OVERRIDES: dict[int, tuple[str, bool]] = {
    99992354: ("TARGET_REVOKED", False),
}


def _public_error_code(value: object, fallback: str = "UNKNOWN") -> str:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not value:
        return fallback
    return value.replace("-", "_").upper()


def _retry_after_seconds(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


class FeishuOperationError(RuntimeError):
    """One classified, non-retried Feishu operation failure."""

    def __init__(
        self,
        message: str,
        *,
        error_code: object = "UNKNOWN",
        retryable: bool = False,
        retry_after_seconds: object = None,
    ) -> None:
        super().__init__(message)
        self.error_code = _public_error_code(error_code)
        self.retryable = bool(retryable)
        self.retry_after_seconds = _retry_after_seconds(retry_after_seconds)


def _response_retry_after(response: object) -> float | None:
    raw = getattr(response, "raw", None)
    headers = getattr(raw, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "retry-after":
            return _retry_after_seconds(value)
    return None


def operation_error_from_response(
    operation: str, response: object,
) -> FeishuOperationError:
    """Classify one unsuccessful generated REST response with SDK rules."""
    from lark_channel import classify_error

    raw_code_value = getattr(response, "code", 0)
    try:
        raw_code = int(raw_code_value or 0)
    except (TypeError, ValueError):
        raw_code = 0
    message = str(getattr(response, "msg", "") or "Feishu API request failed")
    classified = classify_error(raw_code, message)
    override = _REST_ERROR_OVERRIDES.get(raw_code)
    classified_code = override[0] if override else getattr(
        classified, "code", "UNKNOWN",
    )
    classified_retryable = override[1] if override else bool(
        getattr(classified, "retryable", False),
    )
    retry_after = _response_retry_after(response)
    if retry_after is None:
        retry_after = getattr(classified, "retry_after_seconds", None)
    return FeishuOperationError(
        f"Feishu {operation} failed: code={raw_code} msg={message}",
        error_code=classified_code,
        retryable=classified_retryable,
        retry_after_seconds=retry_after,
    )


def operation_error_from_send_result(
    operation: str, result: object,
) -> FeishuOperationError:
    """Preserve the SDK SendError taxonomy at the account boundary."""
    error = getattr(result, "error", None)
    code = getattr(error, "code", "UNKNOWN")
    hint = getattr(error, "hint", None) or "Feishu outbound send failed"
    return FeishuOperationError(
        f"Feishu {operation} failed: "
        f"code={getattr(code, 'value', code) or 'unknown'} msg={hint}",
        error_code=code,
        retryable=bool(getattr(error, "retryable", False)),
        retry_after_seconds=getattr(error, "retry_after_seconds", None),
    )


def failure_result(
    error: BaseException | str,
    *,
    error_code: str | None = None,
    retryable: bool | None = None,
    retry_after_seconds: object = None,
) -> dict[str, Any]:
    """Return the stable public failure shape while retaining ``error`` text."""
    text = str(error) or "Feishu operation failed"
    if isinstance(error, FeishuOperationError):
        resolved_code = error.error_code
        resolved_retryable = error.retryable
        resolved_retry_after = error.retry_after_seconds
    else:
        if error_code is None and isinstance(
            error, (ValueError, TypeError, KeyError, FileNotFoundError),
        ):
            error_code = "INVALID_ARGUMENT"
        resolved_code = _public_error_code(error_code or "UNKNOWN")
        resolved_retryable = bool(retryable) if retryable is not None else False
        resolved_retry_after = _retry_after_seconds(retry_after_seconds)
    return {
        "status": "failed",
        "error": text,
        "message": text,
        "error_code": resolved_code,
        "retryable": resolved_retryable,
        "retry_after_seconds": resolved_retry_after,
    }
