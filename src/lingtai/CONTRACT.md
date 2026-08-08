---
name: init-reader
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/init.jsonc
  - src/lingtai/init_reader.py
  - src/lingtai/init_schema.py
  - src/lingtai/kernel/config_resolve.py
  - src/lingtai/cli.py
  - src/lingtai/agent.py
  - src/lingtai/kernel/workdir.py
  - src/lingtai/kernel/nudge/ANATOMY.md
  - src/lingtai/kernel/nudge/__init__.py
  - src/lingtai/kernel/nudge/init_config.py
  - ENVIRONMENT_VARIABLES.md
  - tests/test_init_reader.py
  - tests/test_cli.py
  - tests/test_deep_refresh.py
  - tests/test_nudge_inline_cap.py
  - tests/test_agent_config_hydration.py
  - tests/test_init_schema.py
  - tests/test_preset_materialization.py
  - tests/test_presets.py
  - tests/test_kimi_code_effort.py
  - src/lingtai/llm/kimi_code/effort.py
  - src/lingtai/kernel/config.py
maintenance: |
  Keep related_files complete and repo-relative: the paired ANATOMY.md, the
  canonical init.jsonc, real reader/writer/validator code, affected composition
  roots, manual route, and focused tests belong here. Update this Contract and
  its paired Anatomy together when reader behavior, compatibility promises,
  ownership, state, or retirement conditions change. If code and Contract
  disagree, fail loud and repair the implementation or obtain an authorized
  contract change; never hide the mismatch by weakening the promise.
---
# Init reader

## Purpose

The kernel repository owns the semantic source for `init.jsonc` and its local
`init.json` reader contract. `src/lingtai/init.jsonc` is the canonical/normal
shape for all new writes and examples. Compatibility shapes are documented in
that same JSONC file and accepted only while reading legacy local files.

## Behavior

The boot CLI and live-refresh `Agent` use one real reader path:
parse JSON/JSONC → materialize the active preset in memory → apply existing
capability preparation → validate with `init_schema.validate_init` → resolve
paths in memory → publish the existing secret-redacted
`system/manifest.resolved.json` artifact. The reader returns
`FULLY_EFFECTIVE`, `READ_OK_WITH_IGNORED_FIELDS`, or `READ_FAILED` facts,
plus a typed shape decision (`PASS`, `NUDGE`, `BLOCKED`, or `UNKNOWN`).
Ignored raw paths and the effective manifest source are reported; failures carry
stage, location when available, safe excerpt, behavior (`STOP` for boot and
`KEEP_PREVIOUS_EFFECTIVE` for refresh), and a next repair step.
Legacy `manifest.capabilities.bash` is mapped in memory to `shell`; equal dual
input nudges, differing dual input blocks, and canonical-only input passes.

Compatibility exists to keep older local files readable while agents/humans
repair them. Retired prompt fields and ignored runtime knobs are never new-write
shapes. If active validation encounters a conflict or unsupported value it fails
closed; the reader does not guess an effective value or silently claim success.
There is no automatic rewrite, strip-and-write-back, migration registry, version
chain, stored progress, or remote runtime dependency in this read path. Explicit
agent/human actions such as an intentional preset activation remain separate
writes and are not reader side effects.

## Port

`read_init(working_dir, materialize=None, prepare=None) -> InitReadOutcome` is the
kernel-owned semantic boundary. Wrapper composition roots inject the existing
preset materializer and provider-inheritance preparation callbacks. The outcome
contains the status, raw file path, in-memory effective data on success, ignored
paths/warnings, and bounded failure evidence. `validate_init` remains the real
schema validator; `parse_jsonc` remains the real JSONC parser;
`write_resolved_manifest` remains the real redacted effective-config artifact.

## Adapters

`cli.load_init` and `Agent._read_init` are composition roots. They inject wrapper
preset loading and consume the same outcome. `WorkingDir.write_resolved_manifest`
is the existing local artifact writer. No reader adapter guesses schema fields,
constructs a migration workspace, or performs a second notification path.

## Contract rules

1. `src/lingtai/init.jsonc` is the sole kernel semantic source. New writers must
   use its canonical/normal shape; compatibility is read-only.
2. Boot and refresh must call the same `read_init` path and use the same parser,
   materializer, validator, path resolver, and redacted effective-manifest
   artifact.
3. The reader must not modify `<workdir>/init.json`, including by stripping
   deprecated fields, canonicalizing raw JSON, persisting resolved presets, or
   writing a venv path. It may mutate only its in-memory effective mapping and
   derived `system/manifest.resolved.json` artifact.
4. Structured reader outcomes and shape/action decisions remain separate. A
   fully effective compatibility read may still require an agent Nudge; an
   ignored-field read is not a PASS; failure is BLOCKED or UNKNOWN rather than
   fabricated success.
5. Compatibility retirement is recorded in this Contract and Git history, not
   per-agent progress files. Unsupported active conflicts fail closed; retired
   compatibility fields are reported as ignored and are never auto-resolved or
   rewritten.
6. Every declared Nudge kind (including the init/config-shape finding) uses the
   ordinary `.notification/nudge.json` transport and shared global
   `LINGTAI_NUDGE_ENABLED` / `LINGTAI_NUDGE_REPEAT_INTERVAL` policy. The goal
   reminder is explicitly a separate protected-goal system notification, not a
   declared Nudge kind, and is therefore not part of Nudge dispatch/docs.
7. Every declared Nudge kind is additionally bound by the shared
   `nudge.upsert` hard inline cap: the fully assembled entry (producer body
   plus `kind` and the policy fields from rule 6) may be at most
   `INLINE_MAX_CHARS=10_000` characters on the wire. A finding that would
   exceed this — including the init/config-shape finding's
   `effective_outcome` payload on a large or non-canonical `init.json` — is
   never truncated and never left inline oversized; the complete original is
   persisted verbatim to a content-addressed, owner-only sidecar file under
   `<working_dir>/tmp/nudge-findings/` (the ordinary agent temp namespace,
   consistent with `tmp/tool-results/` — not `.notification/`), and the wire
   entry is replaced with a compact summary carrying the sidecar's absolute
   path, exact character/byte counts, and a SHA-256 of the exact persisted
   bytes. `kind` is validated against a bounded filesystem-safe shape before
   any file naming. Externalization is fail-loud: if `kind` is invalid or the
   sidecar write does not durably succeed, `upsert` raises
   `NudgeExternalizationError` (a bounded static message that never echoes
   producer content) instead of writing any compact placeholder, and does
   not mutate `.notification/nudge.json` or `.notification/.nudge_state.json`
   at all — both are left completely untouched for a later heartbeat retry.
   This is a Nudge-transport concern, not an `init_reader` concern: no
   producer, including `nudge/init_config.py`, individually re-implements
   truncation, externalization, or kind validation.
8. `manifest.llm.thinking` is validated against a **provider-scoped**
   vocabulary, not one universal tuple. Codex-family providers and
   `provider="custom"`, `api_compat="openai"`, `wire_api="responses"` accept
   `none|minimal|low|medium|high|xhigh`. The `kimi-code`/`kimi_code` route
   accepts exactly `low|high|max` — the K3 coding service's own native effort
   vocabulary — so `max` stays rejected on Responses and
   `none`/`minimal`/`medium`/`xhigh` stay rejected on Kimi. The gateway's
   `medium`→high and `xhigh`→max compatibility aliases are deliberately not
   surfaced. Every other provider remains out of scope. Custom omission keeps
   the existing `high` runtime default; Codex omission keeps its existing
   adapter-owned `xhigh` default; **Kimi omission stays omission** — no
   `KIMI_MODEL_THINKING_EFFORT` variable is set at all, byte-identical to the
   pre-contract invocation. `default` is an internal omission sentinel, never a
   user-configurable literal. Invalid values or scopes fail validation rather
   than being normalized silently.
9. For the Kimi Code route, an explicit level is frozen at chat creation and
   re-applied to the private environment of **every** physical CLI invocation
   of that session — first call, `--session` resumed call, and each
   overflow-recovery retry within one logical send. The model capability gate
   **fails closed** at `create_chat`: explicit effort is accepted only for the
   documented effort-capable coding ids (`k3`, `k3-256k`); the always-thinking
   ids (`kimi-for-coding`, `kimi-for-coding-highspeed`) raise because they have
   no effort dimension, and any other id raises rather than being optimistically
   allowed. The gate judges every model the CLI could actually run: when an API
   key is available the adapter drops `--model` and Kimi's env-model synthesis
   resolves the *adapter's* model rather than the chat's, so a chat model that
   diverges from the adapter model must clear the gate on **both** ids or the
   explicit effort fails closed — an effort is never authorized against a model
   that does not run. Omitted effort never raises, for any model. LingTai-internal ancillary sessions (soul inquiry/consultation mirrors) use `ancillary_session_thinking`: on the Kimi route they pass the omission sentinel so a LingTai-injected legacy `"high"` can never trip the gate against an always-thinking model, and every other provider keeps the legacy default byte-identical. Effort never rebuilds
   the chat, resets the opaque CLI session id, or rewrites the cached
   stable-context system block, and it never reaches argv. Capability status is
   `model_verified=false`: the env-var contract and vocabulary are documented,
   per-model/account acceptance and installed-CLI-version behavior are not
   verified. This contract covers *configured* effort only — there is no
   live-control, clear/reset, or default-restoration claim, and
   `KIMI_MODEL_THINKING_KEEP` is deliberately not set.

## Contract tests

`tests/test_init_reader.py` proves JSONC parsing, identical boot/refresh reader
outcomes, ignored-path reporting, structured parse/validation failure evidence,
secret-redacted effective-manifest use, and the no-auto-mutation invariant.
Focused Nudge tests prove defaults, invalid-value fallback, self-describing
messages, global suppression, and dismiss/repeat semantics.
`tests/test_nudge_inline_cap.py` proves the exact 10,000/10,001-char boundary,
Unicode character-vs-byte counting, exact sidecar content/hash/path/
permissions under `tmp/nudge-findings/`, directory-permission enforcement even
when the directory pre-existed with looser permissions, stable
content-addressed reuse across repeated upserts of the same finding, no
cap-bookkeeping leakage into an ordinary uncapped entry, fail-loud
`NudgeExternalizationError` (bounded message, no mutation of either
`.notification/nudge.json` or `.notification/.nudge_state.json`) both when
the sidecar write fails and when `kind` is oversized or escape-heavy, and
dismissal/repeat semantics for a capped finding.
`tests/test_init_schema.py`, `tests/test_presets.py`,
`tests/test_agent_config_hydration.py`, and
`tests/test_preset_materialization.py` prove the accepted custom Responses
scope, rejected out-of-scope values, and the distinct custom/Codex omission
defaults through real config and session materialization. The same two schema
suites prove the Kimi Code three-value scope for both registered spellings and
that `none`/`minimal`/`medium`/`xhigh`/case-and-whitespace aliases are rejected
there while `max` stays rejected on Responses.
`tests/test_kimi_code_effort.py` is the Kimi configured-effort contract test:
it proves the omitted invocation is byte-identical to the pre-contract one
(including for the always-thinking default model), that each explicit level
reaches the private environment exactly once on the first and every resumed
invocation and never reaches argv, that one logical send with overflow recovery
reuses one frozen snapshot, that the model capability gate rejects
always-thinking and unknown ids before any subprocess dispatch, that
out-of-vocabulary values are rejected before dispatch with a bounded message,
that the `llm_call` record gains only the safe provider-neutral reasoning
fields (and nothing at all for a provider that offers none), and the isolation
properties — `generate()` sets no variable, both provider spellings behave
identically, and the CLI session identity and auth-env fallback are unchanged.

## Maintenance

When a canonical field, compatibility path, conflict rule, or reader stage
changes, update this Contract, `src/lingtai/init.jsonc`, the paired Anatomy,
both composition roots, focused tests, and the environment/manual route in one
candidate. Files that are only legacy migration machinery are not a runtime
registry for this Contract; if later retirement requires deletion, report the
exact path and obtain path-scoped authorization first.
