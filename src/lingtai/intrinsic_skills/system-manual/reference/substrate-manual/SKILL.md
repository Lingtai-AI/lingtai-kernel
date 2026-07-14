---
name: substrate-manual
description: >
  Nested system-manual reference for the expanded LingTai substrate/runtime model.
  Read via the `system-manual` router when resident substrate is too compact and
  you need details about body/extensions, bash vs daemon vs avatar vs MCP,
  lifecycle states (ACTIVE/IDLE/STUCK/ASLEEP/SUSPENDED), the `system` tool,
  notification/read/dismiss discipline, communication channels, memory layers,
  molt model, runtime log routing, collaboration topology, MCP/addon ownership,
  idle/soul behavior, preset tiers, the detailed preset runtime model (raw vs
  resolved init.json, preset identity, TUI/library vs main-agent allowed-only
  catalogs, swap/revert/refresh, daemon task explicit/omitted/CLI-skip paths),
  and resident substrate maintenance. This is
  a nested skill-reference under `system-manual`, not a standalone catalog skill;
  its folder may carry scripts/assets as the substrate reference grows.
version: 1.2.0
tags: [lingtai, system-manual, substrate, runtime, lifecycle, communication, memory, notifications, mcp, preset]
last_changed_at: "2026-07-14T00:00:00-07:00"
---

# Substrate Manual

The resident `substrate` prompt is the compact operating model every LingTai
agent keeps in memory. This reference is its expanded form. Read it when the
short substrate rule is not enough to decide what an agent is, which body to use,
how lifecycle states differ, how communication/notifications work, where memory
belongs, or what the `system` tool controls.

This file is a **nested skill-reference owned by `system-manual`**, not a top-level catalog skill.
Start at `system-manual` when routing is unclear; return here for the expanded
runtime model.

## 1. Body and extensions

An agent has one active mind—the LLM turn loop—and several extensions. Choose the
smallest durable form that fits the need:

| Extension | Persistence | Use it for | Do not use it for |
|---|---:|---|---|
| **Bash** | One command / job | Deterministic host work: git, tests, scripts, curl, builds, file transforms | Long-lived specialization or social coordination |
| **Daemon** | Ephemeral | Context-isolated exploration where you only need the conclusion or artifact | Work that must remember, own a relationship, or persist learning |
| **Avatar** | Persistent peer | A durable specialist, collaborator, or capability that should grow over time | Tiny mechanical tasks better done by bash/daemon |
| **MCP server** | Persistent external tool | Real services and integrations: IMAP, Telegram, Feishu, WeChat, third-party APIs | One-off shell operations or agent memory |
| **Knowledge** | Durable, private | Project facts, decisions, local paths, journals, collaborator context | Portable procedures other agents should reuse |
| **Skill** | Durable, portable | Reusable know-how, checklists, scripts, templates, references | Private project facts or raw logs |

Decision tree:

1. Can one deterministic command/script do it? Use bash.
2. Is it exploratory/noisy, and only the conclusion matters? Use daemon.
3. Should a capability or relationship persist and accumulate experience? Spawn
   or contact an avatar.
4. Is it a durable external service? Use or configure an MCP.
5. Is it a private fact or decision? Put it in knowledge.
6. Is it reusable procedure? Write or update a skill.

Prefer bash over daemon for deterministic commands, daemon over avatar for
throwaway parallel exploration, avatar over daemon for ongoing specialization,
knowledge over pad for durable private facts, and skills over knowledge for
reusable procedures others may need.

## 2. Lifecycle states

Common states:

- **ACTIVE**: currently in a turn. Notifications may be mirrored but not yet
  acted on; some producers defer active-turn injection until the turn ends.
- **IDLE**: awake and waiting. Listeners remain live; soul flow may fire.
- **STUCK**: runtime believes the agent may be blocked or unresponsive.
- **ASLEEP**: quiet but wakeable by mailbox/listener events.
- **SUSPENDED**: process-dead; requires CPR or external restart.

Use sleep/lull for routine rest. Use suspend only when process death is intended.
Use refresh to reload configuration/tools without destroying identity. Use clear
only for recovery when a conversation must be shed externally.

## 3. The `system` tool in practice

Read the tool schema before acting; lifecycle operations can affect other peers.
General guidance:

### `refresh`

Use after changing `init.json`, MCP registry, presets, prompt sections, or
installed capabilities. Refresh preserves identity and conversation while
rebuilding the runtime surface. For runtime/version checks, inspect the live
agent interpreter and imports — prefer `LINGTAI_RUNTIME_PYTHON` when available,
then confirm `lingtai.__file__` and `lingtai.kernel.__file__` — rather than a
convenient shell `python`, conda env, or unrelated checkout. TUI-managed runs
normally expose that interpreter from their runtime venv (for example
`~/.lingtai-tui/runtime/venv` on macOS/Linux; Windows uses the corresponding
`Scripts\python.exe` inside the venv). If a new MCP/tool still does not appear
after refresh, inspect registry/config health before retrying.

**Peer readiness during relaunch.** A same-workdir `lingtai run` process can exist
before it has published a fresh heartbeat. During that gap, peer internal email
can bounce while CPR's child launch is refused as a duplicate PID; these
observations are compatible. Wait for the fresh heartbeat and retry the original
email instead of stacking CPR attempts. Internal email does not queue recipient
delivery across this gap; `email-manual` owns the detailed delivery and bounce
contract.

Refresh is also the **emergency** context-reconstruction path: reach for it when
context is broken or stale, or when an immediate provider-side rebuild is urgently
needed. It is not part of the normal summarize flow — summarize records compact
history now, offers an explicit proactive rebuild path at 0.75 via
`system(action="summarize", rebuild=true)`, and otherwise the runtime forces a
rebuild at the 1.0 full-context hard boundary (see `summarize` below), so
do not refresh just to "apply" a summarize.

### `presets`

Use to list preset bundles and their tier/connectivity/capability tags. Tier tags
are cost/quality hints, not moral rankings:

- tier 5: irreplaceable reasoning.
- tier 4: premium/high-stakes work.
- tier 3: strong everyday work.
- tier 2: cheap throughput.
- tier 1: opportunistic/free use.

Prefer the cheapest preset that can reliably perform the task; switch back when
experimentation is done. For the detailed preset runtime model — raw versus
resolved `init.json`, path identity, the two catalogs, main-agent swap/revert,
and the daemon task/CLI distinction — see §11 below.

### Notifications and dismiss → the `notification` tool

Reading and clearing notification channels is **not** a `system` operation. The
`system` tool exposes no notification or dismiss verb. Use the standalone
`notification` tool: `check` to read the live payload, and the atomic dismiss
verbs `dismiss_channel` / `dismiss_event` / `dismiss_ref` to clear a channel or a
single `system` event. Prefer producer-specific verbs first for guarded
producers (`email.read`, `email.dismiss`, Telegram `read`, other MCP read
actions); a generic channel dismiss is for channels that do not own their own
read state, or for stale mirrors when the producer-owned state is already
handled.

Never treat a notification preview as the full source of truth when it is
truncated, ambiguous, lacks an exact anchor, includes media/attachments, or
contains human instructions. Read the producer channel.

For channel allowlist, envelope shape, protected channels, stale-version/force
semantics, and how large results are ranked via agent_meta and summarized (plus
legacy `large_tool_result` dismiss), read
the first-level `notification-manual` skill.

### Three context-compression / continuation modes

LingTai has three deliberate ways to keep context lean, from local to
whole-conversation. All three preserve the raw original in durable logs; none is
canonical.

1. **A priori — reasoning-guided** (`summary=true` on `bash`/`read`/`grep`/`daemon`/`glob`): the
   tool runs normally and the raw is preserved, but the result is replaced by a
   generated summary *before* it ever enters your context. Prefer this over a
   posteriori summarization when you can predict bulk and already know the facts,
   counts, anchors, or conclusion to retain; it is the cheapest path because raw
   bulk never spends context. The summary is driven by your `reasoning` field. Hard cap:
   500,000 raw chars, above which no summary is generated and you get a refusal
   pointing at the preserved raw. A priori is **lossy** and assumption-driven —
   it compresses *before* you inspect, so prefer it only when you already know the
   narrow facts to keep. It does **not** replace a posteriori for
   high-information-density daemon outputs, reviews, long reports, or results
   whose important facts you cannot name in advance; for those, leave
   `summary=false`, consume, then summarize a posteriori. See
   `reference/summarize-manual/SKILL.md`.
2. **A posteriori — agent-guided** (`system(action="summarize")`, below): replace
   a result you have *already seen* and digested with your own summary.
3. **Molt — context-pressure-triggered** (§5): the whole-conversation
   continuation / reset, the stronger boundary when per-result summarization
   cannot keep context healthy.

Pick a priori first when you can predict bulk, do not need the raw, and already
know what the call must retain; use a posteriori when you've already consumed a
result or the important facts could not be named before inspection; molt when the
conversation as a whole is the problem.

### `summarize`

`summarize` is the a-posteriori system action for tool-result context hygiene:
after you have consumed a completed prior tool result and no longer need the raw
text visible, record a compact summary replacement for its raw payload regardless
of length. The
summary preserves the conclusion, evidence, anchors, validation, risks, and next
steps while lowering active context. Runtime high-attention guidance for this
behavior is carried in `_meta.guidance`, including the resident 0.75 manual
rebuild hint and the 1.0 hard forced-rebuild boundary rule.
Treat guidance as a system-prompt-like appendix placed at the end of context: it
is an ordered `sections[]` structure, not a loose metadata bag.  The kernel's
`meta_readme` explanation of the `_meta` envelope is therefore one guidance
section inside `sections[]`, alongside the packaged sections assembled from the
skill-style Markdown guidance catalog under `src/lingtai/prompts/meta_guidance/catalog/`
(`INDEX.md` + one `<id>.md` per section); follow that latest guidance first when
it appears.

Summarize records a compact replacement in runtime history and may clear large-result
reminders, but active provider-side reconstruction is delayed.
At the provider layer, runtimes serve requests by *appending* onto a stable
cache/continuation prefix rather than *reconstructing* it each turn; rebuilding
that prefix on every summarize would discard the cache benefit. So below the
full-context boundary the summarize stays pending and the session keeps appending —
this delay is normal. Once context is at/above 0.75, the runtime stamps
`_meta.tool_meta.context.rebuild`; if an earlier fresh provider context is worth
the cost, make one proactive tactical `system(action="summarize", rebuild=true)`
call — with new items (record then apply the pending set) or with no items (pure
rebuild of the already-pending summaries); applied summaries flip to
`status: done`. Do not loop rebuild/summarize. At context usage 1.0 (the
full-context hard boundary) the runtime forces a rebuild on the next request
regardless of whether pending summaries exist, but only ONCE per continuous
full-context episode (it does not re-force while context stays at/above 1.0, and
re-arms only after context later drops below 1.0): pending markers are applied and
marked done, and with no pending summaries it still runs to release transient
context. Every 1.0 forced rebuild ALWAYS carries a one-shot
`reconstruction.warning` (before→after context, proactive-0.75-rebuild advice, and
"if still above the 0.6 recovery target, molt"). If that one forced rebuild does
NOT clear the overflow (post-rebuild context stays strictly above 1.0), every
result then also carries a permanent `_meta.tool_meta.context.molt` line `100%
context Forced Rebuilt Failed. Context overflowed!! (xxx %) Molt IMMEDIATELY!!` —
molt immediately. Waiting until full context is not
ideal — prefer the proactive 0.75 rebuild; if the pending total is 0, the forced
rebuild has nothing to apply, so summarize more or molt instead.
`refresh` is reserved for emergency reconstruction (see above). Summarize
is a mini molt for a consumed tool result; molt is the stronger
whole-conversation summarize boundary when summarize/reconstruction cannot get
context below `0.6 * context_window`. A completed task can also be an
efficiency boundary, but molt is not free cleanup: after necessary reporting and
durable-store updates, if no concrete next action remains, default to proactive
task-boundary molt only once session (since-last-molt) API calls exceed 100. Below that
threshold, go idle unless context pressure, explicit human request, or
conversation confusion makes the fresh briefing worth the molt cost. If you have
already decided to molt, do not spend a separate summarize call merely to
prepare.

For the full operating procedure — urgent large-result summarization, idle
cleanup sweeps, original-result recovery by `tool_call_id`, summary quality,
large-result notification behavior, append-vs-reconstruction timing, and the
distinction between summarize and molt — read
`reference/summarize-manual/SKILL.md`.

### Sleep, lull, interrupt, suspend, CPR, clear, nirvana

- `sleep`: self-sleep until a wake event; appropriate when there is no concrete
  task and listeners should remain available.
- `lull`: put another agent to sleep; use only when you are responsible for its
  lifecycle.
- `interrupt`: cancel another agent's current turn; use for genuinely stuck or
  misdirected work.
- `suspend`: terminate another agent's process; stronger than sleep.
- `cpr`: revive a suspended/dead agent when you own the recovery.
- `clear`: force another agent to molt/clear conversation for recovery.
- `nirvana`: permanent destruction; requires special authority and an explicit
  reason.

For peers, prefer communication and diagnosis before force. Karma operations are
administrative tools, not shortcuts around collaboration.

## 4. Communication and notifications

Humans do not communicate through diary text. Reply on the channel where the
message arrived, using the channel's reply/send tools. Text output is private
journal/diary.

Notification previews are hints. Read the producer channel when:

- the preview is truncated or summarized;
- the message has media, attachments, callbacks, or voice transcription;
- the preview contains multiple messages and the newest unresponded message is
  not obvious;
- exact wording matters for authorization;
- the channel has producer-owned read/dismiss state.

For human instructions, acknowledge promptly. If work will take longer than a few
seconds, send a progress message with the communication tool directly before the
long tool call. If the notification preview is incomplete, ambiguous, or exact
anchoring matters, fetch the full message first with the producer channel's
normal read action, then continue. During long work, report meaningful progress
or blockers.

## 5. Memory layers and molt model

Conversation is temporary. Durable layers are:

| Layer | Purpose | Typical contents |
|---|---|---|
| **Pad** | Current work and indexes | Active task, next steps, open branches, who is waiting, pointers into knowledge/reports |
| **Character / lingtai** | Identity and standing relationships | Long-term specialties, collaboration topology, stable preferences and obligations |
| **Knowledge** | Private durable memory | Project facts, decisions, local paths, journals, raw observations, collaborator context |
| **Skills** | Portable know-how | Reusable workflows, command recipes, checklists, scripts, templates |

Flow knowledge outward:

1. Work happens in conversation.
2. Active state and pointers go to pad.
3. Private durable facts go to knowledge.
4. Reusable procedures become skills.
5. Identity/relationship changes update character.

When context pressure rises, tend durable stores before molting. The detailed
molt procedure, session-journal / molt-history record, and successor briefing
rules live in `psyche-manual`; this substrate reference only describes the
memory model.

## 6. Runtime logs and trace inspection

Runtime trace inspection is routed through `system-manual` to the SQLite subguide:
`reference/sqlite-log-query/SKILL.md`. Use that reference for `logs/log.sqlite`,
`lingtai-agent log doctor|query|rebuild`, JSONL source-of-truth rules, WAL/live
read caveats, and the `events` / `chat_entries` schema.

Do not invent SQL schema from memory. Load the reference before writing trace
queries.

## 7. Collaboration and network topology

The network is part of the agent's durable body. Keep topology knowledge in four
places:

- contacts: addresses and aliases;
- character: stable collaborators and specialties;
- pad: active delegations and who is waiting on whom;
- mail/chat history: evidence of actual interactions.

Ask peers for help when their capability fits. Help peers when you can, or route
them to someone better. Report outcomes to the people who need them; avoid broad
noise.

## 8. MCP and addon ownership

MCP servers are durable integrations. The operating model has three layers:

1. **Catalog/registry**: what servers are known.
2. **Activation/config**: what is enabled for this agent.
3. **Runtime tools**: what appears after refresh.

For configuration, onboarding, or troubleshooting, read `mcp-manual` and the
specific addon's README. Curated LingTai addon MCPs (IMAP, Telegram, Feishu,
WeChat, WhatsApp) own their own setup details; do not guess field names from
memory. If you are an avatar without admin ownership of an MCP, do not
reconfigure the orchestrator-owned integration; escalate or ask the orchestrator.

## 9. Idle and soul

When there is no concrete task, go idle/asleep rather than spinning, polling, or
using timed sleeps. Idle keeps listeners available.

Soul flow is advice, not command — and it is **opt-in, disabled by default**
(gated by the `LINGTAI_SOUL_FLOW_ENABLED` env var). When it is off,
`soul(action='flow')` returns a `disabled` status; `inquiry`/`config`/`voice`/
`dismiss` still work. Verify external-event claims through the relevant channel
before acting on any voice. Use self-inquiry when you need a deliberate pause for
judgment; use durable stores for conclusions that should survive molt. For the
full soul-flow mechanics — the env gate, disabled behavior, `delay_seconds` as
cadence-not-off-switch, enabling/disabling, and the privacy/cost rationale — read
the `soul-manual` skill.

## 10. Resident substrate maintenance

Keep resident substrate compact. It should hold invariant rules and routing cues,
not examples or long rationale. When a substrate section grows into recipes,
troubleshooting trees, or extended explanation, move the detail here or into a
more specific `system-manual/reference/*.md` node and leave a short resident
route behind.

## 11. Preset runtime model — `init.json` composition and the preset lifecycle

`init.json` is a distributed composition document, not a single independently
governed component: its schema, migration, active-preset materialization,
prompt reload, capability/MCP setup, identity projection, and main-agent versus
daemon-task selection are owned by several existing boundaries (schema
`init_schema.py`, migration `kernel/migrate`, preset core `kernel/presets.py`,
composition roots `cli.py`/`agent.py`, main-agent operations
`tools/system/preset.py`, and the daemon task path). This section is the single
canonical detailed reference for that composition and for the preset runtime
model specifically; `system-manual`'s router points here, and resident
`substrate`/`procedures` carry only compact routing cues.

**Coding agents:** the structural/code-navigation twin of this section is
`src/lingtai/ANATOMY.md` (its Connections/Notes cite the exact `agent.py`/
`cli.py`/`kernel/presets.py` symbols this section describes). A change to
`init.json` composition, preset materialization, or the daemon-task preset
path must re-check all four surfaces together in the same PR: that Anatomy's
citations, this canonical reference, the resident `substrate`/`procedures`
routing cues, and `tests/test_preset_runtime_model_docs.py` — not just the
code or a single doc layer.

### Raw `init.json` versus the derived resolved manifest

A raw, operator-owned `init.json` is not itself the running configuration. On
every boot and refresh it is composed:

```text
raw operator-owned init.json
  → migration / deprecated-field cleanup
  → active-preset materialization
  → schema validation + path resolution
  → derived system/manifest.resolved.json
  → boot or refresh composition (LLM/config, prompts, capabilities/MCP, identity)
```

- **Raw `init.json`** is the durable source an operator or the preset-swap path
  writes. Within the boot/refresh/preset-composition lifecycle this section
  describes, reads (boot, refresh, prompt-only reload) never write it back
  except the explicit exceptions below.
- **`system/manifest.resolved.json`** is a **derived** runtime artifact: the
  fully materialized, validated, path-resolved manifest with secret-bearing
  keys removed, regenerated on every boot/refresh/molt-reload. It exists so
  consumers can read the actual running configuration without reimplementing
  preset resolution. It is never a write-back source and must not be described
  as one.
- Within this boot/refresh/preset-composition lifecycle, the raw-`init.json`
  writers are explicit: preset activation/swap (atomic write of the new
  active/default/allowed and materialized llm/capabilities), default-preset
  update on a named swap, CLI/agent migration or deprecated-field cleanup, and
  CLI boot's managed `venv_path` writeback (`cli.run` resolves the venv and writes `data["venv_path"]` back
  to `init.json` when the boot-selected/resolved venv differs from the raw
  value or is absent). Everything else
  covered by *this lifecycle* (LLM service state, prompt mirrors under
  `system/*.md`, `.agent.json` identity projection, MCP clients) is derived,
  in-memory-or-mirrored runtime state, not a second source of truth for
  `init.json` itself.
- **This list is scoped to the boot/refresh/preset-composition lifecycle
  above — it is not a repository-wide inventory of every raw-`init.json`
  writer.** Other owner-local features persist their own settings to raw
  `init.json` outside this lifecycle; document those under their owning
  tool/manual, not here. For example, `soul(action="config")` and
  `soul(action="voice")` persist `manifest.soul.*` (delay,
  consultation_past_count, voice, voice_prompt) directly to the agent's own
  `init.json` via `tools/soul/config.py`'s `_persist_soul_config` /
  `_persist_soul_voice`, independent of boot/refresh/preset-swap.

Top-level prompt/env/venv/addons/MCP/manifest field groups follow the same raw
→ derived shape but are owned elsewhere; do not duplicate their detail here:

| Field group | Real owner | Materialization / derived state | Refresh / restart |
|---|---|---|---|
| Prompt pairs (`covenant`, `pad`, `lingtai`, `base_prompt`, `comment`) | Prompt reload (`agent.py` `_reload_prompt_sections`); kernel-owned `principle`/`substrate`/`procedures` ignore init overrides | `system/<section>.md` mirrors, prompt-manager sections | Reloaded on boot/refresh/molt |
| `env_file`, `venv_path` | CLI boot / `venv_resolve.py` | Resolved process environment, venv marker state | Boot resolves; refresh/restart reuse |
| `addons`, `mcp` | MCP registry/addon decompression, capability setup | MCP clients, `_mcp_init_specs`, registry records | Boot loads; refresh retries failed then reloads |
| `manifest` (LLM, capabilities, agent identity, limits) | Schema + composition roots + capability registry | LLM service, `AgentConfig`, `.agent.json` sanitized projection | Boot/refresh reconstruct; some fields need full refresh, not summarize |

For the exact fields, validation, and per-field lifecycle detail, read
`init_schema.py`, `kernel/presets.py`, and `agent.py` directly (`_read_init`,
`_activate_preset`, `_reload_prompt_sections`) rather than expecting this
manual to restate a full field table. Some runtime gaps (`streaming`,
`pseudo_agent_subscriptions`, root-vs-LLM `context_limit` semantics, exact MCP
reload/venv/prompt persistence detail) are open implementation questions, not
resolved by this reference — do not infer a guarantee from a name alone.

### Preset identity and the two catalogs

A preset is one `.json`/`.jsonc` file; its identity is its exact path.
Accepted forms are absolute, `~`-relative, and workdir-relative — there is no
stem lookup, implicit extension, or implicit directory search.

There are two distinct catalog concepts, plus a separate worker path (below):

1. **TUI/library discovery** (`discover_presets_in_dirs()`) enumerates preset
   files in configured directories so a human authoring workflow can choose
   what to allow. This is a **TUI/library authoring helper, not runtime
   authorization**, and it is **not** a directory scan available at agent
   runtime.
2. **Main-agent catalog** (`system(action="presets")`) reads only
   `manifest.preset.allowed` and returns those exact paths with
   description/LLM/capability metadata and fresh connectivity. It is
   **allowed-only**: it must never be described as "all presets in the
   library," and it performs no directory scan or fallback beyond the
   `allowed` list.

`manifest.preset.active` is the preset currently selected/materialized for the
main agent. `manifest.preset.default` is the durable home/revert/fallback
target. `manifest.preset.allowed` is the explicit main-agent swap set. Schema
requires both `active` and `default` to be members of `allowed`.

### Main-agent swap, revert, and refresh sequence

1. Call `system(action="presets")` and choose an exact returned path — not a
   shorthand or a name outside `allowed`.
2. Call `system(action="refresh", preset=<path>)` for a named swap, or
   `revert_preset=true` to read `manifest.preset.default` instead. An empty
   optional `preset` string normalizes to absent; supplying both a non-empty
   `preset` and `revert_preset` is a conflict.
3. The refresh path checks the requested path's `allowed` membership, checks
   the target preset's context limit fits the current conversation, activates
   atomically (writes raw `init.json`), persists the new selected default for
   a named swap, best-effort retries failed MCPs, then rebuilds the runtime
   (LLM/config/capabilities/MCP/prompt reconstruction, preserving conversation
   history where a live session exists).
4. A config, prompt, MCP, or capability edit needs `refresh` to take effect;
   `system(action="summarize")` alone does not reconstruct the runtime and
   must not be used as a refresh substitute.

### Daemon task worker path — explicit, omitted, and external CLI

The daemon/task-worker preset path is a **separate explicit path**, not the
main-agent catalog operation, but on the LingTai backend an explicit
`tasks[].preset` must still resolve inside the main-agent
`manifest.preset.allowed` set — merely existing in the saved/library
directory is not authorization:

- `tasks[].preset` is an optional explicit `.json`/`.jsonc` path for an
  in-process LingTai daemon task. Before any LingTai-side effect (preset
  load, connectivity/provider probing, capability construction, run-dir
  creation, scheduling, or dispatch), the requested path must already be a
  member of the parent agent's resolved `manifest.preset.allowed` set. The
  check uses the same fail-closed normalized path-membership comparison as
  the main-agent swap gate (`_preset_ref_in` in
  `src/lingtai/kernel/presets.py`), so `~`-relative, absolute, and
  workdir-relative forms of an authorized path all pass. An unauthorized path
  is refused with a clear error before the gate reads or resolves anything
  else. The allowlist is read at all only when at least one task in the batch
  actually requests an explicit preset — the daemon schema recommends using a
  path returned by `system(action="presets")`, and that returned path is
  exactly what passes the gate.
- Omitting `tasks[].preset` means the daemon task inherits the **parent's
  regular (non-MCP) effective surface** — a parent-derived preset, not a fresh
  independent default — and this path never reads or consults
  `manifest.preset.allowed` at all. A no-preset LingTai daemon still gets a
  fresh daemon-scoped service rather than reusing the parent's live service
  instance.
- **External CLI backends** (`claude-p`, `codex`, `opencode`, and other
  CLI-driven backends) **skip LingTai preset resolution entirely** and are
  unaffected by this gate. The external CLI owns its own
  model/tools/permissions, and the daemon `tools` field is ignored for that
  path. Do not describe a CLI-backend task as using the LingTai
  preset/allowed model. Task MCP registrations remain separate from LingTai
  preset resolution on every backend.

### Authorizing a saved preset for daemon use

A saved `.json`/`.jsonc` preset is not usable from `tasks[].preset` until the
owning agent's config explicitly allows it. This is a config-owner action —
the daemon call itself cannot mutate `manifest.preset.allowed`:

1. Identify the preset's exact path (for example the path shown by the
   preset-library screen, or wherever the `.json`/`.jsonc` file was saved).
2. Ask whoever owns the agent's `init.json` to add that exact path as a new
   entry in `manifest.preset.allowed`, preserving every existing entry and
   the existing `active`/`default` values — `allowed` must remain a
   non-empty list containing both `active` and `default`.
3. Have the agent refresh (`system(action="refresh")`) so the edited
   `init.json` is re-read and the new entry takes effect.
4. Call `system(action="presets")` and confirm the exact path now appears in
   the allowed-only catalog it returns.
5. Pass that exact returned path — not a shorthand, not the pre-authorization
   path string, and not a directory-scan result — in `tasks[].preset`.

Skipping step 2 (for example, saving the file into the library directory
without editing `allowed`) does not authorize it: `system(action="presets")`
still will not list it, and an `emanate` call using its path is refused by
the gate above.

### Failure and authorization boundaries

- An unauthorized main-agent swap path (not in `allowed`) is rejected before
  any runtime change.
- A target whose context limit cannot hold the current conversation is
  rejected before activation.
- If the active preset file is missing, materialization may fall back to a
  different loadable default; if an existing active preset is malformed,
  materialization fails rather than silently substituting another preset.
- An unauthorized explicit `tasks[].preset` (not in `manifest.preset.allowed`)
  is rejected before load, connectivity/capability preflight, run-dir
  creation, scheduling, or dispatch of that task.
- Once past the allowlist gate, daemon explicit-path preflight failure
  (unloadable path, failed connectivity, failed capability instantiation)
  still prevents dispatch of that task.
