"""Psyche-owned input and static resident prompt composition."""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING

from lingtai.kernel._frontmatter import strip_frontmatter

if TYPE_CHECKING:
    from .settings import PsychePromptInputs

__all__ = [
    "PROMPT_SECTION_REGISTRY",
    "PromptPlan",
    "PromptSection",
    "PromptSectionDefinition",
    "PsychePromptPlanError",
    "compose_prompt_plan",
]


class PsychePromptPlanError(ValueError):
    """A malformed Psyche prompt registry entry."""


@dataclass(frozen=True, slots=True)
class PromptSectionDefinition:
    """Static metadata for one resident section in the Psyche plan."""

    name: str
    summary: str
    resident_source: str
    disclosure_source: str | None = None
    references: tuple[str, ...] = ()
    protected: bool = True
    raw: bool = False
    mirror: str | None = None


# This is deliberately limited to the first PR's three packaged, static
# contributions. The kernel still owns the prompt manager's global order,
# first-slot rendering, cache batches, and protection mechanics.
PROMPT_SECTION_REGISTRY: tuple[PromptSectionDefinition, ...] = (
    PromptSectionDefinition(
        name="principle",
        summary="The opening map and operating principles for the resident prompt.",
        resident_source="principle/principle.md",
        mirror="system/principle.md",
        raw=True,
    ),
    PromptSectionDefinition(
        name="substrate",
        summary="The stable operating model of LingTai bodies, state, and channels.",
        resident_source="substrate/substrate.md",
        mirror="system/substrate.md",
    ),
    PromptSectionDefinition(
        name="procedures",
        summary="The resident action playbook and tool-use procedures.",
        resident_source="procedures/procedures.md",
        mirror="system/procedures.md",
    ),
)


@dataclass(frozen=True, slots=True)
class PromptSection:
    """One immutable, ready-to-apply resident prompt contribution."""

    name: str
    summary: str
    resident: str
    disclosure: str | None
    references: tuple[str, ...]
    protected: bool
    raw: bool
    mirror: str | None
    mirror_text: str | None
    fallback: str | None = None
    fallback_error: Exception | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PromptPlan:
    """One complete immutable Psyche composition input for a reconstruction."""

    inputs: "PsychePromptInputs"
    sections: tuple[PromptSection, ...]


def _validated_registry() -> tuple[PromptSectionDefinition, ...]:
    definitions = PROMPT_SECTION_REGISTRY
    if not isinstance(definitions, tuple) or not definitions:
        raise PsychePromptPlanError("Psyche prompt registry is unavailable")

    names: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, PromptSectionDefinition):
            raise PsychePromptPlanError("Psyche prompt registry has an invalid entry")
        if (
            not isinstance(definition.name, str)
            or not definition.name
            or definition.name in names
            or not isinstance(definition.summary, str)
            or not definition.summary
            or not isinstance(definition.resident_source, str)
            or not definition.resident_source
            or (
                definition.disclosure_source is not None
                and not isinstance(definition.disclosure_source, str)
            )
            or (
                definition.mirror is not None
                and not isinstance(definition.mirror, str)
            )
            or not isinstance(definition.protected, bool)
            or not isinstance(definition.raw, bool)
        ):
            raise PsychePromptPlanError("Psyche prompt registry has an invalid entry")
        if not isinstance(definition.references, tuple) or not all(
            isinstance(reference, str) and reference for reference in definition.references
        ):
            raise PsychePromptPlanError("Psyche prompt registry has invalid references")
        names.add(definition.name)
    return definitions


def _read_section_source(
    working_dir: Path,
    definition: PromptSectionDefinition,
    resource_root: Traversable | None,
) -> tuple[str, str | None, str | None, Exception | None]:
    """Read one packaged body, preserving the existing mirror fallback."""
    mirror_path = (
        working_dir / definition.mirror if definition.mirror is not None else None
    )

    def capture_fallback() -> tuple[str | None, Exception | None]:
        try:
            if mirror_path is None or not mirror_path.is_file():
                return None, None
            return mirror_path.read_text(encoding="utf-8"), None
        except Exception as exc:
            return None, exc

    try:
        if resource_root is None:
            raise FileNotFoundError(definition.resident_source)
        mirror_text = resource_root.joinpath(definition.resident_source).read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        fallback_text, fallback_error = capture_fallback()
        if fallback_error is not None:
            raise fallback_error
        if fallback_text is None:
            return "", None, None, None
        return strip_frontmatter(fallback_text), None, None, None

    # The original loader only consulted the existing mirror after a packaged
    # read or mirror write failure. Capture it opportunistically now so a later
    # write failure can select the same fallback without a mid-transaction read;
    # an unreadable fallback remains non-fatal when the packaged write succeeds.
    fallback_text, fallback_error = capture_fallback()
    fallback = (
        strip_frontmatter(fallback_text) if fallback_text is not None else None
    )
    return strip_frontmatter(mirror_text), mirror_text, fallback, fallback_error


def compose_prompt_plan(working_dir: str | Path) -> PromptPlan:
    """Resolve Psyche inputs and static sections into one immutable plan.

    The owner document is read exactly once. Static bodies are read into this
    candidate exactly once as well; applying mirrors and prompt-manager state is
    deliberately left to the Agent reconstruction transaction.
    """
    from .settings import read_resolved_prompt_inputs

    inputs = read_resolved_prompt_inputs(working_dir)
    definitions = _validated_registry()

    from importlib.resources import files

    try:
        resource_root: Traversable | None = files("lingtai.prompts")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        resource_root = None

    root = Path(working_dir)
    sections = tuple(
        PromptSection(
            name=definition.name,
            summary=definition.summary,
            resident=resident,
            disclosure=definition.disclosure_source,
            references=definition.references,
            protected=definition.protected,
            raw=definition.raw,
            mirror=definition.mirror,
            mirror_text=mirror_text,
            fallback=fallback,
            fallback_error=fallback_error,
        )
        for definition in definitions
        for resident, mirror_text, fallback, fallback_error in (
            _read_section_source(root, definition, resource_root),
        )
    )
    return PromptPlan(inputs=inputs, sections=sections)


if len({definition.name for definition in PROMPT_SECTION_REGISTRY}) != len(
    PROMPT_SECTION_REGISTRY
):
    raise RuntimeError("Psyche prompt registry contains duplicate section names")
