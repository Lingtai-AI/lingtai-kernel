---
name: daemon-cli-backends
description: >
  Nested daemon-manual reference for daemon API details and CLI backends:
  daemon(action=list), claude-code/codex/opencode behavior, backend_options flag
  passing, preset/capability inheritance, and Codex modal capabilities.
version: 1.0.0
---

# Daemon CLI Backend Reference

Nested daemon-manual reference. Open this when choosing a daemon backend,
inspecting `daemon(action="list")`, or passing CLI flags through `backend_options`.

## API note: `daemon(action="list")`

`list` reports only currently-active emanations (in-memory registry). It includes `run_id` and `path` so you know where to read on disk. Historical (completed/failed/cancelled) emanations don't appear in `list` — find them with `bash("ls daemons/")` instead.

## CLI backends

The `backend` parameter selects the execution engine for emanations. Default is `lingtai` (the built-in ChatSession loop). Two external CLI backends are also available:

| Backend | CLI command | Session resume | Notes |
|---------|------------|----------------|-------|
| `lingtai` | (built-in) | N/A — in-process `ask` | Default. Uses preset resolution, tool surface curation, model routing. |
| `claude-code` | `claude --print --dangerously-skip-permissions --output-format stream-json --verbose --name <em_id> <task>` | `claude --resume <claude_session_id>` via `ask` (async — returns immediately, reply arrives via notification / `check`) | Session ID captured from the first event of the stream-json output (typically within ms of process start), so `ask` is usable as soon as `emanate` returns — even while the initial task is still running. |
| `codex` | `codex exec --json --dangerously-bypass-approvals-and-sandbox <task>` | `codex exec resume <codex_session_id>` via `ask` (async — returns immediately, reply arrives via notification / `check`) | Mirrors claude-code. `thread.started` event carries the session id (codex internally calls it `thread_id`), captured immediately. `ask` resumes the same conversation context. |

**When to use CLI backends:** When the task benefits from a different agent runtime's tool surface (e.g., Claude Code's built-in file editing, Codex's sandboxed execution) rather than the lingtai emanation's curated tool set.

**Claude Code auth environment hygiene.** The `claude-code` backend deliberately starts `claude` with auth override variables stripped from the subprocess environment. This includes `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` (which force API billing; GH #107) and `CLAUDE_CODE_OAUTH_TOKEN` (a stale inherited token can override a refreshed `~/.claude/.credentials.json` and appear as a false "weekly limit"; see Lingtai-AI/lingtai#189). If a manual shell invocation of `claude` reports a quota/weekly-limit error, run a tiny smoke test with the stale env token unset before concluding the account is actually exhausted:

```bash
env -u CLAUDE_CODE_OAUTH_TOKEN claude -p 'Reply exactly OK' --allowedTools Read -c
```

Do not print token values while diagnosing; `claude auth status` plus redacted environment variable names are enough.

**CLI backends skip preset resolution** — the external CLI manages its own model, tools, and permissions. The `tools` field in the task spec is ignored for CLI backends.

### Passing free-form CLI flags via `backend_options`

For `claude-code` and `codex` backends, each task may carry an optional
`backend_options` JSON object that is converted to argv tokens and appended
to the CLI command before the task prompt. This lets you reach the underlying
CLI's full flag surface (model selection, search/web access, effort levels,
sandbox/policy switches, etc.) without the daemon needing to hard-code them.

**This is intentionally a passthrough, not a fixed table.** Claude Code and
Codex both rev their flag lists between releases. **Before adding new
options, run `claude --help` or `codex exec --help` in `bash` to discover
what the installed version actually supports today.** Anything in this
manual is illustrative, not authoritative.

```jsonc
// Claude Code with a specific reasoning effort and model
{
  "action": "emanate",
  "backend": "claude-code",
  "tasks": [{
    "task": "Refactor auth.py for clarity.",
    "tools": [],
    "backend_options": {
      "effort": "high",
      "model": "claude-opus-4-7"
    }
  }]
}

// Codex with model + web search
{
  "action": "emanate",
  "backend": "codex",
  "tasks": [{
    "task": "Find the breaking change in the last release.",
    "tools": [],
    "backend_options": {
      "model": "gpt-5",
      "search": true
    }
  }]
}
```

**Conversion rules** (validated before any process is spawned — a single
bad spec refuses the whole batch with a clear `ValueError`):

| Value type | Result |
|---|---|
| `true` | flag only, e.g. `{"search": true}` → `--search` |
| `false` / `null` | flag omitted entirely |
| string / int / float | `--flag <value>` |
| list of scalars | repeated: `--flag v1 --flag v2 ...` |
| nested object / array of objects | **rejected** with `ValueError` |

**Key safety:** keys must look like CLI flag names (letters/digits with
`-` or `_` separators, no leading `-`, no spaces). Underscores in keys are
converted to dashes in the emitted flag, so JSON-friendly `{"output_format":
"json"}` becomes `--output-format json`. Unsafe keys are rejected before
any subprocess call.

**When it applies:** `backend_options` is honored only at `emanate` time
(when the CLI session is first spawned). `daemon(action="ask", ...)` reuses
the existing session via `claude --resume` / `codex exec resume` and does
not re-pass `backend_options` — the runtime flags chosen at emanate time
persist for the life of the session.

**Where it shows up on disk:** the resolved options are written into the
emanation's `daemon.json` (`backend_options` field for the raw object,
`backend_argv` for the converted argv tokens), plus a
`daemon_backend_options` entry in the parent's `logs/events.jsonl`. So a
later `daemon(action="check", id="em-N")` and the events log together let
you reconstruct exactly which flags were passed.

**`lingtai` backend ignores `backend_options`.** The field is silently
dropped for the built-in backend — there's no CLI process to forward it to.

**Working directory:** Both CLI backends run in the parent agent's working directory (`_working_dir`), not in the emanation's `daemons/em-N-*/` folder. The `daemons/` folder is used for tracking state (`daemon.json`, logs) and terminal output (`result.txt`).

**Progress delivery:** CLI stdout/stderr is persisted to the run directory as `cli_output` events and `daemon.json.last_output`; it is not injected into the parent as ordinary `[daemon:em-N]` request text. Completion/failure publishes one compact `system` notification telling the parent which daemon finished and to inspect it with `daemon(action="check", id="em-N")`.

**`ask` on CLI backends is asynchronous.** For `claude-code` and `codex` emanations, `daemon(action="ask", id="em-N", message="...")` spawns the resumed CLI subprocess and returns immediately with `{"status":"sent","async":true}` — it does **not** wait for the reply. Progress streams into the same run directory (`cli_output` events, `last_output`); the final reply text arrives as a `follow-up completed` system notification and is also visible via `daemon(action="check", id="em-N")`. Poll `check` (or wait for the notification) instead of expecting the reply in the `ask` return value.

While one CLI `ask` is in flight against a given emanation, a second `ask` to the same id returns `{"status":"busy", ...}` — `claude --resume` and `codex exec resume` serialize per session, so wait for the first follow-up to complete (or check the notification) before sending another. The `lingtai` backend's `ask` is unchanged: it buffers into a per-emanation followup buffer and is drained by the in-process run loop.

**Claude Code backend specifics.** The backend streams structured JSON events from Claude Code in real time (`--output-format stream-json --verbose`):

- `daemon(check)` sees live progress as each assistant turn arrives — `last_output` updates per turn and `current_tool` tracks Claude Code's own tool calls (`set` on `tool_use` blocks, `clear` on the matching `tool_result`). Note that `tokens` stays at 0 — Claude Code runs through its own provider account and we deliberately don't merge its `usage` fields into the kernel's token ledger (they'd mix with native LLM-adapter accounting that has different cache semantics). Spend is visible to the human via Claude Code's own UI and the `cli_output` event stream.
- `claude_session_id` is set on the first event that carries a session id (typically the system `init` event, within ms of process start). This means `daemon(action="ask", id="em-N", message="...")` works the moment `emanate` returns — you don't have to wait for the initial task to complete. (Earlier versions wrote the session id only post-hoc by scanning `~/.claude/projects/`; that scan is now a fallback for the unusual case where the stream never carried a session id.)
- stderr is captured to its own pipe (no longer merged into stdout) and persisted as `cli_output` events with `stream="stderr"`, so API errors, auth failures, and rate limits are visible during the run rather than buried in a buffered stdout.
- `turn` is not incremented for CLI backends — Claude Code runs its own LLM loop and we don't see "turns" in the same sense. Use `last_output` and `cli_output` events to gauge progress.
- An `is_error=true` in the final `result` event is surfaced as a failed emanation even when the underlying process exited 0, so an error reported inside the LLM stream doesn't masquerade as success.

**Codex backend specifics.** Identical observability + resumability story as claude-code, with codex's own event vocabulary (`--json`):

- `daemon(check)` sees live progress: `last_output` updates as each `item.completed` event with `type=agent_message` arrives. Note that `tokens` stays at 0 — codex runs through its own provider account, and its `cached_input_tokens` semantics differ from the kernel's LLM adapters (codex's `input_tokens` already includes the cached portion, anthropic's doesn't), so we deliberately don't merge codex's `usage` into the token ledger. Spend is visible via the codex CLI's own output and the `cli_output` event stream.
- `codex_session_id` (stored as `daemon.json.codex_session_id`) is set on the first event — `{"type":"thread.started","thread_id":"<uuid>"}` — within ms of process start. `daemon(action="ask", id="em-N", message="...")` runs `codex exec resume <codex_session_id> --json "<message>"` asynchronously: the call returns immediately and the resumed turn's reply lands in the run_dir (`last_output`, `cli_output` events) plus a `follow-up completed` notification, same as the claude-code ask path.
- stderr is captured to its own pipe (was: merged into stdout via `--ephemeral` mode) and persisted as `cli_output` events with `stream="stderr"`.
- Codex doesn't emit an `is_error` flag like Claude Code; the kernel treats absence of a `turn.completed` event (combined with no captured `agent_message` items) as failure even when the process exits 0.
- `--ephemeral` is intentionally NOT passed: it would disable session persistence and break `daemon(ask)`. Sessions persist under `~/.codex/sessions/` and can be re-resumed by ID for the lifetime of the session record.

### Codex modal capabilities and native image generation

`backend="codex"` delegates to the external Codex CLI/runtime, so the emanation can use whatever native tools the installed Codex account/profile exposes — including modalities that aren't surfaced in `codex --help`. The `--image` flag there only documents image *input*; native image *generation* (and other modal tools) may still be available at runtime depending on the profile.

When asking a Codex emanation to generate images, be explicit:

- Request **PNG or JPEG** outputs (say "not SVG" unless you actually want vector art — Codex will otherwise often fall back to an inline SVG).
- Name an **explicit writable directory** under the parent working dir (e.g. `media/images/`) and have the emanation write files there with stable names.
- Specify **dimensions, number of variants, and style** up front; Codex won't ask.

After the emanation completes, verify:

1. `daemon(action="check", id="em-N")` — confirm `state=done` and scan `cli_output` for file paths Codex reported writing.
2. Inspect the output directory directly (e.g. `ls media/images/`) and confirm the files are real PNG/JPEG bytes, not 0-byte stubs or an SVG.

**Detection / failure honesty.** If Codex reports that image generation is unavailable, refuses the modality, or completes without producing any image files, treat it as **runtime/profile unsupported** and report that honestly to the human. Do **not** silently fall back to the MiniMax `draw` capability or accept an SVG as a substitute — the human asked for a Codex-native PNG/JPEG and a different result needs their decision, not yours.
