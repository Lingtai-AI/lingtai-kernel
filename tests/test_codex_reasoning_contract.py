"""Codex-only reasoning construction contract acceptance tests.

All factory tests are hermetic: they use the registered Codex route with a
mocked token manager and replace the OpenAI client before dispatch.
"""

from __future__ import annotations

import json
import re
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import lingtai  # noqa: F401  (registers adapters)
import pytest

from lingtai.init_schema import validate_init
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.kernel.llm.reasoning import ReasoningRouteContext, ReasoningRouteKey
from lingtai.kernel.session import SessionManager
from lingtai.llm.service import LLMService
from tests._migration_workspace_helpers import load_preset
from tests.test_architecture_documents import _assert_repo_file, _read_document


_OFFICIAL_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CLI_SOURCE = "codex_cli_0_144_1_model_metadata"
_OPENAI_DOCS_SOURCE = "openai_gpt_5_3_codex_model_docs"


@dataclass
class _Event:
    type: str
    response: object | None = None


class _FakeResponses:
    def __init__(self) -> None:
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        yield _Event(
            "response.completed",
            response=SimpleNamespace(id="resp_contract", usage=None),
        )


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


class _FakeWsTransport:
    def __init__(self) -> None:
        self.sent_frames: list[dict] = []

    def connect(self, *, headers):
        return None

    def stream(self, frame):
        self.sent_frames.append(frame)
        yield _Event(
            "response.completed",
            response=SimpleNamespace(id="resp_contract_ws", usage=None),
        )

    def send_response_processed(self, response_id):
        pass

    def close(self):
        pass


def _codex_service(
    model: str = "gpt-5.6-sol", *, base_url=None, provider: str = "codex"
):
    """Build the registered Codex service/adapter pair with a fake client.

    The real client is constructed only so the resolved endpoint can be
    asserted; every test dispatches through ``_FakeClient`` instead.
    """
    with mock.patch("lingtai.auth.codex.CodexTokenManager") as manager:
        manager.return_value.get_access_token.return_value = "fake-token"
        manager.return_value.get_account_id.return_value = None
        service = LLMService(provider=provider, model=model, base_url=base_url)
        adapter = service.get_adapter(provider, base_url)
    expected_url = base_url or _OFFICIAL_CODEX_BASE_URL
    assert str(adapter._client.base_url).rstrip("/") == expected_url.rstrip("/")
    adapter._client = _FakeClient()
    return service, adapter


def _registered_codex_adapter(
    model: str = "gpt-5.6-sol", *, base_url=None, provider: str = "codex"
):
    return _codex_service(model, base_url=base_url, provider=provider)[1]


def _assert_lingtai_codex_default(result) -> None:
    """Assert the exact omitted/default capture shared by every Codex route."""
    assert result is not None
    assert result.requested is None
    assert result.normalized == "xhigh"
    assert result.actual == "xhigh"
    assert result.source == "lingtai_codex_default"
    assert result.capability_source == _CLI_SOURCE


def _valid_init() -> dict:
    return {
        "manifest": {
            "llm": {
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "thinking": "ultra",
            },
        },
        "covenant": "",
        "pad": "",
        "lingtai": "",
        "soul": "",
    }


@pytest.mark.parametrize("provider", ["codex", "codex-pool", "codex_pool"])
def test_all_registered_codex_spellings_receive_contract(provider):
    from lingtai.llm.openai.codex_reasoning import CodexReasoningContract

    recorded_contexts = []
    original_construct = CodexReasoningContract.construct

    def _record_context(contract, route, requested):
        recorded_contexts.append(route)
        return original_construct(contract, route, requested)

    with mock.patch.object(CodexReasoningContract, "construct", _record_context):
        session = _registered_codex_adapter(provider=provider).create_chat(
            "gpt-5.6-sol", "system", thinking="max"
        )

    assert recorded_contexts == [
        ReasoningRouteContext(
            key=ReasoningRouteKey(provider, "responses"),
            endpoint=_OFFICIAL_CODEX_BASE_URL,
            model="gpt-5.6-sol",
        )
    ]
    assert session.reasoning_construction_result().actual == "max"


def test_registered_codex_sol_ultra_constructs_and_emits_ultra():
    adapter = _registered_codex_adapter()

    session = adapter.create_chat("gpt-5.6-sol", "system", thinking="ultra")
    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert sent["reasoning"] == {"effort": "ultra"}
    assert "reasoning_effort" not in sent


def test_manifest_accepts_registered_codex_sol_ultra():
    validate_init(_valid_init())


def test_saved_preset_accepts_registered_codex_sol_ultra(tmp_path):
    preset_path = tmp_path / "sol-ultra.json"
    preset_path.write_text(
        json.dumps(
            {
                "name": "sol-ultra",
                "description": "Codex Sol with ultra reasoning.",
                "manifest": _valid_init()["manifest"],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_preset(str(preset_path))

    assert loaded["manifest"]["llm"]["thinking"] == "ultra"


def test_registered_codex_omitted_reasoning_exposes_captured_default():
    session = _registered_codex_adapter().create_chat("gpt-5.6-sol", "system")

    _assert_lingtai_codex_default(session.reasoning_construction_result())


def test_reasoning_result_and_codex_patch_are_frozen():
    result = _registered_codex_adapter().create_chat(
        "gpt-5.6-sol", "system", thinking="high"
    ).reasoning_construction_result()

    with pytest.raises(FrozenInstanceError):
        result.actual = "low"
    with pytest.raises(FrozenInstanceError):
        result.wire_patch.effort = "low"


def test_repeated_sends_reuse_result_and_new_session_constructs_fresh_result():
    adapter = _registered_codex_adapter()
    session = adapter.create_chat("gpt-5.6-sol", "system")
    captured = session.reasoning_construction_result()

    session.send_stream("one")
    assert session.reasoning_construction_result() is captured
    session.send_stream("two")
    assert session.reasoning_construction_result() is captured

    rebuilt = adapter.create_chat("gpt-5.6-sol", "system")
    assert rebuilt.reasoning_construction_result() is not captured
    assert rebuilt.reasoning_construction_result() == captured


def test_config_change_is_captured_only_by_rebuilt_session():
    adapter = _registered_codex_adapter()
    config = {"thinking": "high"}
    original = adapter.create_chat(
        "gpt-5.6-sol", "system", thinking=config["thinking"]
    )
    original_result = original.reasoning_construction_result()

    config["thinking"] = "ultra"
    original.send_stream("still high")
    original_sent = adapter._client.responses.kwargs[-1]

    rebuilt = adapter.create_chat(
        "gpt-5.6-sol", "system", thinking=config["thinking"]
    )
    rebuilt.send_stream("now ultra")
    rebuilt_sent = adapter._client.responses.kwargs[-1]

    assert original.reasoning_construction_result() is original_result
    assert original_result.actual == "high"
    assert original_sent["reasoning"] == {"effort": "high"}
    assert rebuilt.reasoning_construction_result() is not original_result
    assert rebuilt.reasoning_construction_result().actual == "ultra"
    assert rebuilt_sent["reasoning"] == {"effort": "ultra"}


@pytest.mark.parametrize(
    ("thinking", "expected"),
    [("ultra", "ultra"), ("xhigh", "xhigh"), (None, "xhigh")],
)
def test_registered_codex_reasoning_has_rest_ws_semantic_parity(thinking, expected):
    adapter = _registered_codex_adapter()
    create_kwargs = {} if thinking is None else {"thinking": thinking}

    rest = adapter.create_chat("gpt-5.6-sol", "system", **create_kwargs)
    rest.send_stream("rest")
    rest_request = adapter._client.responses.kwargs[-1]

    ws = adapter.create_chat("gpt-5.6-sol", "system", **create_kwargs)
    transport = _FakeWsTransport()
    ws._transport = "websocket"
    ws._ws_enabled = True
    ws._ws_transport_factory = lambda url, headers: transport
    ws.send_stream("ws")

    assert rest_request["reasoning"] == {"effort": expected}
    assert transport.sent_frames[-1]["reasoning"] == {"effort": expected}


def test_registered_codex_luna_rejects_ultra_before_dispatch_with_exact_allowlist():
    adapter = _registered_codex_adapter("gpt-5.6-luna")

    with pytest.raises(ValueError) as exc_info:
        adapter.create_chat("gpt-5.6-luna", "system", thinking="ultra")

    message = str(exc_info.value)
    assert "gpt-5.6-luna" in message
    assert "low, medium, high, xhigh, max" in message
    assert "ultra" not in message.split("must be one of:", 1)[-1]
    assert adapter._client.responses.kwargs == []


@pytest.mark.parametrize(
    "thinking",
    ["none", "minimal", "inherit", "HIGH", " high", "high ", "", True, 1],
)
def test_registered_codex_rejects_non_descriptor_tokens_before_dispatch(thinking):
    adapter = _registered_codex_adapter()

    with pytest.raises(ValueError, match="Codex reasoning.*gpt-5.6-sol"):
        adapter.create_chat("gpt-5.6-sol", "system", thinking=thinking)

    assert adapter._client.responses.kwargs == []


@pytest.mark.parametrize(
    ("model", "base_url"),
    [
        ("unknown-codex-model", None),
        ("gpt-5.6-sol", "https://custom.invalid/backend-api/codex"),
    ],
)
def test_unregistered_model_or_endpoint_uses_legacy_path(model, base_url):
    adapter = _registered_codex_adapter(model, base_url=base_url)

    session = adapter.create_chat(model, "system", thinking="high")
    assert session.reasoning_construction_result() is None
    session.send_stream("hello")

    assert adapter._client.responses.kwargs[-1]["reasoning"] == {"effort": "high"}


def test_direct_codex_adapter_without_injected_contract_retains_legacy_default():
    from lingtai.llm.openai.adapter import CodexOpenAIAdapter

    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url=_OFFICIAL_CODEX_BASE_URL,
        use_responses=True,
        force_responses=True,
    )
    adapter._client = _FakeClient()

    session = adapter.create_chat("gpt-5.6-sol", "system")
    assert session.reasoning_construction_result() is None
    session.send_stream("hello")

    assert adapter._client.responses.kwargs[-1]["reasoning"] == {"effort": "xhigh"}


def test_registered_create_chat_and_generate_share_omitted_construction_path():
    adapter = _registered_codex_adapter()

    adapter.create_chat("gpt-5.6-sol", "system").send_stream("chat")
    chat_request = adapter._client.responses.kwargs[-1]
    adapter.generate("gpt-5.6-sol", "one shot", system_prompt="system")
    generate_request = adapter._client.responses.kwargs[-1]

    assert chat_request["reasoning"] == {"effort": "xhigh"}
    assert generate_request["reasoning"] == {"effort": "xhigh"}


# ---------------------------------------------------------------------------
# Exact seven-row descriptor matrix (model, allowlist order, capability source)
# ---------------------------------------------------------------------------

# One row per exact contract descriptor: model, allowed tuple in intended
# order, capability-source id, and one exact negative just outside that row's
# vocabulary boundary.
_EXACT_DESCRIPTOR_ROWS = [
    ("gpt-5.6-sol", ("low", "medium", "high", "xhigh", "max", "ultra"), _CLI_SOURCE, "none"),
    ("gpt-5.6-sol-wm", ("low", "medium", "high", "xhigh", "max", "ultra"), _CLI_SOURCE, "minimal"),
    ("gpt-5.6-terra", ("low", "medium", "high", "xhigh", "max", "ultra"), _CLI_SOURCE, "none"),
    ("gpt-5.6-luna", ("low", "medium", "high", "xhigh", "max"), _CLI_SOURCE, "ultra"),
    ("gpt-5.3-codex-spark", ("low", "medium", "high", "xhigh"), _CLI_SOURCE, "max"),
    ("codex-auto-review", ("low", "medium", "high", "xhigh", "max"), _CLI_SOURCE, "ultra"),
    ("gpt-5.3-codex", ("low", "medium", "high", "xhigh"), _OPENAI_DOCS_SOURCE, "max"),
]


@pytest.mark.parametrize(
    ("model", "allowed", "capability_source", "rejected"), _EXACT_DESCRIPTOR_ROWS
)
def test_every_descriptor_row_constructs_exact_allowlist_and_rejects_boundary(
    model, allowed, capability_source, rejected
):
    adapter = _registered_codex_adapter(model)

    for token in allowed:
        result = adapter.create_chat(
            model, "system", thinking=token
        ).reasoning_construction_result()
        assert result.requested == token
        assert result.normalized == token
        assert result.actual == token
        assert result.source == "explicit_config"
        assert result.capability_source == capability_source
        request = {}
        result.wire_patch.apply(request)
        assert request == {"reasoning": {"effort": token}}

    with pytest.raises(ValueError) as exc_info:
        adapter.create_chat(model, "system", thinking=rejected)
    assert str(exc_info.value) == (
        f"Codex reasoning for model {model!r} must be one of: {', '.join(allowed)}"
    )
    # No case, whitespace, or prefix inference on any row.
    for alias in (allowed[0].upper(), f" {allowed[0]}", allowed[0][:2]):
        with pytest.raises(ValueError):
            adapter.create_chat(model, "system", thinking=alias)
    assert adapter._client.responses.kwargs == []


# ---------------------------------------------------------------------------
# Main-session construction through SessionManager (kernel AgentConfig route)
# ---------------------------------------------------------------------------


def _codex_session_manager(config: AgentConfig):
    service, adapter = _codex_service()
    session_manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="main",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    return session_manager, adapter


def _non_codex_session_manager(config: AgentConfig, *, service_provider=None):
    service = mock.MagicMock()
    service.model = "gpt-4o"
    if service_provider is not None:
        service.provider = service_provider
    session_manager = SessionManager(
        llm_service=service,
        config=config,
        agent_name="main",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    return session_manager, service


def _ensure_session(session_manager):
    return session_manager.ensure_session()


def _rebuild_session(session_manager):
    session_manager._rebuild_session(ChatInterface())
    return session_manager.chat


# Both main-session construction seams, and both config spellings that reach
# the registered Codex route (explicit provider/model, and the default
# AgentConfig backed by an injected Codex service).
_MAIN_SESSION_SEAMS = [
    pytest.param(_ensure_session, id="ensure_session"),
    pytest.param(_rebuild_session, id="rebuild"),
]
_CODEX_MAIN_SESSION_CONFIGS = [
    pytest.param({"provider": "codex", "model": "gpt-5.6-sol"}, id="explicit_codex"),
    pytest.param({}, id="default_config"),
]


@pytest.mark.parametrize("config_kwargs", _CODEX_MAIN_SESSION_CONFIGS)
@pytest.mark.parametrize("seam", _MAIN_SESSION_SEAMS)
def test_main_session_codex_omission_constructs_lingtai_default(seam, config_kwargs):
    session_manager, adapter = _codex_session_manager(AgentConfig(**config_kwargs))

    chat = seam(session_manager)

    _assert_lingtai_codex_default(chat.reasoning_construction_result())
    chat.send_stream("hello")
    assert adapter._client.responses.kwargs[-1]["reasoning"] == {"effort": "xhigh"}


def test_main_session_explicit_codex_high_stays_explicit():
    session_manager, adapter = _codex_session_manager(
        AgentConfig(provider="codex", model="gpt-5.6-sol", thinking="high")
    )

    chat = session_manager.ensure_session()
    result = chat.reasoning_construction_result()

    assert result.requested == "high"
    assert result.normalized == "high"
    assert result.actual == "high"
    assert result.source == "explicit_config"
    chat.send_stream("hello")
    assert adapter._client.responses.kwargs[-1]["reasoning"] == {"effort": "high"}


def test_explicit_literal_sentinel_string_keeps_explicit_provenance():
    """An explicit value colliding with an internal sentinel spelling stays explicit.

    It is an invalid explicit Codex token, so the contract rejects it before
    dispatch; it must never be silently reinterpreted as constructor omission.
    """
    literal = "__lingtai_thinking_constructor_omitted__"
    config = AgentConfig(provider="codex", model="gpt-5.6-sol", thinking=literal)

    assert config.thinking == literal

    session_manager, adapter = _codex_session_manager(config)
    with pytest.raises(ValueError, match="Codex reasoning.*gpt-5.6-sol"):
        session_manager.ensure_session()
    assert adapter._client.responses.kwargs == []


@pytest.mark.parametrize(
    ("config_kwargs", "service_provider"),
    [({}, "openai"), ({"provider": "openai"}, None)],
)
def test_main_session_non_codex_omitted_thinking_keeps_legacy_high(
    config_kwargs, service_provider
):
    assert AgentConfig().thinking == "high"

    session_manager, service = _non_codex_session_manager(
        AgentConfig(**config_kwargs), service_provider=service_provider
    )

    session_manager.ensure_session()
    assert service.create_session.call_args.kwargs["thinking"] == "high"


# Explicitly supplied falsey values are NOT constructor omission. The session
# seam must not coerce them into a default; the registered Codex contract owns
# the rejection and it must happen before any provider request is built.
_EXPLICIT_FALSEY_THINKING = ["", None, False, 0]


@pytest.mark.parametrize("thinking", _EXPLICIT_FALSEY_THINKING)
@pytest.mark.parametrize("seam", _MAIN_SESSION_SEAMS)
def test_main_session_explicit_falsey_codex_thinking_rejected_before_dispatch(
    seam, thinking
):
    config = AgentConfig(provider="codex", model="gpt-5.6-sol", thinking=thinking)

    assert config.thinking is thinking
    assert config._thinking_constructor_omitted is False

    session_manager, adapter = _codex_session_manager(config)

    with pytest.raises(ValueError, match="Codex reasoning.*gpt-5.6-sol"):
        seam(session_manager)
    assert adapter._client.responses.kwargs == []


@pytest.mark.parametrize("thinking", _EXPLICIT_FALSEY_THINKING)
def test_main_session_non_codex_explicit_falsey_thinking_is_not_coerced(thinking):
    """The seam forwards every explicit value unchanged, on any provider.

    Only a constructor-omitted value keeps the legacy ``"high"``; the seam
    never substitutes a value the caller did not supply.
    """
    session_manager, service = _non_codex_session_manager(
        AgentConfig(provider="openai", thinking=thinking)
    )

    session_manager.ensure_session()
    assert service.create_session.call_args.kwargs["thinking"] is thinking


# ---------------------------------------------------------------------------
# Governed-child Contract governance (root index, headings, shared manual edge)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPENAI_CONTRACT_PATH = "src/lingtai/llm/openai/CONTRACT.md"
_OPENAI_ANATOMY_PATH = "src/lingtai/llm/openai/ANATOMY.md"
_KERNEL_LLM_ANATOMY_PATH = "src/lingtai/kernel/llm/ANATOMY.md"
_LLM_ANATOMY_PATH = "src/lingtai/llm/ANATOMY.md"
_CODEX_CONTRACT_MANUAL_PATH = (
    "src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md"
)


def test_codex_contract_is_valid_governed_child_with_shared_manual_edge():
    root_meta, _ = _read_document(_REPO_ROOT / "CONTRACT.md")
    assert root_meta["related_files"].count(_OPENAI_CONTRACT_PATH) == 1

    contract_meta, contract_body = _read_document(_REPO_ROOT / _OPENAI_CONTRACT_PATH)
    assert list(contract_meta.keys()) == [
        "name",
        "contract_version",
        "root_contract",
        "related_files",
        "maintenance",
    ]
    assert isinstance(contract_meta["name"], str)
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", contract_meta["name"])
    assert isinstance(contract_meta["contract_version"], int)
    assert not isinstance(contract_meta["contract_version"], bool)
    assert contract_meta["contract_version"] > 0
    assert contract_meta["root_contract"] == "CONTRACT.md"
    related = contract_meta["related_files"]
    assert isinstance(related, list) and related
    assert len(related) == len(set(related))
    for path in related:
        assert isinstance(path, str)
        _assert_repo_file(path)
    assert isinstance(contract_meta["maintenance"], str)
    assert contract_meta["maintenance"].strip()

    headings = [
        line[3:].strip()
        for line in contract_body.splitlines()
        if line.startswith("## ")
    ]
    assert headings == [
        "Purpose",
        "Behavior",
        "Port",
        "Adapters",
        "Contract rules",
        "Contract tests",
        "Maintenance",
    ]

    anatomy_meta, _ = _read_document(_REPO_ROOT / _OPENAI_ANATOMY_PATH)
    assert _OPENAI_ANATOMY_PATH in related
    assert _CODEX_CONTRACT_MANUAL_PATH in related
    assert _CODEX_CONTRACT_MANUAL_PATH in anatomy_meta["related_files"]


def test_no_false_shared_anatomy_ownership_edges():
    """The Codex child Contract owns only its co-located Anatomy twin.

    The provider-neutral kernel protocol Anatomy and the multi-provider
    adapter-map Anatomy are shared documents; a reciprocal ownership edge from
    this provider-specific Contract would falsely claim them as owned
    implementation Anatomies.
    """
    contract_meta, _ = _read_document(_REPO_ROOT / _OPENAI_CONTRACT_PATH)
    related = contract_meta["related_files"]
    assert _KERNEL_LLM_ANATOMY_PATH not in related
    assert _LLM_ANATOMY_PATH not in related

    kernel_llm_meta, _ = _read_document(_REPO_ROOT / _KERNEL_LLM_ANATOMY_PATH)
    assert _OPENAI_CONTRACT_PATH not in kernel_llm_meta["related_files"]
    llm_meta, _ = _read_document(_REPO_ROOT / _LLM_ANATOMY_PATH)
    assert _OPENAI_CONTRACT_PATH not in llm_meta["related_files"]

    # The co-located governed pair stays reciprocal.
    assert _OPENAI_ANATOMY_PATH in related
    anatomy_meta, _ = _read_document(_REPO_ROOT / _OPENAI_ANATOMY_PATH)
    assert _OPENAI_CONTRACT_PATH in anatomy_meta["related_files"]


def test_manual_edge_is_llm_adapters_reference_teaching_codex_reasoning():
    contract_meta, contract_body = _read_document(_REPO_ROOT / _OPENAI_CONTRACT_PATH)
    anatomy_meta, _ = _read_document(_REPO_ROOT / _OPENAI_ANATOMY_PATH)
    assert _CODEX_CONTRACT_MANUAL_PATH in contract_meta["related_files"]
    assert _CODEX_CONTRACT_MANUAL_PATH in anatomy_meta["related_files"]
    assert _CODEX_CONTRACT_MANUAL_PATH in contract_body

    manual_meta, manual_body = _read_document(_REPO_ROOT / _CODEX_CONTRACT_MANUAL_PATH)
    for path in (
        _OPENAI_CONTRACT_PATH,
        "src/lingtai/llm/openai/codex_reasoning.py",
        "src/lingtai/init.jsonc",
    ):
        assert path in manual_meta["related_files"], path
    # The reference actually teaches the bounded what/how/why for this
    # capability without duplicating the Contract's seven-row model table.
    for needle in (
        "manifest.llm.thinking",
        "xhigh",
        "reasoning_requested",
        "lingtai_codex_default",
    ):
        assert needle in manual_body, needle


def _first_line_containing(path: Path, needle: str) -> int:
    for number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not found in {path}")


def _cited_ranges(text: str, cited_file: str) -> list[tuple[int, int]]:
    pattern = re.escape(cited_file) + r":(\d+)(?:-(\d+))?"
    return [
        (int(match.group(1)), int(match.group(2) or match.group(1)))
        for match in re.finditer(pattern, text)
    ]


def _assert_exact_citation(
    text: str, cited_file: str, source_path: Path, needle: str
) -> None:
    line = _first_line_containing(source_path, needle)
    ranges = _cited_ranges(text, cited_file)
    assert any(start <= line <= end for start, end in ranges), (
        cited_file,
        needle,
        line,
        ranges,
    )


def test_components_citations_are_exact_for_reasoning_and_init_schema():
    anatomy_text = (_REPO_ROOT / _OPENAI_ANATOMY_PATH).read_text(encoding="utf-8")
    row = next(
        line
        for line in anatomy_text.splitlines()
        if line.startswith("| `codex_reasoning.py`")
    )
    reasoning_source = _REPO_ROOT / "src/lingtai/llm/openai/codex_reasoning.py"
    for needle in (
        "_CODEX_MODELS: dict",
        "class CodexReasoningPatch",
        "class CodexReasoningContract",
        "descriptor = _CODEX_MODELS.get(route.model)",
    ):
        _assert_exact_citation(row, "codex_reasoning.py", reasoning_source, needle)
    _assert_exact_citation(
        row,
        "tests/test_codex_reasoning_contract.py",
        _REPO_ROOT / "tests/test_codex_reasoning_contract.py",
        "def test_every_descriptor_row_constructs_exact_allowlist_and_rejects_boundary",
    )

    wrapper_text = (_REPO_ROOT / "src/lingtai/ANATOMY.md").read_text(encoding="utf-8")
    paragraph = next(
        line
        for line in wrapper_text.splitlines()
        if line.startswith("**`init_reader.py` / `init_schema.py`**")
    )
    schema_source = _REPO_ROOT / "src/lingtai/init_schema.py"
    for needle in (
        "manifest.llm.thinking is currently supported only for",
        "expected a non-empty exact string",
        "manifest.llm.thinking: expected one of",
    ):
        _assert_exact_citation(paragraph, "init_schema.py", schema_source, needle)
