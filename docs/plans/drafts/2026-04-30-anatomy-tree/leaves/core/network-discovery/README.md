# Network Discovery

## What

Agents discover each other by crawling the filesystem under a shared `.lingtai/` base directory. Each subdirectory containing a `.agent.json` manifest is treated as an agent node. The system builds a unified `AgentNetwork` with three edge layers: avatar spawning tree, declared contacts, and actual mail history.

## Contract

### Directory-Based Discovery

The discovery process scans `<base_dir>` (typically `.lingtai/`) for direct child directories. For each child:

1. Check for `<child>/.agent.json`.
2. If present and valid JSON, extract `address` (the working dir path, primary key) and `agent_name`.
3. Register as an `AgentNode`.

There is **no registry file, no daemon, no broadcast**. Discovery is purely filesystem: if a directory has `.agent.json`, it's an agent.

### `.agent.json` Validation

A `.agent.json` file is the agent's on-disk manifest, written by `WorkingDir.write_manifest()`. Required fields:

- `address`: string — the agent's working directory path (primary key in the network graph).
- `agent_name`: string — human-readable name.

Optional fields written by the kernel: `started_at`, `admin`, `stamina`, `soul_delay`, `capabilities`, `combo`, `nickname`.

### Three Edge Layers

| Layer | Source file | What it represents |
|-------|-----------|---------------------|
| **avatar** | `delegates/ledger.jsonl` | Parent → child spawning tree (who created whom) |
| **contact** | `mailbox/contacts.json` | Declared "knows about" edges (address book) |
| **mail** | `mailbox/inbox/` + `mailbox/sent/` | Actual communication history (aggregated sender→recipient edges) |

### Peer Addressing

Agents address each other by **relative name** (the directory name under `.lingtai/`) or **absolute path**. The `resolve_address()` function handles both:

- Relative name (e.g. `"researcher"`) → `<base_dir>/researcher`
- Absolute path (e.g. `"/Users/.../.lingtai/researcher"`) → used as-is

The `is_agent()` check validates by looking for `.agent.json`. The `is_alive()` check reads `.agent.heartbeat` freshness (default 2s threshold). Human agents (where `admin` is explicitly `null` in `.agent.json`) are always considered alive.

### Avatar Ledger Format

Each line in `delegates/ledger.jsonl` is a JSON object with:
- `event`: `"avatar"` (for spawn records)
- `working_dir`: child's address (relative name or absolute path)
- `name`: child's agent name
- `ts`: spawn timestamp (epoch float)
- `mission`: task description string
- `capabilities`: list of capability names
- `provider`, `model`: LLM config at spawn time

## Source

| Component | File | Lines |
|-----------|------|-------|
| Network builder | `src/lingtai/network.py` | 1-331 |
| `build_network()` | `src/lingtai/network.py` | 306-331 |
| `_discover_agents()` | `src/lingtai/network.py` | 143-165 |
| `_build_avatar_edges()` | `src/lingtai/network.py` | 168-216 |
| `_build_contact_edges()` | `src/lingtai/network.py` | 219-238 |
| `_build_mail_edges()` | `src/lingtai/network.py` | 273-299 |
| `resolve_address()` | `src/lingtai_kernel/handshake.py` | 13-22 |
| `is_agent()` | `src/lingtai_kernel/handshake.py` | 25-27 |
| `is_alive()` | `src/lingtai_kernel/handshake.py` | 39-55 |
| Manifest writing | `src/lingtai_kernel/workdir.py` | (WorkingDir.write_manifest) |

## Why

Filesystem-based discovery was chosen over a registry daemon because agents must survive process death (SUSPENDED state) — a daemon would itself die. `.agent.json` as the single source of truth means any tool (shell, TUI, portal) can read the network without importing the kernel. The three edge layers (avatar, contact, mail) capture different trust levels: who you created, who you declared, who you actually talk to — collapsing them would lose the distinction between intention and reality.

## Related

- **agent-state-machine**: `is_alive()` checks heartbeat freshness, linking discovery to lifecycle state.
- **preset-materialization**: Avatar spawn records carry the preset's provider/model for network introspection.
- **`avatar` tool**: Creates new agents, writing to `delegates/ledger.jsonl`.
- **`email` tool**: Creates mail edges by writing to `mailbox/` structure.
- **`lingtai-portal-guide` skill**: Visualizes the network topology built by this subsystem.
