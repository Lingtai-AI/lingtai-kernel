#!/usr/bin/env python3
"""Task Card renderer TEMPLATE for a long-running daemon task (an emanation).

Locate this asset relative to the ABSOLUTE manual path that Telegram's
`manual` action returns (its directory has `task_card/assets/`), then COPY it
into your working directory — the controller confines `renderer_path` to the
agent working directory, so the renderer must live there. Rename it per daemon
if you run more than one. Then bind it with the `task_card` tool:

    {"action": "start", "renderer_path": "render_daemon.py", "interval_s": 10}

The controller runs this file with the runtime interpreter, capturing stdout;
it MUST print exactly one Task Card JSON object and exit 0. It receives no
command-line arguments and runs with your working directory as the process cwd,
so it locates its state by the fixed relative path `STATE_FILE` below.

WHY it reads a snapshot file, not daemon internals
--------------------------------------------------
A daemon run's `daemons/<id>/daemon.json` is a documented but VERSIONED FORENSIC
artifact, not a stable machine-readable API; the sanctioned agent-facing surface
is `daemon(action="check", id=...)`, which is read-only and safe to poll. Rather
than couple a passive renderer to internal `data_version`-gated fields, YOU — the
orchestrator — own a tiny snapshot file and keep it truthful: write it right
after `daemon(emanate)` returns your `id`, and rewrite it from each meaningful
`daemon(check)` result and at the terminal push-notification. The renderer shows
only the LATEST REPORTED SNAPSHOT; it never introspects daemon internals and
never claims activity you have not recorded.

Snapshot schema (`daemon_card_state.json`)
------------------------------------------
An active/terminal card is shown ONLY when both a nonempty string `id` AND an
allow-listed string `state` are present. Any other shape (missing file, non-JSON,
non-object, missing identity/state, an unknown state, or a wrong-typed field)
renders an explicit "awaiting orchestrator update" frame — never a fabricated
`running`.

    {
      "id":        "em-a1b2",               # REQUIRED str: the id daemon(emanate) returned
      "state":     "running",               # REQUIRED str: running|done|failed|cancelled|timeout
      "title":     "Nightly synthesis",     # optional str headline
      "current":   "grep",                  # optional str: what it is doing now (e.g. current_tool)
      "elapsed_s": 312,                     # optional finite number: wall-clock seconds so far
      "last_activity": "2026-07-13T10:29:00Z",  # optional str: last_output_at / last event time
      "health":    "alive",                 # optional str verdict: alive|stalled|unknown
      "updated_at": "2026-07-13T10:30:00Z", # optional str (ISO-8601 UTC of your last write)
      "note":      "phase 2/3"              # optional str extra one-liner
    }

Only the primitive types above are accepted; containers, booleans in numeric
fields, non-finite (NaN/Infinity) or oversized numbers, and wrong types are
ignored, not stringified. Update it from your turn. `daemon(check, id="em-a1b2")`
returns state, elapsed_s, current_tool, last_output_at, result_preview, ...; copy
the non-secret ones you want to surface. A daemon is 'stalled' only if it is still
`running` but its activity has not advanced across checks minutes apart — decide
that verdict yourself and record it as `health`. Write the snapshot COMPLETELY
each time — build the full JSON in memory and write it in one call (an atomic
temp-file-plus-`os.replace` in the same directory avoids a reader seeing a
half-written file) — so the renderer never parses a partial object.

Safety: stdlib-only; reads at most a few KiB of the snapshot; accepts only the
allow-listed primitive fields; strips control characters; clips every string;
caps the line count; and prints a valid bounded card on any missing/partial/
malformed input. Keep secrets and raw output OUT of the snapshot — the card is a
progress view, not a data channel. The manager also redacts at the render
boundary, but that cannot save a snapshot you fill with secrets in violation of
this schema.
"""
from __future__ import annotations

import json
import math
import pathlib

# Relative to your working directory (the renderer's cwd). Rename per daemon if
# you run several so each watcher reads its own snapshot.
STATE_FILE = "daemon_card_state.json"

_MAX_BYTES = 8192  # read at most this many bytes; a bigger snapshot is "unavailable".
_MAX_LINES = 20  # controller rejects more than 20 lines; stay well under it.
_MAX_STR = 120  # clip any single rendered value to this many characters.
_MAX_ELAPSED = 10**9  # ignore an elapsed_s outside this sane magnitude (~31 years).

# The only accepted emanation states, mapped to a small status glyph.
_STATE_GLYPH = {
    "running": "▶",
    "done": "✓",
    "failed": "✗",
    "cancelled": "⊘",
    "timeout": "⏳",
}

# The only accepted health verdicts YOU record after comparing checks.
_HEALTH_GLYPH = {
    "alive": "♥",
    "stalled": "!",
    "unknown": "?",
}

_UNAVAILABLE_TITLE = "Daemon task"
_AWAITING = "no snapshot yet — awaiting orchestrator update"
_INCOMPLETE = "snapshot incomplete — awaiting orchestrator update"


def _clean_str(value: object, limit: int = _MAX_STR) -> str | None:
    """Return a bounded, single-line string ONLY for real ``str`` input.

    Non-strings (containers, numbers, ``None``) return ``None`` — they are never
    stringified. Control characters are stripped so a field cannot break the card.
    """
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value if ch == " " or ch.isprintable()).strip()
    if not text:
        return None
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _elapsed(value: object) -> str | None:
    """Render elapsed seconds as a short m/s label for a FINITE, sanely bounded,
    non-boolean number only. NaN/Infinity, giant, negative, and wrong types are
    rejected (return ``None``).

    An ``int`` is range-checked by INTEGER comparison first — never converted to
    float — so an arbitrarily large integer (e.g. 401 digits, still under the byte
    cap) is rejected without raising ``OverflowError``. ``math.isfinite`` is used
    only for the ``float`` branch, where NaN/Infinity are the concern."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if not 0 <= value <= _MAX_ELAPSED:
            return None
        total = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not 0 <= value <= _MAX_ELAPSED:
            return None
        total = int(value)
    else:
        return None
    minutes, seconds = divmod(total, 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def _load_state() -> dict:
    """Read at most ``_MAX_BYTES`` and parse one JSON object. Any problem (missing,
    oversize, non-JSON, non-object) yields an empty dict so the renderer degrades
    to an explicit awaiting frame instead of crashing or fabricating state."""
    try:
        with pathlib.Path(STATE_FILE).open("rb") as fh:
            raw = fh.read(_MAX_BYTES + 1)
    except OSError:
        return {}
    if len(raw) > _MAX_BYTES:  # oversize: refuse rather than parse an unbounded blob.
        return {}
    # ValueError covers malformed JSON; RecursionError covers a deeply nested (but
    # sub-cap) payload whose parse exceeds the interpreter's recursion limit. Both
    # degrade to the awaiting frame. We deliberately do NOT catch BaseException, so
    # unrelated programmer errors still surface.
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _unavailable(message: str) -> dict:
    """A stable, honest frame that never claims activity."""
    return {"title": _UNAVAILABLE_TITLE, "lines": [message], "footer": "awaiting orchestrator update"}


def _build_card(state: dict) -> dict:
    if not state:
        return _unavailable(_AWAITING)

    # Truthfulness gate: only a recorded identity AND an EXACT allow-listed state
    # may show an active/terminal card. The state is matched verbatim (no case or
    # whitespace normalization), so `RUNNING` or ` running ` is rejected — the
    # documented schema is exact lowercase. Anything else is explicitly incomplete.
    ident = _clean_str(state.get("id"), 64)
    run_state = state.get("state")
    if not ident or not isinstance(run_state, str) or run_state not in _STATE_GLYPH:
        return _unavailable(_INCOMPLETE)

    lines = [f"{_STATE_GLYPH[run_state]} state: {run_state}", f"id: {ident}"]

    health = state.get("health")
    if isinstance(health, str) and health in _HEALTH_GLYPH:
        lines.append(f"{_HEALTH_GLYPH[health]} health: {health}")

    current = _clean_str(state.get("current"), 64)
    if current:
        lines.append(f"doing: {current}")

    elapsed = _elapsed(state.get("elapsed_s"))
    if elapsed is not None:
        lines.append(f"elapsed: {elapsed}")

    last_activity = _clean_str(state.get("last_activity"), 40)
    if last_activity:
        lines.append(f"last activity: {last_activity}")

    note = _clean_str(state.get("note"))
    if note:
        lines.append(note)

    updated_at = _clean_str(state.get("updated_at"), 40)
    footer = (
        f"snapshot as of {updated_at}; awaiting next check"
        if updated_at
        else "awaiting next orchestrator update"
    )

    title = _clean_str(state.get("title")) or _UNAVAILABLE_TITLE
    return {"title": title, "lines": lines[:_MAX_LINES], "footer": footer}


def main() -> None:
    print(json.dumps(_build_card(_load_state())))


if __name__ == "__main__":
    main()
