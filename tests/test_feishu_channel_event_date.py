"""Focused non-finite/overflow guards for Feishu channel event dates (issue 992).

Current-main adaptation: ``tests/test_feishu_channel_events.py`` was retired by
upstream test pruning (2026-10-12), so the reviewed non-finite/overflow
regression coverage for ``FeishuManager._channel_event_date`` lives in this
narrow bottom-assert module instead of reviving the whole legacy suite.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from lingtai.mcp_servers.feishu.manager import FeishuManager


@pytest.mark.parametrize(
    "raw, event, expected",
    [
        ({"header": {"create_time": "1700000000"}}, SimpleNamespace(action_time=None), "2023-11-14T22:13:20Z"),
        ({"header": {"create_time": "1700000000000"}}, SimpleNamespace(action_time=None), "2023-11-14T22:13:20Z"),
        ({"header": {"create_time": "1e300"}}, SimpleNamespace(action_time=None), None),
        ({"header": {"create_time": str(sys.float_info.max)}}, SimpleNamespace(action_time=None), None),
    ],
)
def test_event_date_keeps_finite_seconds_milliseconds_and_large_values(raw, event, expected) -> None:
    result = FeishuManager._channel_event_date(raw, event)
    if expected is not None:
        assert result == expected
    else:
        assert result.endswith("Z")


_NONFINITE_CHILD_TIMEOUT_SECONDS = 5.0


def _channel_event_date_in_child(raw_repr: str) -> tuple[str, float]:
    """Run the real helper in a hard-deadline child process and return
    ``(stdout, elapsed_seconds)``.

    The non-finite regression must never call the potentially hanging helper
    in the pytest parent: if the guard regresses, ``datetime.fromtimestamp``
    blocks in the child, the deadline kills it, and ``TimeoutExpired`` fails
    this test — that is the bounded repository regression signal. The child
    itself asserts the candidate source import, so provenance is checked
    where the helper actually executes rather than by printing a path.
    """
    candidate_src = Path(__file__).resolve().parents[1] / "src"
    script = (
        "import os;\n"
        "from pathlib import Path;\n"
        "import lingtai;\n"
        "expected = Path(os.environ['FEISHU_CHILD_EXPECTED_SRC']).resolve();\n"
        "assert Path(lingtai.__file__).resolve().is_relative_to(expected), lingtai.__file__;\n"
        "from types import SimpleNamespace;\n"
        "from lingtai.mcp_servers.feishu.manager import FeishuManager;\n"
        f"raw = {raw_repr};\n"
        "print(FeishuManager._channel_event_date(raw, SimpleNamespace(action_time=None)))\n"
    )
    env = dict(os.environ)
    env["FEISHU_CHILD_EXPECTED_SRC"] = str(candidate_src)
    env["PYTHONPATH"] = str(candidate_src) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-B", "-c", script],
        capture_output=True,
        text=True,
        timeout=_NONFINITE_CHILD_TIMEOUT_SECONDS,
        env=env,
    )
    return proc.stdout.strip(), time.monotonic() - started


@pytest.mark.parametrize(
    "raw_repr",
    [
        "{'header': {'create_time': 'inf'}, 'ts': '1700000000'}",
        "{'header': {'create_time': '1e999'}, 'ts': '1700000000'}",
        "{'header': {'create_time': 'nan'}, 'ts': '1700000000'}",
        "{'header': {'create_time': '-inf'}, 'ts': '1700000000'}",
        "{'header': {'create_time': float('inf')}, 'ts': '1700000000'}",
        "{'header': {'create_time': float('-inf')}, 'ts': '1700000000'}",
        "{'header': {'create_time': float('nan')}, 'ts': '1700000000'}",
    ],
)
def test_event_date_skips_non_finite_candidates_in_bounded_child(raw_repr) -> None:
    """Guard regression probe: every non-finite candidate is skipped and the
    next finite candidate wins, inside a child bounded by a hard timeout."""
    out, elapsed = _channel_event_date_in_child(raw_repr)
    assert out == "2023-11-14T22:13:20Z"
    assert elapsed < _NONFINITE_CHILD_TIMEOUT_SECONDS


def test_event_date_preserves_candidate_priority_and_now_fallback(monkeypatch) -> None:
    event = SimpleNamespace(action_time="1700000000")
    assert FeishuManager._channel_event_date(
        {"header": {"create_time": "1700000001"}, "ts": "1700000002"}, event
    ) == "2023-11-14T22:13:20Z"
    assert FeishuManager._channel_event_date(
        {"header": {"create_time": "bad"}, "ts": "1700000002"},
        SimpleNamespace(action_time=None),
    ) == "2023-11-14T22:13:22Z"
