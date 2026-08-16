---
name: daemon-backend-deepseek
description: >
  Nested daemon-cli-backends reference for the official DeepSeek Harness
  execution backend. Read this for native Windows launch behavior, DSH patches,
  selected skills and MCP, workspace acceptance, and one-shot limitations.
version: 0.2.0
last_changed_at: 2026-08-18T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/daemon/ANATOMY.md
- src/lingtai/tools/daemon/CONTRACT.md
maintenance: |
  Tracks the DeepSeek Harness daemon backend; update when headless execution,
  session/resume, Cordis patching, MCP, or acceptance behavior changes.
---

# DeepSeek Harness execution backend

`deepseek` is LingTai's canonical backend id for DeepSeek AI's official `dsh`
harness. DSH is a developer preview and may make compatibility-breaking changes.
LingTai uses it as a bounded execution layer: one headless task, a per-run Cordis
patch, selected skills and MCP, durable JSONL artifacts, and parent-owned
acceptance. Planning, high-risk judgment, and final acceptance remain outside
the harness.

## Native Windows command

On Windows, stay in PowerShell. LingTai finds npm's PowerShell-visible `dsh.ps1`
shim, resolves the adjacent official `@deepseek-ai/dsh/lib/bin.js`, and starts
that file with native Node.js. No WSL, Bash, or `cmd.exe` path is involved.

```text
node <official-dsh-bin.js> --profile headless <backend_argv...> --patch <run-patch> <prompt>
```

Use `dsh --help` and `dsh --profile headless --help` from PowerShell to inspect
the installed version. LingTai owns `--profile headless`; `--profile`, `--help`,
`--version`, `--dump-config`, and `--dump-default-config` are reserved.
Repeatable user `--patch` overlays remain available through
`backend_options.patch`, and LingTai appends its policy patch last.

## Task shape

```jsonc
{
  "backend": "deepseek",
  "tasks": [{
    "task": "Implement the scoped change and report the result.",
    "tools": [],
    "workspace": "D:/rawle/Coding/project",
    "allowed_paths": ["src", "tests"],
    "required_checks": [
      ["python", "-m", "pytest", "-q", "tests/test_feature.py"],
      ["git", "diff", "--check"]
    ]
  }]
}
```

`workspace` defaults to the parent Agent directory. Use a clean worktree when
you need fully attributable path acceptance. A pre-dirty worktree is allowed,
but a scoped run reports `needs_decision` because edits to already-dirty files
cannot be attributed reliably.

LingTai copies only explicitly selected skill bundles into the run directory and
mounts them through DSH's filesystem-skill provider. Selected MCP registrations
use DSH's native MCP client. Secret env/header values stay in process environment
variables and are never written into `dsh.patch.yml`. Session persistence is
redirected to readable per-run JSONL. No custom DSH plugin is required.

## Quality gate

Treat DSH as a worker, not its own verifier. After a zero exit, LingTai checks
newly Git-observed paths against `allowed_paths` and executes every
`required_checks` entry directly as argv, never through a shell. Durable
`execution_acceptance.status` is `accepted`, `rejected`, or `needs_decision`.
Escaped paths or failed checks reject; missing checks, non-Git attribution, or a
pre-dirty scoped worktree require a decision.

The headless profile does not expose a verified stable session-id/resume
contract. `daemon(action="ask", ...)` therefore returns an explicit unsupported
error; start a new emanation for follow-up work.

## Authentication

DSH owns its DeepSeek account/API configuration. LingTai neither copies nor
prints credentials. `backend_options.env` may pass an already-authorized,
task-scoped environment to the subprocess; values stay redacted from durable
daemon metadata.

Official source: https://github.com/deepseek-ai/deepseek-harness
