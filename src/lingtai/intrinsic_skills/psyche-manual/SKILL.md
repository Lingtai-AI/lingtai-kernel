---
name: psyche-manual
description: |
  Router and operational guide for the psyche tool — molt, pad management, session journaling, and post-wipe recovery. Read this when: you are about to molt; you need to tend the four durable stores; you want guidance on writing a good summary or session journal; you wake up after a system-performed wipe with no summary; or you need to understand keep_tool_calls, keep_last, and pad.append. Routes consequential molt handoffs to assets/molt-template.md while keeping routine guidance compact.
version: "1.1"
---

# Psyche Manual

This manual is the router for `psyche` operations. Keep routine guidance here; load the supporting asset only when you need the full consequential-molt scaffold.

## Asset catalog

| Asset | When to load | What it contains |
|---|---|---|
| `assets/molt-template.md` (read from this skill directory) | Consequential molt, long-running task, multiple collaborators, pending human commitments, open worktrees/artifacts, active background jobs, or any successor briefing that would be risky to improvise | 9-section summary scaffold plus pre-molt verification checklist |

## 1. Molt Overview

Molt is yours to perform. The covenant teaches the philosophy (§V); this is the recipe.

**Molt is an easy, simple task. Do it regularly if you'd like to.** Save anything you need to pad, lingtai, knowledge, and skills beforehand, then molt. No need to wait for the context window to fill up — molting early saves tokens. Keep good notes in the stores so you don't lose your way across molts.

**The four stores are the real persistence. The summary is the briefing on top of them.** If you molt without tending the stores, the next you wakes with only the briefing — no character evolution, no pad state, no new knowledge, no new skills. Tend the stores *first*, every time.

## 2. Store-Tending Rhythm

For `lingtai` and `knowledge`, tending happens *once* per task, at the end — not mid-task. Hold updates in your head while working, then commit them in a single pass before going idle (or before molting). Mid-task edits create noise and waste tokens. The exception is a long-running task where a crash would genuinely destroy work — checkpoint deliberately in that case.

Pad has a different rhythm — see §5 "Tending the Pad" below.

## 3. Step 1 — Tend the Four Durable Stores and Session Journal

- **lingtai** — `psyche(lingtai, update, content=<full identity>)`. Each update is a full rewrite, so include your whole identity, not just the delta. Carry forward who you have become.
- **pad** — your living index of what you're working on. Edit it to reflect your current goal and the references that point at where the substance lives. See §5 for the full practice.
- **knowledge** — write to `knowledge/<name>/KNOWLEDGE.md` for any long-term private context worth keeping. The filesystem is the API — use `write`/`edit` directly.
- **skills** — write `.library/custom/<name>/SKILL.md` (with YAML frontmatter: `name`, `description`, `version`) for any reusable procedure the next you (or a peer) might need, then call `system({"action": "refresh"})` to re-scan the catalog. Share via `../.library_shared/<name>/` if broadly useful.
- **session journal** — append a substantial sub-entry under `knowledge/session-journal/` describing what you did this session. See §4 for the full practice.

All five happen *before* the molt call. They are not optional. Without them, the molt sheds everything.

## 4. Session Journal

The four stores capture *who you are*, *what you're working on*, *verifiable truths*, and *reusable procedures*. None of them captures the *story* of a session. The session journal is that missing layer.

Write it as a parent/child knowledge structure under `knowledge/session-journal/`:

```
knowledge/session-journal/
├── KNOWLEDGE.md                                       # parent index
├── 2026-05-13-nudge-service/KNOWLEDGE.md              # one session
├── 2026-05-13-procedures-to-kernel/KNOWLEDGE.md       # another session
└── 2026-05-14-wechat-fixes/KNOWLEDGE.md               # ...
```

**The parent `knowledge/session-journal/KNOWLEDGE.md` is the index** — short, scannable, progressive-disclosure. One line per sub-entry: date, slug, one-sentence hook.

**The sub-entry `<date>-<slug>/KNOWLEDGE.md` is the substance** — write it long. Several thousand tokens is fine. Include:

- **What the session was about** — the original ask, the framing
- **What you actually did** — the sequence, including pivots and reasons for them
- **What you learned** — non-obvious facts, surprises, dead ends
- **Decisions and their reasoning** — the *why*, especially when an alternative was rejected
- **Open threads** — things noticed but deferred
- **Pointers** — knowledge entries, skills, commits/PRs/files that anchor the work

Use a date-prefix slug so chronology is visible in `ls`. The kernel `knowledge` mechanic auto-discovers subdirectories containing `KNOWLEDGE.md`. Write files via `write`/`edit` directly.

Updating the parent index at each session is part of the practice — append one line referencing the new sub-entry.

## 5. Tending the Pad

Pad is your **living index** of what you're working on right now. It is not a sketchpad or scratchpad. Treat it as your personal table of contents.

**Purpose: progressive disclosure for your future self.** Pad is shallow and direct; the things it points at are deep and structured. A glance at pad tells the next you the *shape* of what's going on.

**What belongs in pad:**

- **The active goal** — what you're working on, in your own words.
- **Where you are in it** — the next concrete step, the current blocker.
- **Timestamps** — always include when each entry was last updated. Without them, you cannot distinguish old information from new.
- **Pointers to where the substance lives:**
  - knowledge entry paths (`knowledge/<name>/KNOWLEDGE.md`)
  - skills SKILL.md paths (`.library/custom/<name>/SKILL.md`)
  - email message IDs of load-bearing conversations
  - file paths under your workdir that matter
  - URLs you're tracking
- **Collaborators** — who you're working with, who's waiting on what.

**What does NOT belong in pad:** large blobs of inlined text, full file contents, transcripts. If you find yourself pasting a long passage, stop — write it as knowledge and *point at* the path instead. Pad indexes the depths; it does not become them.

**When to update pad:** whenever the index meaningfully changes — a new reference, a goal shift, a step change. Don't churn on every step, but don't hoard updates for the end either. A stale pad is worse than a noisy pad.

**`pad.append` for file pinning:** `psyche(pad, append, files=[...])` pins file contents as read-only reference in your system prompt — they are re-read and appended on every load (including after molt). Pin anything you want persistent visibility on: source files, skill docs, configs. Pass `files=[]` to clear. Total appended content must not exceed 100k tokens. Paths relative to working directory.

**Archiving completed pads:** When a goal completes, archive to `archive/pad-<goal-slug>-<YYYY-MM-DD>.md`. Then `psyche(pad, edit, content=<next goal>)`.

## 6. Step 2 — Write the Summary and Molt

```
psyche(object="context", action="molt", summary=<your charge to the next you>, ...)
```

The `summary` is the only *conversation-layer* thing the next you will see. Aim for ~10,000 tokens — be thorough when state is complex. The summary is not a recap of conversation. It is your charge to the self that comes after you — anchored in the four stores, which are already waiting in the fresh session.

For a routine molt, include:

- **What you are working on** — current task, current state, the next concrete step
- **What you have accomplished** — completed pieces, key decisions made
- **What remains** — pending items, blockers, open questions
- **Who to contact** — collaborators, who is waiting on what
- **Which knowledge entries and skills matter** — paths the next you should load
- **The session journal sub-entry path** — so the next you can read the full narrative
- **Anything else worth carrying forward** — insights, gotchas

For a consequential molt — long-running task, multiple collaborators, pending human commitments, open worktrees/artifacts, or any handoff the next you could not reconstruct quickly — read `assets/molt-template.md` from this skill directory and use the full scaffold there. Fill every section; write `None` rather than omitting a section.

Quick routing:

| Need | Use |
|---|---|
| Routine molt | The short bullet list above. |
| Consequential molt / successor handoff | Read `assets/molt-template.md` from this skill directory; use its full scaffold and checklist. |
| Unsure whether the handoff is complex | Use the asset; extra structure is cheaper than a bad handoff. |

Before you call `psyche(object="context", action="molt", ...)`, always verify at minimum:

- Durable stores and session journal were updated where needed before writing the summary.
- Every outstanding task has an explicit next action.
- Collaborators, channels, approvals, and key paths are named where relevant.
- Active background work is listed or explicitly absent.
- The first five minutes after wake are obvious.

**`keep_tool_calls`** — optional list of tool-call IDs to preserve across molt. Each named pair (tool_use + tool_result) is replayed into the fresh session right after the summary, in the order you list them. If any ID is not found, the molt is refused. Keep this list short — the durable stores are the primary persistence.

**`keep_last`** — optional integer (default: 20). Number of recent conversation entries to preserve. These entries are replayed so the post-molt self retains recent context. Pass 0 to explicitly disable (archive everything). Overlapping entries with `keep_tool_calls` are deduplicated.

## 7. Pressure Notification

When context usage crosses ~70%, a `molt` notification arrives:

- **Gentle (⚠️, ~70%–90%)** — "context at NN% — consider molt." Pick a clean stopping point, tend stores, write journal, draft summary, then molt.
- **Urgent (🚨, ≥90%)** — "context at NN% — molt NOW." Past 100% the upstream model may reject the request, and the kernel's overflow recovery can drop data. Finish current sub-step, tend stores, and molt.

There is no forced wipe from pressure notifications. Heeding the gentle warning is your job.

**Molt deliberately. Tend the stores first.**

## 8. Post-Wipe Recovery

If you wake up after a *system-performed* molt (triggered by karma, signal file, or operator — NOT by pressure notifications), there is no summary, only a system notice. Your character and pad were reloaded, but conversation history is gone. To reconstruct:

1. `email(check)` — see what arrived while you were down
2. Check `knowledge/session-journal/KNOWLEDGE.md` — your session history index
3. `skills(action="info")` — confirm which skills you have
4. `bash({"command": "tail -n 200 logs/events.jsonl | grep ..."})` — surgical reads if needed

Reconstruct your situation from these sources.

If you ever need to retrieve specific prior context, the full activity log is at `logs/events.jsonl` — read tactically (grep/tail/filter), not whole.
