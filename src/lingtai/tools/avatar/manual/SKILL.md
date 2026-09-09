---
name: avatar-manual
description: |
  Use for an unfamiliar or consequential Avatar spawn, shallow/deep choice,
  detached lifecycle, recovery, or footprint review. Routine schema-sufficient
  spawns can use the tool directly.
version: 1.5.0
last_changed_at: 2026-09-09T11:24:00Z
related_files:
- src/lingtai/tools/avatar/__init__.py
- src/lingtai/tools/avatar/settings.py
- src/lingtai/tools/avatar/ANATOMY.md
- src/lingtai/tools/avatar/CONTRACT.md
- src/lingtai/tools/CONTRACT.md
- src/lingtai/tools/avatar/manual/reference/spawn.md
- src/lingtai/tools/avatar/manual/reference/lifecycle.md
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/kernel/prompt.py
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
maintenance: |
  Keep this router and its two references short, direct, and aligned with Avatar
  schema, spawn/authority/lifecycle semantics, and the package-local manual
  loader. Keep the settings anchors and cleanup route stable; update the focused
  reference when its owned hazard changes.
---

# Avatar Manual

Avatar creates a **persistent, independent peer** as a detached process. Use
`daemon` for disposable work and `shell` for a one-off command. After launch,
communicate by mail/email; there is no ongoing Avatar handle.

## Routine spawn: direct path

The schema is enough for a routine call; do not load this manual as a ritual.
Use all five strict spawn-input keys and put the mission in root `reasoning`:

```text
avatar(action="spawn",
       input={"name": "researcher", "type": null, "comment": null,
              "dry_run": false, "confirm": false},
       reasoning="Inspect the heartbeat regression; report evidence by mail and change no code.")
```

State objective, resources, reporting route, done condition, and constraints.
`name` is the canonical sibling basename, not a separate display name. Spawn
creates an independent life: obtain authorization, and do not batch it with
unrelated calls. `dry_run=true` previews without writes or a process, **not**
launch admissibility; `confirm=true` acknowledges only mission review.

| Need | Read |
|---|---|
| Payload choice, exact mission/name gates, preview limits, comment/preset inheritance | [Spawn and identity](reference/spawn.md) |
| Boot result, detached life, authority, platform, failed launch, retirement | [Lifecycle and authority](reference/lifecycle.md) |

`avatar(action="manual", input={}, reasoning="load Avatar guidance")` returns
this **package-local** body and `manual_path`; resolve its links from that path,
not from an Agent's installed skill copy. Avatar has no rules action or fan-out:
use `psyche(action="manual", input={}, reasoning="locate rules guidance")` for
the kernel/Psyche `.rules` protocol.

## Read-only settings

`avatar(action="settings", input={}, reasoning="inspect Avatar policy")`
returns 16 fresh five-field rows: `key`, `current`, `default`, `configurable`,
`comment`. Every row is fixed, `current=default`, `configurable:false`.
The source is `tools/avatar/settings.py`; there is no Avatar settings file,
`LINGTAI_AVATAR_*` override, or set/reset action. Per-call inputs do not change
SHOW. Changing fixed policy requires an authorized source change, tests and
owner relaunch, then SHOW again. Values are public policy, not identity,
credentials, environment contents, or live session state. An unavailable row
fails the whole action as bounded `SETTINGS_UNAVAILABLE`, without partial rows.

### Spawn call defaults

`type` is `shallow`, `comment` is empty, `dry_run` and `confirm` are false when
nullable inputs are omitted/null. These four rows describe call defaults only.
Shallow copies rewritten init plus narrow Psyche inputs; deep adds durable
identity/knowledge state. See the payload reference before assuming isolation.

### Spawn validation policy

Five rows: allowed types `shallow`/`deep`; name minimum 1 and maximum 64
characters; mission minimum 20 trimmed characters; placeholder tokens `bar`,
`check`, `debug`, `foo`, `temp`, `test`, `tmp`. Names use Unicode-aware
`^[\w-]+$` plus explicit length/dot checks: supply letters/digits/underscore/
hyphen only. Exact mission token/space behavior is in the spawn reference.

### Spawn lifecycle policy

Seven rows: boot wait 5.0 seconds, poll 0.1 seconds, stderr tail 2,000 bytes;
preset policy `parent-default`, environment `inherit-launcher-process`,
lifecycle `detached-independent`, and admin inheritance `none`.
Heartbeat presence is not a freshness or human-delivery proof; raw stderr may
be sensitive. The lifecycle reference owns result/error and authority details.
Preset rewriting is conditional on a configured default; see spawn reference.

## Cleanup / Footprint

Targets are sibling `<network-root>/<name>/` and the parent's append-only
`delegates/ledger.jsonl`, not cache. For read-only counts/bytes, the packaged
[shared footprint recipe](../../skills/manual/reference/cleanup-footprint-contract.md#shared-footprint-check-recipe)
accepts explicit target paths and writes/deletes nothing. Consider a report
after an avatar-heavy session or before retirement; logging it to
`logs/cleanup.jsonl` is optional and requires consent.

Never delete directories, ledger, active processes, or recovery/audit state
blindly. Capture a handoff, check current liveness, report footprint, then seek
exact authorization for the specific lifecycle or destructive action. If the
user is unavailable, stop after the report.
