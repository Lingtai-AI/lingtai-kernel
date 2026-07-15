---
name: licc-notification-wake-runbook
description: >
  Operational guide for diagnosing and recovering LICC notification incidents
  where producer input lands but no synthetic notification turn reaches the agent.
status: active
last_changed_at: "2026-07-15"
related_files:
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/services/mcp_inbox.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/meta_block.py
  - tests/test_licc_notification_contract_doc.py
  - tests/test_notification_sync.py
maintenance: |
  Keep this runbook aligned with the LICC notification contract, BaseAgent's
  synthetic injection path, canonical notification metadata paths, wake events,
  and the focused regressions that prove the complete producer-to-LLM chain.
---

# LICC notification wake incidents — diagnose the whole path

Use this runbook when a human message or internal notification reaches its
producer, but the agent does not start a new reasoning turn.

The normative notification schema and authority boundaries live in the
[LICC Notification Contract](../../src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md).
This document is an operational companion: it explains how to find the first
broken edge, recover affected agents, and prove that the repair works in a live
process. It does not create a second notification contract.

## The end-to-end chain

A healthy wake is not one event. It is a chain:

```text
producer store / MCP
  -> LICC event in .mcp_inbox
  -> coalesced .notification mirror
  -> BaseAgent synthetic notification pair
  -> canonical _meta.agent_meta.notifications lanes
  -> tc_wake
  -> new LLM call and response
```

The transient and persistent model-visible paths are:

- attention: `_meta.agent_meta.notifications.attention`
- context: `_meta.agent_meta.notifications.persistent`

For delta IM lanes, continuity hooks such as `previous_block.path` must point to
those same canonical paths. A payload can be correct while a string-valued hook
still points to a removed schema location.

## The failure pattern behind PR #947

[PR #947](https://github.com/Lingtai-AI/lingtai-kernel/pull/947)
repaired two regressions exposed after the notification metadata migration in
PR #939.

### 1. A late import broke an earlier call

`BaseAgent._inject_notification_pair` used
`build_synthetic_meta_envelope(...)` before a later import of that same name
inside the function. Python treats a function-local import as an assignment, so
the name becomes local for the entire function body. The earlier reference then
raises `UnboundLocalError`, even though the module also imported that symbol.

The repair moved the import to module scope and removed the late local import.
The durable review rule is broader:

> When a function both imports and uses the same name, inspect the entire
> function for earlier references. Import placement is part of runtime control
> flow, not cosmetic organization.

### 2. Metadata moved, but continuity hooks did not

The payload migration moved persistent context under
`_meta.agent_meta.notifications.persistent`, but the Telegram delta hook still
named `_meta.notification_persistent`. WeChat and Feishu carried the same stale
path constants.

The payload builder therefore appeared healthy while future deltas could point
to a location that no longer existed. The repair updated all three delta-lane
paths and added one shared registry regression.

The durable migration rule is:

> Treat path constants, breadcrumbs, previous-block hooks, comments, examples,
> and tests as schema clients. Search and validate all of them when a
> model-visible path moves.

### 3. Process health was not agent health

The affected processes still had live PIDs, fresh heartbeats, and listeners.
Those signals proved that the process and some background loops were alive; they
did not prove that a producer event could become a new LLM turn.

Use the following distinction:

| Surface | What it proves | What it does not prove |
|---|---|---|
| PID exists | A process with that identity is running | Notification sync or reasoning works |
| Heartbeat is fresh | The heartbeat loop is progressing | A message reached the model |
| Producer read sees the message | Input landed at the source of truth | The kernel injected a synthetic turn |
| Synthetic pair exists | The kernel built model-visible notification input | The agent woke and called the LLM |
| `tc_wake` exists | The idle agent accepted a wake trigger | The LLM completed a response |
| New LLM response exists | The complete wake path produced reasoning output | The external reply was correct or delivered |

## Diagnose from left to right

Stop at the first missing edge. Do not jump directly to repeated restarts.

### 1. Prove producer arrival

Use the producer tool or store as the source of truth. Confirm the exact incoming
message or event identity. A notification preview alone is not enough when it is
truncated, ambiguous, or stale.

For LICC producers, also confirm that the event reached `.mcp_inbox` or that the
poller recorded the corresponding inbox event. If producer arrival is missing,
the defect is upstream of synthetic injection.

### 2. Prove the notification mirror and synthetic pair

Check that the poller projected the event into the channel's `.notification`
mirror and that BaseAgent created the synthetic user/result pair. The focused
regression `test_notification_injection_has_no_unbound_local_meta_builder`
locks the original Python-scope failure.

A sync exception in an operational log can be decisive even when the structured
event history only shows producer arrival. Search the current process log as
well as event history; do not assume every pre-injection exception became an
event.

### 3. Prove the canonical metadata shape

Inspect the synthetic result metadata, not only the raw notification file.
Confirm that the attention hook and persistent lane use the canonical nested
paths from the contract.

For Telegram, WeChat, and Feishu deltas, also confirm that
`previous_block.path` points to the matching
`_meta.agent_meta.notifications.persistent.mcp.<channel>` lane. The focused
regression `test_delta_persistent_lane_paths_match_current_synthetic_metadata`
locks this registry-wide invariant.

### 4. Prove the wake transition

Look for the wake transition caused by the synthetic tool call, normally
reported as `woke from asleep: tc_wake`. If the pair exists but this transition
does not, the failure is between injection and lifecycle wake handling.

### 5. Prove a new LLM turn

Require a new LLM call and response after the wake timestamp. This is the first
point at which the complete producer-to-reasoning path is proven.

A human-facing reply is a separate final check. The kernel can wake correctly
while a producer reply still fails or remains unauthorized.

## Repair and live recovery sequence

1. **Fix the first broken owner.** Do not add a downstream heal that hides the
   exception or stale schema path.
2. **Run focused regressions.** Cover the exact exception path and any shared
   registry or schema clients affected by the migration.
3. **Re-check the contract.** If normative lanes, authority, or dismissal
   semantics changed, update the contract and its tests. If behavior did not
   change, keep the contract stable and update only stale examples or links.
4. **Load the exact repair.** Verify the runtime's actual import path and commit;
   source checkout state alone is not deployment evidence.
5. **Restart only affected agents.** Avoid broad restarts when version and error
   evidence can identify the blast radius.
6. **Send a real producer event.** Exercise at least one affected human-message
   channel rather than relying only on constructor or unit-test success.
7. **Verify the full ladder.** Require producer arrival, synthetic pair,
   canonical metadata, `tc_wake`, and a new LLM response in timestamp order.
8. **Audit recurrence.** Count the original exception after each new process
   started. Old pre-start errors are historical evidence, not a live recurrence.

PR #947 was accepted only after three affected agents independently completed
that live chain and no new process reproduced the original sync exception.

## Review checklist

Before approving a notification-path change, answer all of these:

- [ ] Did every name imported inside a function get checked for earlier use in
      that same function?
- [ ] Did every model-visible path migration include constants, hooks,
      breadcrumbs, prose, examples, and tests?
- [ ] Does the transient attention hook remain identity-only where a persistent
      lane exists?
- [ ] Does persistent content remain context rather than producer unread state?
- [ ] Do producer read/reply/dismiss tools remain the source of truth for side
      effects?
- [ ] Do delta lanes point `previous_block` at the current canonical path?
- [ ] Is there a regression for the original exception, not merely the intended
      helper output?
- [ ] Did a live process prove pair -> `tc_wake` -> LLM response?
- [ ] Were the actual runtime import path and commit verified?

## Anti-patterns

- **"The PID and heartbeat are fresh, so the agent is healthy."** They are only
  process-level evidence.
- **Repeated CPR before repairing startup or sync.** This reproduces the same
  defect and can obscure the original evidence.
- **Testing only the payload builder.** Injection, wake, and continuity hooks can
  fail around a correct payload.
- **Updating the payload but not its path strings.** String-valued metadata is
  part of the schema.
- **Declaring victory at merge.** Merge is not runtime activation, and activation
  is not live producer-to-LLM proof.
- **Deleting old logs during recovery.** Preserve pre-fix evidence so post-start
  recurrence can be distinguished from historical errors.

## Minimum closeout record

A concise incident closeout should preserve:

- first failing edge and exact exception or stale path;
- repairing commit or PR;
- focused validation commands and literal results;
- runtime import path and loaded commit;
- affected-agent restart evidence;
- one timestamp-ordered producer -> pair -> wake -> LLM chain per sampled agent;
- post-start recurrence count for the original error;
- any adjacent documentation debt deliberately left out of scope.

That record is enough for a future maintainer to distinguish code presence,
runtime activation, and in-situ recovery without requiring private mailbox or
raw runtime logs in the repository.
