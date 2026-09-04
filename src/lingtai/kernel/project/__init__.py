"""Core policy for creating one fresh local Project seed."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectError:
    code: str
    message: str


class ProjectCreationError(Exception):
    def __init__(self, error: ProjectError) -> None:
        self.error = error
        super().__init__(error.message)


@dataclass(frozen=True, slots=True)
class ProjectCreateRequest:
    agent_name: str
    preset_ref: str
    llm: dict[str, object]
    capabilities: dict[str, object]
    psyche_settings_json: str


@dataclass(frozen=True, slots=True)
class ProjectSeed:
    agent_name: str
    preset_ref: str
    human_manifest_json: str
    agent_manifest_json: str
    init_json: str
    psyche_settings_json: str


@dataclass(frozen=True, slots=True)
class ProjectCreationResult:
    agent_name: str
    preset_ref: str

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "created",
            "agent_name": self.agent_name,
            "preset_ref": self.preset_ref,
        }


class ProjectWorkspacePort(ABC):
    """The one filesystem boundary required by fresh Project creation."""

    @abstractmethod
    def create(self, seed: ProjectSeed) -> None:
        """Publish one readable seed or raise a stable ProjectCreationError."""


def _error(code: str, message: str) -> ProjectCreationError:
    return ProjectCreationError(ProjectError(code, message))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _validate(request: ProjectCreateRequest) -> None:
    name = request.agent_name
    if not isinstance(name, str) or not name or name in {".", "..", "human"}:
        raise _error("invalid_agent_name", "agent name must be a new non-reserved segment")
    if name != name.strip() or "/" in name or "\\" in name or "\x00" in name:
        raise _error("invalid_agent_name", "agent name must be one safe segment")
    if (
        not isinstance(request.psyche_settings_json, str)
        or not request.psyche_settings_json
    ):
        raise _error(
            "invalid_covenant",
            "Psyche owner content must be supplied by the caller",
        )
    if not isinstance(request.preset_ref, str) or not request.preset_ref:
        raise _error("invalid_preset", "preset reference must be supplied")
    if not isinstance(request.llm, dict) or not isinstance(request.capabilities, dict):
        raise _error("invalid_preset", "preset must provide LLM and capabilities objects")


def _seed(request: ProjectCreateRequest) -> ProjectSeed:
    manifest: dict[str, object] = {
        "agent_name": request.agent_name,
        "llm": dict(request.llm),
        "capabilities": dict(request.capabilities),
        "preset": {
            "active": request.preset_ref,
            "default": request.preset_ref,
            "allowed": [request.preset_ref],
        },
        "pseudo_agent_subscriptions": ["../human"],
    }
    return ProjectSeed(
        agent_name=request.agent_name,
        preset_ref=request.preset_ref,
        human_manifest_json=_json({"agent_name": "human", "address": "human", "admin": None}),
        agent_manifest_json=_json({
            "agent_name": request.agent_name,
            "address": request.agent_name,
            "admin": {},
        }),
        init_json=_json({"manifest": manifest, "pad": ""}),
        psyche_settings_json=request.psyche_settings_json,
    )


class ProjectCreationUseCase:
    def __init__(self, workspace: ProjectWorkspacePort) -> None:
        self._workspace = workspace

    def create(self, request: ProjectCreateRequest) -> ProjectCreationResult:
        _validate(request)
        seed = _seed(request)
        self._workspace.create(seed)
        return ProjectCreationResult(seed.agent_name, seed.preset_ref)


__all__ = [
    "ProjectCreateRequest",
    "ProjectCreationError",
    "ProjectCreationResult",
    "ProjectCreationUseCase",
    "ProjectError",
    "ProjectSeed",
    "ProjectWorkspacePort",
]
