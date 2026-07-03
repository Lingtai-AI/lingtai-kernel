---
id: summarize_reconstruction_threshold
title: Delayed summarization reconstruction threshold
kind: meta-guidance-section
summary: >
  Resident guidance explaining that summarize records compact history immediately but
  provider-context reconstruction happens later at the threshold.
why: >
  This fragment exists so agents do not waste calls trying to force summarize reconstruction, do
  not assume raw blocks vanished too early, and know when to molt instead.
related_files:
  - "src/lingtai/prompts/principle.md"
  - "src/lingtai/prompts/guidance/INDEX.md"
  - "reference/summarize-manual/SKILL.md"
maintenance: >
  When editing this file, treat related_files as maintained inner links for the prompt/guidance
  source graph. Before changing behavior or prose, crawl the listed files, update any affected
  reciprocal link on the other side (principle links to each prompt/guidance source; each such
  source links back to principle; guidance INDEX links to each guidance section and each section
  links back to INDEX), and keep this list generous enough for future maintainers to find adjacent
  prompt layers. Do not list tests merely because they validate the contract; add loaders,
  manifests, or package metadata only when this file actually discusses them or the prompt-source
  relation needs that link.
---
Summarize has two mechanisms agents must distinguish. First, a successful summarize records an agent-authored replacement in runtime history (and, for legacy compatibility, clears a matching `large_tool_result` reminder if one is still present from before such reminders were retired). That bookkeeping does not mean the active provider-side context the agent is continuing from has been rebuilt, and the agent should not assume the old raw block has disappeared from its current continuation. Ordinary summarize records-only, stamping the marker `status: pending`: even above `0.75`, recording a summary does not by itself rebuild the active provider context. Below the full-context boundary, pending summarized history may remain at the provider layer while the session keeps appending to the existing conversation; this is normal. Do not call `refresh` just to apply a summarize. Once context is at/above `0.75`, `_meta.tool_meta.context.rebuild` is a decision prompt / permission (not an automatic rebuild): it lets you make already-recorded summaries active sooner via a deliberate `system(action="summarize", rebuild=true)` call — with new items (record then apply the pending set) or with no items (pure rebuild of the already-pending summaries) — when paying for an earlier rebuild is worth it; applied summaries flip to `status: done`; do not loop rebuild/summarize. At context usage `1.0` (the full-context hard boundary) the runtime forces a provider-context rebuild / fresh replay on the next request regardless of whether pending summaries exist: pending markers are applied and marked done, and even with no pending summaries the rebuild still runs to release transient context. Every `1.0` forced rebuild ALWAYS attaches a one-shot `reconstruction.warning` (before→after context, proactive-`0.75`-rebuild advice, and "if still above the `0.6` recovery target, tend durable stores and molt"). Waiting until full context is not ideal — prefer the proactive `0.75` rebuild; if the pending total is `0`, the forced rebuild has nothing to apply, so summarize more digested results or molt instead. If summarize or rebuild still cannot bring context below `0.6 * context_window`, tend durable stores and molt deliberately. If you have already decided to molt, skip pre-molt summarize and molt instead. The resident rule is the operational mechanism; the rationale and edge cases live in `system-manual` → `reference/summarize-manual/SKILL.md`.
