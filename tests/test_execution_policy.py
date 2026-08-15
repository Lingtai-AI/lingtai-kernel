from __future__ import annotations

import json

import pytest

from lingtai.execution_policy import (
    ExecutionPolicyCompatibilityError,
    ExecutionPolicyConfigurationError,
    ExecutionPolicyUnavailableError,
    ExecutionRequest,
    PassThroughExecutionPolicy,
    load_execution_policy,
)


def _write_policy_files(tmp_path, *, availability: dict[str, bool]) -> dict[str, object]:
    (tmp_path / "policy.json").write_text(
        json.dumps(
            {
                "api_version": 1,
                "workloads": {
                    "worker": [
                        {"route_id": "primary", "preset": "presets/fast.json"},
                        {"route_id": "secondary", "preset": "presets/spare.json"},
                    ],
                    "review": [
                        {"route_id": "review", "preset": "presets/review.json"}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "health.json").write_text(
        json.dumps(
            {
                "api_version": 1,
                "routes": {
                    route_id: {"available": available}
                    for route_id, available in availability.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return {
        "api_version": 1,
        "adapter": "configured",
        "config": "policy.json",
        "health": "health.json",
    }


def _request(
    *, workload: str = "worker", preset: str | None = None
) -> ExecutionRequest:
    return ExecutionRequest(
        workload=workload,
        preset=preset,
        backend="lingtai",
        parent_address="parent@example",
        parent_is_admin=False,
        allowed_preset_refs=(
            "presets/fast.json",
            "presets/spare.json",
            "presets/review.json",
            "presets/explicit.json",
        ),
    )


def test_absent_declaration_returns_pass_through(tmp_path):
    policy = load_execution_policy(None, tmp_path)

    assert isinstance(policy, PassThroughExecutionPolicy)
    decision = policy.decide(_request())
    assert decision.preset is None
    assert decision.backend == "lingtai"
    assert decision.route_id is None


def test_api_mismatch_fails_fast(tmp_path):
    with pytest.raises(ExecutionPolicyCompatibilityError, match="expected 1"):
        load_execution_policy({"api_version": 2}, tmp_path)


def test_explicit_preset_takes_precedence(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": True, "secondary": True, "review": True},
    )
    policy = load_execution_policy(declaration, tmp_path)

    decision = policy.decide(_request(preset="presets/explicit.json"))

    assert decision.preset == "presets/explicit.json"
    assert decision.backend == "lingtai"
    assert decision.route_id is None


def test_exact_workload_selects_first_healthy_route(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": True, "secondary": True, "review": True},
    )
    policy = load_execution_policy(declaration, tmp_path)

    decision = policy.decide(_request(workload="review"))

    assert decision.preset == "presets/review.json"
    assert decision.backend == "lingtai"
    assert decision.route_id == "review"


def test_allowed_preset_uses_daemon_path_normalization(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": True, "secondary": True, "review": True},
    )
    policy = load_execution_policy(declaration, tmp_path)
    request = _request()
    request = type(request)(
        workload=request.workload,
        preset=request.preset,
        backend=request.backend,
        parent_address=request.parent_address,
        parent_is_admin=request.parent_is_admin,
        allowed_preset_refs=("./presets/fast.json",),
    )

    decision = policy.decide(request)

    assert decision.preset == "presets/fast.json"
    assert decision.route_id == "primary"


def test_workload_matching_does_not_parse_or_normalize_text(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": True, "secondary": True, "review": True},
    )
    policy = load_execution_policy(declaration, tmp_path)

    with pytest.raises(ExecutionPolicyUnavailableError, match="workload 'Review'"):
        policy.decide(_request(workload="Review"))


def test_unavailable_primary_selects_next_healthy_candidate(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": False, "secondary": True, "review": True},
    )
    policy = load_execution_policy(declaration, tmp_path)

    decision = policy.decide(_request())

    assert decision.preset == "presets/spare.json"
    assert decision.route_id == "secondary"


def test_no_healthy_candidate_fails(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": False, "secondary": False, "review": True},
    )
    policy = load_execution_policy(declaration, tmp_path)

    with pytest.raises(ExecutionPolicyUnavailableError, match="workload 'worker'"):
        policy.decide(_request())


def test_malformed_external_json_fails_at_load_boundary(tmp_path):
    declaration = _write_policy_files(
        tmp_path,
        availability={"primary": True, "secondary": True, "review": True},
    )
    (tmp_path / "health.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ExecutionPolicyConfigurationError, match="valid.*health JSON"):
        load_execution_policy(declaration, tmp_path)
