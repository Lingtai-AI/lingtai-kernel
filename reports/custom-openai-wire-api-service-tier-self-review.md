# Local self-review: OpenAI-compatible wire controls

Branch: `fix/custom-openai-wire-api-service-tier`
Commit: `b8a878a9`
Base: `origin/main@2a376f5b`

## Readiness gate

- Clean diff: `git diff --check` passed.
- Targeted tests: `pytest -q tests/test_openai_compact_threshold.py` passed (`37 passed`).
- Compile check: `python -m compileall -q src/lingtai src/lingtai_kernel tests/test_openai_compact_threshold.py` passed.
- Anatomy: updated `src/lingtai/llm/ANATOMY.md`, `src/lingtai/llm/custom/ANATOMY.md`, and `src/lingtai/llm/openai/ANATOMY.md` for the new manifest/provider-default surfaces.
- Secrets: no credentials or private token material added.

## Findings

No confirmed blocking defect found in the local self-review.

Residual risks to mention in PR:
- Backward-compatible `use_responses*` aliases still interact with `wire_api`; new presets should prefer `wire_api`.
- `service_tier` is request-level passthrough and may need omission if a provider rejects it.
- Wrong-shaped ChatCompletion mocks now fail earlier with a `wire_api="responses"` hint.

## Decision

Ready for maintainer review once an issue/PR target is confirmed. No push/open PR performed.
