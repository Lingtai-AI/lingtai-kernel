# Contributing to lingtai-kernel

Thank you for helping improve the LingTai Python runtime. GitHub discovers this root file as the repository contributing guide.

## Start here

- Mandatory repository-local development workflow: find and read this
  repository’s dev guide skill
- Distributed code navigation system: [`ANATOMY.md`](ANATOMY.md)
- Distributed interface and Behavior Contract system: [`CONTRACT.md`](CONTRACT.md)
- Claude Code / coding-agent guidance:
  [`docs/references/claude-code-guide.md`](docs/references/claude-code-guide.md)
- Source-root anatomy: [`src/lingtai/kernel/ANATOMY.md`](src/lingtai/kernel/ANATOMY.md)
- Rust sidecar notes: [`crates/lingtai-search-sidecar/README.md`](crates/lingtai-search-sidecar/README.md)

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

Before changing code or architecture documents, read the repository-local
kernel development skill, then the nearest `ANATOMY.md` to navigate the layer and the paired `CONTRACT.md` (when
governed) to learn its interface and Behavior promises. The development skill owns the change workflow; the root documents own the
structural and interface rules. Follow those routes instead of copying their
checklists here.

Before requesting review, run the narrow tests relevant to your change and at
least `git diff --check`. For code changes, prefer targeted `pytest` runs plus
any package/build checks affected by the diff. Run
`tests/test_architecture_documents.py` when changing the distributed Anatomy or
Contract systems.

## Root hygiene

The repository root is reserved for entry points, legal files, build metadata,
and tool files that must live at root. The canonical repository-local
development router lives in `dev-guide-skill/`; `ANATOMY.md` and `CONTRACT.md`
are
the normative distributed system roots. Long-form references, language
variants, plans, and archival material belong under
`docs/`.
