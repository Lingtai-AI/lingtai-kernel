---
related_files:
- CONTRACT.md
maintenance: |
  Tracks the external, non-model-generated liveness/mailbox-probe contract for headless controllers; update it whenever the runtime lock/heartbeat files, `.status.json` fields, or the probe/ack message shape change.
---

# Headless Runtime Contract

This contract is for external controllers that need to prove a LingTai agent is
real, running, and reachable without relying on model-generated text.

## Runtime Liveness

- `lingtai-agent run <agent-dir>` creates a real runtime process.
- The process owns `.agent.lock` through the workdir lock.
- `.agent.heartbeat` is written by the runtime heartbeat loop while the process
  is alive.
- `.status.json` is the public runtime metadata snapshot. It includes
  `identity.agent_id` plus `runtime.pid`, `runtime.running`,
  `runtime.last_heartbeat`, and `runtime.heartbeat_age_seconds`.

The heartbeat starts during early runtime startup. Until startup finishes, the
heartbeat loop only emits liveness/status metadata; signal and notification
processing begins after the main loop and mail listener are ready.

## Mailbox Probe

Controllers may write a single-recipient probe to the human pseudo-agent outbox:

```json
{
  "from": "human",
  "to": ["agent-address"],
  "type": "runtime_probe",
  "correlationId": "controller-correlation-id",
  "taskId": "controller-task-id",
  "message": "{\"type\":\"runtime_probe\",\"correlationId\":\"controller-correlation-id\",\"taskId\":\"controller-task-id\"}"
}
```

The real `FilesystemMailService` poller claims the message, moves it to
`human/mailbox/sent/<probe-id>/`, writes the probe into the agent inbox, and
writes a structured `runtime_probe_ack` into `human/mailbox/inbox/`.

The ack carries the same `correlationId` and `taskId`, plus `in_reply_to` and a
JSON `structured` object. It is a runtime-managed acknowledgement, not a model
reply and not fallback output.

## Boundaries

Tracked source must not include `.lingtai/`, mailbox state, logs, temp dirs,
`.env`, API keys, hidden prompts, raw prompts, chain-of-thought, or daemon raw
logs. Probe replies expose only public runtime metadata.
