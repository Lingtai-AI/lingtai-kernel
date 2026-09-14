"""Core policy for creating and mechanically inspecting local Projects."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class ProjectError:
    code: str
    message: str


class ProjectCreationError(Exception):
    def __init__(self, error: ProjectError) -> None:
        self.error = error
        super().__init__(error.message)


class ProjectInspectionError(Exception):
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


class ProjectState(str, Enum):
    ABSENT = "absent"
    EMPTY = "empty"
    POPULATED = "populated"


@dataclass(frozen=True, slots=True)
class ProjectInspectionObservation:
    lingtai_root_present: bool
    agent_candidates: int
    agents_with_init: int
    agents_without_init: int


@dataclass(frozen=True, slots=True)
class ProjectInspectionResult:
    state: ProjectState
    agent_candidates: int
    agents_with_init: int
    agents_without_init: int

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "inspected",
            "state": self.state.value,
            "agent_candidates": {
                "total": self.agent_candidates,
                "with_init": self.agents_with_init,
                "without_init": self.agents_without_init,
            },
        }


class ProjectWorkspacePort(ABC):
    """The one filesystem boundary required by fresh Project creation."""

    @abstractmethod
    def create(self, seed: ProjectSeed) -> None:
        """Publish one readable seed or raise a stable ProjectCreationError."""


class ProjectInspectionPort(ABC):
    """Read the one-level filesystem facts required by Project inspection."""

    @abstractmethod
    def inspect(self) -> ProjectInspectionObservation:
        """Return one mutation-free observation or raise ProjectInspectionError."""


def _error(code: str, message: str) -> ProjectCreationError:
    return ProjectCreationError(ProjectError(code, message))


def _inspection_error(code: str, message: str) -> ProjectInspectionError:
    return ProjectInspectionError(ProjectError(code, message))


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


def _validate_observation(observation: ProjectInspectionObservation) -> None:
    if not isinstance(observation.lingtai_root_present, bool):
        raise _inspection_error(
            "invalid_project_observation",
            "project inspection returned an incoherent presence fact",
        )
    counts = (
        observation.agent_candidates,
        observation.agents_with_init,
        observation.agents_without_init,
    )
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
        raise _inspection_error(
            "invalid_project_observation",
            "project inspection returned incoherent counts",
        )
    if (
        observation.agents_with_init + observation.agents_without_init
        != observation.agent_candidates
    ):
        raise _inspection_error(
            "invalid_project_observation",
            "project inspection returned incoherent counts",
        )
    if not observation.lingtai_root_present and observation.agent_candidates:
        raise _inspection_error(
            "invalid_project_observation",
            "absent project inspection cannot contain agent candidates",
        )


class ProjectCreationUseCase:
    def __init__(self, workspace: ProjectWorkspacePort) -> None:
        self._workspace = workspace

    def create(self, request: ProjectCreateRequest) -> ProjectCreationResult:
        _validate(request)
        seed = _seed(request)
        self._workspace.create(seed)
        return ProjectCreationResult(seed.agent_name, seed.preset_ref)


class ProjectInspectionUseCase:
    def __init__(self, inspector: ProjectInspectionPort) -> None:
        self._inspector = inspector

    def inspect(self) -> ProjectInspectionResult:
        observation = self._inspector.inspect()
        _validate_observation(observation)
        if not observation.lingtai_root_present:
            state = ProjectState.ABSENT
        elif observation.agent_candidates == 0:
            state = ProjectState.EMPTY
        else:
            state = ProjectState.POPULATED
        return ProjectInspectionResult(
            state=state,
            agent_candidates=observation.agent_candidates,
            agents_with_init=observation.agents_with_init,
            agents_without_init=observation.agents_without_init,
        )


__all__ = [
    "ProjectCreateRequest",
    "ProjectCreationError",
    "ProjectCreationResult",
    "ProjectCreationUseCase",
    "ProjectError",
    "ProjectInspectionError",
    "ProjectInspectionObservation",
    "ProjectInspectionPort",
    "ProjectInspectionResult",
    "ProjectInspectionUseCase",
    "ProjectSeed",
    "ProjectState",
    "ProjectWorkspacePort",
]
