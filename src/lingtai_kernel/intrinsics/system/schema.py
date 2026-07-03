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
            "rebuild_only": {
                "type": "boolean",
                "description": "For action='summarize': request a one-shot provider-context rebuild that makes already-recorded summaries active in the provider context now, without summarizing any new items. Use with no items. Ordinary summarize only records compact replacements in runtime history; it does NOT rebuild the active provider context on its own. This is the explicit path (offered by the 75% context.rebuild hint) to apply recorded summaries sooner than the automatic 95% reconstruction.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Alias for rebuild_only for action='summarize'. NOT a no-op simulation despite the name: it performs no compression but does request a real one-shot provider-context rebuild, exactly like rebuild_only=true. Use with no items.",
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
