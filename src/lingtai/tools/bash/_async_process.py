"""Bash-local asynchronous process and ownership boundary.

The values in this module are deliberately neutral and JSON-safe.  Concrete
process handles are opaque to Bash policy and are implemented by an outer
adapter selected by composition.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Protocol

from ._shell_dialect import ShellInvocation


@dataclass(frozen=True)
class ProcessRef:
    """A diagnostic process id plus an adapter-owned incarnation when observed."""

    public_id: int
    incarnation: str | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.public_id, int)
            or isinstance(self.public_id, bool)
            or self.public_id <= 0
        ):
            raise ValueError("process public_id must be a positive integer")
        if self.incarnation is not None and (
            not isinstance(self.incarnation, str) or not self.incarnation
        ):
            raise ValueError("process incarnation must be a non-empty string when present")

    def to_dict(self) -> dict[str, object]:
        return {"public_id": self.public_id, "incarnation": self.incarnation}

    @classmethod
    def from_dict(cls, value: object) -> "ProcessRef | None":
        if not isinstance(value, dict):
            return None
        if "public_id" not in value or "incarnation" not in value:
            return None
        public_id = value["public_id"]
        incarnation = value["incarnation"]
        if not isinstance(public_id, int) or isinstance(public_id, bool) or public_id <= 0:
            return None
        if incarnation is not None and (
            not isinstance(incarnation, str) or not incarnation
        ):
            return None
        return cls(public_id, incarnation)


ProcessObservationKind = Literal["same", "changed", "gone", "unknown"]
CancellationOutcome = Literal["natural_or_concurrent", "unconfirmed", "group_cancelled"]


@dataclass(frozen=True)
class ProcessObservation:
    kind: ProcessObservationKind

    def __post_init__(self) -> None:
        if self.kind not in {"same", "changed", "gone", "unknown"}:
            raise ValueError(f"invalid process observation: {self.kind!r}")


@dataclass(frozen=True)
class ProcessCompletion:
    exit_code: int
    cancellation_outcome: CancellationOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool):
            raise ValueError("process exit_code must be an integer")
        if self.cancellation_outcome not in {
            None, "natural_or_concurrent", "unconfirmed", "group_cancelled"
        }:
            raise ValueError(
                f"invalid cancellation outcome: {self.cancellation_outcome!r}"
            )


class OwnedProcess(Protocol):
    """Opaque in-memory ownership token; adapters may use any concrete type."""


class BashAsyncProcessPort(Protocol):
    def launch_supervisor(self, job_dir: Path, start_token: str) -> tuple[ProcessRef, OwnedProcess]: ...
    def identify_current_process(self) -> ProcessRef | None: ...
    def spawn(
        self, invocation: ShellInvocation, cwd: str, stdout_path: Path, stderr_path: Path,
    ) -> tuple[ProcessRef, OwnedProcess]: ...
    def observe(self, process: ProcessRef) -> ProcessObservation: ...
    def wait_supervisor(self, owned: OwnedProcess) -> int: ...
    def wait(
        self, owned: OwnedProcess, cancellation_requested: Callable[[], bool],
    ) -> ProcessCompletion: ...


# Canonical Port name; the Bash spelling remains an internal compatibility name
# for retained PR1 imports and durable supervisor code.
ShellAsyncProcessPort = BashAsyncProcessPort


def process_ref_from_state(state: dict[str, object], prefix: str = "command") -> ProcessRef | None:
    value = state.get(f"{prefix}_process")
    return ProcessRef.from_dict(value)
