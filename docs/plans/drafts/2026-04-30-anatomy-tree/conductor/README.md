# Anatomy Test Conductor

A LingTai agent that orchestrates agent-driven acceptance tests across the anatomy tree.

## What it does

The conductor discovers all `test.md` files in the anatomy tree, spawns one shallow avatar per scenario, waits for each avatar to produce a `test-result.md`, and writes a rollup `INDEX.md` summarizing pass/fail/inconclusive results.

```
anatomy root/
├── mail-protocol/
│   ├── send/
│   │   └── self-send/
│   │       ├── README.md        ← spec (the leaf)
│   │       └── test.md          ← scenario (what the avatar runs)
│   └── receive/
│       └── dedup/
│           ├── README.md
│           └── test.md
├── molt-protocol/
│   └── ...
└── ...

conductor/                       ← this agent's workdir
├── init.json                    ← agent configuration
├── conductor.py                 ← protocol skeleton (reference, not executable)
└── test-results/
    └── 20260430-143000/
        ├── INDEX.md             ← rollup report
        ├── test-mail-send-selfsend.md   ← copied result
        └── test-molt-basic.md           ← copied result
```

## Inputs

| Input | Source | Description |
|-------|--------|-------------|
| Anatomy tree root | `ANATOMY_ROOT` env var or config | Path to the `lingtai-kernel-anatomy/` directory containing the tree of `README.md` + `test.md` leaves |
| Concurrency cap | Default: 8 | Max parallel avatars |
| Per-scenario timeout | Default: 5 min | Configurable via frontmatter in each `test.md` |

### test.md frontmatter (optional)

```yaml
---
timeout: 300          # seconds (default: 300)
concurrency_hint: 1   # informational, not enforced in v0
dependencies: []      # leaf paths that must pass first (not enforced in v0)
---
```

## Outputs

| Output | Location | Description |
|--------|----------|-------------|
| Rollup INDEX.md | `test-results/<timestamp>/INDEX.md` | Summary of all results |
| Individual results | `test-results/<timestamp>/<avatar-name>.md` | Copied from each avatar's workdir |
| Avatar workdirs | `.lingtai/test-*/` | Left intact for debugging (unless `--archive`) |

## How to invoke

### As a LingTai agent

1. Place the conductor's `init.json` in a `.lingtai/conductor/` directory within your network.
2. Start the conductor agent — it will read its briefing and begin the orchestration flow.
3. The conductor discovers scenarios, spawns avatars, polls for results, and writes the rollup.

### Manually (development)

```bash
# Dry run — discover scenarios without running them
python3 conductor.py --root /path/to/anatomy/tree --dry-run

# Full run
python3 conductor.py --root /path/to/anatomy/tree --concurrency 4 --version "0.3.1"
```

Note: `conductor.py` is a **design skeleton** documenting the protocol. The real conductor is a LingTau agent that makes the same tool calls shown in the code.

## How avatars work

Each avatar is spawned as a **shallow** LingTai avatar (`avatar(action="spawn", type="shallow")`). The avatar receives:

1. **Framing instructions** — "You are an anatomy test runner. Execute the scenario, verify pass criteria, write test-result.md."
2. **The test.md content** — the full scenario brief.

The avatar can read its sibling `README.md` for spec details (the conductor copies the leaf directory into the avatar's workdir, or the avatar reads from the shared anatomy root).

The avatar writes `test-result.md` using the standard template (see design doc §3) and then goes idle.

## Rollup INDEX.md format

```markdown
# Test run 20260430-143000

**Total:** 6 scenarios — 4 PASS, 1 FAIL, 1 INCONCLUSIVE
**Wall time:** 245.3s
**Anatomy version:** 0.3.1

## Failures
- [mail-protocol/send/dedup](../test-mail-send-dedup/test-result.md) — 3rd duplicate not rejected

## Inconclusive
- [mcp-protocol/licc/roundtrip](../test-mcp-licc-roundtrip/test-result.md) — no MCP available in test env

## Passes
- mail-protocol/send/self-send
- mail-protocol/send/peer-send
- mail-protocol/send/scheduled
- mail-protocol/identity/card-fields
```

## Design decisions

- **Shallow avatars** (not deep) — each test is independent, no need to inherit conductor's state.
- **Filesystem-native** — no GitHub issues, no external services. Results are markdown files.
- **Observable behavior only** — tests verify filesystem outputs, mailbox contents, status files. Not internal code paths (that's pytest's job).
- **INCONCLUSIVE ≠ FAIL** — distinct outcomes. INCONCLUSIVE means the test couldn't run (missing dependency, environment issue). FAIL means the contract was violated.
- **Concurrency capped** — default 8 parallel avatars to bound cost. Scenarios beyond the cap are batched.

## Relationship to other pieces

| Piece | Role |
|-------|------|
| **conductor** (this) | Orchestration — spawn, wait, aggregate |
| **test.md** | Scenario brief — what to test, how, pass criteria |
| **README.md** | Spec — the contract being verified |
| **SKILL.md** | Navigator — index of all leaves, searchable |
| **Existing pytest** | Unit/integration — complementary, not replaced |

## Known Limitations (v0 skeleton)

These are identified gaps in the current skeleton. None block Phase 1 pilot — they should be resolved during Phase 2 conductor implementation.

1. **Leaf asset delivery to avatars.** The design doc says avatars "can read its sibling `README.md` for spec details," but shallow avatars spawn with a workdir under `.lingtai/test-<name>/`, not inside the anatomy tree. Two options: (a) the conductor copies the leaf directory into each avatar's workdir before spawning, or (b) the conductor embeds an absolute path in the avatar's reasoning prompt. v0 should prefer (a) — it makes the avatar self-contained and avoids path-resolution fragility. Not yet implemented in `conductor.py`.

2. **Avatar workdir path assumption.** The skeleton assumes each avatar's workdir is at `.lingtai/<avatar_name>/`, but the actual path is determined by the kernel at spawn time. The conductor must capture the real workdir from the `avatar()` call's return value (or discover it via `email(check)` / filesystem inspection) before copying leaf assets or polling for `test-result.md`. Not yet wired in `conductor.py`.

3. **Batch overflow (>concurrency scenarios).** `run_conductor()` has a `while remaining` loop for scenarios exceeding the concurrency cap, but only the first batch is actually spawned. The replacement-spawn logic (spawn a new avatar as each finishes) is not implemented. For the pilot (5–6 mail-protocol scenarios, cap 8), this won't trigger — but it will matter when rolling out 80–100 leaves in Phase 3.

## Open questions (from design doc)

These defaults are reasonable for v0 but may be revisited:

1. Should avatars signal completion via email instead of file polling?
2. Should results go to a fixed cross-conductor directory (`~/.lingtai-tui/test-results/`)?
3. Should test.md have YAML frontmatter, or should config be a sibling `test.yaml`?
4. How should the conductor handle scenarios with dependencies between them?
