---
name: vision-contract
tool: vision
contract_version: 1
related_files:
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/tools/vision/manual/SKILL.md
maintenance: |
  Keep this contract aligned with the vision tool and its tests. Bump the
  version only for a repository-policy-required breaking contract change.
---
# Vision capability contract

`vision` analyzes one image through the active preset's current compatible route.
If direct setup is absent, unsupported, or fails, setup still registers the tool
and preserves a read-only `action="manual"` route. It never changes provider or
automatically invokes MCP.

## Scope and registry

The schema has optional `image_path`, `question`, and `action`; `action` is
`analyze` by default or `manual`. Manual works without `image_path`; analyze
requires it and resolves relative paths against the agent working directory.

`PROVIDERS["providers"]` is exactly: `gemini`, `anthropic`, `openai`,
`openrouter`, `custom`, `deepseek`, `minimax`, `mimo`, `glm`, `zhipu`, `grok`,
`qwen`, `kimi`, `codex`, `codex-pool`, `codex_pool`, `claude-code`, and
`claude_code`. The local mlx-vlm pseudo-provider remains available only through
explicit `add_capability(..., provider="local")` opt-in and is intentionally not
advertised to wizards/check-caps. Claude Code is manual-only; Codex aliases use
native Codex Responses; MiniMax uses the Anthropic route. OpenRouter and custom
deliberately try the current OpenAI-compatible model/endpoint/credential without
preflighting image support; other compatible aliases use the current
OpenAI/Anthropic identity. A real request failure is returned as a sanitized
vision tool error that points to `vision(action="manual")` for explicit
alternatives, without silently switching model/provider or invoking MCP.

## Current identity and wires

Direct routes inherit identity only from the same current provider (including the
explicit GLM/Zhipu and codex-pool spelling pairs); a different provider must supply
its own model and credential. Missing identity fails closed to `manual` instead of
using a service default model, a default OAuth path, or an SDK environment key.
A generic `codex` request follows the active Codex service's route: over an active
`codex-pool`/`codex_pool` service it takes the pool route, preserving the active
pool model/endpoint and passing the exact pool-selected credential reference (the
selected candidate's token path) to the native Codex vision service; over an active
direct `codex` service it keeps the explicit `codex_auth_path` route and never
borrows pool accounts. Explicit `codex-pool`/`codex_pool` spellings share the
active identity only with an active pool; over an active direct or unrelated
service they do not inherit its model or direct auth and fail closed to `manual`
unless a complete explicit capability identity is supplied independently per the
preceding rule. Any Codex request over an unrelated provider, or a missing
pool-selected credential reference, likewise fails closed without manufacturing an
identity.
OpenAI preserves current default headers, endpoint, model, and `wire_api`.
A missing, blank/whitespace-only, or `auto` selector means automatic selection:
the current route uses Responses only when it explicitly prefers Responses and
has no custom base URL; otherwise it uses Chat Completions. Unknown nonblank or
non-string selectors remain manual-only. Responses sends `max_output_tokens`.
MiniMax→Anthropic preserves active headers. MiMo accepts only API key/model/base
URL/max tokens: blank/auto resolves to its current Chat Completions route, which
constructs without headers/wire kwargs, while an active unsupported wire remains
manual-only.

## Tool behavior

Success is `{status: "ok", analysis: text}`. Manual success is
`{status: "ok", action: "manual", manual: body}`; missing manual is degraded.
Missing image, empty response, setup failure, and request failure are structured
errors pointing to `vision(action="manual")`. Exception messages are never
returned; failures may include only the provider and exception type.

## Invariants and tests

- `setup` always registers the tool: `tests/test_vision_capability.py`.
- Endpoint identity is sanitized by `sanitize_endpoint` and drops userinfo,
  query, fragment, malformed ports, and non-URLs: `tests/test_agent_preset_manifest.py`.
- Provider construction and exact OpenAI Responses shape are covered in
  `tests/test_vision_capability.py`.
- Manual guidance is provider-neutral and kernel/TUI-independent in
  `manual/SKILL.md`.

Run `python -m pytest tests/test_vision_capability.py tests/test_vision_services.py -q`
and the glossary validator before merging.
