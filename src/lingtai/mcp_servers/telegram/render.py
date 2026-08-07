"""Deterministic Telegram HTML rendering for system-generated content."""
from __future__ import annotations

from typing import Any

_ESCAPE_TABLE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
_DIVIDER = "──────────"
_TAGS = {"heading": "b", "bold": "b", "italic": "i", "code": "code"}


def html_escape(value: object) -> str:
    return value.translate(_ESCAPE_TABLE) if isinstance(value, str) else ""


def _render_block(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    for kind, value in block.items():
        escaped = html_escape(value)
        if kind in _TAGS:
            tag = _TAGS[kind]
            return f"<{tag}>{escaped}</{tag}>" if escaped else ""
        if kind == "code_block":
            return f"<pre>{escaped}</pre>" if escaped else ""
        if kind == "bullet":
            return f"• {escaped}" if escaped else ""
        if kind == "divider":
            return _DIVIDER
        if kind == "paragraph":
            return escaped
    return ""


def render_structured_blocks(blocks: list[Any]) -> str:
    """Render supported blocks, escaping every caller-provided string."""
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        rendered for block in blocks if (rendered := _render_block(block))
    )
