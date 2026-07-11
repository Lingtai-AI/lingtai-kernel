---
name: soul-contract
tool: soul
contract_version: 1
related_files:
  - src/tools/soul/__init__.py
  - src/tools/soul/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Soul capability contract

`soul` is the agent's inner voice: on-demand past-self `inquiry`, mechanical
periodic `flow` consultation, cadence/voice `config`, and a `dismiss` for the
soul-flow notification. The implementation lives in `src/tools/soul/`; the code
is the source of truth.

## Routing Card

**Use this when:**
- You are editing the sync `inquiry` mirror session, the periodic `flow`
  consultation fire, or the soul cadence/voice config knobs.
- You are reviewing how soul voices reach the agent (via `.notification/
  soul.json`) and how flow is opt-in gated.

**Do not use this for:**
- General notification reads: use the `notification` tool
  (`src/tools/notification/CONTRACT.md`). `soul(action='dismiss')` is a thin
  wrapper that clears only the `soul` channel via the shared helper.
- Context molt / summarize: those are `psyche` and `system`
  (`src/tools/psyche/CONTRACT.md`, `src/tools/system/CONTRACT.md`). Soul reads
  molt snapshots as consultation substrate but does not create them.
- Code navigation only: read `src/tools/soul/ANATOMY.md`.

**Fast paths:** action list -> §Tool surface; flow opt-in gate -> §Anchored
claims; log/notification paths -> §State & storage.

## Scope

- Canonical tool name: `soul`.
- Schema requires `action` (enum: `inquiry`, `flow`, `config`, `voice`,
  `dismiss`).
- `flow` is opt-in via an environment variable and disabled by default; the
  agent-invoked call only *triggers* a fire — voices arrive asynchronously.
- Non-goals: general notification verbs, molt/summarize, mailbox actions.

## Tool surface

Schema and dispatch both live in `src/tools/soul/__init__.py`
(`get_schema`, `handle`).

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `inquiry` | `inquiry` (non-empty str) | — | `{status: "ok", voice}` (or `voice: "(silence)"`) | `{error: "inquiry is required ..."}` |
| `flow` | — | — | `{status: "ok", message}` when triggered; `{status: "disabled", enabled: False, env_var, message}` when opt-out | `{error: "soul flow ongoing, request rejected"}` when a fire is already in flight |
| `config` | at least one of `delay_seconds`, `consultation_past_count` | — | `{status: "ok", old, new}` (+ `soul_flow_enabled`/`note` when flow disabled) | `{error: "config requires at least one of ..."}`; range/type `{error}` for each field |
| `voice` | — (read) | `set` (`inner`/`observer`/`custom`), `prompt` (required for `custom`) | `{status: "ok", current, available, prompt, ...}` | `{error: "set must be a string ..."}`; `{error: "Unknown voice profile: ..."}`; `{error: "set='custom' requires a non-empty 'prompt' ..."}`; `{error: "prompt is too long ..."}` |
| `dismiss` | — | — | `{status: "ok", message}` (delegates to `dismiss_channel(soul)`) | dismissal `{status: "error", ...}` from the shared helper |

An unknown/absent `action` returns `{error: "Unknown soul action: ..."}`.
`flow`'s disabled/ongoing paths return **before** spawning any fire thread.

## State & storage

Paths are relative to the agent working directory (`agent._working_dir`).

```text
.notification/soul.json     — where flow/inquiry voices are published for the kernel to surface
logs/soul_flow.jsonl        — append-only record of every soul entry; the `mode` field
                              distinguishes "flow" from "inquiry" (there is no separate
                              soul_inquiry.jsonl)
history/snapshots/          — past-self snapshots sampled as flow consultation substrate (read-only here)
init.json                   — manifest.soul persistence for config (delay_seconds,
                              consultation_past_count) and voice (soul_voice, soul_voice_prompt)
```

- `flow` fires `M = 1 + K` parallel LLM calls, writes voices to
  `.notification/soul.json` via `publish_notification`; the kernel's notification
  sync surfaces them inside the synthesized `notification(action='check')` pair.
- `inquiry` runs a synchronous mirror session and persists the result via
  `_persist_soul_entry(..., mode="inquiry")` into `logs/soul_flow.jsonl`.
- `config`/`voice` update live agent state and persist to `init.json`
  (`manifest.soul`); `config` also restarts the wall-clock timer when
  `delay_seconds` changes.
- `dismiss` clears `.notification/soul.json` through
  `lingtai.kernel.notifications.dismiss_channel(agent, "soul", invoked_by="soul")`.

## Cross-platform invariants

- All persistence is JSON/JSONL via `pathlib.Path` and the shared
  `notifications`/`config` helpers (init.json writes are atomic via a `.tmp` +
  replace). DOCUMENT.
- `flow`/`inquiry` fan-out runs on `threading.Thread` daemons and gates on the
  agent's `_idle` event; no subprocess/PTY. DOCUMENT (do not change).
- No platform-specific path handling beyond `agent._working_dir` joins.
  DOCUMENT — all file access via pathlib.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| Schema exposes `inquiry`/`flow`/`config`/`voice`/`dismiss` | `src/tools/soul/__init__.py:get_schema` | `tests/test_soul.py` |
| `flow` is opt-in and returns a stable `disabled` status when the env var is unset | `src/tools/soul/__init__.py:handle` (`_soul_flow_enabled`) | `tests/test_soul.py` |
| A late consultation result is discarded after a state change | `src/tools/soul/flow.py` | `tests/test_soul.py::test_consultation_fire_discards_late_result_after_state_change` |
| `inquiry` returns a voice (or "(silence)") and persists the entry | `src/tools/soul/__init__.py:handle`, `src/tools/soul/inquiry.py:soul_inquiry` | `tests/test_soul_consultation.py` |
| `config` validates `delay_seconds`/`consultation_past_count` bounds and persists to init.json | `src/tools/soul/config.py:_handle_config`/`_persist_soul_config` | `tests/test_soul.py` |
| `voice` reads/switches built-in profiles and stores custom prompts within the cap | `src/tools/soul/config.py:_handle_voice`/`_persist_soul_voice` | `tests/test_soul.py` |
| `dismiss` delegates to the shared `dismiss_channel` helper for the `soul` channel | `src/tools/soul/__init__.py:handle` | `tests/test_system_dismiss.py::test_soul_dismiss_alias_uses_shared_helper` |
| Soul entries append to `logs/soul_flow.jsonl` keyed by `mode` | `src/tools/soul/flow.py:_persist_soul_entry` | `tests/test_soul_consultation.py` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Flow stays opt-in and burns no thread when disabled | `tests/test_soul.py` (disabled-path assertions) | Call `soul(action='flow')` without the env var | Unexpected LLM cost / fan-out |
| Concurrent flow fires are rejected, not silently dropped | `tests/test_soul.py` (`flow ongoing` path) | Trigger `flow` twice quickly | Surprising silent no-op |
| Config bounds are enforced and persisted | `tests/test_soul.py` (config validation) | Set `delay_seconds` below the min | Runaway cadence / lost settings |
| `dismiss` only clears the `soul` channel via the shared guard | `tests/test_system_dismiss.py::test_soul_dismiss_alias_uses_shared_helper` | Dismiss soul, inspect other channels | Cross-channel notification wipe |
| Late/stale consultation results are discarded | `tests/test_soul.py::test_consultation_fire_discards_late_result_after_state_change` | Change state mid-fire | Stale voices injected into a new context |

Run before merging soul changes:

```bash
python -m pytest tests/test_soul.py tests/test_soul_consultation.py tests/test_system_dismiss.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m tools.glossary_validator --check`.
