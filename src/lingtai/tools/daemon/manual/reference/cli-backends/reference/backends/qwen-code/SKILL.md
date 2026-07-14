---
name: daemon-backend-qwen-code
description: >
  Nested daemon-cli-backends reference for the Qwen Code (`qwen-code` /
  `qwen`) daemon backend's flag surface. Read this only when a daemon task
  needs Qwen-specific CLI flags (model selection, provider tunables): it
  routes you to the installed CLI's live help via bash and shows how to
  translate that help into the generic `backend_options` mechanism. It is
  not a flag catalog.
version: 0.1.0
last_changed_at: "2026-07-09T19:22:18-07:00"
related_files:
- src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md
- src/lingtai/tools/bash/manual/reference/bash-qwen-code/SKILL.md
maintenance: |
  Tracks the Qwen Code daemon backend flag-discovery topic it documents; update when that integration changes.
---

# Qwen Code Daemon Backend — Flag Discovery Entrypoint

This page is deliberately tiny: it only tells you where the knowledge lives.
The Qwen Code CLI (npm package `@qwen-code/qwen-code`, binary `qwen`) revs its
flags and accepted values between releases, so the installed CLI's own help
output is the authority — not this page, and not any flag list LingTai could
ship. The alias `qwen` canonicalizes to the `qwen-code` backend id.

## Discover flags from the installed CLI

1. Load `bash-manual` (its nested `reference/bash-qwen-code/SKILL.md` has
   broader Qwen Code CLI context).
2. Run, in bash: `qwen --version` and `qwen --help`. The daemon backend wraps
   the top-level `qwen` binary directly — it spawns
   `qwen --yolo <backend_argv...> -p <prompt>`, no subcommand — so
   `qwen --help` is the whole relevant flag surface. These are local
   read-only commands; no session is started.
3. Translate the long flags you found into `backend_options` using the
   generic conversion rules in the parent `reference/cli-backends/SKILL.md`
   (key→flag mapping, value handling, key safety, persistence). Nothing
   Qwen-specific is added to that contract here.

## Example: model selection via the generic route

Your options land between the harness-owned `--yolo` and the final
`-p <prompt>` (argv placement is pinned by
`tests/test_daemon_backend_options.py::test_qwen_code_cmd_appends_backend_argv_before_prompt`):

```jsonc
{
  "backend": "qwen",
  "tasks": [{
    "task": "Implement and validate the change.",
    "tools": [],
    "backend_options": {
      "model": "qwen3-coder-plus"
    }
  }]
}
// argv: qwen --yolo --model qwen3-coder-plus -p <prompt>
```

The model vocabulary belongs to the installed CLI and the configured
provider — LingTai does not validate, enumerate, or simulate model names. A
value passes through and the CLI/provider decides its semantics (or rejects
it).

## Harness boundary

Qwen Code reserves `--prompt`/`-p`, `--yolo`/`-y`, and `--approval-mode`:
they drive LingTai's non-interactive headless harness, and passing any of
them in `backend_options` refuses the whole batch before spawn. Beyond that,
do not re-point harness-owned surfaces: the daemon writes a per-run
`<run>/qwen-daemon-settings.json` (carrying `mcpServers.daemon_common` plus
parent stdio MCP registrations) and injects it via the
`QWEN_CODE_SYSTEM_SETTINGS_PATH` environment variable — overriding settings
paths silently breaks completion enforcement.

Plan flags at emanate time: `daemon(action='ask')` is intentionally
unsupported for this backend (no stable headless resume contract), so there
is no later chance to adjust a running session. Output parsing is verbatim
text — Qwen Code headless mode has no machine-readable event stream here, so
stdout/stderr are recorded as-is and the result is the final stdout text;
the `daemon_common` `finish(status="done")` call is still required for
success.

To debug what was actually sent, `daemon.json` records the raw
`backend_options` object alongside the resolved `backend_argv` /
`backend_harness_argv` token lists.
