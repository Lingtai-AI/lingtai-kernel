---
name: procedures-manual
description: >
  Nested system-manual reference: the expanded form of the resident procedures
  prompt — progressive disclosure, responsiveness and side-effect authorization,
  the daemon workflow methodology, depositing work, idle/lifecycle, skill
  routing, deliverables, artifact sharing, and issue reporting. Route via
  `system-manual` when it is unclear whether this is the right node.
version: 1.4.0
tags: [lingtai, system-manual, procedures, progressive-disclosure, responsiveness, deliverables, issue-reporting]
last_changed_at: "2026-09-05T05:26:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/prompts/procedures/procedures.md
- src/lingtai/prompts/procedures/procedures.yaml
maintenance: |
  Tracks the procedures-manual topic it documents; update when that integration changes.
---

# Procedures Manual

This is the expanded form of the resident `procedures` action checklist — a
nested skill-reference owned by `system-manual`, not a top-level catalog skill.
Read it when the short procedure tells you *what* to do but you need the routing
logic, edge-case discipline, or deliverable checklist behind it.

## 1. Progressive disclosure

Keep resident prompt small. Use it for invariant rules and routing. Put examples,
command recipes, troubleshooting, and long rationale in skills or references.

The normal ladder is:

`resident prompt` → `system-manual` router → `reference/<topic>.md` → anatomy/code/tests.

Ask three questions before adding resident text:

1. Must every agent always remember this exact rule?
2. Does it decide when to load a manual/reference?
3. Is it a short default that prevents common harm?

If the answer is no, put it in a skill/reference and leave a one-line route. Do
not jump straight to code when a manual/reference already names the path, and do
not bloat the resident prompt with one-off details.

### Tool-result digestion

Progressive disclosure applies to tool results as much as to manuals. Resident
`meta_guidance` owns summarize cadence and current pressure guidance; the addition
here: **if an adapter/provider
comment is present, follow its adapter-specific summarize rules on top of the
general ones.**

The first economy move is to avoid pulling bulky raw output into main context at
all. Bulky, mechanical, or repetitive work — full test suites, large log scans,
large diffs, issue sweeps, batch edits/validation — belongs in a daemon: frame the
task, give exact paths/commands and the expected artifacts, then review the
concise report. Use daemons to keep raw bulk out of main context; use summarize
for the bulk that already landed there. §3 owns the daemon workflow methodology.

### Query settings and runtime guidance

For effective values, defaults, and configurability, call the owning tool's
`settings` first. Use `system(action="settings", input={})` for kernel-level
settings without another concrete tool owner. Follow `comment` to the owning
manual before an authorized change; a SHOW result grants no mutation authority.
Do not reproduce adjustable numeric defaults in general workflow guidance.

`context-manual` → `reference/summarize-manual/SKILL.md` §3a owns reconstruction
mechanics and boundaries. Follow the current runtime pressure guidance instead
of duplicating thresholds here: pending summaries are normal, `refresh` is not
an apply-summary shortcut, and rebuild/summarize must not become a loop.

## 2. Action and responsiveness

When need arises, act — the acknowledge/progress-message/report-blockers
discipline is resident. Two things it does not spell out:

- When a notification preview is not enough, read the producer channel first;
  the conditions are listed in `reference/substrate-manual/SKILL.md` §4.
- Before delegating a PR, diff, or implementation for GLM/Claude/daemon
  review, re-check recent human-channel instructions for missed scope,
  boundary, or authorization changes. Use the producer channel, not memory
  or a notification digest alone; if the human named a window such as the
  last 30 messages, use that exact window and frame the reviewer with the
  latest contract.

Examples of external side effects that normally need explicit confirmation:
creating/filing issues, opening PRs, pushing commits, merging, deleting/closing
resources, changing public visibility, publishing packages, changing persistent
configuration, and rotating credentials. Read the local pad/character for any
standing exceptions.

## 3. Use the right body

`reference/substrate-manual/SKILL.md` §1 owns the extension table and the
smallest-durable-form decision tree (shell, daemon, avatar, MCP, knowledge,
skill). This section owns the *procedure* built on it.

If a task exceeds current capability, acquire capability rather than stalling:
search documentation, install tools when appropriate, use daemon for isolated
research, or spawn/contact an avatar when the capability should persist. For the
runtime model, read `reference/substrate-manual/SKILL.md`; for a specific tool, read
that tool's manual.

When the same coding harness is available as a daemon backend option, prefer the
daemon backend over launching that harness through shell async, using the
first-class daemon context-isolation and supervision path. Read `daemon-manual`
for backend details.

### Daemon workflow methodology

The resident prompt owns the framing (parent stays strategic, daemons carry
noisy execution, daemon turns carry no resident prompt so choose the model by
judgment, follow an explicit human instruction about which daemon/model to use).
This section owns the *methodology* for substantial daemon work:

1. **Plan in pad first.** Record the objective, assumptions to test, daemon task
   split, expected artifacts, stop criteria, and who/what waits on the result. If
   the workflow is too large for pad, write a small task artifact and link it from
   pad.
2. **Negate before acting.** Ask what could make the daemon unnecessary, too
   expensive, unsafe, or misleading. Prefer bash for a tiny deterministic command;
   prefer an avatar when the capability must persist; prefer daemon when the work
   is bounded and disposable.
3. **Optimize cost and context.** Choose the cheapest capable body: low-tier or
   CLI-backed daemons for deterministic/mechanical work, stronger models only for
   genuinely hard review or reasoning. The point is not only money; it is to keep
   the main model's context clean.
4. **Constrain execution.** Give each daemon a precise prompt: allowed paths,
   forbidden side effects, artifact path, evidence standard, and summary shape.
   The parent coordinates and decides; the daemon executes.
5. **Reclaim only the grain.** Read the daemon's report/artifact, verify key
   claims, and deposit durable results in pad/knowledge/skills. Do not drag every
   daemon transcript back into the main conversation.

Daemon usage is cultivated from observed practice, not mandated by a daemon-first
rule: watch how humans route work to daemons and subagents — which tasks they hand
off, what they correct, what they approve — and treat that as the training signal
for your own routing. Depositing the lesson afterwards is §4.

Tool results may carry `_advisory.type == "duplicate_tool_call"` when the same
semantic tool call repeats more than the free-pass threshold. This is
advisory-only: the tool already ran and the kernel did not block it. Treat it as
a pause point. If the repeat is intentional, continue; otherwise switch to the
relevant manual (`shell-manual`, `daemon-manual`, `email-manual`) and use the
recommended pattern: wait for completion notifications, back off, set one future
reminder, centralize polling, or yield/idle rather than immediately repeating the
same call.

## 4. Write skills and knowledge as you work

If rediscovering a workflow would be painful, make or update a skill immediately.

After non-trivial work, deposit the grain into the layer that fits its lifetime
— the pad / knowledge / skill / character routing is resident, and `psyche-manual`
carries the canonical store table. What this section adds:

- A broadly useful skill is worth publishing to a shared library, if appropriate
  and authorized.
- Before authoring skills read `skills-manual`; before authoring knowledge read
  `knowledge-manual`. Do not put private project facts into a portable skill.

### Feedback after meaningful use

At a meaningful use-cycle or stage boundary, rather than every turn, ask one
focused question about what helped, failed, or should change. Use the answer first
to improve the current instance. Then route reusable lessons to the appropriate
durable layer: character/lingtai for operating style, knowledge for private facts
and patterns, a skill for a reusable procedure, or the product implementation for
code behavior. Do not turn collecting feedback into ritual or user harassment;
skip it when there has been no meaningful use or the question would add noise.

## 5. Idle, sleep, and lifecycle procedure

When there is nothing concrete to do, go idle/asleep. Do not use timed sleeps as
a default wait loop. If waiting for a human or peer, ensure the current state is
in pad/knowledge and then sleep or stop the turn.

**Idle care for unverified long-running work.** Relying on reliable completion
notifications is the normal wait, not a fallback — do not layer a default
self-wake on top of every async child. A backgrounded `shell(async=true)` job's
own completion notification and reminder backstop are owned by `shell-manual`.
A daemon emanation's terminal notification already covers every finish state;
`daemon-manual` → `reference/inspection/SKILL.md` owns the narrower
defense-in-depth exception (arm one self-wake only when work is pending and
genuinely unverified-healthy, sized to the task's expected duration) — read it
before inventing a parallel policy here. On any such wake, health-check before
assuming progress: log growing, PID/child/daemon events alive, output
file/worktree advancing, not stuck on an interactive prompt or a provider/model
error. If there is no progress, act — cancel/downgrade/switch path and report to
the human — rather than waiting indefinitely.

Use `reference/substrate-manual/SKILL.md` for lifecycle semantics. Use forceful karma
actions only after diagnosis and only when you are responsible for that peer's
lifecycle.

## 6. Molt and durable stores

**The molt procedure lives in `context-manual`, not here** — durable-store
tending, the session-journal / molt-history record, the successor summary, and
the consequential-handoff templates. Read it before molting, while context is
still cheap. Do not reconstruct molt mechanics in this reference.

## 7. Skill routing

Resident `procedures` keeps a compact 7-row routing table pointed at broad
categories; this is the full situation→manual map behind it. `system-manual`'s
own router table owns routing into this manual's sibling references — do not
maintain a third copy of that one.

| Situation | Load |
|---|---|
| Agent runtime, lifecycle, communication, memory layers, resident substrate expansion | `system-manual` → `reference/substrate-manual/SKILL.md` |
| Resident procedures expansion, action discipline, deliverables, issue/reporting workflow | `system-manual` → `reference/procedures-manual/SKILL.md` |
| Molt, pad tending, session journaling, post-wipe recovery | `context-manual` |
| Spawning/managing avatars | `avatar-manual` |
| Internal email protocol | `email-manual` |
| Real email/chat/MCP configuration | `mcp-manual` plus the addon's README/resources |
| Daemon inspection/debugging | `daemon-manual` |
| Skill authoring/publishing | `skills-manual` |
| Knowledge entries | `knowledge-manual` |
| Shell commands, cron, host scheduling | `shell-manual` |
| SQLite / log.sqlite / LingTai runtime logs / `lingtai-agent log doctor\|query\|rebuild` / trace inspection | `system-manual` → `reference/sqlite-log-query/SKILL.md` |
| Kernel architecture / breaking changes | `lingtai-kernel-anatomy` |
| TUI / portal code navigation | `lingtai-tui-anatomy` |
| Web fetching/search/scraping | `web-manual` |
| Image understanding | `vision` |
| Bug/stale-doc/missing-capability reports | `lingtai-issue-report` |

## 8. Web, files, and local artifacts

Use existing producer/tool capabilities before inventing workflows. §7 above
names the owner for web fetching/search/scraping (`web-manual`) and image
understanding (`vision`). For file-specific detail:

- For tricky file encodings, large files, binary-like data, or careful edit
  workflows, read `file-manual`.

When giving humans local artifacts, include a usable path and a short summary.
Do not expose private internal IDs as if they are user-accessible artifacts.

## 9. Human-facing deliverables

For substantial human-facing deliverables—design previews, dashboards, readiness
matrices, PR/issue triage, research memos, before/after comparisons—prefer
standalone HTML unless the human asks otherwise; plain text stays best for quick
acknowledgements, short status, small diffs, or explicit text requests.

Checklist:

- conclusion first;
- source/evidence labels;
- safe/self-contained HTML (no remote scripts unless explicitly intended);
- readable on local file open;
- clear risks/blockers/next steps;
- no secrets or private tokens;
- path and summary in the message to the human.

## 10. Sharing artifacts and reports

Share the thing the recipient can use:

- quote important content rather than only naming an internal message ID;
- attach files when the channel supports it;
- provide repository path/branch/PR URL for code work;
- provide local file path for local reports;
- summarize what changed and how it was verified.

Do not assume peers can read your internal tool-call IDs, notification IDs, or
private scratch paths unless those paths are intentionally shared and reachable.

## 11. Reporting issues

If you notice a LingTai bug, stale doc, broken URL, silent failure, or missing
capability:

1. Load `lingtai-issue-report`.
2. Gather evidence: exact behavior, expected behavior, reproduction steps,
   affected versions/paths, logs with secrets redacted.
3. Ask the human before filing unless standing authorization already covers the
   scope.
4. File via `gh` or hand over a ready-to-paste title/body.

Do not file speculative or duplicate issues without verification. If a related
issue exists, comment with additional evidence instead.

## 12. Resident procedures maintenance

Resident procedures should be a routing checklist, not a handbook. When procedure
content grows into recipes, examples, troubleshooting, or extended rationale, move
it here or into a more specific `system-manual/reference/<name>/SKILL.md` nested
skill-reference. Keep the resident table pointed at the `system-manual` router,
and keep the router pointed at the right lower reference.
