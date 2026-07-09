## Summary

- Add `manifest.llm.wire_api` for OpenAI-compatible providers (`auto`, `chat_completions`, `responses`).
- Pass request-level `service_tier` through OpenAI-compatible and custom OpenAI-compatible adapters.
- Improve wrong-wire diagnostics by hinting `wire_api="responses"` when a Chat Completions parser receives a non-ChatCompletion shape.
- Update LLM anatomy and focused tests for the new manifest/provider-default fields.

## Motivation

This supports Codex subscription access through sub2api/intermediate OpenAI-compatible providers where the endpoint may need explicit Responses API routing and may expose fast-tier behavior via OpenAI-compatible request fields.

Issue: N/A — direct PR authorized; no tracking issue filed.

## Validation

- `git diff --check`
- `python -m compileall -q src/lingtai src/lingtai_kernel tests/test_openai_compact_threshold.py`
- `pytest -q tests/test_openai_compact_threshold.py` (`37 passed`)

## Local explainer

- `reports/custom-openai-wire-api-service-tier-20260709.html`

## Notes / risks

- `api_compat` remains the protocol-family selector; `wire_api` only selects the OpenAI-family wire shape.
- Backward-compatible `use_responses*` aliases are still accepted, but new presets should prefer `wire_api`.
- No push/open PR has been performed in local prep.
