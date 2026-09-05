---
name: substrate-manual
description: >
  Nested system-manual reference: the expanded form of the resident substrate
  prompt — body/extensions, lifecycle states, the `system` tool in practice,
  communication and memory layers, collaboration topology, MCP/addon ownership,
  and (§11) the canonical `init.json` composition and preset runtime model.
  Route via `system-manual` when it is unclear whether this is the right node.
version: 1.5.1
tags: [lingtai, system-manual, substrate, runtime, lifecycle, alarm, communication, memory, notifications, mcp, preset]
last_changed_at: "2026-08-29T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/prompts/substrate/substrate.md
- src/lingtai/prompts/substrate/substrate.yaml
- src/lingtai/tools/system/schema.py
- src/lingtai/tools/system/karma.py
- src/lingtai/kernel/base_agent/lifecycle.py
maintenance: |
  Tracks the substrate-manual topic it documents; update when that integration changes.
---

# Substrate Manual

This is the expanded form of the resident `substrate` prompt — a nested
skill-reference owned by `system-manual`, not a top-level catalog skill. Read it
when the short resident rule is not enough; route via `system-manual` when it is
unclear whether this is the right node.

## 1. Body and extensions

An agent has one active mind—the LLM turn loop—and several extensions:

| Extension | Persistence | Use it for | Do not use it for |
|---|---:|---|---|
| **Shell (bash)** | One command / job | Deterministic host work: git, tests, scripts, curl, builds, file transforms | Long-lived specialization or social coordination |
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
rebuilding the runtime surface. Resident substrate §I owns the runtime/version
probe rule; TUI-managed runs normally expose that interpreter from their runtime
venv (for example `~/.lingtai-tui/runtime/venv` on macOS/Linux; Windows uses the
corresponding `Scripts\python.exe` inside the venv). The ordered pre-flight to
run before pressing the button, and the post-refresh verification pass, are owned
by `reference/refresh-precheck/SKILL.md`.

**Peer readiness during relaunch.** A same-workdir `lingtai run` process can exist
before it has published a fresh heartbeat. During that gap, peer internal email
can bounce while CPR's child launch is refused as a duplicate PID; these
observations are compatible. Wait for the fresh heartbeat and retry the original
email instead of stacking CPR attempts. Internal email does not queue recipient
delivery across this gap; `email-manual` owns the detailed delivery and bounce
contract.

Refresh is also a passive full-context reconstruction path with broader
lifecycle effects: reach for it when runtime context/configuration is broken or
stale, never merely to apply a summary. Active reconstruction belongs to
`context(action="rebuild")` — see `context-manual` →
`reference/summarize-manual/SKILL.md` for the rebuild contract and the 0.85/1.0
boundaries.

### `presets`

Use to list preset bundles and their tier/connectivity/capability tags. The
tier-5-to-tier-1 ladder is cost/quality hints, not moral rankings, and is listed
in resident substrate §VII; prefer the cheapest preset that can reliably perform
the task and switch back when experimentation is done. The detailed preset
runtime model — raw versus resolved `init.json`, path identity, the two catalogs,
main-agent swap/revert, and the daemon task/CLI distinction — is §11 below; the
pre-swap checklist is `reference/refresh-precheck/SKILL.md`.

### Notifications and dismiss → the `notification` tool

Reading and clearing notification channels is **not** a `system` operation (the
verbs are on the `notification` tool; resident substrate lists them). The rule
worth holding here: **prefer producer-specific verbs first** for guarded
producers (`email.read`, `email.dismiss`, Telegram `read`, other MCP read
actions); a generic channel dismiss is for channels that do not own their own
read state, or for stale mirrors when the producer-owned state is already
handled. Never treat a notification preview as the full source of truth — §4
lists when to read the producer channel instead.

Everything else — allowlist, envelope shape, protected channels,
stale-version/force, large-result ranking and the legacy `large_tool_result`
dismiss — is owned by the first-level `notification-manual` skill.

### Context compression, and where `summarize` lives

`system` exposes **no** `summarize` action. The three deliberate compression
modes — a-priori `summary=true`, a-posteriori `context(action="summarize")`, and
molt — are listed in resident substrate §VII and owned in full by `context-manual`
→ `reference/summarize-manual/SKILL.md` §0. Read that reference for the 0.85
proactive-rebuild stamp, the 1.0 forced-rebuild boundary and its overflow
warning, urgent versus idle-cleanup cadence, summary quality, original-result
recovery by `tool_call_id`, and the summarize-versus-molt distinction.

Two boundaries are worth restating only as boundaries: summarize records history
now while provider-side reconstruction is delayed, so a pending summary is
normal and `refresh` is not the way to apply one; and `refresh` stays reserved
for emergency context reconstruction, never as a summarize substitute.

Runtime high-attention guidance for this behavior arrives in
`_meta.agent_meta.guidance` — an ordered `sections[]` structure assembled from
the guidance catalog under `src/lingtai/prompts/meta_guidance/catalog/`
(`INDEX.md` + one `<id>.md` per section), which owns its own semantics. Follow
that latest guidance first when it appears.

### Sleep, lull, interrupt, suspend, CPR, clear, target_refresh, nirvana

- `sleep`: self-sleep until a wake event; appropriate when there is no concrete
  task and listeners should remain available.
- `lull`: put another agent to sleep; use only when you are responsible for its
  lifecycle.
- `interrupt`: cancel another agent's current turn; use for genuinely stuck or
  misdirected work.
- `suspend`: terminate another agent's process; stronger than sleep.
- `cpr`: revive a suspended/dead agent when you own the recovery.
- `clear`: force another agent to molt/clear conversation for recovery.
- `target_refresh`: ask another agent to refresh itself (the cross-process twin
  of your own `refresh`). It only writes the target's `.refresh` marker; the
  target's own heartbeat performs the refresh and restart, so the
  `refresh_requested` receipt confirms submission, never completion — confirm
  by observing the target afterwards. It is disruptive: use it only when you
  are responsible for that agent's lifecycle.
- `nirvana`: permanent destruction; requires special authority and an explicit
  reason.

For peers, prefer communication and diagnosis before force. Karma operations are
administrative tools, not shortcuts around collaboration.

### Last-resort `sleep(delay=...)` alarm

Normal waiting is **not** timed sleep: use reliable producer completion
notifications and ordinary **IDLE** whenever the async producer can notify you.
Only when deliberately waiting for async work that has no reliable completion
notification may `system(action="sleep", input={"delay": <positive seconds>,
...})` arm this one-shot last-resort alarm. `delay` is a finite positive JSON
number of seconds and has no configured or public upper bound; `null`/omission
means ordinary sleep.

The agent workdir has at most one `<workdir>/.alarm`, containing only one
parseable absolute wall-clock deadline. The sleep call atomically replaces that
file before it enters ASLEEP. At or after the deadline, the heartbeat turns it
into one ordinary system notification; normal notification sync then performs
any ASLEEP wake. This is deliberately neither a scheduler nor a timer service:
there is no list, history, cancel action, or early-wake cancellation. An early
real notification may wake you, but the alarm remains armed; a later
`system.sleep(delay=...)` replaces it, while `system.sleep` without `delay`
leaves it alone. The file also survives restart until the due notification has
been published and consumed.

If `.alarm` is malformed or unreadable, the heartbeat leaves it untouched and
records a bounded `sleep_alarm_malformed` diagnostic once per unchanged problem
per process instead of firing or logging every tick. Do not guess or rewrite a
bad deadline as a recovery shortcut: inspect the workdir/runtime evidence and
choose an explicit later sleep alarm if appropriate.

**Cross-platform CPR limitation (documented, not a bug):** ``cpr`` relaunches
the target using its configured ``venv_path``, and the venv executable layout is
resolved from the *calling* runtime's platform.  A POSIX caller resolves a
Windows target's venv as ``venv/bin/python`` (POSIX layout), but a Windows venv
stores its launcher at ``Scripts/python.exe`` — so ``cpr`` of a Windows agent
from a Linux/macOS agent fails with "Configured venv_path is not usable".
Relaunch such an agent from the same platform it runs on (its own TUI/CLI, or
``lingtai-agent run <working_dir>`` on the Windows host) instead of crossing
platforms.  This is a documented limitation, not a defect to fix.

## 4. Communication and notifications

Resident §III owns the rule (reply on the channel the message arrived on; text
output is private diary) and lists the usual reasons a preview is not enough.
Two conditions are easy to miss and are worth naming here: read the producer
channel when **exact wording matters for authorization**, and whenever the
channel has **producer-owned read/dismiss state**.

The responsiveness discipline built on this surface — acknowledging promptly,
sending a progress message before long work, and reporting blockers — belongs to
`reference/procedures-manual/SKILL.md` §2.

## 5. Memory layers and molt model

Conversation is temporary. Durable layers are:

| Layer | Purpose | Typical contents |
|---|---|---|
| **Pad** | Current work and indexes | Active task, next steps, open branches, who is waiting, pointers into knowledge/reports |
| **Character / lingtai** | Identity and standing relationships | Long-term specialties, collaboration topology, stable preferences and obligations |
| **Knowledge** | Private durable memory | Project facts, decisions, local paths, journals, raw observations, collaborator context |
| **Skills** | Portable know-how | Reusable workflows, command recipes, checklists, scripts, templates |

Knowledge flows outward from conversation into those four layers; the routing
rule is resident §IV and the store-tending procedure is `context-manual` §2.
When context pressure rises, tend durable stores before molting. The detailed
molt procedure, session-journal / molt-history record, and successor briefing
rules live in `context-manual`; this reference only describes the memory model.

## 6. Runtime logs and trace inspection

Runtime trace inspection is owned by `reference/sqlite-log-query/SKILL.md`, and
mining those traces for improvement candidates by `reference/trajectory-mining/SKILL.md`.
Do not invent SQL schema from memory — load the reference before writing trace queries.

## 7. Collaboration and network topology

The network is part of the agent's durable body. Keep topology knowledge in four
places:

- contacts: addresses and aliases;
- character: stable collaborators and specialties;
- pad: active delegations and who is waiting on whom;
- mail/chat history: evidence of actual interactions.

Ask peers whose capability fits, help or route those who ask you, and report
outcomes to the people who need them without broadcasting noise.

## 8. MCP and addon ownership

MCP servers are durable integrations. The operating model has three layers:

1. **Catalog/registry**: what servers are known.
2. **Activation/config**: what is enabled for this agent.
3. **Runtime tools**: what appears after refresh.

`mcp-manual` owns configuration, onboarding, and troubleshooting for every
layer — read it rather than guessing field names. If you are an avatar without
admin ownership of an MCP, do not reconfigure the orchestrator-owned
integration; escalate or ask the orchestrator.

## 9. Idle and soul

With no concrete task, go idle/asleep rather than spinning, polling, or using
timed sleeps — idle keeps listeners available (resident §V). `soul-manual` owns
soul-flow mechanics in full: the `LINGTAI_SOUL_FLOW_ENABLED` gate, disabled-flow
behavior, `delay_seconds` as cadence-not-off-switch, and the privacy/cost
rationale.

## 10. Resident substrate maintenance

Maintainer-facing: keep resident substrate to invariant rules and routing cues.
The split rule ("detail goes into a nested reference, the router keeps a hint")
is stated once, in `system-manual` → "Maintaining this router".

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
  → read-only compatibility diagnosis
  → active-preset materialization in memory
  → schema validation + path resolution
  → derived system/manifest.resolved.json
  → boot or refresh composition (LLM/config, prompts, capabilities/MCP, identity)
```

- **Raw `init.json`** is the durable source an operator or an explicit
  preset-swap action writes. Within the boot/refresh/preset-composition
  lifecycle, the shared real reader only parses/materializes/validates/resolves
  in memory and never writes raw input back.
- **`system/manifest.resolved.json`** is a **derived** runtime artifact: the
  fully materialized, validated, path-resolved manifest with secret-bearing
  keys removed, regenerated on every boot/refresh/molt-reload. It exists so
  consumers can read the actual running configuration without reimplementing
  preset resolution. It is never a write-back source and must not be described
  as one.
- Within this boot/refresh/preset-composition lifecycle, the only raw-`init.json`
  writer is an explicit preset activation/swap action (atomic write of the new
  active/default/allowed and materialized llm/capabilities). Automatic
  migration, deprecated-field cleanup, AED fallback, and CLI venv write-back
  are intentionally absent. Everything else
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
| Psyche prompt pairs (`base_prompt`, `covenant`, `comment`) plus init Pad/LingTai seeds | Prompt reload (`agent.py` `_reload_prompt_sections`); Psyche reads `settings/psyche.json`, while kernel-owned `principle`/`substrate`/`procedures` ignore init overrides | `system/<section>.md` mirrors, prompt-manager sections | Reloaded on boot/refresh/molt |
| `env_file`, `venv_path` | `init_reader.py`, CLI boot / `venv_resolve.py` | Resolved process environment, venv marker state (in memory; raw input is unchanged) | Boot resolves; refresh/restart reuse |
| `addons`, `mcp` | MCP registry/addon decompression, capability setup | MCP clients, `_mcp_init_specs`, registry records | Boot loads; refresh retries failed then reloads |
| `manifest` (LLM, capabilities, agent identity, limits) | Schema + composition roots + capability registry | LLM service, `AgentConfig`, `.agent.json` sanitized projection | Boot/refresh reconstruct; some fields need full refresh, not summarize |

For the exact fields, validation, and per-field lifecycle detail, read
`init_schema.py`, `kernel/presets.py`, and `agent.py` directly (`_read_init`,
`_activate_preset`, `_reload_prompt_sections`) rather than expecting this
manual to restate a full field table. An authored *preset* may retain
`manifest.llm.context_limit` for its preset-local context-fit guard, but runtime
policy does not read it. Agent `init.json` likewise does not own
`context_limit`, `max_rpm`, `streaming`, `aed_timeout`, `max_aed_attempts`,
`snapshot_interval`, or `activeness`: old root keys are compatibility-known and
ignored, while valid environment and `settings/system.json` v2 values override
fixed defaults. Materialization and preset activation discard a preset context
limit instead of handing it into init. Email's owner manual defines the exact
`pseudo_agent_subscriptions` construction/relaunch lifecycle. Exact MCP reload,
venv, and prompt-persistence details remain open implementation questions not
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
2. Call `system(action="refresh", input={"preset": <path>})` for a named swap, or
   `system(action="refresh", input={"revert_preset": true})` to read
   `manifest.preset.default` instead. An empty
   optional `preset` string normalizes to absent; supplying both a non-empty
   `preset` and `revert_preset` is a conflict.
3. The refresh path checks the requested path's `allowed` membership, checks
   the target preset's context limit fits the current conversation, activates
   atomically (writes raw `init.json`), persists the new selected default for
   a named swap, best-effort retries failed MCPs, then rebuilds the runtime
   (LLM/config/capabilities/MCP/prompt reconstruction, preserving conversation
   history where a live session exists).
4. A config, prompt, MCP, or capability edit needs `refresh` to take effect;
   `context(action="summarize")` alone does not reconstruct the runtime and
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
  refuses the whole batch before load, connectivity/capability preflight,
  run-dir creation, scheduling, or dispatch.
- Once past the allowlist gate, daemon explicit-path preflight failure
  (unloadable path, failed connectivity, failed capability instantiation)
  still refuses the whole batch before any emanation is scheduled.
