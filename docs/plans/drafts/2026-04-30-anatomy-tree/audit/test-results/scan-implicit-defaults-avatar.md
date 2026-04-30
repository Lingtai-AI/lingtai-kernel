# Scan: Implicit Defaults in Avatar Contracts

**Scanner:** `test-avatar`
**Date:** 2026-04-30
**Trigger:** ADR-001 (symlink follow) revealed that `shutil.copytree`'s undocumented default was a real finding. This scan asks: where else in the four avatar leaves do "copy", "create", "write", or "default" appear without explicit documentation of the default behavior?

**Scope:** `spawn/`, `boot-verification/`, `shallow-vs-deep/`, `handshake-files/` README.md + source lines they reference.

**Method:** Grep for verbs (`copy`, `create`, `write`, `append`, `read`, `check`, `poll`) in each README, then cross-reference source code for implicit defaults.

---

## Findings

### ✅ RESOLVED

| # | Contract text | Implicit default | Status |
|---|---|---|---|
| 1 | `shutil.copytree` (shallow-vs-deep §3) | `symlinks=False` — symlinks followed | **ADR-001**, contract updated |

### ⚠️ REVIEW — worth documenting

| # | Contract text | Implicit default | Risk | Recommendation |
|---|---|---|---|---|
| 2 | `.agent.heartbeat` "plain text file containing a single Unix timestamp as a float" (handshake-files §2) | Written by `time.time()` — **wall clock**, not monotonic. Vulnerable to NTP jumps. | Low — NTP adjustments are typically small; 2.0s liveness threshold absorbs drift. A large backward jump could make heartbeat appear permanently fresh; forward jump could kill a healthy agent. | **RESOLVED** — Source comment added to `base_agent.py:716` explaining deliberate choice: monotonic is per-process and meaningless across PIDs; only wall clock survives IPC boundary. NTP acceptable at 2.0s threshold. |
| 3 | `write_text()` for `.prompt`, `init.json`, heartbeat (spawn lines 245-251, handshake line 721) | Uses **platform default encoding** (`locale.getpreferredencoding()`). UTF-8 on macOS/Linux; could differ on Windows. | Very low — LingTai targets macOS/Linux. But if ported to Windows with non-UTF-8 locale, non-ASCII prompt content could corrupt. | **RESOLVED** — All `write_text()` calls in `avatar/__init__.py` (lines 245, 252) and `base_agent.py` (line 727) now have explicit `encoding="utf-8"`. Reader in `handshake.py` (line 54) likewise. |
| 4 | `open(ledger_path, 'a')` + `json.dumps(record, ensure_ascii=False)` (spawn line 118-119) | Append mode, no encoding arg. `ensure_ascii=False` means non-ASCII mission text written as raw Unicode. | Very low — same platform encoding concern. `ensure_ascii=False` is intentional for Chinese missions but not documented. | **DEFERRED** — Low risk, add encoding to next contract revision. |
| 5 | `str(self._heartbeat)` → heartbeat file (handshake line 721) | Float precision is **Python's default `str()`** — typically 12-17 significant digits. No rounding. | Negligible — `float(hb.read_text().strip())` parses it back correctly. But precision is not contractually bounded. | **DEFERRED** — Low risk, informational note in next contract revision. |
| 6 | stderr capture: `stderr_fh.open('wb')` + last 2000 bytes read as `raw.decode('utf-8', errors='replace')` (spawn lines 523, 344) | Binary write + UTF-8 decode with `errors='replace'`. | Very low — `errors='replace'` prevents crash on non-UTF-8 stderr. 2000-byte cap is documented. | **DEFERRED** — Already well-documented. No action. |
| 7 | `proc.poll()` for exit detection (boot-verification line 332) | Returns exit code, but contract only says "returns non-None" — exit code value is not recorded in ledger or returned to caller. | Low — caller gets stderr tail for diagnostics, but exit code is lost. | **DEFERRED** — Consider adding `exit_code` to ledger in next contract revision. |

### ✅ OK — implicit but safe/obvious

| # | Contract text | Implicit default | Verdict |
|---|---|---|---|
| 8 | `heartbeat.is_file()` — "existence only, content not inspected" | Explicitly documented. | ✓ |
| 9 | `start_new_session=True, stdin=DEVNULL, stdout=DEVNULL` | Fully detached process — child gets new session, no stdin. Documented as "Fully detached." | ✓ |
| 10 | `fcntl.flock()` / `msvcrt.locking()` | OS-level exclusive lock. Platform-specific but standard. | ✓ |
| 11 | `atomic rename via .agent.json.tmp` | Explicitly documented. | ✓ |
| 12 | `shutil.copy2` for `combo.json` | Preserves metadata (timestamps, permissions). Standard Python. | ✓ |
| 13 | `dst.parent == src.parent` scope guard | Explicitly documented. | ✓ |

---

## Summary

- **1 resolved** (ADR-001: symlinks)
- **2 resolved** (items 2-3: source patches applied)
  - `base_agent.py:716` — source comment explaining deliberate wall-clock choice
  - `avatar/__init__.py:245,252` + `base_agent.py:727` + `handshake.py:54` — explicit `encoding="utf-8"` on all write_text/read_text calls
- **4 deferred** (items 4-7) — low risk, marked for next contract revision
- **6 safe/obvious** — no action needed

**Patches applied:**

| File | Line | Change |
|---|---|---|
| `src/lingtai/core/avatar/__init__.py` | 245 | `write_text(..., encoding="utf-8")` |
| `src/lingtai/core/avatar/__init__.py` | 252 | `write_text(first_prompt, encoding="utf-8")` |
| `src/lingtai_kernel/base_agent.py` | 716 | Source comment: wall clock deliberate (cross-process IPC) |
| `src/lingtai_kernel/base_agent.py` | 727 | `write_text(..., encoding="utf-8")` |
| `src/lingtai_kernel/handshake.py` | 54 | `read_text(encoding="utf-8")` |

**Deferred items (for next contract revision):**
- Item 4: ledger encoding (low risk — `ensure_ascii=False` is intentional)
- Item 5: heartbeat float precision (negligible — Python roundtrip is stable)
- Item 6: stderr capture (already well-documented)
- Item 7: exit code in ledger (informational improvement)

**Action:** This scan is a checklist for the anatomy author. All items are either resolved or have a concrete deferred path.
