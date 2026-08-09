"""Native Telegram Rich Message rendering for system-generated content."""
from __future__ import annotations

from typing import Any

_FIELDS = {"title", "summary", "facts", "bullets", "steps", "code", "next", "footer"}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(message: dict[str, Any], field: str) -> str | None:
    if field not in message:
        return None
    return _text(message[field], field)


def _string_list(message: dict[str, Any], field: str) -> list[str]:
    value = message.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return [_text(item, field) for item in value]


def _facts(message: dict[str, Any]) -> list[tuple[str, str]]:
    value = message.get("facts", [])
    if not isinstance(value, list):
        raise ValueError("facts must be a list of label/value objects")
    result: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"label", "value"}:
            raise ValueError("facts must contain only label/value objects")
        result.append((
            _text(item["label"], "facts.label"),
            _text(item["value"], "facts.value"),
        ))
    return result


def _next(message: dict[str, Any]) -> tuple[str, str] | None:
    value = message.get("next")
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"label", "text"}:
        raise ValueError("next must contain exactly label and text")
    return _text(value["label"], "next.label"), _text(value["text"], "next.text")


def _code(message: dict[str, Any]) -> tuple[str, str | None] | None:
    value = message.get("code")
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not set(value).issubset({"text", "language"})
        or "text" not in value
    ):
        raise ValueError("code must contain text and optional language")
    language = _text(value["language"], "code.language") if "language" in value else None
    return _text(value["text"], "code.text"), language


def _validated(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ValueError("structured_message must be an object")
    unknown = set(message) - _FIELDS
    if unknown:
        raise ValueError(
            f"structured_message has unknown fields: {', '.join(sorted(unknown))}"
        )
    return {
        "title": _text(message.get("title"), "title"),
        "summary": _optional_text(message, "summary"),
        "facts": _facts(message),
        "bullets": _string_list(message, "bullets"),
        "steps": _string_list(message, "steps"),
        "code": _code(message),
        "next": _next(message),
        "footer": _optional_text(message, "footer"),
    }


def _paragraph(text: Any) -> dict[str, Any]:
    return {"type": "paragraph", "text": text}


def _list_item(text: Any, *, position: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"blocks": [_paragraph(text)]}
    if position is not None:
        item.update({"type": "1", "value": position})
    return item


def _label_value(label: str, value: str) -> list[Any]:
    return [{"type": "bold", "text": f"{label}："}, value]


def _build(data: dict[str, Any]) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "text": data["title"], "size": 2},
    ]
    if data["summary"]:
        blocks.append(_paragraph(data["summary"]))

    if any(data[field] for field in ("facts", "bullets", "steps", "code", "next")):
        blocks.append({"type": "divider"})
    if data["facts"]:
        blocks.append({
            "type": "list",
            "items": [
                _list_item(_label_value(label, value))
                for label, value in data["facts"]
            ],
        })
    if data["bullets"]:
        blocks.append({
            "type": "list",
            "items": [_list_item(item) for item in data["bullets"]],
        })
    if data["steps"]:
        blocks.append({
            "type": "list",
            "items": [
                _list_item(item, position=index)
                for index, item in enumerate(data["steps"], 1)
            ],
        })
    if data["code"]:
        code, language = data["code"]
        block = {"type": "pre", "text": code}
        if language:
            block["language"] = language
        blocks.append(block)
    if data["next"]:
        blocks.append(_paragraph(_label_value(*data["next"])))
    if data["footer"]:
        blocks.append({"type": "footer", "text": data["footer"]})
    return {"blocks": blocks}


def _preview(data: dict[str, Any]) -> str:
    lines = [data["title"]]
    if data["summary"]:
        lines.append(data["summary"])
    lines.extend(f"{label}：{value}" for label, value in data["facts"])
    lines.extend(f"• {item}" for item in data["bullets"])
    lines.extend(f"{index}. {item}" for index, item in enumerate(data["steps"], 1))
    if data["code"]:
        lines.append(data["code"][0])
    if data["next"]:
        lines.append(f"{data['next'][0]}：{data['next'][1]}")
    if data["footer"]:
        lines.append(data["footer"])
    return "\n".join(lines)


def render_structured_message(
    message: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Validate once, then return native blocks and their searchable preview."""
    data = _validated(message)
    return _build(data), _preview(data)


def build_rich_message(message: dict[str, Any]) -> dict[str, Any]:
    """Build one ``InputRichMessage`` while preserving agent-authored wording."""
    return _build(_validated(message))


def plain_text_preview(message: dict[str, Any]) -> str:
    """Return searchable text for duplicate detection and sent-message history."""
    return _preview(_validated(message))
