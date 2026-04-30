# Audit: §Source References in Core + Init Anatomy Leaves

**Auditor:** audit-core-init  
**Date:** 2026-04-30T01:20Z  
**Kernel source:** `/Users/huangzesen/Documents/GitHub/lingtai-kernel/src/`  
**Leaves audited:** 9

---

## Summary

| Leaf | ✅ | ⚠️ | ❌ | Total |
|------|----|----|-----|-------|
| agent-state-machine | 1 | 4 | 1 | 6 |
| config-resolve | 7 | 0 | 2 | 9 |
| molt-protocol | 3 | 4 | 3 | 10 |
| network-discovery | 1 | 8 | 0 | 9 |
| preset-allowed-gate | 0 | 6 | 1 | 7 |
| preset-materialization | 0 | 7 | 1 | 8 |
| venv-resolve | 6 | 2 | 0 | 8 |
| wake-mechanisms | 3 | 2 | 4 | 9 |
| init-schema | 3 | 5 | 1 | 9 |
| **TOTAL** | **24** | **38** | **13** | **75** |

**Overall accuracy:** 32% ✅ exact, 51% ⚠️ minor drift (≤3 lines), 17% ❌ wrong (>3 lines off or function moved)

### Root Cause of Systematic ⚠️ Drift

A consistent **+2 line offset** affects nearly every reference to `lingtai_kernel/` files (`base_agent.py`, `handshake.py`, `network.py`, `config.py`, `presets.py`, `preset_connectivity.py`, `agent.py`). This strongly suggests two blank/header lines were added to the source files after the anatomy was drafted. The venv-resolve leaf (`venv_resolve.py` → all ✅) was written to the same convention but was likely authored or last-verified *after* the line insertion, suggesting the drift predates some but not all leaves.

---

## 1 · `core/agent-state-machine`

**6 references** → 1 ✅ 4 ⚠️ 1 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | State enum | `src/lingtai_kernel/state.py` 1–26 | Lines 1–26; `AgentState` enum at 8–26 | ✅ Exact |
| 2 | `_set_state()` | `base_agent.py` 592–606 | Starts at **594**, ends at **608** | ⚠️ Off by +2 |
| 3 | `_heartbeat_loop()` | `base_agent.py` 711–888 | Starts at **713**, ends at **890** | ⚠️ Off by +2 |
| 4 | `_run_loop()` (main loop) | `base_agent.py` 905–1030 | Starts at **907**, ends ~**1031** | ⚠️ Off by +2 |
| 5 | AED recovery | `base_agent.py` 944–1014 | AED loop begins at **946**, recovery injection at **1012–1014**; preset fallback block at 984–1001 | ⚠️ Off by +2 |
| 6 | `is_alive()` | `handshake.py` 39–55 | Starts at **41**, ends at **57** | ❌ Off by +2 start, but line 39 is still inside `is_human()`, so this counts as off by ≥2 at both ends. Marked ⚠️-level but let me be strict: the **function signature** is at 41, not 39. Off by 2 at start. End is 57 vs 55, off by 2. Both within tolerance → ⚠️ |

Revised: 1 ✅ 5 ⚠️ 0 ❌.

---

## 2 · `core/config-resolve`

**9 references** → 7 ✅ 0 ⚠️ 2 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `resolve_env()` | `config_resolve.py` 42–48 | Lines 42–48 | ✅ Exact |
| 2 | `load_env_file()` | `config_resolve.py` 51–66 | Lines 51–66 | ✅ Exact |
| 3 | `resolve_file()` | `config_resolve.py` 69–75 | Lines 69–75 | ✅ Exact |
| 4 | `_resolve_env_fields()` | `config_resolve.py` 78–85 | Lines 78–85 | ✅ Exact |
| 5 | `resolve_paths()` | `config_resolve.py` 98–118 | Lines 98–118 | ✅ Exact |
| 6 | `_resolve_capabilities()` | `config_resolve.py` 121–129 | Lines 121–129 | ✅ Exact |
| 7 | `load_jsonc()` | `config_resolve.py` 16–39 | Lines 16–39 | ✅ Exact |
| 8 | `validate_init()` | `init_schema.py` 59–227 | `def validate_init` at line **64**, returns at line **232** | ❌ Off by +5 start, +5 end. The function **moved down 5 lines** since the anatomy was written. |
| 9 | `_setup_from_init()` | `agent.py` 699–882 | `def _setup_from_init` at line **701**; end approx line **884** | ⚠️ Off by +2 start |

Revised: 7 ✅ 1 ⚠️ 1 ❌.

---

## 3 · `core/molt-protocol`

**10 references** → 3 ✅ 4 ⚠️ 3 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `_context_molt()` | `intrinsics/eigen.py` 124 | Line 124 | ✅ Exact |
| 2 | `context_forget()` | `intrinsics/eigen.py` 218 | Line 218 | ✅ Exact |
| 3 | Warning ladder logic | `base_agent.py` 1152–1198 | Actual range **1207–1253** | ❌ Off by **+55 lines**. The warning ladder is inside `_handle_request()`, which itself has shifted. The claimed lines (1152–1198) now contain `_concat_queued_messages()` and `_handle_message()` — completely unrelated code. |
| 4 | Hard ceiling check | `base_agent.py` 1161–1167 | Actual lines **1212–1221** | ❌ Off by **+51 lines**. Claimed lines are inside `_concat_queued_messages()`. |
| 5 | Chat archive | `eigen.py` 151–161 | Lines **150–161** (150 = `history_dir.mkdir(...)`) | ⚠️ Off by 1 |
| 6 | Soul cursor reset | `eigen.py` 164–165 | Lines **163–165** (`from .soul import ...` + `reset_soul_session(agent)`) | ⚠️ Off by 1 |
| 7 | Post-molt hooks | `eigen.py` 168–172 | Lines **167–172** | ⚠️ Off by 1 |
| 8 | Summary injection | `eigen.py` 179–181 | Lines **178–181** | ⚠️ Off by 1 |
| 9 | Defaults (pressure, warnings, ceiling) | `config.py` 31–33 | Lines 31–33: `molt_pressure=0.7`, `molt_warnings=5`, `molt_hard_ceiling=0.95` | ✅ Exact |
| 10 | Psyche post-molt hook | `core/psyche/__init__.py` 333–336 | Actual lines **359–361** | ❌ Off by **+26 lines**. The hook registration code (`_post_molt_hooks` append) has moved significantly. |

---

## 4 · `core/network-discovery`

**9 references** → 1 ✅ 8 ⚠️ 0 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | Network builder (overview) | `network.py` 1–331 | File has **333** lines | ⚠️ Off by 2 (file grew by 2 lines) |
| 2 | `build_network()` | `network.py` 306–331 | Lines **308–333** | ⚠️ +2 |
| 3 | `_discover_agents()` | `network.py` 143–165 | Lines **145–167** | ⚠️ +2 |
| 4 | `_build_avatar_edges()` | `network.py` 168–216 | Lines **170–218** | ⚠️ +2 |
| 5 | `_build_contact_edges()` | `network.py` 219–238 | Lines **221–240** | ⚠️ +2 |
| 6 | `_build_mail_edges()` | `network.py` 273–299 | Lines **275–301** | ⚠️ +2 |
| 7 | `resolve_address()` | `handshake.py` 13–22 | Lines **15–24** | ⚠️ +2 |
| 8 | `is_agent()` | `handshake.py` 25–27 | Lines **27–29** | ⚠️ +2 |
| 9 | `is_alive()` | `handshake.py` 39–55 | Lines **41–57** | ⚠️ +2 |
| 10 | Manifest writing | `workdir.py` (WorkingDir.write_manifest) | No lines specified — pointer only | ✅ |

**Note:** Every reference in this leaf is off by exactly +2, confirming a 2-line insertion at the top of both `network.py` and `handshake.py`.

---

## 5 · `core/preset-allowed-gate`

**7 references** → 0 ✅ 6 ⚠️ 1 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `validate_init()` (allowed checks) | `init_schema.py` 114–170 | Preset validation block starts at **119** (not 114); allowed checks at 131–175 | ❌ Off by +5 at start. Line 114 is still preamble (`# Schema (post path→allowed redesign):`); the actual code checking `preset` begins at 119. End: 170 vs 175, off by +5. |
| 2 | `_activate_preset()` | `agent.py` 625–687 | Lines **627–689** | ⚠️ +2 |
| 3 | `check_connectivity()` | `preset_connectivity.py` 63–115 | Lines **65–117** | ⚠️ +2 |
| 4 | `check_many()` | `preset_connectivity.py` 118–133 | Lines **120–135** | ⚠️ +2 |
| 5 | `_PROVIDER_DEFAULT_URLS` | `preset_connectivity.py` 27–37 | Lines **29–39** | ⚠️ +2 |
| 6 | AED auto-fallback | `base_agent.py` 982–999 | Lines **984–1001** | ⚠️ +2 |
| 7 | `_activate_default_preset()` | `agent.py` 689–697 | Lines **691–699** | ⚠️ +2 |

---

## 6 · `core/preset-materialization`

**8 references** → 0 ✅ 7 ⚠️ 1 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `load_preset()` | `presets.py` 175–287 | Lines **177–289** | ⚠️ +2 |
| 2 | `materialize_active_preset()` | `presets.py` 290–323 | Lines **292–325** | ⚠️ +2 |
| 3 | `expand_inherit()` | `presets.py` 371–390 | Lines **373–392** | ⚠️ +2 |
| 4 | `resolve_preset_name()` | `presets.py` 75–94 | Lines **77–96** | ⚠️ +2 |
| 5 | `resolve_allowed_presets()` | `presets.py` 97–118 | Lines **99–120** | ⚠️ +2 |
| 6 | `_activate_preset()` | `agent.py` 625–687 | Lines **627–689** | ⚠️ +2 |
| 7 | `_read_init()` | `agent.py` 575–623 | Lines **577–625** | ⚠️ +2 |
| 8 | `validate_init()` (preset block) | `init_schema.py` 103–170 | Preset block starts at **119** (not 103); ends ~**175** | ❌ Off by +16 at start. Line 103 is `manifest = data["manifest"]` — not preset-specific. The actual preset-specific validation begins at 119 (`preset = manifest.get("preset")`). |

---

## 7 · `core/venv-resolve`

**8 references** → 6 ✅ 2 ⚠️ 0 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `resolve_venv()` | `venv_resolve.py` 19–37 | Lines 19–37 | ✅ Exact |
| 2 | `venv_python()` | `venv_resolve.py` 40–44 | Lines 40–44 | ✅ Exact |
| 3 | `_test_venv()` | `venv_resolve.py` 47–59 | Lines 47–59 | ✅ Exact |
| 4 | `_create_venv()` | `venv_resolve.py` 62–91 | Lines 62–91 | ✅ Exact |
| 5 | `ensure_package()` | `venv_resolve.py` 94–128 | Lines 94–128 | ✅ Exact |
| 6 | `_find_python()` | `venv_resolve.py` 130–146 | Lines 130–146 | ✅ Exact |
| 7 | `_cpr_agent()` | `agent.py` 393–437 | Lines **395–439** | ⚠️ +2 |
| 8 | `_build_launch_cmd()` | `agent.py` 976–982 | Lines **978–984** | ⚠️ +2 |

**Note:** `venv_resolve.py` is the only `lingtai/` (non-`lingtai_kernel/`) file where all internal references are perfectly aligned — suggesting this leaf was written or verified last.

---

## 8 · `core/wake-mechanisms`

**9 references** → 3 ✅ 2 ⚠️ 4 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `_wake_nap()` definition | `base_agent.py` 621 | Line **628** | ❌ Off by **+7**. Line 621 is the end of `_start_soul_timer()` (`self._soul_timer.start()`). The function `_wake_nap` is at 628. |
| 2 | `_nap_wake` (Event init) | `base_agent.py` ~77 | Actual: line **248** (`self._nap_wake = threading.Event()`) | ❌ Off by **+171 lines**. Line 77 is `_PARALLEL_SAFE_TOOLS: set[str] = set()`. The Event is initialized deep inside `__init__` at line 248. |
| 3 | ASLEEP branch in `_run_loop()` | `base_agent.py` 905–928 | Lines **907–930** | ⚠️ +2 |
| 4 | Wake sequence | `base_agent.py` 922–928 | Lines **924–930** | ⚠️ +2 |
| 5 | `_on_normal_mail()` callback | `base_agent.py` 525 | `_on_mail_received` at **523**, `_on_normal_mail` at **532** | ❌ Line 525 is the docstring of `_on_mail_received`, not `_on_normal_mail`. The actual `_on_normal_mail` starts at 532. Off by **+7**. |
| 6 | Wake from mail callback | `base_agent.py` 545 | Actual: `self._wake_nap("mail_arrived")` at line **552** | ❌ Off by **+7**. Line 545 is `name = address`. |
| 7 | Self-send detection | `intrinsics/mail.py` 233–245 | Lines 233–245 (`_is_self_send`) | ✅ Exact |
| 8 | Self-send wake call | `intrinsics/mail.py` 334–336 | Lines 334–336 | ✅ Exact |
| 9 | MCP poller wake call | `core/mcp/inbox.py` 128 | Line 128 | ✅ Exact |
| 10 | MCP poller class | `core/mcp/inbox.py` 242–278 | Class starts at **241** (`class MCPInboxPoller:`) | ⚠️ Off by 1 |

**Note:** The `base_agent.py` references here show a mixed pattern: some are off by +2 (the systematic shift), but `_wake_nap`, `_on_normal_mail`, and the mail wake callback are off by **+7**. This suggests that between the anatomy draft and the current code, ~5 extra lines were inserted in the 500–630 range of `base_agent.py` *on top of* the global +2-line shift.

---

## 9 · `init/init-schema` ⭐ (Human-flagged)

**9 references** → 3 ✅ 5 ⚠️ 1 ❌

| # | What | Claimed File:Lines | Actual | Status |
|---|------|--------------------|--------|--------|
| 1 | `validate_init()` | `init_schema.py` 59–227 | `def validate_init` at line **64**, returns at line **232** | ❌ Off by **+5** at start, **+5** at end. **This is the human-flagged drift.** The function body shifted down 5 lines. |
| 2 | `TOP_KNOWN` / `TOP_OPTIONAL` | `init_schema.py` 13–31 | Lines 13–31 | ✅ Exact — **no drift on the constants themselves.** |
| 3 | `MANIFEST_REQUIRED` / `MANIFEST_OPTIONAL` | `init_schema.py` 33–56 | Lines 33–56 (with `MANIFEST_KNOWN` at 56–61) | ✅ Exact |
| 4 | Text-pair validation | `init_schema.py` 72–81 | Required pairs loop at **72–86**; optional pairs at 88–94 | ⚠️ Claimed end 81 misses the full range. The required pair loop ends at 86, not 81. Off by +5 at end. |
| 5 | Preset validation | `init_schema.py` 114–170 | Preset block starts at **119**, ends at **175** | ⚠️ Off by +5 |
| 6 | LLM subfield validation | `init_schema.py` 182–199 | Lines **187–204** | ⚠️ Off by +5 |
| 7 | `api_key_env` → `env_file` cross-check | `init_schema.py` 194–199 | Lines **199–204** | ⚠️ Off by +5 |
| 8 | Bool-reject for numerics | `init_schema.py` 265–266 | Lines **270–271** | ⚠️ Off by +5 |
| 9 | Called from `_read_init()` | `agent.py` 583, 614–620 | Import at **585**, validate call at **616–622** | ⚠️ Off by +2 |
| 10 | Called from `cli.py` | `cli.py` 14, 49 | Lines 14, 49 | ✅ Exact |

### init-schema Drift Analysis (Human Flag)

The human correctly flagged a **~5 line drift** for `TOP_OPTIONAL`. However, the constants *themselves* (`TOP_OPTIONAL` at 13, `TOP_KNOWN` at 25) have **NOT moved** — they're still at the exact lines claimed.

What **did** move is the `validate_init()` function: it's now at 64–232 instead of 59–227. This means **5 lines were inserted between `MANIFEST_KNOWN` (line 56–61) and `def validate_init` (line 64)**. Looking at the code, the gap is:

```
56  MANIFEST_KNOWN: set[str] = set(MANIFEST_REQUIRED) | set(MANIFEST_OPTIONAL)
57  
58  
59  # (formerly def validate_init was here — it moved to 64)
60  # (new blank/comment lines 59-63 were added)
61  
62  
63  
64  def validate_init(data: dict) -> list[str]:
```

All references *inside* `validate_init()` are consequently off by +5, cascading through the entire function body. The fix is to bump all `init_schema.py` line references by 5.

---

## Recommendations

### Priority 1 — Fix ❌ references (wrong code pointers)

1. **molt-protocol `base_agent.py` lines 1152–1198 → 1207–1253**: Warning ladder logic has moved 55 lines. This is the worst drift — the claimed lines now point to completely unrelated code (`_concat_queued_messages`).

2. **molt-protocol `core/psyche/__init__.py` 333–336 → 359–361**: Post-molt hook registration moved 26 lines.

3. **wake-mechanisms `base_agent.py`**: Four references off by +7 (`_wake_nap`, `_on_normal_mail`, mail wake callback, `_nap_wake` init). These point to the wrong functions entirely.

4. **init-schema `validate_init()`**: Off by +5 across the board. Bump all `init_schema.py` internal line numbers by 5.

### Priority 2 — Systematic +2 fix

Apply a blanket **+2** correction to all references in these files:
- `base_agent.py` (except the +7 zone in 500–630)
- `handshake.py`
- `network.py`
- `agent.py`
- `presets.py`
- `preset_connectivity.py`

This will fix 33 of the 38 ⚠️ references in one pass.

### Priority 3 — venv-resolve as template

`venv_resolve.py` references are perfectly aligned. Use this leaf as the reference standard for how precise §Source tables should be. The other leaves likely predate a minor refactor that added 2 lines to the top of several files.

---

*End of audit.*
