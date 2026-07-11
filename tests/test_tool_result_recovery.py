from __future__ import annotations

import json
from pathlib import Path

from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock
from lingtai.kernel.tool_result_artifacts import ARTIFACT_MARKER, PREVENTIVE_MAX_CHARS
from lingtai.kernel.tool_result_recovery import recover_tool_result_block_from_events


def _write_events(workdir: Path, events: list[dict]) -> None:
    logs = workdir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def test_recovers_exact_tool_call_id_and_tool_name(tmp_path: Path) -> None:
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-1",
                "tool_name": "bash",
                "result": {"stdout": "ok"},
                "ts": 1,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-1",
        tool_name="bash",
    )

    assert block is not None
    assert block.id == "tc-1"
    assert block.name == "bash"
    assert block.content == {"stdout": "ok"}
    assert block.synthesized is False


def test_newest_matching_tool_result_wins(tmp_path: Path) -> None:
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-1",
                "tool_name": "bash",
                "result": "older",
                "ts": 1,
            },
            {
                "type": "tool_result",
                "tool_call_id": "tc-1",
                "tool_name": "bash",
                "result": "newer",
                "ts": 2,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-1",
        tool_name="bash",
    )

    assert block is not None
    assert block.content == "newer"


def test_scan_event_bound_limits_recovery(tmp_path: Path) -> None:
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-1",
                "tool_name": "bash",
                "result": "older match outside bound",
                "ts": 1,
            },
            {
                "type": "other",
                "message": "newest event consumes the one-event scan budget",
                "ts": 2,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-1",
        tool_name="bash",
        max_scan_events=1,
    )

    assert block is None


def test_tool_name_mismatch_is_not_replayed(tmp_path: Path) -> None:
    logs: list[tuple[str, dict]] = []
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-1",
                "tool_name": "read",
                "result": "wrong tool",
                "ts": 1,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-1",
        tool_name="bash",
        logger_fn=lambda event, **fields: logs.append((event, fields)),
    )

    assert block is None
    assert [event for event, _ in logs] == ["tool_result_replay_miss"]


def test_scan_event_bound_can_force_safe_miss(tmp_path: Path) -> None:
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-1",
                "tool_name": "bash",
                "result": "older match",
                "ts": 1,
            },
            {
                "type": "tool_result",
                "tool_call_id": "other",
                "tool_name": "bash",
                "result": "newer non-match",
                "ts": 2,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-1",
        tool_name="bash",
        max_scan_events=1,
    )

    assert block is None


def test_oversized_recovered_result_is_spilled_before_replay(tmp_path: Path) -> None:
    big = "X" * (PREVENTIVE_MAX_CHARS + 1)
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-big",
                "tool_name": "bash",
                "result": big,
                "ts": 1,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-big",
        tool_name="bash",
    )

    assert block is not None
    assert isinstance(block.content, dict)
    assert block.content["artifact"] == ARTIFACT_MARKER
    assert block.content["source"] == "recovered_from_events"
    assert block.content["tool_call_id"] == "tc-big"
    assert block.content["original_char_count"] == len(big)
    spill_path = tmp_path / block.content["spill_path"]
    assert spill_path.read_text(encoding="utf-8") == big


def test_replay_logs_safe_metadata_without_raw_result(tmp_path: Path) -> None:
    secret = "RAW-RESULT-SECRET"
    events: list[tuple[str, dict]] = []
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-secret",
                "tool_name": "bash",
                "tool_args": {"command": f"echo {secret}"},
                "result": {"stdout": secret},
                "ts": 1,
            },
        ],
    )

    block = recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id="tc-secret",
        tool_name="bash",
        logger_fn=lambda event, **fields: events.append((event, fields)),
    )

    assert block is not None
    assert block.content == {"stdout": secret}
    logged = json.dumps(events, default=str)
    assert "tool_result_replayed_from_log" in logged
    assert secret not in logged
    assert "tool_args" not in logged


def test_close_pending_multicall_replays_hit_and_synthesizes_miss_in_order(
    tmp_path: Path,
) -> None:
    logs: list[tuple[str, dict]] = []
    _write_events(
        tmp_path,
        [
            {
                "type": "tool_result",
                "tool_call_id": "tc-hit",
                "tool_name": "bash",
                "result": {"stdout": "recovered"},
                "ts": 1,
            },
        ],
    )
    iface = ChatInterface()
    iface.tool_result_recovery_lookup = lambda call: recover_tool_result_block_from_events(
        tmp_path,
        tool_call_id=call.id,
        tool_name=call.name,
        logger_fn=lambda event, **fields: logs.append((event, fields)),
    )
    iface.add_assistant_message(
        [
            ToolCallBlock(id="tc-hit", name="bash", args={}),
            ToolCallBlock(id="tc-miss", name="read", args={}),
        ]
    )

    iface.close_pending_tool_calls("multi-call heal")

    assert iface.has_pending_tool_calls() is False
    assert len(iface.entries) == 2
    tail = iface.entries[-1]
    assert tail.role == "user"
    assert len(tail.content) == 2
    first, second = tail.content
    assert isinstance(first, ToolResultBlock)
    assert first.id == "tc-hit"
    assert first.name == "bash"
    assert first.content == {"stdout": "recovered"}
    assert first.synthesized is False
    assert isinstance(second, ToolResultBlock)
    assert second.id == "tc-miss"
    assert second.name == "read"
    assert second.synthesized is True
    assert "multi-call heal" in second.content
    assert [event for event, _ in logs] == [
        "tool_result_replayed_from_log",
        "tool_result_replay_miss",
    ]
