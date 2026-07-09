# Contributing to lingtai-kernel

Thank you for helping improve the LingTai Python runtime. GitHub discovers this root file as the repository contributing guide.

## Start here

- Repository navigation: [`ANATOMY.md`](ANATOMY.md)
- Claude Code / coding-agent guidance:
  [`docs/references/claude-code-guide.md`](docs/references/claude-code-guide.md)
- Source-root anatomy: [`src/lingtai_kernel/ANATOMY.md`](src/lingtai_kernel/ANATOMY.md)
- Rust sidecar notes: [`crates/lingtai-search-sidecar/README.md`](crates/lingtai-search-sidecar/README.md)
- Native Windows / PowerShell support status (open PR-candidate stack, not yet merged/released):
  [`docs/references/windows-support-status.md`](docs/references/windows-support-status.md)

## Community and safety

- Code of conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- Security reporting: [`SECURITY.md`](SECURITY.md)
- Support guidance: [`SUPPORT.md`](SUPPORT.md)

## Workflow

Use a branch and worktree for non-trivial changes, keep changes focused, and open
pull requests rather than pushing directly to `main`.

```bash
git fetch origin main
git worktree add -b <branch-slug> .worktrees/<slug> origin/main
cd .worktrees/<slug>
```

Before requesting review, run the narrow tests relevant to your change and at
least `git diff --check`. For code changes, prefer targeted `pytest` runs plus
any package/build checks affected by the diff.

## Root hygiene

The repository root is reserved for entry points, legal files, build metadata,
and tool files that must live at root. Long-form references, language variants,
plans, and archival material belong under `docs/`.
