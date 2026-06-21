## LingTai kernel v0.13.0

This release ships the post-v0.12.4 runtime/tooling update set from `v0.12.4..cfebe167`.

### Highlights
- Adds `read(max_chars=...)` plus stronger read/manual guidance for paginated and truncated file reads.
- Makes progressive disclosure first-class with `system.summarize`, always-present `_tool_result_metadata`, and dismissible large-tool-result reminders.
- Splits notification handling into the mandatory standalone `notification` tool and removes the old `system.notification` / `system.dismiss` aliases.
- Requires `session_journal_path` for agent-initiated `psyche.context.molt`, protecting durable session journals before context shedding.
- Persists actual canonical notification-block snapshots for TUI `/notification` history.
- Improves Codex cache-affinity and identity headers, including underscore `session_id` / `thread_id` compatibility and honest LingTai client-identity metadata to the Codex backend.
- Adds daemon terminal-state notifications and claude-p daemon token-usage reporting.
- Cleans internal i18n so operator/public strings stay localized while tool-schema/operating-instruction text falls back to English.

### Compatibility notes
- Agents and tools should now use the `notification` capability for notification check/dismiss operations; notification verbs are no longer exposed through `system`.
- Agent-initiated molt calls must provide a valid `session_journal_path`.

### Validation
- PYTHONPATH=src python -m pytest -q — 2536 passed, 4 skipped in 262.07s (0:04:22).
- `python -m build` completed after removing source-tree `__pycache__` directories.
- `python -m twine check dist/*` passed:
```text
Checking dist/lingtai-0.13.0-cp312-cp312-macosx_11_0_arm64.whl: PASSED
Checking dist/lingtai-0.13.0.tar.gz: PASSED
```
- Artifact SHA-256:
```text
8fda5e36f6daeb216a6257422235e94c4d96130d26e2f37cd4fcafedd5453f93  dist/lingtai-0.13.0-cp312-cp312-macosx_11_0_arm64.whl
f8e9f8d34e50a884b59ab117aa299b48c0ea0b850b97fdb090b1915644deb758  dist/lingtai-0.13.0.tar.gz
```
