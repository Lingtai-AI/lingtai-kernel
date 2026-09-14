---
name: migration-guide
description: >
  Canonical system-manual migration guide for authorizing and verifying a
  mechanical Agent workdir/address or whole-Project path move. Runtime/source
  migration routes outward; the bundled helper is only a narrow POSIX option.
version: 2.0.0
last_changed_at: "2026-09-14T00:00:00Z"
tags: [lingtai, system-manual, migration-guide, agent, project, workdir, address, rename, relocation, cutover, recovery, posix]
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/procedures-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/refresh-precheck/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/change_name.py
- src/lingtai/tools/system/CONTRACT.md
- src/lingtai/kernel/project/ANATOMY.md
- src/lingtai/kernel/workdir_lease/CONTRACT.md
- src/lingtai/kernel/agent_presence/CONTRACT.md
- tests/test_how_to_change_name.py
- tests/test_how_to_change_name_e2e.py
maintenance: |
  Keep migration-guide aligned with System identity, Project, presence/lease,
  runtime-update, and refresh owners. Preserve the helper's narrow POSIX
  same-parent contract; this guide grants no move or adjacent authority.
---

# Migration Guide — Agent and Project Path Relocation

**Conclusion:** this guide helps select, recover, and prove a mechanical path
move. It is not an executor or authority grant. Reading or installing it,
finding its helper, passing preflight, or possessing tools authorizes no move or adjacent side effect.

## Classify before acting

| Operation | Meaning | Route |
|---|---|---|
| `name_nickname` | Mutable display nickname; no path move. | System identity action. |
| `name_set` | One-time immutable true name; no path move. | System identity action. |
| Mechanical migration | Move one Agent workdir/derived address or a whole Project path; immutable identity does not thereby change. | This guide. |
| Runtime/source migration | Change source, interpreter, venv, install provenance, or selectors; not an Agent/Project rename. | [`runtime-update-checks`](../runtime-update-checks/SKILL.md), then [`refresh-precheck`](../refresh-precheck/SKILL.md) only for a separately authorized refresh. |

A Project-root move normally preserves each Agent's identity, true name, and
basename address. Inventory affected owners' old references without inferring
permission to rewrite them.

## Require exact current authority

For every attempt, the human owner must currently authorize canonical source and
target; one-Agent or whole-Project scope and each runtime; exact quiescence,
filesystem, launch, rewrite, and recovery actions; acceptable recovery state and source-retention/deletion boundary; and each communication proof's channel, origin, peer, and bounded content.

Missing, stale, inferred, or broader-than-written scope is `HOLD`. This guide
does not authorize target/staging creation, stop/signal/cancellation, refresh/relaunch/CPR, config or registry edits, symlinks, credential access, communication, deletion, or cleanup.

## Gate the cutover

Collect private read-only evidence and recheck changing facts immediately before cutover or publication:

1. **Source identity:** canonical non-symlink source, expected structure, and
   exact immutable identity tuple; enumerate every in-scope Agent in a Project.
2. **Target and no-replace:** canonical non-symlink parent and an absent final
   target, including no symlink. Recheck at the operation; never merge or replace.
3. **Quiescence:** account for human work, collaboration, async work, and every
   affected runtime. Use only authorized lifecycle actions. Bind processes to
   executable, cwd, parent, and start identity; prove old processes gone, old
   heartbeats stale/withdrawn, and OS leases acquirable. A scan failure or lock
   file alone proves nothing.
4. **Filesystem lane:** establish same-volume or cross-volume from filesystem
   evidence. Unknown, unsupported, or network-filesystem semantics are `HOLD`.
5. **Recovery:** freeze the allowed authoritative state for each phase, evidence
   location, receipt boundary, and human decision owner for ambiguity.

## Select one lane

**Same volume:** after all gates, use one platform-proven atomic **no-replace** rename. A whole Project moves as one root-tree operation after all its runtimes are quiesced. There is no merge/replace fallback.

**Cross volume:** only separate human authority for the exact staging mechanism
may permit copying a quiesced closed snapshot to a fresh target-volume staging
sibling, verifying a closed byte/tree manifest plus required metadata and every
identity, and publishing to the absent final target with a platform-proven
no-replace operation. Keep the source quiesced and retained until target-origin
acceptance. Never reinterpret rename as copy-and-delete; source deletion and
staging cleanup require later authority.

## Fail closed and recover by observed phase

| Observed phase | Rule |
|---|---|
| Before cutover | Source remains authoritative; target remains absent. No automatic launch or retry. |
| Staging incomplete | Source remains authoritative and retained; staging is non-authoritative. |
| Rename/publication happened | Inspect the target as the moved object; never retry a missing source or auto-rollback. |
| Both paths exist | Keep both quiesced; the human chooses the authoritative copy before launch, repair, or cleanup. |
| Evidence is ambiguous | Freeze state and `HOLD`; do not guess from paths, processes, or an old receipt. |

No relaunch, retry, rollback, refresh, repair, deletion, cleanup, or recovery message follows by implication.

## Accept only target-origin proof

Launch only when explicitly authorized. `PASS` needs one coherent post-cutover
bundle proving:

1. exactly the expected target-bound process/incarnation, executable, parent,
   and start identity, with no old-source duplicate;
2. a fresh post-cutover heartbeat and the target OS lease held by that process;
3. unchanged immutable identity and intended address semantics;
4. an isolated probe from the target interpreter and cwd, without inherited
   old-source paths, matching `sys.executable`, package source files,
   version/HEAD, and selector tuple;
5. connected health for only affected integrations; and
6. only if authorized, the named originating-channel greeting or round trip.
   Otherwise record `not authorized / not run` and send nothing.

Any mismatch or unknown is `HOLD`; registry presence or heartbeat alone is not
acceptance.

## Compact redacted receipt

```text
operation/scope_authority: <Agent|Project>; <redacted reference>
source_target/volume_lane: <redacted labels>; <same|cross>
quiescence/source_identity/no_replace: PASS | HOLD
cutover_phase/outcome: <label>; retained_source: yes | not applicable
target_process_heartbeat_lease: PASS | HOLD
isolated_target_import_source_identity: PASS | HOLD
affected_integrations: PASS | HOLD | not applicable
communication: verified | not authorized / not run
final: PASS | HOLD; hold_reason: <sanitized category>
```

Keep raw paths, PIDs, private identifiers, credentials, messages, and logs in the
approved private context, not a shared receipt.

## Appendix: narrow POSIX same-parent helper

With exact execution authority, the bundled `scripts/change_name.py` is an
option only for one live POSIX Agent, same parent/new basename, on a supported
local same-volume filesystem:

```sh
python <installed-change_name.py> <canonical-absolute-agent-workdir> <new-basename>
```

Its strict preflight and supervisor require the expected Agent metadata,
heartbeat, lease, process, runtime, safe absent destination, and supported
no-replace primitive. It suspends that Agent, waits for process/heartbeat/lease
release, performs one Linux `RENAME_NOREPLACE` or macOS `RENAME_EXCL` rename,
updates only its documented in-workdir fields, and launches/checks the target.
Those lifecycle, filesystem, config, logging, and launch effects still require
explicit authority.

The helper has no Windows, network-filesystem, cross-parent, cross-volume, or
whole-Project lane and no copy/delete, rollback, peer rewrite, symlink,
integration, or cleanup support. Its own success does not prove a target-held
lease, isolated target origin, or affected integrations; run the acceptance
checks above. On failure follow the phase table and `HOLD` rather than retrying,
restarting, repairing, rolling back, or deleting without current authority.
