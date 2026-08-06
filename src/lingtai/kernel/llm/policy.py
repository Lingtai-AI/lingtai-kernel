"""Construction-time reasoning policy and exact wire-emission evidence."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True, slots=True)
class ReasoningPolicy:
    """Immutable reasoning control captured when a chat session is built."""

    effort: str
    mode: None = None
    keep: None = None

    def __eq__(self, other: Any) -> bool:
        """Keep legacy scalar equality useful for old construction assertions."""
        if isinstance(other, ReasoningPolicy):
            return (
                self.effort == other.effort
                and self.mode is other.mode
                and self.keep is other.keep
            )
        if isinstance(other, str):
            return self.effort == other
        return NotImplemented


# Dataclasses generate a field-based hash for frozen records after processing
# the class body. Scalar compatibility equality makes that hash invalid, so
# explicitly remove hashing after decoration.
ReasoningPolicy.__hash__ = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class ReasoningEmission:
    """Exact construction evidence for one provider-owned reasoning mapper.

    The base ``requested``/``emitted``/``omitted`` shape is the historical
    event contract; ``dropped``, ``coupled``, ``route``, ``source``, and
    ``phase`` are additive expansion fields serialized only when set, so
    records from mappers that do not populate them keep the exact old shape.
    """

    requested: str
    emitted: tuple[str, str] | None = None
    omitted: bool = False
    dropped: bool = False
    coupled: tuple[tuple[str, str], ...] = ()
    route: str | None = None
    source: str | None = None
    phase: str | None = None

    def __post_init__(self) -> None:
        # Runtime-freeze the evidence fields first: annotations alone do not
        # prevent caller-owned lists from drifting after construction.
        if self.emitted is not None:
            emitted = self.emitted
            if (
                isinstance(emitted, str)
                or not isinstance(emitted, (list, tuple))
                or len(emitted) != 2
                or not all(isinstance(part, str) for part in emitted)
            ):
                raise ValueError(
                    "reasoning emission 'emitted' must be a (path, value) string pair"
                )
            object.__setattr__(self, "emitted", (emitted[0], emitted[1]))
        object.__setattr__(
            self, "coupled", _freeze_evidence_pairs(self.coupled, field_name="coupled")
        )
        if self.omitted == (self.emitted is not None):
            raise ValueError("reasoning emission must be emitted or omitted")
        if self.dropped and not self.omitted:
            raise ValueError("a dropped emission cannot also emit a field")

    def as_dict(self) -> dict[str, object]:
        """Return the additive event shape, with no provider claims."""
        payload: dict[str, object] = {"requested": self.requested}
        if self.omitted:
            payload["omitted"] = True
            if self.dropped:
                payload["dropped"] = True
        else:
            path, value = self.emitted
            payload["emitted"] = {"path": path, "value": value}
        if self.coupled:
            payload["coupled"] = [
                {"path": path, "value": value} for path, value in self.coupled
            ]
        if self.route is not None:
            payload["route"] = self.route
        if self.source is not None:
            payload["source"] = self.source
        if self.phase is not None:
            payload["phase"] = self.phase
        return payload


_DISPOSITIONS = ("emitted", "omitted", "dropped", "unsupported")
_UNRESOLVED = object()


def _freeze_json_value(value: Any) -> Any:
    """Return a deeply immutable snapshot of one JSON delta value.

    Mappings become ``MappingProxyType`` views over fresh dicts no other
    reference can reach; scalars pass through. Anything else — including
    mutable SDK objects — is refused so a stored result can never drift from
    the evidence derived from it. Route mappers store native fragments as
    their plain-JSON spec and let the construction site materialize them.
    """
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError(
        f"reasoning delta value of type {type(value).__name__} cannot be "
        "stored immutably"
    )


def _thaw_json_value(value: Any) -> Any:
    """Materialize a stored value back into fresh plain JSON structures."""
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    return value


def _freeze_evidence_pairs(
    pairs: Any, *, field_name: str
) -> tuple[tuple[str, str], ...]:
    """Snapshot/validate evidence pairs into tuples of exact string pairs.

    Frozen dataclasses do not enforce annotated tuple types at runtime; a
    caller-owned list could otherwise mutate stored evidence after
    validation. Malformed pair shapes/types are rejected loudly.
    """
    if isinstance(pairs, str) or not isinstance(pairs, (list, tuple)):
        raise ValueError(
            f"{field_name} must be a sequence of (path, value) string pairs"
        )
    frozen: list[tuple[str, str]] = []
    for pair in pairs:
        if (
            isinstance(pair, str)
            or not isinstance(pair, (list, tuple))
            or len(pair) != 2
            or not all(isinstance(part, str) for part in pair)
        ):
            raise ValueError(
                f"{field_name} entries must be (path, value) string pairs"
            )
        frozen.append((pair[0], pair[1]))
    return tuple(frozen)


@dataclass(frozen=True, slots=True)
class ReasoningConstruction:
    """Neutral immutable result of one route-local reasoning mapper invocation.

    The carrier is shared; the mapping logic stays route-local. One instance
    is produced exactly once at a real construction site and consumed twice:
    its deltas build the actual request kwargs / process command, and its
    ``emission()`` projection is the recorded evidence. ``__post_init__``
    refuses contradictory disposition/delta/evidence shapes and verifies the
    evidence paths against the actual deltas, so the record cannot drift from
    the bytes it describes.
    """

    route: str
    requested: str
    disposition: str
    json_delta: tuple[tuple[str, Any], ...] = ()
    argv_delta: tuple[str, ...] = ()
    env_delta: tuple[tuple[str, str], ...] = ()
    # Path prefix under which the json delta is merged at the construction
    # site (e.g. Gemini Interactions merges its fragment into
    # ``generation_config``; the native daemon hands its delta to
    # ``create_session``). Evidence paths must spell this exact declared
    # root; nothing else is stripped or guessed.
    merge_root: str | None = None
    emitted_path: str | None = None
    emitted_value: str | None = None
    coupled: tuple[tuple[str, str], ...] = ()
    source: str | None = None
    phase: str | None = None

    def __post_init__(self) -> None:
        # Deep-freeze the stored deltas first so verification below runs
        # against exactly what any later reader can observe.
        object.__setattr__(
            self,
            "json_delta",
            tuple((key, _freeze_json_value(value)) for key, value in self.json_delta),
        )
        object.__setattr__(self, "argv_delta", tuple(self.argv_delta))
        object.__setattr__(
            self, "env_delta", tuple(tuple(pair) for pair in self.env_delta)
        )
        object.__setattr__(
            self, "coupled", _freeze_evidence_pairs(self.coupled, field_name="coupled")
        )
        if self.disposition not in _DISPOSITIONS:
            raise ValueError(f"unknown reasoning disposition {self.disposition!r}")
        emitted = self.disposition == "emitted"
        has_evidence = self.emitted_path is not None and self.emitted_value is not None
        if emitted != has_evidence:
            raise ValueError(
                "emitted disposition and emitted path/value evidence must agree"
            )
        if emitted and not (self.json_delta or self.argv_delta or self.env_delta):
            raise ValueError("emitted disposition requires a non-empty delta")
        if self.json_delta and not emitted and not self.coupled:
            raise ValueError(
                "a non-emitted json delta must carry coupled-field evidence"
            )
        if self.disposition == "unsupported" and (
            self.json_delta or self.argv_delta or self.env_delta or self.coupled
        ):
            raise ValueError("an unsupported phase carries no construction delta")
        if emitted:
            self._verify_pair(self.emitted_path, self.emitted_value)
        for path, value in self.coupled:
            self._verify_pair(path, value)

    def materialize_json(self) -> dict[str, Any]:
        """Return a fresh, byte-identical mutable copy of the json delta.

        The construction site merges this copy into its request kwargs;
        mutating the copy can never write back into the stored result.
        """
        return {key: _thaw_json_value(value) for key, value in self.json_delta}

    def _verify_pair(self, path: str, value: str) -> None:
        resolved = self._resolve(path)
        if resolved is _UNRESOLVED or not (
            resolved == value or str(resolved) == value
        ):
            raise ValueError(
                f"evidence {path}={value!r} is not derived from the construction delta"
            )

    def _resolve(self, path: str) -> Any:
        if path.startswith("argv[") and path.endswith("]"):
            # Exact indexed argv position — used where raw tokens are
            # ambiguous (repeated flags, presence flags, flag-like values).
            index_text = path[len("argv["):-1]
            if index_text.isdigit():
                index = int(index_text)
                if index < len(self.argv_delta):
                    return self.argv_delta[index]
            return _UNRESOLVED
        if path.startswith("argv:"):
            flag = path[len("argv:"):]
            if flag in self.argv_delta:
                index = self.argv_delta.index(flag)
                if index + 1 < len(self.argv_delta):
                    return self.argv_delta[index + 1]
            return _UNRESOLVED
        if path.startswith("env:"):
            return dict(self.env_delta).get(path[len("env:"):], _UNRESOLVED)
        # Exact dotted lookup over the json delta. When the owner declares a
        # merge root, the recorded path must spell that exact root before the
        # delta-relative remainder; wrong or missing roots do not resolve and
        # there is no prefix guessing.
        segments = path.split(".")
        if self.merge_root is not None:
            root = self.merge_root.split(".")
            if segments[: len(root)] != root:
                return _UNRESOLVED
            segments = segments[len(root):]
            if not segments:
                return _UNRESOLVED
        node: Any = dict(self.json_delta)
        for segment in segments:
            if isinstance(node, Mapping) and segment in node:
                node = node[segment]
            else:
                return _UNRESOLVED
        return node

    def emission(self) -> ReasoningEmission:
        """Project this result's evidence record from its actual deltas."""
        if self.disposition == "emitted":
            return ReasoningEmission(
                requested=self.requested,
                emitted=(self.emitted_path, self.emitted_value),
                coupled=self.coupled,
                route=self.route,
                source=self.source,
                phase=self.phase,
            )
        return ReasoningEmission(
            requested=self.requested,
            omitted=True,
            dropped=self.disposition == "dropped",
            coupled=self.coupled,
            route=self.route,
            source=self.source,
            phase=self.phase,
        )


def reasoning_policy(value: ReasoningPolicy | str | None) -> ReasoningPolicy:
    """Convert a structured or legacy construction argument to a policy."""
    if isinstance(value, ReasoningPolicy):
        return value
    return ReasoningPolicy("default" if value is None else value)
