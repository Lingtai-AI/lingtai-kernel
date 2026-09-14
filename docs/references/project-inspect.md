---
related_files:
  - src/lingtai/kernel/project/CONTRACT.md
  - src/lingtai/kernel/project/ANATOMY.md
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/kernel/workdir.py
  - src/lingtai/adapters/project_workspace.py
  - src/lingtai/cli_project.py
  - tests/test_project_inspection.py
maintenance: |
  Keep this procedure aligned with the Project inspection Contract, CLI payload,
  and fail-closed filesystem behavior. Do not turn observation into repair.
---
# Inspect a local Project

Inspect the final Project root without starting or changing anything:

```text
lingtai-agent project inspect --root ROOT [--json]
```

`ROOT` must be an existing, readable real directory. The command reports:

- `absent` when `ROOT/.lingtai` does not exist;
- `empty` when `.lingtai` is a real directory with zero non-human agent workdir
  candidates; or
- `populated` when at least one non-human candidate exists.

A candidate is a direct, real directory other than `human` with a regular
`.agent.json` or `init.json`, using the kernel's canonical `WorkdirLayout` marker
names. Markerless helper directories do not count. Counts distinguish candidates
with a regular `init.json` from candidates without one, so an all-init-absent
stored blueprint remains `populated` while human/helper-only residue is `empty`.
The command checks only marker shape and never parses their bytes.

JSON output has this stable path-free shape:

```json
{"agent_candidates":{"total":1,"with_init":1,"without_init":0},"state":"populated","status":"inspected"}
```

Without `--json`, the same facts render deterministically:

```text
Project state: populated
Agent workdir candidates: 1 (with init: 1, without init: 0)
```

Invalid caller roots, a symlinked or non-directory `.lingtai`, unsupported direct
entry shapes, non-regular candidate markers, and traversal failures return a
stable Project error on stderr and exit one. Success exits zero.

This is read-only migration infrastructure, not a validity checker or the infant
fix. It does not adopt, delete, repair, prepare, publish, reset, load a preset,
read prompts/messages/secrets, start an Agent, or touch global/runtime/TUI state.
An `empty` or failed observation is never permission to mutate the retained tree;
`project create` still refuses every existing `.lingtai`.

Inspection is a best-effort observation of a quiescent tree, not a security or authorization boundary for mutation. Future mutation code must revalidate its targets; see [the Project contract](../../src/lingtai/kernel/project/CONTRACT.md).

On Windows, filesystem reparse points (including junctions) at the caller root, `.lingtai`, direct-child, or regular-file marker boundaries are reported as unsafe/invalid rather than traversed.
