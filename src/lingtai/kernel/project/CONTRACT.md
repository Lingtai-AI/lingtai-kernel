---
name: project-creation
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/project/ANATOMY.md
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
  Keep this small governed Project boundary reciprocal with its Anatomy and root
  Contract. Update the Ports, adapters, focused tests, manuals, and this contract
  together when observable creation or inspection behavior changes.
---
# Project ownership

## Purpose

The Project component owns two narrow local CLI use cases:

- `lingtai-agent project create` creates one new `.lingtai` seed below a
  caller-selected existing directory.
- `lingtai-agent project inspect --root ROOT` reports one-level structural facts
  about the final `ROOT/.lingtai` path without changing or fully validating it.

Neither use case starts an Agent or creates provider, MCP, registry, venv,
runtime, TUI, or global state. Inspection is migration infrastructure; it does
not itself repair the historical infant-root lifecycle defect.

## Behavior

Creation writes a `human` pseudo-agent and one named agent with mailboxes,
manifests, a preset-derived `init.json`, and the Psyche-owned caller covenant.
Its existing fresh-only behavior remains unchanged.

Inspection returns exactly one state: `absent` when only the final `.lingtai`
entry is missing, `empty` when that real directory has zero non-human agent
workdir candidates, or `populated` when it has at least one candidate. It also
returns candidate counts split by regular `init.json` presence. These are
mechanical observations, not valid/invalid Project claims. Callers and coding
agents must not interpret `empty` as permission to delete, adopt, repair, or
retry creation over the retained tree.

## Port

`ProjectWorkspacePort.create(seed)` is the fresh-creation outbound Port.
`ProjectInspectionPort.inspect() -> ProjectInspectionObservation` is the
separate read-only outbound Port. The observation contains only final-root
presence and three coherent nonnegative counts. `ProjectInspectionUseCase`
validates those facts and alone selects the public `ProjectState`.

The success payload is stable and path-free:

```json
{"agent_candidates":{"total":1,"with_init":1,"without_init":0},"state":"populated","status":"inspected"}
```

## Adapters

`FilesystemProjectWorkspaceAdapter` exclusively creates and validates a fresh
seed. `FilesystemProjectInspectionAdapter` uses full non-following filesystem
metadata, rejects exposed Windows reparse points, streams one-level `.lingtai`
entries, and uses `WorkdirLayout` for the canonical `.agent.json` and `init.json`
marker names. It never opens those files or walks mailboxes, settings, prompts,
messages, logs, histories, or secrets.

`cli_project` is the composition root. Only the create branch loads a preset,
reads a covenant, or imports Agent/Psyche readers. The inspect branch constructs
only its filesystem adapter and renders deterministic JSON or two-line human
output.

## Contract rules

1. Creation retains its existing contract: caller root is an existing directory,
   `.lingtai` must not exist, caller covenant is nonempty UTF-8, preset is
   loadable, current owner readers accept the generated seed, and a handled
   failure removes only the target created by that call.
2. Inspection requires a real, readable caller directory and does not follow a
   caller-root symlink. Invalid, missing, non-directory, or unreadable roots fail
   with `invalid_project_root` and no mutation.
3. Only a missing final `ROOT/.lingtai` is `absent`. A present `.lingtai` symlink,
   file, or other non-directory shape fails with `unsafe_project_structure`;
   traversal errors fail with `project_inspect_failed`.
4. A non-human direct child is an agent workdir candidate only when it is a real
   directory containing at least one regular canonical marker: `.agent.json` or
   `init.json`. `human`, ordinary root files, and markerless helper directories
   do not count. Symlinked/special direct entries and non-regular candidate
   markers fail closed with `unsafe_project_structure`.
5. Every candidate with a regular `init.json` increments `with_init`; every other
   candidate increments `without_init`; the two counts must sum to `total`.
   Therefore an all-init-absent stored blueprint is `populated`, a current fresh
   creation seed is `populated` with init, and human/helper-only residue is
   `empty`.
6. Inspection reads no marker content and recurses into no child. Invalid JSON,
   prompt ownership, orchestrator cardinality, mixed initialization, liveness,
   and runtime readiness remain outside this state vocabulary.
7. Success exits zero. Project inspection errors use the existing Project CLI
   error envelope on stderr and exit one. Output contains no caller path by
   default and is deterministic for the same observation.
8. Inspection performs no write, cleanup, adoption, preparation, publication,
   reset, preset load, environment mutation, process operation, network request,
   or runtime construction. Existing partial roots remain untouched, and the
   fresh-only create command continues to refuse every existing `.lingtai`.

## Contract tests

`tests/test_project_inspection.py` proves Core classification and coherence,
absent zero-write behavior, human/helper-only emptiness, stored blueprints,
initialized and mixed multi-agent facts, canonical-marker non-parsing, unchanged
bytes/paths, symlink/non-directory/malformed/traversal failures, and exact
JSON/human/error rendering. `tests/test_project_creation.py` remains the focused
creation regression for seed shape, existing-target refusal, owner-reader
acceptance, and no Agent start.

## Maintenance

Keep inspection mechanical and one-level. Expanding it into runtime validation,
repair, staging/publication, reset, or TUI lifecycle ownership requires a
separate authorized vertical slice and corresponding Ports, adapters, tests, and
manual changes. Keep the Contract/Anatomy links reciprocal and re-run Project,
CLI, architecture, and docs-governance checks after edits.

Where the platform exposes Windows file attributes, the caller root, `.lingtai`, each direct child, and every marker required to be a regular file must not carry `FILE_ATTRIBUTE_REPARSE_POINT`.

## Concurrency and mutation boundary

Inspection is a best-effort observation of a quiescent tree. It is not a security or authorization boundary for mutation. Concurrent replacement can invalidate the observation, so every future mutation caller must revalidate its targets with mutation-specific safeguards immediately before mutation. This Draft does not promise handle-relative traversal.
