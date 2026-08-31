---
related_files:
  - src/lingtai/kernel/agent_guardian/ANATOMY.md
  - src/lingtai/kernel/agent_guardian/CONTRACT.md
  - src/lingtai/kernel/agent_guardian/BEHAVIORS.md
  - src/lingtai/cli_guardian.py
maintenance: |
  Keep this thin operator manual aligned with CLI help, JSON output, ledger
  corruption behavior, and the shadow-only/no-install/no-actuator boundary.
---
# Agent Guardian Manual

Run one observation with:

```text
lingtai-agent guardian --agent-dir <path> --once
```

Omit `--once` for the foreground loop. One guardian per directory is enforced
by `system/.agent_guardian.lock`; a second exits nonzero. The command prints
compact JSON containing the four-way verdict, five-way shadow plan, intent,
confirmation, and bounded evidence digest/status. It never starts or resumes an
agent, sends a signal, runs CPR, installs a service, or daemonizes.

The durable authority is `logs/agent_lifecycle.jsonl`. Explicit suspend remains
active without TTL and across boot/crash until a matching explicit CPR
event clears it.

Either an existing legacy `.suspend` marker or durable intent at ordinary
`lingtai-agent run` is the **early refusal**: it returns mechanical code
`explicit_suspend_active` before Agent, provider, MCP, or ToolPlugin
construction and preserves the marker. A marker visible at the
post-construction recheck blocks registration; cleanup removes only stale
`.sleep`/`.refresh` and never silently removes `.suspend`. A durable suspend
arriving after the early read is
the separate **late race**: locked boot registration re-reads durable intent and
refuses its append. In either late case the constructed-but-unstarted Agent is
stopped and no boot row/start occurs. Matching CPR owns durable-intent and marker
clearance before a later run. The reader does not repair or ignore corrupt/
unsupported/torn or impossible boot-after-suspend history: it exits nonzero.

Descriptor reads and unique same-descriptor appends are bounded to 4 MiB
cumulative raw bytes, 64 KiB per record, and 4096 physical records, including
exact duplicate physical rows. A write that
would exceed a bound is refused before changing the file, while an exact
duplicate event-id retry stays byte-identical at the limit and re-fsyncs the
existing event reachability. Boot PID evidence is
restricted to `1..2147483647`, and recorded working-dir/executable/command paths
must be resolved absolute control-free strings with both agent-dir fields equal.
Changed
guardian policy records immediately; unchanged policy checkpoints once every
24 hours, with backward wall-clock movement forcing a checkpoint. The daily
count-only window is about 11.2 years before boot, intent, or changed-policy
facts consume rows. This slice has no compaction or retention: either finite
limit fails closed, and retention remains future separately authorized work.
Malformed/recursive/oversized JSON and invalid Unicode never escape as raw
tracebacks. Guardian `.agent.json` and Agent Record reads are independently
bounded to 1 MiB on one binary descriptor; growth or allocation failure becomes
conservative malformed/unreadable evidence. New v2 mechanical codes include
`ledger_agent_dir_unavailable`,
`ledger_changed_during_read`, `invalid_event_encoding`,
`boot_while_suspend_active`, `guardian_actor_id_mismatch`, and
`invalid_presence_sample`; ordinary locked boot refusal remains
`explicit_suspend_active`.

Guardian freshness is 120 seconds, separate from public `liveness`; stale or
uncertain evidence gets the required second sample even under `--once`.
Linux supplies full exact evidence. On macOS, a libproc miss uses only
`os.kill(pid, 0)`, an existence probe that delivers no signal: `ESRCH` alone is
absent and every accessible/inaccessible uncertainty remains unknown. On
Windows, an existing PID remains unknown when exact command, executable, or
stopped-state evidence is unavailable; the guardian does not claim parity.

Plans are observations only: `none`, `hold_explicit_suspend`, `would_sigcont`,
`would_launch`, or `observe_only`. Future actuation still requires a separate
recovery lease plus durable attempt budget/backoff and separate authorization.
