---
name: bash-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/bash/__init__.py
  - src/lingtai/tools/bash/_tool_family.py
  - src/lingtai/tools/bash/CONTRACT.md
  - src/lingtai/tools/bash/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/tool_family/__init__.py
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/context/__init__.py
  - src/lingtai/tools/psyche/__init__.py
  - src/lingtai/kernel/base_agent/turn.py
  - src/lingtai/kernel/llm/base.py
  - tests/test_shell_sandbox_containment.py
  - tests/test_shell_tool_plugin_declaration.py
  - tests/test_shell_settings.py
  - tests/contracts/llm_conversation_input/test_send_str.py
  - tests/test_context_ownership_redesign.py
  - tests/test_repeated_tool_error_continue.py
  - tests/test_daemon_check_historical.py
  - src/lingtai/tools/daemon/BEHAVIORS.md
maintenance: |
  Written by the shell/sandbox CONVERT_BEHAVIOR migration (2026-08). The
  canonical shell implementation lives in this directory (src/lingtai/tools/bash/;
  the public tool is `shell`; see bash-contract), so this file's contract/anatomy
  links are local (CONTRACT.md, ANATOMY.md). Keep in sync with every contract
  this file guards: bash-contract, the LTP v2 family convention
  (src/lingtai/tools/CONTRACT.md + tool_family), the context/psyche contracts,
  the email contract, and the base-agent runtime contract (agent-runtime). When
  any of those changes in a way that affects agent-observable behavior, update
  the matching LABT here in the same change.
---
# Shell / Sandbox Behavior Tests

LABT v1. These are self-contained agent-executable behavioral tests for the
shell/sandbox family: the `shell` tool's working-dir containment and strict
LTP v2 envelope, the agent conversation-input `send(str)` wire, the context
ownership redesign (durable prompt sources), and the turn engine's repeated-
tool-error continuation. They prove the *observable* promises of
`src/lingtai/tools/bash/CONTRACT.md` (the `shell` capability contract),
`src/lingtai/tools/context/CONTRACT.md`, `src/lingtai/tools/psyche/CONTRACT.md`,
`src/lingtai/tools/email/CONTRACT.md`, and the base-agent runtime contract.
Low-level mechanics stay in pytest; each LABT below is self-contained and
executable verbatim by an agent with the listed tools.

`tests/test_daemon_check_historical.py` is already converted: it is covered by
`src/lingtai/tools/daemon/BEHAVIORS.md` Behavior D003 (daemon.check falls back
to historical run dirs), so it is NOT duplicated here.

Pinned pytest commands below must run from the repo root `<repo>` with the
project's Python: any interpreter that resolves `lingtai` from `<repo>/src`
(an editable install) and has pytest installed. Prefer the project's configured
python, e.g. `python -m pytest ...` from an activated project environment, or
the interpreter named by the `LINGTAI_RUNTIME_PYTHON` environment variable if
your environment provides it. Do not hardcode a machine-specific venv path; the
expected pass counts are pinned and platform notes are given per LABT.

## Behavior S001 — shell run stays inside the agent sandbox

- **id**: S001
- **title**: shell `working_dir` must equal the agent sandbox or be nested
  under it; sibling-prefix and outside paths are refused
- **guards**: `bash-contract` § Cross-platform invariants
  ([CONTRACT.md](../bash/CONTRACT.md#cross-platform-invariants)) and § Tool
  surface run error shape ([CONTRACT.md](../bash/CONTRACT.md#tool-surface))
- **supersedes**: `tests/test_shell_sandbox_containment.py`
- **runner**: any LingTai agent with the `shell` tool
- **prerequisites**: your working dir `<agent>`; nothing else
- **estimate**: 2 min

### Steps
1. Call `shell(action="run", input={"command": "pwd"}, reasoning="check")`
   with no `working_dir`.
2. Create `<agent>/sub/deep/` (write `<agent>/sub/deep/.keep` with the file
   tool or `mkdir` it from a shell run inside the sandbox).
3. Call `shell(action="run", input={"command": "pwd",
   "working_dir": "<agent>/sub/deep"}, reasoning="check")`.
4. Call `shell(action="run", input={"command": "pwd",
   "working_dir": "<agent>-sibling"}, reasoning="check")` — a name sharing
   the sandbox string as a prefix but not nested under it.
5. Call `shell(action="run", input={"command": "pwd",
   "working_dir": "<outside>"}, reasoning="check")` where `<outside>` is any
   absolute directory NOT under `<agent>` (e.g. the parent of `<agent>`, `/tmp`
   on POSIX, or `C:\Windows` on Windows).
6. (POSIX-only pinned check — skip on native Windows) From `<repo>`, run
   `python -m pytest tests/test_shell_sandbox_containment.py -q`.

### Expected evidence
- [ ] Step 1: `{status: "ok", ...}` and `stdout` contains your working dir
      (the sandbox itself is accepted as `working_dir`).
- [ ] Step 3: `{status: "ok", ...}` and `stdout` contains the resolved
      `<agent>/sub/deep` path (a nested cwd is accepted).
- [ ] Step 4: `{status: "error", message}` and the message contains
      `under agent working directory` (sibling-prefix escape refused).
- [ ] Step 5: `{status: "error", message}` containing
      `under agent working directory` (outside path refused).
- [ ] Steps 4–5 ran no command: no `stdout`/`stderr` payload, and nothing was
      created or modified outside `<agent>`.
- [ ] Step 6 (POSIX only): summary reads `5 passed`. This step is not part of
      the LABT on native Windows — the two `@posix_paths` end-to-end tests are
      skipped there and the un-marked POSIX-path unit assertions fail by design
      (the file proves the Windows separator path via monkeypatch on POSIX CI
      and is not in the Windows CI set); on Windows, steps 1–5 are the
      cross-platform evidence and the pinned check is omitted.

### Pass / Fail
Pass when nested `working_dir` runs succeed, every out-of-bounds path is
refused with an error containing `under agent working directory`, no command
escaped the sandbox, and (on POSIX) the pinned suite passes. Fail if any
outside or sibling-prefix path executes, or if the error message shape changes.

## Behavior S002 — shell exposes the strict LTP v2 family envelope

- **id**: S002
- **title**: the `shell` tool root is exactly `{action, input, reasoning,
  summarize}`; unknown actions and cross-action input keys are refused before
  any dispatch
- **guards**: `bash-contract` § Tool surface (closed LTP v2 root,
  `ACTION_REQUIRED` / `INVALID_ARGUMENT` rejections)
  ([CONTRACT.md](../bash/CONTRACT.md#tool-surface)); `lingtai-tool-protocol`
  § Envelope (closed LTP v2 root)
  ([CONTRACT.md](../CONTRACT.md#envelope))
- **runner**: any LingTai agent with the `shell` tool
- **prerequisites**: none beyond your working dir
- **estimate**: 2 min

### Steps
1. Inspect the `shell` tool definition in your own environment (the composed
   schema the host exposes to you). Record its root `properties`, `required`,
   and `additionalProperties`.
2. Call `shell(action="run", input={"command": "pwd"}, reasoning="ok")`.
3. Call `shell(action="frobnicate", input={}, reasoning="x")` — an action
   that is not one of the five children.
4. Call `shell(action="poll", input={"command": "pwd"}, reasoning="x")` — a
   `run`-only field smuggled into `poll`'s input.
5. Call `shell(action="settings", input={}, reasoning="x")`.
6. Call `shell(action="manual", input={}, reasoning="x")`.
7. Call `shell(action="manual", input={"page": 2}, reasoning="x")`.

### Expected evidence
- [ ] Step 1: `properties == {action, input, reasoning, summarize}`,
      `required == [action, input, reasoning]`, and
      `additionalProperties == false` (the strict LTP v2 root).
- [ ] Step 2: `{status: "ok", ...}` — the valid envelope dispatches.
- [ ] Step 3: `{status: "failed", error_code: "ACTION_REQUIRED", message:
      "action must be one of run, poll, cancel, settings, or manual"}`.
- [ ] Step 4: `{status: "failed", error_code: "INVALID_ARGUMENT", message:
      "unsupported shell input field"}` — rejected before any job lookup.
- [ ] Step 5: exactly seven rows keyed `shell_kind`,
      `sync_timeout_default_seconds`, `sync_timeout_max_seconds`,
      `result_max_chars`, `async_default`,
      `async_reminder_default_seconds`, and `command_policy`; every row has
      exactly `key/current/default/configurable/comment`, and both command
      policy values are `<redacted>`.
- [ ] Step 6: `{status: "ok", content: [{type: "text", text: <shell-manual
      body>}], structuredContent: {manual_path}}`.
- [ ] Step 7: `{status: "failed", error_code: "INVALID_ARGUMENT", message:
      "unsupported shell input field"}` — `manual` input is strict empty.
- [ ] Steps 3–7 spawned no process and touched no job state.

### Pass / Fail
Pass when the root schema and settings inventory match exactly and every
invalid call is refused with the exact `error_code`/`message` above. Fail if an
unknown action or a cross-action input key dispatches, if settings leaks policy
values, or if the closed root gains a field.

## Behavior S003 — send(str) reaches the provider transport as provider text

- **id**: S003
- **title**: a plain user-text turn (`send(str)`) is serialized into the
  provider wire for every conforming production session regime, and `send`
  returns a real `LLMResponse` with concrete `UsageMetadata`
- **guards**: `agent-runtime` § Behavior
  kernel `ChatSession` ABC § `send` ([base.py](../../kernel/llm/base.py))
- **supersedes**: `tests/contracts/llm_conversation_input/test_send_str.py`
- **runner**: any LingTai agent with shell access to a checkout of `<repo>`
  and the project venv `python` (resolving `lingtai` from `<repo>/src`)
- **prerequisites**: the repo checkout; pytest installed in that venv
- **estimate**: 3 min

### Steps
1. From `<repo>`, run
   `python -m pytest tests/contracts/llm_conversation_input/test_send_str.py -q`.
2. Read the summary line and, if any failure, the failing regime name.
3. (Optional live check) In your own session, submit a plain user-text turn
   (a bare string, no tool call) to a real provider and confirm the reply
   responds to that text — the text reached the provider, not a tool result.

### Expected evidence
- [ ] Exit code 0 and the summary line reads `10 passed`.
- [ ] The ten conforming regime rows all ran and passed: `openai_chat`,
      `deepseek_chat`, `mimo_chat`, `zhipu_chat`, `anthropic`, `claude_code`,
      `gemini_interactions`, `openai_responses`, `codex_responses`,
      `gated_openai_chat`.
- [ ] Each row proves, for `USER_TEXT = "characterization text turn"`: the
      text appears in the captured provider wire in that regime's shape — the
      OpenAI-family dict `{"role": "user", "content": "characterization text
      turn"}` (openai_chat, deepseek_chat, mimo_chat, zhipu_chat,
      openai_responses, codex_responses, gated_openai_chat), the Anthropic
      messages array (anthropic), the rendered CLI prompt string
      (claude_code), or the Gemini Interactions `input` array
      (gemini_interactions) — and `send` returns an `LLMResponse` whose
      `UsageMetadata` carries the concrete mocked counts `input_tokens=10`,
      `output_tokens=5` (never zeroed).
- [ ] Step 3: the provider reply engages with your plain text (no error, no
      dropped turn).

### Pass / Fail
Pass when `10 passed` and the wire/envelope facts above hold. Fail if any
conforming regime drops or rewrites the user text, returns no concrete
`UsageMetadata`, or the suite reports a failure.

## Behavior S004 — context ownership: durable sources change disk, never the live prompt

- **id**: S004
- **title**: durable prompt sources (`<agent>/system/*.md`, pinned references)
  are plain files; `shell` writes never hot-load the running prompt, only
  an explicit `context(action="rebuild")` recomposes, and retired
  domain-mutation actions are rejected
- **guards**: `context-contract` § Full reconstruction ordering
  ([CONTRACT.md](../context/CONTRACT.md#full-reconstruction-ordering)) and §
  LTP v2 port ([CONTRACT.md](../context/CONTRACT.md#ltp-v2-port));
  `psyche-tool-contract` § Root reuse is not action compatibility
  ([CONTRACT.md](../psyche/CONTRACT.md#root-reuse-is-not-action-compatibility))
- **supersedes**: `tests/test_context_ownership_redesign.py`
- **runner**: any LingTai agent with the `shell`, `context`, and `psyche` tools
- **prerequisites**: your working dir `<agent>`; the `system/` dir may need
  creating
- **estimate**: 4 min

### Steps
1. Inspect the `psyche` and `context` tool definitions in your environment;
   record each `action` enum.
2. Call each retired domain-mutation action and record the results:
   `psyche(action="update", input={}, reasoning="x")`, `"load"`, `"edit"`,
   `"append"`, `"info"`, and `context(action="load", input={},
   reasoning="x")`.
3. Call `psyche(action="lingtai", input={}, reasoning="inspect")`; record the
   status.
4. Call `psyche(action="lingtai", input={"content": "not allowed"},
   reasoning="x")`; record the result.
5. Create `<agent>/system/` and write `<agent>/system/pad.md` containing
   `DURABLE-ONE` via `shell(action="run", input={"command": "mkdir -p
   <agent>/system && printf 'DURABLE-ONE\n' > <agent>/system/pad.md"},
   reasoning="x")`, then re-read it with `shell` to confirm the write. Read
   `<agent>/system/system.md` (it may not exist yet) and record whether it
   contains `DURABLE-ONE`.
6. Call `context(action="rebuild", input={}, reasoning="x")`; record the
   result fields. Read `<agent>/system/system.md` again and record whether it
   now contains `DURABLE-ONE`.
7. (Pinned check) From `<repo>`, run
   `python -m pytest tests/test_context_ownership_redesign.py -q`.

### Expected evidence
- [ ] Step 1: `psyche` action enum == `["pad", "lingtai", "knowledge",
      "skills", "manual"]`; `context` action enum == `["molt",
      "summarize", "rebuild", "manual"]` (the locked inventories).
- [ ] Step 2: every retired call returns an error whose message contains
      `Unknown psyche action` (for the psyche calls) or `Unknown context
      action` (for the context call).
- [ ] Step 3: status is `ok` or `degraded` — the 灵台 signpost survives as
      `psyche(action="lingtai")`.
- [ ] Step 4: `{status: "failed", error_code: "INVALID_ARGUMENT", ...}` — the
      lingtai input branch is strict empty.
- [ ] Step 5: `pad.md` on disk reads `DURABLE-ONE`, and
      `<agent>/system/system.md` does NOT contain `DURABLE-ONE` (no hot-load
      from a plain file write).
- [ ] Step 6: rebuild returns `status: "ok"` with `prompt_reconstructed` true
      and a `prompt_reconstruction` field containing `All canonical prompt
      sources`; `<agent>/system/system.md` now contains `DURABLE-ONE`
      (recomposition, not overlay — a removed source also disappears on the
      next full rebuild).
- [ ] Step 7: summary reads `12 passed`.

### Pass / Fail
Pass when all evidence holds. Fail if a retired action dispatches, the lingtai
envelope accepts extra input, a plain file write changes the running prompt
before `rebuild`, or rebuild does not recompose `system.md` from the durable
sources.

## Behavior S005 — repeated identical tool errors stay normal tool results

- **id**: S005
- **title**: identical tool errors keep flowing as ordinary tool-result
  payloads; there is no repeated-error hard-stop continuation and no
  `repeated_tool_error` notification
- **guards**: `agent-runtime` § Behavior (main turn loop)
  ([CONTRACT.md](../../kernel/base_agent/CONTRACT.md#behavior)); incident
  regression 2026-06-12 (mimo-1) documented in the superseded test
- **supersedes**: `tests/test_repeated_tool_error_continue.py`
- **runner**: any LingTai agent with the `shell` tool (live check), plus
  shell access to `<repo>` (pinned check)
- **prerequisites**: your working dir `<agent>`; repo checkout for step 4
- **estimate**: 3 min

### Steps
1. Deliberately trigger the same tool error at least three times in a row,
   e.g. `shell(action="run", input={"command": "definitely-not-a-command-xyz"},
   reasoning="x")` three times.
2. After the third identical error, make one more ordinary tool call (e.g.
   `shell(action="run", input={"command": "ls <agent>"}, reasoning="x")`) and
   finish the turn; record that the runtime continued
   normally — you were not dropped to IDLE and needed no continuation/
   notification workaround.
3. Check whether `<agent>/.notification/repeated_tool_error.json` exists.
4. (Pinned check) From `<repo>`, run
   `python -m pytest tests/test_repeated_tool_error_continue.py -q`.

### Expected evidence
- [ ] Steps 1–2: each failure came back as an ordinary tool result with its
      error payload; the follow-up tool call and your final reply completed
      within the same turn (no hard-stop path, no string continuation
      synthesized by the engine).
- [ ] Step 3: no `repeated_tool_error.json` exists in
      `<agent>/.notification/`.
- [ ] Step 4: summary reads `3 passed`. The pinned tests additionally assert:
      every payload the session received was a list of tool results (never a
      `str`), committed ids `call_1`..`call_3` (and, in the second test,
      through `call_4`), no pending tool calls remain, no log event starts
      with `repeated_tool_error`, and no notification file is written.

### Pass / Fail
Pass when repeated identical errors keep the normal tool loop going with no
notification file and the pinned suite passes. Fail if the engine hard-stops
on identical errors, synthesizes a text continuation, writes a
`repeated_tool_error.json`, or logs a `repeated_tool_error` event.

## Behavior S006 — email send works through the strict LTP v2 envelope

- **id**: S006
- **title**: `email(action="send", input={"address": ..., "message": ...})`
  writes exactly one sent record, and cross-action input keys / unknown root
  fields are refused before any mailbox I/O
- **guards**: `email-contract` § Tool surface (send row; cross-action key
  rejection before any mailbox I/O or delivery)
  ([CONTRACT.md](../email/CONTRACT.md#tool-surface))
- **supersedes**: `tests/test_tool_family_email_migration.py::test_cross_action_key_is_rejected_before_any_mailbox_io`,
  `::test_send_fields_cannot_be_smuggled_through_a_read_call`,
  `::test_unknown_root_field_is_rejected`
- **runner**: any LingTai agent with the `email` tool
- **prerequisites**: your working dir `<agent>` (the mailbox is
  `<agent>/mailbox/`); a recipient address string for your own mailbox
  (peer mode, e.g. your agent name)
- **estimate**: 2 min

### Steps
1. Record the current `sent/` dirs: glob `<agent>/mailbox/sent/*/` and note
   the count.
2. Call `email(action="send", input={"address": "<recipient>",
   "message": "hello"}, reasoning="x")`; record the result.
3. Glob `<agent>/mailbox/sent/*/message.json` again; read the newest file and
   record its `address` and `message` fields.
4. Call `email(action="read", input={"email_id": ["x"], "address":
   "peer", "message": "leak"}, reasoning="x")` — `send` fields smuggled
   into a `read` call; record the result.
5. Call `email(action="contacts", input={}, not_a_field=1, reasoning="x")`
   — an unknown root field; record the result.
6. Re-glob `<agent>/mailbox/sent/` and `<agent>/mailbox/inbox/`; record
   whether steps 4–5 created or changed any mailbox record.

### Expected evidence
- [ ] Step 2: `{status: "sent", to, cc, bcc, delay}` with `to` containing
      `<recipient>` and no error.
- [ ] Step 3: exactly one new `sent/<uuid>/message.json` exists; its JSON
      carries `"message": "hello"` and the recipient address (a single
      record per send call).
- [ ] Step 4: `{status: "failed", error_code: "INVALID_ARGUMENT", message:
      "unsupported email input field"}` — the smuggle is refused before any
      mailbox I/O or delivery.
- [ ] Step 5: `{status: "failed", error_code: "INVALID_ARGUMENT", message:
      "unsupported email argument"}`.
- [ ] Step 6: the `sent/` and `inbox/` inventories are unchanged by steps 4–5
      (no delivery, no read-state change).

### Pass / Fail
Pass when a string-message send produces exactly one `sent/` record with the
canonical receipt, and both envelope violations are refused with the exact
`error_code`/`message` above without touching the mailbox. Fail if a smuggled
key dispatches, mail leaves via a read-shaped call, or the send writes more
than one record.
