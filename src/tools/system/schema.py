"""Schema — tool registration (get_description, get_schema)."""
from __future__ import annotations


def get_description(lang: str = "en") -> str:
    return 'Runtime inspection, lifecycle control, synchronization, and inter-agent management.\n\nSelf-actions (no permissions needed): sleep, refresh, summarize.\nKarma actions (require admin.karma=True): lull, interrupt, suspend, cpr, clear.\nNirvana (require admin.karma=True AND admin.nirvana=True): nirvana.\n\nNotification verbs (check/dismiss) are NOT here — they live on the standalone notification tool. See system-manual skill for detailed usage of each action.'


def get_schema(lang: str = "en") -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["refresh", "sleep", "lull", "interrupt", "suspend", "cpr", "clear", "nirvana", "presets", "summarize"],
                "description": "refresh: rebuild from init.json — same identity, preserved conversation. Reloads MCP, capabilities, addons, LLM, prompt sections. See system-manual.\n\npresets: list available presets with tags, connectivity, capabilities. See system-manual.\n\nsleep: go to sleep until mail wakes you. Self only.\n\nlull: put another agent to sleep (karma).\n\nsuspend: freeze another agent (karma).\n\ncpr: resuscitate suspended agent (karma).\n\ninterrupt: cancel another agent's turn (karma).\n\nclear: force molt on another agent (karma). See system-manual.\n\nnirvana: permanently destroy an agent (karma + nirvana). See system-manual.\n\nsummarize: record an agent-authored compact replacement for one or more prior tool-result blocks in runtime history. Pass items=[{tool_call_id, summary}, ...]. The original result remains in events.jsonl; the active provider context may still contain the old raw result until a rebuild applies it (manual rebuild=true, or the runtime's forced rebuild at the 1.0 full-context hard boundary). Use after digesting a large result to free context budget. Pass rebuild=true (default false) to also request a provider-context rebuild that applies the pending summaries now; rebuild=true with no items is a pure rebuild of already-pending summaries. Do not loop rebuild/summarize. Choose the tool_call_ids to compress from _meta.agent_meta.current_tool_result_chars.top_results (the ranked list of the largest formal results in context) — large results are surfaced there, not pushed as notifications. (Legacy: if a stale large_tool_result reminder still exists in system.json, a successful summarize of its tool_call_id also clears it.) To read or dismiss notifications, use the notification tool. See system-manual.",
            },
            "reason": {
                "type": "string",
                "description": 'Reason for sleep, refresh, or clear (logged to event log; for clear, becomes the source tag in the recovery summary).',
            },
            "address": {
                "type": "string",
                "description": "Target agent's address (working directory path). Required for interrupt, lull, suspend, cpr, clear, nirvana.",
            },
            "preset": {
                "type": "string",
                "description": "Optional preset to swap to before refreshing. A preset is a {LLM, capabilities} bundle from your library. Use action='presets' to list. Swap is light and reversible. If current context exceeds target preset's context_limit, swap is refused — molt first.",
            },
            "revert_preset": {
                "type": "boolean",
                "description": "Optional. Pass true with action='refresh' to swap back to your default preset (manifest.preset.default — typically the one your agent was created with). Cannot be used together with the 'preset' argument. Useful as a 'home button' after experimenting with another preset, without needing to remember your default's name. Errors if no default is configured.",
            },
            "rebuild": {
                "type": "boolean",
                "description": "For action='summarize' (default false): request a provider-context rebuild that makes recorded summaries active in the active provider context now. With items, summaries are recorded first and then the rebuild is requested; with no items, it is a pure rebuild using the already-pending summaries. When false (the default), summarize only records compact replacements in runtime history and does NOT rebuild the active provider context — the old raw result may still ride the current continuation until a rebuild applies it (a manual rebuild=true, or the 1.0 full-context hard boundary where the runtime forces a rebuild regardless of pending). Prefer one tactical rebuild=true call when context is high (>=0.75 / the context.rebuild hint) or a fresh context is worth the cache-miss cost; do not loop rebuild. Note: rebuild=false with no items is an invalid no-op.",
            },
            "items": {
                "type": "array",
                "description": "Required for action='summarize' unless rebuild=true (a bare rebuild=true rebuilds already-pending summaries with no items). List of items to summarize, each with 'tool_call_id' (the id of the prior tool-result block) and 'summary' (your agent-authored summary text). Supports multiple items per call. The original result is NOT deleted — it remains retrievable from events.jsonl by tool_call_id.",
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
