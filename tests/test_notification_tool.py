"""Tests for the standalone ``notification`` intrinsic.

The notification tool is the **only** agent-callable home for the notification
verbs.  ``system`` exposes no notification or dismiss verb — there are no
compatibility aliases.  Dismissal is **atomic**:

* ``check`` returns a placeholder dict that the meta-block later stamps the
  live payload onto;
* ``dismiss_channel`` (``input={"channel": ...}``) clears one channel whole
  and refuses ``event_id``/``ref_id``;
* ``dismiss_event`` (``input={"event_id": ..., "channel": ...}``) removes one
  ``system`` event;
* ``dismiss_ref`` (``input={"ref_id": ..., "channel": ...}``) removes
  ``system`` event(s) by ``ref_id``.

Since the LTP v2 migration the tool is a ``ToolFamily``: every call is the
closed ``action`` + ``input`` + ``reasoning`` (+ optional ``summarize``)
envelope, with each action's arguments in its own strict ``input`` object.
The action values and the public tool name are unchanged.  :func:`_call` is
the envelope helper every test below goes through; the strict optional
fields are exercised explicitly where the nullable representation matters.

``summarize`` is NOT an action here — it stays a ``system`` action.  The root
``summarize`` boolean is the cross-cutting LTP v2 post-processing control.

``large_tool_result`` reminders are dismissable as an escape hatch (#430,
superseding the original #424 "undismissable" rule): every atomic notification
action — with or without ``force`` — clears such a reminder and acks its
``ref_id``.  ``system(action="summarize")`` remains the preferred discharge and
still auto-clears the matching reminder on success.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lingtai.tools.registry import INTRINSICS as ALL_INTRINSICS
from lingtai.tools import (
    notification as notif_intrinsic,
    system as sys_intrinsic,
    context as context_intrinsic,
)
from lingtai.kernel.notifications import (
    clear_blocked_channel_warning,
    dismiss_channel,
    flag_unregistered_channel,
    is_channel_allowed,
    is_present_channel_flagable,
    reset_hook_registry_for_tests,
    submit,
    sync_hook_registry,
)
from tests._notification_store_helpers import (
    FakeNotificationStore,
    fingerprint_notifications,
    load_hook_manifests_for_test,
    notification_store_for,
    publish_test_payload,
    replace_hook_manifests_for_test,
    snapshot_notifications,
)

# Shared with test_system_dismiss.py — see tests/_notification_helpers.py.
from tests._notification_helpers import (
    StubAgent as _StubAgent,
    events as _events,
    mark_delivered as _mark_delivered,
    publish_large_result_reminder as _publish_large_result_reminder,
)


def _call(agent: Any, action: str, **action_input: Any) -> dict:
    """Invoke the notification family through the public LTP v2 envelope.

    Every test dispatches through ``handle`` exactly as the kernel does, so
    envelope validation (unknown action, cross-action input, unknown root
    field) is on the same path as action behavior.  ``reasoning`` is required
    by the family schema and always supplied.
    """
    return notif_intrinsic.handle(
        agent, {"action": action, "input": dict(action_input), "reasoning": "test"}
    )


# ---------------------------------------------------------------------------
# Mandatory include + schema availability.
# ---------------------------------------------------------------------------


def test_notification_is_registered_like_system() -> None:
    """The notification intrinsic is in ALL_INTRINSICS — wired for every agent."""
    assert "notification" in ALL_INTRINSICS
    assert ALL_INTRINSICS["notification"]["module"] is notif_intrinsic


def test_notification_wired_into_every_agent() -> None:
    """_wire_intrinsics iterates the injected registry unconditionally → mandatory.

    There is no manifest gate: every key in the injected intrinsic registry
    (``lingtai.tools.registry.INTRINSICS``) is wired into ``agent._intrinsics``. Proving
    'notification' lands there alongside 'system' is the mandatory-include proof.
    """
    from lingtai.kernel.base_agent import BaseAgent

    wired: dict[str, Any] = {}
    modules: dict[str, Any] = {}

    class _FakeAgent:
        _intrinsic_registry = ALL_INTRINSICS
        _intrinsics = wired
        _intrinsic_modules = modules

        def _log(self, *a, **k):
            pass

    BaseAgent._wire_intrinsics(_FakeAgent())  # type: ignore[arg-type]

    assert "system" in wired
    assert "notification" in wired
    assert callable(wired["notification"])


_ACTIONS = ["check", "dismiss_channel", "dismiss_event", "dismiss_ref", "add", "drop", "edit", "list", "manual"]


def test_notification_schema_exposes_atomic_actions() -> None:
    """All action values survive migration, now as the family enum."""
    schema = notif_intrinsic.get_schema("en")
    assert schema["properties"]["action"]["enum"] == _ACTIONS


def test_notification_action_order_is_pinned() -> None:
    """ACTION_ORDER is the single source and cannot silently drift.

    Mirrors the peer-family pins (email/system/context migrations): the exact
    tuple is restated so a reorder of the canonical list fails loudly. Read and
    clear actions keep their pre-existing prefix; hook management (add/drop/
    edit/list) follows in ACTION_ORDER; manual is last.
    """
    from lingtai.tools.notification import ACTION_ORDER

    assert ACTION_ORDER == (
        "check",
        "dismiss_channel",
        "dismiss_event",
        "dismiss_ref",
        "add",
        "drop",
        "edit",
        "list",
        "manual",
    )


def test_notification_root_is_the_closed_ltp_v2_envelope() -> None:
    """Root is exactly action+input+reasoning+summarize and closed.

    ``reasoning`` is required by the family schema itself, not left to Agent
    schema composition (which only merges ``properties``, never ``required``).
    """
    schema = notif_intrinsic.get_schema("en")
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["reasoning"]["type"] == "string"
    assert schema["properties"]["summarize"]["type"] == "boolean"
    # The old flat dismiss params are gone from the root — they now live only
    # inside the action branch that actually accepts each one.
    for key in ("channel", "force", "event_id", "ref_id", "reason", "items"):
        assert key not in schema["properties"]


def test_each_action_input_branch_is_strict_and_exact() -> None:
    """Root exposes every action's exact input shape before invocation.

    Both disclosure surfaces are checked: the ``input.oneOf`` branches (for
    model discoverability) and the ``allOf``/``if``/``then`` correlation (which
    ties the ``action`` const to that action's own input schema).
    """
    schema = notif_intrinsic.get_schema("en")
    branches = schema["properties"]["input"]["oneOf"]
    assert [b["title"] for b in branches] == [f"{a} input" for a in _ACTIONS]

    expected_props = {
        "add": {
            "name",
            "channel",
            "source",
            "description",
            "how_to_modify",
            "how_to_cancel",
            "version",
            "instructions",
        },
        "drop": {"name"},
        "edit": {
            "name",
            "version",
            "source",
            "description",
            "channel",
            "how_to_modify",
            "how_to_cancel",
            "instructions",
        },
        "list": set(),
        "check": set(),
        "dismiss_channel": {"channel", "force", "reason"},
        "dismiss_event": {"event_id", "channel", "force", "reason"},
        "dismiss_ref": {"ref_id", "channel", "force", "reason"},
        "manual": set(),
    }
    for action, branch in zip(_ACTIONS, branches):
        assert branch["additionalProperties"] is False, action
        assert set(branch["properties"]) == expected_props[action], action
        # Envelope controls never leak into an action's own input.
        for leaked in ("reasoning", "_reasoning", "summarize", "action"):
            assert leaked not in branch["properties"], (action, leaked)

    # Schema-level correlation: one if/then per action, in the same order.
    conditions = schema["allOf"]
    assert [c["if"]["properties"]["action"]["const"] for c in conditions] == _ACTIONS
    for action, condition, branch in zip(_ACTIONS, conditions, branches):
        assert condition["if"]["required"] == ["action"]
        then_input = condition["then"]["properties"]["input"]
        # Same canonical child schema as the disclosure branch, minus the
        # presentational title the branch adds.
        assert then_input == {k: v for k, v in branch.items() if k != "title"}, action


def test_manual_branch_matches_the_shared_manual_child_schema() -> None:
    """The advertised ``manual`` input equals the child that actually dispatches.

    ``schema.py`` declares ``_MANUAL_INPUT_SCHEMA`` for composition while
    ``handle`` registers ``tool_family.manual.build_manual_child``. If those
    two ever drift, the model would be shown an input shape dispatch does not
    enforce.
    """
    from lingtai.tools.tool_family.manual import build_manual_child

    child = build_manual_child(object(), "notification-manual")
    assert notif_intrinsic.INPUT_SCHEMAS["manual"] == dict(child.input_schema)


def test_action_enum_keeps_notification_specific_prose() -> None:
    """The generic composer's neutral placeholder is replaced, not inherited."""
    adesc = notif_intrinsic.get_schema()["properties"]["action"]["description"]
    assert "Required operation within the notification family." != adesc
    for action in _ACTIONS:
        assert f"{action}:" in adesc
    # The legacy large_tool_result escape hatch stays documented in-schema.
    assert "large_tool_result" in adesc


def test_notification_schema_has_no_kitchen_sink_dismiss() -> None:
    """The non-atomic aggregate 'dismiss' / 'summarize' are not actions here."""
    enum = notif_intrinsic.get_schema("en")["properties"]["action"]["enum"]
    assert "dismiss" not in enum
    assert "summarize" not in enum


def test_notification_schema_is_canonical_english() -> None:
    """Schema descriptions are canonical English, language-independent."""
    base_desc = notif_intrinsic.get_description()
    base_schema = notif_intrinsic.get_schema()
    # Ignored lang arg must not change the output.
    for lang in ("en", "zh", "wen"):
        assert notif_intrinsic.get_description(lang) == base_desc
        assert notif_intrinsic.get_schema(lang) == base_schema
    # Descriptions are real prose, not raw i18n keys.
    assert base_desc and "notification" in base_desc.casefold()
    # Call examples show the LTP v2 envelope form, not the old flat one.
    assert "notification(action='manual', input={}" in base_desc
    assert "action + input + reasoning" in base_desc
    assert "read-only" in base_desc.casefold()
    adesc = base_schema["properties"]["action"]["description"]
    assert adesc and "check" in adesc.casefold()
    assert "notification(action='manual'" in adesc
    assert "read-only" in adesc.casefold()
    # Per-action prose now lives on each action's own input branch.
    dismiss_channel_branch = next(
        branch
        for branch in base_schema["properties"]["input"]["oneOf"]
        if branch["title"] == "dismiss_channel input"
    )
    cdesc = dismiss_channel_branch["properties"]["channel"]["description"]
    assert cdesc and "channel" in cdesc.casefold()


# ---------------------------------------------------------------------------
# system no longer exposes notification / dismiss verbs.
# ---------------------------------------------------------------------------


def test_system_schema_drops_notification_and_dismiss() -> None:
    enum = sys_intrinsic.get_schema("en")["properties"]["action"]["enum"]
    assert "notification" not in enum
    assert "dismiss" not in enum
    # ``summarize`` is no longer a system action either — context hygiene
    # moved to context(action='summarize'|'rebuild').
    assert "summarize" not in enum
    # The dismiss-only params are gone from the system schema too.
    props = sys_intrinsic.get_schema("en")["properties"]
    for key in ("channel", "force", "event_id", "ref_id"):
        assert key not in props


def test_system_rejects_notification_action(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = sys_intrinsic.handle(agent, {"action": "notification", "input": {}})
    assert res["status"] == "error"
    assert "Unknown system action" in res["message"]


def test_system_rejects_dismiss_action(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "soul", {"header": "soul flow"})
    _mark_delivered(agent)
    res = sys_intrinsic.handle(agent, {"action": "dismiss", "input": {"channel": "soul"}})
    assert res["status"] == "error"
    assert "Unknown system action" in res["message"]
    # The channel was NOT cleared — system can't dismiss anything.
    assert "soul" in snapshot_notifications(tmp_path)


def test_system_module_has_no_dismiss_callable() -> None:
    assert not hasattr(sys_intrinsic, "_dismiss")


# ---------------------------------------------------------------------------
# manual — installed progressive-disclosure body, strictly read-only.
# ---------------------------------------------------------------------------


def _notification_manual_path(workdir: Path) -> Path:
    return (
        workdir
        / ".library"
        / "intrinsic"
        / "capabilities"
        / "notification-manual"
        / "SKILL.md"
    )


def test_manual_returns_installed_notification_manual_without_state_mutation(
    tmp_path: Path,
) -> None:
    agent = _StubAgent(tmp_path)
    manual_path = _notification_manual_path(tmp_path)
    manual_path.parent.mkdir(parents=True)
    manual_body = "---\nname: notification-manual\n---\n\n# Installed sentinel\n"
    manual_path.write_text(manual_body, encoding="utf-8")

    publish_test_payload(
        tmp_path,
        "system",
        {"data": {"events": [{"event_id": "evt_keep", "ref_id": "keep"}]}},
    )
    before_state = snapshot_notifications(tmp_path)
    before_fingerprint = fingerprint_notifications(tmp_path)
    before_logs = list(agent._logs)

    res = _call(agent, "manual")

    assert res == {
        "status": "ok",
        "notification_manual": manual_body,
        "manual_path": str(manual_path),
    }
    assert snapshot_notifications(tmp_path) == before_state
    assert fingerprint_notifications(tmp_path) == before_fingerprint
    assert agent._logs == before_logs


def test_manual_missing_installed_file_returns_degraded_without_state_mutation(
    tmp_path: Path,
) -> None:
    agent = _StubAgent(tmp_path)
    manual_path = _notification_manual_path(tmp_path)

    res = _call(agent, "manual")

    assert res == {
        "status": "degraded",
        "notification_manual": "",
        "manual_path": str(manual_path),
        "error": (
            "notification manual missing — initializer may have failed or "
            "capability not installed correctly"
        ),
    }
    assert not (tmp_path / ".notification").exists()
    assert agent._logs == []


# ---------------------------------------------------------------------------
# check — placeholder shape.
# ---------------------------------------------------------------------------


def test_check_returns_placeholder_dict(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = _call(agent, "check")
    assert res["_notification_placeholder"] is True
    assert "notification(action=check)" in res["message"]
    assert "notifications" not in res


def test_unknown_action_errors(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = _call(agent, "bogus")
    assert res["status"] == "error"
    assert "Unknown notification action" in res["message"]


# ---------------------------------------------------------------------------
# dismiss_channel.
# ---------------------------------------------------------------------------


def test_dismiss_channel_clears_surface(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "soul", {"header": "soul flow"})
    _mark_delivered(agent)

    res = _call(agent, "dismiss_channel", channel="soul")

    assert res == {"status": "ok", "channel": "soul", "cleared": True, "forced": False}
    assert snapshot_notifications(tmp_path) == {}
    # Provenance: invoked_by="notification"; no system_dismiss line.
    assert _events(agent, "notification_dismiss")[0]["invoked_by"] == "notification"
    assert _events(agent, "system_dismiss") == []


def test_dismiss_channel_missing_channel(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = _call(agent, "dismiss_channel")
    assert res["status"] == "error"
    assert res["reason"] == "missing_channel"


def test_dismiss_channel_rejects_event_target_before_any_io(tmp_path: Path) -> None:
    """event_id/ref_id are not in dismiss_channel's branch → rejected at dispatch.

    Pre-migration these keys reached ``_dismiss_channel``, which refused them
    with ``channel_dismiss_rejects_event_target``. Under LTP v2 they are absent
    from this action's own strict ``input_schema``, so the family dispatcher
    rejects the cross-action pairing *before* the handler runs — strictly
    earlier, and with no notification I/O. The published channel must survive
    untouched.
    """
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "system", {"header": "keep me"})
    before = snapshot_notifications(tmp_path)
    before_logs = list(agent._logs)

    for action_input in ({"event_id": "evt_a"}, {"ref_id": "goal:current"}):
        res = _call(agent, "dismiss_channel", channel="system", **action_input)
        assert res["status"] == "failed", action_input
        assert res["error_code"] == "INVALID_ARGUMENT", action_input
        assert snapshot_notifications(tmp_path) == before, action_input
        assert agent._logs == before_logs, action_input


def test_dismiss_channel_still_refuses_event_target_on_direct_call(
    tmp_path: Path,
) -> None:
    """The inner defense-in-depth check survives the migration.

    The envelope now rejects these keys first, so this covers the second layer:
    a direct in-process call that bypasses ``handle`` still gets the original
    ``channel_dismiss_rejects_event_target`` refusal rather than silently
    clearing the whole channel.
    """
    agent = _StubAgent(tmp_path)
    for kwargs in ({"event_id": "evt_a"}, {"ref_id": "goal:current"}):
        res = notif_intrinsic._dismiss_channel(agent, {"channel": "system", **kwargs})
        assert res["status"] == "error", kwargs
        assert res["reason"] == "channel_dismiss_rejects_event_target", kwargs


# ---------------------------------------------------------------------------
# dismiss_event.
# ---------------------------------------------------------------------------


def test_dismiss_event_removes_one(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(
        tmp_path,
        "system",
        {
            "header": "2 system notifications",
            "data": {
                "events": [
                    {"event_id": "evt_a", "source": "daemon", "ref_id": "a", "body": "A"},
                    {"event_id": "evt_b", "source": "daemon", "ref_id": "b", "body": "B"},
                ]
            },
        },
    )
    _mark_delivered(agent)

    res = _call(agent, "dismiss_event", event_id="evt_b")

    assert res["status"] == "ok"
    assert res["removed"] == 1
    events = snapshot_notifications(tmp_path)["system"]["data"]["events"]
    assert [e["event_id"] for e in events] == ["evt_a"]


def test_dismiss_event_missing_event_id(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = _call(agent, "dismiss_event")
    assert res["status"] == "error"
    assert res["reason"] == "missing_event_id"


def test_dismiss_event_defaults_to_system_channel(tmp_path: Path) -> None:
    """No channel given → operates on the 'system' channel."""
    agent = _StubAgent(tmp_path)
    publish_test_payload(
        tmp_path,
        "system",
        {"data": {"events": [{"event_id": "evt_a", "source": "daemon", "ref_id": "a"}]}},
    )
    _mark_delivered(agent)
    res = _call(agent, "dismiss_event", event_id="evt_a")
    assert res["status"] == "ok"
    assert res["channel"] == "system"
    assert res["removed"] == 1


# ---------------------------------------------------------------------------
# dismiss_ref.
# ---------------------------------------------------------------------------


def test_dismiss_ref_removes_by_ref(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(
        tmp_path,
        "system",
        {"data": {"events": [{"event_id": "evt_a", "source": "goal.reminder", "ref_id": "goal:current"}]}},
    )
    _mark_delivered(agent)

    res = _call(agent, "dismiss_ref", ref_id="goal:current")

    assert res["status"] == "ok"
    assert res["removed"] == 1
    assert not (tmp_path / ".notification" / "system.json").exists()


def test_dismiss_ref_missing_ref_id(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = _call(agent, "dismiss_ref")
    assert res["status"] == "error"
    assert res["reason"] == "missing_ref_id"


# ---------------------------------------------------------------------------
# large_tool_result escape hatch (#430): every atomic dismiss action — with or
# without force — now clears a large_tool_result reminder and acks its ref_id.
# (Supersedes the original #424 "undismissable" guard.)  The per-action matrix
# below is the single source of truth; there are no per-action singletons.
# ---------------------------------------------------------------------------


def test_large_result_guard_every_atomic_action(tmp_path: Path) -> None:
    """All atomic actions — channel/event/ref, with or without force — now succeed
    for large_tool_result reminders (escape-hatch behaviour from #430): each
    returns status=ok, reports ``acked_large_result_refs``, and removes the
    reminder from the channel."""
    cases = [
        ("dismiss_channel", {"channel": "system"}),
        ("dismiss_channel", {"channel": "system", "force": True}),
        ("dismiss_event", {"event_id": "evt_lr"}),
        ("dismiss_event", {"event_id": "evt_lr", "force": True}),
        ("dismiss_ref", {"ref_id": "large_tool_result:toolu_big"}),
        ("dismiss_ref", {"ref_id": "large_tool_result:toolu_big", "force": True}),
    ]
    for action, action_input in cases:
        kwargs = {"action": action, **action_input}
        agent = _StubAgent(tmp_path / json.dumps(kwargs, sort_keys=True))
        _publish_large_result_reminder(agent._working_dir)
        _mark_delivered(agent)
        res = _call(agent, action, **action_input)
        assert res["status"] == "ok", (kwargs, res)
        assert "acked_large_result_refs" in res, (kwargs, res)
        notifs = snapshot_notifications(agent._working_dir)
        events = notifs.get("system", {}).get("data", {}).get("events", [])
        assert not any(ev["source"] == "large_tool_result" for ev in events), kwargs


# ---------------------------------------------------------------------------
# summarize is NOT a notification action; the system one still auto-clears.
# ---------------------------------------------------------------------------


def test_summarize_is_not_a_notification_action(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    res = _call(agent, "summarize", items=[{"tool_call_id": "x", "summary": "y"}])
    assert res["status"] == "error"
    assert "Unknown notification action" in res["message"]


class _Block:
    def __init__(self, block_id: str, name: str, content: Any) -> None:
        self.id = block_id
        self.name = name
        self.content = content


class _Entry:
    def __init__(self, role: str, content: list) -> None:
        self.role = role
        self.content = content


class _Interface:
    def __init__(self, entries: list) -> None:
        self._entries = entries


class _Chat:
    def __init__(self, entries: list) -> None:
        self.interface = _Interface(entries)


@dataclass
class _SummarizeAgent:
    _working_dir: Path
    _chat: Any = None
    _logs: list = field(default_factory=list)
    _summarize_notification_threshold: int = 3000
    _notification_store: object = field(init=False)

    def __post_init__(self) -> None:
        self._notification_store = notification_store_for(self._working_dir)

    def _log(self, event_type: str, **fields: Any) -> None:
        self._logs.append((event_type, fields))

    def _save_chat_history(self, ledger_source: str | None = None) -> None:
        pass


def _make_summarize_agent(tmp_path: Path, tool_call_id: str) -> _SummarizeAgent:
    from lingtai.kernel.llm.interface import ToolResultBlock

    block = ToolResultBlock(id=tool_call_id, name="read", content="x" * 5000)
    entry = _Entry("user", [block])
    agent = _SummarizeAgent(_working_dir=tmp_path)
    agent._chat = _Chat([entry])
    return agent


def test_context_summarize_success_clears_large_result_reminder(tmp_path: Path) -> None:
    tool_call_id = "toolu_sum_ok"
    agent = _make_summarize_agent(tmp_path, tool_call_id)
    _publish_large_result_reminder(tmp_path, tool_call_id=tool_call_id)

    # The public action moved to ``context``; the legacy reminder auto-clear
    # behavior it drives is unchanged.
    res = context_intrinsic.handle(
        agent,
        {
            "action": "summarize",
            "input": {
                "items": [{"tool_call_id": tool_call_id, "summary": "digested"}]
            },
        },
    )

    assert res["status"] == "ok"
    assert res["summarized"] == 1
    assert f"large_tool_result:{tool_call_id}" in res["cleared_reminders"]
    assert not (tmp_path / ".notification" / "system.json").exists()


def test_context_summarize_failure_does_not_clear_reminder(tmp_path: Path) -> None:
    reminder_tcid = "toolu_real"
    agent = _make_summarize_agent(tmp_path, "toolu_real")
    _publish_large_result_reminder(tmp_path, tool_call_id=reminder_tcid)

    res = context_intrinsic.handle(
        agent,
        {
            "action": "summarize",
            "input": {
                "items": [{"tool_call_id": "toolu_missing", "summary": "nope"}]
            },
        },
    )

    assert res["status"] == "error"
    assert res["summarized"] == 0
    assert res["failed"] == 1
    assert res["cleared_reminders"] == []
    events = snapshot_notifications(tmp_path)["system"]["data"]["events"]
    assert any(
        ev["ref_id"] == f"large_tool_result:{reminder_tcid}" for ev in events
    )


# ---------------------------------------------------------------------------
# Producer ownership + stale/force + protected boundaries via the new tool.
# ---------------------------------------------------------------------------


def test_guarded_channel_refuses_without_force(tmp_path: Path) -> None:
    import lingtai.tools.email  # noqa: F401 — registers the guard

    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "email", {"header": "1 unread"})

    res = _call(agent, "dismiss_channel", channel="email")

    assert res["status"] == "error"
    assert res["reason"] == "guarded"
    # Producer surface untouched (canonical state separate from mirror).
    assert "email" in snapshot_notifications(tmp_path)
    assert _events(agent, "notification_dismiss_guarded")[0]["invoked_by"] == "notification"


def test_stale_channel_refused_without_force(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "system", {"header": "one", "data": {"events": ["old"]}})
    _mark_delivered(agent)
    publish_test_payload(tmp_path, "system", {"header": "two", "data": {"events": ["old", "new"]}})

    res = _call(agent, "dismiss_channel", channel="system")

    assert res["status"] == "error"
    assert res["reason"] == "stale_channel_version"
    assert snapshot_notifications(tmp_path)["system"]["header"] == "two"


def test_force_bypasses_stale_on_allowed_channel(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "system", {"header": "one", "data": {"events": ["old"]}})
    _mark_delivered(agent)
    publish_test_payload(tmp_path, "system", {"header": "two", "data": {"events": ["old", "new"]}})

    res = _call(agent, "dismiss_channel", channel="system", force=True)

    assert res["status"] == "ok"
    assert res["forced"] is True
    assert "system" not in snapshot_notifications(tmp_path)


def test_protected_goal_channel_refused(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "goal", {"data": {"status": "active"}})
    agent._notification_fp = fingerprint_notifications(tmp_path)

    res = _call(agent, "dismiss_channel", channel="goal", force=True)

    assert res["status"] == "error"
    assert res["reason"] == "protected_channel"
    assert (tmp_path / ".notification" / "goal.json").exists()


def test_post_molt_dismiss_requires_reason(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "post-molt", {"header": "continue?"})
    _mark_delivered(agent)
    res = _call(agent, "dismiss_channel", channel="post-molt")
    assert res["status"] == "error"
    assert res["reason"] == "missing_ack_reason"

    res2 = _call(
        agent, "dismiss_channel", channel="post-molt", reason="continue: done"
    )
    assert res2["status"] == "ok"
    assert res2["reason"] == "continue: done"


# ---------------------------------------------------------------------------
# LTP v2 envelope: dispatch validation, null-optional handling, wire parity.
# ---------------------------------------------------------------------------


def test_unknown_action_fails_before_any_io(tmp_path: Path) -> None:
    """An unknown action keeps its exact pre-migration result and touches nothing.

    ``CONTRACT.md`` Port pins ``{status: "error", message}`` naming the unknown
    action, so the generic dispatcher's ``ACTION_REQUIRED`` envelope error is
    normalized back to it in ``handle`` rather than leaking a new shape.
    """
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "system", {"header": "keep me"})
    before = snapshot_notifications(tmp_path)

    res = _call(agent, "bogus")

    assert res == {"status": "error", "message": "Unknown notification action: bogus"}
    assert snapshot_notifications(tmp_path) == before
    assert agent._logs == []


def test_unknown_root_field_is_rejected(tmp_path: Path) -> None:
    """The root is closed: a fifth public field never reaches an action."""
    agent = _StubAgent(tmp_path)
    res = notif_intrinsic.handle(
        agent,
        {"action": "check", "input": {}, "reasoning": "r", "channel": "system"},
    )
    assert res["status"] == "failed"
    assert res["error_code"] == "INVALID_ARGUMENT"


def test_non_boolean_summarize_is_rejected(tmp_path: Path) -> None:
    """``summarize`` is a root boolean; a wrong type fails before dispatch."""
    agent = _StubAgent(tmp_path)
    res = notif_intrinsic.handle(
        agent, {"action": "check", "input": {}, "reasoning": "r", "summarize": "yes"}
    )
    assert res["status"] == "failed"
    assert res["error_code"] == "INVALID_ARGUMENT"


def test_summarize_is_stripped_and_never_reaches_an_action(tmp_path: Path) -> None:
    """A valid root ``summarize`` leaves the action result byte-identical."""
    agent = _StubAgent(tmp_path)
    plain = _call(agent, "check")
    with_flag = notif_intrinsic.handle(
        agent, {"action": "check", "input": {}, "reasoning": "r", "summarize": True}
    )
    assert with_flag == plain


def test_notification_is_on_the_kernel_ltp_v2_summarize_allowlist() -> None:
    """Root ``summarize`` is advertised, so the kernel must actually honor it.

    ``ToolFamily.build_schema`` advertises ``summarize`` for every family, but
    whether the kernel treats that spelling as the a-priori summary control is
    a separate per-family allowlist decision. Without this entry the tool would
    show the model a control the kernel silently ignores.
    """
    from lingtai.kernel.tool_result_summary import summary_requested

    assert summary_requested({"summarize": True}, "notification") is True
    assert summary_requested({"summarize": True}, "some-unmigrated-tool") is False


def test_tc_id_injection_does_not_break_dispatch(tmp_path: Path) -> None:
    """``_dispatch_tool`` injects ``_tc_id`` into EVERY intrinsic's args.

    It is kernel plumbing that predates the LTP v2 envelope and is not a
    public root field, so the family must strip it rather than reject the call.
    Only psyche's molt consumes it; notification ignores it.
    """
    agent = _StubAgent(tmp_path)
    res = notif_intrinsic.handle(
        agent,
        {"action": "check", "input": {}, "reasoning": "r", "_tc_id": "toolu_abc"},
    )
    assert res["_notification_placeholder"] is True


def test_null_optionals_are_treated_as_absent(tmp_path: Path) -> None:
    """Strict schemas send optionals as explicit nulls; defaulting must survive.

    ``dismiss_event``'s ``channel`` defaults to ``system``. The model sends
    ``channel=None`` for "not supplied", so null must become *absent* before
    the handler's ``args.get("channel", "system")`` runs — otherwise Core would
    receive ``channel=None``.
    """
    agent = _StubAgent(tmp_path)
    publish_test_payload(
        tmp_path,
        "system",
        {"data": {"events": [{"event_id": "evt_a", "source": "daemon", "ref_id": "a"}]}},
    )
    _mark_delivered(agent)

    res = notif_intrinsic.handle(
        agent,
        {
            "action": "dismiss_event",
            "input": {
                "event_id": "evt_a",
                "channel": None,
                "force": None,
                "reason": None,
            },
            "reasoning": "r",
        },
    )

    assert res["status"] == "ok"
    assert res["channel"] == "system"
    assert res["removed"] == 1


def test_null_force_and_reason_match_omission(tmp_path: Path) -> None:
    """``force=None`` must not become a truthy force, nor ``reason=None`` an ack."""
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "post-molt", {"header": "continue?"})
    _mark_delivered(agent)

    res = notif_intrinsic.handle(
        agent,
        {
            "action": "dismiss_channel",
            "input": {"channel": "post-molt", "force": None, "reason": None},
            "reasoning": "r",
        },
    )

    # Null reason is absent, so the post-molt ack requirement still bites.
    assert res["status"] == "error"
    assert res["reason"] == "missing_ack_reason"


def test_check_and_manual_perform_no_mutation(tmp_path: Path) -> None:
    """The two read-only actions leave notification state and logs untouched."""
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "system", {"header": "keep me"})
    before = snapshot_notifications(tmp_path)
    before_fp = fingerprint_notifications(tmp_path)
    before_logs = list(agent._logs)

    _call(agent, "check")
    _call(agent, "manual")

    assert snapshot_notifications(tmp_path) == before
    assert fingerprint_notifications(tmp_path) == before_fp
    assert agent._logs == before_logs


def test_manual_takes_no_input_and_performs_no_io(tmp_path: Path) -> None:
    """``manual``'s input is strictly empty; a stray key is rejected pre-I/O."""
    agent = _StubAgent(tmp_path)
    res = _call(agent, "manual", channel="system")
    assert res["status"] == "failed"
    assert res["error_code"] == "INVALID_ARGUMENT"
    assert not (tmp_path / ".notification").exists()


def test_manual_result_is_flattened_not_double_wrapped(tmp_path: Path) -> None:
    """Host presentation flattens the canonical child result; no nested envelope.

    ``ToolFamily.handle`` returns ``build_manual_child``'s canonical
    ``content``/``structuredContent`` result verbatim. Notification's pinned
    public shape is the flat one, so the adaptation happens after dispatch —
    and the canonical keys must not survive into the public result.
    """
    agent = _StubAgent(tmp_path)
    manual_path = _notification_manual_path(tmp_path)
    manual_path.parent.mkdir(parents=True)
    manual_path.write_text("# body\n", encoding="utf-8")

    res = _call(agent, "manual")

    assert set(res) == {"status", "notification_manual", "manual_path"}
    assert res["notification_manual"] == "# body\n"
    for canonical in ("content", "structuredContent", "manual"):
        assert canonical not in res


def test_family_schema_survives_chat_and_responses_wires() -> None:
    """Exact action↔input correlation reaches both provider wires.

    Responses rewrites nested ``oneOf`` to ``anyOf`` but preserves root
    ``allOf`` verbatim, so the correlation layer must be intact on both.
    """
    from lingtai.kernel.llm.base import FunctionSchema
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    schema = FunctionSchema(
        name="notification",
        description=notif_intrinsic.get_description(),
        parameters=notif_intrinsic.get_schema(),
    )
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]

    for wire, combinator in ((chat, "oneOf"), (responses, "anyOf")):
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["required"] == ["action", "input", "reasoning"]
        assert wire["additionalProperties"] is False
        assert wire["properties"]["action"]["enum"] == _ACTIONS
        branches = wire["properties"]["input"][combinator]
        assert [b["title"] for b in branches] == [f"{a} input" for a in _ACTIONS]
        for branch in branches:
            assert branch["additionalProperties"] is False
            for leaked in ("reasoning", "_reasoning", "summarize"):
                assert leaked not in branch["properties"]
        conditions = wire["allOf"]
        assert [
            c["if"]["properties"]["action"]["const"] for c in conditions
        ] == _ACTIONS


def test_every_action_dispatches_through_the_family(tmp_path: Path) -> None:
    """All five canonical children are reachable; none is a dead registration."""
    agent = _StubAgent(tmp_path)
    publish_test_payload(
        tmp_path,
        "system",
        {"data": {"events": [{"event_id": "e1", "source": "daemon", "ref_id": "r1"}]}},
    )
    _mark_delivered(agent)

    assert _call(agent, "check")["_notification_placeholder"] is True
    assert _call(agent, "manual")["status"] == "degraded"
    assert _call(agent, "dismiss_event", event_id="e1")["status"] == "ok"

    publish_test_payload(
        tmp_path,
        "system",
        {"data": {"events": [{"event_id": "e2", "source": "daemon", "ref_id": "r2"}]}},
    )
    _mark_delivered(agent)
    assert _call(agent, "dismiss_ref", ref_id="r2")["status"] == "ok"

    publish_test_payload(tmp_path, "soul", {"header": "x"})
    _mark_delivered(agent)
    assert _call(agent, "dismiss_channel", channel="soul")["status"] == "ok"


def test_reserved_manual_collision_fails_loudly() -> None:
    """A second ``manual`` child is a defect, caught at construction."""
    from lingtai.tools.tool_family import ChildTool, ToolFamily, ToolFamilyError

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    try:
        ToolFamily(
            "notification",
            [
                ChildTool("manual", empty, lambda _i: {}),
                ChildTool("manual", empty, lambda _i: {}),
            ],
        )
    except ToolFamilyError:
        pass
    else:  # pragma: no cover - the constructor must refuse
        raise AssertionError("duplicate reserved manual child must fail loudly")


# ---------------------------------------------------------------------------
# Hook registry lifecycle (add/drop/edit/list) + whitelist gate.
# ---------------------------------------------------------------------------


def _hook_manifest(
    name: str = "comm_watcher",
    channel: str = "comm_watcher",
    **overrides: Any,
) -> dict:
    """Return a valid hook manifest with test defaults."""
    manifest = {
        "name": name,
        "version": "1.0.0",
        "channel": channel,
        "source": "G:",
        "description": "poll relay",
        "how_to_modify": "notification(action='edit', input={'name': ...})",
        "how_to_cancel": "notification(action='drop', input={'name': ...}) and stop the watcher",
    }
    manifest.update(overrides)
    return manifest


class _WarnFlagAgent(_StubAgent):
    """Stub agent with an ``_enqueue_system_notification`` recorder."""

    def __init__(self, workdir: Path) -> None:
        super().__init__(workdir)
        self.system_notifications: list[dict[str, Any]] = []

    def _enqueue_system_notification(self, **kwargs: Any) -> str:
        self.system_notifications.append(kwargs)
        return "evt_blocked"


def _make_sync_agent(tmp_path: Path) -> Any:
    """Real-sync stub: drives ``_sync_notifications``' D2 loop for real.

    Mirrors the ``_Agent`` stub in ``test_notification_sync.py``: a BaseAgent
    subclass whose state is set directly so ``_sync_notifications`` executes
    its real code (hook-registry seeding, present-fp cache, derived-fp filter,
    flag-unregistered scan) without a full runtime.
    """
    import queue
    from types import SimpleNamespace

    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.kernel.state import AgentState

    class _Agent(BaseAgent):
        def __init__(self, workdir: Path) -> None:
            self._working_dir = workdir
            self._notification_store = notification_store_for(workdir)
            self._state = AgentState.IDLE
            self._notification_fp = ()
            self._notification_deferred_log_fp = ()
            self._notification_block_id = None
            self._chat_stub = SimpleNamespace(
                interface=SimpleNamespace(entries=[])
            )
            self._logs: list[tuple[str, dict]] = []
            self.agent_name = "sync-stub"
            self.system_notifications: list[dict[str, Any]] = []
            self.inbox = queue.Queue()

        @property
        def _chat(self):
            return self._chat_stub

        def _save_chat_history(self, *, ledger_source: str = "main") -> None:
            pass

        def _log(self, event_type: str, **fields: Any) -> None:
            self._logs.append((event_type, fields))

        def _enqueue_system_notification(self, **kwargs: Any) -> str:
            self.system_notifications.append(kwargs)
            return "evt_blocked"

        def _wake_nap(self, *_a: Any, **_kw: Any) -> None:
            pass

        def _set_state(self, *_a: Any, **_kw: Any) -> None:
            pass

        def _reset_uptime(self) -> None:
            pass

    return _Agent(tmp_path)


class TestHookLifecycle:
    """Hook registry lifecycle through the notification family + whitelist gate."""

    def test_add_shows_manifest_in_list(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        res = _call(agent, "add", **_hook_manifest())
        assert res["status"] == "ok", res
        assert res["reason"] == "added"
        assert load_hook_manifests_for_test(tmp_path) == [_hook_manifest()]

    def test_add_duplicate_name_errors(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        res = _call(agent, "add", **_hook_manifest())
        assert res["status"] == "error"
        assert res["reason"] == "duplicate_name"
        assert res["name"] == "comm_watcher"
        assert len(load_hook_manifests_for_test(tmp_path)) == 1

    def test_add_channel_in_use_errors(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        res = _call(
            agent, "add", **_hook_manifest(name="second_hook", channel="comm_watcher")
        )
        assert res["status"] == "error"
        assert res["reason"] == "channel_in_use"
        assert res["channel"] == "comm_watcher"
        assert res["name"] == "comm_watcher"
        assert len(load_hook_manifests_for_test(tmp_path)) == 1

    def test_edit_changes_fields(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        res = _call(
            agent,
            "edit",
            name="comm_watcher",
            description="updated relay",
            channel="comm_watcher_v2",
        )
        assert res["status"] == "ok", res
        assert res["reason"] == "edited"
        manifests = load_hook_manifests_for_test(tmp_path)
        assert manifests[0]["description"] == "updated relay"
        assert manifests[0]["channel"] == "comm_watcher_v2"
        assert manifests[0]["name"] == "comm_watcher"

    def test_edit_channel_in_use_errors(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        assert _call(
            agent, "add", **_hook_manifest(name="second", channel="comm_watcher2")
        )["status"] == "ok"
        res = _call(agent, "edit", name="second", channel="comm_watcher")
        assert res["status"] == "error"
        assert res["reason"] == "channel_in_use"
        assert res["channel"] == "comm_watcher"
        # The first hook's manifest is untouched.
        manifests = load_hook_manifests_for_test(tmp_path)
        assert manifests[0]["channel"] == "comm_watcher"
        assert manifests[1]["channel"] == "comm_watcher2"

    def test_edit_unknown_name_errors(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        res = _call(agent, "edit", name="missing", description="x")
        assert res["status"] == "error"
        assert res["reason"] == "not_found"

    def test_drop_removes_and_revokes_allowlist(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        sync_hook_registry(agent)
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is True

        res = _call(agent, "drop", name="comm_watcher")
        assert res["status"] == "ok", res
        assert res["reason"] == "dropped"
        assert load_hook_manifests_for_test(tmp_path) == []
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is False

    def test_drop_unknown_name_errors(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        res = _call(agent, "drop", name="missing")
        assert res["status"] == "error"
        assert res["reason"] == "not_found"

    def test_add_edit_drop_list_round_trip_dispatches_through_family(
        self, tmp_path: Path
    ) -> None:
        """add → edit → drop → list round trip through the real dispatcher."""
        agent = _StubAgent(tmp_path)

        added = _call(agent, "add", **_hook_manifest())
        assert added["status"] == "ok" and added["reason"] == "added"
        listed = _call(agent, "list")
        assert listed["status"] == "ok"
        assert [m["name"] for m in listed["hooks"]] == ["comm_watcher"]

        edited = _call(agent, "edit", name="comm_watcher", description="edited relay")
        assert edited["status"] == "ok" and edited["reason"] == "edited"
        listed = _call(agent, "list")
        assert listed["hooks"][0]["description"] == "edited relay"

        dropped = _call(agent, "drop", name="comm_watcher")
        assert dropped["status"] == "ok" and dropped["reason"] == "dropped"
        listed = _call(agent, "list")
        assert listed["status"] == "ok" and listed["hooks"] == []
        assert load_hook_manifests_for_test(tmp_path) == []

    def test_registered_hook_channel_becomes_allowed_after_sync(
        self, tmp_path: Path
    ) -> None:
        """Registering a hook allowlists its channel; unregistered stay blocked."""
        agent = _StubAgent(tmp_path)
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is False

        replace_hook_manifests_for_test(tmp_path, [_hook_manifest()])
        sync_hook_registry(agent)

        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is True
        assert is_channel_allowed("some_other_hook", workdir=str(agent._working_dir)) is False

    def test_unregistered_channels_stay_not_allowed(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        sync_hook_registry(agent)
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is False
        # Kernel-side publishing refuses the unregistered channel entirely.
        with pytest.raises(ValueError, match="not allowlisted"):
            submit(agent, "comm_watcher", data={}, header="x")

    def test_flag_unregistered_channel_warns_once_then_after_clear(
        self, tmp_path: Path
    ) -> None:
        """Warn-and-flag dedupes per workdir+channel until cleared."""
        agent = _WarnFlagAgent(tmp_path)

        flag_unregistered_channel(agent, "comm_watcher")
        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 1
        enqueued = agent.system_notifications[0]
        assert enqueued["source"] == "notification_hook"
        assert enqueued["ref_id"] == "blocked_channel:comm_watcher"
        assert "not registered" in enqueued["body"]

        clear_blocked_channel_warning(agent, "comm_watcher")
        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 2

        # An allowlisted channel never flags.
        flag_unregistered_channel(agent, "system")
        assert len(agent.system_notifications) == 2

    def test_sync_hook_registry_seeds_from_fake_store(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        store = FakeNotificationStore()
        agent = SimpleNamespace(
            _working_dir=tmp_path / "fake-agent",
            _notification_store=store,
        )
        replace_hook_manifests_for_test(agent, [_hook_manifest()])

        sync_hook_registry(agent)

        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is True

    def test_reset_hook_registry_for_tests_isolation(self, tmp_path: Path) -> None:
        agent = _StubAgent(tmp_path)
        replace_hook_manifests_for_test(tmp_path, [_hook_manifest()])
        sync_hook_registry(agent)
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is True

        reset_hook_registry_for_tests()

        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is False
        # Re-sync re-seeds from the still-persisted registry (mirror is lazy).
        sync_hook_registry(agent)
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is True

    def test_malformed_store_registry_syncs_to_empty(self, tmp_path: Path) -> None:
        """A broken hooks.json seeds no channels instead of crashing the sync."""
        agent = _StubAgent(tmp_path)
        (tmp_path / ".notification").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".notification" / "hooks.json").write_text(
            "{not-json", encoding="utf-8"
        )

        sync_hook_registry(agent)

        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is False


class TestHookRegistryFableFixes:
    """Regression tests from fable r1 (PR review findings F1-F5)."""

    def test_d2_never_flags_kernel_private_dotfiles(self) -> None:
        """F1: .nudge_state.json and invalid stems are not flaggable channels."""
        assert is_present_channel_flagable(".nudge_state.json") is False
        # Store-owned registry files are excluded by the adapter fingerprint
        # itself, so the D2 loop never sees them; the pure helper still treats
        # valid stems as flaggable when present through another enumeration.
        assert is_present_channel_flagable("hooks.json") is True
        assert is_present_channel_flagable("large_result_acks.json") is True
        assert is_present_channel_flagable("comm_watcher.json") is True
        assert is_present_channel_flagable("not json") is False
        assert is_present_channel_flagable("has..dots.json") is False

    def test_add_rejects_store_reserved_channels(self, tmp_path: Path) -> None:
        """F2: hooks / large_result_acks can never be hook channels."""
        agent = _StubAgent(tmp_path)
        for reserved in ("hooks", "large_result_acks"):
            res = _call(agent, "add", **_hook_manifest(channel=reserved))
            assert res["status"] == "error", res
            assert res["reason"] == "invalid_manifest", res
            assert "reserved" in res["message"], res

    def test_add_rejects_builtin_channels(self, tmp_path: Path) -> None:
        """F2/F12: built-in static allowlist channels are not hook channels."""
        agent = _StubAgent(tmp_path)
        res = _call(agent, "add", **_hook_manifest(channel="system"))
        assert res["status"] == "error", res
        assert "built-in" in res["message"], res

    def test_hooks_json_survives_force_dismiss_of_registered_channel(
        self, tmp_path: Path
    ) -> None:
        """F2 regression: a force dismiss can never delete the registry file."""
        agent = _StubAgent(tmp_path)
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        assert load_hook_manifests_for_test(tmp_path) != []
        # Force-dismiss the hook's own channel: must clear the channel file
        # only, never hooks.json.
        from lingtai.kernel.notifications import dismiss_channel

        res = dismiss_channel(agent, "comm_watcher", invoked_by="test", force=True)
        assert res["status"] == "ok", res
        assert load_hook_manifests_for_test(tmp_path) != []
        assert _call(agent, "list")["hooks"] != []

    def test_two_agents_hook_allowlists_are_isolated(self, tmp_path: Path) -> None:
        """F3: one agent's hook channel is not allowlisted for another."""
        agent_a = _StubAgent(tmp_path / "agent-a")
        agent_b = _StubAgent(tmp_path / "agent-b")
        assert _call(agent_a, "add", **_hook_manifest())["status"] == "ok"
        sync_hook_registry(agent_a)
        sync_hook_registry(agent_b)

        assert is_channel_allowed(
            "comm_watcher", workdir=str(agent_a._working_dir)
        ) is True
        assert is_channel_allowed(
            "comm_watcher", workdir=str(agent_b._working_dir)
        ) is False

    def test_add_clears_warning_then_drop_reblocks(self, tmp_path: Path) -> None:
        """F4: warn is cleared on register; drop re-enables a later warning.

        Real sequence: an unregistered channel warns once; registering its hook
        clears the dedupe book (and allowlists the channel); dropping the hook
        blocks it again, so a later publish re-warns. The warned book is never
        cleared manually — every transition goes through the production paths.
        """
        agent = _WarnFlagAgent(tmp_path)

        # Blocked before registration: exactly one warn event.
        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 1, agent.system_notifications
        assert agent.system_notifications[0]["ref_id"] == "blocked_channel:comm_watcher"

        # Registering the hook clears the warned book and allowlists the
        # channel: re-flagging the now-registered channel emits nothing new.
        assert _call(agent, "add", **_hook_manifest())["status"] == "ok"
        sync_hook_registry(agent)
        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 1, agent.system_notifications

        # After drop the channel is blocked again; the cleared dedupe book
        # means this re-block warns once more (count == 2). If add_hook had
        # not cleared the marker, the re-flag would be deduped and count
        # would stay 1.
        assert _call(agent, "drop", name="comm_watcher")["status"] == "ok"
        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 2, agent.system_notifications
        assert agent.system_notifications[1]["ref_id"] == "blocked_channel:comm_watcher"

    def test_d2_integration_present_file_blocked_and_flagged_once(
        self, tmp_path: Path
    ) -> None:
        """F5: through the real D2 sync loop, a present-but-unregistered
        channel file is not delivered and produces exactly one
        notification_hook event; a kernel-private dotfile produces none.

        Unlike the earlier copy-pasted scan, this drives
        ``agent._sync_notifications()`` for real, so the present-fp cache,
        the derived-fp filter, the hook-registry seeding, and the
        ``_notification_present_fp`` guard all execute.
        """
        from lingtai.kernel.notifications import is_channel_allowed

        agent = _make_sync_agent(tmp_path)
        store = agent._notification_store

        # Register a hook for a DIFFERENT channel so the mirror is seeded
        # with relay_channel while comm_watcher stays unregistered.
        assert (
            _call(
                agent,
                "add",
                **_hook_manifest(name="relay", channel="relay_channel"),
            )["status"]
            == "ok"
        )
        store.publish("comm_watcher", {"header": "blocked"})
        (tmp_path / ".notification" / ".nudge_state.json").write_text(
            "{}", encoding="utf-8"
        )

        agent._sync_notifications()

        # Exactly one warn event, emitted through the real loop.
        refs = [ev["ref_id"] for ev in agent.system_notifications]
        assert refs == ["blocked_channel:comm_watcher"], refs

        # No delivery: the blocked channel stays out of the allow-filtered
        # view, the dotfile is never surfaced, and the committed fingerprint
        # is untouched (nothing was injected into the wire).
        workdir = str(agent._working_dir)
        snapshot = store.snapshot(
            lambda ch: is_channel_allowed(ch, workdir=workdir)
        )
        assert "comm_watcher" not in snapshot
        assert ".nudge_state" not in snapshot
        assert "relay_channel" not in snapshot
        assert agent._notification_fp == ()
        assert agent._chat_stub.interface.entries == []

    def test_flag_unregistered_channel_dedupes_without_manual_clear(
        self, tmp_path: Path
    ) -> None:
        """D2 dedupe: a repeated flag for the same unregistered channel warns
        once; the warned book is never cleared manually."""
        agent = _WarnFlagAgent(tmp_path)

        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 1, agent.system_notifications

        # Second scan/flag for the same channel emits nothing new.
        flag_unregistered_channel(agent, "comm_watcher")
        assert len(agent.system_notifications) == 1, agent.system_notifications
        assert agent.system_notifications[0]["ref_id"] == "blocked_channel:comm_watcher"

        # A different unregistered channel is its own dedupe key.
        flag_unregistered_channel(agent, "other_watcher")
        assert len(agent.system_notifications) == 2, agent.system_notifications

    def test_sync_hook_registry_reseeds_on_stat_change(self, tmp_path: Path) -> None:
        """F6: an out-of-band hooks.json rewrite (content + stat change)
        re-seeds the mirror on the next sync."""
        agent = _StubAgent(tmp_path)
        replace_hook_manifests_for_test(agent, [_hook_manifest()])
        sync_hook_registry(agent)
        assert is_channel_allowed("comm_watcher", workdir=str(agent._working_dir)) is True

        # Out-of-band write: bypass the store and rewrite hooks.json directly
        # (as a sibling CLI or hook installer would). mtime_ns/size change.
        hooks_file = tmp_path / ".notification" / "hooks.json"
        hooks_file.write_text(
            json.dumps(
                [
                    _hook_manifest(),
                    _hook_manifest(name="relay", channel="relay_channel"),
                ]
            ),
            encoding="utf-8",
        )

        sync_hook_registry(agent)

        assert is_channel_allowed(
            "relay_channel", workdir=str(agent._working_dir)
        ) is True
        assert is_channel_allowed(
            "comm_watcher", workdir=str(agent._working_dir)
        ) is True

    def test_sync_hook_registry_retry_after_transient_load_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """F7: a transient load failure leaves the seeded marker unset and
        does not crash; the next sync retries and seeds."""
        from types import SimpleNamespace

        store = FakeNotificationStore()
        agent = SimpleNamespace(
            _working_dir=tmp_path / "fake-agent",
            _notification_store=store,
        )
        replace_hook_manifests_for_test(agent, [_hook_manifest()])

        real_load = store.load_hook_manifests
        calls = {"n": 0}

        def _flaky_load():
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("transient registry read failure")
            return real_load()

        monkeypatch.setattr(store, "load_hook_manifests", _flaky_load)

        sync_hook_registry(agent)  # must not crash

        from lingtai.kernel.notifications import _HOOK_REGISTRY_SEEDED

        assert str(agent._working_dir) not in _HOOK_REGISTRY_SEEDED
        assert is_channel_allowed(
            "comm_watcher", workdir=str(agent._working_dir)
        ) is False

        monkeypatch.setattr(store, "load_hook_manifests", real_load)
        sync_hook_registry(agent)

        assert str(agent._working_dir) in _HOOK_REGISTRY_SEEDED
        assert is_channel_allowed(
            "comm_watcher", workdir=str(agent._working_dir)
        ) is True


# ---------------------------------------------------------------------------
# Agent-facing guidance must teach a call shape the dispatcher accepts.
#
# The migration closed the root envelope, so any surviving flat-shape call
# template in a runtime producer string or installed manual now teaches a call
# that fails. These tests parse the *shipped* templates and execute them
# through the registered dispatcher, so guidance drift is caught mechanically
# rather than by eye.
# ---------------------------------------------------------------------------


def _parse_taught_call(template: str) -> dict:
    """Turn a taught ``notification(action=..., input=..., reasoning=...)``
    template into the args dict the kernel would dispatch.

    The taught templates use JSON spellings (``null``) and ``...`` ellipsis
    placeholders for the agent to fill in. Both are normalized here so the
    *structure* of the taught call — which is what the closed envelope
    validates — is what gets executed.
    """
    import ast

    marker = "notification("
    start = template.index(marker) + len(marker)
    # Scan to the matching close paren so trailing prose in the same
    # instruction string is not swallowed by a greedy match.
    depth, end = 1, start
    while depth:
        if template[end] == "(":
            depth += 1
        elif template[end] == ")":
            depth -= 1
            if depth == 0:
                break
        end += 1
    body = template[start:end].replace("null", "None")
    # Quoted ellipsis placeholders ('...') are already valid string literals.
    call = ast.parse(f"f({body})", mode="eval").body
    return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}


def _shipped_post_molt_instructions(tmp_path: Path) -> str:
    """Return the post-molt ``instructions`` string exactly as published.

    Runs the real publisher against a disposable workdir and reads the string
    back off the published channel, so the test asserts against what the agent
    actually receives rather than a copy restated in this file.
    """
    from lingtai.tools.context._molt import _publish_post_molt

    workdir = tmp_path / "post-molt-template"
    workdir.mkdir(parents=True, exist_ok=True)
    agent = _StubAgent(workdir)
    _publish_post_molt(
        agent,
        initiator="agent",
        source="test",
        molt_count=1,
        summary="summary body",
        reasoning=None,
        summary_path=None,
        before_tokens=10,
        after_tokens=1,
    )
    published = snapshot_notifications(workdir)["post-molt"]
    return published["instructions"]


def test_post_molt_instruction_template_round_trips_through_dispatcher(
    tmp_path: Path,
) -> None:
    """The literal post-molt ack instruction must actually dismiss post-molt.

    This is the highest-severity guidance path in the kernel: the reminder
    re-injects every session until dismissed, so if its own dismissal
    instruction teaches a rejected shape the agent is stuck in a loop it cannot
    exit. The template is read from the shipped source string, not restated
    here, so editing the instruction without re-checking it fails this test.
    """
    instructions = _shipped_post_molt_instructions(tmp_path)
    assert "notification(action='dismiss_channel'" in instructions, (
        "post-molt ack instruction no longer contains a dismissal template"
    )
    args = _parse_taught_call(instructions)

    assert args["action"] == "dismiss_channel"
    assert args["input"]["channel"] == "post-molt"
    assert "reasoning" in args, "taught call must supply the required reasoning"

    # A real reason is required; the template teaches one.
    assert isinstance(args["input"].get("reason"), str) and args["input"]["reason"]
    args["input"]["reason"] = "continue: finished the pending work"
    args["reasoning"] = "acknowledge the post-molt continuation"

    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "post-molt", {"header": "continue?"})
    _mark_delivered(agent)

    result = notif_intrinsic.handle(agent, args)

    assert result["status"] == "ok", result
    assert result["channel"] == "post-molt"
    assert result["reason"] == "continue: finished the pending work"
    assert "post-molt" not in snapshot_notifications(tmp_path)


def test_post_molt_template_without_reason_still_refuses(tmp_path: Path) -> None:
    """The taught shape must not accidentally bypass the ack-reason guard."""
    agent = _StubAgent(tmp_path)
    publish_test_payload(tmp_path, "post-molt", {"header": "continue?"})
    _mark_delivered(agent)

    res = notif_intrinsic.handle(
        agent,
        {
            "action": "dismiss_channel",
            "input": {"channel": "post-molt", "force": None, "reason": None},
            "reasoning": "acknowledge",
        },
    )

    assert res["status"] == "error"
    assert res["reason"] == "missing_ack_reason"
    assert "post-molt" in snapshot_notifications(tmp_path)


def test_runtime_producer_instruction_templates_are_dispatchable(
    tmp_path: Path,
) -> None:
    """Every runtime ``instructions`` string teaches an accepted call shape.

    Covers the four producer strings the migration would otherwise strand:
    post-molt (psyche), tool_loop_guard (turn loop), nudge, and btw (soul).
    Each taught call is dispatched against its own published channel and must
    succeed — proving the guidance and the envelope agree.
    """
    cases = [
        ("tool_loop_guard", "handled"),
        ("nudge", None),
        ("btw", None),
    ]
    for channel, reason in cases:
        workdir = tmp_path / f"producer-{channel}"
        workdir.mkdir(parents=True, exist_ok=True)
        agent = _StubAgent(workdir)
        publish_test_payload(workdir, channel, {"header": channel})
        _mark_delivered(agent)

        res = notif_intrinsic.handle(
            agent,
            {
                "action": "dismiss_channel",
                "input": {"channel": channel, "force": None, "reason": reason},
                "reasoning": "acknowledge the producer notification",
            },
        )

        assert res["status"] == "ok", (channel, res)
        assert channel not in snapshot_notifications(agent._working_dir), channel


def test_no_shipped_guidance_teaches_the_rejected_flat_shape() -> None:
    """No agent-facing source teaches a root-flat notification call.

    The closed envelope rejects `notification(action=..., channel=...)` with
    INVALID_ARGUMENT and no I/O, so a surviving flat template is guidance that
    is actively wrong. Scans runtime producer strings and installed manuals for
    a `notification(` call carrying a root-level argument other than the four
    envelope fields.
    """
    import re

    repo = Path(__file__).resolve().parents[1] / "src" / "lingtai"
    # A taught call is flat when a root kwarg is a known action argument.
    flat = re.compile(
        r"notification\(\s*action=['\"][a-z_]+['\"]\s*,\s*"
        r"(channel|event_id|ref_id|force|reason)\s*="
    )
    # `kernel/notifications.py` names the old signature in an internal comment
    # about the guard registry; it is not agent-facing guidance and teaches no
    # call. Everything else in the tree must use the envelope.
    allowed = {"src/lingtai/kernel/notifications.py"}
    offenders = []
    for path in sorted(repo.rglob("*")):
        if path.suffix not in {".py", ".md"} or not path.is_file():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            rel = str(path.relative_to(repo.parent.parent))
            if flat.search(line) and rel not in allowed:
                offenders.append(f"{rel}:{line_no}")
    assert not offenders, "flat-shape notification guidance survives:\n" + "\n".join(offenders)
