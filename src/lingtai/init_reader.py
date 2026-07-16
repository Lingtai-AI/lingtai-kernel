"""The one real ``init.json`` reader used by boot and refresh.

The reader is deliberately factual and read-only over the user-owned file.  It
parses, materializes, prepares, validates, resolves, and returns one structured
outcome.  Compatibility is an in-memory interpretation, never a write-back.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .init_schema import DEPRECATED_TOP_FIELDS, LEGACY_MIGRATED_TOP_FIELDS, validate_init
from .kernel.config_resolve import parse_jsonc, resolve_paths
from .kernel.workdir import _redact_secrets
from .tools.registry import (
    CapabilityShapeDecision,
    classify_capabilities,
)


class InitReadStatus(str, Enum):
    """Structured facts about the real reader's result."""

    FULLY_EFFECTIVE = "FULLY_EFFECTIVE"
    READ_OK_WITH_IGNORED_FIELDS = "READ_OK_WITH_IGNORED_FIELDS"
    READ_FAILED = "READ_FAILED"


class InitShapeDecision(str, Enum):
    """Typed action axis for canonical/compatibility configuration shape."""

    PASS = "PASS"
    NUDGE = "NUDGE"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class InitReadOutcome:
    """Redaction-safe reader report shared by boot, refresh, and Nudge."""

    status: InitReadStatus
    file: str
    data: dict[str, Any] | None = None
    stage: str | None = None
    line: int | None = None
    column: int | None = None
    json_path: str | None = None
    safe_excerpt: str | None = None
    ignored_paths: list[str] = field(default_factory=list)
    compatibility_paths: list[dict[str, str]] = field(default_factory=list)
    conflict_paths: list[str] = field(default_factory=list)
    shape_decision: InitShapeDecision = InitShapeDecision.UNKNOWN
    warnings: list[str] = field(default_factory=list)
    behavior: str = "STOP"
    fallback_effective: str | None = None
    next_step: str | None = None
    effective_config_source: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not InitReadStatus.READ_FAILED

    @property
    def finding_decision(self) -> InitShapeDecision:
        """Combine shape and ignored-field facts without conflating their axes."""
        if self.shape_decision is InitShapeDecision.BLOCKED:
            return InitShapeDecision.BLOCKED
        if self.shape_decision is InitShapeDecision.UNKNOWN:
            return InitShapeDecision.UNKNOWN
        if self.shape_decision is InitShapeDecision.NUDGE or self.ignored_paths:
            return InitShapeDecision.NUDGE
        return InitShapeDecision.PASS

    @property
    def redacted_effective_config(self) -> dict[str, Any] | None:
        """Return the reader's current in-memory effective data, redacted."""
        if self.data is None:
            return None
        try:
            value = _redact_secrets(self.data)
        except (TypeError, ValueError):
            return {"redacted": True, "unavailable": "effective config was not serializable"}
        return value if isinstance(value, dict) else None

    def to_payload(self) -> dict[str, Any]:
        """Serialize the complete safe outcome for logs, events, and Nudge."""
        payload: dict[str, Any] = {
            "read_result": self.status.value,
            "file": self.file,
            "reader_status": self.status.value,
            "shape_decision": self.shape_decision.value,
            "finding_decision": self.finding_decision.value,
            "failure_stage": self.stage,
            "ignored_paths": list(self.ignored_paths),
            "compatibility_paths": list(self.compatibility_paths),
            "conflict_paths": list(self.conflict_paths),
            "behavior": self.behavior,
            "effective_config": {
                "source": self.effective_config_source,
                "redacted": True,
                "data": self.redacted_effective_config,
            },
        }
        for key in ("line", "column", "json_path", "safe_excerpt", "fallback_effective", "next_step"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload

    def log_fields(self) -> dict[str, Any]:
        """Return the same redaction-safe payload used by all consumers."""
        return self.to_payload()


Materialize = Callable[[dict[str, Any]], None]
Prepare = Callable[[dict[str, Any]], None]


def reader_callbacks(
    working_dir: str | Path,
    *,
    load_preset: Callable[..., dict[str, Any]],
) -> tuple[Materialize, Prepare]:
    """Build the one boot/refresh callback composition.

    CLI boot and Agent refresh call this helper, so materialization and provider
    inheritance cannot silently diverge between their first reads.
    """
    from .presets import expand_inherit, materialize_active_preset
    from .tools.registry import CORE_DEFAULTS

    root = Path(working_dir)

    def materialize(data: dict[str, Any]) -> None:
        materialize_active_preset(
            data,
            root,
            core_defaults=CORE_DEFAULTS,
            load_preset=load_preset,
        )

    def prepare(data: dict[str, Any]) -> None:
        manifest = data.get("manifest")
        if not isinstance(manifest, dict):
            return
        llm = manifest.get("llm") or {}
        capabilities = manifest.get("capabilities") or {}
        if isinstance(capabilities, dict):
            expand_inherit(capabilities, llm)

    return materialize, prepare


def read_init(
    working_dir: str | Path,
    *,
    materialize: Materialize | None = None,
    prepare: Prepare | None = None,
    failure_behavior: str = "STOP",
) -> InitReadOutcome:
    """Read local ``init.json`` through parse/materialize/prepare/validate/resolve."""
    path = Path(working_dir) / "init.json"
    display_path = str(path)
    effective_source = str(Path(working_dir) / "system" / "manifest.resolved.json")
    if not path.is_file():
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            stage="FILE_READ",
            shape_decision=InitShapeDecision.UNKNOWN,
            behavior=failure_behavior,
            next_step="Create init.json from the kernel canonical init.jsonc example.",
            effective_config_source=effective_source,
        )

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            stage="FILE_READ",
            shape_decision=InitShapeDecision.UNKNOWN,
            safe_excerpt=f"{type(exc).__name__}: unable to read file (content omitted)",
            behavior=failure_behavior,
            next_step="Restore readable UTF-8 access to init.json, then rerun the same check.",
            effective_config_source=effective_source,
        )

    try:
        data = parse_jsonc(raw)
    except json.JSONDecodeError as exc:
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            stage="JSON_PARSE",
            line=exc.lineno,
            column=exc.colno,
            json_path="$",
            safe_excerpt=_safe_excerpt(raw, exc.pos),
            shape_decision=InitShapeDecision.UNKNOWN,
            behavior=failure_behavior,
            next_step="Repair JSON/JSONC syntax, then rerun the same reader.",
            effective_config_source=effective_source,
        )
    except (TypeError, ValueError) as exc:
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            stage="JSON_PARSE",
            shape_decision=InitShapeDecision.UNKNOWN,
            safe_excerpt=f"{type(exc).__name__}: malformed JSON/JSONC input (content omitted)",
            behavior=failure_behavior,
            next_step="Repair JSON/JSONC syntax, then rerun the same reader.",
            effective_config_source=effective_source,
        )

    if not isinstance(data, dict):
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            stage="JSON_PARSE",
            json_path="$",
            safe_excerpt="top-level JSON value is not an object (content omitted)",
            shape_decision=InitShapeDecision.UNKNOWN,
            behavior=failure_behavior,
            next_step="Make the top-level init.json value an object, then rerun the same reader.",
            effective_config_source=effective_source,
        )

    # This is the only canonical/legacy evaluator. It is also called by the
    # capability registry, so a downstream setup cannot silently choose a side.
    shape = InitShapeDecision.PASS
    compatibility_paths: list[dict[str, str]] = []
    conflict_paths: list[str] = []
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            data=data,
            stage="VALIDATE",
            json_path="manifest",
            safe_excerpt="manifest is missing or not an object (content omitted)",
            shape_decision=InitShapeDecision.UNKNOWN,
            behavior=failure_behavior,
            next_step="Repair the manifest object, then rerun the same reader.",
            effective_config_source=effective_source,
        )
    if "capabilities" in manifest:
        capabilities = manifest.get("capabilities")
        normalized, evidence = classify_capabilities(capabilities)
        shape = InitShapeDecision(evidence.decision.value)
        compatibility_paths = [dict(item) for item in evidence.compatibility_paths]
        conflict_paths = list(evidence.conflict_paths)
        if shape is InitShapeDecision.BLOCKED:
            return InitReadOutcome(
                InitReadStatus.READ_FAILED,
                display_path,
                data=data,
                stage="CONFLICT",
                json_path="manifest.capabilities.shell",
                safe_excerpt=(
                    "manifest.capabilities.bash and manifest.capabilities.shell "
                    "contain different values (content omitted)"
                ),
                compatibility_paths=compatibility_paths,
                conflict_paths=conflict_paths,
                shape_decision=InitShapeDecision.BLOCKED,
                behavior=failure_behavior,
                next_step=(
                    "Have the Agent explicitly keep one canonical "
                    "manifest.capabilities.shell value, remove bash, then rerun the same reader."
                ),
                effective_config_source=effective_source,
            )
        if shape is InitShapeDecision.UNKNOWN:
            return InitReadOutcome(
                InitReadStatus.READ_FAILED,
                display_path,
                data=data,
                stage="CAPABILITY_SHAPE",
                json_path="manifest.capabilities",
                safe_excerpt="manifest.capabilities is not a classifiable object (content omitted)",
                compatibility_paths=compatibility_paths,
                shape_decision=InitShapeDecision.UNKNOWN,
                behavior=failure_behavior,
                next_step="Repair manifest.capabilities to an object, then rerun the same reader.",
                effective_config_source=effective_source,
            )
        manifest["capabilities"] = normalized

    try:
        if materialize is not None:
            materialize(data)
    except Exception as exc:
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            data=data,
            stage="PRESET",
            compatibility_paths=compatibility_paths,
            shape_decision=shape,
            safe_excerpt=f"{type(exc).__name__}: preset materialization failed (details omitted)",
            behavior=failure_behavior,
            next_step="Repair the active preset reference/content, then rerun the same reader.",
            effective_config_source=effective_source,
        )

    # Preset materialization is part of the same effective reader. Re-run the
    # shared evaluator on its in-memory result so a preset cannot introduce a
    # conflict that downstream setup would otherwise discover later.
    manifest = data.get("manifest")
    if isinstance(manifest, dict) and "capabilities" in manifest:
        normalized, effective_evidence = classify_capabilities(manifest.get("capabilities"))
        effective_shape = InitShapeDecision(effective_evidence.decision.value)
        for item in effective_evidence.compatibility_paths:
            mapping = dict(item)
            if mapping not in compatibility_paths:
                compatibility_paths.append(mapping)
        conflict_paths = list(effective_evidence.conflict_paths)
        if effective_shape is InitShapeDecision.BLOCKED:
            return InitReadOutcome(
                InitReadStatus.READ_FAILED,
                display_path,
                data=data,
                stage="CONFLICT",
                json_path="manifest.capabilities.shell",
                safe_excerpt=(
                    "manifest.capabilities.bash and manifest.capabilities.shell "
                    "contain different values (content omitted)"
                ),
                compatibility_paths=compatibility_paths,
                conflict_paths=conflict_paths,
                shape_decision=InitShapeDecision.BLOCKED,
                behavior=failure_behavior,
                next_step=(
                    "Have the Agent explicitly keep one canonical "
                    "manifest.capabilities.shell value, remove bash, then rerun the same reader."
                ),
                effective_config_source=effective_source,
            )
        if effective_shape is InitShapeDecision.UNKNOWN:
            return InitReadOutcome(
                InitReadStatus.READ_FAILED,
                display_path,
                data=data,
                stage="CAPABILITY_SHAPE",
                json_path="manifest.capabilities",
                safe_excerpt="manifest.capabilities is not a classifiable object (content omitted)",
                compatibility_paths=compatibility_paths,
                shape_decision=InitShapeDecision.UNKNOWN,
                behavior=failure_behavior,
                next_step="Repair manifest.capabilities to an object, then rerun the same reader.",
                effective_config_source=effective_source,
            )
        manifest["capabilities"] = normalized
        if effective_shape is InitShapeDecision.NUDGE:
            shape = InitShapeDecision.NUDGE

    try:
        if prepare is not None:
            prepare(data)
        warnings = validate_init(data)
    except Exception as exc:
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            data=data,
            stage="VALIDATE",
            json_path=_path_from_error(str(exc)),
            compatibility_paths=compatibility_paths,
            shape_decision=shape,
            safe_excerpt=_safe_error(str(exc)),
            behavior=failure_behavior,
            next_step="Repair the reported schema/path conflict, then rerun the same reader.",
            effective_config_source=effective_source,
        )

    try:
        resolve_paths(data, working_dir)
    except Exception as exc:
        return InitReadOutcome(
            InitReadStatus.READ_FAILED,
            display_path,
            data=data,
            stage="RESOLVE",
            compatibility_paths=compatibility_paths,
            shape_decision=shape,
            safe_excerpt=f"{type(exc).__name__}: path resolution failed (details omitted)",
            behavior=failure_behavior,
            next_step="Repair the path value, then rerun the same reader.",
            effective_config_source=effective_source,
        )

    ignored = _ignored_paths(data, warnings)
    status = InitReadStatus.READ_OK_WITH_IGNORED_FIELDS if ignored else InitReadStatus.FULLY_EFFECTIVE
    return InitReadOutcome(
        status,
        display_path,
        data=data,
        stage="RESOLVE",
        ignored_paths=ignored,
        compatibility_paths=compatibility_paths,
        shape_decision=shape,
        warnings=list(warnings),
        behavior="CONTINUE",
        next_step=(
            "Have the Agent explicitly repair or replace the listed raw paths, then rerun the same reader; "
            "the reader will not modify init.json."
            if ignored or shape is InitShapeDecision.NUDGE
            else None
        ),
        effective_config_source=effective_source,
    )


def _ignored_paths(data: dict[str, Any], warnings: list[str]) -> list[str]:
    """Enumerate raw paths validation accepts but the real reader ignores."""
    paths: list[str] = []
    for key in sorted(DEPRECATED_TOP_FIELDS | LEGACY_MIGRATED_TOP_FIELDS):
        if key in data:
            paths.append(key)
    manifest = data.get("manifest")
    if isinstance(manifest, dict):
        for key in ("molt_notice", "molt_pressure", "molt_urgency", "molt_prompt", "stamina"):
            if key in manifest:
                paths.append(f"manifest.{key}")
    for warning in warnings:
        if warning.startswith("unknown top-level field: "):
            paths.append(warning.removeprefix("unknown top-level field: "))
        elif warning.startswith("unknown field: "):
            paths.append(warning.removeprefix("unknown field: "))
        elif warning.startswith("unknown field in "):
            paths.append(warning.removeprefix("unknown field in ").split(":", 1)[0])
    return list(dict.fromkeys(paths))


def _path_from_error(message: str) -> str | None:
    match = re.search(r"(?:field|key):?\s*([A-Za-z0-9_.\[\]-]+)", message)
    return match.group(1) if match else None


def _safe_error(message: str) -> str:
    return re.sub(r"(['\"])(?:[^'\"]*)\1", "<redacted>", message)[:300]


def _safe_excerpt(raw: str, position: int) -> str:
    start = max(0, position - 80)
    end = min(len(raw), position + 80)
    excerpt = raw[start:end].replace("\n", "\\n")
    excerpt = re.sub(
        r"(?i)((?:api[_-]?key|token|secret|password|credential)[^:=]{0,20}[:=]\s*)([^,;}]*)",
        r"\1<redacted>",
        excerpt,
    )
    return excerpt[:300]


__all__ = [
    "InitReadOutcome",
    "InitReadStatus",
    "InitShapeDecision",
    "read_init",
    "reader_callbacks",
]
