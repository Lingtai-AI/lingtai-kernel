---
related_files:
  - src/lingtai/kernel/project/CONTRACT.md
  - src/lingtai/kernel/project/ANATOMY.md
  - src/lingtai/cli_project.py
maintenance: |
  Keep this short public procedure aligned with the Project CLI contract. Do not
  add runtime, preset-management, or TUI workflow until separately authorized.
---
# Create a local Project

Create one fresh local LingTai seed without starting an agent:

```text
lingtai-agent project create --dir ROOT --name AGENT --preset PRESET --covenant-file FILE [--json]
```

`ROOT` must already be a directory without `.lingtai`. `FILE` is nonempty UTF-8
covenant text supplied by the caller. `PRESET` is loaded through the current
wrapper preset loader.

On success the command writes `ROOT/.lingtai/` with a `human` pseudo-agent and
one named agent, including their mailboxes, manifests, and the named agent's
`init.json` plus `settings/psyche.json` (which receives the caller covenant).
The CLI serializes that document through Psyche's owner primitive and validates
both generated owner documents before reporting success.

The command is data-only: it does not start an Agent, install a venv, contact a
provider, launch an MCP, or create TUI/global project state. Starting the agent
is a separate command:

```text
lingtai-agent run ROOT/.lingtai/AGENT
```
