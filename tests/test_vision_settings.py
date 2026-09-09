"""Focused proofs for Vision's read-only five-field settings inventory."""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch

import pytest

from lingtai.services.vision import VisionService
from lingtai.tools.vision import DECLARATION, get_schema, setup


_KEYS = (
    "provider",
    "base_url",
    "model",
    "api_key",
    "api_key_env",
    "max_tokens",
    "api_compat",
    "wire_api",
    "default_headers",
    "token_path",
    "instructions",
    "max_output_tokens",
    "timeout",
)
_FIELDS = ("key", "current", "default", "configurable", "comment")
_SENSITIVE = {
    "base_url",
    "api_key",
    "api_key_env",
    "default_headers",
    "token_path",
    "instructions",
}
_REDACTED = "<redacted>"


class _StubAgent:
    def __init__(self, working_dir: Path) -> None:
        self._working_dir = working_dir
        self.service = None
        self.tools: dict[str, dict] = {}
        self._official_tool_plugins: dict[str, object] = {}

    @property
    def working_dir(self) -> Path:
        return self._working_dir

    @property
    def official_tool_plugins(self):
        return MappingProxyType(self._official_tool_plugins)

    def _authorize_official_tool_declaration(self, _declaration) -> None:
        return None

    def _record_official_tool_binding(self, _declaration, _plugin) -> None:
        return None

    def _mount_official_tool(self, transaction) -> None:
        transaction.consume()
        plugin = transaction.plugin
        self.tools[plugin.name] = {
            "schema": plugin.schema,
            "handler": plugin.handler,
        }
        transaction.mark_mounted(self)

    def _claim_official_tool(self, transaction) -> None:
        self._official_tool_plugins[transaction.declaration.name] = (
            transaction.declaration
        )


def _write_local_settings(path: Path, **values) -> Path:
    target = path / "settings" / "vision.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"schema_version": 1, **values}), encoding="utf-8"
    )
    return target


def _show(manager, action_input: object = None) -> dict:
    value = {} if action_input is None else action_input
    return manager.handle(
        {"action": "settings", "input": value, "reasoning": "test"}
    )


def _by_key(result: dict) -> dict[str, dict]:
    return {row["key"]: row for row in result["settings"]}


def test_local_inventory_is_exact_applied_snapshot_and_has_true_defaults(
    tmp_path, monkeypatch
):
    pointer = "VISION_TEST_PRIVATE_POINTER"
    pointer_secret = "pointer-secret-sentinel"
    file_secret = "file-secret-sentinel"
    private_url = "https://private-vision.invalid/v1"
    monkeypatch.setenv(pointer, pointer_secret)
    owner_file = _write_local_settings(
        tmp_path,
        base_url=private_url,
        model="file-vision-model",
        api_key=file_secret,
        max_tokens=321,
    )

    with patch("lingtai.services.vision.openai.OpenAIVisionService") as factory:
        manager = setup(
            _StubAgent(tmp_path),
            provider="local",
            api_key_env=pointer,
            api_compat="anthropic",
        )
        factory.assert_called_once_with(
            api_key=pointer_secret,
            model="file-vision-model",
            base_url=private_url,
            wire_api="chat_completions",
            max_tokens=321,
        )
        factory.reset_mock()

        # A later file edit is prospective: both the service and SHOW retain
        # the successfully applied bind snapshot until the next refresh/bind.
        owner_file.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "base_url": "https://later.invalid/v1",
                    "model": "later-model",
                    "max_tokens": 999,
                }
            ),
            encoding="utf-8",
        )
        result = _show(manager)
        assert _show(manager) == result
        factory.assert_not_called()

    assert DECLARATION.settings is True
    assert DECLARATION.public_actions == (
        "analyze", "check", "list", "settings", "manual"
    )
    assert get_schema()["properties"]["action"]["enum"] == list(
        DECLARATION.public_actions
    )
    assert tuple(row["key"] for row in result["settings"]) == _KEYS
    assert all(tuple(row) == _FIELDS for row in result["settings"])
    assert all(row["configurable"] is True for row in result["settings"])

    rows = _by_key(result)
    assert rows["provider"]["current"] == "local"
    assert rows["provider"]["default"] is None
    assert rows["model"]["current"] == "file-vision-model"
    assert rows["model"]["default"] is None
    assert rows["max_tokens"]["current"] == 321
    assert rows["max_tokens"]["default"] == 1024
    assert rows["api_compat"]["current"] is None
    assert rows["api_compat"]["default"] is None
    assert rows["wire_api"]["current"] == "chat_completions"
    assert rows["wire_api"]["default"] == "chat_completions"
    assert rows["max_output_tokens"]["current"] is None
    assert rows["max_output_tokens"]["default"] is None
    assert rows["timeout"]["current"] is None
    assert rows["timeout"]["default"] is None
    for key in _SENSITIVE:
        assert rows[key]["current"] == _REDACTED
        assert rows[key]["default"] == _REDACTED
    rendered = repr(result)
    for private in (pointer, pointer_secret, file_secret, private_url):
        assert private not in rendered


def test_all_sensitive_route_inputs_are_redacted_by_construction(
    tmp_path, monkeypatch
):
    pointer = "VISION_TEST_OPENAI_POINTER"
    pointer_secret = "openai-pointer-secret"
    private_url = "https://private-openai.invalid/v1"
    private_header = "private-header-sentinel"
    monkeypatch.setenv(pointer, pointer_secret)
    with patch("lingtai.services.vision.create_vision_service") as factory:
        openai_manager = setup(
            _StubAgent(tmp_path),
            provider="openai",
            api_key_env=pointer,
            model="private-openai-model",
            base_url=private_url,
            default_headers={"Authorization": private_header},
        )
        openai_result = _show(openai_manager)

    private_token_path = "/private/codex-token-sentinel.json"
    private_instructions = "private-instructions-sentinel"
    private_codex_url = "https://private-codex.invalid/backend"
    with patch("lingtai.services.vision.create_vision_service") as factory:
        codex_manager = setup(
            _StubAgent(tmp_path),
            provider="codex",
            model="gpt-test-vision",
            base_url=private_codex_url,
            token_path=private_token_path,
            instructions=private_instructions,
            max_output_tokens=77,
            timeout=9.5,
        )
        codex_result = _show(codex_manager)
        factory.assert_called_once()

    private_model_path = str(tmp_path / "private-models" / "vision-model")
    with patch("lingtai.services.vision.openai.OpenAIVisionService"):
        path_model_result = _show(
            setup(
                _StubAgent(tmp_path),
                provider="local",
                model=private_model_path,
            )
        )

    for result in (openai_result, codex_result):
        rows = _by_key(result)
        for key in _SENSITIVE:
            assert rows[key]["current"] == _REDACTED
            assert rows[key]["default"] == _REDACTED
    codex_rows = _by_key(codex_result)
    openai_rows = _by_key(openai_result)
    assert openai_rows["model"]["current"] == "private-openai-model"
    assert openai_rows["model"]["default"] is None
    assert openai_rows["max_tokens"]["default"] == 1024
    assert codex_rows["wire_api"]["current"] == "responses"
    assert codex_rows["model"]["current"] == "gpt-test-vision"
    assert codex_rows["model"]["default"] is None
    assert codex_rows["max_output_tokens"]["current"] == 77
    assert codex_rows["timeout"]["current"] == 9.5
    assert codex_rows["timeout"]["default"] == 120.0
    path_model_rows = _by_key(path_model_result)
    assert path_model_rows["model"]["current"] == _REDACTED
    assert path_model_rows["model"]["default"] == _REDACTED
    rendered = repr((openai_result, codex_result, path_model_result))
    for private in (
        pointer,
        pointer_secret,
        private_url,
        private_header,
        private_token_path,
        private_instructions,
        private_codex_url,
        private_model_path,
    ):
        assert private not in rendered


def test_inherited_route_snapshots_only_the_same_active_provider(tmp_path):
    active = MagicMock()
    active.provider = "openai"
    active._model = "active-vision-model"
    active._base_url = "https://active-private.invalid/v1"
    active.api_key = "active-private-key"
    active._provider_defaults = {
        "openai": {
            "api_compat": "openai",
            "default_headers": {"X-Private": "active-private-header"},
            "wire_api": "auto",
            "use_responses_api": True,
        }
    }
    agent = _StubAgent(tmp_path)
    agent.service = active

    with patch("lingtai.services.vision.create_vision_service") as factory:
        result = _show(setup(agent))
        factory.assert_called_once_with(
            "openai",
            api_key="active-private-key",
            model="active-vision-model",
            base_url="https://active-private.invalid/v1",
            default_headers={"X-Private": "active-private-header"},
            wire_api="chat_completions",
        )

    rows = _by_key(result)
    assert rows["provider"]["current"] == "openai"
    assert rows["provider"]["default"] is None
    assert rows["model"]["current"] == "active-vision-model"
    assert rows["model"]["default"] is None
    assert rows["wire_api"]["current"] == "chat_completions"
    assert rows["wire_api"]["default"] == "chat_completions"
    assert rows["api_compat"]["current"] is None
    rendered = repr(result)
    for private in (
        "https://active-private.invalid/v1",
        "active-private-key",
        "X-Private",
        "active-private-header",
    ):
        assert private not in rendered


def test_api_compat_reports_only_a_protocol_selected_by_the_resolver(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as factory:
        result = _show(
            setup(
                _StubAgent(tmp_path),
                provider="custom",
                api_key="fake-relay-key",
                model="relay-vision-model",
                api_compat="anthropic",
            )
        )
        factory.assert_called_once_with(
            "anthropic",
            api_key="fake-relay-key",
            model="relay-vision-model",
        )

    assert _by_key(result)["api_compat"]["current"] == "anthropic"


def test_every_comment_targets_its_exact_owner_manual_heading(tmp_path):
    _write_local_settings(tmp_path, model="manual-target-model")
    with patch("lingtai.services.vision.openai.OpenAIVisionService"):
        rows = _show(setup(_StubAgent(tmp_path), provider="local"))["settings"]
    manual = (
        Path(__file__).parents[1]
        / "src/lingtai/tools/vision/manual/SKILL.md"
    ).read_text(encoding="utf-8")
    for row in rows:
        slug = row["key"].replace("_", "-")
        assert row["comment"] == f"vision-manual#setting-{slug}"
        assert f"## Setting: {slug}" in manual


@pytest.mark.parametrize("invalid", ["missing-model", "invalid-document"])
def test_unavailable_current_fails_the_whole_action_without_rows(
    tmp_path, invalid
):
    if invalid == "invalid-document":
        _write_local_settings(tmp_path, model="ignored", unknown=True)
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as factory:
        manager = setup(_StubAgent(tmp_path), provider="local")
        factory.assert_not_called()
    assert _show(manager) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_opaque_injected_service_does_not_fabricate_serialized_settings(tmp_path):
    manager = setup(
        _StubAgent(tmp_path), vision_service=MagicMock(spec=VisionService)
    )
    assert _show(manager) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_settings_is_strict_read_only_and_never_changes_owner_file(tmp_path):
    owner_file = _write_local_settings(tmp_path, model="unchanged-model")
    before = owner_file.read_bytes()
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as factory:
        manager = setup(_StubAgent(tmp_path), provider="local")
        factory.reset_mock()
        assert "settings" in _show(manager)
        for invalid in (
            {"set": "model", "value": "changed"},
            {"reset": "model"},
        ):
            assert _show(manager, invalid) == {
                "status": "failed",
                "error_code": "INVALID_ARGUMENT",
                "message": "unsupported vision input field",
            }
        factory.assert_not_called()
    assert owner_file.read_bytes() == before


def test_ordinary_analyze_action_is_unchanged(tmp_path):
    service = MagicMock(spec=VisionService)
    service.analyze_image.return_value = "ordinary-analysis"
    manager = setup(_StubAgent(tmp_path), vision_service=service)
    image = tmp_path / "image.png"
    image.write_bytes(b"not-decoded-by-mock")

    result = manager.handle(
        {
            "action": "analyze",
            "input": {"image_path": str(image), "question": None},
            "reasoning": "ordinary action",
        }
    )
    assert result == {"status": "ok", "analysis": "ordinary-analysis"}
    service.analyze_image.assert_called_once_with(
        str(image), prompt="Describe what you see in this image."
    )


def test_local_manual_selects_provider_before_owner_file_example(tmp_path):
    import re

    manual = Path(__file__).resolve().parents[1] / "src/lingtai/tools/vision/manual"
    body = (manual / "reference/backends.md").read_text(encoding="utf-8")
    examples = [json.loads(value) for value in re.findall(r"```json\n(.*?)\n\s*```", body, re.S)]
    capability = examples[0]["vision"]
    assert capability["provider"] == "local"
    document = examples[1]
    _write_local_settings(tmp_path, **{k: v for k, v in document.items() if k != "schema_version"})
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as factory:
        # File alone must not select a route or construct a client.
        unselected = setup(_StubAgent(tmp_path))
        assert "settings" not in _show(unselected)
        factory.assert_not_called()
        selected = setup(_StubAgent(tmp_path), **capability)
        assert _by_key(_show(selected))["provider"]["current"] == "local"
        assert factory.call_args.kwargs["model"] == document["model"]
    assert "file alone does not select" in body


def test_setting_anchor_routes_to_exact_detail_and_redaction_is_explicit():
    manual = Path(__file__).resolve().parents[1] / "src/lingtai/tools/vision/manual"
    root = (manual / "SKILL.md").read_text(encoding="utf-8")
    reference = (manual / "reference/settings.md").read_text(encoding="utf-8")
    for key in _KEYS:
        slug = key.replace("_", "-")
        section = root.split(f"## Setting: {slug}\n", 1)[1].split("\n## ", 1)[0]
        assert f"reference/settings.md#setting-{slug}" in section
        assert f"## Setting: {slug}" in reference
    assert "both current and default" in reference
    assert "even when unused" in reference
