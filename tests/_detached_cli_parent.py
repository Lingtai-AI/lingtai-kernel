"""Short-lived parent used by detached CLI credential acceptance tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from lingtai.adapters.posix.daemon_supervisor import PosixDaemonSupervisorAdapter
from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest
from lingtai.kernel.daemon_supervisor.manifest import build_manifest, manifest_path_for, write_manifest
from lingtai.tools.daemon.run_dir import DaemonRunDir


def main() -> int:
    parent, fake_cli, report = map(Path, sys.argv[1:4])
    parent.mkdir(parents=True, exist_ok=True)
    run_dir = DaemonRunDir(
        parent_working_dir=parent, handle="em-parent-exit", run_id="em-parent-exit",
        task="credential parent exit", tools=[], model="codex", max_turns=1,
        timeout_s=30, parent_addr=parent.name, parent_pid=os.getpid(),
        system_prompt="credential parent exit",
    )
    raw_argv = [sys.executable, str(fake_cli), "--token", "argv-secret-parent-exit"]
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="codex", parent_working_dir=str(parent),
        run_dir=str(run_dir.path), task="credential parent exit", tools=[],
        max_turns=1, timeout_s=30, group_id=None, backend_argv=raw_argv,
    )
    write_manifest(run_dir.path, manifest)
    request = DaemonSupervisorRequest(
        run_id=run_dir.run_id, manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )
    PosixDaemonSupervisorAdapter().spawn_detached(
        request,
        capsule={
            "backend_argv": raw_argv,
            "credential_env": {"OPENAI_API_KEY": os.environ["OPENAI_API_KEY"]},
        },
    )
    print(run_dir.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
