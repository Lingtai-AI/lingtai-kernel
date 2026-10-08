---
name: avatar-lifecycle-reference
description: |
  Avatar lifecycle reference: detached life, ledger/state, derived-child
  authority, boot verification, platform launch, escalation, and footprint.
version: 1.1.0
last_changed_at: 2026-09-09T11:24:00Z
related_files:
- src/lingtai/tools/avatar/manual/SKILL.md
- src/lingtai/tools/avatar/manual/reference/spawn.md
- src/lingtai/tools/avatar/__init__.py
- src/lingtai/tools/avatar/_launcher.py
- src/lingtai/tools/avatar/CONTRACT.md
- src/lingtai/adapters/avatar_launcher.py
- src/lingtai/adapters/posix/avatar_launcher.py
- src/lingtai/adapters/windows/avatar_launcher.py
- src/lingtai/cli.py
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
maintenance: |
  Keep this reference aligned with launcher/CLI authority, boot observation,
  ledger, and cleanup behavior. Keep the parent router concise; do not add an
  Avatar cleanup command or turn lifecycle facts into schema prose.
---

# Avatar lifecycle and authority

The [Avatar Manual](../SKILL.md) owns the direct call, SHOW and footprint route;
[spawn and identity](spawn.md) owns copy/mission/preview rules.

## Independent life and state

An avatar is a detached process with its own directory, history, molts and
lifecycle; it outlives the parent's context. Communicate by mail/email, not an
in-process handle. Quiet is not completion: do not send probe mail or retry
forever. Report blockers and the decision needed before handing off or molting.

```text
<parent>/delegates/ledger.jsonl        # append-only audit
<network-root>/<name>/                # direct sibling
  init.json  settings/psyche.json  .prompt
  logs/spawn.stderr  logs/agent.log
  .lingtai-derived-child.json         # only when Driver-granted
  system/ knowledge/ exports/ combo.json  # deep payload as applicable
```

Provider admission records `avatar_admission_decision` **before** real path /
existing-directory checks, so a rejected spawn can leave an admission audit.
The full `avatar` record follows launch and boot observation: name/basename,
mission, type, PID, boot status/error. Dry-run and mission-gate returns write
no record. Ledger liveness consults the stored `working_dir`; do not treat a
ledger line as current health evidence. Exceptions can leave a partial child
directory/init/`.prompt`; there is no atomic rollback promise. Preserve evidence
and inspect before retrying or cleaning; do not retry under a different name
to evade a refusal.

## Derived-child authority

Only a Driver-approved child-endpoint lease permits writing
`.lingtai-derived-child.json` before launch, outside managed `system/`.
Presence is restrictive—including malformed contents, directory or symlink;
legacy `system/derived_child.json` is restrictive too. Only both missing
relaxes the requirement. Unknown/I/O-error state is not absence.

Markers are not credentials or parent identity. They protect trusted same-user
launch/config continuity, not against a child editing its own directory. The
one-use opaque POSIX lease is consumed at launch; the inherited descriptor's
environment locator is not itself authority. CLI boot reads durable/environment
markers and requires real authority for nested daemon/avatar launches. Public
input, prompt and marker edits cannot grant it. Windows closes/rejects a POSIX
lease rather than silently dropping the restriction.

## Launch and boot observation

The selected child-init Python launches `[python, "-m", "lingtai", "run", <dir>]`.
stdin/stdout are disconnected; stderr goes to `logs/spawn.stderr`. A positive
PID and opaque handle permit nonblocking poll/release, not continuing ownership
of the peer. Launcher environment is inherited; newborn admin is cleared.

The manager checks `.agent.heartbeat` as a file every 0.1 seconds for up to
5.0 seconds **before** checking process exit. It does not verify freshness or
heartbeat contents. Boot `ok` therefore is not a live capability or human
receipt. Early process exit yields boot `failed` and an outer `error` response;
neither event within the window yields boot `slow` in a top-level `status=ok`
response with a warning. Handle release after slow does not terminate the child.

For an early-exit diagnostic the manager reads the entire stderr file, then
keeps the last 2,000 bytes plus a truncation prefix when needed, decodes with
replacement and includes exit code/path context. This is not a 2,000-byte total
response or a bounded file read. The returned stderr/path is **not redacted**;
it can contain private data. Inspect locally and sanitize before forwarding.

The selector uses POSIX when `os.name == "posix"`, otherwise Windows when
`sys.platform == "win32"`; unsupported hosts fail without a fallback. POSIX
starts a new session and termination targets only its owned process, not its
tree. Windows uses detached creation flags/`close_fds`; both termination methods
are forceful `TerminateProcess`. No parent-side handle release kills the child.

## Care and retirement

Record address, mission and delegation reason in Pad. Report scope ambiguity,
silence, budget or security concerns with evidence and an actionable decision.
For retirement follow the root footprint route: capture a handoff, check
liveness, report bytes, and get exact approval. Sleep, suspend and nirvana are
not interchangeable; nirvana is **permanent destruction** requiring its own
privileges and explicit owner authorization. Do not substitute filesystem
deletion. When the user is unavailable, stop after the report.
