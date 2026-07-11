---
name: notification-contract
tool: notification
contract_version: 1
related_files:
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Notification capability contract

`notification` is the standalone notification surface: the **only**
agent-callable home for reading the live notification payload and clearing
notification mirrors. It owns `check` plus three *atomic* dismiss verbs — there
is no kitchen-sink `dismiss`. The implementation lives in
`src/lingtai/tools/notification/`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the agent-facing notification verbs (`check`,
  `dismiss_channel`, `dismiss_event`, `dismiss_ref`).
- You are reviewing how `check` gets its live payload stamped, or how the three
  dismiss verbs delegate to the shared guard.

**Do not use this for:**
- Publishing notifications: producers call
  `lingtai.kernel.notifications.submit`/`clear` (re-exported by `system` as
  `publish_notification`/`clear_notification`). This tool only reads/clears.
- `summarize` / large-result compaction: that is `system(action='summarize')`
  (`src/lingtai/tools/system/CONTRACT.md`) — `summarize` is not a notification verb.
- The decision logic behind dismissal (allowlists, protected channels,
  stale-version guard): that lives in `lingtai.kernel.notifications.
  dismiss_channel`, not here.
- Code navigation only: read `src/lingtai/tools/notification/ANATOMY.md`.

**Fast paths:** action list -> §Tool surface; channel files -> §State &
storage; delegation to the shared guard -> §Anchored claims.

## Scope

- Canonical tool name: `notification`.
- Schema requires `action` (enum: `check`, `dismiss_channel`, `dismiss_event`,
  `dismiss_ref`).
- `system` exposes **no** notification/dismiss alias; those verbs live here
  exclusively.
- Non-goals: producer-side publish, summarize, mailbox actions.

## Tool surface

Schema (`src/lingtai/tools/notification/schema.py`) and dispatch
(`src/lingtai/tools/notification/__init__.py:handle`).

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `check` | — | — | `{_notification_placeholder: True, message}` — the live `_meta.notifications` + `_meta.notification_guidance` payload is stamped onto this same dict by the turn loop | — |
| `dismiss_channel` | `channel` | `force`, `reason` | shared `dismiss_channel` result (`{status: "ok", ...}`) | `{status: "error", reason: "missing_channel"}`; `{status: "error", reason: "channel_dismiss_rejects_event_target"}` when `event_id`/`ref_id` is supplied |
| `dismiss_event` | `event_id` | `channel` (default `system`), `force`, `reason` | shared `dismiss_channel` result | `{status: "error", reason: "missing_event_id"}` |
| `dismiss_ref` | `ref_id` | `channel` (default `system`), `force`, `reason` | shared `dismiss_channel` result | `{status: "error", reason: "missing_ref_id"}` |

An unknown/absent `action` returns `{status: "error", message: "Unknown
notification action: ..."}`. All three dismiss verbs delegate to
`lingtai.kernel.notifications.dismiss_channel(..., invoked_by="notification")`,
so the allowlist, post-molt ack-reason requirement, protected-channel refusal,
generic-dismiss guard, and stale-channel-version refusal all hold here by
construction.

## State & storage

This tool owns no storage of its own; it reads/clears producer-written mirrors
under the agent working directory (`agent._working_dir`):

```text
.notification/<channel>.json   — per-channel notification mirror (e.g. email.json,
                                 soul.json, post-molt.json, system.json). dismiss_channel
                                 clears one whole file; dismiss_event/dismiss_ref remove a
                                 single event by id from .notification/system.json.
```

`check` writes nothing — it returns a placeholder dict, and the turn loop's
meta-block post-hook (`attach_active_notifications`) stamps the live payload onto
the freshest dict-shaped tool result. Producer-owned state is never touched by
dismissal; guarded mirrors refuse without `force`.

## Cross-platform invariants

- No direct filesystem or subprocess work in this tool: `check` returns an
  in-memory dict, and every dismiss verb delegates to the shared
  `dismiss_channel` helper (which performs the pathlib-based, compare-and-clear
  channel writes). DOCUMENT — no platform-specific behavior in this tool; all
  file access is via `lingtai.kernel.notifications`.
- No PTY/subprocess. DOCUMENT (do not change).

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `notification` is registered and wired into every agent like `system` | `src/lingtai/tools/notification/__init__.py`, `schema.py` | `tests/test_notification_tool.py::test_notification_is_registered_like_system`, `tests/test_notification_tool.py::test_notification_wired_into_every_agent` |
| Schema exposes exactly the four atomic verbs (no kitchen-sink dismiss) | `src/lingtai/tools/notification/schema.py:get_schema` | `tests/test_notification_tool.py::test_notification_schema_exposes_atomic_actions`, `tests/test_notification_tool.py::test_notification_schema_has_no_kitchen_sink_dismiss` |
| `check` returns a placeholder dict (so the meta-block can stamp it) | `src/lingtai/tools/notification/__init__.py:_check` | `tests/test_notification_tool.py::test_check_returns_placeholder_dict` |
| Unknown actions error out | `src/lingtai/tools/notification/__init__.py:handle` | `tests/test_notification_tool.py::test_unknown_action_errors` |
| `dismiss_channel` clears a whole surface and rejects event/ref targets | `src/lingtai/tools/notification/__init__.py:_dismiss_channel` | `tests/test_notification_tool.py::test_dismiss_channel_clears_surface`, `tests/test_notification_tool.py::test_dismiss_channel_rejects_event_target` |
| `dismiss_channel` requires a channel | `src/lingtai/tools/notification/__init__.py:_dismiss_channel` | `tests/test_notification_tool.py::test_dismiss_channel_missing_channel` |
| `dismiss_event` removes one event and defaults to the `system` channel | `src/lingtai/tools/notification/__init__.py:_dismiss_event` | `tests/test_notification_tool.py::test_dismiss_event_removes_one`, `tests/test_notification_tool.py::test_dismiss_event_defaults_to_system_channel` |
| `dismiss_ref` removes events by ref_id | `src/lingtai/tools/notification/__init__.py:_dismiss_ref` | `tests/test_notification_tool.py::test_dismiss_ref_removes_by_ref`, `tests/test_notification_tool.py::test_dismiss_ref_missing_ref_id` |
| `system` schema drops notification/dismiss; `summarize` is not a notification verb | `src/lingtai/tools/notification/schema.py`, `src/lingtai/tools/system/schema.py` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss`, `tests/test_notification_tool.py::test_summarize_is_not_a_notification_action` |
| Guarded/protected channels refuse without `force`; the delegation preserves every guard | `src/lingtai/tools/notification/__init__.py` → `lingtai.kernel.notifications.dismiss_channel` | `tests/test_notification_tool.py::test_guarded_channel_refuses_without_force`, `tests/test_notification_tool.py::test_protected_goal_channel_refused` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Notification verbs live only here, not on `system` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss` | Call `system(action='check')` | Diverging duplicate notification surfaces |
| Dismissal is atomic (no kitchen-sink `dismiss`) | `tests/test_notification_tool.py::test_notification_schema_has_no_kitchen_sink_dismiss` | Inspect the schema enum | Ambiguous clears wipe more than intended |
| `check` stays a placeholder the meta-block can stamp | `tests/test_notification_tool.py::test_check_returns_placeholder_dict` | Call `check`, inspect `_meta.notifications` | Live payload never reaches the agent |
| dismiss verbs preserve the shared guards | `tests/test_notification_tool.py::test_guarded_channel_refuses_without_force` / `test_protected_goal_channel_refused` | Dismiss a protected channel without force | Protected/producer state clobbered |
| `dismiss_channel` refuses event/ref targets | `tests/test_notification_tool.py::test_dismiss_channel_rejects_event_target` | Pass `event_id` to `dismiss_channel` | Whole-channel wipe when a single event was meant |

Run before merging notification changes:

```bash
python -m pytest tests/test_notification_tool.py tests/test_system_dismiss.py -q
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
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
