"""Reasoning construction policy for the registered official Codex route."""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

from lingtai.kernel.llm.reasoning import (
    ReasoningConstructionResult,
    ReasoningRouteContext,
)


CODEX_OFFICIAL_ENDPOINT = "https://chatgpt.com/backend-api/codex"
CODEX_REASONING_PROVIDERS = frozenset({"codex", "codex-pool", "codex_pool"})

_CLI_CAPABILITY_SOURCE = "codex_cli_0_144_1_model_metadata"
_OPENAI_CAPABILITY_SOURCE = "openai_gpt_5_3_codex_model_docs"


@dataclass(frozen=True)
class _CodexModelDescriptor:
    allowed: tuple[str, ...]
    capability_source: str


_CODEX_MODELS: dict[str, _CodexModelDescriptor] = {
    "gpt-5.6-sol": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh", "max", "ultra"),
        _CLI_CAPABILITY_SOURCE,
    ),
    "gpt-5.6-sol-wm": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh", "max", "ultra"),
        _CLI_CAPABILITY_SOURCE,
    ),
    "gpt-5.6-terra": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh", "max", "ultra"),
        _CLI_CAPABILITY_SOURCE,
    ),
    "gpt-5.6-luna": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh", "max"),
        _CLI_CAPABILITY_SOURCE,
    ),
    "gpt-5.3-codex-spark": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh"),
        _CLI_CAPABILITY_SOURCE,
    ),
    "codex-auto-review": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh", "max"),
        _CLI_CAPABILITY_SOURCE,
    ),
    "gpt-5.3-codex": _CodexModelDescriptor(
        ("low", "medium", "high", "xhigh"),
        _OPENAI_CAPABILITY_SOURCE,
    ),
}


@dataclass(frozen=True)
class CodexReasoningPatch:
    """Immutable Codex Responses ``reasoning.effort`` patch."""

    effort: str

    def apply(self, request: MutableMapping[str, Any]) -> None:
        request["reasoning"] = {"effort": self.effort}


@dataclass(frozen=True)
class CodexReasoningContract:
    """Exact capability and LingTai omission baseline for official Codex."""

    def construct(
        self,
        route: ReasoningRouteContext,
        requested: object,
    ) -> ReasoningConstructionResult | None:
        if route.key.provider not in CODEX_REASONING_PROVIDERS:
            return None
        if route.key.wire != "responses":
            return None
        if route.endpoint not in {CODEX_OFFICIAL_ENDPOINT, f"{CODEX_OFFICIAL_ENDPOINT}/"}:
            return None
        descriptor = _CODEX_MODELS.get(route.model)
        if descriptor is None:
            return None

        if requested == "default":
            captured_requested = None
            normalized = "xhigh"
            source = "lingtai_codex_default"
        else:
            if not isinstance(requested, str) or requested not in descriptor.allowed:
                allowed = ", ".join(descriptor.allowed)
                raise ValueError(
                    f"Codex reasoning for model {route.model!r} must be one of: {allowed}"
                )
            captured_requested = requested
            normalized = requested
            source = "explicit_config"

        return ReasoningConstructionResult(
            requested=captured_requested,
            normalized=normalized,
            actual=normalized,
            source=source,
            capability_source=descriptor.capability_source,
            wire_patch=CodexReasoningPatch(normalized),
        )
