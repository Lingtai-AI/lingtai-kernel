---
id: token_efficiency
title: Token efficiency state
kind: meta-guidance-section
summary: >
  Resident guidance for interpreting `_meta.agent_meta.token_efficiency` as current-session
  context and cache economy.
why: >
  This fragment exists because token/caching numbers are dynamic runtime scalars; agents need a
  stable interpretation hook without repeating the full token-efficiency procedure in each tool
  result.
related_files:
  - "src/lingtai/prompts/principle.md"
  - "src/lingtai/prompts/guidance/INDEX.md"
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
Read `_meta.agent_meta.token_efficiency` as current-session token economy state, not lifetime or project accounting. It is explicitly scoped by `scope: current_session` and includes `api_calls`, cumulative `input_tokens`, cumulative `cached_tokens`, `cache_rate` (cached/input as a 0-1 fraction), `avg_input_tokens_per_api_call`, `context_tokens`, `context_window`, and `guidance_ref`. Use `context_tokens` and `context_window` directly: rising context means the current session is carrying more into each provider request. For historical per-provider-round facts, inspect `_meta.tool_meta.token_usage` on individual tool results instead of the latest-only agent_meta snapshot; it records input, cache-miss, output/thinking/cached tokens, cache rate, and context usage so agents can see repeated high-context summarize/reconstruction costs after the latest state has moved on. Apply the token-efficiency principle from the system prompt prefix: summarize already-consumed tool results when continuing, use daemons before bulky work enters main context, and treat task-boundary molt as a costed decision. At a completed task boundary, default to proactive molt only when current-session `api_calls > 100`; below that threshold, go idle unless context pressure, explicit human request, or conversation confusion makes the molt worth its cost.
