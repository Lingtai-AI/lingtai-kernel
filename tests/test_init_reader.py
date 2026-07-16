from __future__ import annotations

import json
from pathlib import Path

from lingtai.init_reader import InitReadStatus, InitShapeDecision, read_init
from lingtai.kernel.config_resolve import load_jsonc


_REQUIRED = {
    "manifest": {"llm": {"provider": "openai", "model": "gpt-4o"}},
    "covenant": "operator contract",
    "pad": "durable state",
}


def _write_init(path: Path, text: str) -> None:
    (path / "init.json").write_text(text, encoding="utf-8")


def test_kernel_canonical_init_jsonc_is_parseable_and_has_current_shape():
    root = Path(__file__).parents[1]
    data = load_jsonc(root / "src/lingtai/init.jsonc")
    assert data["manifest"]["llm"]["provider"] == "minimax"
    assert data["manifest"]["capabilities"]["shell"]["policy_file"] == "bash_policy.json"
    assert data["covenant"]
    assert data["pad"] == ""


def test_real_reader_reports_ignored_legacy_paths_without_mutating_input(tmp_path):
    raw = json.dumps({**_REQUIRED, "substrate": "old resident text", "manifest": {
        **_REQUIRED["manifest"], "stamina": 1,
    }}, indent=2)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.READ_OK_WITH_IGNORED_FIELDS
    assert "substrate" in outcome.ignored_paths
    assert "manifest.stamina" in outcome.ignored_paths
    assert outcome.shape_decision is InitShapeDecision.PASS
    assert outcome.finding_decision is InitShapeDecision.NUDGE
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw
    assert outcome.data is not None
    assert outcome.data["substrate"] == "old resident text"


def test_real_reader_reports_json_location_and_redacted_excerpt(tmp_path):
    raw = '{"manifest":{"llm":{"provider":"openai","api_key":"super-secret"}'
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.READ_FAILED
    assert outcome.stage == "JSON_PARSE"
    assert outcome.line == 1
    assert outcome.column is not None
    assert outcome.safe_excerpt is not None
    assert "super-secret" not in outcome.safe_excerpt
    assert outcome.behavior == "STOP"
    assert outcome.shape_decision is InitShapeDecision.UNKNOWN
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw


def test_real_reader_uses_in_memory_materialization_and_prepare_callbacks(tmp_path):
    _write_init(tmp_path, json.dumps(_REQUIRED))
    calls: list[str] = []

    def materialize(data: dict) -> None:
        calls.append("materialize")
        data["manifest"]["llm"]["model"] = "materialized"

    def prepare(data: dict) -> None:
        calls.append("prepare")
        data["comment"] = "prepared"

    outcome = read_init(tmp_path, materialize=materialize, prepare=prepare)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert calls == ["materialize", "prepare"]
    assert outcome.data["manifest"]["llm"]["model"] == "materialized"
    assert outcome.data["comment"] == "prepared"


def _capability_init(capabilities: dict) -> str:
    data = {**_REQUIRED, "manifest": {**_REQUIRED["manifest"], "capabilities": capabilities}}
    return json.dumps(data, indent=2)


def test_bash_only_materializes_effective_shell_and_nudges_without_writeback(tmp_path):
    raw = _capability_init({"bash": {"yolo": True}})
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert outcome.shape_decision is InitShapeDecision.NUDGE
    assert outcome.compatibility_paths == [{
        "raw_path": "manifest.capabilities.bash",
        "effective_path": "manifest.capabilities.shell",
    }]
    assert outcome.data["manifest"]["capabilities"] == {"shell": {"yolo": True}}
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw


def test_identical_dual_capability_shape_keeps_one_shell_and_nudges(tmp_path):
    raw = _capability_init({"bash": {"yolo": True}, "shell": {"yolo": True}})
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert outcome.shape_decision is InitShapeDecision.NUDGE
    assert list(outcome.data["manifest"]["capabilities"]) == ["shell"]
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw


def test_conflicting_dual_capability_shape_blocks_and_preserves_raw(tmp_path):
    raw = _capability_init({"bash": {"yolo": False}, "shell": {"yolo": True}})
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path, failure_behavior="KEEP_PREVIOUS_EFFECTIVE")

    assert outcome.status is InitReadStatus.READ_FAILED
    assert outcome.stage == "CONFLICT"
    assert outcome.shape_decision is InitShapeDecision.BLOCKED
    assert outcome.conflict_paths == [
        "manifest.capabilities.bash",
        "manifest.capabilities.shell",
    ]
    assert outcome.behavior == "KEEP_PREVIOUS_EFFECTIVE"
    assert outcome.finding_decision is InitShapeDecision.BLOCKED
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw


def test_explicit_repair_changes_same_reader_to_pass(tmp_path):
    _write_init(tmp_path, _capability_init({"bash": {"yolo": True}}))
    assert read_init(tmp_path).shape_decision is InitShapeDecision.NUDGE

    repaired = _capability_init({"shell": {"yolo": True}})
    _write_init(tmp_path, repaired)
    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert outcome.shape_decision is InitShapeDecision.PASS
    assert outcome.finding_decision is InitShapeDecision.PASS


def test_outcome_payload_contains_redacted_effective_data_and_behavior(tmp_path):
    data = {**_REQUIRED, "manifest": {
        **_REQUIRED["manifest"],
        "api_key": "top-secret",
        "capabilities": {"shell": {"yolo": True}},
    }}
    _write_init(tmp_path, json.dumps(data))
    outcome = read_init(tmp_path, failure_behavior="KEEP_PREVIOUS_EFFECTIVE")
    payload = outcome.to_payload()

    assert payload["effective_config"]["redacted"] is True
    assert "top-secret" not in json.dumps(payload)
    assert payload["shape_decision"] == "PASS"
    assert payload["behavior"] == "CONTINUE"
