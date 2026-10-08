---
name: migration-guide
description: >
  Conclusion-first router for authorizing, selecting, recovering, and proving a
  mechanical Agent workdir/address or whole-Project path move.
version: 2.3.0
last_changed_at: "2026-09-15T03:15:00Z"
tags: [lingtai, system-manual, migration-guide, agent, project, workdir, address, rename, relocation, cutover, recovery, posix]
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/procedures-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/refresh-precheck/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/reference/project-move/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/reference/agent-name-move/SKILL.md
- src/lingtai/tools/system/CONTRACT.md
- src/lingtai/tools/system/ANATOMY.md
maintenance: |
  Keep this file a short common gate/router/receipt. Put helper implementation
  detail only in its one nested lane reference, and keep those links reciprocal.
---

# Migration Guide — Agent and Project Path Relocation

**Conclusion:** classify the request, prove exact current authority, pass every
common gate, choose one lane, and then read only that lane's reference. This
router and its helpers grant no authority. `HOLD` on any missing, stale,
ambiguous, or broader-than-written fact.

## 1. Classify

| Request | Route |
|---|---|
| Mutable display nickname (`name_nickname`) | System identity action; no path move. |
| One-time immutable true name (`name_set`) | System identity action; no path move. |
| One Agent workdir/derived address or a whole Project path | Continue here; immutable identity does not thereby change. |
| Source, interpreter, venv, install provenance, or selector change | [`runtime-update-checks`](../runtime-update-checks/SKILL.md), then [`refresh-precheck`](../refresh-precheck/SKILL.md) only for a separately authorized refresh. |

A Project-root move normally preserves every Agent's identity, true name, and
basename address. Inventory affected owners' stale references without inferring
permission to rewrite them.

## 2. Prove authority

Before every attempt, record current human-owner authority for:

- the canonical source and target, one-Agent or whole-Project scope, and every
  affected runtime;
- the exact quiescence, filesystem, launch, rewrite, staging/copy when relevant,
  cutover, recovery, source-retention, deletion, and cleanup actions allowed;
- the acceptable recovery state and decision owner; and
- each communication proof's channel, origin, peer, and bounded content.

Reading or installing this guide, finding a helper, or passing preflight proves
no permission. This guide does not itself authorize target/staging creation,
stop/signal/cancellation, refresh/relaunch/CPR, config or registry edits,
symlinks, credential access, communication, deletion, cleanup, retry, rollback,
or repair.

## 3. Pass the common gates

Recheck changing facts immediately before cutover or publication:

- [ ] **Source identity:** canonical non-symlink source, expected structure, and
  exact immutable identity tuple; enumerate every in-scope Agent in a Project.
- [ ] **Target/no-replace:** canonical non-symlink parent and absent final target,
  including no symlink; never merge or replace.
- [ ] **Quiescence:** account for human work, collaboration, async work, and all
  affected runtimes. Use only authorized lifecycle actions. Bind processes to
  executable, cwd, parent, and start identity; prove old processes gone,
  heartbeats stale/withdrawn, and OS leases acquirable. A failed scan or lock
  file alone proves nothing.
- [ ] **Filesystem:** establish same-volume or cross-volume from filesystem
  evidence. Unknown, unsupported, or network-filesystem semantics are `HOLD`.
- [ ] **Recovery:** freeze the allowed authoritative state, evidence location,
  receipt boundary, and human decision owner for every phase.

Keep raw evidence private.

## 4. Choose one lane

| Lane | Action |
|---|---|
| One live POSIX Agent, same parent and new basename, supported local same volume | Read [Agent-name move](reference/agent-name-move/SKILL.md). |
| Initiating POSIX Agent moves its whole Project root on a supported local same volume | Read [Project move](reference/project-move/SKILL.md). |
| Other same-volume move | Use only a separately authorized, platform-proven atomic **no-replace** rename. A whole Project moves as one root tree after all its runtimes are quiesced; there is no merge/replace fallback. |
| Cross-volume move | No bundled helper applies. Continue only with the separately authorized flow below. |

For cross-volume work, copy one quiesced closed snapshot to a fresh target-volume
staging sibling using the exact authorized mechanism; verify a closed byte/tree
manifest, required metadata, and every identity; then publish to the absent final
target with a platform-proven no-replace operation. Keep the source quiesced and
retained until target-origin acceptance. Never reinterpret rename as
copy-and-delete: source deletion and staging cleanup require later authority.

## 5. Recover from the observed phase

| Observed phase | Rule |
|---|---|
| Before cutover | Source remains authoritative; target remains absent. No automatic launch or retry. |
| Staging incomplete | Source remains authoritative and retained; staging is non-authoritative. |
| Rename/publication happened | Inspect the target as the moved object; never retry a missing source or auto-rollback. |
| Both paths exist | Keep both quiesced; the human chooses the authoritative copy before launch, repair, or cleanup. |
| Evidence is ambiguous | Freeze state and `HOLD`; do not guess from paths, processes, or an old receipt. |

No relaunch, retry, rollback, refresh, repair, deletion, cleanup, or recovery
message follows by implication.

## 6. Accept only target-origin proof

Launch only when explicitly authorized. `PASS` needs one coherent post-cutover
bundle proving:

1. exactly the expected target-bound process/incarnation, executable, parent,
   and start identity, with no old-source duplicate;
2. a fresh post-cutover heartbeat and target OS lease held by that process;
3. unchanged immutable identity and intended address semantics;
4. an isolated probe from the target interpreter and cwd, without inherited
   old-source paths, matching `sys.executable`, package source files,
   version/HEAD, and selector tuple;
5. connected health for only affected integrations; and
6. only when authorized, the named originating-channel greeting or round trip;
   otherwise record `not authorized / not run` and send nothing.

Any mismatch or unknown is `HOLD`; registry presence or heartbeat alone is not
acceptance.

## 7. Emit a compact redacted receipt

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

Keep paths, PIDs, private identifiers, credentials, messages, and logs in the
approved private context, not a shared receipt.
