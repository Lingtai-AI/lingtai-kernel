---
name: migration-project-move
description: >
  Narrow POSIX contract for the bundled initiating-Agent whole-Project
  same-volume self-move helper.
version: 1.0.0
last_changed_at: "2026-09-15T03:15:00Z"
tags: [lingtai, migration, project, relocation, posix, no-replace, lease, recovery]
related_files:
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/move_project.py
- src/lingtai/tools/system/ANATOMY.md
- src/lingtai/tools/system/CONTRACT.md
- tests/test_move_project.py
maintenance: |
  Keep this helper contract aligned with move_project.py and its focused tests.
  Common authority, phase, acceptance, and receipt rules stay in the parent router.
---

# Project Move Helper

**Conclusion:** first complete the parent [migration router](../../SKILL.md).
Use this helper only for its narrow initiating-Agent, local same-volume POSIX
lane; otherwise `HOLD`.

## Invoke

```sh
python <exact-move_project.py> --agent-dir /absolute/project/.lingtai/agent --to /absolute/new-project-root [--timeout 30]
```

The bundled source is `../../scripts/move_project.py`. It moves only the Project
directly above the initiating Agent's `.lingtai`. The authorized operation must
include shutdown, root rename, any project-local venv rebase, and relaunch.

## Filesystem and observation contract

The helper requires a trusted local operator, separately established
local-filesystem semantics, and no same-privilege source, target, or helper
mutation. It rechecks canonical non-symlink paths, an absent target outside the
source, equal `st_dev`, and Linux `RENAME_NOREPLACE` or macOS `RENAME_EXCL`.
Network or unknown filesystems are `HOLD`.

Supported process observation is a flat `ps command=` string. Source, target,
initiating workdir, and every Agent-present direct child must therefore be
printable with no surrounding whitespace; interior spaces and printable Unicode
remain valid. Before any runtime probe or process scan, the helper lazily
verifies a process-local copied environment with bounded `locale charmap`:
Darwin uses the proven `en_US.UTF-8` candidate; Linux prefers
`C.UTF-8`/`C.utf8`, then `en_US.UTF-8`; only a normalized exact `UTF-8` result is
accepted. That cached environment goes only to helper-owned command and ancestry
`ps` calls with strict UTF-8 decoding. It neither changes the ambient
environment nor enters the relaunched Agent. Probe, execution, decode, or
no-locale failure refuses before suspension.

## Self-handoff and sibling fence

“Self” means invocation by that Agent's synchronous Shell. The outer helper
requires the exact PID/token in its current ancestor chain, starts the detached
supervisor with one inherited private gate, and releases it only to preflight and
flock every valid stopped sibling. While the Shell still pins normal Agent
execution/lease teardown, the outer helper revalidates ancestor/token, process,
heartbeat, lease, and workdir set; exclusively creates `.suspend`; finalizes and
verifies its descriptor at exact mode 0600; closes it; sends one explicit commit
byte; then closes the gate. EOF before that byte and wrong or trailing protocol
data abort source-authoritative. Direct terminal/wrapper callers refuse.

After the exact byte and normal close, one `lstat` accepts either one regular
non-symlink marker or its lifecycle-consumed absence. Every pre-marker
PID/token/ancestry observation remains strict. Only after that commit, an
unavailable initiating identity may mean normal exit when a fresh strict
Project-process rescan proves its PID absent and finds no replacement. The wait
still needs a later empty scan, stale heartbeat, acquired initiating lease, and
final cutover revalidation. A crash plus external auto-restart is outside this
causal guarantee and requires inspection.

Any direct child with `.agent.json` or `init.json` is Agent-present, except the
exact canonical direct record-only `human` pseudo-agent (`agent_name`/`address`
both `human`, `admin` missing/null), which is validated then excluded as
non-launchable. Every malformed, partial, symlinked, aliased, noncanonical, or
identity-disagreeing shape refuses before the marker. Safe ordinary siblings
remain fenced through target liveness. Post-rename proof rejects any Agent-run
process still naming the old source. Siblings and `human` are never launched.

## Runtime and prompt boundaries

A present `venv_path` must be a string before selection. A nonempty string
remains explicit and must be existing, canonical, absolute, and non-symlink;
only a missing key or empty string selects the existing canonical managed
default. Only a project-local explicit venv rebases. Every path the canonical
owner classifies as the managed default—including an explicit equal path—must
have marker status exactly `match` before suspension and at target-origin probe;
`missing`, `error`, and `mismatch` refuse read-only. That managed default receives
current Python-selection policy with the owner's `RuntimeError` fallback.
Ordinary nondefault operator venvs retain owner-compatible marker handling and
remain policy-exempt.

Durable init stays unchanged. The helper creates, stamps, heals, removes, or
rewrites no venv or managed-environment marker. The exact target
runtime/cwd/environment prove
executable/import origin and relaunch only the initiator. Semantic JSON/mode and
file-plus-parent fsync are promised, not bytes, timestamps, owner, xattrs, or
ACLs.

A known source `.prompt` refuses before suspend. A raced source/target prompt is
preserved and fails loud at the observed authority boundary. After rename and
before relaunch, the helper exclusively creates and durably syncs one mode-0600
target `.prompt` naming the exact old/new roots and telling the same Agent to
continue only remaining stale-reference migration, verify/report, and not move
again or broaden side effects. Success waits for that prompt to be consumed;
the lifecycle supplies durable `prompt_received` and `wake` evidence.

## Scope and recovery boundary

The private log moves at `logs/move-project.log`. The helper offers no Windows,
cross-filesystem, copy, merge, overwrite, address change, registry rewrite,
sibling relaunch, framework/Project CLI/TUI, version, integration, or
communication claim. Follow the parent router's observed-phase recovery table
and complete its target-origin acceptance checklist; helper success alone is not
full acceptance.
