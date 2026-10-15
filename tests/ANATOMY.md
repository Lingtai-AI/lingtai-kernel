---
related_files:
  - src/lingtai/tools/plugin/ANATOMY.md
  - tests/test_plugin_mcp_mode.py
  - ENVIRONMENT_VARIABLES.md
  - ANATOMY.md
  - CONTRACT.md
  - AGENTS.md
  - dev-guide-skill/SKILL.md
  - pyproject.toml
  - tests/BEHAVIORS.md
  - tests/CONTRACT.md
  - tests/__init__.py
  - tests/_molt_helpers.py
  - tests/_tool_plugin_helpers.py
  - tests/test_acp_stdio.py
  - tests/test_avatar_rules.py
  - tests/test_context_ownership_redesign.py
  - tests/test_correlated_turns.py
  - tests/test_daemon_check.py
  - tests/test_daemon_check_historical.py
  - tests/test_daemon_checkpoint.py
  - tests/test_daemon_claude_p_submanual.py
  - tests/test_daemon_per_batch_limits.py
  - tests/test_daemon_qwen_code_submanual.py
  - tests/test_daemon_settings.py
  - tests/test_eigen.py
  - tests/test_email_abs_reply_route.py
  - tests/test_goal_notification.py
  - tests/test_karma.py
  - tests/test_kernel_version_nudge.py
  - tests/test_large_result_rescan.py
  - tests/test_mcp_identity_discovery.py
  - tests/test_molt_notification_persistence.py
  - tests/test_post_molt_notification.py
  - tests/test_preset_context_guard.py
  - tests/test_provider_admission.py
  - tests/test_psyche_family.py
  - tests/test_psyche_prompt_settings.py
  - tests/test_repeated_tool_error_continue.py
  - tests/test_resident_acp_socket.py
  - tests/test_shell_sandbox_containment.py
  - tests/test_source_drift.py
  - tests/test_system_declared_plugin.py
  - tests/test_tool_family_avatar_migration.py
  - tests/test_tool_family_email_migration.py
  - tests/test_tool_family_manual_contract.py
  - tests/test_tool_family_system_migration.py
  - tests/test_tool_meta_comment_overflow.py
  - tests/_agent_dir_helpers.py
  - tests/_agent_presence_helpers.py
  - tests/_daemon_helpers.py
  - tests/_detached_cli_parent.py
  - tests/_fake_claude_sdk_cli.py
  - tests/_fake_codex_cli.py
  - tests/_fake_llm_adapter.py
  - tests/_fake_resume_cli.py
  - tests/_lifecycle_clock_helpers.py
  - tests/_manager_detached_parent.py
  - tests/_migration_workspace_helpers.py
  - tests/_notification_store_helpers.py
  - tests/_refresh_watcher_helpers.py
  - tests/_service_helpers.py
  - tests/_snapshot_helpers.py
  - tests/_workdir_lease_helpers.py
  - tests/claude
  - tests/codex
  - tests/conftest.py
  - tests/contracts/llm_conversation_input/__init__.py
  - tests/contracts/llm_conversation_input/regimes.py
  - tests/contracts/llm_conversation_input/test_openai_responses_tool_result_conversion.py
  - tests/contracts/llm_conversation_input/test_regime_inventory.py
  - tests/contracts/llm_conversation_input/test_send_str.py
  - tests/contracts/llm_conversation_input/test_send_tool_results.py
  - tests/opencode
  - tests/test_aed_recovery.py
  - tests/test_agent.py
  - tests/test_agent_capabilities.py
  - tests/test_agent_meta_guidance.py
  - tests/test_agent_presence.py
  - tests/test_anatomy_drift_checker.py
  - tests/test_architecture_documents.py
  - tests/test_avatar_launcher_windows.py
  - tests/test_bash_shell_dialect.py
  - tests/test_cli_integration.py
  - tests/test_cli_worker_poison_recovery.py
  - tests/test_context_pressure_reminder.py
  - tests/test_context_pressure_streak.py
  - tests/test_daemon_attention_delay.py
  - tests/test_daemon_detached_supervisor.py
  - tests/test_daemon_notification_channel.py
  - tests/test_daemon_process_port.py
  - tests/test_daemon_windows_lock.py
  - tests/test_daemon_windows_process_port.py
  - tests/test_daemon_windows_supervisor.py
  - tests/test_docs_governance.py
  - tests/test_feishu_rich_outbound.py
  - tests/test_git_init.py
  - tests/test_how_to_change_name_e2e.py
  - tests/test_init_reader.py
  - tests/test_kernel_isolation.py
  - tests/test_kernel_migrate.py
  - tests/test_labt_validation.py
  - tests/test_lifecycle_daemon_shutdown.py
  - tests/test_lifecycle_stop_wake.py
  - tests/test_lingtai_facade.py
  - tests/test_mail_transport.py
  - tests/test_mcp_sdk_v2_contract.py
  - tests/test_notification_store.py
  - tests/test_notification_sync.py
  - tests/test_outbound_file_containment.py
  - tests/test_perform_refresh_handshake.py
  - tests/test_portable_adapter_encoding.py
  - tests/test_powershell_decode_windows_output.py
  - tests/test_pre_request_hook.py
  - tests/test_process_identity.py
  - tests/test_process_match.py
  - tests/test_process_scan.py
  - tests/test_project_creation.py
  - tests/test_refresh_watcher_lease_probe.py
  - tests/test_refresh_watcher_windows.py
  - tests/test_risky_action_gate.py
  - tests/test_shell_kind_classifier.py
  - tests/test_shell_kind_spawn_args.py
  - tests/test_shell_pr1_contract.py
  - tests/test_shell_windows_native.py
  - tests/test_snapshot.py
  - tests/test_task_card_resident_shared.py
  - tests/test_tc_wake.py
  - tests/test_auto_molt_idle_episode.py
  - tests/test_telegram_task_card_transport.py
  - tests/test_apriori_summary_session_cost.py
  - tests/test_token_ledger.py
  - tests/test_tool_executor.py
  - tests/test_tool_result_recovery.py
  - tests/test_tool_result_restore_after_continuation_failure.py
  - tests/test_windows_import_graph.py
  - tests/test_windows_regression.py
  - tests/test_windows_taskkill_fallback.py
  - tests/test_workdir_lease.py
  - tests/test_workdir_lease_posix_only.py
  - tests/test_worker_hang_aed_redo.py
  - tests/test_daemon_central_manager.py
  - tests/test_fsutil.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  This is a navigation-only inventory anatomy: tests/ is a validation surface,
  not a governed component, so tests/CONTRACT.md deliberately stays outside the
  root contract graph and this file is NOT its governed twin — read that file
  for the testing methodology and do not restate it here. Every tracked file
  under tests/ must appear above, so adding or deleting a test file updates
  this list in the same change; test_architecture_documents.py enforces that.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# tests/

A deliberately small retained validation surface: 104 pytest modules, shared
fixtures, and fake executables. This is a navigation-only inventory, not a
governed component or a promise of comprehensive product conformance.
`tests/CONTRACT.md` remains the methodology charter outside the root contract
graph.

## Components

- `conftest.py` owns hermetic environment/registry isolation and the
  `make_agent_dir` fixture. Retained `_*.py` modules provide only the fixtures
  needed by the surviving tests, including subprocess-loaded fake providers.
- Startup/prompt/CLI smoke constructs real Agent/runtime objects with a local
  deterministic LLM substitute; it does not claim live-provider acceptance.
- `contracts/llm_conversation_input/` drives production factories and concrete
  sessions with local transport doubles. It checks common text/tool-result
  serialization and returned envelopes, not OAuth or remote service behavior.
- Poison recovery, refresh single-flight, persisted tool-result restoration,
  physical partial sends, resident old-first rotation/commit-after-success,
  side-effect gating, and file containment retain distinct failure invariants.
- The 31 explicitly named macOS/Windows CI selector modules are retained
  with unchanged executable assertions as a separate native/platform tier; CI triggers and failure
  semantics are untouched. These are not all blackbox smoke.
- `test_architecture_documents.py`, `test_docs_governance.py`,
  `test_anatomy_drift_checker.py`, and `test_labt_validation.py` retain graph and
  document validation; retaining them is not runtime conformance evidence.
- `codex`/`opencode`/`claude` and fake CLI/provider modules remain local
  subprocess fixtures, not real installed-provider tests. The `claude`
  fixture pairs a fake CLI subprocess with the real installed
  `claude_agent_sdk` package to prove the SDK wire protocol end to end, not
  network/model access.

- `test_tc_wake.py` and `test_pre_request_hook.py` retain the accepted modern
  canonical-wire wake and generic adapter-hook regressions.
  `test_notification_sync.py` is only their existing 70-line helper closure;
  its former notification matrices are retired, not restored.
- `test_auto_molt_idle_episode.py` covers the strict current-context/time gate,
  IDLE/ASLEEP continuity, unloaded/unknown context, publication retry, actual
  post-molt context, journal refusal, env parsing and full Agent prompt loading.
  It does not restore retired heartbeat/notification matrices.
- Reduced `test_tool_executor.py` retains the accepted ordinary serial/parallel
  unexpected-key and explicit custom-handler boundaries, plus LP003
  default-on a-priori presentation probes: actual single/concurrent executor,
  daemon-local locators, curated schema/dispatch, literal control precedence,
  parser failures, and Context domain isolation. Reduced
  `test_context_pressure_reminder.py` plus `test_context_pressure_streak.py`
  retain numeric thresholds, canonical ownership and fresh-round integration.

- `test_daemon_central_manager.py` and `test_fsutil.py` are retained unchanged
  from accepted main to keep protected historical report/plan edges valid.
  Their existing helper closure is already retained; no stub replacements.

## Connections

`pyproject.toml` still collects the test tree. `AGENTS.md` and
`dev-guide-skill/SKILL.md` still own validation. Component owners link only
retained files in `related_files`; `retired-test:` prose identifiers explicitly
record surrendered coverage, not replacement tests. Historical `supersedes`
entries and archived evidence keep their original selectors as provenance.

## Composition

- Parent: repository-root `ANATOMY.md`.
- Methodology neighbour: `tests/CONTRACT.md`, deliberately not a governed twin.
- Collection package: `contracts/llm_conversation_input/`; no child anatomy.

## State

Tests own temporary directories and processes. Fake external transports do not
prove native provider delivery. Every surviving process/lifecycle test remains
responsible for its own teardown under the methodology charter.

## Notes

The 2026-10-12 pruning removes whole provider/backend/channel/presentation,
legacy/parity, capability/schema and kernel-internal matrices rather than
replace them with new mock-heavy suites. Many removed tests had unique edge
coverage: removal is an authorized maintenance tradeoff, not evidence that
their behavior is obsolete or that all pytest tests are pointless. Product
contracts and agent behavior tasks are not weakened. Automated LABT recipes
whose selectors disappeared are explicitly retired, not silently remapped to
unrelated smoke. Restore a focused regression from Git history when a
concrete defect or required native/manual acceptance earns it.
