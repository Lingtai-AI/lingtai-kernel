#!/usr/bin/env python3
"""Task Card renderer TEMPLATE for a long-running `shell(async=true)` job.

Locate this asset relative to the ABSOLUTE manual path that Telegram's
`manual` action returns (its directory has `task_card/assets/`), then COPY it
into your working directory — the controller confines `renderer_path` to the
agent working directory, so the renderer must live there. Rename it per job if
you run more than one. Then bind it with the `task_card` tool:

    {"action": "start", "renderer_path": "render_bash_async.py", "interval_s": 5}

The controller runs this file with the runtime interpreter, capturing stdout;
it MUST print exactly one Task Card JSON object and exit 0. It receives no
command-line arguments and runs with your working directory as the process cwd,
so it locates its state by the fixed relative path `STATE_FILE` below.

WHY it reads a snapshot file, not shell internals
-------------------------------------------------
The shell async job's own on-disk state under `system/jobs/<job_id>/` is PRIVATE
to the shell capability, and `shell(action="poll")` is a one-shot, consuming read
(the first terminal poll marks the job consumed). A passive renderer must not
touch either. Instead, YOU — the orchestrator — own a tiny snapshot file and
keep it truthful: write it right after `shell(async=true)` returns your `job_id`,
and rewrite it after each meaningful poll and at the terminal result. The
renderer shows only the LATEST REPORTED SNAPSHOT; it never invents job state and
never claims progress you have not recorded.

Snapshot schema (`task_card_state.json`)
----------------------------------------
An active/terminal card is shown ONLY when both a nonempty string `job_id` AND an
allow-listed string `status` are present. Any other shape (missing file, non-JSON,
non-object, missing identity/status, an unknown status, or a wrong-typed field)
renders an explicit "awaiting orchestrator update" frame — never a fabricated
`starting`/`running`.

    {
      "job_id":   "job-a1b2...",             # REQUIRED str: the id shell(async=true) returned
      "status":   "running",                 # REQUIRED str: starting|running|done|failed|cancelled|unknown
      "title":    "Refactor auth module",    # optional str headline
      "exit_code": 0,                        # optional int, once a terminal poll reports a known exit code
      "stage":    "tests passing",           # optional str progress note
      "updated_at": "2026-07-13T10:30:00Z",  # optional str (ISO-8601 UTC of your last write)
      "note":     "3/5 modules done"         # optional str extra one-liner
    }

`status` is a DISPLAY STATE you derive from the sanctioned action result — it is
NOT the raw top-level `status` of the poll. Shell's terminal poll is ALWAYS
top-level `status: "done"`: a nonzero inner command does not change it to a
top-level `"failed"`. Read the additive fidelity fields and map them:

    shell(action="poll") result                              -> record status=
    ----------------------------------------------------------  ---------------
    {"status": "running", ...}                                  "running"
    {"status": "done", "exit_status_known": true,               "done"
        "exit_code": 0, "ok": true, "command_status": "success"}
    {"status": "done", "exit_status_known": true,               "failed"
        "exit_code": <nonzero>, "ok": false,
        "command_status": "failed"}
    {"status": "done", "exit_status_known": false,              "unknown"
        "exit_code": null, ...}
    shell(action="cancel") -> {"status": "cancelled", ...}       "cancelled"

So a nonzero completion is recorded `failed` (never `done`), and an
exit-status-unknown terminal completion is recorded `unknown` — a distinct
TERMINAL state that reports the exit status is unavailable and claims NEITHER
success NOR failure (Shell itself never invents `-1` or a false
`command_status: "failed"` for it, so neither may this card). Only copy the exit
code into `exit_code` when `exit_status_known` is true; for `unknown` leave it
out. Update the snapshot from your turn on each meaningful poll and at the
terminal result. Write it COMPLETELY each time — build the full JSON in memory
and write it in one call (an atomic temp-file-plus-`os.replace` in the same
directory avoids a reader seeing a half-written file) — so the renderer never
parses a partial object.

Only the primitive types above are accepted; containers, booleans in numeric
fields, non-finite/oversized numbers, and wrong types are ignored, not
stringified.

Terminal cleanup — you MUST stop the watcher yourself
-----------------------------------------------------
This renderer is PASSIVE: it only prints title/lines/footer and has no
`watch_id` and no tool access, so it CANNOT stop the watch or clear the card by
itself. When the job reaches a terminal status (`done`, `failed`, `cancelled`,
or `unknown` — the exit-status-unavailable terminal) — which you learn from the
terminal `shell(action="poll")` or `shell(action="cancel")` result — record that
terminal snapshot, then IMMEDIATELY call
`task_card(action="stop", watch_id="<watch_id>")` (the `watch_id` that
`task_card(action="start", ...)` returned) to quiesce the watcher and clear the
programmable slot so the finished card does not stay resident. This is required
for `unknown` too: an unavailable exit status is still terminal and must be
stopped/cleared, not left resident. If it returns a retryable `stop_failed`,
call the SAME `task_card(action="stop", ...)` again — do not restart or
duplicate the watch. A terminal snapshot's footer says
`terminal snapshot — stop/clear this watch now` as your reminder. Non-terminal
statuses (`starting`, `running`) stay resident and keep updating.

Safety: stdlib-only; reads at most a few KiB of the snapshot; accepts only the
allow-listed primitive fields; strips control characters; clips every string;
caps the line count; and prints a valid bounded card on any missing/partial/
malformed input. Keep secrets and raw log bodies OUT of the snapshot — the card
is a progress view, not a data channel. The manager also redacts at the render
boundary, but that cannot save a snapshot you fill with secrets in violation of
this schema.
"""
from __future__ import annotations

import json
import pathlib

# Relative to your working directory (the renderer's cwd). Rename per job if you
# run several async jobs so each watcher reads its own snapshot.
STATE_FILE = "task_card_state.json"

_MAX_BYTES = 8192  # read at most this many bytes; a bigger snapshot is "unavailable".
_MAX_LINES = 20  # controller rejects more than 20 lines; stay well under it.
_MAX_STR = 120  # clip any single rendered value to this many characters.
_MAX_INT = 10**9  # ignore an exit_code outside this sane magnitude.

# The only accepted display states, mapped to a small status glyph. These are
# DISPLAY states the orchestrator derives from the sanctioned poll/cancel result
# (see the module docstring), not the raw top-level shell ``status``. ``unknown``
# is the exit-status-unavailable terminal: it claims neither success nor failure.
_STATUS_GLYPH = {
    "starting": "…",
    "running": "▶",
    "done": "✓",
    "failed": "✗",
    "cancelled": "⊘",
    "unknown": "?",
}

# Terminal display states: the job has finished (successfully, unsuccessfully, or
# with an unavailable exit status) or was cancelled. When the snapshot records one
# of these, the work is over and the watcher should be quiesced. The renderer is
# PASSIVE — it cannot stop itself — so it asks the orchestrator, in the footer, to
# call ``task_card(action="stop", watch_id="<watch_id>")``. ``unknown`` is
# terminal too: an unavailable exit status still ends the job. ``starting`` and
# ``running`` are non-terminal.
_TERMINAL_STATUSES = {"done", "failed", "cancelled", "unknown"}
_TERMINAL_FOOTER = "terminal snapshot — stop/clear this watch now"

# For the exit-status-unavailable terminal, the body line must state that the exit
# status is unavailable and imply NEITHER success NOR failure.
_UNKNOWN_OUTCOME_LINE = "exit status unavailable — outcome unknown"

_UNAVAILABLE_TITLE = "Async job"
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


def _clean_int(value: object) -> int | None:
    """Accept only a real, sanely bounded, non-boolean ``int``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if -_MAX_INT <= value <= _MAX_INT:
        return value
    return None


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
    """A stable, honest frame that never claims progress."""
    return {"title": _UNAVAILABLE_TITLE, "lines": [message], "footer": "awaiting orchestrator update"}


def _build_card(state: dict) -> dict:
    if not state:
        return _unavailable(_AWAITING)

    # Truthfulness gate: only a recorded identity AND an EXACT allow-listed status
    # may show an active/terminal card. The status is matched verbatim (no case or
    # whitespace normalization), so `RUNNING` or ` running ` is rejected — the
    # documented schema is exact lowercase. Anything else is explicitly incomplete.
    job_id = _clean_str(state.get("job_id"), 64)
    status = state.get("status")
    if not job_id or not isinstance(status, str) or status not in _STATUS_GLYPH:
        return _unavailable(_INCOMPLETE)

    lines = [f"{_STATUS_GLYPH[status]} status: {status}", f"job: {job_id}"]

    if status == "unknown":
        # Exit-status-unavailable terminal: state it plainly and do NOT surface an
        # exit code even if the snapshot carries one, so the card cannot imply a
        # known success/failure the poll never established.
        lines.append(_UNKNOWN_OUTCOME_LINE)
    else:
        exit_code = _clean_int(state.get("exit_code"))
        if exit_code is not None:
            lines.append(f"exit: {exit_code}")

    stage = _clean_str(state.get("stage"))
    if stage:
        lines.append(f"stage: {stage}")

    note = _clean_str(state.get("note"))
    if note:
        lines.append(note)

    if status in _TERMINAL_STATUSES:
        # The job has finished: ask the orchestrator to stop the watcher now so the
        # completed card does not stay resident. A non-terminal status keeps the
        # awaiting-next-check footer.
        footer = _TERMINAL_FOOTER
    else:
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
