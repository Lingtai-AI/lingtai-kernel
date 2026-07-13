"""Tests for the two co-located programmable Task Card renderer template assets
(bash-async + daemon) and the two-entry-point human contract that routes to them.

These bind four properties, not merely valid Task Card JSON:

1. **The two-entry-point human contract.** The top-level Telegram manual (an agent
   may load only this one) expresses, *bound together in the programmable-Task-Card
   section*, the default that a Telegram-originated turn's meaningful long-running
   bash-async/daemon work should normally get a human-visible watcher, plus a route
   to the nested manual/assets. The nested manual binds the snapshot model, both
   copyable template names, and the start|inspect|retry|stop / single-resident
   lifecycle.
2. **Snapshot truthfulness (semantic).** Running each template through the real
   ``TaskCardController._run_renderer``, a snapshot missing its identity, missing or
   carrying an unknown/wrong-typed state, malformed, non-object, or empty must
   render an explicit *awaiting* frame and MUST NOT claim ``starting``/``running``
   or any live state. A live/terminal state is shown only with a recorded identity
   plus an allow-listed state.
3. **Bounds and type safety.** An oversize state file, container-valued fields,
   giant integers, non-finite floats, booleans in numeric fields, and hostile
   long strings never crash the renderer and never fabricate progress — they
   degrade to the awaiting frame or are clipped within the Task Card bounds.
4. **Asset shape/packaging.** Both files exist beside the manual, are stdlib-only,
   and document their snapshot schema.

No Telegram or network: ``_run_renderer`` runs the renderer as a subprocess and
validates its stdout; the reverse channel is never called.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import threading
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.task_card import TaskCardController

_TASK_CARD_DIR = (
    Path(__file__).resolve().parents[1]
    / "src/lingtai/mcp_servers/telegram/task_card"
)
_ASSETS_DIR = _TASK_CARD_DIR / "assets"
_NESTED_SKILL = _TASK_CARD_DIR / "SKILL.md"
_TOP_SKILL = _TASK_CARD_DIR.parent / "SKILL.md"

# (asset filename, its documented state-snapshot filename, identity key, state key)
_BASH = ("render_bash_async.py", "task_card_state.json", "job_id", "status")
_DAEMON = ("render_daemon.py", "daemon_card_state.json", "id", "state")
_TEMPLATES = [_BASH, _DAEMON]
_IDS = [t[0] for t in _TEMPLATES]

# stdlib modules the templates are allowed to import — nothing third-party.
_STDLIB_ALLOWED = {"__future__", "json", "math", "pathlib"}

_MAX_LINES = 20  # controller's hard line cap (controller._MAX_LINES).

# The explicit, honest "no usable snapshot" marker both templates emit. Its
# presence (and the absence of any live-state label) is how we assert no fabricated
# progress without pinning exact prose.
_AWAITING_MARKER = "awaiting orchestrator update"

# Live-state labels a template prints ONLY when identity + allow-listed state are
# both present. If any of these appear on a partial/unknown snapshot, the renderer
# has fabricated state.
_LIVE_LABELS = ("status:", "state:")

# A full, valid snapshot per template (identity + allow-listed state + extras).
_FULL_STATE = {
    "render_bash_async.py": {
        "title": "Refactor auth module",
        "job_id": "job-a1b2c3d4e5f6",
        "status": "running",
        "exit_code": 0,
        "stage": "tests passing",
        "updated_at": "2026-07-13T10:30:00Z",
        "note": "3/5 modules",
    },
    "render_daemon.py": {
        "title": "Nightly synthesis",
        "id": "em-a1b2",
        "state": "running",
        "current": "grep",
        "elapsed_s": 312,
        "last_activity": "2026-07-13T10:29:00Z",
        "health": "alive",
        "updated_at": "2026-07-13T10:30:00Z",
        "note": "phase 2/3",
    },
}

# The allow-listed display states each template may render as live/terminal. For
# bash these are DISPLAY states the orchestrator derives from the sanctioned
# poll/cancel result (see _BASH_RESULT_TO_DISPLAY_STATE below), not raw bash
# statuses; `unknown` is the exit-status-unavailable terminal.
_ALLOWED_STATES = {
    "render_bash_async.py": ["starting", "running", "done", "failed", "cancelled", "unknown"],
    "render_daemon.py": ["running", "done", "failed", "cancelled", "timeout"],
}

# Terminal states: the work has finished, so the terminal snapshot's footer must
# request that the orchestrator stop/clear the watcher (the passive renderer
# cannot stop itself). Non-terminal states are the remaining allow-listed states —
# derived, not hand-maintained, so the two lists cannot drift apart. The bash
# `unknown` display state is terminal (an unavailable exit status still ends the job).
_TERMINAL_STATES = {
    "render_bash_async.py": ["done", "failed", "cancelled", "unknown"],
    "render_daemon.py": ["done", "failed", "cancelled", "timeout"],
}
_NON_TERMINAL_STATES = {
    asset: [s for s in _ALLOWED_STATES[asset] if s not in _TERMINAL_STATES[asset]]
    for asset in _ALLOWED_STATES
}

# The exact model-facing stop call the templates/manual must teach (action-based,
# like bash(action="poll") / daemon(action="check")) and the terminal footer text.
_STOP_CALL = 'task_card(action="stop", watch_id="<watch_id>")'
_TERMINAL_FOOTER_TEXT = "terminal snapshot — stop/clear this watch now"
_TERMINAL_FOOTER_MARKER = "stop/clear this watch"


# =========================================================================
# 1. Two-entry-point human contract (bound concepts, not loose substrings)
# =========================================================================


def _section(text: str, header: str) -> str:
    """Return the body of the markdown section starting at ``header`` up to the
    next same-or-higher-level header, so an assertion binds to the intended
    section rather than to the whole document."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == header), None)
    assert start is not None, f"section not found: {header!r}"
    level = len(header) - len(header.lstrip("#"))
    body: list[str] = []
    for ln in lines[start + 1:]:
        stripped = ln.lstrip("#")
        this_level = len(ln) - len(stripped)
        if ln.startswith("#") and this_level <= level:
            break
        body.append(ln)
    return "\n".join(body)


def test_top_level_manual_binds_watcher_default_in_taskcard_section():
    """An agent loading ONLY the top-level Telegram manual must find, together in
    the programmable Task Card section, the Telegram-originated + meaningful +
    long-running qualification, the human-visible watcher default, both work kinds,
    and a route onward to the templates. Binding them to one section (not scanning
    the whole file) means a regression that weakens the advice fails here."""
    section = _section(
        _TOP_SKILL.read_text(encoding="utf-8"),
        "## PROGRAMMABLE TASK CARD (`task_card` tool)",
    ).lower()
    # The default/trigger, all in the same section.
    assert "default" in section
    assert "telegram-originated turn" in section
    assert "meaningful" in section and "long-running" in section
    assert "human-visible" in section
    assert "watcher" in section
    # Both kinds of long-running work.
    assert "bash(async=true)" in section
    assert "daemon" in section
    # Route onward to the concrete templates + nested manual.
    assert "render_bash_async.py" in section and "render_daemon.py" in section
    assert "task_card/skill.md" in section
    # Truthful framing: snapshot, not an autonomous live feed.
    assert "snapshot" in section
    assert "live progress" not in section


def test_nested_manual_binds_snapshot_model_templates_and_lifecycle():
    """The nested manual must bind the snapshot model, both copyable template
    names, the truthfulness rule, and the start|inspect|retry|stop / single-resident
    lifecycle."""
    text = _NESTED_SKILL.read_text(encoding="utf-8")
    lowered = text.lower()

    # Both copyable templates named, with the snapshot files they read.
    assert "render_bash_async.py" in text and "render_daemon.py" in text
    assert "task_card_state.json" in text and "daemon_card_state.json" in text

    # The snapshot / latest-reported model (not autonomous live progress).
    assert "latest reported snapshot" in lowered
    assert "snapshot" in lowered

    # The truthfulness rule is stated: identity + allow-listed state required,
    # else an awaiting frame; never fabricated starting/running.
    assert "identity" in lowered
    assert "awaiting orchestrator update" in lowered
    assert "never" in lowered and ("fabricat" in lowered or "invent" in lowered)

    # Copy-into-workdir guidance resolved from the manual path (not a fixed
    # source/installed path), because the controller confines renderer_path.
    assert "manual" in lowered and "working directory" in lowered
    assert "confine" in lowered

    # Full lifecycle, in its lifecycle section.
    lifecycle = _section(text, "### The lifecycle: start | inspect | retry | stop").lower()
    for action in ("start", "inspect", "retry", "stop"):
        assert action in lifecycle

    # Single-resident manager, no second manager/card.
    assert "single resident" in lowered
    assert "does not start another manager" in lowered or (
        "never starts a second manager" in lowered
    ) or ("not start another manager" in lowered)


def test_nested_manual_binds_watcher_default_in_when_to_reach_section():
    """The nested manual must independently lock the watcher-default advisory in
    its own ``When to reach for this`` section — not only the top-level manual and
    not only nested asset/lifecycle wording. Binding these concepts to that section
    means the encouragement cannot be silently removed or weakened to optional
    while this test keeps passing."""
    section = _section(
        _NESTED_SKILL.read_text(encoding="utf-8"),
        "## When to reach for this",
    ).lower()
    # The Telegram-originated + meaningful + long-running qualification.
    assert "telegram-originated turn" in section
    assert "meaningful" in section and "long-running" in section
    # Both kinds of long-running work.
    assert "bash(async=true)" in section
    assert "daemon" in section
    # A human-visible watcher is the recommended response.
    assert "human-visible" in section
    assert "watcher" in section
    # It is a default, with an explicit skip qualification (not merely optional).
    assert "default" in section
    assert "skip" in section
    # Routed to / related to the snapshot + template workflow.
    assert "snapshot" in section
    assert "two ready templates" in section
    # Honest framing, not an autonomous live feed.
    assert "live progress" not in section


def test_nested_manual_binds_terminal_cleanup_in_its_own_section():
    """The nested manual must lock the terminal-cleanup workflow in its own
    section: a finished watch is stopped by the orchestrator (the renderer is
    passive) via the exact action-based stop call, every terminal state is named,
    a retryable stop_failed is retried with the same call (not restarted), and
    non-terminal states stay resident. This is the core behavioral promise of this
    change, so a regression that drops it must fail here."""
    section = _section(
        _NESTED_SKILL.read_text(encoding="utf-8"),
        "### Terminal cleanup: stop a finished watch",
    ).lower()
    # The renderer cannot end itself; the orchestrator must stop it with the exact
    # action-based tool call (no method-style pseudo-invocation).
    assert "passive" in section
    assert _STOP_CALL.lower() in section
    assert "stop(watch_id)" not in section  # no bare/method-style pseudo-call
    # Every terminal state is named (bash + daemon), plus the terminal footer cue.
    for terminal in ("done", "failed", "cancelled", "timeout"):
        assert terminal in section, terminal
    assert _TERMINAL_FOOTER_TEXT.lower() in section
    # Retryable stop_failed is retried with the SAME stop call, never restarted.
    assert "stop_failed" in section
    assert "same" in section
    assert "restart" in section or "duplicate" in section
    # Non-terminal states stay resident.
    assert "starting" in section and "running" in section
    assert "resident" in section


# =========================================================================
# 4. Asset shape / packaging
# =========================================================================


@pytest.mark.parametrize("asset,state_file,_id,_state", _TEMPLATES, ids=_IDS)
def test_template_asset_exists_and_is_stdlib_only(asset, state_file, _id, _state):
    path = _ASSETS_DIR / asset
    assert path.is_file(), path
    source = path.read_text(encoding="utf-8")

    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported_roots.add(node.module.split(".")[0])
    assert imported_roots <= _STDLIB_ALLOWED, imported_roots

    # Documents the snapshot file, the STATE_FILE hook, and a byte bound.
    assert state_file in source
    assert "STATE_FILE" in source
    assert "_MAX_BYTES" in source

    # Documents the terminal-cleanup workflow with the EXACT action-based stop
    # call (no method-style pseudo-invocation): the passive renderer cannot stop
    # itself, so the orchestrator calls task_card(action="stop", ...) on a terminal
    # state and retries the same call (never restart) on a retryable stop_failed.
    # Whitespace is normalized so the concepts bind regardless of line wrapping.
    normalized = " ".join(source.split())
    lowered = normalized.lower()
    assert _STOP_CALL in normalized
    assert "task_card.stop(" not in normalized  # no method-style pseudo-call
    assert "cannot stop" in lowered
    assert "stop_failed" in normalized
    # The retry guidance points at the SAME stop call, not a restart.
    assert "call the same" in lowered
    assert '`task_card(action="stop", ...)` again' in normalized


# =========================================================================
# Executable behavior through the real controller
# =========================================================================


class _FakeAgent:
    """Minimal host for ``TaskCardController._run_renderer`` — no reverse channel
    is exercised by rendering, so a client map is unnecessary here."""

    def __init__(self, working_dir: Path) -> None:
        self._working_dir = str(working_dir)
        self._mcp_clients_by_tool: dict = {}
        self._telegram_task_card_context = {"account": "acct", "chat_id": 42}
        self._shutdown = threading.Event()

    def _enqueue_system_notification(self, **_kwargs):
        return "notif-id"

    def add_tool(self, *_a, **_k):
        pass


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


def _render(asset: str, workdir: Path, state_file: str | None = None, state=None) -> dict:
    """Copy the real asset into ``workdir``, optionally write a snapshot, and run
    it through the real controller. Returns the validated frame dict."""
    dest = workdir / asset
    shutil.copy(_ASSETS_DIR / asset, dest)
    if state is not None:
        (workdir / state_file).write_text(
            state if isinstance(state, str) else json.dumps(state)
        )
    ctrl = TaskCardController(_FakeAgent(workdir))
    try:
        return ctrl._run_renderer(dest, 10.0)
    finally:
        ctrl.shutdown_for_agent_stop()


def _assert_valid_bounded_frame(frame: dict) -> None:
    assert isinstance(frame, dict)
    assert frame.get("title") or frame.get("footer") or frame.get("lines")
    lines = frame.get("lines", [])
    assert isinstance(lines, list)
    assert len(lines) <= _MAX_LINES
    assert all(isinstance(x, str) for x in lines)
    if frame.get("title") is not None:
        assert isinstance(frame["title"], str)
    if frame.get("footer") is not None:
        assert isinstance(frame["footer"], str)


def _body(frame: dict) -> str:
    return " ".join(frame.get("lines", []))


def _assert_awaiting(frame: dict) -> None:
    """The frame is the honest 'no usable snapshot' frame: it carries the awaiting
    marker and NO live-state label — so it cannot be read as progress."""
    _assert_valid_bounded_frame(frame)
    body = _body(frame)
    assert _AWAITING_MARKER in body, body
    for label in _LIVE_LABELS:
        assert label not in body, f"awaiting frame leaked a live label: {body!r}"


def _assert_live(frame: dict, identity: str, state: str) -> None:
    """The frame shows the recorded identity and the allow-listed state, and is
    NOT an awaiting frame."""
    _assert_valid_bounded_frame(frame)
    body = _body(frame)
    assert _AWAITING_MARKER not in body, body
    assert identity in body, body
    assert state in body, body


# -- happy paths -----------------------------------------------------------


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_full_snapshot_renders_true_identity_and_state(asset, state_file, id_key, state_key, workdir):
    state = _FULL_STATE[asset]
    frame = _render(asset, workdir, state_file, state)
    _assert_live(frame, state[id_key], state[state_key])


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_every_allowlisted_state_renders_when_identity_present(asset, state_file, id_key, state_key, workdir):
    for st in _ALLOWED_STATES[asset]:
        frame = _render(asset, workdir, state_file, {id_key: "the-id", state_key: st})
        _assert_live(frame, "the-id", st)


# -- terminal cleanup: finished work must request stopping the watcher ------


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_terminal_state_footer_requests_stop_and_clear(asset, state_file, id_key, state_key, workdir):
    """A terminal snapshot (finished work) must render a live card whose FOOTER
    asks the orchestrator to stop/clear the watcher — the passive renderer cannot
    stop itself, so this footer is the human/orchestrator cue that the completed
    card should not stay resident. The updated_at path is used to prove the
    terminal footer wins over the ordinary 'snapshot as of …' footer."""
    for st in _TERMINAL_STATES[asset]:
        frame = _render(
            asset,
            workdir,
            state_file,
            {id_key: "the-id", state_key: st, "updated_at": "2026-07-13T10:30:00Z"},
        )
        _assert_live(frame, "the-id", st)  # still a truthful live/terminal card
        footer = frame.get("footer", "")
        assert _TERMINAL_FOOTER_MARKER in footer, (st, footer)
        # The terminal footer replaces the awaiting-next semantics entirely.
        assert "awaiting next" not in footer, (st, footer)
        assert "snapshot as of" not in footer, (st, footer)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_non_terminal_state_footer_stays_resident_awaiting(asset, state_file, id_key, state_key, workdir):
    """A non-terminal snapshot (still running) must keep the awaiting-next footer
    and MUST NOT request a stop — the watch stays resident and keeps updating."""
    for st in _NON_TERMINAL_STATES[asset]:
        # With updated_at: the 'snapshot as of …; awaiting next check' footer.
        frame = _render(
            asset,
            workdir,
            state_file,
            {id_key: "the-id", state_key: st, "updated_at": "2026-07-13T10:30:00Z"},
        )
        _assert_live(frame, "the-id", st)
        footer = frame.get("footer", "")
        assert "awaiting next" in footer, (st, footer)
        assert _TERMINAL_FOOTER_MARKER not in footer, (st, footer)

        # Without updated_at: the bare 'awaiting next orchestrator update' footer.
        frame = _render(asset, workdir, state_file, {id_key: "the-id", state_key: st})
        footer = frame.get("footer", "")
        assert "awaiting next orchestrator update" in footer, (st, footer)
        assert _TERMINAL_FOOTER_MARKER not in footer, (st, footer)


# =========================================================================
# B1: the bash display state is a mapping from the REAL public Bash result
# =========================================================================
#
# The bash `status` an orchestrator records is a DISPLAY state derived from the
# sanctioned `bash(action="poll")` / `bash(action="cancel")` result — NOT the raw
# top-level bash `status`, which is ALWAYS "done" on completion (a nonzero inner
# command is signalled by the additive `exit_status_known`/`exit_code`/`ok`/
# `command_status` fields, never by a top-level "failed"). These fixtures are the
# real public result shapes documented in src/lingtai/tools/bash/CONTRACT.md and
# render them through the real controller so the public-result -> display-state
# mapping is executable and locked, not hand-authored renderer states.

# Representative REAL `bash` result shapes (see bash/CONTRACT.md §Tool surface).
_BASH_RESULT_RUNNING = {"status": "running", "job_id": "job-a1b2", "pid": 4321}
_BASH_RESULT_DONE_ZERO = {
    "status": "done", "exit_status_known": True, "exit_code": 0,
    "ok": True, "command_status": "success", "stdout": "all green\n", "stderr": "",
}
_BASH_RESULT_DONE_NONZERO = {
    "status": "done", "exit_status_known": True, "exit_code": 1,
    "ok": False, "command_status": "failed", "stdout": "", "stderr": "boom\n",
    "warning": "command exited with code 1; …",
}
_BASH_RESULT_DONE_UNKNOWN = {
    "status": "done", "job_id": "job-a1b2", "exit_status_known": False,
    "exit_code": None, "stdout": "", "stderr": "",
    "message": "Async job terminated but its exit status is unavailable",
}
_BASH_RESULT_CANCELLED = {"status": "cancelled", "job_id": "job-a1b2"}


def _bash_result_to_display_state(result: dict) -> str:
    """The exact poll/cancel-result -> recorded `status` mapping the bash manual and
    the render_bash_async.py docstring instruct the orchestrator to perform. This is
    the SAME procedure a real orchestrator follows (not a test-only shortcut): branch
    on the additive fidelity fields, never on a top-level "failed" (bash never emits
    one)."""
    top = result.get("status")
    if top == "running":
        return "running"
    if top == "cancelled":
        return "cancelled"
    assert top == "done", f"unexpected top-level bash status: {top!r}"
    if not result.get("exit_status_known"):
        return "unknown"  # exit_status_known is false -> exit status unavailable
    return "done" if result.get("exit_code") == 0 else "failed"


# (real bash result, expected display state, is-terminal)
_BASH_RESULT_CASES = [
    (_BASH_RESULT_RUNNING, "running", False),
    (_BASH_RESULT_DONE_ZERO, "done", True),
    (_BASH_RESULT_DONE_NONZERO, "failed", True),
    (_BASH_RESULT_CANCELLED, "cancelled", True),
    (_BASH_RESULT_DONE_UNKNOWN, "unknown", True),
]
_BASH_RESULT_IDS = ["running", "done-zero", "done-nonzero", "cancelled", "done-unknown"]


def _snapshot_from_bash_result(result: dict) -> dict:
    """Build the orchestrator-owned snapshot from a real bash result exactly as the
    manual documents: derive the display `status`, and copy `exit_code` ONLY when the
    exit status is known (omit it for `unknown`)."""
    status = _bash_result_to_display_state(result)
    snapshot = {"job_id": "job-a1b2", "status": status}
    if status in ("done", "failed") and result.get("exit_status_known"):
        snapshot["exit_code"] = result["exit_code"]
    return snapshot


@pytest.mark.parametrize("result,expected_state,is_terminal", _BASH_RESULT_CASES, ids=_BASH_RESULT_IDS)
def test_bash_result_maps_to_expected_display_state(result, expected_state, is_terminal, workdir):
    """A real bash poll/cancel result, mapped as documented and rendered through the
    real controller, produces the expected truthful live/terminal card — proving a
    nonzero completion renders `failed` (not `done`) and every terminal display
    state renders the stop/clear footer."""
    snapshot = _snapshot_from_bash_result(result)
    assert snapshot["status"] == expected_state  # the documented mapping held
    frame = _render("render_bash_async.py", workdir, "task_card_state.json", snapshot)
    _assert_live(frame, "job-a1b2", expected_state)
    footer = frame.get("footer", "")
    if is_terminal:
        assert _TERMINAL_FOOTER_MARKER in footer, (expected_state, footer)
    else:
        assert _TERMINAL_FOOTER_MARKER not in footer, (expected_state, footer)
        assert "awaiting next" in footer, (expected_state, footer)


def test_bash_done_nonzero_is_failed_not_done(workdir):
    """The Terra B1 core case: a terminal poll is top-level `status: "done"` even on
    a nonzero inner command, so copying the raw top-level status would fabricate
    success. The documented mapping records `failed`, and the rendered card shows
    `failed` (never a `done` glyph/label) plus the terminal stop footer."""
    snapshot = _snapshot_from_bash_result(_BASH_RESULT_DONE_NONZERO)
    frame = _render("render_bash_async.py", workdir, "task_card_state.json", snapshot)
    _assert_live(frame, "job-a1b2", "failed")
    body = _body(frame)
    assert "status: failed" in body, body
    assert "status: done" not in body, body  # the raw top-level status is NOT copied
    assert "exit: 1" in body, body
    assert _TERMINAL_FOOTER_MARKER in frame.get("footer", "")


def test_bash_exit_status_unknown_is_terminal_unknown_outcome(workdir):
    """`{"status": "done", "exit_status_known": false, "exit_code": null}` maps to the
    distinct terminal `unknown` display state. Its text must say the exit status is
    unavailable and imply NEITHER success NOR failure; it must NOT surface a success
    glyph, a failure glyph, or an exit code; and it is TERMINAL, so its footer still
    requires the exact stop closeout."""
    snapshot = _snapshot_from_bash_result(_BASH_RESULT_DONE_UNKNOWN)
    assert snapshot["status"] == "unknown"
    assert "exit_code" not in snapshot  # unknown exit status is NOT recorded as a code
    frame = _render("render_bash_async.py", workdir, "task_card_state.json", snapshot)
    _assert_live(frame, "job-a1b2", "unknown")
    body = _body(frame).lower()
    # States plainly that the exit status is unavailable / outcome unknown.
    assert "unavailable" in body and "unknown" in body, body
    # Neither success nor failure is implied: no done/success or failed wording,
    # and no exit code leaks in.
    assert "success" not in body, body
    assert "failed" not in body and "failure" not in body, body
    assert "exit:" not in body, body
    # Terminal: the stop/clear footer is required even though the outcome is unknown.
    footer = frame.get("footer", "")
    assert _TERMINAL_FOOTER_MARKER in footer, footer
    assert "awaiting next" not in footer, footer


def test_bash_exit_status_unknown_does_not_surface_stray_exit_code(workdir):
    """Even if a snapshot mistakenly carries an exit_code alongside `unknown` (which
    the mapping says to omit), the renderer must NOT surface it — an unavailable exit
    status can never be dressed up as a known outcome."""
    frame = _render(
        "render_bash_async.py",
        workdir,
        "task_card_state.json",
        {"job_id": "job-a1b2", "status": "unknown", "exit_code": 0},
    )
    _assert_live(frame, "job-a1b2", "unknown")
    body = _body(frame)
    assert "exit: 0" not in body and "exit:" not in body, body
    assert _TERMINAL_FOOTER_MARKER in frame.get("footer", "")


def test_nested_manual_documents_bash_display_state_mapping():
    """The nested manual must make the poll-result -> display-state mapping explicit:
    the derived-display-state framing, all four terminal display states (including
    the exit-status-unavailable `unknown`), and that a nonzero completion is `failed`
    rather than a copied top-level `done`."""
    text = _NESTED_SKILL.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "display state" in lowered
    assert "exit_status_known" in text
    assert "command_status" in text
    # The unknown/exit-status-unavailable terminal is documented as terminal.
    assert "unknown" in lowered
    assert "exit status is unavailable" in lowered or "exit-status-unavailable" in lowered
    # A nonzero completion is failed, not a raw copied done.
    assert "`failed`" in text and "never" in lowered


def test_bash_template_docstring_documents_display_state_mapping():
    """render_bash_async.py's own docstring must document the derived display state,
    the additive fidelity fields it maps from, and the terminal `unknown` outcome —
    so an agent copying the asset learns the mapping from the file itself."""
    source = (_ASSETS_DIR / "render_bash_async.py").read_text(encoding="utf-8")
    normalized = " ".join(source.split())
    lowered = normalized.lower()
    assert "display state" in lowered
    assert "exit_status_known" in normalized
    assert "command_status" in normalized
    assert "unknown" in lowered
    # It states the unavailable-exit-status terminal claims neither success nor failure.
    assert "unavailable" in lowered
    assert "neither success" in lowered or ("claims neither" in lowered)


# -- truthfulness: no fabricated progress ----------------------------------


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_missing_file_is_awaiting_not_progress(asset, state_file, id_key, state_key, workdir):
    _assert_awaiting(_render(asset, workdir))


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_partial_snapshot_only_title_is_awaiting_not_starting(asset, state_file, id_key, state_key, workdir):
    """The EXACT Terra case: {"title": "..."} must NOT render starting/running."""
    frame = _render(asset, workdir, state_file, {"title": "only title"})
    _assert_awaiting(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_state_without_identity_is_awaiting(asset, state_file, id_key, state_key, workdir):
    frame = _render(asset, workdir, state_file, {state_key: "running"})
    _assert_awaiting(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_identity_without_state_is_awaiting(asset, state_file, id_key, state_key, workdir):
    frame = _render(asset, workdir, state_file, {id_key: "the-id"})
    _assert_awaiting(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_unknown_state_is_awaiting_not_rendered_verbatim(asset, state_file, id_key, state_key, workdir):
    frame = _render(asset, workdir, state_file, {id_key: "the-id", state_key: "totally-made-up"})
    _assert_awaiting(frame)
    assert "totally-made-up" not in _body(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
@pytest.mark.parametrize(
    "variant", ["RUNNING", "Running", "running ", " running", " running ", "RuNnInG"],
    ids=["upper", "title", "trailing-ws", "leading-ws", "surrounding-ws", "mixed"],
)
def test_case_or_whitespace_mutated_state_is_awaiting_not_live(asset, state_file, id_key, state_key, variant, workdir):
    """The allow-list is EXACT lowercase: a case- or whitespace-mutated state
    (e.g. ``RUNNING``) is schema-invalid and must render the awaiting frame, never
    a normalized live ``running`` frame."""
    frame = _render(asset, workdir, state_file, {id_key: "the-id", state_key: variant})
    # _assert_awaiting already rejects any live status:/state: label; additionally
    # confirm the mutated token was not normalized/echoed into the card at all.
    _assert_awaiting(frame)
    assert variant.strip() not in _body(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_wrong_typed_identity_or_state_is_awaiting(asset, state_file, id_key, state_key, workdir):
    for bad in ({id_key: 12345, state_key: "running"}, {id_key: "the-id", state_key: ["running"]}):
        _assert_awaiting(_render(asset, workdir, state_file, bad))


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
@pytest.mark.parametrize(
    "bad",
    ["{not valid json", "[1, 2, 3]", '"a bare string"', "42", "", "{}"],
    ids=["malformed", "array", "string", "number", "empty", "empty-object"],
)
def test_malformed_or_nonobject_snapshot_is_awaiting(asset, state_file, id_key, state_key, bad, workdir):
    _assert_awaiting(_render(asset, workdir, state_file, bad))


# -- bounds and type safety ------------------------------------------------


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_oversize_state_file_is_awaiting_not_parsed(asset, state_file, id_key, state_key, workdir):
    """A snapshot larger than the byte cap is refused before parsing (so a huge
    file cannot be read/parsed/stringified every tick), yielding an awaiting frame
    even though it contains a valid identity + state."""
    huge = {id_key: "the-id", state_key: "running", "note": "A" * 50000}
    frame = _render(asset, workdir, state_file, huge)
    _assert_awaiting(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_container_valued_optional_fields_are_dropped_not_stringified(asset, state_file, id_key, state_key, workdir):
    """Container-valued optional fields must be ignored, never stringified, while a
    valid identity + state still render."""
    state = {
        id_key: "the-id",
        state_key: "running",
        "title": {"nested": "object"},
        "stage": ["a", "b"],
        "current": {"x": 1},
        "note": [1, 2, 3],
        "last_activity": {"t": 1},
    }
    frame = _render(asset, workdir, state_file, state)
    _assert_live(frame, "the-id", "running")
    body = _body(frame)
    # No Python container repr leaked into the card.
    for token in ("{", "}", "[", "]", "'"):
        assert token not in body, f"container leaked into card: {body!r}"


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_giant_integer_numeric_fields_are_dropped(asset, state_file, id_key, state_key, workdir):
    giant = 10 ** 40
    state = {id_key: "the-id", state_key: "running", "exit_code": giant, "elapsed_s": giant}
    frame = _render(asset, workdir, state_file, state)
    _assert_live(frame, "the-id", "running")
    assert str(giant) not in _body(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_401_digit_integer_under_byte_cap_does_not_error(asset, state_file, id_key, state_key, workdir):
    """A 401-digit integer is a valid JSON number that fits well under the 8 KiB
    byte cap, so it reaches the numeric field. It must be rejected by an INTEGER
    range check — never converted to float (``math.isfinite``/``float(int)`` raises
    ``OverflowError`` for it) — so the renderer still emits a controller-valid
    bounded frame with a valid identity + state, not an error."""
    big = int("9" * 401)
    assert len(str(big)) == 401
    raw = (
        '{"%s": "the-id", "%s": "running", "exit_code": %s, "elapsed_s": %s}'
        % (id_key, state_key, big, big)
    )
    assert len(raw) < 8192  # stays below the byte cap so it reaches the numeric field
    frame = _render(asset, workdir, state_file, raw)
    _assert_live(frame, "the-id", "running")
    assert str(big) not in _body(frame)  # the giant number is dropped, not rendered


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_deeply_nested_json_under_byte_cap_degrades_to_awaiting(asset, state_file, id_key, state_key, workdir):
    """A deeply nested (but sub-cap) JSON payload makes ``json.loads`` exceed the
    interpreter recursion limit (``RecursionError``, a ``RuntimeError`` — not a
    ``ValueError``). The renderer must degrade to a controller-valid bounded
    awaiting frame, not crash with a nonzero exit."""
    depth = 1100
    raw = "[" * depth + "]" * depth
    assert len(raw) < 8192  # fits under the byte cap, so it reaches json.loads
    frame = _render(asset, workdir, state_file, raw)
    _assert_awaiting(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"], ids=["nan", "inf", "neg-inf"])
def test_non_finite_numeric_fields_do_not_crash(asset, state_file, id_key, state_key, token, workdir):
    """json.loads accepts NaN/Infinity; the renderer must reject them for numeric
    fields without crashing (a crash → nonzero exit → controller error, no watch)."""
    raw = (
        '{"%s": "the-id", "%s": "running", "exit_code": %s, "elapsed_s": %s}'
        % (id_key, state_key, token, token)
    )
    frame = _render(asset, workdir, state_file, raw)
    _assert_live(frame, "the-id", "running")
    assert token.lstrip("-") not in _body(frame)


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_boolean_in_numeric_field_is_dropped(asset, state_file, id_key, state_key, workdir):
    state = {id_key: "the-id", state_key: "running", "exit_code": True, "elapsed_s": True}
    frame = _render(asset, workdir, state_file, state)
    _assert_live(frame, "the-id", "running")
    body = _body(frame)
    assert "exit: True" not in body and "elapsed: True" not in body


@pytest.mark.parametrize("asset,state_file,id_key,state_key", _TEMPLATES, ids=_IDS)
def test_hostile_long_strings_and_control_chars_stay_bounded(asset, state_file, id_key, state_key, workdir):
    """A long-but-in-byte-budget string field is clipped and its control
    characters stripped; the frame stays within the Task Card bounds and a valid
    identity + state still render. (Kept under ``_MAX_BYTES`` so this exercises the
    per-field clip, not the oversize-file guard, which is covered separately.)"""
    state = {
        id_key: "the-id",
        state_key: "running",
        # One long field (well over the 120-char clip, well under the byte cap)
        # plus embedded control characters.
        "title": "x" * 2000 + "\n\r\tinjected\x00",
    }
    frame = _render(asset, workdir, state_file, state)
    _assert_live(frame, "the-id", "running")
    for line in frame["lines"]:
        assert len(line) <= 200, len(line)
    assert len(frame.get("title", "")) <= 200
    assert len(frame.get("footer", "")) <= 200
    # Control characters were stripped from displayed values.
    assert not re.search(r"[\n\r\t\x00]", _body(frame) + frame.get("title", ""))
