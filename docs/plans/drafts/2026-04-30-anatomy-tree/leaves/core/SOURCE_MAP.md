# Core Leaves — Source File Map

When modifying a source file below, check the corresponding leaf README's `## Source` table.
If line numbers shifted, update them. If semantics changed, update `## Contract` too.

| Source file (under `src/`) | Leaf directory |
|---|---|
| `lingtai_kernel/state.py` | `agent-state-machine/` |
| `lingtai_kernel/base_agent.py` (state transitions, heartbeat, AED, `_run_loop`) | `agent-state-machine/` |
| `lingtai_kernel/handshake.py` (resolve_address, is_agent, is_alive) | `network-discovery/` |
| `lingtai/network.py` | `network-discovery/` |
| `lingtai/config_resolve.py` | `config-resolve/` |
| `lingtai/init_schema.py` (validate_init) | `config-resolve/`, `preset-allowed-gate/` |
| `lingtai/presets.py` | `preset-materialization/` |
| `lingtai/agent.py` (_activate_preset, _read_init, _setup_from_init) | `preset-materialization/` |
| `lingtai/preset_connectivity.py` | `preset-allowed-gate/` |
| `lingtai/init_schema.py` (preset block validation) | `preset-allowed-gate/` |
| `lingtai/venv_resolve.py` | `venv-resolve/` |

> Some source files map to multiple leaves. `init_schema.py` appears twice because
> it validates both the general config shape and the preset block specifically.

## Lifecycle of the Why

Each leaf has a `## Why` section that records the original pain behind a design choice.
This is a **hypothesis**, not a law. It has a shelf life.

When making a major change to a subsystem, ask one question before defending the status quo:

> **Is this pain still real?**

If the constraint that motivated the design has been relieved by new capabilities,
architectural shifts, or changed requirements — update or remove the Why.
A stale Why is worse than no Why: it chains future builders to battles already won.

Three tiers of stale-Why detection:
1. **Line numbers drift** — easy to spot, mechanical to fix.
2. **Behavioral claims diverge** — harder; requires reading the code, not just counting lines.
3. **Pain no longer felt** — hardest; requires understanding whether the original constraint still holds in the current world.

The first refactor that touches a leaf is the moment of truth for all three tiers.
