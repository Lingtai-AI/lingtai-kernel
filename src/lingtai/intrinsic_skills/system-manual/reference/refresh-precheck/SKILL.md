---
name: refresh-precheck
description: >
  Nested system-manual reference and single owner for a refresh transaction:
  select same-runtime reload, source/venv cutover, or failure recovery; run only
  triggered owner checks; issue exactly one refresh; capture one targeted receipt.
version: 2.0.0
last_changed_at: "2026-09-09T00:00:00Z"
tags: [lingtai, system, refresh, preset, precheck, cutover, recovery, receipt, lifecycle]
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/substrate-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/settings-inventory/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
- src/lingtai/prompts/substrate/substrate.md
- src/lingtai/prompts/procedures/procedures.md
- src/lingtai/tools/system/schema.py
- src/lingtai/tools/system/settings.py
- src/lingtai/kernel/presets.py
maintenance: |
  Own the three-mode refresh transaction and its budgets, call shapes, receipts,
  refusal boundaries, and trigger routing. Leave source/install/nudge diagnosis
  to runtime-update-checks and subsystem facts to their owning manuals. Never
  turn an unchanged subsystem check into a universal preflight or receipt step.
---

# Refresh Transaction

The invariant is:

`select one mode -> one targeted preflight -> exactly one refresh -> one targeted receipt`

Refresh reloads code and configuration already visible to the runtime. It does
not install, update, switch a checkout, repair a venv, save unfinished work, or
grant authority. Read this reference immediately before the call.

## Select exactly one mode

| Mode | Select when | Preflight budget | Targeted receipt |
|---|---|---|---|
| **A. Same-runtime reload** | Source, venv, and interpreter selectors stay unchanged; a reload or preset swap applies an authorized on-disk change. | One transaction gate for exact target/authority and interruption safety; at most one check owned by the changed input. | Fresh process identity or heartbeat, plus one assertion for the changed surface. |
| **B. Source/venv cutover** | An update/build owner intentionally changed source, venv, interpreter, version, or their selectors. | Authority/work safety for the exact target; consume the owner's target receipt; compare intended selectors. Do not repeat a general install audit. | One target assertion: runtime-tuple equality, extended with expected MCP health only if MCP interpreter/config changed; then one real originating-channel round trip. |
| **C. Failure recovery** | The preceding refresh refused, raised, did not relaunch, or produced `logs/refresh_failed_permanent.json`. | Read the exact error/artifact; identify and repair only its owning fault; authorize at most one deliberate retry. | Fresh-process proof plus the assertion for that repaired fault. |

Mode budgets are ceilings, not goals. A has one combined transaction gate plus
at most one changed-owner check: two before and two after. B uses the combined
transaction gate, target receipt, and selector equality: three before and two
after. The first after-check includes changed-MCP health only on that trigger.
Never inspect an unchanged
subsystem.

## Universal transaction gate

Before A or B, resolve these two facts as one decision, not two tool calls:

1. **Target and authority:** name the exact agent/workdir, record its current
   process incarnation or heartbeat baseline, and name the authorized reload or
   already-authorized write. A nudge, successful preflight, or tool
   availability is not consent to install, migrate, edit config, or expand a
   preset allowlist. A safe pure self-reload needs no invented write authority.
2. **Work safety:** refuse while the relaunch would interrupt active human work,
   an in-flight collaboration, or daemon work whose result needs this process.
   Tend any durable state that actually needs tending; do not run generic Git,
   notification, or daemon inventories.

Stop if the target or runtime selector is ambiguous. Route source, venv,
installer, nudge, and mismatch diagnosis to `runtime-update-checks`; do not
refresh hoping ambiguity will resolve itself.

## Add only the triggered owner check

| Actual change or trigger | One preflight addition | One post-refresh assertion / owner route |
|---|---|---|
| `init.json`, an init-owned value, or capability config | Use the changed owner's parser/validator on that document or value. | Assert only that changed value/surface on the fresh process. |
| MCP registry, MCP config, or MCP interpreter | Use the MCP owner's registry/config health check. | Check the expected MCP entry and relevant problem state; do not inventory MCP otherwise. |
| Preset swap | Call `system(action="presets", input={}, reasoning="select exact allowed preset path")` once and pass an exact returned path. | Assert the active preset from the fresh process's resolved runtime state; do not call `presets` again. |
| Preset revert | No catalog lookup is needed. | Assert the configured default became active. |
| Environment or a System-owned setting | Read only its environment-catalog entry or relevant `system(action="settings", input={}, reasoning="read changed owner value")` row. | Assert that one effective value, respecting its documented read point. |
| Canonical prompt source only | Route out before selecting a mode: use `context(action="rebuild", input={})`, not refresh. | Follow `context-manual`; no refresh transaction started. |
| Source, venv, interpreter, version, or selector | Select B and consume the update/build owner's frozen receipt. | Compare the runtime tuple below; mismatch returns to `runtime-update-checks`. |
| `source_drift`, `kernel_version`, or other nudge | Read that finding through `runtime-update-checks`; it is not universal preflight. | Dismiss only under notification-owner guidance after the matching fact is resolved. |
| No changed subsystem (pure reload) | No add-on check. | Fresh-process proof is the receipt; do not manufacture an audit. |

Preset activation is an additive A case. The runtime itself owns preset/revert
exclusivity, allowlist authorization, materialization, and context-fit refusal.
Do not simulate or bypass those gates; if refused, preserve the old surface and
route the exact error.

## Issue exactly one call

Choose one shape; nulls are explicit because the System input is closed.

```text
system(action="refresh", input={"reason":"<authorized reload reason>","preset":null,"revert_preset":null}, reasoning="reload <targeted change>")
system(action="refresh", input={"reason":null,"preset":"<exact allowed path>","revert_preset":null}, reasoning="activate authorized preset")
system(action="refresh", input={"reason":null,"preset":null,"revert_preset":true}, reasoning="return to configured default preset")
```

Execute exactly one of those lines. Never stack calls or refresh again “to be
sure.” A successful tool result only accepts or hands off the request; it does
**not** prove that a replacement process started. Missing launch-command or
watcher setup, handshake failure, and terminal watcher exhaustion remain
failures until fresh-process evidence exists.

## Capture one targeted receipt

Record one compact receipt, not a second checklist:

```text
mode: A | B | C
target: <exact agent/workdir>
authority_and_reason: <who/what authorized this transaction>
refresh_calls: 1
fresh_process: <new incarnation or heartbeat advanced beyond the pre-call baseline>
target_assertion: <one changed surface, runtime tuple, or repaired fault + result>
originating_channel_round_trip: <required for B; otherwise n/a>
```

For B, the update/build owner must freeze before refresh: target, exact
`runtime_tuple` fields (`sys_executable`, `lingtai_file`,
`lingtai_kernel_file`, and `version_or_head`), plus intended `selectors`.
Consume that receipt and compare intended selectors once; do not rerun `.pth`,
editable-install, package, or general Git audits. On the fresh process, compare
those four runtime fields directly to the frozen tuple. If MCP interpreter or
config changed, include the expected
MCP entry and relevant problem state in that same target assertion. Then complete
one real request/response round trip on the originating channel. If
`LINGTAI_RUNTIME_PYTHON` is absent or the tuple differs, stop and diagnose
through `runtime-update-checks`; never substitute PATH Python or a guessed venv.

## Mode C: recover one fault, once

1. Read the returned error first. If the watcher exhausted its bounded retries,
   also read `logs/refresh_failed_permanent.json` and the matching high-priority
   system notification. That artifact is terminal evidence, not permission to
   launch again.
2. Route the named fault: preset/refusal semantics to `substrate-manual`; source,
   venv, selector, or import mismatch to `runtime-update-checks`; MCP fault to
   `mcp-manual`; context fit/rebuild to `context-manual`; launcher/watcher fault
   to its owner. Do not rerun the happy-path table.
3. Repair only that fault with the required human/config-owner authority. If the
   cause is unknown, the repair is unproved, or a permanent failure has not been
   escalated to the human/launcher owner, stop.
4. After a specific repair, permit exactly one deliberate retry and produce the
   C receipt. A failed retry ends the transaction; do not loop or stack calls.

## Hard scope boundary

This reference is guidance, not a public command, script, runtime guard, or
installer. It performs no write, migration, download, package switch, config
change, allowlist expansion, or automatic retry. Owner checks remain available
on their triggers; `.pth`, install provenance, MCP, preset, Git, nudge, whole
tool surface, and daemon checks are never unconditional refresh ceremony.
