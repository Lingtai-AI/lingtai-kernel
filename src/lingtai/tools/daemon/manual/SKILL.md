---
name: daemon-manual
description: >
  Operational router for the `daemon` tool: inspect slow/stuck/failed emanations,
  read daemon artifact folders, choose polling cadence, avoid reclaiming on a
  hunch, understand `daemon(action="list")`, use CLI backends and `backend_options`,
  and clean up daemon footprint. Read this after dispatching daemon work that is
  slow, failed, timed out, exited 143 / SIGTERM, or needs backend-specific reasoning.
version: 0.7.0
last_changed_at: "2026-07-14T00:00:00-07:00"
related_files:
- src/lingtai/tools/daemon/DAEMON_CONTRACT.md
- src/lingtai/tools/daemon/CONTRACT.md
- src/lingtai/tools/daemon/ANATOMY.md
- src/lingtai/tools/daemon/manual/reference/forensics/SKILL.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# Daemon Manual — Router

The `daemon` tool schema covers dispatch/follow-up/check/reclaim. This manual
routes to deeper operational references: how to inspect daemon artifacts, decide
whether work is stuck, use CLI backends safely, and clean up old emanations.

Scope note: this manual does **not** restate the daemon tool argument schema, and
it does not document cross-process recovery/orphan-detection internals. For the
broader runtime turn loop that daemon emanations mirror, use `lingtai-kernel-anatomy`
and its runtime-loop reference.

Maintainer note: the cross-backend daemon architecture capability contract is
`src/lingtai/tools/daemon/DAEMON_CONTRACT.md`. Update or explicitly re-check that
contract when changing backend routing, selected `skills`, one-run `mcp`, native
MCP mounting, `daemon_common`, backend support status, or run artifacts.

Use the smallest reference that matches the problem. Do not kill or reclaim a
daemon on a hunch; inspect first.

## Nested reference catalog

`daemon-manual` owns these nested references. They are parent-owned drill-down
files, not standalone top-level skills.

```yaml
- name: daemon-forensics
  location: reference/forensics/SKILL.md
  description: |
    Daemon artifact forensics: persistent daemons/em-* folders, daemon.json
    status fields, chat_history.jsonl, token_ledger.jsonl, events.jsonl,
    interpreting exit code 143 / SIGTERM (terminated, not a test/code failure),
    and how to inspect progress without guessing.
- name: daemon-inspection
  location: reference/inspection/SKILL.md
  description: |
    Polling cadence, stall heuristics, anti-patterns, backend-specific polling
    notes, and reminders before resting while daemon work remains pending.
- name: daemon-cli-backends
  location: reference/cli-backends/SKILL.md
  description: |
    Daemon API details and CLI backends: daemon(action=list), claude-p/codex/opencode behavior,
    backend_options flag passing, preset/capability inheritance, and Codex
    modal capabilities.
- name: daemon-cleanup
  location: reference/cleanup/SKILL.md
  description: |
    Scope boundaries and daemon footprint cleanup: what the manual does not
    cover, reclaim persistence, and safe cleanup of old daemon artifacts.
```

## Router table

| Need / keywords | Read |
|---|---|
| Find an emanation's folder; inspect `daemon.json`, transcript, token ledger, event log; understand result paths or token attribution | `reference/forensics/SKILL.md` |
| Interpret a CLI-backend **exit code 143 / SIGTERM** (terminated from outside — watchdog/timeout/reclaim — not a test or code failure); decide rerun vs hand-off; report it to a human | `reference/forensics/SKILL.md` |
| Decide whether a daemon is stuck; choose when to list/check/tail; avoid polling too often; set a reminder before resting | `reference/inspection/SKILL.md` |
| Use `daemon(action="list")`; choose `lingtai` vs `claude-p`/`codex`/`opencode`; pass `backend_options`; understand CLI backend limitations | `reference/cli-backends/SKILL.md` |
| Retire or audit old daemon artifacts; understand what `reclaim` does and does not delete; scope boundaries | `reference/cleanup/SKILL.md` |

## Quick decision tree

1. **Need only the daemon tool argument schema?** Use the tool description.
2. **Daemon seems slow?** Read `reference/forensics/SKILL.md`, then
   `reference/inspection/SKILL.md` if you might intervene.
3. **Daemon failed/timed out?** Read recent events/transcript via the forensics
   reference before retrying.
4. **Choosing an execution backend or flags?** Read
   `reference/cli-backends/SKILL.md`.
5. **Cleaning old folders?** Read `reference/cleanup/SKILL.md` and avoid deleting
   useful forensic evidence without a reason.

## Core rules to keep resident

- Keep daemon lightweight. If the task needs long-lived persona, molt/pad,
  durable knowledge, or ongoing ownership, spawn an avatar/agent instead of
  stretching daemon.
- Think of each task item as **task objective + behavior guidance + tool
  surface**:
  - `task` answers **what to do**: concrete objective, inputs, expected output,
    destination path, and verification checklist. Keep it task-shaped.
  - `system_prompt` answers **how this daemon should behave while doing it**:
    the parent's one-run role, constraints, safety posture, interpretation
    rules, collaboration boundaries, and tool-use policy. Omit it or leave it
    blank for the default daemon persona. It can guide or narrow behavior, but
    cannot override lifecycle limits, available tool schema, selected skills,
    or the ToolExecutor/ToolCallGuard execution gates.
  - `tools` answers **what the daemon can technically use** for this run. The
    parent still uses `system_prompt` to say when and how those tools should be
    used (for example: read-only file access, no network, write only to one
    report path, or ask a named peer before guessing). `email` is
    daemon-eligible communication, but it is not granted by default; include
    `tools: ["email"]` only when the daemon should be able to use internal mail.
    Other tool names still matter for file/bash/web/etc. access.
  - `skills` answers **which workflows the daemon should know about**. It is an
    optional list of strings. Each string may be either a skill directory
    containing `SKILL.md` or a direct `SKILL.md` path; relative paths resolve
    against the parent agent working directory. The runtime parses each skill's
    frontmatter and injects a compact YAML skill list into the daemon prompt.
    Use `system_prompt` to say when/how those selected skills should be applied.
  - `mcp` answers **which one-run MCP registrations belong to this daemon**. It
    is optional and is an array of full MCP registration objects: `name` plus
    `transport`/`type` (`stdio` or `http`), then `command`/`args`/`env` for
    stdio or `url`/`headers` for HTTP. The runtime serializes these registrations
    as YAML into every backend's oneshot context. The built-in LingTai backend
    also starts them as task-scoped MCP clients and exposes their tools for this
    run. Claude, Codex, OpenCode, Qwen, and Kimi CLI backends additionally receive
    daemon-generated native MCP configuration for the built-in `daemon_common`;
    Claude, Codex, OpenCode, and Qwen also receive native config for
    parent-provided stdio MCP registrations. Kimi receives native config for
    parent-provided stdio and HTTP MCP registrations through its run-private
    `mcp.json`. HTTP MCP registrations remain prompt catalog context for other
    CLI backends until a backend-specific HTTP MCP config path is implemented.
    LingTai automatically adds the built-in `daemon_common` MCP to MCP-capable
    daemon backends. Its `finish(status, summary?, reason?, artifacts?)` tool is
    the hard terminal-success contract: only `finish(status="done")` permits
    `done`; `failed`/`incomplete`, missing finish, or invalid completion prevents
    silent success. Secret `env`/`headers` values are redacted in prompts.
  - `preset`: optional body/model/tool-shape override for this daemon — an
    explicit `.json`/`.jsonc` path. On the LingTai backend it must already be
    a member of the parent agent's resolved `manifest.preset.allowed` set
    (the same fail-closed normalized path check `system(action="refresh")`
    uses); an unauthorized path is refused before load/connectivity/capability
    checks, run-dir creation, scheduling, or dispatch. Being present in the
    saved/library directory is not by itself authorization — call
    `system(action="presets")` first and pass one of the exact paths it
    returns. Omitting `preset` inherits the parent's regular (non-MCP)
    effective surface instead of a fresh independent default, and does not
    perform this allowlist check at all. External CLI backends skip LingTai
    preset resolution entirely and use their own model/tools/permissions. For
    the full preset runtime model (raw vs resolved `init.json`, preset
    identity, main-agent catalog vs this worker path, and how to authorize a
    new preset for this path), read `system-manual` →
    `reference/substrate-manual/SKILL.md` §11.
  - `backend_options`: raw CLI flags for CLI backends only.
  - `context_token_limit`: optional context-token compaction threshold (rendered/provider-context tokens, not cumulative spend). Effective only for `backend="lingtai"` tasks whose resolved provider is Codex (`codex`/`codex-pool`) — every other provider and every external CLI backend ignores it. When the session's provider-visible input-token count reaches the limit, the runtime compacts provider context via Codex's standalone compaction (`POST /responses/compact`) and continues the same tool loop — the daemon keeps running; nothing restarts or drops history. Omit to inherit the parent service's resolved context window as the threshold. Must be a positive integer; a boolean is rejected.
- Treat `system_prompt` as the parent's behavioral contract for **all** tools
  and selected skills/MCP context, not only for communication. If a daemon receives `bash`,
  say whether it may run mutating commands; if it receives file access, say what
  it may read/write; if it receives web/MCP tools, say what external calls are
  allowed; if it can communicate, say who it may contact and what context it may
  share; if `skills` or `mcp` are selected, say when to read/apply/call them.
- `email` is daemon-eligible but opt-in. Grant it only with `tools: ["email"]`
  when a daemon truly needs to communicate in the local agent network: reporting
  to peers, asking a sibling for context, or handing off a result. Availability
  is not authorization to broadcast. The parent should specify communication
  rules in `system_prompt`: allowed recipients, purpose, tone, thread/reply
  discipline, information boundaries, whether the daemon may ask questions or
  only report, how to report back to the parent, and when not to send mail.
- LingTai-backend daemon tool calls go through the kernel `ToolExecutor` /
  `ToolCallGuard` path before dispatch, so guarded side effects are not allowed
  to bypass normal proposal/execution policy just because they run in a daemon.
- Every `daemon.emanate` call returns a batch `group_id` shared by all daemon
  runs launched in that same call. Use `group_id` for logical batch context and
  audit. It is not a hard security boundary; use each daemon's `run_id` for
  per-run filesystem/audit identity.
- Track daemon work in the parent agent's pad, not in daemon itself. When you
  fan out multiple tasks, immediately write a small pad table after `emanate`:
  label/purpose, returned `id`, `group_id`, brief/context file path, expected
  artifact, and current status. Use `daemon(action="list")` and
  `daemon(action="check", id=...)` as the mechanical truth, then update the pad
  as the parent-facing map. Daemon should stay thin; if you need durable memory
  or identity, use an avatar instead.
- Do not copy large background into every task. Put reusable context in a
  brief/report/notes file and pass that file path explicitly in the `task`
  (with file access if the daemon should read it). A follow-up daemon should
  consume visible artifacts such as the previous task prompt, result file,
  report, event summary, or context files; do not treat a daemon as a resumable
  mind or hidden-context container. Prefer making daemon history searchable and
  easy to point at over copying or reviving a daemon session.
- Each emanation is disposable memory but durable evidence: its folder persists
  after completion or reclaim until cleanup.
- `daemon(action="list")` is the first layer of progressive disclosure: it
  reads active in-memory runs plus historical `daemons/*/daemon.json` run
  records, returning compact metadata, prompt/result previews, paths, and
  optional `contains`/`status`/`last` filtering. If a historical
  `daemon.json` is missing, invalid, or has an old `data_version`, list
  does a best-effort lazy rebuild from the run folder before indexing it.
  It is not a full transcript; use the returned paths for details.
- **Every terminal outcome is push-notified exactly once** — done, failed,
  cancelled, or timed out. After you dispatch, you can safely go IDLE and wait
  for the notification; do not poll only to ask "is it done yet". The
  notification arrives on the system channel carrying the daemon id, terminal
  status, task summary, and the result/error path. React to it with
  `daemon(action="check", id=...)` (and read `result.txt` for the full output).
- **`check` still resolves a daemon after refresh/molt.** A refresh/molt gives
  you a fresh daemon registry with no in-memory entries, but the run folders
  and their notifications survive on disk. New daemon ids are compact run ids
  such as `em-a1b2` (or `em-a1b2-1` after a collision), and
  `daemon(action="check", id=...)` exact-matches that `daemons/<run_id>/` folder
  on a registry miss. Legacy short handles such as `em-5` are accepted only when
  they resolve to one historical run; if several old runs share the handle,
  `check` returns an ambiguity error with `match_count`/`latest_run_id` instead
  of an unbounded path list. Use the exact `run_id` from the notification or
  `daemon(action="list")` when a legacy handle is ambiguous.
- **Defense-in-depth, not primary signal: a self-wake guards against a daemon
  that never reaches a terminal state at all.** The terminal notification covers
  every state a run can *finish* in, but a run that hangs without the watchdog
  firing, or a degraded notification-wake path, could leave you waiting forever.
  When daemon work is pending and unverified-healthy, you may arm one self-wake
  (a `.notification/cron.json` reminder) sized to the task's expected duration as
  a backstop. On wake, health-check — `state`/`last_output_at` advancing,
  `current_tool`/`tool_call_count` changing, events alive — and if there is no
  progress, reclaim/downgrade/switch path and report rather than waiting
  indefinitely. Do not turn this backstop into frequent polling. See
  `reference/inspection/SKILL.md`.
- If repeated-call `_advisory` appears on `daemon(list/check)`, the call still
  ran; treat it as a signal to stop the loop, centralize status checking in the
  parent, and read `reference/inspection/SKILL.md` before polling again.
- If an emanation might be stuck, inspect state changes, recent transcript, and
  event activity before reclaiming.
- CLI backend flags are passthroughs. Verify the current CLI's `--help` before
  relying on a flag.

### Example: separate task from behavior guidance

Use `task` for the deliverable and `system_prompt` for the daemon's operating
contract:

```json
{
  "task": "Audit the daemon manual changes and write a concise review to reports/daemon-manual-review.md.",
  "system_prompt": "Act as a documentation reviewer. Stay read-only except for the requested report file. Use the selected daemon-manual skills only when you need exact daemon semantics. Use the local-docs MCP only for daemon documentation lookup, not for unrelated search. You may use email only to ask dev-2 for missing daemon context; do not contact the human. If you email dev-2, state the exact question, include only the relevant snippet, and summarize the exchange in your final report. Do not use web tools unless the local docs are insufficient.",
  "tools": ["file", "bash"],
  "mcp": [
    {"name": "local-docs", "transport": "stdio", "command": "python", "args": ["-m", "local_docs_mcp"]}
  ],
  "skills": [
    "src/lingtai/tools/daemon/manual",
    "src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md"
  ]
}
```

The same pattern applies to non-email tools: `tools` grants a capability surface;
`skills` grants a selected workflow catalog; `mcp` grants one-run MCP registrations (serialized for all backends and mounted only where the backend has a native MCP path); `system_prompt` tells the daemon how to
exercise all of them in this one run.

## Maintenance

Keep this router short. Put new backend recipes, inspection examples, and cleanup
procedures in nested references so agents load only the needed detail.
