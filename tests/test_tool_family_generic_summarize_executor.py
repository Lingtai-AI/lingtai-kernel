"""ToolExecutor evidence that raw-first summarize retention is generic, not
Web-specific: a fake ``widget`` ToolFamily's dispatch result plugs into the
existing (untouched) executor/summary mechanism with zero family-specific
kernel wiring.

A ``ToolFamily``-composed root is closed to exactly
``action``/``input``/``reasoning``/``summarize`` — the legacy ``summary`` key
is not admitted at the family's own dispatch boundary, matching ``web``
(neither recognizes or ever recognized ``summary``; sending it to either
raises the same ``INVALID_ARGUMENT`` today as it did before migration).
``ToolExecutor`` itself does not strip ``summary``/``summarize`` in
``_prepare_args`` — only ``reasoning``. So the realistic integration for an
*unmigrated-style* caller of a ToolFamily-based tool is a thin composition
wrapper (as ``dispatch_fn`` here) that pops the legacy flag before calling
into the family, exactly mirroring how a real, still-unmigrated LingTai tool
would keep tolerating ``summary=true`` today. This proves the raw-logged-
before-summary mechanism itself needs no family-specific kernel wiring — a
distinct invariant from ``test_web_ltp_v2_summarize_executor.py``, which
proves the canonical ``summarize`` spelling specifically for the migrated
``web`` family via the kernel's per-family allowlist.
"""
from __future__ import annotations

from lingtai.kernel.llm.base import ToolCall
from lingtai.kernel.loop_guard import LoopGuard
from lingtai.kernel.tool_executor import ToolExecutor
from lingtai.kernel.tool_result_summary import APRIORI_SUMMARY_MARKER
from lingtai.tools.tool_family import ChildTool, ToolFamily


def _widget_family() -> ToolFamily:
    def spin_handler(input_):
        return {"status": "ok", "action": "spin", "marker": "WIDGETRAW-marker"}

    return ToolFamily(
        "widget",
        [
            ChildTool(
                "spin",
                {"type": "object", "properties": {}, "additionalProperties": False},
                spin_handler,
            )
        ],
    )


def _make_executor(*, dispatch_fn, summarizer_fn, events, tmp_path):
    def logger_fn(event_type, **fields):
        events.append((event_type, fields))

    def make_tool_result_fn(name, result, **kw):
        return {"role": "tool", "name": name, "content": result, **kw}

    return ToolExecutor(
        dispatch_fn=dispatch_fn,
        make_tool_result_fn=make_tool_result_fn,
        guard=LoopGuard(),
        known_tools={"widget"},
        parallel_safe_tools=set(),
        logger_fn=logger_fn,
        working_dir=tmp_path,
        summarizer_fn=summarizer_fn,
    )


def _raw_logged(events, *, needle):
    for event_type, fields in events:
        if event_type == "tool_result" and needle in str(fields.get("result")):
            return True
    return False


def test_generic_family_legacy_summary_flag_raw_logged_before_summary(tmp_path):
    fam = _widget_family()
    events = []

    def dispatch(tc):
        # A composition wrapper tolerating the legacy ``summary`` flag before
        # calling into the family's own closed-root dispatcher — see module
        # docstring for why this indirection is realistic, not a workaround.
        args = dict(tc.args)
        args.pop("summary", None)
        return fam.handle(args)

    ex = _make_executor(
        dispatch_fn=dispatch,
        summarizer_fn=lambda sp, up, tn, cid: "SUMMARY: spun",
        events=events,
        tmp_path=tmp_path,
    )
    results, _, _ = ex.execute([
        ToolCall(name="widget", args={"action": "spin", "input": {}, "reasoning": "r", "summary": True}, id="w1"),
    ])
    content = results[0]["content"]
    assert content["artifact"] == APRIORI_SUMMARY_MARKER
    assert content["generated_summary"] == "SUMMARY: spun"
    assert "WIDGETRAW-marker" not in str(content)
    assert _raw_logged(events, needle="WIDGETRAW-marker")


def test_generic_family_without_summary_flag_passes_raw_through_unmodified(tmp_path):
    fam = _widget_family()
    events = []
    ex = _make_executor(
        dispatch_fn=lambda tc: fam.handle(tc.args),
        summarizer_fn=lambda sp, up, tn, cid: (_ for _ in ()).throw(AssertionError("must not summarize without opt-in")),
        events=events,
        tmp_path=tmp_path,
    )
    results, _, _ = ex.execute([
        ToolCall(name="widget", args={"action": "spin", "input": {}, "reasoning": "r"}, id="w2"),
    ])
    content = results[0]["content"]
    assert content == {"status": "ok", "action": "spin", "marker": "WIDGETRAW-marker"}
