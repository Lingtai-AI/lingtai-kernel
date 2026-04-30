# Audit: §Source References — daemon + mcp Leaves

**Auditor:** audit-daemon-mcp  
**Date:** 2026-04-30  
**Kernel source:** `/Users/huangzesen/Documents/GitHub/lingtai-kernel/src/lingtai/`  
**Leaves base:** `leaves/capabilities/`

---

## 1. daemon/dual-ledger/README.md

**Format:** Name-anchored (`file.py::FunctionName()`), no line numbers.

| # | Reference | Status | Notes |
|---|---|---|---|
| 1 | `run_dir.py::DaemonRunDir.append_tokens()` | ✅ | Confirmed at `core/daemon/run_dir.py:298`. Dual-write logic with `extra={source, em_id, run_id}` for parent ledger. |
| 2 | `run_dir.py::DaemonRunDir.__init__()` | ✅ | Confirmed at `core/daemon/run_dir.py:34`. `_parent_token_ledger` set at line 53 from `parent_working_dir`. |
| 3 | `run_dir.py::DaemonRunDir._safe()` | ✅ | Confirmed at `core/daemon/run_dir.py:167`. Swallows `OSError`, optionally forwards via `log_callback`. |
| 4 | `__init__.py::_run_emanation()` | ✅ | Confirmed at `core/daemon/__init__.py:336`. Session created with `tracked=False` at line 388. |
| 5 | `__init__.py::_accum()` | ✅ | Confirmed at `core/daemon/__init__.py:393`. Reads `resp.usage` (line 394), calls `run_dir.append_tokens()` (line 397). |
| 6 | `lingtai_kernel/token_ledger.py::append_token_entry()` | ✅ | Confirmed at `token_ledger.py:13`. Shared JSONL append helper with `extra` dict support. |
| 7 | `lingtai_kernel/token_ledger.py::sum_token_ledger()` | ✅ | Confirmed at `token_ledger.py:61`. Sums all entries without source filtering. |

**Summary:** 7/7 ✅ · 0 ⚠️ · 0 ❌

---

## 2. daemon/followup-injection/README.md

**Format:** Name-anchored (`file.py::FunctionName()`), no line numbers.

| # | Reference | Status | Notes |
|---|---|---|---|
| 1 | `__init__.py::_run_emanation()` — intermediate text → `_notify_parent()` | ✅ | Confirmed at `__init__.py:336`. Lines 419-420: `if response.text: self._notify_parent(em_id, response.text)`. |
| 2 | `__init__.py::_run_emanation()` — follow-up drain gate | ✅ | Confirmed at `__init__.py:458`: `if not response.tool_calls:` → `_drain_followup(em_id)`. Matches "only after text-only responses". |
| 3 | `__init__.py::_notify_parent()` | ✅ | Confirmed at `__init__.py:474`. Builds `f"[daemon:{em_id}]\n\n{text}"` prefix, puts on `agent.inbox`. |
| 4 | `__init__.py::_drain_followup()` | ✅ | Confirmed at `__init__.py:480`. Uses `followup_lock`, clears buffer to `""`, returns `text or None`. |
| 5 | `__init__.py::_handle_ask()` | ✅ | Confirmed at `__init__.py:723`. Line 731: concatenates with `"\n\n"` when buffer non-empty. |
| 6 | `__init__.py::_on_emanation_done()` | ✅ | Confirmed at `__init__.py:833`. Truncation at line 848 (`_max_result_chars`), suppression at line 852 (`_notify_threshold`). |
| 7 | `__init__.py::_NOTIFY_MIN_LEN` | ✅ | Confirmed at `__init__.py:132`. `_NOTIFY_MIN_LEN = 20`. Class constant on `DaemonManager`. |
| 8 | `__init__.py::_max_result_chars` | ✅ | Confirmed at `__init__.py:143`. Constructor parameter, default 2000 (line 136). |
| 9 | `run_dir.py::DaemonRunDir.record_user_send()` | ✅ | Confirmed at `run_dir.py:194`. Appends to `chat_history.jsonl` with `kind ∈ {"task", "tool_results", "followup"}`. |

**Summary:** 9/9 ✅ · 0 ⚠️ · 0 ❌

---

## 3. daemon/pre-send-health/README.md

**Format:** Name-anchored (`file.py::FunctionName()`), no line numbers.

| # | Reference | Status | Notes |
|---|---|---|---|
| 1 | `__init__.py::_handle_emanate()` | ✅ | Confirmed at `__init__.py:490`. Orchestrates capacity gate → preset validation → tool surface → filesystem construction. |
| 2 | `__init__.py::DaemonManager.__init__()` | ✅ | Confirmed at `__init__.py:134`. Stores `_max_emanations` (138), `_max_turns` (139), `_timeout` (140). |
| 3 | `__init__.py::_instantiate_preset_capabilities()` | ✅ | Confirmed at `__init__.py:249`. Uses `_ToolCollector` sandbox at line 292. |
| 4 | `__init__.py::_build_tool_surface()` | ✅ | Confirmed at `__init__.py:174`. Group expansion + `EMANATION_BLACKLIST` filter + existence check. |
| 5 | `__init__.py::_ToolCollector` | ✅ | Confirmed at `__init__.py:37`. Proxy class intercepting `add_tool()` into local dicts. |
| 6 | `__init__.py::EMANATION_BLACKLIST` | ✅ | Confirmed at `__init__.py:34`. Exact value: `{"daemon", "avatar", "psyche", "library"}`. |
| 7 | `run_dir.py::DaemonRunDir.__init__()` | ✅ | Confirmed at `run_dir.py:34`. `mkdir` (70), `daemon.json` (99), `.prompt` (100), `.heartbeat` (101), `daemon_start` event (102-103). |
| 8 | `lingtai/preset_connectivity.py::check_connectivity()` | ✅ | Confirmed at `preset_connectivity.py:65`. Live LLM endpoint probe. |

**Summary:** 8/8 ✅ · 0 ⚠️ · 0 ❌

---

## 4. daemon/max-rpm-gating/README.md

**Format:** Name-anchored (`file.py::FunctionName()`), no line numbers.

| # | Reference | Status | Notes |
|---|---|---|---|
| 1 | `__init__.py::DaemonManager.__init__()` — `max_emanations` parameter | ✅ | Confirmed at `__init__.py:134`. `max_emanations: int = 4`. |
| 2 | `__init__.py::_handle_emanate()` — prune + count + capacity check | ✅ | Confirmed at `__init__.py:490`. Prune at 530-532, count at 535, capacity check at 536. |
| 3 | `__init__.py::_handle_reclaim()` — cancel all, clear registry, reset `_next_id` | ✅ | Confirmed at `__init__.py:821`. Cancel at 824-826, clear at 828, reset `_next_id` at 829. |
| 4 | `__init__.py::_watchdog()` — timeout thread | ✅ | Confirmed at `__init__.py:858`. Sets `timeout_event` then `cancel_event` (lines 871-872). |
| 5 | `__init__.py::setup()` — wires `max_emanations` | ✅ | Confirmed at `__init__.py:880`. `max_emanations` passed to `DaemonManager` at line 885. |

**Summary:** 5/5 ✅ · 0 ⚠️ · 0 ❌

---

## 5. mcp/inbox-listener/README.md

**Format:** Line-number table (`file:lines`).

| # | Reference | Claimed Lines | Actual Lines | Status | Notes |
|---|---|---|---|---|---|
| 1 | Constants (`INBOX_DIRNAME`, `POLL_INTERVAL`, `MAX_EVENTS_PER_CYCLE`) | 50-58 | 50-58 | ✅ | `INBOX_DIRNAME` at 50, `POLL_INTERVAL` at 56, `MAX_EVENTS_PER_CYCLE` at 57, `_MAX_SUBJECT_LEN` at 58. |
| 2 | `validate_event()` | 65-96 | 65-96 | ✅ | Exact match. |
| 3 | `_format_notification()` | 103-116 | 103-116 | ✅ | Exact match. |
| 4 | `_dispatch_event()` | 119-136 | 119-136 | ✅ | Exact match. Last line 136 closes `agent._log(...)`. |
| 5 | `_dead_letter()` | 143-159 | 143-159 | ✅ | Exact match. |
| 6 | `_scan_once()` | 170-234 | 170-234 | ✅ | Exact match. Returns `dispatched` at line 234. |
| 7 | `MCPInboxPoller` class | 241-284 | 241-284 | ✅ | Exact match. `stop()` ends at line 284. |
| 8 | Poller started in `Agent.start()` | 440-444 | 441-446 | ⚠️ | `start()` at 441, import at 444, instantiation at 445, `.start()` at 446. Claimed range 440-444 misses lines 445-446 (instantiation + start). |
| 9 | Poller stopped in `Agent.stop()` | 545-552 | 546-554 | ⚠️ | `stop()` at 546, poller retrieval at 549, `poller.stop()` at 552, except block continues to 554. Claimed range ends 2 lines early. |
| 10 | LICC env injection (`LINGTAI_AGENT_DIR`) | 308-310 | 307-312 | ⚠️ | Comment starts at 307, `licc_env = {` at 310, `"LINGTAI_AGENT_DIR"` at 311. Actual key assignment is at line 311, not 310. Off by 1. |
| 11 | `LINGTAI_MCP_NAME` per-spawn injection | 328-330 | 328-332 | ⚠️ | Comment at 328-329, `merged_env = {` at 330, `**licc_env,` at 331, `"LINGTAI_MCP_NAME": name,` at 332. Actual assignment is at line 332, not 330. Off by 2. |

**Summary:** 7 ✅ · 4 ⚠️ · 0 ❌

---

## 6. mcp/capability-discovery/README.md

**Format:** Line-number table (`file:lines`).

| # | Reference | Claimed Lines | Actual Lines | Status | Notes |
|---|---|---|---|---|---|
| 1 | Constants (`REGISTRY_FILENAME`, `_NAME_RE`, `_VALID_TRANSPORTS`) | 37-43 | 37-43 | ✅ | `REGISTRY_FILENAME` at 37, `_NAME_RE` at 41, `_VALID_TRANSPORTS` at 42, `_MAX_SUMMARY_LEN` at 43. |
| 2 | `_load_catalog()` | 53-75 | 53-75 | ✅ | Exact match. |
| 3 | `validate_record()` | 82-125 | 82-125 | ✅ | Exact match. |
| 4 | `validate_registry_line()` | 128-138 | 128-138 | ✅ | Exact match. |
| 5 | `read_registry()` | 149-186 | 149-186 | ✅ | Exact match. |
| 6 | `_append_record()` | 189-194 | 189-194 | ✅ | Exact match. |
| 7 | `decompress_addons()` | 201-257 | 201-257 | ✅ | Exact match. Returns report dict at lines 252-257. |
| 8 | `_build_registry_xml()` | 274-299 | 274-299 | ✅ | Exact match. |
| 9 | `_reconcile()` | 306-342 | 306-342 | ✅ | Exact match. |
| 10 | `setup()` | 382-406 | 382-406 | ✅ | Exact match. End of file. |
| 11 | `_load_mcp_from_workdir()` | 274-389 | 276-393 | ⚠️ | Function starts at 276 (not 274), ends at 393 (not 389). Off by 2 at start, 4 at end. |
| 12 | Registry gating in loader | 371-388 | 373-391 | ⚠️ | Gating logic starts at 373 (cross-ref comment), warning continues to 391. Off by 2-3 lines. |
| 13 | LICC env injection | 308-310, 328-330 | 307-312, 328-332 | ⚠️ | Same drift as inbox-listener. `LINGTAI_AGENT_DIR` key at 311 (not 310), `LINGTAI_MCP_NAME` at 332 (not 330). |

**Summary:** 10 ✅ · 3 ⚠️ · 0 ❌

---

## 7. mcp/licc-roundtrip/README.md

**Format:** Line-number table (`file:lines`).

| # | Reference | Claimed Lines | Actual Lines | Status | Notes |
|---|---|---|---|---|---|
| 1 | Reference client (`push_inbox_event()`) | Anatomy ref §5 | N/A (external) | ✅ | Correctly noted as vendored into each MCP repo, not in kernel source. |
| 2 | Atomic write protocol (docstring) | 1-33 | 1-33 | ✅ | Docstring spans lines 1-33. |
| 3 | `_scan_once()` | 170-234 | 170-234 | ✅ | Exact match. |
| 4 | `validate_event()` | 65-96 | 65-96 | ✅ | Exact match. |
| 5 | `_dispatch_event()` | 119-136 | 119-136 | ✅ | Exact match. |
| 6 | `_format_notification()` | 103-116 | 103-116 | ✅ | Exact match. |
| 7 | `_dead_letter()` | 143-159 | 143-159 | ✅ | Exact match. |
| 8 | `MCPInboxPoller` | 241-284 | 241-284 | ✅ | Exact match. |
| 9 | LICC env vars injected at spawn | 308-310, 328-330 | 307-312, 328-332 | ⚠️ | Same drift as inbox-listener. |
| 10 | Poller started in `Agent.start()` | 440-444 | 441-446 | ⚠️ | Same drift as inbox-listener. |
| 11 | Poller stopped before MCP clients | 545-552 | 546-554 | ⚠️ | Same drift as inbox-listener. |

**Summary:** 8 ✅ · 3 ⚠️ · 0 ❌

---

## Overall Summary

| Leaf | ✅ | ⚠️ | ❌ | Total |
|---|---|---|---|---|
| daemon/dual-ledger | 7 | 0 | 0 | 7 |
| daemon/followup-injection | 9 | 0 | 0 | 9 |
| daemon/pre-send-health | 8 | 0 | 0 | 8 |
| daemon/max-rpm-gating | 5 | 0 | 0 | 5 |
| mcp/inbox-listener | 7 | 4 | 0 | 11 |
| mcp/capability-discovery | 10 | 3 | 0 | 13 |
| mcp/licc-roundtrip | 8 | 3 | 0 | 11 |
| **Totals** | **54** | **10** | **0** | **64** |

### Findings

**No ❌ (broken) references found.** Every function/class/constant referenced in all 7 leaves exists in the kernel source at or near the claimed location.

**All ⚠️ findings are in `agent.py` line-number references.** The 4 daemon leaves use name-anchoring (`file.py::FunctionName()`) and are immune to line drift — all 29 references are ✅. The 3 MCP leaves use line-number tables, and the `agent.py` references have drifted by 1-4 lines:

- **`agent.py:308-310` (LICC env injection):** `LINGTAI_AGENT_DIR` key is at line 311, not 310. Off by 1.
- **`agent.py:328-330` (`LINGTAI_MCP_NAME` injection):** Actual assignment at line 332, not 330. Off by 2.
- **`agent.py:440-444` (poller started in `Agent.start()`):** Instantiation at 445, `.start()` at 446. Claimed range misses the last 2 lines.
- **`agent.py:545-552` (poller stopped in `Agent.stop()`):** Exception block continues to 554. Off by 2.
- **`agent.py:274-389` (`_load_mcp_from_workdir`):** Function starts at 276, ends at 393. Off by 2-4.
- **`agent.py:371-388` (registry gating):** Starts at 373, warning continues to 391. Off by 2-3.

**All `core/mcp/inbox.py` and `core/mcp/__init__.py` line references are exact.** The drift is confined to `agent.py`, which is a large file (984 lines) more susceptible to insertions above the referenced sections.

### Recommendation

The 10 ⚠️ references are all acceptably close (≤4 lines off) and the referenced code still exists at the nearby lines. No immediate fix is required, but a future pass could tighten the `agent.py` line numbers. The daemon leaves' name-anchoring strategy is demonstrably more resilient.
