---
related_files:
  - src/lingtai_kernel/ANATOMY.md
  - src/lingtai_kernel/maintenance/__init__.py
  - src/lingtai_kernel/maintenance/retention.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# maintenance

Kernel-owned maintenance reporters and future maintenance actions.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `retention.py` — dry-run retention reporter for issue `Lingtai-AI/lingtai#363`. It defines cleanup candidate records (`retention.py:73-86`), read-only high-footprint records (`retention.py:110-122`), and the report scanner (`retention.py:155-196`). It reports stale candidates for terminal daemon run dirs, historical `mailbox/sent/` copies, opt-in archive mail, and rebuildable `logs/log.sqlite`, plus separate footprint observations for portal replay, agent logs, and agent history (`retention.py:331-351`, `retention.py:404-500`). It has no delete/apply function.
- `__init__.py` — public exports for the maintenance package (`__init__.py:3-21`).

## Connections

- **Inbound:** `lingtai.cli` wires `lingtai-agent maintenance cleanup <target>` to `retention.scan_retention()`.
- **Outbound:** Uses only stdlib filesystem reads and JSON parsing. It deliberately does not import runtime services or mutate kernel state.

## Composition

Parent: `src/lingtai_kernel/`. Siblings include `services/` for authoritative log/mail services and `base_agent/` for lifecycle state.

## State

This package owns no durable state. The retention reporter reads these existing runtime paths:

- `.portal/topology.jsonl` under a direct `.lingtai` root as a portal replay footprint only
- `daemons/<run_id>/daemon.json` and `.heartbeat`
- `mailbox/sent/`, `mailbox/archive/`
- `mailbox/inbox/`, `mailbox/outbox/`, `mailbox/schedules/` as protected operational mail state
- `logs/events.jsonl`, `logs/token_ledger.jsonl`, `logs/refresh_failed_permanent.json` as protected authoritative or recovery logs
- `logs/soul_flow.jsonl` and `logs/refresh_relaunch.log` as read-only footprint observations only: never cleanup candidates, not protected-section entries
- `logs/log.sqlite` as a rebuildable report candidate only
- `history/chat_history_archive.jsonl` and `history/snapshots/*.json` as history footprints only
- `tmp/tool-results/` as protected artifact state
- `.agent.lock`, `.agent.heartbeat`, `.status.json` as lifecycle protection signals

## Key Invariants

- The package is report-only: no deletion, move, archive, rewrite, or truncation code path exists.
- Footprints are separate from candidates. Their `risk` and `recommendation` fields guide future design work; they never authorize cleanup.
- `mailbox/outbox/` and `mailbox/schedules/` are pending-delivery/future-send queues and are never candidates.
- Inbox mail is never a candidate, regardless of read state; read does not prove handled.
- `logs/events.jsonl`, `logs/token_ledger.jsonl`, and `logs/refresh_failed_permanent.json` are protected authoritative or recovery logs. `logs/soul_flow.jsonl` and `logs/refresh_relaunch.log` are footprint-only observations and never candidates. `logs/log.sqlite` is rebuildable and may be reported as a candidate when stale.
- Active agents, ASLEEP agents, SUSPENDED agents, held `.agent.lock`, and fresh `.agent.heartbeat` protect an agent's interior retention classes from becoming candidates; read-only footprint observations do not bypass that protection.
- Daemon run dirs require terminal `daemon.json.state`, old age, and stale/missing daemon `.heartbeat`; mtime alone is not enough.

## Notes

- CLI output is intentionally stable JSON under `--json`; human output is a compact summary and reminds operators no files changed.
