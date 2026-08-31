from __future__ import annotations

import json
from pathlib import Path

from lingtai.init_reader import InitReadStatus, InitShapeDecision, read_init
from lingtai.kernel.config_resolve import load_jsonc


_REQUIRED = {
    "manifest": {"llm": {"provider": "openai", "model": "gpt-4o"}},
    "pad": "durable state",
}


def _write_init(path: Path, text: str) -> None:
    (path / "init.json").write_text(text, encoding="utf-8")


def test_kernel_canonical_init_jsonc_is_parseable_and_has_current_shape():
    root = Path(__file__).parents[1]
    data = load_jsonc(root / "src/lingtai/init.jsonc")
    assert data["manifest"]["llm"]["provider"] == "minimax"
    assert data["manifest"]["capabilities"]["shell"]["policy_file"] == "bash_policy.json"
    assert "covenant" not in data
    assert data["pad"] == ""


def test_real_reader_reports_ignored_legacy_paths_without_mutating_input(tmp_path):
    legacy_prompt_inputs = {
        "base_prompt": {"legacy": "inert"},
        "base_prompt_file": 1,
        "covenant": ["legacy", "inert"],
        "covenant_file": False,
        "comment": {"legacy": "inert"},
        "comment_file": None,
    }
    raw = json.dumps({
        **_REQUIRED,
        **legacy_prompt_inputs,
        "substrate": "old resident text",
        "manifest": {
            **_REQUIRED["manifest"], "stamina": 1, "cache_miss_budget": 123,
        },
    }, indent=2)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.READ_OK_WITH_IGNORED_FIELDS
    assert set(legacy_prompt_inputs) <= set(outcome.ignored_paths)
    assert "substrate" in outcome.ignored_paths
    assert "manifest.stamina" in outcome.ignored_paths
    assert "manifest.cache_miss_budget" in outcome.ignored_paths
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
        data["lingtai"] = "prepared"

    outcome = read_init(tmp_path, materialize=materialize, prepare=prepare)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert calls == ["materialize", "prepare"]
    assert outcome.data["manifest"]["llm"]["model"] == "materialized"
    assert outcome.data["lingtai"] == "prepared"


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


def _context_limit_init(top: object, nested_present: bool, nested: object) -> str:
    manifest = {
        **_REQUIRED["manifest"],
        "llm": {**_REQUIRED["manifest"]["llm"]},
    }
    if top is not None:
        manifest["context_limit"] = top
    if nested_present:
        manifest["llm"]["context_limit"] = nested
    return json.dumps({**_REQUIRED, "manifest": manifest}, indent=2)


def test_legacy_root_context_limit_is_ignored_and_nested_is_diagnosed(tmp_path):
    """Old root input is compatibility-known; nested input remains unknown."""
    raw = _context_limit_init(top=350000, nested_present=True, nested=500000)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.READ_OK_WITH_IGNORED_FIELDS
    assert outcome.shape_decision is InitShapeDecision.PASS
    assert outcome.finding_decision is InitShapeDecision.NUDGE
    assert outcome.compatibility_paths == []
    assert "unknown field in manifest.llm: context_limit" in outcome.warnings
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw

def test_root_only_context_limit_is_fully_effective_but_inert(tmp_path):
    raw = _context_limit_init(top=500000, nested_present=False, nested=None)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert outcome.shape_decision is InitShapeDecision.PASS
    assert outcome.compatibility_paths == []


def test_preset_materialization_discards_context_limit_from_effective_init(tmp_path):
    """A preset keeps its context metadata without creating an init source."""
    from lingtai.init_reader import reader_callbacks

    def fake_load_preset(name, working_dir=None):
        return {
            "name": "narrow",
            "description": {"summary": "narrow context"},
            "manifest": {
                "llm": {
                    "provider": "openai", "model": "gpt-4o",
                    "context_limit": 16384,
                },
                "capabilities": {},
            },
        }

    materialize, prepare = reader_callbacks(tmp_path, load_preset=fake_load_preset)
    raw = _context_limit_init(top=350000, nested_present=True, nested=500000)
    # Point the manifest at the active preset so materialize runs.
    manifest = json.loads(raw)["manifest"]
    manifest["preset"] = {"active": "narrow", "default": "narrow", "allowed": ["narrow"]}
    raw = json.dumps({**_REQUIRED, "manifest": manifest}, indent=2)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path, materialize=materialize, prepare=prepare)

    assert outcome.status is InitReadStatus.FULLY_EFFECTIVE
    assert outcome.shape_decision is InitShapeDecision.PASS
    # The raw root is retained only as inert compatibility data; the preset
    # value never crosses into the materialized LLM block or runtime policy.
    assert outcome.data["manifest"]["context_limit"] == 350000
    assert "context_limit" not in outcome.data["manifest"]["llm"]
    assert outcome.compatibility_paths == []
    # Raw init bytes are untouched.
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw


def test_stray_nested_context_limit_does_not_mask_fatal_read(tmp_path):
    """Failure precedence: a later materialization failure must stay
    READ_FAILED/UNKNOWN even when a stray nested context_limit was observed,
    so the init-config Nudge is not cleared or mis-titled as a benign shape.
    """
    from lingtai.init_reader import reader_callbacks

    def broken_load_preset(name, working_dir=None):
        raise KeyError(name)

    materialize, prepare = reader_callbacks(tmp_path, load_preset=broken_load_preset)
    manifest = json.loads(_context_limit_init(top=350000, nested_present=True, nested=500000))["manifest"]
    manifest["preset"] = {"active": "missing", "default": "missing", "allowed": ["missing"]}
    raw = json.dumps({**_REQUIRED, "manifest": manifest}, indent=2)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path, materialize=materialize, prepare=prepare,
                       failure_behavior="KEEP_PREVIOUS_EFFECTIVE")

    assert outcome.status is InitReadStatus.READ_FAILED
    assert outcome.finding_decision is InitShapeDecision.UNKNOWN
    assert outcome.stage in ("PRESET", "VALIDATE")


def test_failure_precedence_validate_and_resolve_stages(tmp_path):
    """VALIDATE and RESOLVE failures must also classify as UNKNOWN, not as a
    benign PASS/NUDGE that could clear an existing init-config Nudge.
    """
    # VALIDATE failure: old runtime keys are inert, so use a required LLM key.
    raw_data = json.loads(_context_limit_init(top="not-an-int", nested_present=True, nested=500000))
    raw_data["manifest"]["llm"]["provider"] = 5
    raw = json.dumps(raw_data)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path, failure_behavior="KEEP_PREVIOUS_EFFECTIVE")

    assert outcome.status is InitReadStatus.READ_FAILED
    assert outcome.stage == "VALIDATE"
    assert outcome.finding_decision is InitShapeDecision.UNKNOWN


def test_failure_precedence_resolve_stage_with_broken_paths(tmp_path):
    """RESOLVE failure via a monkeypatched resolver keeps the outcome
    READ_FAILED/UNKNOWN even with a stray nested context_limit present.
    """
    from lingtai import init_reader

    original = init_reader.resolve_paths

    def broken_resolve(data, working_dir):
        raise OSError("deliberate resolve failure")

    init_reader.resolve_paths = broken_resolve
    try:
        raw = _context_limit_init(top=500000, nested_present=True, nested=500000)
        _write_init(tmp_path, raw)
        outcome = read_init(tmp_path, failure_behavior="KEEP_PREVIOUS_EFFECTIVE")
    finally:
        init_reader.resolve_paths = original

    assert outcome.status is InitReadStatus.READ_FAILED
    assert outcome.stage == "RESOLVE"
    assert outcome.finding_decision is InitShapeDecision.UNKNOWN


def test_failure_precedence_over_mixed_bash_compatibility(tmp_path):
    """Mixed compatibility (bash->shell NUDGE) plus a fatal read still
    classifies as UNKNOWN, preserving the fatal stage over the benign shape.
    """
    from lingtai.init_reader import reader_callbacks

    def broken_load_preset(name, working_dir=None):
        raise KeyError(name)

    materialize, prepare = reader_callbacks(tmp_path, load_preset=broken_load_preset)
    manifest = json.loads(_context_limit_init(top=350000, nested_present=True, nested=500000))["manifest"]
    manifest["preset"] = {"active": "missing", "default": "missing", "allowed": ["missing"]}
    manifest["capabilities"] = {"bash": {"yolo": True}}
    raw = json.dumps({**_REQUIRED, "manifest": manifest}, indent=2)
    _write_init(tmp_path, raw)

    outcome = read_init(tmp_path, materialize=materialize, prepare=prepare,
                       failure_behavior="KEEP_PREVIOUS_EFFECTIVE")

    assert outcome.status is InitReadStatus.READ_FAILED
    assert outcome.finding_decision is InitShapeDecision.UNKNOWN
    assert outcome.stage in ("PRESET", "VALIDATE")
