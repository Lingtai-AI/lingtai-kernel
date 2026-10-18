"""Hidden, prerequisite-gated fresh Project creation; no runtime launch."""
from __future__ import annotations

from abc import ABC, abstractmethod
import copy
from dataclasses import dataclass
from enum import Enum

from . import ProjectCreationError, ProjectError
from .inspection import ProjectMemberObservation, ProjectMemberRole


class ProcessEvidence(str, Enum):
    CLEAN = "clean"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InteractiveOptions:
    language: str
    covenant: str
    karma: bool = True
    nirvana: bool = False
    pad: str = ""
    base_prompt: str = ""
    env_file: str = ""
    venv_path: str = ""
    addons: tuple[str, ...] = ()
    allowed_presets: tuple[str, ...] = ()
    # Already-selected activation specifications, not a credential channel.
    mcp: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class InteractiveCreateRequest:
    project_dir: str
    approved_root: str
    agent_name: str
    directory_name: str
    preset_ref: str
    preset_manifest: dict[str, object]
    options: InteractiveOptions
    recipe: str
    process_evidence: ProcessEvidence
    location: str = "unknown"
    commands: str = ""


@dataclass(frozen=True, slots=True)
class CreationAttempt:
    attempt_id: str


@dataclass(frozen=True, slots=True)
class RecipeSelection:
    """Resource handle: opaque to Core, resolved only by the resource adapter."""
    source: str
    recipe_id: str
    library_name: str | None


@dataclass(frozen=True, slots=True)
class RecipeLayers:
    comment: str = ""
    covenant_file: str | None = None
    greet: str | None = None
    library_path: str | None = None


@dataclass(frozen=True, slots=True)
class InteractiveCreationResult:
    attempt_id: str
    agent_name: str
    directory_name: str
    preset_ref: str
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "status": "created", "committed": True,
            "attempt_id": self.attempt_id, "agent_name": self.agent_name,
            "directory_name": self.directory_name, "preset_ref": self.preset_ref,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class InteractiveProjectSeed:
    directory_name: str
    human_manifest: dict[str, object]
    agent_manifest: dict[str, object]
    init_document: dict[str, object]
    base_prompt: str
    covenant: str | None
    covenant_file: str | None
    greet: str | None
    language: str
    location: str
    commands: str
    pad_seed: str = ""


def make_interactive_seed(request: InteractiveCreateRequest, layers: RecipeLayers) -> InteractiveProjectSeed:
    from .configuration import _initial_agent_tools

    options = request.options
    tools = {**_initial_agent_tools(), "email": {"pseudo_agent_subscriptions": ["../human"]}}
    if layers.library_path and layers.library_path not in tools["skills"]["paths"]:
        tools["skills"]["paths"].append(layers.library_path)
    admin = {"karma": options.karma, "nirvana": options.nirvana}
    document = {"agent": {"name": request.agent_name, "language": options.language, "permissions": admin},
                "llm": {"preset": {"active": request.preset_ref, "default": request.preset_ref,
                        "allowed": list(dict.fromkeys((*options.allowed_presets, request.preset_ref)))}},
                "tools": tools, "plugins": [], "mcp": copy.deepcopy(options.mcp or {})}
    host = {key: getattr(options, key) for key in ("env_file", "venv_path") if getattr(options, key)}
    if host:
        document["host"] = host
    return InteractiveProjectSeed(
        request.directory_name, {"agent_name": "human", "address": "human", "admin": None},
        {"agent_name": request.agent_name, "address": request.directory_name, "admin": admin},
        document, options.base_prompt, None if layers.covenant_file else options.covenant,
        layers.covenant_file, layers.greet, options.language, request.location, request.commands, options.pad or layers.comment,
    )


class InteractiveWorkspacePort(ABC):
    @abstractmethod
    def preflight(self, request: InteractiveCreateRequest) -> None: ...

    @abstractmethod
    def begin(self) -> CreationAttempt: ...

    @abstractmethod
    def build(self, attempt: CreationAttempt, seed: InteractiveProjectSeed) -> None: ...

    @abstractmethod
    def validate(self, attempt: CreationAttempt, request: InteractiveCreateRequest,
                 *, persisted: bool) -> None: ...

    @abstractmethod
    def publish(self, attempt: CreationAttempt) -> tuple[str, ...]:
        """Exclusive publication. Return only after known commit; never raise after commit."""

    @abstractmethod
    def discard_owned(self, attempt: CreationAttempt) -> bool:
        """Remove only proven-owned staging; false preserves uncertain/failed cleanup."""


class CreationRecipePort(ABC):
    @abstractmethod
    def observe_local(self) -> RecipeSelection | None: ...

    @abstractmethod
    def packaged_default(self) -> RecipeSelection: ...

    @abstractmethod
    def materialize(self, selection: RecipeSelection, attempt: CreationAttempt,
                    language: str) -> RecipeLayers: ...


class CreationPrerequisitePort(ABC):
    @abstractmethod
    def persist_and_verify(self, attempt: CreationAttempt, preset_ref: str) -> bool:
        """Outer credential/preset owner, called only after valid staging; no secrets returned."""


def creation_error(code: str) -> ProjectCreationError:
    # No arbitrary exception/request text crosses the result/log boundary.
    return ProjectCreationError(ProjectError(code, "interactive Project creation could not complete"))


def validate_request(request: InteractiveCreateRequest) -> None:
    name = request.directory_name
    if (not name or name in {".", "..", "human"} or name.startswith(".")
            or name != name.strip() or any(c in name for c in "/\\\x00")):
        raise creation_error("invalid_project_request")
    if not request.agent_name.strip() or not request.preset_ref:
        raise creation_error("invalid_project_request")
    if request.recipe not in {"", "adaptive"}:
        raise creation_error("invalid_project_request")
    options = request.options
    if (type(options.karma) is not bool or type(options.nirvana) is not bool
            or not options.covenant or not options.language or not (options.karma or options.nirvana)
            or any(c in options.language for c in "/\\\x00") or options.language in {".", ".."}):
        raise creation_error("invalid_project_request")
    if request.process_evidence is not ProcessEvidence.CLEAN:
        raise creation_error("project_process_state_unresolved")


def validate_staged_members(members: tuple[ProjectMemberObservation, ...],
                            directory_name: str) -> None:
    expected = {("human", ProjectMemberRole.HUMAN),
                (directory_name, ProjectMemberRole.ORCHESTRATOR)}
    if len(members) != 2 or {(member.directory_name, member.role) for member in members} != expected:
        raise creation_error("invalid_staged_membership")


class InteractiveCreationUseCase:
    def __init__(self, workspace: InteractiveWorkspacePort, recipes: CreationRecipePort,
                 prerequisites: CreationPrerequisitePort) -> None:
        self.workspace = workspace
        self.recipes = recipes
        self.prerequisites = prerequisites

    def create(self, request: InteractiveCreateRequest) -> InteractiveCreationResult:
        validate_request(request)
        self.workspace.preflight(request)
        attempt = self.workspace.begin()
        committed = False
        phase = "apply_recipe"
        try:
            layers = RecipeLayers()
            if request.recipe:
                selection = self.recipes.observe_local()
                if selection is None:
                    selection = self.recipes.packaged_default()
                layers = self.recipes.materialize(selection, attempt, request.options.language)
            phase = "build"
            self.workspace.build(attempt, make_interactive_seed(request, layers))
            phase = "validate_staging"
            self.workspace.validate(attempt, request, persisted=False)
            phase = "persist_prerequisites"
            if not self.prerequisites.persist_and_verify(attempt, request.preset_ref):
                raise creation_error("prerequisites_rejected")
            phase = "verify_prerequisites"
            self.workspace.validate(attempt, request, persisted=True)
            phase = "publish"
            warnings = self.workspace.publish(attempt)
            committed = True
            return InteractiveCreationResult(attempt.attempt_id, request.agent_name,
                                             request.directory_name, request.preset_ref, warnings)
        except ProjectCreationError:
            raise
        except Exception:
            raise creation_error(phase + "_failed") from None
        finally:
            if not committed and not self.workspace.discard_owned(attempt):
                # Never pretend a failed/unproved cleanup left no staging.
                raise creation_error("owned_staging_cleanup_unresolved") from None
