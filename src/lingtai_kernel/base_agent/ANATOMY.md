# base_agent

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues (mail or `discussions/<name>-patch.md`); do not silently fix.

Generic agent kernel. Single class `BaseAgent` with methods distributed across 6 helper modules. `__init__.py` retains the constructor, properties, state machine, and subclass-overridable hooks. (Previously 7 modules; `soul_flow.py` was deleted — soul-domain logic moved to `intrinsics/soul/flow.py`, splice logic moved to `tc_inbox.TCInbox.drain_into()`.)

## Components

- `base_agent/__init__.py` — BaseAgent class definition (916 lines). Constructor (`base_agent/__init__.py:176-406`), properties (`base_agent/__init__.py:420-478`), state machine (`_set_state` at `base_agent/__init__.py:513-540`, `_wake_nap` at `base_agent/__init__.py:543-545`, `_log` at `base_agent/__init__.py:548-557`), hooks (`_pre_request` at `base_agent/__init__.py:875-878`, `_post_request` at `base_agent/__init__.py:880-883`, `_on_tool_result_hook` at `base_agent/__init__.py:885-892`), `_wire_intrinsics` (`base_agent/__init__.py:409-413`). Session persistence: `_save_chat_history` (`base_agent/__init__.py:831-865`), `get_token_usage` (`base_agent/__init__.py:808-820`), `get_chat_state`/`restore_chat`/`restore_token_state` (`base_agent/__init__.py:822-830`). Module-level helpers: `_format_stamina` (`base_agent/__init__.py:54-62`), `_build_identity_section` (`base_agent/__init__.py:65-144`). Pass-through stubs to all submodules (46 methods, ~92 lines of boilerplate).
- `base_agent/identity.py` — Identity, manifest, and status (150 lines). `_set_name` (`base_agent/identity.py:11-22`), `_set_nickname` (`base_agent/identity.py:24-28`), `_update_identity` (`base_agent/identity.py:30-47`), `_build_manifest` (`base_agent/identity.py:50-80`), `_status` (`base_agent/identity.py:83-150`).
- `base_agent/lifecycle.py` — Heartbeat, signal-file detection, start/stop, refresh (442 lines). `_start` (`base_agent/lifecycle.py:15-77`), `_reset_uptime` (`base_agent/lifecycle.py:79-82`), `_stop` (`base_agent/lifecycle.py:84-113`), `_start_heartbeat` (`base_agent/lifecycle.py:115-127`), `_stop_heartbeat` (`base_agent/lifecycle.py:129-141`), `_heartbeat_loop` (`base_agent/lifecycle.py:143-323`), `_perform_refresh` (`base_agent/lifecycle.py:325-386`), `_can_fallback_preset` (`base_agent/lifecycle.py:388-400`), `_build_launch_cmd` (`base_agent/lifecycle.py:402-405`), `_check_rules_file` (`base_agent/lifecycle.py:407-442`).
- `base_agent/turn.py` — Turn engine: main loop, message dispatch, AED, response processing (425 lines). `_run_loop` (`base_agent/turn.py:22-132`), `_concat_queued_messages` (`base_agent/turn.py:134-159`), `_handle_message` (`base_agent/turn.py:161-168`), `_handle_request` (`base_agent/turn.py:171-245`), `_handle_tc_wake` (`base_agent/turn.py:247-332`), `_get_guard_limits` (`base_agent/turn.py:334-341`), `_process_response` (`base_agent/turn.py:343-425`).
- `base_agent/tools.py` — Tool surface: schemas, dispatch, registry (161 lines). `_dispatch_tool` (`base_agent/tools.py:13-33`), `_refresh_tool_inventory_section` (`base_agent/tools.py:35-50`), `_build_tool_schemas` (`base_agent/tools.py:52-100`), `_add_tool` (`base_agent/tools.py:102-131`), `_remove_tool` (`base_agent/tools.py:133-142`), `_override_intrinsic` (`base_agent/tools.py:144-157`), `_has_capability` (`base_agent/tools.py:159-161`).
- `base_agent/prompt.py` — System prompt building, flushing, updating (55 lines). `_build_system_prompt` (`base_agent/prompt.py:9-16`), `_build_system_prompt_batches` (`base_agent/prompt.py:18-32`), `_flush_system_prompt` (`base_agent/prompt.py:34-42`), `_update_system_prompt` (`base_agent/prompt.py:44-55`).
- `base_agent/messaging.py` — Mail arrival, notifications, outbound messaging (161 lines). `_on_mail_received` (`base_agent/messaging.py:10-18`), `_on_normal_mail` (`base_agent/messaging.py:20-61`), `_enqueue_system_notification` (`base_agent/messaging.py:63-131`), `_notify` (`base_agent/messaging.py:133-141`), `_mail` (`base_agent/messaging.py:143-149`), `_send` (`base_agent/messaging.py:151-161`).

## Connections

- All submodules are leaves — they import nothing from `base_agent/`. Cross-module communication is mediated through the `agent` parameter (the BaseAgent instance).
- `__init__.py` imports from all submodules lazily (inside method bodies) to avoid circular imports at load time. Soul-flow pass-throughs now import from `intrinsics/soul/flow.py` and `intrinsics/soul/inquiry.py`.
- `lifecycle.py` → `intrinsics/soul/flow.py` (via `agent._start_soul_timer()`, `agent._cancel_soul_timer()`), `intrinsics/soul/inquiry.py` (via `agent._run_inquiry()`).
- `turn.py` → `tc_inbox.TCInbox.drain_into()` (inline in `_drain_tc_inbox`), `intrinsics/soul/flow.py` (via `agent._cancel_soul_timer()`), `intrinsics/soul/inquiry.py` (via `agent._run_inquiry()`).
- `prompt.py` → `tools.py` (via `agent._refresh_tool_inventory_section()`).
- All submodules read agent attributes (`agent._config`, `agent._session`, `agent._chat`, `agent._tc_inbox`, etc.) — the agent object is the shared state.
- `messaging.py` imports `from ..message import _make_message, MSG_REQUEST, MSG_TC_WAKE` and `from ..i18n import t as _t`.
- `tools.py` imports `from ..intrinsics import ALL_INTRINSICS`, `from ..llm import FunctionSchema`, `from ..i18n import t as _t`.
- `turn.py` imports `from ..state import AgentState`, `from ..loop_guard import LoopGuard`, `from ..tool_executor import ToolExecutor`.
- `lifecycle.py` imports `from ..state import AgentState`, `from ..token_ledger import sum_token_ledger`.

## Composition

- **Parent:** `src/lingtai_kernel/` (see `ANATOMY.md`).
- **Siblings:** `intrinsics/`, `llm/`, `services/`, `i18n/`, `session.py`, `tc_inbox.py`, `tool_executor.py`, `loop_guard.py`, `prompt.py`, `meta_block.py`, `config.py`, `state.py`, `workdir.py`, `message.py`.

## State

- `identity.py` mutates `.agent.json` (manifest) and `system/system.md` (identity prompt section) via `_build_manifest` (`base_agent/identity.py:50-80`) and `_update_identity` (`base_agent/identity.py:30-47`).
- `lifecycle.py` writes `.agent.heartbeat` (`base_agent/lifecycle.py:155-159`), consumes signal files (`.interrupt`, `.refresh`, `.suspend`, `.sleep`, `.prompt`, `.clear`, `.inquiry`, `.rules`).
- `turn.py` writes `history/chat_history.jsonl` via `_save_chat_history` (delegated to `__init__.py`), writes `.status.json` and `logs/token_ledger.jsonl`.
- `messaging.py` enqueues on `tc_inbox` for system notifications (`base_agent/messaging.py:99-108`).

## Notes

- **`__init__.py` is ~916 lines.** This is intentional — the constructor (230 lines), properties, state machine, hooks, cross-cutting infrastructure (`_save_chat_history`, `_log`), and 46 pass-through stubs all live here. The package is not "thin shell + 6 leaves" — it's "916-line core + 7 specialized helpers." Future readers should not try to extract more from `__init__.py` thinking it should be smaller; the remaining code is genuinely cross-cutting or bound to the class definition.
- **Pass-through pattern.** Each extracted method becomes a 2-line stub in `__init__.py` (lazy import + call). This preserves the `BaseAgent` class interface while the implementation lives in submodules. The `self` → `agent` conversion is mechanical but `_heartbeat_loop` (183 lines, 15+ cross-cluster calls) deserves extra-careful review.
- **Subclass overrides stay on `__init__.py`.** `_activate_preset`, `_activate_default_preset`, `_build_launch_cmd`, `_pre_request`, `_post_request`, `_on_tool_result_hook`, `_cpr_agent` are all overridden by the `Agent` subclass in the wrapper package. They must remain as methods on `BaseAgent`.
