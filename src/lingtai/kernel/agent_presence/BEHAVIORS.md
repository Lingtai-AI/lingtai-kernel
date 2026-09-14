---
name: agent-presence-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/agent_presence/CONTRACT.md
  - src/lingtai/kernel/agent_presence/ANATOMY.md
  - src/lingtai/kernel/agent_presence/__init__.py
  - src/lingtai/adapters/posix/agent_presence.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  behavior clause of the agent-presence contract changes, update the guarding
  LABT here in the same change.
---
# Agent Presence Store Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/agent_presence/CONTRACT.md`. Pinned pytest commands must run
from the repo root with the project's Python (any interpreter that resolves
`lingtai` from `<repo>/src` and has pytest installed).

## Behavior AP001 — present-but-malformed manifests still count as agents and human manifests stay alive without a heartbeat

- **id**: AP001
- **title**: present-but-malformed manifests still count as agents and human manifests stay alive without a heartbeat
- **guards**: `agent-presence-store` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch working directory `<scratch>` outside any agent directory
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_agent_presence.py -q` and capture the outcome.
2. In `<scratch>`, write `.agent.json` containing malformed JSON (e.g. `{not json`) and run the presence observation path against it using the production adapter (`PosixAgentPresenceStoreAdapter`) via a short Python snippet with `PYTHONPATH=<repo>/src`; record the returned manifest observation.
3. In a second scratch directory, write `.agent.json` with `{"admin": null}` and a stale `.agent.heartbeat`, then observe alive-ness through `observe_alive`; record whether the heartbeat was consulted.
4. Publish a heartbeat with `wall_seconds=123.0` through the adapter and read back the `.agent.heartbeat` bytes with a hexdump.

### Expected evidence
- [ ] Step 1: the shared fake/production conformance suite passes, pinning the tri-state classification, human-always-alive, strict freshness threshold, shared environment resolution (10-second default and invalid-value fallback), and byte-exact heartbeat writes.
- [ ] Step 2: the malformed manifest is classified `MALFORMED`, not absent, and still counts as an agent (`is_agent` true).
- [ ] Step 3: a valid manifest with `admin` missing-or-null is always alive, and the heartbeat file was never read for that observation.
- [ ] Step 4: `.agent.heartbeat` contains exactly `123.0` with no trailing newline.

### Pass / Fail
Pass when every evidence item above holds. Fail on any mismatch — e.g. a malformed manifest classified as absent, a human observed through heartbeat, or heartbeat bytes that are not byte-exact — and record the evidence trail in the task report.
