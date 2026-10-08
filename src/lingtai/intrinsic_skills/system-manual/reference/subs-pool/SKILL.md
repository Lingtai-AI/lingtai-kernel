---
name: subs-pool
description: >
  Nested system-manual reference pointing to subs-pool, the external Codex
  subscription pool. LingTai has no built-in Codex account pool; use this to
  pool several ChatGPT/Codex accounts behind one local Responses endpoint
  reached through provider `openai` with `wire_api: responses`, or to migrate a
  removed `codex-pool`/`custom` provider config.
version: 0.2.0
last_changed_at: "2026-09-29T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md
- src/lingtai/init_schema.py
- src/lingtai/llm/_register.py
maintenance: |
  Pointer only. The subs-pool repository owns install, account management,
  quota, and proxy behavior; keep this file to the LingTai-side configuration
  shape and update it only when that shape or the repository location changes.
---
# subs-pool

LingTai does not pool Codex accounts. The `codex` provider binds exactly one
OAuth account (`codex_auth_path`, else `~/.lingtai-tui/codex-auth.json`). The
former `codex-pool` / `codex_pool` providers were removed and now fail init
validation.

Account pooling lives in **subs-pool**: https://github.com/Lingtai-AI/subs-pool
— a standalone Codex subscription pool with a loopback Responses proxy. Follow
its README to install it, import accounts, and run
`subspool codex serve --listen 127.0.0.1:<port>` with a local access key
(`CODEX_POOL_API_KEY`).

## Point an agent at it

To LingTai the proxy is a plain OpenAI-compatible Responses endpoint, so it is
reached through the generic `openai` provider (the `openai` Responses wire is
always stateless full-history replay):

```json
"llm": {
  "provider": "openai",
  "wire_api": "responses",
  "base_url": "http://127.0.0.1:<port>/v1",
  "api_key_env": "<ENV_VAR_WITH_THE_LOCAL_ACCESS_KEY>",
  "model": "<codex model>",
  "thinking": "xhigh"
}
```

- Put the access key in the agent's `env_file` under that variable name.
- `thinking` is sent verbatim as the standard `reasoning.effort`; omit it to
  let the endpoint apply its own default.
- The normal tier is the default; add `"service_tier": "fast"` (sent as the
  standard `priority`) only when you want the priority tier. `auto`, `default`,
  `flex`, and `priority` are also accepted verbatim; any other value fails
  validation.
- The same pattern reaches any other subscription pool or gateway (for example
  sub2api): provider `openai` + `base_url` (+ `wire_api`) for an
  OpenAI-compatible endpoint, or provider `anthropic` + `base_url` for an
  Anthropic-compatible one. LingTai ships no per-vendor adapters; the removed
  `custom` provider and its `api_compat` key are no longer accepted (`api_compat`
  is ignored as a legacy key; `provider: "custom"` fails validation).
- Vision capabilities that named `codex-pool` must name another provider
  (e.g. `codex`, or `openai` with the same `base_url` and key where the endpoint
  supports images).
- Account selection, quota, and failover are the proxy's job; diagnose them
  with `subspool codex status` / `subspool-cli codex quota`, not LingTai logs.
