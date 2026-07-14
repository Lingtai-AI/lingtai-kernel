---
related_files:
- src/lingtai/llm/openai/adapter.py
- src/lingtai/llm/openai/codex_ws.py
- src/lingtai/llm/openai/ANATOMY.md
maintenance: |
  Historical wire-shape investigation report (dated 2026-06-22, PR #454 era); content is frozen as a point-in-time capture — do not rewrite retroactively, but correct factual errors and note in-repo if the adapter's header/body shape it describes later diverges.
---

# Codex HTTP anatomy investigation: LingTai vs. Codex CLI

Date: 2026-06-22  
Scope: ChatGPT-backed Codex Responses requests emitted by LingTai and by the
local Codex CLI (`codex-cli 0.130.0`).

## Why this document exists

LingTai's Codex provider had already moved to the native Responses route and had
recently gained stable cache-affinity headers and honest client identity. A local
wire-shape investigation compared LingTai with the official Codex CLI to answer
three engineering questions:

1. Which endpoint/body shape do both clients use?
2. Which identity/cache/account/metadata headers are present in Codex CLI but
   absent in LingTai?
3. Which fields can LingTai add honestly without impersonating Codex CLI?

This document records the investigation so the adapter anatomy can point to a
stable reference instead of re-explaining the capture history inline.

## Capture method and safety

The investigation intentionally avoided TLS MITM and did not forward either
capture to the real OpenAI/ChatGPT upstream. Both clients were routed to local
fake Responses endpoints on `127.0.0.1`; each fake endpoint recorded the request
and returned a minimal SSE response.

- LingTai capture: invoked the real local `CodexOpenAIAdapter` against a fake
  `base_url` ending in `/backend-api/codex`.
- Codex CLI capture: invoked installed `codex exec` v0.130.0 with a local fake
  model provider configured as `wire_api='responses'` and `OPENAI_API_KEY` set to
  a fake value.
- Secrets and stable account identifiers were redacted in shareable artifacts.
- Raw local artifacts were kept outside the repository; this document records the
  reproducible structure, not secrets.

## Endpoint shape

| Client | Captured method/path | Real/default interpretation |
|---|---|---|
| LingTai native Codex adapter | `POST /backend-api/codex/responses` | Corresponds to `https://chatgpt.com/backend-api/codex/responses`. |
| Codex CLI v0.130.0, fake provider | `POST /v1/responses` | The forced provider used OpenAI-style `/v1/responses`; the header/body structure came from the real CLI process. |

Both clients use a Responses-style request body and streaming SSE response shape,
not Chat Completions.

## LingTai request shape before the metadata PR

A minimal no-tool LingTai capture sent:

- Headers: `Authorization`, `originator: lingtai`, `User-Agent: LingTai/<version>`,
  Python OpenAI SDK `X-Stainless-*`, and cache-affinity headers
  `session_id` / `thread_id` when a stable Codex identity exists.
- Body keys: `model`, `instructions`, `input`, `reasoning`, `store: false`,
  `stream: true`, `include: ["reasoning.encrypted_content"]`, and
  `prompt_cache_key`.
- Cache affinity: `prompt_cache_key == session_id == thread_id == <stable
  per-agent id>` in LingTai's normal root-agent path.

PR #454 separately added an honest `ChatGPT-Account-ID` header when the user's
own account id can be derived from Codex OAuth/auth metadata. It preserves
`originator: lingtai` and does not impersonate Codex CLI.

## Codex CLI v0.130.0 request shape observed locally

The local CLI capture sent these notable headers:

- `originator: codex_exec`
- `User-Agent: codex_exec/0.130.0 (...)`
- `chatgpt-account-id`
- `session_id` and `thread_id` as UUID-shaped values
- `x-client-request-id`
- `x-codex-window-id`, observed as `<session_uuid>:0`
- `x-codex-turn-metadata`, JSON containing session/thread/turn/sandbox/timestamp
- `x-codex-beta-features: terminal_resize_reflow`

Its body included the common Responses fields plus CLI-specific scaffolding:
`tools`, `tool_choice: auto`, `parallel_tool_calls: true`, `text.verbosity`, and
`client_metadata.x-codex-installation-id`.

The blog post that motivated the check described `codex_cli_rs`; this machine's
installed CLI v0.130.0 used `codex_exec`. LingTai must treat the exact string as
version-specific implementation detail, not as an identity to copy.

## Fields LingTai can add honestly

LingTai can add a small compatibility metadata envelope without pretending to be
Codex CLI:

| Field | LingTai meaning |
|---|---|
| `x-client-request-id` | Fresh UUID per request. |
| `x-codex-window-id` | `<session_id>:0` for LingTai's current single-window Codex session model. |
| `x-codex-turn-metadata` | Compact JSON with `session_id`, `thread_id`, generated `turn_id`, truthful `sandbox`, and `turn_started_at_unix_ms`. |
| `client_metadata.x-codex-installation-id` | UUID-shaped LingTai installation id derived from LingTai's own non-secret anchor/id. |

The OpenAI Python SDK exposes no typed `client_metadata` argument on
`responses.create`, so LingTai passes it via `extra_body={"client_metadata": ...}`.

## Fields LingTai should not copy blindly

- Do not change `originator` or `User-Agent` to official CLI values.
- Do not use `~/.codex/installation_id`; LingTai derives its own id.
- Do not send `x-codex-beta-features: terminal_resize_reflow` unless LingTai
  actually implements and intends to advertise that feature.
- Do not invent attestation or other official-CLI-only fields.

## Implementation notes

The metadata PR extends `CodexResponsesSession` in `src/lingtai/llm/openai/adapter.py`:

1. Build cache-affinity headers exactly as before: literal underscore
   `session_id` / `thread_id`.
2. Add metadata headers only when both session and thread identity are present.
3. Generate request and turn ids per request.
4. Keep `ChatGPT-Account-ID` behavior from PR #454 independent of the metadata
   envelope.
5. Add body `client_metadata` through `extra_body` and preserve any existing
   caller-supplied `extra_body.client_metadata` keys.
6. Keep tests focused on the separation between stable cache/identity fields and
   intentionally variable per-request metadata.

## Validation performed during development

Before merging PR #454:

- Targeted Codex auth/cache/reasoning tests: `70 passed`.
- Full kernel pytest: `2548 passed, 4 skipped`.
- PR #454 merged as `bd72689`.

For the metadata PR before final merge:

- `tests/test_codex_prompt_cache_key.py`: `40 passed`.
- Codex auth/cache/raw-reasoning targeted suite: `72 passed`.
- Full kernel pytest should be run before merge and recorded in the PR/merge
  report.

## Design principle

The compatibility goal is not to look like Codex CLI. It is to give the Codex
backend honest, useful metadata that LingTai can actually stand behind while
keeping LingTai's own identity explicit.
