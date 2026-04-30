# Conductor

> **Type:** Infrastructure (not a protocol leaf)
> **Status:** Design — not yet implemented

---

## What

The conductor is the **test runner** for the anatomy tree. It discovers test scenarios across all protocol subtrees, spawns avatar(s) for each, waits for results, and aggregates them into a single rollup.

The conductor is not itself testable — it IS the verifier. It has no `test.md`.

---

## Protocol

### 1. Discovery

```
glob("**/test.md", root=anatomy_root)
```

Collects all `test.md` files across the tree. Each becomes one **scenario**.

### 2. Frontmatter parsing

Each `test.md` carries optional YAML frontmatter:

```yaml
---
timeout: 300               # max seconds per scenario (default: 300)
tier: self-contained       # see tier definitions below
requires:                  # only when tier != self-contained
  peers: 1                 # how many sibling avatars to spawn
  preset: null             # preset for peer avatars (default: conductor's default)
  pre_state: null          # conductor-orchestrated setup before avatar runs
---
```

**If no frontmatter**, the conductor treats the scenario as `tier: self-contained`, `timeout: 300`.

### 3. Tier classification

| Tier | Meaning | Conductor action |
|---|---|---|
| `self-contained` | Avatar can verify using only its own tools | Spawn 1 avatar, run |
| `requires-peer` | Scenario needs sibling agent(s) in the network | Spawn N avatars + 1 test avatar, wire as siblings, run |
| `requires-env` | Scenario needs external services, special config, or environment state | Mark **SKIPPED**, record reason |

#### `self-contained` execution

1. Spawn a shallow avatar with the `test.md` body as its initial prompt.
2. Avatar's workdir is fresh (`init.json` only).
3. Avatar writes `test-result.md` in its workdir, then exits.
4. Conductor reads `test-result.md`.

#### `requires-peer` execution

1. Spawn `requires.peers` peer avatars (shallow, default preset). These are sibling agents in the test avatar's network — same `.lingtai/` parent.
2. Record each peer's address.
3. Inject peer addresses into the test avatar's prompt (append: `Your peers are at: <addr1>, <addr2>`).
4. Spawn the test avatar with the modified prompt.
5. Wait for `test-result.md` in the test avatar's workdir.
6. Peer avatars are lull'd after the test completes.

#### `requires-env` execution

- Do not attempt. Record as `SKIPPED` with `reason: "requires <what>"`.
- The conductor does not provision external services.

### 4. Wait and timeout

- Poll each avatar's workdir for `test-result.md` at 5-second intervals.
- If `test-result.md` appears → scenario complete.
- If timeout expires (per frontmatter) → kill avatar, record as `TIMEOUT`.
- Wall-clock timeout for the entire run = `sum(per-scenario timeouts)` (scenarios run in parallel, bounded by concurrency cap).

### 5. Concurrency

Default cap: **8 avatars** simultaneously (configurable). Scenarios queue if more are ready. The conductor does not spawn unbounded avatars.

### 6. Aggregation → INDEX.md

Read all `test-result.md` files, produce rollup:

```markdown
# Test run <ISO timestamp>

**Total:** N scenarios — X PASS, Y FAIL, Z INCONCLUSIVE, W SKIPPED, V TIMEOUT
**Wall time:** <seconds>
**Anatomy version:** <git ref or SKILL.md version>
**Conductor:** <conductor agent_id>

## Results

| # | Scenario | Status | Time | Avatar | Notes |
|---|---|---|---|---|---|
| 1 | mail-protocol/send/self-send | PASS | 45s | abc-123 | |
| 2 | mail-protocol/send/peer-send | PASS | 30s | def-456 | used 1 peer |
| 3 | mail-protocol/receive/polling-listener | SKIP | — | — | requires-env: needs pseudo-agent |
| ... | | | | | |

## Failures

### mail-protocol/send/dedup
- **Avatar:** ghi-789
- **Verdict:** FAIL — 3rd identical message was not blocked
- **Link:** [test-result.md](./ghi-789/test-result.md)
```

### 7. Cleanup

- By default: **keep** avatar workdirs for debugging.
- Optional: `--archive` flag moves completed avatars to `archive/<timestamp>/`.
- Conductor's own workdir persists INDEX.md permanently.

---

## test.md contract (summary)

The conductor enforces these rules on every `test.md` it discovers:

1. **Frontmatter is optional** — sensible defaults apply.
2. **Body is the avatar's prompt** — loaded verbatim as the avatar's initial `.prompt`.
3. **Avatar must write `test-result.md`** — using the standard template (see `mail-protocol/send/self-send/test.md` for an example).
4. **Avatar must exit cleanly** — no infinite loops, no sleep-as-idle.
5. **Pass criteria must be filesystem-observable** — no LLM judgment ("did the avatar say the right thing"), only structural checks (file exists, JSON valid, field matches expected).

---

## Status codes

| Code | Meaning | Assigned by |
|---|---|---|
| **PASS** | All pass criteria met | Avatar (in `test-result.md`) |
| **FAIL** | At least one criterion violated | Avatar (in `test-result.md`) |
| **INCONCLUSIVE** | Test could not execute (missing dependency, env issue) | Avatar (in `test-result.md`) |
| **SKIPPED** | Conductor decided not to run (`requires-env`, resource cap) | Conductor (in INDEX.md) |
| **TIMEOUT** | Avatar did not produce `test-result.md` within timeout | Conductor (in INDEX.md) |
| **ERROR** | Avatar crashed, workdir missing, unexpected failure | Conductor (in INDEX.md) |

---

## Relationship to the anatomy tree

```
anatomy-root/
├── conductor/              ← you are here (infrastructure)
│   └── README.md
├── mail-protocol/
│   ├── send/
│   │   ├── self-send/
│   │   │   └── test.md    ← discovered by conductor
│   │   └── ...
│   └── receive/
│       └── ...
├── memory-system/          ← future protocol subtree
└── ...
```

The conductor operates on **any** `test.md` in the tree, regardless of protocol. It is protocol-agnostic infrastructure.
