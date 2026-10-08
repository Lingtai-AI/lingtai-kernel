---
name: migration-agent-name-move
description: >
  Narrow contract for the self-contained POSIX same-parent Agent workdir/address
  rename script and its transactional runtime/MCP relocation.
version: 1.0.0
last_changed_at: "2026-09-15T03:15:00Z"
tags: [lingtai, migration, agent, address, rename, posix, transaction, venv, mcp]
related_files:
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/change_name.py
- src/lingtai/tools/system/ANATOMY.md
- src/lingtai/tools/system/CONTRACT.md
- src/lingtai/tools/context/BEHAVIORS.md
- src/lingtai/cli.py
- tests/test_how_to_change_name.py
- tests/test_how_to_change_name_e2e.py
- tests/test_cli.py
maintenance: |
  Keep this contract aligned with the directly runnable self-contained script,
  the CLI fence, L006, and the focused transactional tests introduced by #1722.
---

# Agent-name Move Helper

**Conclusion:** first complete the parent [migration router](../../SKILL.md).
Use this script only for one live POSIX Agent, a new basename under the same
parent, and a supported local same-volume filesystem; otherwise `HOLD`.

## Invoke the exact self-contained script

```sh
python <exact-change_name.py> <canonical-absolute-agent-workdir> <new-basename>
```

The bundled source is `../../scripts/change_name.py`. It remains directly
runnable and self-contained: stdlib plus the compatible existing LingTai runtime
used to validate its registry. It does not import a companion migration module.
As demonstrated by [`Lingtai-AI/lingtai-kernel#1722`](https://github.com/Lingtai-AI/lingtai-kernel/pull/1722)
dogfood, an exact fetched/copied script can drive an existing compatible
runtime; installing the change that supplied the script is
not a prerequisite. The background supervisor continues with that exact script.

## Preflight and frozen plan

Before suspension, the script requires expected Agent metadata, one fresh
heartbeat, one matching process, a held lease, an executable configured runtime,
an absent `.suspend`, an absent `.change-name-incomplete`, an absent destination,
and a supported libc no-replace primitive. Unsupported POSIX/libc cells refuse
before lifecycle side effects.

Selected JSON rejects duplicate keys and non-standard `NaN`/infinity. The plan
snapshots the complete relevant `bin/`, `.pth`, and `direct_url.json` inventory.
Under the acquired migration lease it fingerprints that inventory again
immediately before cutover, so a new or changed in-scope binding refuses while
the source remains authoritative.

## Project-local runtime rewrites

Only when a venv is lexically and canonically inside this Agent, the plan rebases:

- executable regular `bin/` launcher shebangs whose entire interpreter field is
  one existing old-root path;
- absolute path lines in site-packages `.pth` files; and
- local `file:` URLs in `direct_url.json`, including case-insensitive
  `localhost`, while retaining query and fragment.

A rewritten shebang—including `#!` and its line ending—must be at most 255
encoded bytes, conservatively below the supported Linux executable-header bound
and Darwin's larger bound. An editable import from outside the venv must be
covered by one exact `.pth` path; unsupported executable/finder bindings refuse
before suspension.

An external venv receives no runtime metadata writes. If its observed import
origin is inside the old Agent root, the script refuses before suspension because
external metadata repair is outside this lane.

The foreground handoff, background supervisor, clean source/target probes, and
gated resumed Agent use an explicit Python environment: `PYTHONPATH` and
`PYTHONHOME` are absent, bytecode writes and user-site imports are disabled, and
other operator environment values are preserved. Thus the accepted target
origin and resumed process use the same import lane.

## Narrow MCP rewrites

The plan validates the registry first. It then rebases only the complete
`command` field in ordinary supported stdio entries:

- top-level `init.json.mcp` entries whose `type` is `"stdio"` or omitted; and
- validated stdio records in workdir `mcp_registry.jsonl`.

A matching registry record whose `source` is `"lingtai-curated"` makes its init
entry activation-only; all legacy init launcher fields remain unchanged and the
Agent continues to derive the launcher from its current catalog. HTTP commands,
args, env, secrets, prompts, arbitrary strings, external paths, and other files
or registries are never rewritten.

When one selected init object or registry line changes, decoded JSON values are
preserved semantically—not byte-for-byte formatting, whitespace, or escape
spelling. Duplicate keys and malformed in-scope data refuse before suspension.

## Transaction and gated launch

The supervisor follows these visible phases:

1. Exclusively create `.suspend` with no-follow, regular mode-0600 semantics and
   parent fsync; wait for process, heartbeat, and lease release.
2. Hold the migration lease; exclusively create and verify the durable mode-0600
   `.change-name-incomplete` fence; revalidate the frozen plan.
3. Publish exactly one Linux `RENAME_NOREPLACE` or macOS `RENAME_EXCL` rename and
   immediately fsync the shared parent. The target is authoritative from this
   point; no automatic move, rollback, or deletion occurs.
4. While still holding the lease and target fence, apply every planned
   mode-preserving atomic rewrite with file and parent-directory fsync; require
   the clean target probe to equal the exact planned import origin; verify
   immutable identity; write only `.agent.json.address`; remove `.suspend`; and
   prepare/check a pipe-gated child.
5. Only at that coherent prepared boundary, remove the proven fence, release the
   lease, send one launch byte, and require target process/identity/heartbeat
   liveness.

A pre-rename failure durably removes only the proven helper-created source fence;
the source, possibly suspended, stays authoritative. The launch child cannot
enter `cli.run` before the lease release and commit byte. The fence ordering
prevents a normal `lingtai run <new>` from clearing `.suspend` and constructing
an Agent against partial target state; it does not claim to eliminate every
same-privilege process race.

Before any gate commit, a launch-setup, fence-removal, lease-release, or gate-write
failure durably restores the target fence, closes the gate, and reaps the
helper-owned child. Any other unproven post-rename failure likewise leaves or
durably restores the fence. Post-commit liveness failure is an inspection
boundary.

The normal CLI refuses any shape at `.change-name-incomplete` before signal
cleanup, init loading, or Agent construction and never consumes it. Clearing the
fence is separately authorized recovery.

## Scope and acceptance boundary

The script has no Windows, network-filesystem, cross-parent, cross-volume, or
whole-Project lane. It supports no copy/delete, rollback, peer rewrite, broad
descriptor rewrite, integration, communication, or cleanup action.

Its success proves only the bounded filesystem, import, identity, process, and
heartbeat checks above. It does not prove configured Telegram, IMAP, MCP, or any
other integration health, nor the parent router's complete acceptance bundle.
Run only owner-selected authorized external acceptance checks and record omitted
ones as `not authorized / not run`; the generic script invents no channel-health
protocol and performs no real-network test. On failure, follow the parent
router's observed-phase table rather than retrying, restarting, repairing,
clearing the fence, rolling back, or deleting without current authority.
