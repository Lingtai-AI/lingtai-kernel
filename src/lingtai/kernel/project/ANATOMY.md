---
related_files:
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/project/CONTRACT.md
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/adapters/project_workspace.py
  - src/lingtai/cli_project.py
  - src/lingtai/tools/psyche/settings.py
  - docs/references/project-create.md
  - tests/test_project_creation.py
maintenance: |
  Keep this map reciprocal with the Project Contract and parent kernel Anatomy.
  Update it with changed Project ownership, composition, or state; code remains
  the structural source of truth.
---
# project

`kernel/project` owns the small Core boundary for creating one fresh local
Project seed. Its Contract defines the CLI-visible behavior.

## Components

- `__init__.py` — request/seed/result values, one `ProjectWorkspacePort`, input
  validation, and `ProjectCreationUseCase`.
- `src/lingtai/adapters/project_workspace.py` — the filesystem implementation
  that exclusively creates `.lingtai`, writes the seed, and calls an injected
  reader validator.
- `src/lingtai/cli_project.py` — parser and composition root for caller inputs,
  wrapper preset loading, the adapter, and output.

## Connections

`cli_project` calls wrapper `agent.load_preset`, asks Psyche's public v1
serializer for the owner-document content, and supplies the same init and
Psyche owner readers used by reconstruction. Project Core receives that content
as an opaque seed string and depends only on `ProjectWorkspacePort`; the adapter
depends inward on Core values.

## Composition

`lingtai-agent project create --dir ROOT --name AGENT --preset PRESET
--covenant-file FILE` constructs a request, asks the use case to create a seed,
and emits a small result. It never calls `Agent.start` or `cli.run`.

## State

Success writes `ROOT/.lingtai/` with `human` and the named agent, their
mailboxes and manifests, plus the named agent's `init.json` and
`settings/psyche.json`. No global or runtime state is written.

## Notes

The public command procedure is
[`docs/references/project-create.md`](../../../../docs/references/project-create.md).
