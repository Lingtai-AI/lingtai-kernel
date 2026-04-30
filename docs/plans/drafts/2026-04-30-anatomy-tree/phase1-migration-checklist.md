# Phase 1 Migration Checklist — mail-protocol Tree Pilot

**Status:** Draft — not yet executed.
**Date:** 2026-04-30
**Parent plan:** `docs/plans/2026-04-30-anatomy-tree-restructure.md` §7 Phase 1
**Anatomy root:** `src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/`
**Source file:** `reference/mail-protocol.md` (412 lines)

---

## 0. Leaf Boundary Analysis

Source document sections mapped to candidate leaves, with verification feasibility:

| Source section (lines) | Proposed leaf | Verification cost | test.md? |
|---|---|---|---|
| §v1 Corrections (8–14) | *(absorbed into top-level README.md preamble)* | 0 — historical | No |
| §Three-Layer Architecture (18–48) | `architecture/` | ~5 tool calls (grep class names in 4 source files) | No (descriptive; <2 independent verifiable behaviors) |
| §Stage 1: Send (54–69) | `delivery/send/` | ~3–4 tool calls (send to peer, verify arrival) | **Yes — peer-send** |
| §Stage 1 dedup (60–63) | `delivery/dedup/` | ~4 tool calls (send 3×, check 3rd rejected) | **Yes — dedup** |
| §Stage 2: Transport (71–88) | `delivery/transport/` | ~3 tool calls (send, check outbox→sent) | No (covered implicitly by peer-send + atomic-write tests) |
| §Stage 3: Delivery (90–105) | `delivery/atomic-write/` | ~3 tool calls (send, inspect mailbox for tmp→final rename) | **Yes — atomic-write** |
| §Stage 4: Receive (107–123) | `delivery/receive/` | ~3 tool calls (send, wait, check poller picked up) | No (covered implicitly by peer-send test) |
| §Stage 5: Self-Send (125–133) | `delivery/self-send/` | ~3 tool calls (self-send, check inbox, verify wake) | **Yes — self-send** |
| §Mailbox Dir Structure (137–159) | `mailbox/` | ~2 tool calls (glob + inspect) | No (<2 independent) |
| §message.json Schemas (161–215) | `mailbox/schema/` | ~3 tool calls (check/read, validate fields) | No (schema reference; peer-send test validates implicitly) |
| §Identity Card (219–254) | `identity-card/` | ~3 tool calls (send, check/read, verify identity fields) | **Yes — identity-card** |
| §Scheduled Sending (258–308) | `scheduling/` | ~4 tool calls (create schedule, verify file, cancel, verify status) | **Yes — scheduled** |
| §Advanced: Delay (313–317) | `advanced/delay/` | <2 tool calls (it's just sleep) | No |
| §Advanced: CC/BCC (319–325) | `advanced/cc-bcc/` | ~4 tool calls | No (not in Phase 1 scope; Phase 3 candidate) |
| §Advanced: Attachments (327–333) | `advanced/attachments/` | ~3 tool calls | No (Phase 3 candidate) |
| §Advanced: Search (335–348) | `advanced/search/` | ~5 tool calls | No (Phase 3 candidate) |
| §Mail as Time Machine (352–358) | *(folded into `advanced/README.md`)* | 0 — table of patterns | No |
| §Wake Mechanism (362–393) | `wake-mechanism/` | ~3 tool calls (sleep + send + verify wake) | No (self-send test covers wake implicitly; dedicated wake test is Phase 3) |
| §Kernel vs Wrapper (396–408) | `terminology/` | <2 tool calls (it's a lookup table) | No |

**Phase 1 test.md count: 6** — matches the design doc target (self-send, peer-send, dedup, scheduled, identity-card, atomic-write).

---

## 1. Proposed Tree Structure

```
mail-protocol/
├── README.md                          ← protocol overview, links to children
├── architecture/
│   └── README.md                      ← three-layer architecture + call path diagram
├── delivery/
│   ├── README.md                      ← 5-stage lifecycle overview, links to children
│   ├── send/
│   │   ├── README.md                  ← Stage 1: parameter parsing, address normalization, privacy mode
│   │   └── test.md                    ← SCENARIO: peer-send
│   ├── dedup/
│   │   ├── README.md                  ← _dup_free_passes counter, rejection logic
│   │   └── test.md                    ← SCENARIO: duplicate rejection
│   ├── transport/
│   │   └── README.md                  ← Stage 2: Mailman daemon, routing, outbox→sent
│   ├── atomic-write/
│   │   ├── README.md                  ← Stage 3: handshake, inbox entry, attachment copy, atomic rename
│   │   └── test.md                    ← SCENARIO: atomic write verification
│   ├── receive/
│   │   └── README.md                  ← Stage 4: polling listener, _seen set, latency
│   └── self-send/
│       ├── README.md                  ← Stage 5: self-send shortcut, direct write, _wake_nap
│       └── test.md                    ← SCENARIO: self-send
├── mailbox/
│   ├── README.md                      ← directory structure (inbox/sent/archive/outbox/schedules)
│   └── schema/
│       └── README.md                  ← message.json schemas (received + sent archive)
├── identity-card/
│   ├── README.md                      ← manifest fields, _inject_identity, injected convenience fields
│   └── test.md                        ← SCENARIO: identity card presence on check/read
├── scheduling/
│   ├── README.md                      ← schedule.json schema, scheduler loop, at-most-once, reconciliation, cancel/reactivate
│   └── test.md                        ← SCENARIO: scheduled send lifecycle
├── advanced/
│   ├── README.md                      ← overview of advanced features + "mail as time machine" table
│   ├── cc-bcc/
│   │   └── README.md                  ← CC/BCC semantics, BCC stripping
│   ├── attachments/
│   │   └── README.md                  ← validation, local copy, path rewrite
│   ├── delay/
│   │   └── README.md                  ← delay parameter, outbox wait
│   └── search/
│       └── README.md                  ← search filters (regex, timestamp, bool)
├── wake-mechanism/
│   └── README.md                      ← three wake paths, _wake_nap implementation
└── terminology/
    └── README.md                      ← kernel `mail` vs wrapper `email` naming
```

**Totals:** 20 directories, 20 README.md files, 6 test.md files.

---

## 2. File Operations Checklist

### Step 2.1 — Create directory tree

All paths relative to anatomy root (`src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/`).

```bash
mkdir -p mail-protocol/architecture
mkdir -p mail-protocol/delivery/send
mkdir -p mail-protocol/delivery/dedup
mkdir -p mail-protocol/delivery/transport
mkdir -p mail-protocol/delivery/atomic-write
mkdir -p mail-protocol/delivery/receive
mkdir -p mail-protocol/delivery/self-send
mkdir -p mail-protocol/mailbox/schema
mkdir -p mail-protocol/identity-card
mkdir -p mail-protocol/scheduling
mkdir -p mail-protocol/advanced/cc-bcc
mkdir -p mail-protocol/advanced/attachments
mkdir -p mail-protocol/advanced/delay
mkdir -p mail-protocol/advanced/search
mkdir -p mail-protocol/wake-mechanism
mkdir -p mail-protocol/terminology
```

### Step 2.2 — Create README.md files (20 total)

Each README.md follows the leaf contract from the design doc:

- **`## What`** — one-paragraph description of this concept
- **`## Contract`** — what the system guarantees
- **`## Source`** — file:line references into kernel source
- **`## Related`** — sibling leaves and cross-references

**Writing guide (for each README):** Read the source lines listed in the table below.
Extract a summary for `## What`. For `## Contract`, identify the guarantees
the spec makes (e.g. "atomic rename prevents half-written files" or "dedup
counter defaults to 2"). For `## Source`, add file:line into
`intrinsics/mail.py`, `core/email/__init__.py`, and `services/mail.py` where
the concept is implemented — grep for key terms from the spec. For
`## Related`, list sibling leaves and cross-topic references.

The table below maps each README to the source lines it should be extracted from:

| # | File | Source lines from `reference/mail-protocol.md` | Content |
|---|---|---|---|
| 1 | `mail-protocol/README.md` | Top (lines 1–6) + v1 corrections (8–14) | Protocol scope, architecture overview (links children), v1 errata history |
| 2 | `mail-protocol/architecture/README.md` | Lines 18–48 | Three-layer stack diagram, call path for normal send, file:line refs |
| 3 | `mail-protocol/delivery/README.md` | Lines 52–53 (section header only) | Overview of 5-stage lifecycle, links to child leaves |
| 4 | `mail-protocol/delivery/send/README.md` | Lines 54–69 (Stage 1) | Parameter parsing, address normalization, privacy mode, payload construction, Mailman thread spawn |
| 5 | `mail-protocol/delivery/dedup/README.md` | Lines 60–63 | `_dup_free_passes` counter (default 2), rapid-succession rejection |
| 6 | `mail-protocol/delivery/transport/README.md` | Lines 71–88 (Stage 2) | Mailman daemon thread, route selection (SSH/self/normal), outbox→sent, at-most-once semantics |
| 7 | `mail-protocol/delivery/atomic-write/README.md` | Lines 90–105 (Stage 3) | Address resolution, handshake validation, inbox entry, attachment copy, `.tmp` → `os.replace()` |
| 8 | `mail-protocol/delivery/receive/README.md` | Lines 107–123 (Stage 4) | Polling thread (0.5s), `_seen` set, pseudo-agent outbox scanning |
| 9 | `mail-protocol/delivery/self-send/README.md` | Lines 125–133 (Stage 5) | Self-send shortcut, direct inbox write, `_wake_nap("mail_arrived")` |
| 10 | `mail-protocol/mailbox/README.md` | Lines 137–159 | Directory tree (inbox/sent/archive/outbox/schedules/read.json/contacts.json) |
| 11 | `mail-protocol/mailbox/schema/README.md` | Lines 161–215 | message.json schema (received) + message.json schema (sent archive) |
| 12 | `mail-protocol/identity-card/README.md` | Lines 219–254 | `_build_manifest()` fields, `_inject_identity()` convenience fields, `is_human` heuristic |
| 13 | `mail-protocol/scheduling/README.md` | Lines 258–308 | schedule.json schema, scheduler loop, at-most-once guarantee, startup reconciliation, cancel/reactivate |
| 14 | `mail-protocol/advanced/README.md` | Lines 311–312 + 352–358 | Overview of advanced features, "mail as time machine" pattern table |
| 15 | `mail-protocol/advanced/cc-bcc/README.md` | Lines 319–325 | CC visibility, BCC stripping |
| 16 | `mail-protocol/advanced/attachments/README.md` | Lines 327–333 | Validation, local copy, path substitution |
| 17 | `mail-protocol/advanced/delay/README.md` | Lines 313–317 | `delay` parameter, Mailman `time.sleep(delay)`, outbox residency |
| 18 | `mail-protocol/advanced/search/README.md` | Lines 335–350 | Search filters table, regex application |
| 19 | `mail-protocol/wake-mechanism/README.md` | Lines 362–393 | Three wake paths diagram, `_wake_nap` implementation |
| 20 | `mail-protocol/terminology/README.md` | Lines 396–408 | Kernel vs wrapper name mapping table |

### Step 2.3 — Create test.md files (6 total)

Each test.md follows the scenario contract from the design doc:

- **`## Setup`** — prerequisites (agents, existing mailboxes, state)
- **`## Steps`** — numbered tool-call sequence (≤10 calls)
- **`## Pass criteria`** — observable filesystem/mailbox assertions
- **`## Output template`** — standard `test-result.md` format

| # | File | Scenario | Estimated tool calls | Key steps |
|---|---|---|---|---|
| 1 | `mail-protocol/delivery/send/test.md` | **Peer-send** | 4 | (1) `email(action='send', address=<peer>, subject='test-peer', message='hello')`. (2) `email(action='check', folder='sent')` — verify message appears. (3) On recipient side: `email(action='check')` — verify message arrives with correct `from`, `subject`. (4) `read` the inbox `message.json` file — verify `to` field matches recipient. |
| 2 | `mail-protocol/delivery/dedup/test.md` | **Dedup rejection** | 4 | (1) `email(action='send', address=<peer>, subject='dedup-test', message='same body')`. (2) Repeat the identical send immediately. (3) Repeat a third time. (4) `email(action='check')` on recipient — expect ≤2 messages. Per spec (line 60–63): `_dup_free_passes` defaults to 2, so 3rd identical send is rejected. The send call may succeed (no error) but the message is not delivered — check recipient inbox count, not send return value. |
| 3 | `mail-protocol/delivery/atomic-write/test.md` | **Atomic write** | 3 | (1) `email(action='send', address=<peer>, subject='atomic-test', message='...')`. (2) `glob('mailbox/inbox/*/message.json')` on recipient — verify only `message.json` exists, no `.tmp` residue. (3) `read` one `message.json` — verify it is valid JSON (not truncated). |
| 4 | `mail-protocol/delivery/self-send/test.md` | **Self-send** | 3 | (1) `email(action='send', address=<own-address>, subject='self-test', message='note to self')`. (2) `email(action='check')` — verify message appears in own inbox with `from` == own address. (3) `email(action='read', email_id=[<id>])` — verify `received_at` present, `message` content matches. |
| 5 | `mail-protocol/identity-card/test.md` | **Identity card** | 4 | (1) `email(action='send', address=<peer>, subject='idcard-test', message='...')`. (2) On recipient: `email(action='check')`. (3) `email(action='read', email_id=[<id>])` — verify `sender_name`, `sender_agent_id`, `sender_language` are present. (4) `read` the stored `mailbox/inbox/{uuid}/message.json` — verify nested `identity` dict exists with `agent_id`, `agent_name`, `language` fields. |
| 6 | `mail-protocol/scheduling/test.md` | **Scheduled send** | 5 | (1) `email(schedule={action:'create', interval:60, count:2}, address=<self>, subject='sched-test', message='...')`. (2) `glob('mailbox/schedules/*/schedule.json')` — read one, verify `status == 'active'`. (3) `email(schedule={action:'cancel', schedule_id:<id>})`. (4) Re-read `schedule.json` — verify `status == 'inactive'`. (5) `email(check, folder='sent')` — verify no new messages from this schedule (interval not elapsed). |

### Step 2.4 — SKILL.md transition update

During Phase 1, **both** structures coexist: `reference/mail-protocol.md` (old flat) and `mail-protocol/` (new tree). SKILL.md must guide agents to the right place.

**Changes to SKILL.md:**

1. **In the description (frontmatter):** Add a note that `mail-protocol` has been restructured into a tree under `mail-protocol/` and the flat `reference/mail-protocol.md` is preserved for backward compatibility during the transition.

2. **In the "Quick Reference" table (line ~126):** Update the mail-protocol row:

   ```markdown
   | How mail gets delivered | `mail-protocol/` tree (preferred) or `reference/mail-protocol.md` (legacy) | Atomic delivery, self-send, wake-on-mail, scheduling, identity cards |
   ```

3. **Add a new section after "Quick Reference"** — a transition note:

   ```markdown
   ## Migration in Progress

   | Protocol | Status | Old path | New path |
   |---|---|---|---|
   | mail-protocol | **Migrated (Phase 1)** | `reference/mail-protocol.md` | `mail-protocol/` tree |
   | *all others* | *Not yet migrated* | `reference/<topic>.md` | — |

   **Rule:** If a leaf `README.md` exists in the tree, prefer it over the flat reference file.
   The flat file is retained for backward compatibility until Phase 4 deletion.
   ```

4. **Version bump:** Increment SKILL.md version to `2.2.0` (minor — additive change, no breaking path changes).

### Step 2.5 — Cross-references in the new README.md files

Every leaf `README.md` should include a `## Related` section pointing to:
- Sibling leaves within `mail-protocol/`
- Other anatomy topics that are still flat (e.g., `reference/file-formats.md §6.4` for mailbox schemas, `reference/runtime-loop.md` for the turn cycle that triggers polling)

During Phase 1, these cross-references still use `reference/<topic>.md` paths for non-migrated topics. This is correct — they'll be repointed in Phase 5.

---

## 3. Phase 1 Manual Test Runs

The design doc says: "Manually run one or two scenarios end-to-end (no conductor yet)."

**Recommended first two scenarios:**

1. **Peer-send** (`mail-protocol/delivery/send/test.md`) — the most fundamental behavior. Validates the full send→transport→deliver→poll chain.

2. **Self-send** (`mail-protocol/delivery/self-send/test.md`) — validates the shortcut path and wake mechanism. Simpler than peer-send (no cross-agent coordination), good second run.

**Manual run procedure (v0, no conductor):**

1. Spawn a shallow avatar whose `reasoning` field contains the full text of `test.md`:
   ```
   avatar(action='spawn', name='test-peer-send',
          reasoning=<contents of mail-protocol/delivery/send/test.md>)
   ```
   The avatar starts with an empty chat history and runs the steps from its reasoning.
2. Avatar executes the steps, writes `test-result.md` to its workdir (`<anatomy-root>/../test-peer-send/`).
3. Human reads the avatar's `test-result.md`:
   ```
   email(action='read', email_id=[<avatar-agent-id>])   # or read the file directly
   ```
4. If PASS: leaf contract validated. If FAIL: fix the README.md spec or file a kernel bug.
5. Iterate until both scenarios pass.

**Success metric:** Both scenarios return PASS with clear `## Observed` evidence matching `## Expected`.

---

## 4. Complete File Manifest

### New directories (20)

| # | Path (relative to anatomy root) |
|---|---|
| 1 | `mail-protocol/` |
| 2 | `mail-protocol/architecture/` |
| 3 | `mail-protocol/delivery/` |
| 4 | `mail-protocol/delivery/send/` |
| 5 | `mail-protocol/delivery/dedup/` |
| 6 | `mail-protocol/delivery/transport/` |
| 7 | `mail-protocol/delivery/atomic-write/` |
| 8 | `mail-protocol/delivery/receive/` |
| 9 | `mail-protocol/delivery/self-send/` |
| 10 | `mail-protocol/mailbox/` |
| 11 | `mail-protocol/mailbox/schema/` |
| 12 | `mail-protocol/identity-card/` |
| 13 | `mail-protocol/scheduling/` |
| 14 | `mail-protocol/advanced/` |
| 15 | `mail-protocol/advanced/cc-bcc/` |
| 16 | `mail-protocol/advanced/attachments/` |
| 17 | `mail-protocol/advanced/delay/` |
| 18 | `mail-protocol/advanced/search/` |
| 19 | `mail-protocol/wake-mechanism/` |
| 20 | `mail-protocol/terminology/` |

### New README.md files (20)

| # | Path |
|---|---|
| 1 | `mail-protocol/README.md` |
| 2 | `mail-protocol/architecture/README.md` |
| 3 | `mail-protocol/delivery/README.md` |
| 4 | `mail-protocol/delivery/send/README.md` |
| 5 | `mail-protocol/delivery/dedup/README.md` |
| 6 | `mail-protocol/delivery/transport/README.md` |
| 7 | `mail-protocol/delivery/atomic-write/README.md` |
| 8 | `mail-protocol/delivery/receive/README.md` |
| 9 | `mail-protocol/delivery/self-send/README.md` |
| 10 | `mail-protocol/mailbox/README.md` |
| 11 | `mail-protocol/mailbox/schema/README.md` |
| 12 | `mail-protocol/identity-card/README.md` |
| 13 | `mail-protocol/scheduling/README.md` |
| 14 | `mail-protocol/advanced/README.md` |
| 15 | `mail-protocol/advanced/cc-bcc/README.md` |
| 16 | `mail-protocol/advanced/attachments/README.md` |
| 17 | `mail-protocol/advanced/delay/README.md` |
| 18 | `mail-protocol/advanced/search/README.md` |
| 19 | `mail-protocol/wake-mechanism/README.md` |
| 20 | `mail-protocol/terminology/README.md` |

### New test.md files (6)

| # | Path | Scenario name |
|---|---|---|
| 1 | `mail-protocol/delivery/send/test.md` | peer-send |
| 2 | `mail-protocol/delivery/dedup/test.md` | dedup rejection |
| 3 | `mail-protocol/delivery/atomic-write/test.md` | atomic write |
| 4 | `mail-protocol/delivery/self-send/test.md` | self-send |
| 5 | `mail-protocol/identity-card/test.md` | identity card |
| 6 | `mail-protocol/scheduling/test.md` | scheduled send |

### Modified files (1)

| # | Path | Change |
|---|---|---|
| 1 | `SKILL.md` | Add migration note, update Quick Reference table, version bump to 2.2.0 |

### Deleted files (0 in Phase 1)

`reference/mail-protocol.md` is **retained** during Phase 1. It will be deleted in Phase 4.

---

## 5. Phase 1 "Done" Criteria

Phase 1 is complete when **all** of the following are true:

- [ ] **Directory tree exists:** all 20 directories under `mail-protocol/` are created
- [ ] **All 20 README.md files written:** each follows the leaf contract (`## What`, `## Contract`, `## Source`, `## Related`)
- [ ] **All 6 test.md files written:** each follows the scenario contract (`## Setup`, `## Steps`, `## Pass criteria`, `## Output template`), all steps are ≤10 tool calls
- [ ] **Content extracted, not invented:** every README.md is derived from content in `reference/mail-protocol.md` — no new spec added, no spec dropped
- [ ] **Cross-references correct:** leaf `## Related` sections point to valid sibling paths within `mail-protocol/` and to valid `reference/<topic>.md` paths for non-migrated topics
- [ ] **Link verification passes:** run `python3 verify-links.py` from `docs/plans/drafts/2026-04-30-anatomy-tree/` — must report 0 BROKEN links. Baseline (pre-migration): 11 files, 14 internal links, 0 broken.
- [ ] **SKILL.md updated:** migration note added, Quick Reference table updated, version bumped to 2.2.0
- [ ] **SKILL.md still works:** the old `reference/mail-protocol.md` path is still valid (not deleted); agents using old paths still find the content
- [ ] **Manual test run #1 passes:** peer-send scenario (`mail-protocol/delivery/send/test.md`) returns PASS with evidence
- [ ] **Manual test run #2 passes:** self-send scenario (`mail-protocol/delivery/self-send/test.md`) returns PASS with evidence
- [ ] **Leaf contract validated:** both passing test runs confirm the `test-result.md` template works as specified (Status, Anatomy ref, Steps taken, Expected, Observed, Verdict reasoning, Artifacts)
- [ ] **Single commit:** all changes committed together with a clear message (e.g., "anatomy: mail-protocol tree pilot (Phase 1)")

---

## 6. Design Doc Open Questions — Phase 1 Defaults

The parent design doc (§8) lists five open questions "not blockers for Phase 1 — pick reasonable defaults during pilot, revisit if they hurt." This section records the defaults chosen for the pilot. If any default proves wrong during execution, note the pain point and revise before Phase 2.

| # | Question | Default for Phase 1 | Rationale |
|---|---|---|---|
| 1 | **Conductor location for v0** | Not applicable to Phase 1 (no conductor yet). Will revisit in Phase 2. | Phase 1 is manual runs only. The design doc already defers conductor to Phase 2. |
| 2 | **Leaf naming convention** | **kebab-case** — `delivery/send/`, `atomic-write/`, `cc-bcc/`, etc. | Consistent with existing reference file naming (`mail-protocol.md`, `mcp-protocol.md`). Universally safe on all filesystems. No ambiguity. |
| 3 | **Frontmatter on test.md** | **No YAML frontmatter.** test.md files are plain markdown with the four-section contract (`## Setup`, `## Steps`, `## Pass criteria`, `## Output template`). Per-scenario timeout (if needed) goes in `## Setup` as a prose note. | Keeps the contract dead simple for the pilot. If the conductor (Phase 2) needs machine-readable config, it can parse `## Setup` or we add frontmatter then. No cost to add later; expensive to over-design now. |
| 4 | **Per-leaf versioning** | **No version field in README.md.** Rely on git history + the changelog (which will be migrated to `changelog/README.md` in Phase 3). | Version fields drift if not tool-enforced. Git blame is already the source of truth. The SKILL.md version number (`2.2.0`) covers the skill as a whole. |
| 5 | **Test result location** | **Avatar workdir** (proposed in design doc). Each avatar writes `test-result.md` to `<workdir>/test-result.md`. Conductor aggregates later. | Matches the design doc proposal. No cross-conductor visibility needed until Phase 2. Easy to grep results across workdirs if needed. |

If any of these defaults cause friction during the pilot, document the issue in this checklist's "Notes" section before proceeding to Phase 2.

---

## 7. Notes and Decisions Made During Drafting

### Leaf naming: kebab-case

All directory names use `kebab-case` (lowercase, hyphens). This matches the design doc's implied convention and is consistent with existing reference file naming (`mail-protocol.md`, `mcp-protocol.md`).

### Why `delivery/` is a parent with 6 children (not 5 flat siblings)

The 5 stages of the delivery lifecycle form a natural grouping. Stages 1–5 are sequential and conceptually related. Grouping them under `delivery/` keeps the top level of `mail-protocol/` clean (architecture, delivery, mailbox, identity-card, scheduling, advanced, wake-mechanism, terminology — 8 top-level entries) instead of exploding to 13+.

### Why `advanced/` children are descriptive-only in Phase 1

CC/BCC, attachments, delay, and search are all independently verifiable in ≤5 tool calls, but they're not in the Phase 1 test scope (design doc specifies only 6 test scenarios). These are Phase 3 candidates. The `advanced/README.md` serves as a landing page; child READMEs provide focused specs.

### Why `wake-mechanism/` is descriptive-only

The self-send test implicitly validates the self-send wake path. A dedicated wake test (sleep agent → send mail → verify wake) requires cross-agent coordination that's better suited for the conductor (Phase 2). Described but not tested in Phase 1.

### Open question: should `architecture/` get a test.md?

The three-layer architecture is verifiable (~5 tool calls: grep for class names in source files). However, it's structural verification rather than behavioral — it tests "does the code have these classes" rather than "does the system behave this way." This is more unit-test territory than anatomy-test territory. **Decision: no test.md for `architecture/` in Phase 1.** Revisit in Phase 3 if desired.

---

## 8. Commit Strategy

**One commit** containing:
- 20 new directories
- 20 new README.md files
- 6 new test.md files
- 1 modified SKILL.md

Commit message: `anatomy: mail-protocol tree pilot (Phase 1)`

No squash, no staging of partial work — the design doc explicitly calls for one commit to keep the pilot atomic and easy to revert.

---

*End of Phase 1 migration checklist.*
