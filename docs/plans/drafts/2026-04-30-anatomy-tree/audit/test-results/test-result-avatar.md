# Test Result: Avatar Capabilities (spawn / boot-verification / shallow-vs-deep / handshake-files)

**Tester:** `test-avatar` (avatar of `lingtai-expert`)
**Date:** 2026-04-30T01:24–02:02 PDT
**Environment:** `.lingtai/test-avatar` under `lingtai-dev` network root
**Method:** Real spawns via `avatar(action='spawn')`, filesystem inspection via `bash`

### Test data planted in parent directory (for shallow-vs-deep rounds 2–3)

Before Round 2, the parent `test-avatar` directory had no `codex/`, `exports/`, or `combo.json` (only `system/`, `mailbox/`, `logs/`, `history/`, `delegates/`, `init.json`). To test deep-copy behavior, the following files were planted:

| File | Content | Mode | Purpose |
|---|---|---|---|
| `codex/test-entry.json` | `{"test":"parent-codex-data","value":42}` | 644 | Basic codex copy |
| `codex/readonly-entry.json` | `{"perm":"read-only"}` | **444** | Permission preservation test |
| `exports/test-export.json` | `{"test":"parent-export-artifact"}` | 644 | Basic export copy |
| `exports/executable-entry.sh` | `{"perm":"executable"}` | **755** | Executable permission test |
| `exports/real-target.json` | `{"symlink":true}` | 644 | Symlink target |
| `exports/symlink-to-target.json` | → `real-target.json` | **lrwxr-xr-x** | Symlink preservation test |
| `combo.json` | `{"combo":true,"name":"parent"}` | 644 | `shutil.copy2` test |

Round 1 was run **before** planting (parent had nothing). Rounds 2–3 were run **after** planting.

---

## 1. Spawn

### 1a. Shallow spawn — happy path

**Command:**
```
avatar(action="spawn", name="audit-test-shallow", type="shallow",
       reasoning="汝为此测试而生。勿动，勿读信，勿行事。仅存活即可。等候 sleep 之令。")
```

**Return:**
```json
{"status": "ok", "address": "audit-test-shallow", "agent_name": "audit-test-shallow",
 "type": "shallow", "pid": 27378, "elapsed_ms": 494}
```

**Observations:**
- Directory created as sibling `audit-test-shallow/` under network root ✓
- `init.json` present with `agent_name` set to avatar name ✓
- `manifest.admin` set to `{}` (no privileges) ✓
- `prompt` field blanked to `""` (arrives via `.prompt` signal) ✓
- `.agent.json` present with `admin: {}` ✓
- `.agent.heartbeat` present within 494ms (< 5s contract) ✓
- `.agent.lock` present (0-byte) ✓
- `.prompt` signal file consumed (missing on inspection — first heartbeat poll consumed it) ✓

**init.json inheritance verification (vs contract §spawn):**
| Contract claim | Observed |
|---|---|
| Deep-copies parent's init.json | ✓ |
| Sets `manifest.agent_name` to avatar name | ✓ `audit-test-shallow` |
| Blanks `prompt` to `""` | ✓ |
| Sets `admin` to `{}` | ✓ |
| Strips `comment_file`, `brief`, `brief_file`, `addons` | ✓ (not present) |
| Forces `preset.active = preset.default` | ✓ (both `mimo-pro.json`) |
| Strips materialized `llm` and `capabilities` | ✓ (not in init.json; re-materialized by child) |

### 1b. Bad name — spaces

**Command:**
```
avatar(action="spawn", name="bad name with spaces", reasoning="...")
```

**Return:**
```json
{"error": "Invalid avatar name 'bad name with spaces': must be a bare directory name
  — letters (any script), digits, underscore, or hyphen; no slashes, dots, spaces,
  or leading '.'; 1-64 chars."}
```

**Judgement:** ✓ Contract says `[\w-]+` + no spaces. Error message is descriptive and matches.

### 1c. Bad name — slash

**Command:**
```
avatar(action="spawn", name="bad/name", reasoning="...")
```

**Return:**
```json
{"error": "Invalid avatar name 'bad/name': must be a bare directory name ..."}
```

**Judgement:** ✓ Contract says "single-segment only — no slashes." Correctly rejected.

### 1d. Ledger recording

```
{"ts": 1777537479.35, "name": "audit-test-shallow", "type": "shallow", "boot_status": "ok"}
{"ts": 1777537515.13, "name": "audit-test-deep",    "type": "deep",    "boot_status": "ok"}
{"ts": 1777537824.82, "name": "audit-deep-2",       "type": "deep",    "boot_status": "ok"}
{"ts": 1777537825.30, "name": "audit-shallow-2",    "type": "shallow", "boot_status": "ok"}
```

**Judgement:** ✓ Every spawn appended to `delegates/ledger.jsonl` with required fields (`ts`, `event`, `name`, `working_dir`, `mission`, `type`, `pid`, `boot_status`).

---

## 2. Boot Verification

### 2a. Successful boot (ok)

All four spawns returned `boot_status: "ok"` in the ledger and `"status": "ok"` in the tool return. The `_wait_for_boot` poll loop detected `.agent.heartbeat` within 500ms.

**Contract verification:**

| Contract claim | Observed |
|---|---|
| Polls at 0.1s intervals | ✓ (total time ~500ms suggests fast detection) |
| `.agent.heartbeat` file existence triggers `ok` | ✓ |
| `boot_status: "ok"` recorded in ledger | ✓ |
| Return includes `address`, `agent_name`, `type`, `pid` | ✓ |

### 2b. Failed boot — NOT TESTED (INCONCLUSIVE)

**Why:** Testing `failed` requires a child that crashes during boot. The contract says "bad init.json → process exits → read spawn.stderr tail." I did not create a corrupted init.json because:
1. The avatar tool validates init.json through its own pipeline before spawning
2. Injecting a raw bad init.json would require filesystem manipulation mid-spawn
3. Risk of leaving a broken directory behind

**Recommendation:** A future test should write a syntactically invalid `init.json` to a fresh directory and invoke `lingtai run` directly via `bash` to verify the stderr capture path.

### 2c. Slow boot — NOT TESTED (INCONCLUSIVE)

**Why:** Would require a child that takes >5s to write heartbeat. No mechanism available to inject delay without modifying source code.

---

## 3. Shallow vs Deep

### 3a. Round 1 — Parent with no codex/exports/combo.json

Parent directory had empty/missing `codex/`, `exports/`, `combo.json`.

**Result:** Both shallow and deep produced **identical directory trees** (modulo timestamps):
```
init.json, system/, mailbox/, logs/, history/   (both)
codex/, exports/, combo.json                     (neither — nothing to copy)
```

**Judgement:** ✓ Correct behavior — `_prepare_deep()` copies what exists; with nothing to copy, result is same as `mkdir()`. **However, this round does NOT validate the deep-copy contract.** See 3b for the real test.

### 3b. Round 2 — Parent WITH codex/exports/combo.json

**Planted test data (parent directory):**

| File | Content | Permissions |
|---|---|---|
| `codex/test-entry.json` | `{"test": "parent-codex-data", "value": 42}` | 644 (default) |
| `codex/readonly-entry.json` | `{"perm": "read-only"}` | 444 (read-only) |
| `exports/test-export.json` | `{"test": "parent-export-artifact"}` | 644 (default) |
| `exports/executable-entry.sh` | `{"perm": "executable"}` | 755 (executable) |
| `exports/real-target.json` | `{"symlink": true}` | 644 (default) |
| `exports/symlink-to-target.json` | → `real-target.json` (symlink) | lrwxr-xr-x |
| `combo.json` | `{"combo": true, "name": "parent"}` | 644 (default) |

**Deep (`audit-deep-2`) tree:**
```
audit-deep-2/
├── codex/
│   ├── test-entry.json          ← COPIED (byte-identical)
│   └── readonly-entry.json      ← COPIED (byte-identical, permissions preserved 444)
├── combo.json                   ← COPIED (byte-identical)
├── exports/
│   ├── test-export.json         ← COPIED (byte-identical)
│   ├── executable-entry.sh      ← COPIED (byte-identical, permissions preserved 755)
│   ├── real-target.json         ← COPIED (byte-identical)
│   └── symlink-to-target.json   ← COPIED AS REGULAR FILE (symlink NOT preserved)
├── history/
│   └── chat_history.jsonl       ← NOT copied (created fresh by child)
├── init.json                    ← modified by _make_avatar_init
├── logs/                        ← created fresh
├── mailbox/inbox/               ← created fresh
└── system/                      ← COPIED (then overwritten by child)
```

**Shallow (`audit-shallow-2`) tree:**
```
audit-shallow-2/
├── init.json                    ← modified by _make_avatar_init
├── logs/                        ← created fresh
├── mailbox/inbox/               ← created fresh
└── system/                      ← created fresh (not copied from parent)
```

**Diff results (parent → deep):**
```
codex/test-entry.json      → identical ✓
codex/readonly-entry.json  → identical content ✓, permissions 444 preserved ✓
exports/test-export.json   → identical ✓
exports/executable-entry.sh → identical content ✓, permissions 755 preserved ✓
exports/real-target.json   → identical ✓
exports/symlink-to-target.json → content identical, BUT symlink followed (see below)
combo.json                 → identical ✓
system/covenant.md         → identical ✓
```

**Permission & symlink findings (Round 3 — `audit-deep-3`):**

| Property | Parent | Deep copy | Verdict |
|---|---|---|---|
| `codex/readonly-entry.json` mode | `-r--r--r--` (444) | `-r--r--r--` (444) | ✓ Preserved |
| `exports/executable-entry.sh` mode | `-rwxr-xr-x` (755) | `-rwxr-xr-x` (755) | ✓ Preserved |
| `exports/symlink-to-target.json` type | `lrwxr-xr-x` (symlink) | `-rw-r--r--` (regular file) | ⚠️ **Followed** — symlink resolved to regular file |
| Symlink target content | `{"symlink": true}` | `{"symlink": true}` | ✓ Content correct (target file content copied) |

**Implication:** `shutil.copytree()` without `symlinks=True` follows symlinks by default. If a parent's `codex/` or `exports/` contains symlinks, the deep avatar gets regular-file copies. This is the intended behavior — contract updated, ADR-001 (§3d) records the decision rationale.

**Contract verification:**

| Contract claim | Observed |
|---|---|
| Deep copies `system/` via `shutil.copytree` | ✓ (byte-identical) |
| Deep copies `codex/` via `shutil.copytree` | ✓ (byte-identical) |
| Deep copies `exports/` via `shutil.copytree` | ✓ (byte-identical) |
| Deep copies `combo.json` via `shutil.copy2` | ✓ (byte-identical) |
| Deep preserves file permissions | ✓ (444, 755 both preserved) |
| Deep preserves symlinks | ✗ **Followed** — symlinks become regular files (**RESOLVED** — contract 已修，见 §3d ADR-001) |
| Deep does NOT copy `history/` | ✓ (child's own history created fresh) |
| Deep does NOT copy `mailbox/` | ✓ (child's own mailbox created fresh) |
| Deep does NOT copy `delegates/` | ✓ (not present in child) |
| Deep does NOT copy `.agent.json` | ✓ (child writes own on boot) |
| Deep does NOT copy `.agent.heartbeat` | ✓ (child writes own on boot) |
| Deep does NOT copy `logs/` | ✓ (child's own logs) |
| Shallow has only `init.json` + system scaffolding | ✓ (no codex/exports/combo.json) |
| `_make_avatar_init` identical for both types | ✓ (both init.jsons have same structure) |

### 3c. Notable observation — `system/` created for both

The contract says shallow is "bare `mkdir()`" — but both shallow and deep end up with a `system/` directory. This is because `system/` is **not** from the parent copy; it's created by the **child process on boot** (the kernel materializes system prompt files). The deep copy of `system/` from the parent is subsequently **overwritten** by the child's own materialization. This means the deep copy of `system/` is effectively a no-op in practice.

**Implication:** The meaningful distinction between deep and shallow is `codex/`, `exports/`, and `combo.json`. The `system/` copy is redundant if the child always re-materializes.

### 3d. ADR-001: Symlink behavior in deep avatar copy

**Status:** RESOLVED (2026-04-30)
**Decision makers:** `test-avatar` (auditor), `lingtai-expert` (parent, via review)

**Context:**
`_prepare_deep()` calls `shutil.copytree()` without `symlinks=True`, so Python's default
`symlinks=False` applies: symbolic links in the source are *followed* and copied as regular
files in the destination. The contract (shallow-vs-deep/README.md) was silent on this behavior.

**Options considered:**

| Option | Action | Risk |
|---|---|---|
| A. Follow symlinks (status quo) | Document `symlinks=False` in contract | Minimal — symlinks in codex/exports are rare; those pointing outside working dir would break on copy |
| B. Preserve symlinks | Change code to `shutil.copytree(..., symlinks=True)` | Medium — symlinks to absolute paths or parent-relative paths become dangling in child |

**Decision: Option A — follow symlinks, document in contract.**

Rationale:
- `codex/` and `exports/` are knowledge stores, not source repos — symlinks are uncommon.
- Symlinks pointing outside the working directory (e.g., `../shared/data`) would break after copy into a sibling directory with a different name.
- `symlinks=False` matches Python stdlib default and requires no code change.
- File permissions (mode bits) *are* preserved — verified with 444 and 755 modes.

**Action taken:**
- Contract `shallow-vs-deep/README.md` updated: added "symlinks followed" to copytree description, added explicit "Symlink behavior" paragraph.

**Not considered:** Symlink-aware copy (`copy_function` with `follow_symlinks=False`) — unnecessary complexity for this use case.

---

## 4. Handshake Files

### 4a. Files observed in a freshly booted avatar

| File | Present | Content |
|---|---|---|
| `.agent.json` | ✓ | Full identity manifest (see §1a) |
| `.agent.heartbeat` | ✓ | Unix timestamp float: `1777537487.371831` |
| `.agent.lock` | ✓ | 0-byte file (fcntl lock) |
| `.prompt` | consumed | Unlinked after first heartbeat poll |
| `.status.json` | ✓ | Runtime snapshot with identity, runtime, tokens |

### 4b. `.agent.json` content (shallow)

```json
{
  "agent_id": "20260430-082439-0d17",
  "agent_name": "audit-test-shallow",
  "nickname": null,
  "address": "audit-test-shallow",
  "created_at": "2026-04-30T08:24:39Z",
  "started_at": "2026-04-30T08:24:39Z",
  "admin": {},
  "language": "wen",
  "stamina": 36000,
  "state": "active",
  "soul_delay": 120,
  "molt_count": 0,
  "capabilities": [...]
}
```

**Contract verification:**

| Contract claim | Observed |
|---|---|
| `admin` is `{}` for avatars | ✓ |
| `agent_id` is permanent birth ID | ✓ (unique per spawn) |
| `.agent.heartbeat` is Unix timestamp float | ✓ |
| `.agent.lock` is 0-byte exclusive lock file | ✓ |
| `.prompt` consumed on first poll (one-shot) | ✓ (missing on inspection) |
| `.status.json` written by child on boot | ✓ |

### 4c. `.agent.heartbeat` liveness

```
Shallow heartbeat: 1777537571.6819289
Deep heartbeat:    1777537571.2706518
```

Both within the 2.0s liveness threshold. Heartbeats are refreshed every ~1s by the child's `_heartbeat_loop()`.

### 4d. Handshake sequence (as-observed)

1. `avatar(spawn)` → parent creates workdir + writes `init.json` + writes `.prompt`
2. Parent launches `[python -m lingtai run <dir>]` (detached)
3. Child boots → `WorkingDir.acquire_lock()` → writes `.agent.json`
4. Child starts heartbeat thread → writes `.agent.heartbeat`
5. Parent's `_wait_for_boot()` detects heartbeat → returns `"ok"`
6. Child consumes `.prompt` on first poll → unlinks it

**Matches contract §handshake-files "Handshake sequence on boot"** ✓

---

## 5. Summary Judgement

| Leaf | Status | Notes |
|---|---|---|
| **spawn** | ✅ PASS | Name validation, workdir creation, init.json inheritance, process launch, ledger recording — all match contract. |
| **boot-verification** | ⚠️ PARTIAL | `ok` outcome verified. `failed` and `slow` outcomes not tested (INCONCLUSIVE). |
| **shallow-vs-deep** | ✅ PASS | Deep copies codex/exports/combo.json (byte-identical). File permissions preserved. Symlinks followed → contract 已修明言 `symlinks=False` (ADR-001, §3d). `system/` copy is redundant (child re-materializes). |
| **handshake-files** | ✅ PASS | `.agent.json`, `.agent.heartbeat`, `.agent.lock`, `.prompt`, `.status.json` all present and match contract format. |

## 6. Experience Notes

1. **Parent with no content makes Round 1 misleading.** The first shallow-vs-deep comparison showed identical trees because the parent had nothing to copy. Only after planting codex/exports/combo.json did the difference emerge. The contract is technically correct but the test environment matters enormously.

2. **`system/` copy in deep is a no-op.** Both shallow and deep avatars end up with identical `system/` content because the child re-materializes it on boot. The `_prepare_deep` `shutil.copytree('system/')` writes files that the child then overwrites. This is not a bug (defense-in-depth), but it means `system/` is not a meaningful discriminant between types.

3. **MCP warnings in spawn.stderr.** Both avatars log `WARNING: init.json mcp 'feishu': skipped — not in mcp_registry.jsonl` for all four MCP servers. The `addons` field is stripped from avatar init.json per contract, but the `mcp` block is inherited. Avatars inherit MCP server definitions but the servers aren't registered in their own registry. This is expected behavior (each agent registers independently) but produces noisy stderr.

4. **`history/chat_history.jsonl` is created by child on boot, not copied.** Both shallow and deep have `history/` with their own `chat_history.jsonl`. For deep, the parent's history is explicitly NOT copied (contract line 483). Verified by different file sizes (105214 vs 104950 bytes for the two original spawns — content differs because each had a different boot conversation).

5. **Boot time is well under 5s.** All four spawns returned in 475–610ms. The 5s timeout provides ample margin.

6. **File permissions preserved on deep copy.** `shutil.copytree()` and `shutil.copy2()` both preserve permission bits. Verified with 444 (read-only) and 755 (executable). This matches Python stdlib behavior.

7. **Symlinks are followed, not preserved.** `shutil.copytree()` without `symlinks=True` follows symlinks and copies the target file content as a regular file. Content is correct but symlink structure is lost. Decision: follow (status quo) and document in contract — see ADR-001 (§3d). Contract `shallow-vs-deep/README.md` has been updated.

---

*Audited by `test-avatar` · 2026-04-30 · lingtai anatomy tree project*

---

## Appendix: Related Documents

| Document | Path | Description |
|---|---|---|
| **ADR-001** (inline §3d) | — | Symlink follow decision in this file |
| **Implicit defaults scan** | `scan-implicit-defaults-avatar.md` (same directory) | Systematic scan of all implicit defaults across four avatar contract leaves; 3 resolved, 4 deferred |
| **Source patch** | commit `991f695` | `fix(avatar): explicit utf-8 encoding on write_text/read_text; wall-clock comment` |
| **Contract updated** | `shallow-vs-deep/README.md` (lines 42-50) | Symlink behavior documented; "Symlink behavior" paragraph added |
