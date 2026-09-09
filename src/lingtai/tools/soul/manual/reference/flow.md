---
name: soul-flow-reference
description: Operator procedure for Soul's opt-in flow gate, cadence, and recovery.
related_files:
- src/lingtai/tools/soul/manual/SKILL.md
- src/lingtai/tools/soul/flow.py
- src/lingtai/tools/soul/__init__.py
- src/lingtai/tools/soul/config.py
- src/lingtai/tools/soul/manual/reference/consultation.md
- tests/test_soul.py
- tests/test_tool_family_soul_migration.py
maintenance: |
  Keep the gate, disabled/ongoing paths, cadence, and operator procedure accurate with Soul's implementation and router anchors.
---

# Soul flow and opt-in gate

## Gate and operator procedure

`LINGTAI_SOUL_FLOW_ENABLED` is the sole opt-in gate, read from the live process environment. Missing, blank, or unrecognized values disable flow; `1`, `true`, `yes`, and `on` enable it (case-insensitive, whitespace trimmed). No Soul action changes it.

1. With operator authorization, set `LINGTAI_SOUL_FLOW_ENABLED=1` in the agent's configured environment source. For a configured `env_file`, refresh reloads its assigned values; for launcher-only environment, relaunch from the changed launcher. Editing another shell's environment does not change the running process.
2. Use `settings` to confirm `flow_enabled: true` before calling:

```json
{"action":"flow","input":{},"reasoning":"start an opted-in consultation"}
```

3. To disable, explicitly set `0` in the same source and apply it through the same lifecycle procedure. Removing a line from an env file may leave an inherited process value; verify SHOW reports false. Do not use a large delay as a mute switch.

With the gate off, `flow` returns `status: "disabled"` before locking, waiting for IDLE, or starting a thread. A defensive gate also stops stray timer callers. This is expected: do not retry until the environment changes. Other Soul actions remain available; deliberate `inquiry` needs no opt-in.

## Cadence, cost, and recovery

Cadence/count belong to [configuration](configuration.md); changing delay alone cannot enable or disable flow. Timers fire on IDLE. A voluntary trigger waits for IDLE up to the live delay; an already-ongoing fire is rejected, not duplicated. A successful trigger acknowledges immediately, not completed reflection.

Each enabled fire reads current/past-self context and may run `1 + K` parallel LLM calls, so opt-in protects recurring cost and snapshot privacy. Follow [consultation mechanics](consultation.md) for output, bounded tool refusals, late-result handling, and storage. Use `dismiss` for its notification, not to disable the gate.
