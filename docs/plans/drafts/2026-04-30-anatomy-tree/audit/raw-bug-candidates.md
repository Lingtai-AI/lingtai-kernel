# Raw Kernel Bug Candidates

**Collected:** 2026-04-30
**Sources:** 9 avatar test/audit reports + implicit-defaults scan + tmp-cleanup proposal
**Filter:** Kernel source code bugs ONLY (`src/lingtai/` or `src/lingtai_kernel/`), excluding anatomy §Source line-number drift

---

## Candidate #1 — Bash sandbox rejects relative `working_dir`

- **Category:** CONFIRMED
- **Symptom:** Passing `working_dir="./logs"` to bash returns error "must be under agent working directory", even though `./logs` IS a subdirectory of the agent's working dir.
- **Source file:** `src/lingtai/core/bash/__init__.py` lines 176–187 (`BashManager.handle()` — path validation)
- **Evidence:** test-result-bash §3.3. `Path("./logs").resolve()` uses the **Python process cwd** (interpreter's cwd, typically project root), NOT the agent's working directory. So `./logs` resolves to something outside the sandbox. The error message is misleading — it claims the path is outside the sandbox when it actually IS inside but resolves wrong.
- **Severity:** Medium — relative `working_dir` is silently broken; error message misleads users.
- **Suggested fix:** Anchor relative paths to agent dir before resolving: `resolved = Path(self._working_dir) / cwd` then `.resolve()`. Or require absolute paths and update the README.

---

## Candidate #2 — Parallel bash calls all fail with `health_check:pre_send_pairing`

- **Category:** SUSPECTED
- **Symptom:** Dispatching 4 independent bash calls in a single turn causes ALL 4 to fail with `"tool execution either timed out, errored, or was interrupted before its result could be committed"`. Sequential calls work fine.
- **Source file:** Kernel runtime loop — `health_check:pre_send_pairing` logic (exact location not pinned in reports)
- **Evidence:** test-result-bash §6.1. All 4 parallel calls failed; each succeeded individually on retry.
- **Severity:** Low — workaround is sequential calls. But surprising since daemon tool spawns 4 parallel workers by default.
- **Suggested fix:** Investigate `health_check:pre_send_pairing` logic in the kernel runtime loop to determine if parallel tool calls from the same turn are intentionally unsupported or if there's a race condition.

---

## Candidate #3 — `system(show)` does not surface lifecycle `state` field

- **Category:** SUSPECTED
- **Symptom:** `system(show)` returns identity + runtime + tokens but no `state` field (e.g., `"state": "active"`). The README contract implies state should be observable here. `.status.json` also lacks it. State is only in `.agent.json`.
- **Source file:** `src/lingtai_kernel/base_agent.py` — `system(show)` handler and `_build_manifest()` (exact handler location not pinned)
- **Evidence:** test-result-core-init §1 (T1, T2). Both `system(show)` and `.status.json` lack a `state` field. `.agent.json` has it. Contract suggests it should be surfaced.
- **Severity:** Low — functionality works, but observability is incomplete. An agent cannot self-inspect its lifecycle state without reading `.agent.json` from disk.
- **Suggested fix:** Add `"state": self._state.value` to the `system(show)` output dict in the kernel's system tool handler.

---

## Candidate #4 — Bash output truncation suffix format and unit mismatch

- **Category:** SUSPECTED
- **Symptom:** README contract says truncation suffix is `"\n... (truncated, {total} chars total)"` but actual suffix is `"[truncated — 50035 bytes total]"` (bracket, em-dash, "bytes" instead of "chars").
- **Source file:** `src/lingtai/core/bash/__init__.py` lines 200–203 (output truncation logic)
- **Evidence:** test-result-bash §5.1. The format string in the code differs from what the README documents. Also, truncation is in bytes not chars — for ASCII they're identical, but for multibyte UTF-8 (CJK etc.) they diverge.
- **Severity:** Low — functionally correct (truncation works), but downstream parsers expecting the documented format will break. The chars-vs-bytes distinction matters for non-ASCII output.
- **Suggested fix:** Either update the README to match the actual suffix format, or update the code to match the README. Clarify whether the cap is in chars or bytes.

---

## Candidate #5 — Error return shape inconsistency across file tools

- **Category:** DESIGN_CONCERN
- **Symptom:** `read` returns errors as `{status: "error", message: ...}` while `write`, `edit`, `glob`, and `grep` return errors as `{error: ...}`. Callers must handle two different error shapes.
- **Source file:** Multiple — `src/lingtai/core/file_io/__init__.py` (read handler), `src/lingtai/core/bash/__init__.py` (write/edit/glob/grep handlers)
- **Evidence:** test-result-file-tools §总结 (统一性观察). read uses `status: "error" + message`; write/edit/glob/grep use `error`. Two formats coexist.
- **Severity:** Low — doesn't break functionality, but requires dual parsing in consumers.
- **Suggested fix:** Standardize on one error shape across all file tools. Recommend `{status: "error", message: ...}` to match the `read` pattern.

---

## Candidate #6 — Mail `from` display inconsistency between `check` and `read`

- **Category:** DESIGN_CONCERN
- **Symptom:** `email(check)` shows `from` as `"test-bash (test-bash)"` (agent_name enriched) while `email(read)` shows `from` as `"test-bash"` (raw address). The difference is undocumented.
- **Source file:** `src/lingtai_kernel/intrinsics/mail.py` lines 214–217 (`_message_summary`) vs `src/lingtai/core/email/__init__.py` line 344/351 (`_inject_identity`)
- **Evidence:** test-result-mail §1.3 observation. `check` uses `_inject_identity()` enriched format; `read` uses raw `from` field.
- **Severity:** Low — by-design (read returns raw data, check returns display-optimized), but should be documented.
- **Suggested fix:** Document the difference in the mail README. Optionally add an `identity` field to the `read` output for consistency.

---

## Candidate #7 — Orphaned `.tmp` files on crash (no startup reconciliation)

- **Category:** DESIGN_CONCERN
- **Symptom:** If the agent process crashes after writing `message.json.tmp` but before `os.replace()`, the `.tmp` file is orphaned in the mailbox forever. No reconciliation on restart.
- **Source file:** `src/lingtai_kernel/services/mail.py` lines 198–207 (atomic write in `FilesystemMailService.send()`) and `src/lingtai/core/email/__init__.py` line ~1304 (`setup()`)
- **Evidence:** proposal-cleanup-tmp-on-startup.md. The atomic-write leaf explicitly documents this as "What it does NOT protect against".
- **Severity:** Low — requires a crash at the exact wrong moment. But orphans accumulate over time with no cleanup mechanism.
- **Suggested fix:** Add `_cleanup_tmp_orphans(max_age_s=300)` to `EmailManager.setup()`, called after `_reconcile_schedules_on_startup()`. Implementation provided in the proposal.

---

## Candidate #8 — Grandchild process leak on bash timeout (no process-group kill)

- **Category:** CONFIRMED (documented limitation)
- **Symptom:** When a bash command times out, only the direct child shell is killed. Background/grandchild processes survive as orphans. E.g., `bash -c 'sleep 300 & echo "child_pid=$!"'` with 2s timeout leaves `sleep 300` running.
- **Source file:** `src/lingtai/core/bash/__init__.py` lines 190–212 (`subprocess.run()` with `shell=True`, `TimeoutExpired` catch)
- **Evidence:** test-result-bash §2.3. PID 29131 (`sleep 300`) confirmed still running after timeout; manually killed.
- **Severity:** Medium — real operational risk for commands with backgrounded children. README correctly documents as "NOT implemented."
- **Suggested fix:** Use `preexec_fn=os.setsid` to create a process group, then `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` in the timeout handler. Requires careful testing for cross-platform compatibility.

---

## Candidate #9 — Mail scheduling: failed sends count toward total

- **Category:** DESIGN_CONCERN
- **Symptom:** When a scheduled send fails (error or "blocked" by dedup), the `sent` counter is still incremented (at lines 720–722) and the interval timer resets. A schedule can "complete" (reach `count` total) without successfully delivering all messages.
- **Source file:** `src/lingtai/core/email/__init__.py` lines 720–722 (increment-before-send) and lines 742–746 (post-send handling)
- **Evidence:** audit-avatar-mail.md semantic audit of `mail/scheduling`. The at-most-once guarantee increments the counter before the send result is known. Failed sends are counted.
- **Severity:** Low-Medium — a schedule with intermittent failures (e.g., target agent down) will "complete" early, losing messages. The at-most-once design prevents duplicates but doesn't guarantee delivery.
- **Suggested fix:** Consider moving the counter increment to after successful send (changes semantics to at-least-once for the counter), or add a `failed` field to the schedule record for observability.

---

## Candidate #10 — `read` error leaks codec internals for binary files

- **Category:** DESIGN_CONCERN
- **Symptom:** Reading a binary file returns `"'utf-8' codec can't decode byte 0x80 in position 128: invalid start byte"` — exposing Python codec internals rather than a generic "binary file" error.
- **Source file:** `src/lingtai/core/file_io/__init__.py` (read handler, error path)
- **Evidence:** test-result-file-tools §2 (R5). Contract says "raise a generic read error" but implementation leaks the underlying exception message. Behavior is correct (the error IS caught), but "generic" implies a cleaner message.
- **Severity:** Very Low — cosmetic. Doesn't affect functionality.
- **Suggested fix:** Catch `UnicodeDecodeError` specifically and return a cleaner message like "Cannot read <path>: file appears to be binary (not valid UTF-8)".

---

## Candidate #11 — Deep avatar `system/` copy is a no-op (child re-materializes)

- **Category:** NON_BUG
- **Symptom:** `_prepare_deep()` calls `shutil.copytree('system/')` to copy the parent's system directory, but the child process re-materializes its own `system/` on boot, overwriting the copy.
- **Source file:** `src/lingtai/core/avatar/__init__.py` lines 437–484 (`_prepare_deep`)
- **Evidence:** test-result-avatar §3c. Both shallow and deep avatars end up with identical `system/` content.
- **Severity:** None — this is defense-in-depth, not a bug. The copy is harmless (extra I/O on spawn).
- **Suggested fix:** No code fix needed. Could add a comment in `_prepare_deep` noting the `system/` copy is redundant but kept for defense-in-depth.

---

## Candidate #12 — MCP servers silently inactive when `mcp` capability not declared

- **Category:** NON_BUG
- **Symptom:** If `init.json` has MCP server configs but the agent's `capabilities` array doesn't include `["mcp", {}]`, all MCP servers are silently inert — no error, no warning visible to the agent.
- **Source file:** By design — kernel MCP lifecycle gating
- **Evidence:** test-result-psyche-daemon-mcp §7. Design constraint: agents opt in to MCP via capability declaration.
- **Severity:** None — this is intentional design. But the silent inactivity could confuse developers.
- **Suggested fix:** No code fix. The report already provides an ⚡铁律 note for future readers.

---

## Candidate #13 — `.tmp` orphan cleanup script exists but is manual-only

- **Category:** DESIGN_CONCERN
- **Symptom:** A standalone `cleanup_tmp_orphans.py` script exists in the audit test-results/scripts directory, but agents don't have automatic startup reconciliation for `.tmp` orphans.
- **Source file:** `audit/test-results/scripts/cleanup_tmp_orphans.py` (diagnostic tool) + `src/lingtai/core/email/__init__.py` (missing integration)
- **Evidence:** proposal-cleanup-tmp-on-startup.md proposes adding `_cleanup_tmp_orphans()` to `EmailManager.setup()`.
- **Severity:** Low — same root cause as Candidate #7. The proposal provides a complete implementation.
- **Suggested fix:** Integrate `_cleanup_tmp_orphans()` into `EmailManager.setup()` after `_reconcile_schedules_on_startup()`.

---

## Summary Table

| # | Category | Severity | Component | Brief |
|---|----------|----------|-----------|-------|
| 1 | **CONFIRMED** | Medium | bash/sandbox | Relative `working_dir` resolves against process cwd, not agent dir |
| 2 | **SUSPECTED** | Low | kernel runtime | Parallel bash calls fail with health_check error |
| 3 | **SUSPECTED** | Low | system(show) | Lifecycle `state` not surfaced in system(show) or .status.json |
| 4 | **SUSPECTED** | Low | bash/truncation | Suffix format and unit (chars vs bytes) differ from contract |
| 5 | DESIGN_CONCERN | Low | file tools | Error shape inconsistency across read vs write/edit/glob/grep |
| 6 | DESIGN_CONCERN | Low | mail | `from` display differs between check (enriched) and read (raw) |
| 7 | DESIGN_CONCERN | Low | mail/atomic-write | Orphaned `.tmp` files on crash, no startup cleanup |
| 8 | **CONFIRMED** | Medium | bash/kill | Grandchild process leak on timeout (no process-group kill) |
| 9 | DESIGN_CONCERN | Low-Med | mail/scheduling | Failed sends increment counter, schedule can "complete" early |
| 10 | DESIGN_CONCERN | Very Low | file/read | Binary file error leaks Python codec internals |
| 11 | NON_BUG | — | avatar/deep-copy | `system/` copy is redundant (child re-materializes) |
| 12 | NON_BUG | — | mcp | Silent inactivity when capability not declared (by design) |
| 13 | DESIGN_CONCERN | Low | mail/atomic-write | Same as #7, with implementation proposal available |

**Actionable kernel bugs: #1 (CONFIRMED), #8 (CONFIRMED, documented), #2 (SUSPECTED, needs investigation)**
**Design concerns worth tracking: #5, #6, #7/#13, #9**
**Not bugs: #11, #12**

---

## Note on Dark Parts Catalog

The audit-report.md references a "Dark Parts Catalog (15 findings including 3 Critical)" produced by audit-llm. The catalog file was not found in the audit directory or in the leaves-llm-providers directory. The audit-llm.md report focused on §Source line-number drift (anatomy issues, excluded from this document). The 3 Critical findings from the Dark Parts Catalog may contain additional kernel bugs but cannot be included here without access to the catalog content. Recommend locating or regenerating this catalog.
