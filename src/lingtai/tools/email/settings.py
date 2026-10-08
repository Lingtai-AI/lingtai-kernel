"""Source-backed display rows for Email's read-only settings action."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from ..tool_family import SettingRow

# Installed-code policies. Operational consumers import these constants so
# SHOW cannot drift from the values that actually govern Email behavior.
EMAIL_BODY_CHAR_LIMIT = 50_000
EMAIL_CHECK_RESULT_TOKEN_LIMIT = 10_000
EMAIL_UNREAD_MAX_ENTRIES = 10


_FIXED_ROWS = (
    SettingRow(
        "send.body_char_limit",
        EMAIL_BODY_CHAR_LIMIT,
        EMAIL_BODY_CHAR_LIMIT,
        False,
        "email-manual#send-body-character-limit",
    ),
    SettingRow(
        "check.result_token_limit",
        EMAIL_CHECK_RESULT_TOKEN_LIMIT,
        EMAIL_CHECK_RESULT_TOKEN_LIMIT,
        False,
        "email-manual#check-result-token-limit",
    ),
    SettingRow(
        "unread.max_entries",
        EMAIL_UNREAD_MAX_ENTRIES,
        EMAIL_UNREAD_MAX_ENTRIES,
        False,
        "email-manual#unread-notification-entry-limit",
    ),
)


def email_settings_rows(
    read_pseudo_agent_subscriptions: Callable[[], Sequence[str]] | None = None,
) -> tuple[SettingRow, ...]:
    """Return Email's effective construction snapshot or fail as one inventory."""
    if read_pseudo_agent_subscriptions is None:
        raise RuntimeError("Email pseudo-agent subscription snapshot is unavailable")
    subscriptions = read_pseudo_agent_subscriptions()
    if isinstance(subscriptions, (str, bytes)) or not isinstance(
        subscriptions, Sequence
    ):
        raise RuntimeError("Email pseudo-agent subscription snapshot is invalid")
    if not all(isinstance(item, str) for item in subscriptions):
        raise RuntimeError("Email pseudo-agent subscription snapshot is invalid")
    return (
        *_FIXED_ROWS,
        SettingRow(
            "manifest.pseudo_agent_subscriptions",
            list(subscriptions),
            ["../human"],
            True,
            "email-manual#pseudo-agent-subscriptions",
            _sensitive=True,
        ),
    )
