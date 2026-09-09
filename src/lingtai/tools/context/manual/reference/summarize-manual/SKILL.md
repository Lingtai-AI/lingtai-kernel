---
name: summarize-manual
description: >-
  Operational guide for recording tool-result summaries, applying them with a full rebuild, recovering raw evidence, and choosing summarize versus molt.
last_changed_at: "2026-09-08T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/tools/system/summarize.py
- src/lingtai/prompts/meta_guidance/catalog/summarize_best_practice.md
- src/lingtai/prompts/meta_guidance/catalog/summarize_reconstruction_threshold.md
maintenance: |
  Tracks the summarize-manual topic it documents; update when that integration changes.
---

# Summarize Manual

Use this reference after you have inspected a bulky tool result and know what a future turn must retain, or when current Context guidance asks you to compact consumed results. It is the focused procedure; resident `meta_guidance` remains the source for current pressure timing.

## Choose the operation

- `context(action="summarize")` is **record-only**. It replaces selected prior tool-result blocks in runtime history with your agent-authored summaries, marks them pending, and preserves the raw event for recovery. The active provider continuation may still carry the raw block.
- `context(action="rebuild")` is the one active application path. It first re-reads and recomposes **all** canonical prompt sources, then records/applies supplied and pending summaries, then requests provider replay with the new prompt and history. Bare `input={}` is valid, including with no pending summaries.
- `context(action="molt")` is the whole-session boundary. Choose it when pressure, an explicit human reset request, or conversation confusion makes a fresh briefing worthwhile; do not use it as task-completion housekeeping.

The root `summarize` boolean is a separate generic result-presentation control. It is not the `context` action, is not an item-list input, and does not apply a recorded summary to provider context. The action/schema descriptions are authoritative for fields and requiredness.

## A-priori root result summaries

Before a call, root `summarize=true` requests a generated, noncanonical, lossy summary; put the retention specification in `reasoning` and preserve its `raw_locator` (and producer ID) for recovery. If generation fails, it is fail-closed: no raw payload is returned, so rerun narrowly or with `summarize=false`. Check `summary_effect.prev_chars`, `after_chars`, and `saved_chars`; weak savings are a lesson to sharpen the retention specification or choose a narrower call.

## Record a summary

Summarize only a completed result you have read. Pick targets from the newest `_meta.agent_meta.agent_state.current_tool_result_chars.top_results` IDs. In each item, `tool_call_id` is the producer call ID, not the visible `_tool_call_id` event reference; preserve any `raw_locator` or spill path. Preserve the conclusion, evidence or error, paths/URLs/IDs, validation status, risks, and next step—enough to decide whether the raw result must be reopened. Batch several already-digested results when useful:

```json
{
  "action": "summarize",
  "input": {
    "items": [
      {"tool_call_id": "call_abc123", "summary": "Conclusion, evidence, anchors, risks, and next step."}
    ]
  },
  "reasoning": "Keep the evidence and recovery anchor needed by the next turn."
}
```

Recording is not durable-store authoring and does not rebuild. Do not summarize text that still needs exact inspection, quotation, patching, or comparison. If the result is still ambiguous, reopen it first.

## Apply and recover

Call one `context(action="rebuild", input={})` when making recorded summaries or a durable prompt-source edit active is worth a provider replay. Reconstruction is deliberately delayed for cache efficiency; do not loop summarize/rebuild calls. If composition fails, the Context contract leaves summaries unapplied and reports the reconstruction error.

The rebuild receipt reports the provider round that requested the rebuild; post-rebuild context usage does not exist yet in that receipt. Observe it on the next provider round.

Keep the original `tool_call_id` in any summary. The normal fallback is a narrow JSONL search:

```bash
rg 'call_abc123' <workdir>/logs/events.jsonl
```

For a spilled result, preserve and reopen its `tmp/tool-results/` path. Use `system(action="manual", input={}, reasoning="load System routes")` for settings, cache-miss budget ownership, and current automatic threshold details; this reference does not duplicate System-owned numbers or configuration procedures.

When the current pressure reminder is active, follow the resident cadence: make at most one useful summarize/rebuild pass, then stop repeating it and molt deliberately if the context remains too high. The pressure reminder is a decision aid, not an automatic molt order. A deliberate molt still requires the Context manual's journal gate and concise successor handoff.
