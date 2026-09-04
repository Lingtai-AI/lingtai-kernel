---
name: project-creation
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/project/ANATOMY.md
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/adapters/project_workspace.py
  - src/lingtai/cli_project.py
  - src/lingtai/tools/psyche/settings.py
  - docs/references/project-create.md
  - tests/test_project_creation.py
maintenance: |
  Keep this small governed Project boundary reciprocal with its Anatomy and root
  Contract. Update the Port, adapter, focused tests, and this contract together
  when its observable creation behavior changes.
---
# Project creation

`lingtai-agent project create` creates one new local `.lingtai` seed below a
caller-selected existing directory. It writes a `human` pseudo-agent and one
named agent with mailboxes, manifests, an `init.json` based on the caller's
preset, and `settings/psyche.json` containing the caller covenant. It does not start an Agent or create
runtime, provider, MCP, registry, venv, or TUI state.

## Boundary

The Core owns request validation and a complete `ProjectSeed`. Its one
technology-neutral `ProjectWorkspacePort.create(seed)` operation is implemented
by the filesystem adapter. `cli_project` is the composition root: it reads the
caller covenant, serializes it through Psyche's public v1 owner-document
serializer, uses wrapper `load_preset`, and injects both the opaque serialized
content and the current init/Psyche readers. Project Core neither imports Psyche
nor names its schema version or value keys.

## Guarantees

- The root must already be a directory; an existing `.lingtai` is refused.
- The caller supplies a nonempty UTF-8 covenant and a loadable preset.
- Psyche is the only owner-document serialization authority; Project Core
  forwards the composition-root-provided content byte-for-byte into its seed.
- The adapter creates the `.lingtai` directory exclusively, writes the complete
  seed, then verifies that initial init and Psyche owner documents can be read
  by the current reconstruction seams.
  If creation or validation fails, it removes only the directory it just created.
- Results and errors contain stable small fields and do not start an Agent.

## Non-goals

This first slice does not add agents to existing projects, recover partial
projects, manage presets, launch an agent, or provision a venv, addon, MCP, or
TUI project record. The user procedure is
[`docs/references/project-create.md`](../../../../docs/references/project-create.md).
