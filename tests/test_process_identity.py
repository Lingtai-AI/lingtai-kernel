from __future__ import annotations

from types import SimpleNamespace

from lingtai.adapters.posix import process_identity as identity
from lingtai.tools.daemon import runtime
from lingtai.tools.daemon import supervisor_runtime


def test_linux_identity_parses_comm_parentheses_and_boot_id(tmp_path):
    proc_root = tmp_path / "proc"
    stat_dir = proc_root / "123"
    stat_dir.mkdir(parents=True)
    # A comm containing spaces must not shift the start-ticks field.
    fields_after_comm = ["S"] + ["0"] * 18 + ["987654"]
    (stat_dir / "stat").write_text(
        "123 (worker with spaces) " + " ".join(fields_after_comm),
        encoding="utf-8",
    )
    boot_id = tmp_path / "boot_id"
    boot_id.write_text("boot-abc\n", encoding="utf-8")

    assert identity._linux_process_identity(
        123, proc_root=proc_root, boot_id_path=boot_id
    ) == "linux:boot-abc:987654"


def test_ps_identity_is_bounded_and_contains_start_and_parent(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="Mon Jul 14 21:00:00 2026  77\n")

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    result = identity._ps_process_identity(42)

    assert result == "ps:Mon Jul 14 21:00:00 2026  77"
    assert seen["argv"] == ["ps", "-o", "lstart=", "-o", "ppid=", "-p", "42"]
    assert seen["kwargs"]["timeout"] == 1.0


def test_identity_match_refuses_unknown_and_mismatch(monkeypatch):
    monkeypatch.setattr(identity, "process_identity", lambda pid: None)
    assert identity.process_identity_matches(42, "linux:boot:1") is False
    assert identity.process_identity_matches(42, None) is False

    monkeypatch.setattr(identity, "process_identity", lambda pid: "linux:boot:2")
    assert identity.process_identity_matches(42, "linux:boot:1") is False
    assert identity.process_identity_matches(42, "linux:boot:2") is True


def test_darwin_precise_identity_adapter_is_used(monkeypatch):
    monkeypatch.setattr(identity.sys, "platform", "darwin")
    monkeypatch.setattr(identity, "_darwin_process_identity", lambda pid: "darwin:1:234567")
    monkeypatch.setattr(identity.os, "kill", lambda pid, sig: None)
    assert identity.process_identity(42) == "darwin:1:234567"


def test_darwin_unavailable_identity_refuses_destructive_identity(monkeypatch):
    monkeypatch.setattr(identity.sys, "platform", "darwin")
    monkeypatch.setattr(identity, "_darwin_process_identity", lambda pid: None)
    monkeypatch.setattr(identity.os, "kill", lambda pid, sig: None)
    assert identity.process_identity(42) is None
    assert not identity.process_identity_matches(42, "darwin:1:234567")


def test_darwin_pid_reuse_mismatch_does_not_match(monkeypatch):
    monkeypatch.setattr(identity.sys, "platform", "darwin")
    monkeypatch.setattr(identity, "_darwin_process_identity", lambda pid: "darwin:2:1")
    monkeypatch.setattr(identity.os, "kill", lambda pid, sig: None)
    assert not identity.process_identity_matches(42, "darwin:1:1")


def test_kill_process_group_refuses_stale_or_unknown_identity(monkeypatch):
    proc = SimpleNamespace(pid=4242, returncode=None, poll=lambda: None, wait=lambda **kwargs: None)
    proc._lingtai_pgid = 4242
    proc._lingtai_process_identity = "linux:boot:old"
    killed = []
    monkeypatch.setattr(runtime, "process_identity_matches", lambda pid, saved: False)
    monkeypatch.setattr(runtime.os, "killpg", lambda *args: killed.append(args))

    runtime.kill_process_group(proc)

    assert killed == []


def test_detached_owner_refuses_stale_reused_pid_signal(monkeypatch):
    class RunDir:
        path = None

        @staticmethod
        def read_state_from_disk(path):
            return {
                "execution_pid": None,
                "execution_pgid": None,
                "child_pid": 5151,
                "child_pgid": 5151,
                "child_start_identity": "linux:boot:old",
            }

    killed = []
    monkeypatch.setattr(supervisor_runtime.os, "getpgid", lambda pid: 5151)
    monkeypatch.setattr(supervisor_runtime, "process_identity_matches", lambda pid, saved: False)
    monkeypatch.setattr(supervisor_runtime.os, "killpg", lambda *args: killed.append(args))

    supervisor_runtime._terminate_exact_run_children(RunDir())

    assert killed == []
