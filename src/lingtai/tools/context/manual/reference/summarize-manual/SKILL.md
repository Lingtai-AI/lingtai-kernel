---
name: summarize-manual
description: >-
  Operational guide for tool-result summarization: the three compression modes
  (a-priori `summary=true`, a-posteriori `context(action="summarize")`, molt),
  the urgent and idle cadences, delayed provider reconstruction and the
  0.85/1.0 rebuild boundaries, recovery by `tool_call_id`, and summarize
  versus molt.
last_changed_at: "2026-08-07T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/tools/system/summarize.py
- src/lingtai/prompts/meta_guidance/catalog/summarize_best_practice.md
- src/lingtai/prompts/meta_guidance/catalog/summarize_reconstruction_threshold.md
maintenance: |
  Tracks the summarize-manual topic it documents; update when that integration changes.
---

# Summarize Manual

`context(action="summarize")` is context hygiene for completed tool results. It
records an agent-authored compact replacement for one or more prior tool-result
blocks in runtime history. It does **not** delete the original event; the raw
result remains in logs for fallback, and the active provider continuation may
still carry the old raw block until delayed reconstruction applies the compacted
history.

Use this manual when runtime guidance tells you to summarize, when a result is
ranked large under `_meta.agent_meta.agent_state.current_tool_result_chars.top_results`,
when tool output has served its immediate purpose, or when you need to explain
how summarize differs from molt.

## 0 · The three modes (a priori, a posteriori, molt)

Tool-result summarization is one face of a larger idea: keeping context lean.
LingTai gives you three deliberate modes, ordered from local to
whole-conversation. All three preserve the raw original in durable logs and none
is canonical.

| Mode | Trigger | When the raw is hidden | Authored by |
|---|---|---|---|
| **A priori** — reasoning-guided | `summarize=true` (legacy `summary=true` also accepted) on any migrated family — e.g. `file` (covers read/grep/glob-style bulky reads), `shell`, `daemon` | *before* the result ever enters context | the runtime LLM, driven by your `reasoning` |
| **A posteriori** — agent-guided | `context(action="summarize")` | *after* you have already seen and digested it | you |
| **Molt** — context-pressure-triggered | `context(action='molt', ...)` | the whole conversation is continued/reset | you (briefing) |

Sections 1–6 below are mostly about the a-posteriori `summarize` action; §1a
covers the a-priori `summary=true` option and when to prefer it; §6 contrasts
both with molt.

## 1a · A priori summary: `summarize=true` on migrated tool families

Any LingTai Tool Protocol v2-migrated family — `web`, `mcp`, `file` (covering
read/grep/glob-style bulky reads), `vision`, `avatar`, `soul`, `shell`,
`notification`, `system`, `daemon`, `email`, `task_card`, `context`, `psyche`
— accepts an optional boolean root `summarize` (legacy spelling `summary` is
also honored on any call, for backward compatibility). Default `false`. When
`true`:

- The tool runs **normally**. The raw result is written to the durable event log
  and (if oversized) spilled, exactly as with `summary=false` — nothing is lost
  and the raw is recoverable by `tool_call_id` (see §4).
- **Before** the result enters your model-visible context, the runtime replaces
  it with a generated summary. The summary is driven by your `reasoning` field
  on that call — so when you set `summary=true`, make `reasoning` specific about
  what to retain (e.g. "I only need the failing test names and their assertion
  messages", "I only need the list of changed file paths").
- The replacement is clearly marked **generated and non-canonical** and carries
  a retrieval hint pointing back at the preserved raw by `tool_call_id`.

Prefer it when you can state the retention spec before the call and the output
is large — rule of thumb >10k chars; leave it `false` when you need exact
line/file/diff/stderr text you will quote, diff, patch, or compare.

**A priori is preferred but still lossy.** The runtime discards everything
outside what your `reasoning` named, with no chance for you to notice what
mattered — so it is **not** a substitute for a-posteriori
`context(action="summarize")` on high-information-density results whose
important facts you cannot name in advance (daemon outputs, code reviews, long
reports). For those, leave `summary=false`, consume the raw, then summarize a
posteriori once you know what to keep — or molt when the whole conversation is
the pressure.

**Hard cap.** If the raw visible payload exceeds **500,000 characters**, the
runtime does **not** call the summarizer LLM. Instead you receive a small
summary-layer refusal that says the result exceeded the cap, that the raw is
preserved, and how to retrieve / narrow / rerun. The oversized raw is **not**
dumped into your context on this path. Narrow the call (tighter command, path,
pattern, or `offset`/`limit`) and rerun, or rerun with `summary=false` to take
the raw (capped/spilled) result directly.

**Untrusted output.** The summarizer treats the tool output strictly as data: it
will not follow instructions embedded inside the tool result. This is the same
prompt-injection posture the rest of the runtime uses for external text.

**Fail-closed.** `summary=true` is a request *not* to put the raw into context.
If the summarizer call fails or returns nothing, you get a summary-layer error
with the retrieval locator — never the raw payload. Rerun with `summary=false`
if you actually need the raw.

**Reasoning critique as feedback.** A generated `summary=true` result may end
with a brief, plain-text critique of whether your `reasoning` (the retention
spec) was specific enough to guide what to keep. Treat it as feedback: sharpen
your `reasoning` on later `summary=true` calls so the summary keeps what you
actually need. If the critique says the reasoning was too poor for the summary
to be trusted, do not rely on the lossy summary — inspect the preserved raw
original via the retrieval hint / `raw_locator` (by `tool_call_id`, see §4)
before acting on it. This critique is ordinary summary prose, not a separate
field — read it, don't parse it.

## 1 · The principle: progressive disclosure

A raw tool result is the first layer: it is useful while you inspect it. After
you have consumed it and no longer need the raw text visible, the better layer is
an index that future-you can reason from without carrying the raw bulk. Strongly
prefer summarizing already-digested completed tool results regardless of length;
keep raw output visible only for active inspection, quotation, or comparison.

A good summary should let future-you decide whether the hidden raw result must
be reopened. Preserve:

- the conclusion or decision;
- key evidence, measurements, or error text;
- paths, URLs, message IDs, tool_call_ids, commit hashes, job IDs, and other
  anchors;
- validation status and commands/tests run;
- risks, caveats, and unresolved questions;
- next steps.

Do not write casual one-liners for consequential results. The summary is the
progressive-disclosure entry point.

## 2 · The two summarize cadences

### Urgent cadence: summarize the bulky result now

Use this when a tool result is long or noisy — typically one that ranks high in
`_meta.agent_meta.agent_state.current_tool_result_chars.top_results` (above its `threshold`,
counted in `over_threshold_count`). `agent_meta` is a complete current final-carrier snapshot attached to each eligible final carrier. Its nested `agent_state.current_tool_result_chars` is current on the newest emitted snapshot; older snapshots remain retained historical traces and are not actionable.

1. Read or inspect the result first.
2. Decide what future-you needs from it.
3. On a later step, call `context(action="summarize")` on the completed prior
   result. Do not try to summarize the current result in the same tool batch
   before it exists.
4. Batch several already-digested results in one summarize call when convenient.

### Idle cleanup cadence: sweep what is already consumed

Use this when the task quiets down, before the context window becomes urgent.
Look back over older tool results that are already digested, obsolete, or only
useful as evidence anchors, and replace them with summaries regardless of length
when you are continuing in the same session. This lowers token per API call and
improves cache/continuation efficiency for the next turn.

Idle cleanup is also the right time to decide whether a deliberate molt is
worth its cost. If the current task is complete, necessary reporting/durable
stores are tended, no human reply is pending, and no concrete next action
remains, do not molt automatically; molt only when context pressure (≥85%),
explicit human request, or conversation confusion makes the fresh briefing
worth its cost. Summarize is a mini molt for a consumed tool result. Once you
have decided to molt, do not spend a separate summarize call merely to
prepare; molt is the stronger whole-conversation summarize boundary.

## 3 · How to call summarize

Summarize prior completed tool results only:

```json
{
  "action": "summarize",
  "input": {
    "items": [
      {
        "tool_call_id": "call_abc123",
        "summary": "What future-you needs: conclusion, evidence, anchors, validation, risks, next steps."
      }
    ]
  },
  "reasoning": "Compact the completed result while preserving its evidence and next steps."
}
```

Operational rules:

- `tool_call_id` is the producer call ID shown on the original result, not the
  visible `_tool_call_id` event ref.
- A successful summarize updates the runtime-history/chat-history copy and
  persists that compact replacement; it does not mutate the original event log
  and does not by itself prove the active provider continuation has dropped the
  old raw block.
- If a large-result notification points at that result, successful summarize
  clears the reminder.
- If the result is still ambiguous, reopen or inspect it before summarizing.

## 3a · Delayed summarization: summary recorded now, provider reconstruction delayed

Summarize has two decoupled effects:

1. **Runtime-history replacement now.** The prior tool-result block in local
   history is replaced with your agent-authored summary, stamped `status: pending`,
   and matching large-result reminders may clear.
2. **Full reconstruction later.** The current provider continuation may still
   contain the old raw block. A manual `context(action="rebuild")` first re-reads
   and recomposes every canonical system-prompt source, then applies pending/new
   summaries (markers flip to `status: done`), then requests provider replay with
   the new prompt/history. The automatic 1.0 hard forced path uses the
   same full prompt reconstruction contract before fresh replay.

The rebuild action's own result reports the provider round that requested the rebuild.
Post-rebuild context usage does not exist yet at that point; it only becomes
observable on the next provider round. Do not read the requesting round's usage
number as if it already reflects the rebuilt context.

The dynamic pending totals in the result comment scan only `status: pending`
markers — already-applied (`done`) markers and legacy markers without a status
are not counted as pending.

Reconstruction is delayed because runtimes append turns onto a stable
cache/continuation prefix, and rebuilding that prefix on every summarize would
discard the cache benefit.

- **Below 1.0 of the context window:** summarize stays pending at the provider
  layer and the session keeps appending. This delay is normal, not a failure.
- **At or above 0.85 of the context window:** `_meta.agent_meta.agent_state.context.rebuild`
  is stamped continuously. It is a decision prompt / permission, not an automatic
  rebuild — recording summaries never triggers a provider-context rebuild on its
  own. If making already-recorded summaries active in the provider context earlier
  is worth the cost, make one proactive tactical
  `context(action="rebuild", input={})` call. Every call recomposes all canonical
  prompt sources first. With new items it then records/applies them; with no items
  it applies the already-pending set, or simply replays the newly composed prompt
  when none are pending. Bare `{}` is valid. Do not loop rebuild/summarize calls.
- **At 1.0 of the context window (the full-context HARD boundary):** the runtime
  **forces** a provider-context rebuild / fresh replay on the next request
  **regardless of whether pending summaries exist**, but only **once per
  continuous full-context episode**. The automatic forced rebuild fires a single
  time when provider-reported usage first reaches `1.0` (inclusive); it does NOT
  re-force while usage stays at/above `1.0` (including exactly `1.0`), and it
  re-arms only after a later provider round drops usage strictly below `1.0`, so a
  future crossing can force exactly once again. Both automatic paths — the
  pre-request boundary check and the immediate post-`summarize` release — share
  this one latch, so they cannot double-fire. (Explicit
  `context(action="rebuild")` is independent and always available.)
  If pending summaries exist,
  they are applied and their markers marked done. `summarize` is the only
  historical tool-result body replacement a rebuild applies; the fresh replay
  otherwise preserves each historical timely-transient holder and does not
  strip its agent_meta/guidance or notifications/notification_guidance keys,
  on every provider, without rewriting recorded history — only the LATEST
  holder
  per family represents current state, older holders are historical traces
  and must not be acted on. Every 1.0 forced rebuild ALWAYS carries a one-shot
  `_meta.agent_meta.agent_state.events.reconstruction.warning`: it reports the before→after context
  change, advises that reaching the full boundary means waiting was not ideal (prefer
  a proactive 0.85 `context(action="rebuild")`), and says that if the rebuilt context is still
  above the 0.75 recovery target you should tend durable stores and molt. This is
  one unified warning — it does not branch on whether context dropped low or stayed
  high.
- **If the forced rebuild does NOT clear the overflow:** the persistent
  `Forced Rebuilt Failed` warning activates. Once the forced fresh replay's own
  first provider response is observed and that post-rebuild provider input is still
  **strictly above** `1.0` (a failed forced request keeps verification pending
  until a successful provider-usage result exists), EVERY following result carries
  a permanent `_meta.agent_meta.agent_state.context.molt` line, verbatim:
  `100% context Forced Rebuild Failed to Bring Usage Below 100%. Context overflowed!! (xxx %) Molt IMMEDIATELY!!`
  (`xxx` = the current measured percentage, one decimal). It rides the same
  permanent current-state channel as the sustained-pressure and cache-miss-budget
  molt reminders (each preserved on its own line when several coexist). Because the
  runtime will NOT keep force-rebuilding while you stay overflowed, this line is
  the signal that the emergency rebuild could not recover the context — molt
  immediately. At exactly `1.0` after the rebuild there is no repeat rebuild and no
  overflow warning (the warning is for strictly `> 1.0`).

Waiting for the 1.0 forced boundary is the emergency path — prefer the proactive
0.85 rebuild. If no summary is pending, the forced rebuild has nothing to apply
(the replay-preservation rule above still holds), so summarize more or molt
instead. `refresh` is the emergency path for broken/stale context or explicit
human direction — never the way to "apply" a summary. If summarize or
a rebuild still cannot bring context below `0.75 * context_window` (the recovery
target), tend durable stores and molt deliberately.

## 4 · Recovering the original result

A summarized block should carry a retrieval hint. The usual fallback is to search
the agent event log by the preserved `tool_call_id`:

```bash
grep 'call_abc123' <workdir>/logs/events.jsonl
```

For structured trace work, use the SQLite/log tooling documented in
`../../../system-manual/reference/sqlite-log-query/SKILL.md`, for example `lingtai-agent log query`, to
locate the event and inspect nearby context.

If the original was a spill result, the log entry or summary should also point to
the spill path under `tmp/tool-results/`. Preserve that path in the summary.

## 5 · Good and bad uses

Good uses:

- a large test output after you know which tests passed/failed;
- a long file read after extracting the relevant lines and path;
- a search sweep after preserving matched files and decisions;
- a channel read after responding and keeping the message IDs that matter;
- a resolved error once the recovery path is known.

Bad uses:

- summarizing before you have read or understood the result;
- hiding evidence that you still need to inspect line by line;
- replacing a required deliverable with a vague recap;
- assuming summarize is a durable memory layer.

## 6 · Summarize is not molt

Neither summary mode updates pad, character, knowledge, skills, or the
session-journal, and neither sheds the conversation — the mini-molt framing and
the molt boundary are resident. `context-manual` owns the molt procedure and its
required checklist. Use them together:

1. Summarize bulky consumed tool results so the active context is navigable.
2. Tend durable stores for facts, procedures, identity changes, and current plan.
3. Molt deliberately while you still have enough context to write a good
   briefing, not only when warnings become urgent.
