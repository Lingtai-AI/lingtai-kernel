---
name: codex-reasoning-construction
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/CONTRACT.md
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md
  - src/lingtai/kernel/llm/reasoning.py
  - src/lingtai/kernel/llm/base.py
  - src/lingtai/kernel/session.py
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/openai/codex_reasoning.py
  - src/lingtai/llm/openai/adapter.py
  - src/lingtai/kernel/config.py
  - src/lingtai/init_schema.py
  - src/lingtai/kernel/presets.py
  - src/lingtai/agent.py
  - src/lingtai/init.jsonc
  - tests/test_codex_reasoning_contract.py
  - tests/test_session.py
  - tests/test_openai_responses_streaming.py
maintenance: |
  Keep this Codex-specific contract, its paired Anatomy, the provider-neutral
  extension boundary, factory registration, construction/emission seams, config
  guidance, and focused tests synchronized. Do not broaden it into a provider or
  model catalogue. If code and Contract disagree, fail loud and repair one under
  an authorized product decision rather than weakening the promise silently.
---
# Codex reasoning construction

## Purpose

This contract owns reasoning construction only for the native registered
`codex`, `codex-pool`, and `codex_pool` provider route on the Responses wire,
the exact official endpoint `https://chatgpt.com/backend-api/codex` (one trailing
slash is equivalent), and an exact evidence-backed model from the table in
Contract rules. It does not own generic OpenAI Responses, custom endpoints,
unknown models, direct adapter construction, or Chat Completions.

## Behavior

LingTai agents that observe runtime reasoning behavior compare the emitted
`reasoning_*` evidence with this contract, surface mismatches, and preserve
uncertainty; they must not hide a defect by weakening this promise. Coding
agents keep the exact model table, the provider-local implementation, factory
registration, the paired Anatomy, config guidance, and the focused tests
synchronized in the same change, and must not grow this contract into a
provider or model catalogue. The capability is taught by the LLM-adapters
manual reference
`src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md`
(its Codex reasoning-effort section: what `manifest.llm.thinking` selects, how
to set/omit/inspect it, and why validation is provider-local); this contract
states the obligations, the manual reference teaches the procedure.

## Port

The provider-neutral kernel extension point lives in
`src/lingtai/kernel/llm/reasoning.py`: an immutable route key/context
(`ReasoningRouteKey`, `ReasoningRouteContext`), the `ReasoningContract`
protocol, the immutable `ReasoningConstructionResult`, and an opaque
mapping-application patch protocol. It carries no registry: the provider
factory binds one contract directly for its own registered aliases. Shared
kernel code preserves only the omission fact — constructor/manifest omission for Codex-family providers stays the
internal `default` sentinel — and must not know `xhigh`, the nested wire field
path, Codex model tables, or provider defaults.
`ChatSession.reasoning_construction_result()` in
`src/lingtai/kernel/llm/base.py` returns `None` by default. A future route adds
a sibling contract plus factory registration; it must not modify this Codex
policy or create a global catalogue.

## Adapters

`CodexReasoningContract` and the immutable `CodexReasoningPatch`
(`src/lingtai/llm/openai/codex_reasoning.py`) are the provider-local adapter of
this policy. `_register._codex` (`src/lingtai/llm/_register.py`) constructs one
stateless contract and binds it for all three registered provider spellings,
injecting it and the actual alias into `CodexOpenAIAdapter`
(`src/lingtai/llm/openai/adapter.py`), so session construction receives that
same alias in its route context. `CodexResponsesSession` stores the one
immutable result constructed at session construction and applies its patch to
the common source request mapping before REST/WebSocket projection.
`SessionManager` (`src/lingtai/kernel/session.py`) is the observability
adapter: immediately before `llm_call` it reads only the captured result.
Direct adapter construction has no injected contract and keeps the legacy path.

## Contract rules

1. Exact capability table. Accepted tokens are exact and case-sensitive; no
   trimming, case folding, prefix matching, aliases, `none`, or `minimal` are
   inferred. Unsupported values, empty/non-string values (including `None`),
   and booleans fail before dispatch — on the direct adapter route and on the
   main-session kernel route alike, because `SessionManager._session_thinking()`
   forwards every explicit value unchanged instead of substituting a default.
   The literal string `default` is the internal omission sentinel — the only
   spelling that means omission at this contract — and cannot be configured
   literally in manifests or presets.

   | Model | Allowed explicit `thinking` values | Capability source id |
   |---|---|---|
   | `gpt-5.6-sol` | `low, medium, high, xhigh, max, ultra` | `codex_cli_0_144_1_model_metadata` |
   | `gpt-5.6-sol-wm` | `low, medium, high, xhigh, max, ultra` | `codex_cli_0_144_1_model_metadata` |
   | `gpt-5.6-terra` | `low, medium, high, xhigh, max, ultra` | `codex_cli_0_144_1_model_metadata` |
   | `gpt-5.6-luna` | `low, medium, high, xhigh, max` | `codex_cli_0_144_1_model_metadata` |
   | `gpt-5.3-codex-spark` | `low, medium, high, xhigh` | `codex_cli_0_144_1_model_metadata` |
   | `codex-auto-review` | `low, medium, high, xhigh, max` | `codex_cli_0_144_1_model_metadata` |
   | `gpt-5.3-codex` | `low, medium, high, xhigh` | `openai_gpt_5_3_codex_model_docs` |

2. Manifest/preset validation checks only Codex scope, non-empty string type,
   and the reserved `default` sentinel. The model table belongs only to
   `CodexReasoningContract`; custom OpenAI Responses retains its exact
   `none|minimal|low|medium|high|xhigh` validation.
3. Omitted Codex config preserves the internal `default` omission sentinel on
   both the wrapper manifest-hydration path and direct kernel
   `AgentConfig(provider=<codex-family>)` construction; an explicitly supplied
   value — including `"high"`, and including a falsey `""`/`None`/`False`/`0` —
   is never reinterpreted as omission and is never rewritten into one by the
   session seam, so an invalid explicit value fails before dispatch instead of
   silently sending a fabricated effort. The contract deliberately normalizes
   omission/default to LingTai's quality baseline `xhigh`; this is not claimed
   as an upstream default.
4. `_register._codex` constructs one stateless contract and binds it for all
   three registered provider spellings, injecting it and the actual alias
   into `CodexOpenAIAdapter`.
5. Session construction uses the actual selected endpoint and actual model and
   constructs one immutable `ReasoningConstructionResult`. Explicit values
   normalize unchanged with source `explicit_config`; omission captures
   requested omission, normalized/actual `xhigh`, and source
   `lingtai_codex_default`.
6. The immutable provider-local patch owns the exact wire shape
   `reasoning: {effort: <actual>}`. `CodexResponsesSession.send_stream` applies
   it once to the common source request mapping before REST/WebSocket projection.
   Repeated sends reuse the exact result; a rebuilt session constructs a fresh
   result from its new effective config.
7. `ChatSession.reasoning_construction_result()` defaults to `None`. Immediately
   before `llm_call`, `SessionManager` reads only the captured result and may log
   `reasoning_requested`, `reasoning_normalized`, `reasoning_actual`,
   `reasoning_source`, and `reasoning_capability_source`. Omission is rendered as
   `reasoning_requested="omitted"`. No endpoint, account, credential, or opaque
   patch is logged.
8. An endpoint/model that is not an exact descriptor returns no result and
   follows the previous `_responses_reasoning_kwargs` path. Direct
   `CodexOpenAIAdapter` construction has no injected contract and follows that
   same legacy path. Generic and custom Responses behavior is unchanged.

## Contract tests

`tests/test_codex_reasoning_contract.py` proves exact route/model capability
for every descriptor row (full allowlist construction, exact allowlist order
via the pre-dispatch error text, exact capability-source ids, and exact
negatives per vocabulary boundary), omitted/default capture on both the
registered factory route and the kernel `SessionManager`
`ensure_session`/rebuild main-session route, immutable result/patch ownership,
per-alias contract binding, result reuse/rebuild, REST/WS parity,
pre-dispatch rejection, legacy/direct fallthrough, create-chat/generate
construction parity, and this contract's own governed-child governance (root
index, headings, shared manual edge). `tests/test_session.py` proves exact safe
`llm_call` observation and no-result field preservation.
`tests/test_openai_responses_streaming.py` preserves generic and direct legacy
Responses behavior.

## Maintenance

Governed by the root `CONTRACT.md`. Keep `related_files` complete and
repo-relative, including the paired `src/lingtai/llm/openai/ANATOMY.md`, the
kernel Port module, the provider-local adapter files, contract tests, and the
`llm-adapters` manual reference route (carried by both owner twins). Update the Port, affected
adapters, tests, and this contract together when a boundary or normative
behavior changes; update the paired Anatomy when structure changes. Report
pairing/ownership mismatches instead of auto-fixing them, and never weaken the
promise to match accidental behavior.
