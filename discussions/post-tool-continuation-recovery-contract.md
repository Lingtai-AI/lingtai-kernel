# Post-tool continuation failure recovery contract

Status: draft contract for `Lingtai-AI/lingtai#223`
Scope: LingTai kernel/runtime recovery after a tool has committed side effects but the following LLM continuation fails.

## Problem statement

A LingTai turn has two different commit boundaries:

1. **Tool side effect boundary** — the local tool has already run. It may have written files, sent mail/chat, spawned daemons, changed agent state, or updated durable runtime logs.
2. **LLM continuation boundary** — the model-visible conversation must receive the `tool_result` and the provider must accept the next continuation request.

`lingtai#223` describes incidents where boundary (1) completed but boundary (2) failed with provider errors. The kernel already warns the agent not to blindly retry, but the warning alone is not enough when side effects are complex, especially for `daemon(action="emanate")` where multiple child runs may have been created before the continuation failed.

The goal is not to make every side effect automatically reversible. The goal is to make post-failure recovery deterministic enough that the next agent turn can reconcile the real state without guessing or repeating a destructive operation.

## Recovery contract

When a tool result exists locally and the post-tool LLM continuation fails, the runtime must preserve or publish a bounded recovery record with enough information to reconcile the side effect.

### Required recovery fields

Every recovery record should include:

- `schema_version`: integer, starting at `1`.
- `event`: stable event name, e.g. `post_tool_continuation_failed`.
- `tool_call_id`: provider/tool-call id when available.
- `tool_name`: tool namespace (`daemon`, `psyche`, `bash`, `telegram`, etc.).
- `tool_completed`: boolean. For this contract the relevant value is `true`.
- `continuation_error`: bounded string with provider/runtime error class and message. No secrets.
- `ledger_source`: where the failed continuation occurred (`turn`, `tc_wake`, etc.).
- `recorded_at`: UTC timestamp.
- `recovery_hint`: short agent-facing instruction. It must say to inspect actual state before retrying.
- `result_summary`: bounded, redacted summary of the tool result, never an unbounded raw spill.

The record may be stored in durable logs, surfaced through a system notification, or both. If it is only logged, another recovery path must still make it discoverable after refresh/molt.

### Tool-specific fields

The record should include known-safe structured fields for common side-effecting tools:

#### `daemon(action="emanate")`

If the tool result contains daemon metadata, include:

- `daemon_ids`: list of short daemon ids (`em-...`).
- `group_id`: daemon group id when present.
- `run_ids`: child run ids when present.
- `run_dirs`: bounded list of run directory paths when present.
- `task_count`: number of requested child tasks.
- `backend`: daemon backend when present.
- `immediate_statuses`: map/list of known terminal/running states if already available.
- `next_steps`: recommended deterministic checks, usually `daemon(action="list", contains=<group_id or run_id>)`, then `daemon(action="check", id=...)` for failures.

#### `psyche(context="molt")`

If the molt completed, include:

- `molt_count`.
- `summary_path`.
- `archive_path`.
- `tokens_before` / `tokens_after` if available.
- `next_steps`: read the post-molt brief / summary path before retrying any context operation.

#### External send/reply tools

For tools that can send messages or mutate external state (`email`, `telegram`, `imap`, `wechat`, `feishu`, etc.), include only safe routing/state identifiers:

- channel/account alias;
- message id or recipient id if it is already normally visible to the agent;
- send/reply status;
- next step: check sent state / conversation before retrying.

Do **not** include raw secrets, tokens, webhook payloads, full private messages beyond the already-returned bounded tool result, or raw tool args if they may contain credentials.

### Result-size policy

Recovery records must not solve a continuation failure by reinjecting the same oversized payload into the next provider call.

- Store or point to large results by path/artifact id.
- Include a bounded preview and exact byte/char counts.
- Require explicit range reads for large artifacts.
- Keep system notifications short enough to survive notification sync and provider context limits.

## Runtime behavior requirements

### 1. Preserve truthful tool completion state

If the tool completed, the recovery text must not imply that the side effect is unknown. It should say:

- the tool completed locally;
- the continuation failed after completion;
- do not blindly retry;
- inspect the recorded state and continue from the confirmed side effect.

This aligns with existing `tool_completed=True` synthetic results and real tool-result restoration.

### 2. Prefer exact replay before synthetic uncertainty

If a pending tool-call heal is needed later, the runtime should first try to replay exact durable `tool_result` evidence before synthesizing “may or may not have completed.” If exact evidence is absent or unsafe, it should fall back to synthetic uncertainty.

This is the domain covered by `lingtai-kernel#297`.

### 3. Notify when the agent may appear stuck

When continuation failure leaves the agent asleep/stuck or with a worker still running, a high-priority system notification should expose:

- that the agent is not doing invisible useful work;
- the predecessor tool names/count;
- a safe next action (`refresh`, inspect daemon group, read recovery artifact, etc.).

This overlaps with `lingtai-kernel#295` and `lingtai-kernel#298`.

### 4. Daemon terminal state must be honest

A daemon child that timed out, was cancelled, or failed must not be suppressed as a short successful result. Terminal state priority must prefer explicit run-dir state, watchdog timeout, cancellation, failure sentinels, and near-timeout backstops before `done`.

This is the domain covered by `lingtai-kernel#296`.

### 5. Do not retry side-effecting tools automatically

The runtime may suggest deterministic reconciliation steps, but it must not automatically re-run a side-effecting tool call after continuation failure. Retrying belongs to the agent/human after state inspection.

## Coverage map as of 2026-06-16

`lingtai#223` is a cluster issue. The following open PRs address parts of the required behavior:

- `lingtai-kernel#295` — surfaces post-tool continuation hangs with predecessor tool context and high-priority system notification.
- `lingtai-kernel#296` — fixes daemon terminal-state priority so timeout/cancel/fail notifications are not suppressed as short success.
- `lingtai-kernel#297` — replays exact durable tool results from `logs/events.jsonl` before synthetic pending-tool-call heal.
- `lingtai-kernel#298` — adds fail-closed WorkerStillRunning poison recovery, bounded unfinished-turn artifacts, and refresh handoff.

Those PRs are complementary. Landing only one of them does not fully close `lingtai#223`.

## Remaining implementation gaps

After the above PRs, the main remaining gaps are:

1. **Unified recovery record schema** — the kernel should write a stable, documented `post_tool_continuation_failed` record rather than relying on ad-hoc log fields and prose notices.
2. **Daemon spawn reconciliation envelope** — `daemon(action="emanate")` continuation failures should expose `ids`, `group_id`, `run_ids`, and task counts in the recovery record when those are present in the tool result.
3. **Large-result recovery discipline** — spill artifacts should be referenced by path + bounded preview, not eagerly read into daemon/model input during forensic recovery.
4. **Close criteria** — `lingtai#223` should close only when either:
   - the above schema/envelope exists and the listed PRs have landed, or
   - maintainers explicitly split the remaining gaps into follow-up issues and close #223 as tracked by those issues.

## Acceptance checklist

A future implementation PR that claims to close `lingtai#223` should include tests for:

- local tool result exists, continuation fails, and a durable recovery record is written;
- `daemon(action="emanate")` result with multiple ids produces a recovery record containing those ids and `group_id`;
- recovery notice/result never includes unbounded raw result content;
- pending-tool-call heal replays durable exact result before synthetic fallback;
- WorkerStillRunning continuation poison does not mutate/save unsafe chat history after timeout;
- daemon timeout/cancel/fail terminal states produce visible notifications and are not suppressed as short success;
- recovery instructions say inspect actual state before retrying, not “retry the tool.”

## Non-goals

- Reversing side effects.
- Retrying side-effecting tool calls automatically.
- Logging raw secrets or full tool args.
- Making daemon provider rate limits disappear; the contract only requires state to be inspectable and retry planning to be explicit.
