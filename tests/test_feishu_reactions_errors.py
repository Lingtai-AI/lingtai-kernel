"""Focused Feishu public reactions and stable failure-result coverage."""
from __future__ import annotations

from collections import UserDict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lingtai.mcp_servers.feishu._errors import (
    FeishuOperationError,
    failure_result,
    operation_error_from_response,
)
from lingtai.mcp_servers.feishu._family import (
    FEISHU_SCHEMA,
    _basic_validate,
    handle_feishu,
)
from lingtai.mcp_servers.feishu.manager import FeishuManager


_FAILURE_KEYS = {
    "status",
    "error",
    "message",
    "error_code",
    "retryable",
    "retry_after_seconds",
}


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error: FeishuOperationError | None = None

    def add_reaction_with_id(self, message_id: str, emoji_type: str) -> str:
        self.calls.append(("add", message_id, emoji_type))
        if self.error is not None:
            raise self.error
        return "reaction-provider-id"

    def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        self.calls.append(("remove", message_id, reaction_id))
        if self.error is not None:
            raise self.error
        return True


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.default_account = account
        self.account = account

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == self.account.alias
        return self.account


def _manager(tmp_path: Path) -> tuple[FeishuManager, _FakeAccount]:
    account = _FakeAccount()
    return (
        FeishuManager(
            _FakeService(account),
            working_dir=tmp_path,
            on_inbound=lambda _payload: None,
        ),
        account,
    )


def _call(manager: FeishuManager, input_: dict[str, Any]) -> dict[str, Any]:
    return handle_feishu(
        manager,
        {"action": "react", "input": input_, "reasoning": "focused test"},
    )


def _branches() -> dict[str, dict[str, Any]]:
    branches = FEISHU_SCHEMA["properties"]["input"]["anyOf"]
    return {
        branch["title"].removesuffix(" input"): branch for branch in branches
    }


def test_react_schema_and_dispatch_keep_add_remove_fields_disjoint(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    schema = _branches()["react"]
    compound_id = "main:oc_chat:om_target"

    assert _basic_validate(
        {"message_id": compound_id, "operation": "add", "emoji_type": "SMILE"},
        schema,
    )
    assert _basic_validate(
        {
            "message_id": compound_id,
            "operation": "remove",
            "reaction_id": "reaction-provider-id",
        },
        schema,
    )

    invalid = (
        {"message_id": compound_id, "operation": "add"},
        {"message_id": compound_id, "operation": "remove"},
        {
            "message_id": compound_id,
            "operation": "add",
            "emoji_type": "SMILE",
            "reaction_id": "not-for-add",
        },
        {
            "message_id": compound_id,
            "operation": "remove",
            "reaction_id": "reaction-provider-id",
            "emoji_type": "not-for-remove",
        },
    )
    for input_ in invalid:
        result = _call(manager, input_)
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_ARGUMENT"
        assert _FAILURE_KEYS.issubset(result)
    assert account.calls == []


def test_public_react_returns_provider_id_and_uses_it_for_remove(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    compound_id = "main:oc_chat:om_target"

    added = _call(
        manager,
        {"message_id": compound_id, "operation": "add", "emoji_type": "SMILE"},
    )
    removed = _call(
        manager,
        {
            "message_id": compound_id,
            "operation": "remove",
            "reaction_id": added["reaction_id"],
        },
    )

    assert added == {
        "status": "added",
        "message_id": compound_id,
        "reaction_id": "reaction-provider-id",
        "emoji_type": "SMILE",
    }
    assert removed == {
        "status": "removed",
        "message_id": compound_id,
        "reaction_id": "reaction-provider-id",
    }
    assert account.calls == [
        ("add", "om_target", "SMILE"),
        ("remove", "om_target", "reaction-provider-id"),
    ]


@pytest.mark.parametrize(
    ("raw_code", "expected_code", "retryable", "retry_after"),
    [
        (230003, "PERMISSION_DENIED", False, None),
        (230001, "FORMAT_ERROR", False, None),
        (99992354, "TARGET_REVOKED", False, None),
        (99991402, "RATE_LIMITED", True, 17.0),
    ],
)
def test_rest_errors_keep_public_taxonomy_and_retry_after(
    raw_code: int,
    expected_code: str,
    retryable: bool,
    retry_after: float | None,
) -> None:
    headers = UserDict(
        {"Retry-After": "17"} if expected_code == "RATE_LIMITED" else {},
    )
    response = SimpleNamespace(
        code=raw_code,
        msg="provider rejected request",
        raw=SimpleNamespace(headers=headers),
    )

    result = failure_result(operation_error_from_response("probe", response))

    assert set(result) == _FAILURE_KEYS
    assert result["status"] == "failed"
    assert result["error"] == result["message"]
    assert result["error_code"] == expected_code
    assert result["retryable"] is retryable
    assert result["retry_after_seconds"] == retry_after


def test_manager_surfaces_classified_reaction_failure_without_retry(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    account.error = FeishuOperationError(
        "rate limited",
        error_code="RATE_LIMITED",
        retryable=True,
        retry_after_seconds=8,
    )

    result = _call(
        manager,
        {
            "message_id": "main:oc_chat:om_target",
            "operation": "add",
            "emoji_type": "SMILE",
        },
    )

    assert result == {
        "status": "failed",
        "error": "rate limited",
        "message": "rate limited",
        "error_code": "RATE_LIMITED",
        "retryable": True,
        "retry_after_seconds": 8.0,
    }
    assert account.calls == [("add", "om_target", "SMILE")]


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"action": "missing", "input": {}, "reasoning": "test"},
        {"action": "accounts", "input": {}, "reasoning": 3},
        {
            "action": "accounts",
            "input": {},
            "reasoning": "test",
            "unsupported": True,
        },
    ],
)
def test_family_validation_failures_always_use_stable_shape(args: dict[str, Any]) -> None:
    result = handle_feishu(None, args)

    assert set(result) == _FAILURE_KEYS
    assert result["status"] == "failed"
    assert result["error"] == result["message"]
    assert result["retryable"] is False
    assert result["retry_after_seconds"] is None
