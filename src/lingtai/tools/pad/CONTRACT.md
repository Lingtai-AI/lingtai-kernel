---
name: pad-contract
tool: pad
contract_version: 3
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/pad/BEHAVIORS.md
  - tests/test_agents_memory.py
  - src/lingtai/tools/pad/ANATOMY.md
  - src/lingtai/tools/pad/__init__.py
  - src/lingtai/tools/pad/_pad.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/intrinsic_skills/pad-manual/SKILL.md
  - src/lingtai/kernel/project/interactive_creation.py
  - src/lingtai/adapters/project_interactive_workspace.py
  - tests/test_project_interactive_creation.py
  - src/lingtai/kernel/project/CONTRACT.md
maintenance: |
  This component contract is governed by root CONTRACT.md. Keep the paired
  ANATOMY.md, manual, schemas, results, glossary resources, and focused tests in
  sync. Bump contract_version for breaking behavior changes. Unified Pad owns
  one-time legacy preservation; no public Pad tool or append action remains.
---
# Pad memory contract

## Purpose and ownership

Pad (`system/pad.md`) plus Knowledge are the agent's private memory system.
Pad stays injected, including pinned `system/pad_append.json` references.
Mechanical identity and Covenant remain separate and unchanged.
Guarded by [PD001](BEHAVIORS.md#behavior-pd001).

## Public port

No public `pad` tool, append/edit/load action, or alias exists. Read the Pad
manual through `psyche(action="pad", input={}, reasoning="...")`. Source edits
use authorized Shell operations; ordinary edits do not hot-load a prompt.

## Internal composition

`_seed_pad` uses configured Pad only for a missing/empty body, before the legacy
merge on real init boot. `_legacy_memory_seed` resolves accepted old inline/file
init strings before teardown, preserving readable-file bytes with inline fallback.
It performs no old-source I/O after the completion marker exists. `_pad_load` is the canonical composer for Psyche boot,
Agent rebuild, refresh and the single post-molt reconstruction hook. It reads
Pad and pinned references, removes stale `character`, then publishes only when
requested. Knowledge and Skills keep their existing catalog owners.

## Legacy preservation

Before composing, `_merge_legacy_memory` preserves all UTF-8 legacy
`system/lingtai.md` bytes and a distinct resolved configured legacy value in Pad,
including line endings. Neither may overwrite configured or existing Pad memory.
A direct Psyche boot with staged init defers migration until canonical setup or
rebuild has its configured inputs, rather than marking disk-only preservation early. Existing Pad bytes are
preserved as a prefix. When the exact legacy body already occurs in Pad, no
second copy is appended. The legacy source is never deleted, emptied or written.
An atomic binary replacement precedes the durable one-time marker
`system/lingtai-merged-into-pad`; interruption before marking is recoverable by
exact containment. Once marked, later legacy edits or deliberate Pad rewrites do
not re-append anything. No disk or configured input creates no marker; an explicitly empty old input
is marked without appending. A missing configured file uses its inline fallback. Invalid UTF-8 or a symlink Pad fails without a lossy
merge. The workdir lease serializes runtime writers; concurrent external source
edits and power-loss filesystem guarantees are not a distributed transaction.
Failed prompt publication may roll back the applied prompt generation, but the
completed durable memory preservation remains and is safely reused next time.

## Evidence

[PD001](BEHAVIORS.md#behavior-pd001) and `tests/test_agents_memory.py` provide a
compact real-startup/lifecycle smoke for injected memory and one-time preservation.
It is not exhaustive coverage of crash windows or every legacy-seed combination.
Parent must execute the smoke and complete-prompt proof before acceptance.

Fresh interactive Project seeds carry the selected Recipe comment body as typed
out-of-band creation data (explicit nonempty caller Pad wins), never in new init
or the retired `comment_file` field. The Project publication adapter fills only
absent/empty canonical Pad through the existing `_seed_pad` owner; nonempty Pad
survives. This does not hot-load or expand this capability's public actions.
The Project transaction Contract owns that fresh-writer ordering.
