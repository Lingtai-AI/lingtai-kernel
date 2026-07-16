"""Small contract checks for the Bash-local async process/lock boundaries."""
from pathlib import Path

import pytest

from lingtai.adapters.bash_process import select_bash_async_process
from lingtai.adapters.bash_state_lock import select_bash_state_lock
from lingtai.adapters.posix import bash_process as posix_bash_process
from lingtai.tools.bash._async_process import (
    ProcessCompletion,
    ProcessObservation,
    ProcessRef,
)
from lingtai.tools.bash._shell_dialect import ShellInvocation


def test_process_ref_is_json_safe_and_round_trips():
    ref = ProcessRef(41, "incarnation-a")
    assert ProcessRef.from_dict(ref.to_dict()) == ref
    unavailable = ProcessRef(42, None)
    assert ProcessRef.from_dict(unavailable.to_dict()) == unavailable
    assert ProcessRef.from_dict({"public_id": 41, "incarnation": ""}) is None
    assert ProcessRef.from_dict({"public_id": 41}) is None


def test_posix_selectors_expose_capability_local_adapters(tmp_path):
    process = select_bash_async_process()
    lock = select_bash_state_lock()
    assert process.observe(ProcessRef(2**31 - 1, "never")).kind in {
        "gone", "unknown", "changed"
    }
    with lock.exclusive(Path(tmp_path)):
        assert (Path(tmp_path) / ".state.lock").exists()


def test_observation_is_neutral_value():
    assert ProcessObservation("same").kind == "same"
    with pytest.raises(ValueError, match="invalid process observation"):
        ProcessObservation("invented")
    with pytest.raises(ValueError, match="invalid cancellation outcome"):
        ProcessCompletion(0, "invented")
    with pytest.raises(ValueError, match="positive integer"):
        ProcessRef(0, "incarnation")


def test_posix_unavailable_identity_remains_unknown_if_identity_appears(monkeypatch):
    process = select_bash_async_process()
    monkeypatch.setattr(posix_bash_process, "_alive", lambda public_id: True)
    unavailable = ProcessRef(41, None)

    monkeypatch.setattr(
        posix_bash_process,
        "_ref",
        lambda public_id: ProcessRef(public_id, "later-observed-incarnation"),
    )
    assert process.observe(unavailable) == ProcessObservation("unknown")

    monkeypatch.setattr(
        posix_bash_process, "_ref", lambda public_id: ProcessRef(public_id, None)
    )
    assert process.observe(unavailable) == ProcessObservation("unknown")


def test_posix_adapter_owns_spawn_and_exact_wait(tmp_path):
    process = select_bash_async_process()
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    invocation = ShellInvocation(
        script="printf adapter-out; printf adapter-err >&2; exit 7",
        executable="/bin/sh",
        argv=("-c",),
    )

    process_ref, owned = process.spawn(
        invocation, str(tmp_path), stdout_path, stderr_path
    )
    completion = process.wait(owned, lambda: False)

    assert process_ref.public_id > 0
    assert completion == ProcessCompletion(7)
    assert stdout_path.read_text() == "adapter-out"
    assert stderr_path.read_text() == "adapter-err"
