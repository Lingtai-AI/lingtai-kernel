"""Composition root for ``lingtai-agent guardian`` shadow observation."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from lingtai.adapters.agent_guardian import (
    FilesystemLifecycleLedgerAdapter,
    LocalAgentGuardianHostAdapter,
    observe_guardian_manifest,
)
from lingtai.kernel.agent_guardian import (
    GUARDIAN_CHECKPOINT_SECONDS,
    GUARDIAN_CONFIRMATION_INTERVAL_SECONDS,
    GUARDIAN_LOOP_INTERVAL_SECONDS,
    GUARDIAN_OUTPUT_SCHEMA,
    GUARDIAN_OUTPUT_VERSION,
    GuardianAlreadyRunning,
    GuardianLeaseUnavailable,
    GuardianHostPort,
    LifecycleLedgerCorruption,
    LifecycleLedgerError,
    LifecycleLedgerPort,
    evaluate_presence,
    needs_confirmation,
    parse_utc_timestamp,
    stable_json,
)
from lingtai.kernel.agent_presence import is_agent


EXIT_INVALID = 2
EXIT_ALREADY_RUNNING = 3
EXIT_LEDGER_UNSAFE = 4
EXIT_AMBIGUOUS = 5


def add_guardian_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "guardian",
        help="Observe one agent and emit a shadow-only recovery plan as JSON",
    )
    parser.add_argument("--agent-dir", type=Path, required=True, help="Agent working directory to observe")
    parser.add_argument("--once", action="store_true", help="Emit one confirmed verdict and exit")


def _write_json(stream: TextIO, value: object) -> None:
    stream.write(stable_json(value) + "\n")
    stream.flush()


def _error(stream: TextIO, code: str) -> None:
    _write_json(stream, {"error": {"code": code}})


def _agent_address(snapshot, agent_dir: Path) -> str:
    return agent_dir.name


def _checkpoint_due(latest: dict | None, *, policy_fingerprint: str, wall_now: float) -> bool:
    if latest is None or latest["payload"]["policy_fingerprint"] != policy_fingerprint:
        return True
    recorded = parse_utc_timestamp(latest["recorded_at"]).timestamp()
    elapsed = wall_now - recorded
    return elapsed < 0 or elapsed >= GUARDIAN_CHECKPOINT_SECONDS


def _output(decision, *, agent_dir: Path, agent_address: str, recorded: bool) -> dict:
    return {
        "schema": GUARDIAN_OUTPUT_SCHEMA,
        "schema_version": GUARDIAN_OUTPUT_VERSION,
        "agent_address": agent_address,
        "agent_dir": str(agent_dir),
        "sampled_at": datetime.fromtimestamp(decision.sample.sampled_at, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "verdict": decision.verdict,
        "recovery_plan": decision.recovery_plan,
        "confirmation": decision.confirmation,
        "intent": {
            "state": "active" if decision.active_intent_id else "none",
            "intent_id": decision.active_intent_id,
        },
        "evidence": {
            "digest": decision.evidence_digest(),
            "confirmation_sample_digest": (
                decision.confirmation_sample.evidence_digest()
                if decision.confirmation_sample else None
            ),
            "runtime_id": decision.sample.runtime_id,
            "pid": decision.sample.pid,
            "expected_start_identity": decision.sample.expected_start_identity,
            "observed_start_identity": decision.sample.observed_start_identity,
            "process": decision.sample.process,
            "agent_lease": decision.sample.agent_lease,
            "agent_manifest": decision.sample.agent_manifest,
            "heartbeat": decision.sample.heartbeat,
            "heartbeat_age_seconds": decision.sample.heartbeat_age_seconds,
            "command_match": decision.sample.command_match,
            "executable_match": decision.sample.executable_match,
            "registered_workdir_match": decision.sample.registered_workdir_match,
            "issues": list(decision.sample.issues),
        },
        "shadow_only": True,
        "recorded": recorded,
    }


def run_guardian_cli(
    agent_dir: Path,
    *,
    once: bool,
    ledger: LifecycleLedgerPort | None = None,
    host: GuardianHostPort | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Run one owned observer.  No action-capable dependency exists here."""
    try:
        agent_dir = agent_dir.resolve()
    except (OSError, RuntimeError, ValueError):
        _error(stderr, "agent_dir_invalid")
        return EXIT_INVALID
    if not agent_dir.is_dir():
        _error(stderr, "agent_dir_invalid")
        return EXIT_INVALID
    manifest = observe_guardian_manifest(agent_dir)
    if not is_agent(manifest):
        _error(stderr, "agent_dir_not_agent")
        return EXIT_INVALID
    try:
        ledger = ledger or FilesystemLifecycleLedgerAdapter(agent_dir)
        host = host or LocalAgentGuardianHostAdapter(agent_dir)
    except LifecycleLedgerError as exc:
        _error(stderr, exc.code)
        return EXIT_LEDGER_UNSAFE
    except (OSError, OverflowError, TypeError, ValueError):
        _error(stderr, "guardian_setup_unavailable")
        return EXIT_LEDGER_UNSAFE
    guardian_id = str(uuid.uuid4())
    try:
        host.acquire_guardian_lease()
    except GuardianAlreadyRunning:
        _error(stderr, "guardian_already_running")
        return EXIT_ALREADY_RUNNING
    except GuardianLeaseUnavailable as exc:
        _error(stderr, exc.code)
        return EXIT_LEDGER_UNSAFE
    except (OSError, OverflowError, TypeError, ValueError):
        _error(stderr, "guardian_lease_unavailable")
        return EXIT_LEDGER_UNSAFE

    exit_code: int | None = None
    terminal_output: dict | None = None
    terminal_error: str | None = None
    original_error: BaseException | None = None
    try:
        while exit_code is None:
            try:
                snapshot = ledger.read_snapshot()
                first = host.sample(snapshot.latest_boot)
                second = None
                if needs_confirmation(first):
                    host.sleep(GUARDIAN_CONFIRMATION_INTERVAL_SECONDS)
                    snapshot = ledger.read_snapshot()
                    second = host.sample(snapshot.latest_boot)
                decision = evaluate_presence(
                    first,
                    second,
                    active_intent_id=snapshot.active_intent_id,
                )
                address = _agent_address(snapshot, agent_dir)
                should_record = once or _checkpoint_due(
                    snapshot.latest_guardian,
                    policy_fingerprint=decision.policy_fingerprint(),
                    wall_now=host.wall_time(),
                )
                if should_record:
                    ledger.append_guardian_verdict(
                        agent_address=address,
                        actor_id=guardian_id,
                        reason="shadow_presence_evaluation",
                        payload=decision.event_payload(guardian_id),
                    )
                output = _output(
                    decision,
                    agent_dir=agent_dir,
                    agent_address=address,
                    recorded=should_record,
                )
            except (LifecycleLedgerCorruption, LifecycleLedgerError) as exc:
                terminal_error = exc.code
                exit_code = EXIT_LEDGER_UNSAFE
                continue
            except (
                OSError,
                OverflowError,
                TypeError,
                ValueError,
                RecursionError,
                UnicodeError,
                MemoryError,
            ):
                terminal_error = "guardian_observation_unavailable"
                exit_code = EXIT_LEDGER_UNSAFE
                continue
            if once:
                terminal_output = output
                exit_code = EXIT_AMBIGUOUS if decision.verdict == "unknown" else 0
                continue
            _write_json(stdout, output)
            try:
                host.sleep(GUARDIAN_LOOP_INTERVAL_SECONDS)
            except (
                OSError,
                OverflowError,
                TypeError,
                ValueError,
                RecursionError,
                UnicodeError,
                MemoryError,
            ):
                terminal_error = "guardian_observation_unavailable"
                exit_code = EXIT_LEDGER_UNSAFE
    except KeyboardInterrupt:
        exit_code = 0
    except BaseException as exc:
        # Preserve unrelated programmer/system failures, but release ownership
        # before re-raising them below with their original traceback.
        original_error = exc

    try:
        host.release_guardian_lease()
    except (
        GuardianLeaseUnavailable,
        OSError,
        OverflowError,
        TypeError,
        ValueError,
        RecursionError,
        UnicodeError,
    ):
        # Lease-release failure has explicit precedence over the pending result.
        terminal_output = None
        terminal_error = "guardian_lease_unavailable"
        exit_code = EXIT_LEDGER_UNSAFE

    if original_error is not None:
        raise original_error.with_traceback(original_error.__traceback__)
    if terminal_error is not None:
        _error(stderr, terminal_error)
    elif terminal_output is not None:
        _write_json(stdout, terminal_output)
    return 0 if exit_code is None else exit_code


def handle_guardian_command(args) -> None:
    raise SystemExit(run_guardian_cli(args.agent_dir, once=bool(args.once)))


__all__ = ["add_guardian_parser", "handle_guardian_command", "run_guardian_cli"]
