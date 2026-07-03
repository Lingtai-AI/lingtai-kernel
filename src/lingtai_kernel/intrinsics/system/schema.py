"""Schema — tool registration (get_description, get_schema)."""
from __future__ import annotations


def get_description(lang: str = "en") -> str:
    from ...i18n import t
    return t(lang, "system_tool.description")


def get_schema(lang: str = "en") -> dict:
    from ...i18n import t
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["refresh", "sleep", "lull", "interrupt", "suspend", "cpr", "clear", "nirvana", "presets", "summarize"],
                "description": t(lang, "system_tool.action_description"),
            },
            "reason": {
                "type": "string",
                "description": t(lang, "system_tool.reason_description"),
            },
            "address": {
                "type": "string",
                "description": t(lang, "system_tool.address_description"),
            },
            "preset": {
                "type": "string",
                "description": t(lang, "system_tool.preset_description"),
            },
            "revert_preset": {
                "type": "boolean",
                "description": t(lang, "system_tool.revert_preset_description"),
            },
            "rebuild": {
                "type": "boolean",
                "description": "For action='summarize' (default false): request a provider-context rebuild that makes recorded summaries active in the active provider context now. With items, summaries are recorded first and then the rebuild is requested; with no items, it is a pure rebuild using the already-pending summaries. When false (the default), summarize only records compact replacements in runtime history and does NOT rebuild the active provider context — the old raw result may still ride the current continuation until the automatic 95% reconstruction. Prefer one tactical rebuild=true call when context is high (>=0.75 / the context.rebuild hint) or a fresh context is worth the cache-miss cost; do not loop rebuild. Note: rebuild=false with no items is an invalid no-op.",
            },
            "items": {
                "type": "array",
                "description": t(lang, "system_tool.items_description"),
                "items": {
                    "type": "object",
                    "properties": {
                        "tool_call_id": {
                            "type": "string",
                            "description": "The id of the prior tool-result block to summarize.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Your agent-authored summary of that tool result.",
                        },
                    },
                    "required": ["tool_call_id", "summary"],
                },
            },
        },
        "required": ["action"],
    }
