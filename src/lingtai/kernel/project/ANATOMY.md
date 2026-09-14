---
related_files:
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/project/CONTRACT.md
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/kernel/workdir.py
  - src/lingtai/adapters/project_workspace.py
  - src/lingtai/cli_project.py
  - src/lingtai/tools/psyche/settings.py
  - docs/references/project-create.md
  - docs/references/project-inspect.md
  - tests/test_project_creation.py
  - tests/test_project_inspection.py
maintenance: |
  Keep this map reciprocal with the Project Contract and parent kernel Anatomy.
  Update it with changed Project ownership, composition, or state; code remains
  the structural source of truth.
---
# project

`kernel/project` owns the small Core boundary for fresh local Project creation
and one-level, read-only Project inspection. Its Contract defines both CLI-visible
behaviors without assigning runtime-validity or repair policy to this component.

## Components

- `ProjectWorkspacePort`, creation values, validation, and
  `ProjectCreationUseCase` own the fresh-seed Core flow
  (`src/lingtai/kernel/project/__init__.py:28-57,93-98,121-164,198-206`).
- `ProjectState`, inspection observation/result values,
  `ProjectInspectionPort`, and `ProjectInspectionUseCase` own mechanical state
  classification (`src/lingtai/kernel/project/__init__.py:60-106,167-195,209-227`).
- `FilesystemProjectWorkspaceAdapter` exclusively creates and validates one
  `.lingtai` seed (`src/lingtai/adapters/project_workspace.py:41-93`).
- `FilesystemProjectInspectionAdapter` reads only direct entry types and the
  `WorkdirLayout` manifest/init marker paths; it never opens marker content
  (`src/lingtai/adapters/project_workspace.py:111-191`).
- `add_project_parser`, `_handle_inspect`, and `handle_project_command` compose
  caller input, adapters, and deterministic output
  (`src/lingtai/cli_project.py:23-48,121-175`).

## Connections

For creation, `cli_project` calls wrapper `agent.load_preset`, asks Psyche's
public v1 serializer for owner-document content, and injects the current init and
Psyche readers. For inspection it constructs only the filesystem inspection
adapter; that adapter depends inward on Project Core and reuses marker names
from `kernel.workdir`, while Core alone maps observations to public states.

## Composition

`lingtai-agent project create ...` publishes one fresh seed.
`lingtai-agent project inspect --root ROOT [--json]` observes an existing caller
root without calling creation, Agent, preset, Psyche, registry, or runtime paths.

## State

Creation success writes `ROOT/.lingtai/` with `human` and one named agent.
Inspection owns no persistent state: its immutable observation/result values live
only for the call, and it neither creates nor alters the caller tree.

## Notes

The procedures are [`project-create.md`](../../../../docs/references/project-create.md)
and [`project-inspect.md`](../../../../docs/references/project-inspect.md).
Inspection state is mechanical evidence, not a runtime validity verdict or an
infant-root repair mechanism.
