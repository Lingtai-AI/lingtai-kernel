---
name: migration-guide
description: >
  Canonical system-manual migration guide for authorizing and verifying a
  mechanical Agent workdir/address or whole-Project path move. Runtime/source
  migration routes outward; the bundled helpers are narrow POSIX options.
version: 2.2.7
last_changed_at: "2026-09-15T02:08:00Z"
tags: [lingtai, system-manual, migration-guide, agent, project, workdir, address, rename, relocation, cutover, recovery, posix]
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/procedures-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/refresh-precheck/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/change_name.py
- src/lingtai/cli.py
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/move_project.py
- src/lingtai/tools/system/CONTRACT.md
- src/lingtai/tools/system/ANATOMY.md
- src/lingtai/tools/context/BEHAVIORS.md
- src/lingtai/kernel/project/ANATOMY.md
- src/lingtai/kernel/workdir_lease/CONTRACT.md
- src/lingtai/kernel/agent_presence/CONTRACT.md
- tests/test_how_to_change_name.py
- tests/test_how_to_change_name_e2e.py
- tests/test_cli.py
- tests/test_move_project.py
maintenance: |
  Keep migration-guide aligned with System identity, Project, presence/lease, runtime-update, and refresh owners.
  Preserve each helper's narrow POSIX contract and distinct lane; this guide grants no move or adjacent authority.
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

## Appendix: narrow POSIX Project-root self-move helper

```sh
python <installed-move_project.py> --agent-dir /absolute/project/.lingtai/agent --to /absolute/new-project-root [--timeout 30]
```

Use `scripts/move_project.py` only with current authority for the exact source, absent target, shutdown, root rename, venv rebase, and relaunch. It moves only the Project directly above the initiating Agent's `.lingtai` and requires a trusted local operator, separately established local-filesystem semantics, and no same-privilege source/target/helper mutation. Canonical non-symlink paths, absent/outside target, equal `st_dev`, and Linux `RENAME_NOREPLACE` or macOS `RENAME_EXCL` are rechecked; network/unknown filesystems are `HOLD`. Because supported process observation is a flat `ps command=` string, source, target, initiating workdir, and every Agent-present direct child must be printable with no surrounding whitespace; interior spaces and printable Unicode remain valid. Before any runtime probe or process scan, the helper lazily verifies a process-local copied environment by bounded `locale charmap`: Darwin uses the proven `en_US.UTF-8` candidate, while Linux prefers `C.UTF-8`/`C.utf8` then `en_US.UTF-8`, and only a normalized exact `UTF-8` result is accepted. That cached environment is passed only to helper-owned command and ancestry `ps` calls with strict UTF-8 decoding; it neither changes the ambient environment nor enters the relaunched Agent, and probe, execution, decode, or no-locale failure refuses before suspension.

"Self" means invocation by that Agent's synchronous Shell: the outer helper requires the exact PID/token in its current ancestor chain, starts the detached supervisor with one inherited private gate, and releases it only to preflight and flock every valid stopped sibling. While the Shell still pins normal Agent execution/lease teardown, the outer helper revalidates ancestor/token, process, heartbeat, lease, and workdir set, exclusively creates `.suspend`, finalizes and verifies its descriptor at exact mode 0600, closes it, sends one explicit commit byte, then closes the gate. EOF before that byte and wrong or trailing protocol data abort source-authoritative; direct terminal/wrapper callers refuse. After the exact byte and normal close, a single `lstat` accepts either one regular non-symlink marker or its lifecycle-consumed absence. Every pre-marker PID/token/ancestry observation remains strict. Only after that commit, an unavailable initiating identity may mean normal exit when a fresh strict Project-process rescan proves its PID absent and finds no replacement; the wait still needs a later empty scan, stale heartbeat, acquired initiating lease, and final cutover revalidation. A crash plus external auto-restart is outside this causal guarantee and requires inspection.

Any direct child with `.agent.json` or `init.json` is Agent-present, except the exact canonical direct record-only `human` pseudo-agent (`agent_name`/`address` both `human`, `admin` missing/null), which is validated then excluded as non-launchable. Every malformed, partial, symlinked, aliased, noncanonical, or identity-disagreeing shape still refuses before the marker; safe ordinary siblings remain fenced through target liveness. Post-rename proof also rejects any Agent-run process still naming the old source. Siblings and `human` are never launched.

A present `venv_path` must be a string before selection; a nonempty string remains explicit and must be existing/canonical/absolute/non-symlink, while only a missing key or empty string selects the existing canonical managed default. Only a project-local explicit venv rebases. Every path the canonical owner classifies as the managed default, including an explicit equal path, must have marker status exactly `match` both before suspension and at target-origin probe; `missing`, `error`, and `mismatch` refuse read-only. That managed default receives current Python-selection policy with the owner's `RuntimeError` fallback; ordinary nondefault operator venvs retain owner-compatible marker handling and remain policy-exempt. Durable init stays unchanged, and no venv or marker is created, stamped, healed, removed, or rewritten by the helper. The exact target runtime/cwd/environment then prove executable/import origin and relaunch only the initiator; semantic JSON/mode and file+parent fsync are promised, not bytes/timestamps/owner/xattrs/ACLs.

Before proven rename, source is authoritative with no automatic restart/retry; afterward target is authoritative with no rollback/repair/cleanup. A known source `.prompt` refuses before suspend; a raced source/target prompt is preserved and fails loud at the observed authority boundary. After rename and before relaunch, the helper exclusively creates and durably syncs one mode-0600 target `.prompt` naming the exact old/new roots and telling the same Agent to continue only remaining stale-reference migration, verify/report, and not move again or broaden side effects. Success also waits for that prompt to be consumed, while the lifecycle supplies durable `prompt_received` and `wake` evidence.

The private log moves at `logs/move-project.log`; no Windows/cross-filesystem/copy/merge/overwrite/address change/registry rewrite/sibling relaunch/framework/Project CLI/TUI/version/integration/communication claim is made.

## Appendix: narrow POSIX same-parent Agent-name helper

With exact execution authority, the bundled `scripts/change_name.py` is an
option only for one live POSIX Agent, same parent/new basename, on a supported
local same-volume filesystem:

```sh
python <installed-change_name.py> <canonical-absolute-agent-workdir> <new-basename>
```

Its strict preflight and supervisor require expected Agent metadata, heartbeat,
lease, process, runtime, an absent `.suspend` and
`.change-name-incomplete`, an absent destination, and a supported no-replace
primitive, rejecting unsupported POSIX/libc cells before suspension. It parses
selected JSON with duplicate-key and non-standard `NaN`/infinity rejection and
snapshots the complete relevant `bin/`, `.pth`, and
`direct_url.json` inventory. Under the acquired migration lease it fingerprints
that inventory again immediately before cutover, so a new or changed in-scope
binding refuses while the source is still authoritative.

For a venv lexically and canonically inside this Agent only, the plan rebases
executable regular `bin/` launcher shebangs whose entire interpreter field is an
existing old-root path, absolute path lines in site-packages `.pth` files, and
local `file:` URLs in `direct_url.json` (including case-insensitive `localhost`;
query and fragment are retained). A rewritten shebang, including `#!` and its
line ending, must be at most 255 encoded bytes, a conservative limit below the
supported Linux executable-header bound and Darwin's larger bound. An editable
import from outside the venv must be covered by one exact `.pth` path;
unsupported executable/finder bindings refuse before suspension. An external
venv receives no runtime metadata writes, and if its observed import origin is
inside the old Agent root the helper refuses before suspension because external
metadata repair is outside this lane. The background supervisor, clean source
probes, and gated resumed Agent all use one explicit Python environment:
`PYTHONPATH`/`PYTHONHOME` are absent, bytecode writes and user-site imports are
disabled, and other operator environment values are preserved. This makes the
accepted target origin and the process actually resumed use the same import lane.

The same plan validates the registry first, then rebases only `command` in
ordinary supported stdio entries: top-level `init.json.mcp` entries whose `type`
is `"stdio"` (or omitted), and validated stdio records in workdir
`mcp_registry.jsonl`. A matching registry record with
`source == "lingtai-curated"` makes its init entry activation-only, so every
legacy init launcher field remains unchanged and the running Agent continues to
derive the launcher from its current catalog. HTTP commands, args, env, secrets,
prompts, arbitrary strings, external paths, and other files/registries are not
rewritten. When one selected init object or registry line must change, decoded
JSON values are preserved semantically, not byte-for-byte formatting, whitespace,
or escape spelling; duplicate keys and malformed in-scope data refuse before
suspension.

The supervisor creates `.suspend` with exclusive/no-follow regular mode-0600
semantics and parent fsync. After the Agent stops and the migration lease is
held, it likewise creates and verifies a durable mode-0600
`.change-name-incomplete` fence before rename; the fence moves atomically with
the root. A pre-rename failure removes only that proven helper-created source
fence durably, leaving the source (possibly suspended) authoritative. After one
Linux `RENAME_NOREPLACE` or macOS `RENAME_EXCL` rename, it immediately fsyncs the
shared parent. From that publication onward the target is authoritative and no
automatic move, rollback, or deletion occurs.

Still holding the migration lease and target fence, the helper applies every
planned mode-preserving atomic rewrite with file and parent-directory fsync,
requires the clean target probe to equal the exact planned import origin,
verifies immutable identity, writes only `.agent.json.address`, removes the
suspend request, and prepares/checks a pipe-gated child launch while the fence remains present.
Only at that coherent prepared boundary does it remove the proven fence, release
the lease, and send the launch byte. This ordering prevents a normal
`lingtai run <new>` from clearing `.suspend` and constructing an Agent against
partial target state; it does not claim to eliminate every same-privilege
process race. Before any gate commit, launch-setup, fence-removal,
lease-release, or gate-write failure durably restores the target fence, closes
the gate, and reaps the helper-owned child; post-commit liveness remains an
inspection boundary. Any other unproven post-rename failure likewise leaves or
durably restores the fence. The normal CLI refuses any
shape at that marker before signal cleanup, init loading, or Agent construction
and never consumes it; clearing it is separately authorized recovery.

The helper has no Windows, network-filesystem, cross-parent, cross-volume, or
whole-Project lane and no copy/delete, rollback, peer rewrite, broad descriptor
rewrite, integration, communication, or cleanup support. Its own success proves
only its bounded filesystem/import/identity/process/heartbeat checks; it does
**not** prove configured Telegram, IMAP, MCP, or any other integration health,
nor the complete acceptance bundle above. Those owner-selected external
acceptance checks remain required and must be recorded separately (or as
`not authorized / not run`); the generic helper invents no channel-health
protocol and performs no real-network test. On failure follow the phase table
and `HOLD` rather than retrying, restarting, repairing, clearing the fence,
rolling back, or deleting without current authority.
