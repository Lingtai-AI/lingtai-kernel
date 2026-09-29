"""Shell's LTP v2 action-separated migration: schema, dispatch, and lifecycle.

Proves the public/model-facing ``shell`` tool is the ToolFamily-composed
``action``/``input``/``reasoning``/``summarize`` envelope over the canonical
children ``run | poll | cancel | settings | manual``, while ``ShellManager`` — the sync
execution path, the working-directory sandbox, the durable async lifecycle,
cancellation, and terminal receipts — stays the exact, untouched engine the
pre-migration suites (``test_bash_async.py``, ``test_layers_bash.py``,
``test_shell_sandbox_containment.py``) already exercise through its legacy flat
call shape.

The invariants under test come from ``src/lingtai/tools/CONTRACT.md`` (Envelope,
Dispatch and actions) and the overnight Shell-specific acceptance line:
run-only fields live only in ``run``'s branch, ``job_id`` only in
``poll``/``cancel``'s, the reserved ``manual`` performs no target operation, and
every child result stays canonical/raw with no second envelope wrapped around
it.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests._notification_store_helpers import notification_store_for
from lingtai.tools.bash import ShellManager, ShellPolicy, setup
from lingtai.tools.bash._tool_family import (
    CANCEL_INPUT_SCHEMA,
    DECLARATION,
    MANUAL_INPUT_SCHEMA,
    POLL_INPUT_SCHEMA,
    RUN_INPUT_SCHEMA,
    ShellFamilyDispatcher,
    get_schema,
)
from tests._tool_family_schema_helpers import (
    action_input_schema,
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)

_ACTIONS = ["run", "poll", "cancel", "settings", "manual"]
_SETTINGS_INPUT_SCHEMA = DECLARATION.public_input_schemas()["settings"]
_CANONICAL_INPUT_SCHEMAS = {
    "run": RUN_INPUT_SCHEMA,
    "poll": POLL_INPUT_SCHEMA,
    "cancel": CANCEL_INPUT_SCHEMA,
    "settings": _SETTINGS_INPUT_SCHEMA,
    "manual": MANUAL_INPUT_SCHEMA,
}


def _make_manager(tmp_path: Path) -> ShellManager:
    agent = SimpleNamespace(_notification_store=notification_store_for(tmp_path))
    return ShellManager(
        policy=ShellPolicy.yolo(), working_dir=str(tmp_path), agent=agent
    )


def _make_dispatcher(tmp_path: Path) -> tuple[ShellFamilyDispatcher, ShellManager]:
    mgr = _make_manager(tmp_path)
    return ShellFamilyDispatcher(mgr, mgr._agent), mgr


def _install_shell_manual(working_dir: Path, body: str) -> Path:
    """Install a shell manual exactly where the real initializer puts it.

    ``Agent._install_intrinsic_manuals`` maps the retained implementation
    directory ``bash/`` to the canonical model-facing destination name
    ``shell`` under ``.library/intrinsic/capabilities/`` — so
    ``build_manual_child(agent, "shell")`` reads the same body/path the
    pre-migration ``ShellManager.handle({"action": "manual"})`` did.
    """
    manual_dir = working_dir / ".library" / "intrinsic" / "capabilities" / "shell"
    manual_dir.mkdir(parents=True)
    manual_path = manual_dir / "SKILL.md"
    manual_path.write_text(body, encoding="utf-8")
    return manual_path


def _run(dispatcher: ShellFamilyDispatcher, **input_fields) -> dict:
    return dispatcher.handle(
        {"action": "run", "input": input_fields, "reasoning": "migration test"}
    )


# ---------------------------------------------------------------------------
# Public identity and root envelope
# ---------------------------------------------------------------------------

def test_public_model_facing_name_is_shell_with_the_family_schema(tmp_path):
    """The registered public name stays exactly ``shell`` (``bash`` is only the
    retained implementation package), and the registered schema is now the
    action-separated family envelope, not the legacy flat shape."""
    agent = MagicMock()
    agent.official_tool_plugins = {}
    agent.working_dir = Path(tmp_path)

    setup(agent, yolo=True)

    transaction = agent._mount_official_tool.call_args.args[0]
    assert transaction.plugin.name == "shell"
    schema = transaction.plugin.schema
    assert schema == get_schema()
    # The legacy flat run-only fields are gone from the public root.
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    for legacy_flat_field in ("command", "timeout", "working_dir", "async", "reminder", "job_id"):
        assert legacy_flat_field not in schema["properties"]


def test_registered_description_documents_the_action_separated_call_shape(tmp_path):
    """The model-facing description must teach the shape actually registered —
    ``input.async`` / ``input={'job_id': ...}``, not the legacy flat fields."""
    agent = MagicMock()
    agent.official_tool_plugins = {}
    agent.working_dir = Path(tmp_path)

    setup(agent, yolo=True)
    description = agent._mount_official_tool.call_args.args[0].plugin.description

    for action in _ACTIONS:
        assert f"shell(action='{action}'" in description
    assert "input.async=true" in description
    # Setup-time adapter/host metadata and the failure-fidelity guidance the
    # pre-migration description carried are retained.
    assert "Active shell dialect:" in description
    assert "Host OS:" in description
    assert "exit_code" in description and "command_status" in description
    assert "warning field" in description


def test_root_is_strict_action_input_reasoning_summarize():
    schema = get_schema()

    assert schema["type"] == "object"
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["properties"]["reasoning"]["type"] == "string"
    assert schema["properties"]["summarize"]["type"] == "boolean"
    assert schema["properties"]["action"]["enum"] == _ACTIONS


def test_canonical_children_are_the_five_shell_actions():
    schema = get_schema()
    # One root ``oneOf`` branch per child in registration order; the settings
    # child is discriminated by its ``action`` const like every other, so the
    # composed root never needs an ``anyOf`` fallback.
    assert branch_actions(schema) == _ACTIONS
    assert_compact_envelope(schema, _ACTIONS)


# ---------------------------------------------------------------------------
# Per-action input separation
# ---------------------------------------------------------------------------

def test_run_only_fields_live_only_in_run_and_job_id_only_in_poll_cancel():
    run_props = set(RUN_INPUT_SCHEMA["properties"])
    assert run_props == {"command", "timeout", "working_dir", "async", "reminder"}
    assert "job_id" not in run_props

    assert set(POLL_INPUT_SCHEMA["properties"]) == {"job_id"}
    assert set(CANCEL_INPUT_SCHEMA["properties"]) == {"job_id"}
    for schema in (POLL_INPUT_SCHEMA, CANCEL_INPUT_SCHEMA):
        assert schema["required"] == ["job_id"]
        assert run_props.isdisjoint(schema["properties"])

    # Manual is strict-empty: it takes no input at all. Its schema is sourced
    # from the shared reserved-manual builder, so there is exactly one
    # definition and the wire/dispatch views cannot drift.
    assert MANUAL_INPUT_SCHEMA["properties"] == {}
    assert MANUAL_INPUT_SCHEMA["additionalProperties"] is False
    from lingtai.tools.tool_family.manual import build_manual_child

    assert MANUAL_INPUT_SCHEMA == dict(build_manual_child(None, "shell").input_schema)


def test_every_child_input_schema_is_closed_and_free_of_host_fields():
    for schema in (
        RUN_INPUT_SCHEMA,
        POLL_INPUT_SCHEMA,
        CANCEL_INPUT_SCHEMA,
        _SETTINGS_INPUT_SCHEMA,
        MANUAL_INPUT_SCHEMA,
    ):
        assert schema["additionalProperties"] is False
        for host_field in ("action", "reasoning", "_reasoning", "summarize", "summary"):
            assert host_field not in schema["properties"]


def test_root_one_of_correlates_each_action_const_to_its_own_input_schema():
    """Each root ``oneOf`` branch pairs one ``action`` const with exactly its
    canonical child ``input_schema`` — no ``title``, no root ``allOf``, and
    no second copy of any child under ``properties.input``."""
    schema = get_schema()
    assert len(schema["oneOf"]) == len(_ACTIONS)
    assert "allOf" not in schema and "anyOf" not in schema
    for duplicate in ("oneOf", "anyOf", "allOf", "properties"):
        assert duplicate not in schema["properties"]["input"]

    for action, branch in zip(_ACTIONS, schema["oneOf"]):
        assert set(branch) == {"properties"}
        assert set(branch["properties"]) == {"action", "input"}
        assert branch["properties"]["action"] == {"const": action}
        assert branch["properties"]["input"] == _CANONICAL_INPUT_SCHEMAS[action]


def test_composed_schema_branches_are_mutation_isolated():
    """A caller mutating one composed schema must not corrupt the next call's
    nor the canonical child schema the dispatcher validates against."""
    first = get_schema()
    action_input_schema(first, "run")["properties"].clear()
    action_input_schema(first, "poll")["properties"]["smuggled"] = {"type": "string"}

    second = get_schema()

    assert set(action_input_schema(second, "run")["properties"]) == {
        "command", "timeout", "working_dir", "async", "reminder"
    }
    assert set(action_input_schema(second, "poll")["properties"]) == {"job_id"}
    # The canonical child schemas are untouched by the mutation.
    assert set(RUN_INPUT_SCHEMA["properties"]) == {
        "command", "timeout", "working_dir", "async", "reminder"
    }
    assert set(POLL_INPUT_SCHEMA["properties"]) == {"job_id"}
    # Branches within one call never share a container with each other.
    assert "smuggled" not in action_input_schema(first, "cancel")["properties"]


# ---------------------------------------------------------------------------
# Dispatch-time rejection (before any handler I/O)
# ---------------------------------------------------------------------------

def test_unknown_action_fails_with_shells_own_five_action_message(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    result = dispatcher.handle({"action": "exec", "input": {}, "reasoning": "r"})

    assert result["status"] == "failed"
    assert result["error_code"] == "ACTION_REQUIRED"
    assert result["message"] == (
        "action must be one of run, poll, cancel, settings, or manual"
    )


@pytest.mark.parametrize(
    "action, action_input",
    [
        ("run", {"command": "echo hi", "job_id": "job-1"}),   # poll/cancel field in run
        ("poll", {"job_id": "job-1", "command": "echo hi"}),  # run field in poll
        ("cancel", {"job_id": "job-1", "async": True}),       # run field in cancel
        ("manual", {"command": "echo hi"}),                   # manual takes nothing
    ],
    ids=["job_id-in-run", "command-in-poll", "async-in-cancel", "command-in-manual"],
)
def test_cross_action_input_fields_are_rejected_before_handler_io(tmp_path, action, action_input):
    """A key belonging to another action's branch is refused at dispatch — the
    engine is never reached, so no command runs and no job is touched."""
    mgr = _make_manager(tmp_path)
    mgr.handle = lambda args: pytest.fail("cross-action input reached ShellManager")
    dispatcher = ShellFamilyDispatcher(mgr, mgr._agent)

    result = dispatcher.handle({"action": action, "input": action_input, "reasoning": "r"})

    assert result["status"] == "failed"
    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["message"] == "unsupported shell input field"


def test_unknown_root_field_and_non_object_input_are_rejected(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    unknown_root = dispatcher.handle(
        {"action": "run", "input": {"command": "echo hi"}, "reasoning": "r", "command": "echo hi"}
    )
    assert unknown_root["error_code"] == "INVALID_ARGUMENT"
    assert unknown_root["message"] == "unsupported shell argument"

    bad_input = dispatcher.handle({"action": "run", "input": "echo hi", "reasoning": "r"})
    assert bad_input["error_code"] == "INVALID_ARGUMENT"
    assert bad_input["message"] == "input must be an object"


def test_reasoning_and_summarize_never_reach_the_engine(tmp_path):
    """Host envelope metadata is stripped before dispatch: the engine sees only
    the action's own flattened input."""
    seen: list[dict] = []
    mgr = _make_manager(tmp_path)
    mgr.handle = lambda args: seen.append(dict(args)) or {"status": "ok"}
    dispatcher = ShellFamilyDispatcher(mgr, mgr._agent)

    dispatcher.handle({
        "action": "run",
        "input": {"command": "echo hi"},
        "reasoning": "why I am calling this",
        "summarize": True,
    })

    assert seen == [{"action": "run", "command": "echo hi"}]


def test_non_boolean_summarize_is_rejected(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    result = dispatcher.handle(
        {"action": "run", "input": {"command": "echo hi"}, "reasoning": "r", "summarize": "yes"}
    )

    assert result["error_code"] == "INVALID_ARGUMENT"
    assert result["message"] == "summarize must be a boolean"


# ---------------------------------------------------------------------------
# Null-as-absent translation into the unchanged engine defaults
# ---------------------------------------------------------------------------

def test_null_optionals_are_dropped_so_engine_defaults_apply(tmp_path):
    """Strict schemas express optionals as required-nullable; null means absent,
    so ``ShellManager`` applies its own unchanged runtime defaults."""
    seen: list[dict] = []
    mgr = _make_manager(tmp_path)
    mgr.handle = lambda args: seen.append(dict(args)) or {"status": "ok"}
    dispatcher = ShellFamilyDispatcher(mgr, mgr._agent)

    dispatcher.handle({
        "action": "run",
        "input": {
            "command": "echo hi", "timeout": None, "working_dir": None,
            "async": None, "reminder": None,
        },
        "reasoning": "r",
    })

    assert seen == [{"action": "run", "command": "echo hi"}]


def test_falsy_but_present_values_are_preserved_not_dropped(tmp_path):
    seen: list[dict] = []
    mgr = _make_manager(tmp_path)
    mgr.handle = lambda args: seen.append(dict(args)) or {"status": "ok"}
    dispatcher = ShellFamilyDispatcher(mgr, mgr._agent)

    dispatcher.handle({
        "action": "run",
        "input": {
            "command": "echo hi", "timeout": 0, "working_dir": "",
            "async": False, "reminder": 0,
        },
        "reasoning": "r",
    })

    assert seen == [{
        "action": "run", "command": "echo hi", "timeout": 0,
        "working_dir": "", "async": False, "reminder": 0,
    }]


# ---------------------------------------------------------------------------
# Sync execution through the envelope
# ---------------------------------------------------------------------------

def test_sync_success_returns_the_canonical_raw_result(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    result = _run(dispatcher, command="echo migrated")

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "migrated"
    assert result["ok"] is True
    assert result["command_status"] == "success"
    # Canonical/raw: the child result is returned verbatim, never nested in a
    # second Host envelope.
    assert "content" not in result
    assert "result" not in result


def test_sync_nonzero_failure_keeps_status_ok_with_fidelity_fields(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    result = _run(dispatcher, command="exit 7")

    assert result["status"] == "ok"  # status means the shell ran, not that it succeeded
    assert result["exit_code"] == 7
    assert result["ok"] is False
    assert result["command_status"] == "failed"
    assert "command exited with code 7" in result["warning"]


def test_working_dir_sandbox_is_enforced_through_the_envelope(tmp_path):
    sandbox = tmp_path / "sandbox"
    external = tmp_path / "external"
    sandbox.mkdir()
    external.mkdir()
    agent = SimpleNamespace(_notification_store=notification_store_for(tmp_path))
    mgr = ShellManager(policy=ShellPolicy.yolo(), working_dir=str(sandbox), agent=agent)
    dispatcher = ShellFamilyDispatcher(mgr, agent)

    result = _run(dispatcher, command="pwd", working_dir=str(external))

    assert result["status"] == "error"
    assert "under agent working directory" in result["message"]


def test_timeout_is_honored_through_the_envelope(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    result = _run(dispatcher, command="sleep 5", timeout=0.3)

    assert result["status"] == "error"
    assert "timed out" in result["message"]


# ---------------------------------------------------------------------------
# Async lifecycle: run -> poll -> terminal receipt
# ---------------------------------------------------------------------------

def test_async_run_then_terminal_poll_through_the_envelope(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    started = _run(dispatcher, command="echo async-migrated", **{"async": True})
    job_id = started["job_id"]
    assert started["status"] == "ok"
    assert job_id
    assert started["handoff"]  # async return-handoff guard preserved

    deadline = time.time() + 20
    while time.time() < deadline:
        poll = dispatcher.handle(
            {"action": "poll", "input": {"job_id": job_id}, "reasoning": "check"}
        )
        if poll["status"] == "done":
            break
        time.sleep(0.2)

    assert poll["status"] == "done"
    assert poll["exit_code"] == 0
    assert "async-migrated" in poll["stdout"]


def test_poll_is_one_shot_after_terminal_consumption(tmp_path):
    """The second terminal poll must not re-serve the same completed job as if
    it were fresh — the pre-migration one-shot semantics are unchanged."""
    dispatcher, mgr = _make_dispatcher(tmp_path)

    started = _run(dispatcher, command="echo once", **{"async": True})
    job_id = started["job_id"]

    deadline = time.time() + 20
    while time.time() < deadline:
        first = dispatcher.handle(
            {"action": "poll", "input": {"job_id": job_id}, "reasoning": "check"}
        )
        if first["status"] == "done":
            break
        time.sleep(0.2)
    assert first["status"] == "done"

    second = dispatcher.handle(
        {"action": "poll", "input": {"job_id": job_id}, "reasoning": "check again"}
    )
    # Whatever the engine's one-shot answer is, it is the engine's own — the
    # envelope must reproduce it identically to the legacy flat call path.
    assert second == mgr.handle({"action": "poll", "job_id": job_id})


def test_cancel_through_the_envelope_terminates_the_job(tmp_path):
    dispatcher, _ = _make_dispatcher(tmp_path)

    started = _run(dispatcher, command="sleep 30", **{"async": True})
    job_id = started["job_id"]
    assert job_id

    cancelled = dispatcher.handle(
        {"action": "cancel", "input": {"job_id": job_id}, "reasoning": "stop it"}
    )

    assert cancelled["status"] in {"ok", "done", "cancelled"}
    # No orphan: the job reaches a terminal state rather than staying live.
    deadline = time.time() + 20
    while time.time() < deadline:
        poll = dispatcher.handle(
            {"action": "poll", "input": {"job_id": job_id}, "reasoning": "confirm terminal"}
        )
        if poll["status"] in {"done", "error"}:
            break
        time.sleep(0.2)
    assert poll["status"] in {"done", "error"}


def test_async_reminder_default_and_explicit_value_reach_the_engine(tmp_path):
    seen: list[dict] = []
    mgr = _make_manager(tmp_path)
    mgr.handle = lambda args: seen.append(dict(args)) or {"status": "ok", "job_id": "j"}
    dispatcher = ShellFamilyDispatcher(mgr, mgr._agent)

    dispatcher.handle({
        "action": "run",
        "input": {"command": "sleep 1", "async": True, "reminder": 42.0},
        "reasoning": "r",
    })
    assert seen[-1]["reminder"] == 42.0

    # Omitted/null reminder leaves the engine's own 1800s durable default.
    dispatcher.handle({
        "action": "run",
        "input": {"command": "sleep 1", "async": True, "reminder": None},
        "reasoning": "r",
    })
    assert "reminder" not in seen[-1]
    assert RUN_INPUT_SCHEMA["properties"]["reminder"]["default"] == 1800.0


# ---------------------------------------------------------------------------
# Agent-facing guidance strings must teach the registered envelope
# ---------------------------------------------------------------------------

def _parse_taught_call(text: str) -> dict:
    """Turn a taught ``shell(action="poll", input={"job_id": "..."})`` string
    into the args dict an agent copying it verbatim would actually send."""
    match = re.search(
        r'shell\(action="(?P<action>\w+)",\s*input=\{"job_id":\s*"(?P<job_id>[^"]+)"\}\)',
        text,
    )
    assert match, f"no envelope-shaped shell call found in: {text!r}"
    return {
        "action": match.group("action"),
        "input": {"job_id": match.group("job_id")},
        "reasoning": "following the taught call verbatim",
    }


def test_async_receipt_teaches_a_call_the_registered_schema_accepts(tmp_path):
    """The async start receipt is instructional content. An agent that copies it
    verbatim must land a call the public envelope accepts — not one rejected
    before dispatch."""
    dispatcher, _ = _make_dispatcher(tmp_path)

    started = _run(dispatcher, command="echo receipt-round-trip", **{"async": True})
    job_id = started["job_id"]
    message = started["message"]

    # The taught call names the envelope, not the flat root field.
    assert f'shell(action="poll", input={{"job_id": "{job_id}"}})' in message
    assert f'job_id="{job_id}"' not in message

    taught = _parse_taught_call(message)
    assert taught["input"]["job_id"] == job_id

    # Round-trip: the exact taught call must survive dispatch, not fail closed.
    deadline = time.time() + 20
    while time.time() < deadline:
        result = dispatcher.handle(taught)
        assert result.get("error_code") is None, (
            f"the receipt taught a call the registered schema rejects: {result}"
        )
        assert result["status"] in {"running", "done"}
        if result["status"] == "done":
            break
        time.sleep(0.2)
    assert result["status"] == "done"
    assert "receipt-round-trip" in result["stdout"]


def test_durable_reminder_teaches_a_call_the_registered_schema_accepts(tmp_path):
    """The durable watchdog may wake an agent hours later (possibly post-molt),
    so the call it hands over must also round-trip through the envelope."""
    dispatcher, mgr = _make_dispatcher(tmp_path)

    started = _run(dispatcher, command="sleep 30", **{"async": True})
    job_id = started["job_id"]
    published: list[str] = []
    mgr._agent._enqueue_system_notification = (
        lambda **kw: published.append(kw["body"]) or True
    )
    try:
        mgr._publish_async_reminder(job_id)

        assert published, "reminder published no notification body"
        body = published[0]
        assert f'shell(action="poll", input={{"job_id": "{job_id}"}})' in body
        assert f'job_id="{job_id}"' not in body

        taught = _parse_taught_call(body)
        result = dispatcher.handle(taught)
        assert result.get("error_code") is None, (
            f"the reminder taught a call the registered schema rejects: {result}"
        )
        assert result["status"] in {"running", "done"}
    finally:
        dispatcher.handle(
            {"action": "cancel", "input": {"job_id": job_id}, "reasoning": "cleanup"}
        )


def test_family_manual_worked_examples_use_the_registered_envelope():
    """The manual is mandated reading served by ``action="manual"``. Every
    worked ``shell(...)`` call in it must parse as the envelope shape, and
    ``reminder`` must not appear on poll/cancel anywhere."""
    manual = (
        Path(__file__).resolve().parents[1]
        / "src" / "lingtai" / "tools" / "bash" / "manual" / "SKILL.md"
    ).read_text(encoding="utf-8")

    # No flat run-only field is passed at the call root anywhere in the manual.
    for flat in ('shell(async=', 'async=true,', 'job_id="job-', 'reminder=1800'):
        assert flat not in manual, f"manual still teaches the flat shape: {flat}"

    # Every worked call opens with an explicit action= and is one of the five.
    calls = re.findall(r'shell\(action="(\w+)"', manual)
    assert calls, "expected worked shell() examples in the manual"
    assert set(calls) <= set(_ACTIONS)
    # run/poll/cancel are all demonstrated.
    assert {"run", "poll", "cancel"} <= set(calls)

    # poll/cancel examples carry job_id and never reminder.
    for action in ("poll", "cancel"):
        block = re.search(
            rf'shell\(action="{action}",\s*input=\{{(?P<input>[^}}]*)\}}', manual
        )
        assert block, f"no envelope-shaped {action} example in the manual"
        assert "job_id" in block.group("input")
        assert "reminder" not in block.group("input")


# ---------------------------------------------------------------------------
# Reserved manual child
# ---------------------------------------------------------------------------

def test_manual_returns_the_canonical_body_and_path_without_double_wrap(tmp_path):
    manual_body = "# shell manual\n\nasync hygiene and advanced usage.\n"
    manual_path = _install_shell_manual(tmp_path, manual_body)
    agent = SimpleNamespace(
        _notification_store=notification_store_for(tmp_path), _working_dir=tmp_path
    )
    mgr = ShellManager(policy=ShellPolicy.yolo(), working_dir=str(tmp_path), agent=agent)
    dispatcher = ShellFamilyDispatcher(mgr, agent)

    result = dispatcher.handle({"action": "manual", "input": {}, "reasoning": "read manual"})

    assert result["status"] == "ok"
    # Full body at content[0].text, host-local path in structuredContent — the
    # shared ManualTool contract, returned verbatim (no second envelope).
    assert result["content"][0]["text"] == manual_body
    assert result["structuredContent"]["manual_path"] == str(manual_path)
    assert "content" not in result["structuredContent"]
    # Same body and same host-local path the installed manual actually holds —
    # the destination `Agent._install_intrinsic_manuals` maps `bash/manual/` to.
    assert result["content"][0]["text"] == manual_path.read_text(encoding="utf-8")
    assert manual_path.parent.name == "shell"

    # ``manual`` is a family child only. The engine's own manual branch is
    # gone, so the engine no longer serves documentation: an internal call
    # naming it falls through to `run`, which rejects the empty command
    # instead of returning a manual.
    engine_manual = mgr.handle({"action": "manual"})
    assert "manual" not in engine_manual
    assert engine_manual["status"] == "error"


def test_manual_performs_no_shell_operation(tmp_path):
    """Manual is no-I/O: it must not reach the execution engine at all."""
    _install_shell_manual(tmp_path, "# shell manual\n")
    agent = SimpleNamespace(
        _notification_store=notification_store_for(tmp_path), _working_dir=tmp_path
    )
    mgr = ShellManager(policy=ShellPolicy.yolo(), working_dir=str(tmp_path), agent=agent)
    mgr.handle = lambda args: pytest.fail("manual reached the shell execution engine")
    dispatcher = ShellFamilyDispatcher(mgr, agent)

    result = dispatcher.handle({"action": "manual", "input": {}, "reasoning": "read manual"})

    assert result["status"] == "ok"
    assert result["content"][0]["text"].startswith("# shell manual")


def test_manual_name_is_reserved_once_by_the_family(tmp_path):
    """The family owns exactly one ``manual`` child; a second registration is a
    loud construction defect, not a silently-resolved naming choice."""
    from lingtai.tools.tool_family import ChildTool, ToolFamily, ToolFamilyError

    # Shell's real family registers ``manual`` exactly once.
    assert get_schema()["properties"]["action"]["enum"].count("manual") == 1

    with pytest.raises(ToolFamilyError, match="duplicate child name 'manual'"):
        ToolFamily("shell", [
            ChildTool("run", RUN_INPUT_SCHEMA, lambda _i: {}, title="run input"),
            ChildTool("manual", MANUAL_INPUT_SCHEMA, lambda _i: {}, title="manual input"),
            ChildTool("manual", MANUAL_INPUT_SCHEMA, lambda _i: {}, title="manual input"),
        ])


# ---------------------------------------------------------------------------
# Provider wire parity
# ---------------------------------------------------------------------------

def test_shell_family_schema_survives_chat_and_responses_wires():
    from lingtai.kernel.llm.base import FunctionSchema
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    schema = FunctionSchema(name="shell", description="shell", parameters=get_schema())
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]

    # Chat Completions passes the composed schema through byte-for-byte.
    assert chat == get_schema()
    for wire in (chat, responses):
        assert wire["type"] == "object"
        assert wire["required"] == ["action", "input", "reasoning"]
        assert wire["additionalProperties"] is False
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["properties"]["action"]["enum"] == _ACTIONS
        # The root ``oneOf`` survives as ``oneOf`` on BOTH wires (the
        # Responses scrub only rewrites nested ``oneOf``); nothing is
        # duplicated under ``properties.input`` or a root ``allOf``.
        assert "anyOf" not in wire and "allOf" not in wire
        for duplicate in ("oneOf", "anyOf", "allOf"):
            assert duplicate not in wire["properties"]["input"]
        assert branch_actions(wire) == _ACTIONS
        for branch in action_input_schemas(wire).values():
            assert branch["additionalProperties"] is False
            for host_field in ("reasoning", "_reasoning", "summarize"):
                assert host_field not in branch["properties"]
    # The Responses scrub's only touch on this shape: the typed root ``input``
    # gains an empty ``properties`` map; it gains no duplicate child schema.
    assert responses["properties"]["input"]["properties"] == {}


def test_responses_wire_preserves_the_root_action_input_correlation():
    """The root ``oneOf`` discriminated union must survive the Responses
    scrubber, so a mismatched action/input pairing is refusable at the schema
    layer on both wires — dispatch remains authoritative regardless."""
    from lingtai.kernel.llm.base import FunctionSchema
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    schema = FunctionSchema(name="shell", description="shell", parameters=get_schema())
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]

    # Identical per-action mapping on both wires.
    assert chat["oneOf"] == responses["oneOf"]
    for wire in (chat, responses):
        assert branch_actions(wire) == _ACTIONS
        run_branch = action_input_schema(wire, "run")
        assert "job_id" not in run_branch["properties"]
        assert set(run_branch["properties"]) == set(RUN_INPUT_SCHEMA["properties"])
        poll_branch = action_input_schema(wire, "poll")
        assert set(poll_branch["properties"]) == {"job_id"}


# ---------------------------------------------------------------------------
# Cross-cutting Host surfaces
# ---------------------------------------------------------------------------

def test_shell_is_a_migrated_family_for_the_single_central_summarizer():
    """``summarize`` is recognized for ``shell`` by the one existing central
    summarizer — no second summarizer and no shell-specific mechanism."""
    from lingtai.kernel.tool_result_summary import summary_requested

    assert summary_requested({"summarize": True}, "shell") is True
    assert summary_requested({"summarize": True}, "some_unmigrated_tool") is False
    assert summary_requested({"summarize": False}, "shell") is False
    # The legacy spelling stays honored unconditionally for historical callers.
    assert summary_requested({"summary": True}, "shell") is True


def test_recovery_notice_reads_shell_fields_from_the_new_envelope():
    """The synthesized-recovery context helper must still name the command and
    action after migration; those fields now live under ``input``."""
    from lingtai.kernel.llm.interface import ToolCallBlock, _tool_call_context

    context = _tool_call_context(ToolCallBlock(
        id="tc-1", name="shell",
        args={
            "action": "run",
            "input": {"command": "echo recovered", "working_dir": "/tmp", "async": True},
            "reasoning": "r",
        },
    ))

    assert "shell action: run" in context
    assert "shell command preview: echo recovered" in context
    assert "shell working_dir: /tmp" in context
    assert "shell async: True" in context


def test_recovery_notice_still_reads_a_historical_flat_call():
    from lingtai.kernel.llm.interface import ToolCallBlock, _tool_call_context

    context = _tool_call_context(ToolCallBlock(
        id="tc-2", name="bash",
        args={"action": "poll", "job_id": "job-1"},
    ))

    assert "shell action: poll" in context
    assert "shell job_id: job-1" in context
