---
name: component-behavior-test-convention
behavior_version: 2
labt_version: 2
related_files:
  - CONTRACT.md
  - ANATOMY.md
  - dev-guide-skill/SKILL.md
  - tests/CONTRACT.md
  - src/lingtai/kernel/BEHAVIORS.md
  - src/lingtai/kernel/agent_guardian/BEHAVIORS.md
  - src/lingtai/kernel/tool_plugin/BEHAVIORS.md
  - src/lingtai/adapters/acp/BEHAVIORS.md
  - src/lingtai/tools/BEHAVIORS.md
  - src/lingtai/tools/system/BEHAVIORS.md
  - src/lingtai/tools/context/BEHAVIORS.md
  - src/lingtai/tools/daemon/BEHAVIORS.md
  - src/lingtai/tools/bash/BEHAVIORS.md
  - src/lingtai/tools/telegram/BEHAVIORS.md
  - src/lingtai/tools/tool_family/BEHAVIORS.md
  - src/lingtai/tools/file/BEHAVIORS.md
  - src/lingtai/tools/feishu/BEHAVIORS.md
  - src/lingtai/tools/web_search/BEHAVIORS.md
  - src/lingtai/tools/notification/BEHAVIORS.md
maintenance: |
  This file is the normative root of the distributed behavior-test definition
  system: it owns the LingTai Agent Behavior Task (LABT) specification, its
  version, and the contract-of-behaviors. Keep the root CONTRACT.md and root
  ANATOMY.md reciprocal: contract clauses that state agent-observable behavior
  MUST link to the LABT(s) that guard them, LABTs MUST annotate the contract
  clause they guard, and anatomy MUST link code to both when applicable.
  The three documents form one tridirectional loop; changing any one requires
  re-checking the other two. Change architecture rules, schemas, templates, and
  validation together. Bump behavior_version for a breaking convention change;
  bump labt_version when the LABT specification itself changes (fields,
  self-containment rules, version semantics).
---
# Behavior Test Convention (BEHAVIORS.md)

## Purpose

**BEHAVIORS.md is the distributed agent-observable behavior definition system.**
It is the **sibling of `CONTRACT.md`** (not a governed child): a
`BEHAVIORS.md` sits beside a `CONTRACT.md` in the same directory and records
**LingTai Agent Behavior Tasks** — self-contained agent-executable behavioral
tests in markdown. Each LABT is a scenario an agent runs against the real
product to prove the contract's *important behavior clauses* do not drift.
The contract/behaviors pair has the stronger connection: every important
behavior clause lives in the contract and is verified by a LABT here. The
paired `ANATOMY.md` is the third node of the tridirectional loop: all three
files are interconnected if applicable.

It is the third leg of the **tridirectional linkage**:

| Document | Answers | Role |
|---|---|---|
| `CONTRACT.md` | What does it promise? | Interface / behavior obligations |
| `BEHAVIORS.md` | How do we verify the promise with an agent? | Self-contained agent tasks (LABT) |
| `ANATOMY.md` | Where is the code? | Code navigation |

## LingTai Agent Behavior Task (LABT) — version 2

A **LABT** is the unit of behavior verification in this repository: a single
markdown section, fully self-contained, that an agent or daemon can execute
verbatim with no other context. LABT version is owned and versioned by this
root file (`labt_version`); child `BEHAVIORS.md` files MUST declare which
`labt_version` they conform to.

### Self-containment rule (normative)

The LABT text itself MUST contain every fact an executor needs: exact paths,
commands, expected values, and pass criteria. The executor must NOT need to
open any other file to run the task. References to contracts, anatomy, or
pytest are for traceability and review — never required for execution.

### LABT fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable anchor. Default scheme is `B###`; section heading becomes `#behavior-b###`. Per-family files MAY use a family prefix (e.g. `D###` daemon, `L###` lifecycle, `T###` tool-family, `C###` comms, `K###` kernel, `S###` shell) — prefix IDs are globally unique, collision-free across files, and readably self-describing in cross-refs; anchor becomes `#behavior-<prefix>###` (e.g. `#behavior-d003`). A validation test MUST NOT hardcode one scheme; it must accept the per-family scheme a child file declares. |
| `title` | yes | One-sentence behavior promise |
| `guards` | yes | Contract clause guarded (bidirectional ref). Format: `<contract-frontmatter-name> § <clause heading>`; the `name:` MUST match the target contract's frontmatter exactly |
| `supersedes` | no | pytest file(s) this LABT replaces/complements (traceability) |
| `runner` | yes | Which agent/daemon shape can execute (e.g. any LingTai agent with `system`) — an agent shape, NOT a pytest command |
| `prerequisites` | yes | Exact setup required (agents, dirs, files, permissions) |
| `steps` | yes | Ordered concrete actions the executor performs |
| `expected_evidence` | yes | Observable outcomes checklist (state/file/receipt/notification) |
| `pass_fail` | yes | How to decide pass vs fail, incl. forbidden side effects |
| `estimate` | no | Expected duration hint |

### LABT template

```markdown
## Behavior B001 — <title>

- **id**: B001
- **guards**: `<contract-name>` § <clause> (link back to the contract clause)
- **supersedes**: `tests/<file>.py::<test>` (optional)
- **runner**: <agent shape required>
- **prerequisites**: <exact setup — dirs, files, permissions>
- **estimate**: <duration>

### Steps
1. <action with exact path/command>
2. <observe>

### Expected evidence
- [ ] <observable outcome>
- [ ] <observable outcome>

### Pass / Fail
Pass when all evidence observed and no forbidden side effect occurs. Fail on
any mismatch; record the evidence trail in the task report.
```

## Relationship to pytest

pytest remains for **low-level assertions** (pure units, hermetic adapter
contracts, fast regressions). `BEHAVIORS.md` is the **primary behavior
verification entry** for behavior clauses: LABT scenarios an agent executes
against the real runtime, checking observable outcomes rather than internal
call shapes.

When a CONVERT_BEHAVIOR pytest is migrated, the LABT records the original
pytest file as `supersedes` so the trace stays complete; the pytest may be kept
(bottom asserts) or removed, per the change's judgment.

## Tridirectional linkage rules

Contract ⇄ Behaviors ⇄ Anatomy form one tridirectional loop: every edge carries
a stable id reference, and changing any node requires re-checking the other two.

```text
      CONTRACT.md
      /          \
 [B###]            [B###]
    /                \
BEHAVIORS.md <----> ANATOMY.md
      (related_files + LABT ids)
```

| Edge | Reference form | Where it lives |
|---|---|---|
| contract → behaviors | `[B012](BEHAVIORS.md#behavior-b012)` on every important behavior clause (states, receipts, side effects, auth gates, communication outcomes) | CONTRACT.md |
| behaviors → contract | `guards: <contract-name> § <clause>` + relative link back | each LABT entry |
| behaviors ↔ anatomy | `related_files` lists BEHAVIORS.md; anatomy entries name the LABT ids they implement | ANATOMY.md + BEHAVIORS.md |

**Change one, check the other two.** A change affecting agent-observable
behavior in any of the three MUST update or at least re-verify the other two
in the same change. This is a review gate, not optional polish.

## BEHAVIORS.md frontmatter schema

```yaml
---
name: <component>-behavior-tests      # e.g. system-behavior-tests
behavior_version: 1                   # child-file format revision
labt_version: 2                       # MUST match this root's labt_version
contract: CONTRACT.md                 # the contract(s) this guards (relative); may be a list
anatomy: ANATOMY.md                   # the anatomy this pairs with (relative)
related_files:                        # real repo-relative paths
  - src/lingtai/tools/system/karma.py
  - tests/test_karma.py
  - <any manual that teaches the behavior>
maintenance: |
  <who updates this, when, and how it links with contract/anatomy changes>
---
```

## Discovery and validation

- Root `BEHAVIORS.md` links every governed child `behaviors.md` exactly once in
  `related_files` (same rule as the contract-of-contract).
- A validation test (`tests/test_architecture_documents.py` or a sibling)
  SHOULD enforce: every contract behavior clause has a LABT link; every LABT
  has a `guards` annotation resolving to a real contract clause; every child
  BEHAVIORS.md is linked from this root and declares a matching `labt_version`.
- Keep each BEHAVIORS.md concise: one scenario per LABT, self-contained steps,
  evidence checklists over prose.
