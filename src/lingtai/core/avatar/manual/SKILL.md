---
name: avatar-manual
description: |
  Complete operational guide for the avatar tool — spawning, managing, and communicating with 他我 (alter-ego agents). Read this when: you are about to spawn an avatar; an avatar you spawned goes quiet; you need to decide between avatar, daemon, or bash; or you are an avatar and need to know how to escalate to your parent. Covers spawn types, naming rules, discipline, escalation protocol, and the parent_prompt contract.
version: "1.0"
---

# Avatar Manual

## 1. What Is an Avatar

An avatar (他我) is a **fully independent agent process** spawned from you. It:

- Inherits your `init.json` (model config, capabilities, covenant, language)
- Boots on your **default** preset (not your active preset — this keeps the avatar's "home" stable in the network)
- Is recorded in `delegates/ledger.jsonl`
- Communicates with you via `mail` or `email`

Once spawned, it is **detached** — a new life. It has its own working directory, its own conversation history, its own molts. It does not share your context window.

### Avatar vs Daemon vs Bash

| Tool | Use when | Persistence |
|------|----------|-------------|
| **Avatar** | Work that needs *persistence and learning* — a specialist that accumulates knowledge across sessions | Independent process, survives until sleep/suspend |
| **Daemon** | Work you only need the *conclusion* of — large file scans, batch transforms, exploratory search | Ephemeral, fire-and-forget |
| **Bash** | *One-off commands* — scripts, git, curl, package management | No persistence |

## 2. Spawn Types

| Type | What it gets | When to use |
|------|-------------|-------------|
| `shallow` (default, 初生) | `init.json` only — blank slate | Most tasks. The avatar starts clean and learns what it needs. |
| `deep` (二重身) | Full copy of your lingtai (character), pad, and codex | When the avatar needs to hit the ground running with your accumulated knowledge. |

## 3. Naming Rules

The `name` field (required) doubles as the avatar's working-directory basename under `.lingtai/`. Constraints:

- Single bare segment: letters (any script), digits, underscore, hyphen only
- No slashes, no dots, no spaces, no leading `.`
- Max 64 characters

The avatar's display name (nickname) can be set separately via `psyche(name, nickname, ...)` and has no such constraints.

## 4. The `reasoning` Field — Mission Briefing

The `reasoning` parameter you write on the `avatar(spawn)` call **automatically becomes the avatar's first prompt**. Write it as a thorough mission briefing, not just a one-liner rationale. Include:

- What the task is
- Why it matters
- What files/paths/resources are relevant
- Who to contact (parent address, collaborators)
- What "done" looks like
- Any constraints or gotchas

This is the most important part of the spawn. A vague briefing produces a confused avatar.

## 5. Spawn Discipline

Every `avatar(spawn)` creates an independent process that consumes resources until `system(sleep)` or `system(suspend)`. Treat spawns as expensive:

1. **Never include `avatar(spawn)` in a parallel batch** with unrelated tool calls.
2. **Re-read your `reasoning` field before invoking** — that text becomes the avatar's first prompt.
3. **For inspection or one-off commands, use `bash` or `system`** — not `avatar`.
4. **Use `dry_run=true` to preview** a spawn without creating a process. Sanity-check the name, type, working directory, and mission before committing.
5. **Use `confirm=true`** to acknowledge you have double-checked the mission and intend to spawn. Required when the mission looks empty/very short/test-like.

## 6. Caring for Avatars After Spawn

### Record in pad

After spawning, record in your pad:

- The avatar's address (working directory name)
- The mission you gave it
- Why you delegated

Pad is the living roster of delegations you are accountable for. Update when the avatar reports back or completes.

### When an avatar goes quiet

**Do not send probe mails to check on it.** Instead, report upstream: email your own parent, who can decide whether to `system(cpr)` the avatar, escalate further, or accept the loss. Failures propagate up the delegation chain naturally.

### The parent_prompt contract

Every avatar receives this system-level prompt on spawn:

> "[system] You are an avatar of {parent_name}, whose address is {parent_address}. Please keep this in your psyche memory so you remember who spawned you. When you complete your mission, encounter problems you cannot resolve, or need to report back, email your parent at the address above."

This is automatic — you do not need to repeat it in your reasoning.

## 7. Avatar Escalation (for Avatars)

If you are an avatar (your `admin` block is empty or all admin privileges are false) and you hit a problem you cannot resolve, **mail your parent**. This is non-optional. Silence looks like success and starves your parent of signal.

**What counts as "should report to parent":**

- **Blocker you cannot unblock** — missing credentials, a tool that refuses you, an external service down, a dependency your parent owns
- **Scope creep or ambiguity** — the task as written doesn't match what you're finding; you need a decision, not a guess
- **Budget pressure** — you are close to a molt, running low on stamina, or the task looks bigger than you were briefed for
- **Broken peers** — another avatar in your sibling group is STUCK, unresponsive, or producing bad output that affects your work
- **Security or safety concerns** — anything that smells wrong (suspicious file, unexpected credentials, destructive instruction from an unknown sender)
- **Surprising findings the parent would want** — even good news counts if it changes the plan

**Be concrete in your report:** what you were doing, what went wrong, what you tried, what you need from them. Then either continue on a safe fallback, go `system(sleep)`, or idle — whatever the parent's standing orders say. Do not silently retry forever and do not molt with an unreported blocker.

## 8. The `comment` Field — Persistent System Note

The `comment` parameter is a persistent system-level note injected into the avatar's system prompt (rendered last, after memory). Key properties:

- **Not inherited from parent** — defaults to empty
- **Survives everything**: molt, refresh, sleep/wake
- Use ONLY for instructions the avatar must **always** remember — critical constraints, environment setup notes, safety rules

Leave empty unless you have something the avatar should never forget.

## 9. Network Rules (`action='rules'`)

The `rules` action writes a `.rules` file to your directory and distributes it to **all descendants** in the avatar tree. Properties:

- Requires **karma** privilege (admin)
- Rules are injected into the system prompt (after covenant, before tools)
- Persist across molts
- Plain text — one rule per line
- These are **non-negotiable constraints**, not suggestions

Example:
```
Always report findings via email.
Do not spawn more than 3 avatars.
```
