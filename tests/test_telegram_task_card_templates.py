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

# The allow-listed states each template may render as live/terminal.
_ALLOWED_STATES = {
    "render_bash_async.py": ["starting", "running", "done", "failed", "cancelled"],
    "render_daemon.py": ["running", "done", "failed", "cancelled", "timeout"],
}


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
