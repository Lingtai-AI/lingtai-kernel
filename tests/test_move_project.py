"""Focused safety and isolated lifecycle checks for project-root migration."""
from __future__ import annotations
import importlib.util
import json
import os
import re
import socket
import stat
import subprocess
import sys
import sysconfig
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from lingtai import venv_resolve
from lingtai.adapters.project_workspace import FilesystemProjectWorkspaceAdapter
from lingtai.kernel.project import ProjectCreateRequest, ProjectCreationUseCase

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/move_project.py"
spec = importlib.util.spec_from_file_location("move_project", SCRIPT)
move_project = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = move_project
assert spec.loader is not None
spec.loader.exec_module(move_project)
@pytest.fixture(autouse=True)
def _reset_process_observation_environment(monkeypatch):
    if hasattr(move_project, "_PROCESS_OBSERVATION_ENV"): monkeypatch.setattr(move_project, "_PROCESS_OBSERVATION_ENV", None)
def _wait_until(predicate, timeout: float = 25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False
def _agents_at(agent: Path) -> list[int]:
    result = subprocess.run(
        ["ps", "-ax", "-o", "pid=,command="], capture_output=True, encoding="utf-8", errors="strict",
        env=move_project._process_observation_environment(), check=True, timeout=5
    )
    suffix = f" -m lingtai run {agent}"
    return [int(line.split(None, 1)[0]) for line in result.stdout.splitlines() if line.endswith(suffix)]
def _supervisor_at(pid: int | None, source: Path) -> bool:
    if pid is None:
        return False
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False, timeout=5
    )
    return result.returncode == 0 and "--_supervise" in result.stdout and str(source) in result.stdout
def _write_init(agent: Path, venv: Path, base_url: str | None = None) -> dict:
    data = {"manifest": {"agent_name": "temporary-true-name", "language": "en",
        "llm": {"provider": "gemini", "model": "test", "api_key": "fake", "base_url": base_url},
        "capabilities": {}, "soul": {"delay": 60}, "stamina": 10, "context_limit": None,
        "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 5, "admin": {}, "streaming": False},
        "principle": "", "covenant": "No network.", "pad": "", "lingtai": "", "venv_path": str(venv),
        "preserved": {"nested": [1, True, None, "unchanged"]}}
    (agent / "init.json").write_text(json.dumps(data), encoding="utf-8")
    return data
def _write_identity(agent: Path, agent_id: str = "id") -> None:
    data = {"agent_id": agent_id, "agent_name": "temporary-true-name", "address": agent.name}
    (agent / ".agent.json").write_text(json.dumps(data), encoding="utf-8")
def _create_project(root: Path, preset: Path, base_url: str | None = None) -> Path:
    root.mkdir()
    llm = {"provider": "gemini", "model": "test", "api_key": "fake", "base_url": base_url}
    preset.write_text(json.dumps({"description": {"summary": "test"},
        "manifest": {"llm": llm, "capabilities": {}}}), encoding="utf-8")
    request = ProjectCreateRequest("initiator", str(preset), llm, {},
        '{"covenant":"No network.","schema_version":1}\n')
    ProjectCreationUseCase(FilesystemProjectWorkspaceAdapter(root, validate_agent=lambda _: None)).create(request)
    return root / ".lingtai/initiator"
def _make_live_seed(agent: Path) -> None:
    (agent / "logs").mkdir()
    manifest = json.loads((agent / ".agent.json").read_text(encoding="utf-8"))
    manifest["agent_id"] = "id"
    (agent / ".agent.json").write_text(json.dumps(manifest), encoding="utf-8")
def _receipt(path: Path):
    return None if not path.exists() else (path.stat().st_ino, path.read_bytes())
def _prepared_runtime(tmp_path: Path, monkeypatch, status: str, managed: bool = True):
    home = tmp_path / "home"; default = home / ".lingtai-tui/runtime/venv"; runtime = default if managed else tmp_path / "operator-venv"
    subprocess.run([venv_resolve._find_python(), "-m", "venv", "--copies", "--without-pip", "--system-site-packages", str(runtime)], check=True, capture_output=True, text=True, timeout=60)
    marker = runtime / ".lingtai-env.json"
    if status in {"match", "policy-error-match"}: marker.write_text(json.dumps(venv_resolve._current_venv_env_marker(runtime)), encoding="utf-8")
    elif status == "mismatch":
        payload = venv_resolve._current_venv_env_marker(runtime); payload["os"] = "other-os"; marker.write_text(json.dumps(payload), encoding="utf-8")
    elif status == "error": marker.write_text("{", encoding="utf-8")
    seam = tmp_path / "policy-seam"
    if status.startswith("policy-error") or status == "obsolete-missing":
        seam.mkdir(); policy = "(_ for _ in ()).throw(RuntimeError('unavailable'))" if status.startswith("policy-error") else "v._PythonSelectionPolicy(v.sys.platform,v.platform.machine(),None,None,(99,0),None,())"
        (seam / "sitecustomize.py").write_text(f"from lingtai import venv_resolve as v\nv._python_selection_policy=lambda:{policy}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home)); monkeypatch.setenv("PYTHONPATH", os.pathsep.join(([str(seam)] if seam.is_dir() else []) + [str(ROOT / "src")]))
    monkeypatch.setattr(venv_resolve, "_DEFAULT_RUNTIME_DIR", default); return runtime, marker
@pytest.mark.parametrize("case", ["canonical", "canonical-missing-admin", "ordinary-partial", "malformed", "nonmapping", "wrong-name",
    "wrong-address", "missing-identity", "admin", "init", "record-symlink", "record-directory",
    "child-alias", "classification-error"])
def test_project_created_human_topology_is_exactly_classified(case: str, tmp_path: Path, monkeypatch):
    source = tmp_path / "source"; agent = _create_project(source, tmp_path / "preset.json"); _make_live_seed(agent)
    human = source / ".lingtai/human"; record = human / ".agent.json"
    if case == "canonical-missing-admin": data = json.loads(record.read_text()); data.pop("admin"); record.write_text(json.dumps(data))
    elif case == "ordinary-partial":
        rogue = source / ".lingtai/rogue"; rogue.mkdir(); (rogue / ".agent.json").write_text("{}")
    elif case in {"malformed", "nonmapping"}: record.write_text("{" if case == "malformed" else "[]")
    elif case in {"wrong-name", "wrong-address", "missing-identity", "admin"}:
        data = json.loads(record.read_text()); key = "agent_name" if case == "wrong-name" else "address"
        if case == "missing-identity": data.pop(key)
        else: data[key if case != "admin" else "admin"] = {} if case == "admin" else "other"
        record.write_text(json.dumps(data))
    elif case == "init": data = json.loads(record.read_text()); data["agent_id"] = "human-id"; record.write_text(json.dumps(data)); (human / "init.json").write_text('{"manifest":{"agent_name":"human"}}')
    elif case in {"record-symlink", "record-directory"}:
        saved = record.rename(human / "manifest.saved")
        record.symlink_to(saved) if case == "record-symlink" else record.mkdir()
    elif case == "child-alias":
        actual = human.rename(source / ".lingtai/human-real"); human.symlink_to(actual, target_is_directory=True)
    elif case == "classification-error":
        real = move_project._record_present
        monkeypatch.setattr(move_project, "_record_present", lambda path: (_ for _ in ()).throw(
            move_project.MoveProjectError("classification failed")) if path == record else real(path))
    if case.startswith("canonical"): assert move_project._agent_workdirs(source) == (agent,)
    else:
        with pytest.raises(move_project.MoveProjectError): move_project._agent_workdirs(source)
def _events(agent: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in (agent / "logs/events.jsonl").read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError):
        return []
def _continuation_prompt(source: Path, target: Path) -> str:
    return (f"Project-root migration continuation: this same Agent's Project moved from {source} to {target}. "
            f"From the new root {target}, continue only the remaining stale-reference migration, then verify and "
            "report. Do not move again, roll back, create symlinks, delete or clean anything, change the Agent "
            "address, or alter unrelated config or auth.")
def _fake_plan(source: Path, target: Path, workdirs: tuple[Path, ...]):
    agent = workdirs[0]
    return SimpleNamespace(source=source, target=target, agent=agent,
        target_agent=target / ".lingtai" / agent.name, identity=("id", "name", agent.name),
        pid=101, workdirs=workdirs, process_token="stable-token")
def _stub_supervision(monkeypatch, plan, token: str = "stable-token") -> None:
    monkeypatch.setattr(move_project, "preflight", lambda *_: plan)
    monkeypatch.setattr(move_project, "_agent_workdirs", lambda _: plan.workdirs)
    monkeypatch.setattr(move_project, "_project_processes", lambda *_: {plan.pid: plan.agent})
    monkeypatch.setattr(move_project, "process_identity", lambda _: token)
def _process_preflight_fixture(tmp_path: Path, monkeypatch):
    source, target, venv = tmp_path / "source", tmp_path / "target", tmp_path / "venv"
    agent = source / ".lingtai/initiator"
    (agent / "logs").mkdir(parents=True)
    venv.mkdir()
    _write_init(agent, venv)
    _write_identity(agent)
    monkeypatch.setattr(move_project, "_probe", lambda runtime, *_: (runtime, ROOT / "src/lingtai/__init__.py"))
    monkeypatch.setattr(move_project, "_process_token", lambda *_: "stable-token")
    monkeypatch.setattr(move_project, "_fresh", lambda *_: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda *_: True)
    return source, target, agent
def _ps_rows(monkeypatch, *rows: str) -> None:
    move_project._PROCESS_OBSERVATION_ENV = os.environ.copy()
    output = "".join(f"{index + 101} {row}\n" for index, row in enumerate(rows))
    monkeypatch.setattr(move_project.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout=output))
def test_preflight_recognizes_official_console_initiating_process(tmp_path: Path, monkeypatch):
    _, target, agent = _process_preflight_fixture(tmp_path, monkeypatch)
    _ps_rows(monkeypatch, f"/venv/bin/lingtai-agent run {agent}")
    assert move_project.preflight(agent, target).pid == 101
@pytest.mark.parametrize("case", ["symlink", "dotdot", "non-direct", "argument-lookalike", "ambiguous"])
def test_preflight_canonical_process_path_fence(case: str, tmp_path: Path, monkeypatch):
    source, target, agent = _process_preflight_fixture(tmp_path, monkeypatch)
    if case == "symlink":
        shown = tmp_path / "agent-alias"; shown.symlink_to(agent, target_is_directory=True)
    elif case == "dotdot":
        (source / ".lingtai/spare").mkdir(); shown = source / ".lingtai/spare/../initiator"
    elif case == "non-direct":
        shown = agent / "nested"; shown.mkdir()
    else:
        shown = agent
    command, expected = f"/venv/bin/lingtai-agent run {shown}", "Project Agent path"
    if case == "argument-lookalike":
        command, expected = f"tail -f /tmp/x lingtai-agent run {agent}", "exactly one live process"
    elif case == "ambiguous":
        command, expected = f"python -m lingtai run {agent} -m lingtai run {agent}", "ambiguous LingTai command"
    _ps_rows(monkeypatch, command)
    with pytest.raises(move_project.MoveProjectError, match=expected): move_project.preflight(agent, target)
@pytest.mark.parametrize("program", ["/venv/bin/lingtai-agent", "/venv/bin/lingtai"], ids=["console", "legacy"])
def test_post_suspend_rescan_detects_supported_replacement(program: str, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent, sibling = source / ".lingtai/initiator", source / ".lingtai/sibling"
    agent.mkdir(parents=True); sibling.mkdir()
    plan = _fake_plan(source, target, (agent, sibling))
    _ps_rows(monkeypatch, f"{program} run {sibling}")
    monkeypatch.setattr(move_project, "process_identity", lambda *_: None)
    with pytest.raises(move_project.MoveProjectError, match="another live Project Agent appeared"):
        move_project._validate_observed_processes(plan, {plan.pid: plan.agent}, post_suspend=True)
@pytest.mark.parametrize(("gate", "program"), [("final", "/venv/bin/lingtai-agent"), ("old-source", "/venv/bin/lingtai")])
def test_supported_process_forms_reach_quiescence_gates(gate: str, program: str, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai/initiator"; agent.mkdir(parents=True)
    plan = _fake_plan(source, target, (agent,)); plan.init = {}
    _ps_rows(monkeypatch, f"{program} run {agent}")
    if gate == "old-source":
        action, expected = lambda: move_project._reject_old_source_processes(plan), "old source root"
    else:
        monkeypatch.setattr(move_project, "_roots", lambda *_: (source, target, agent))
        monkeypatch.setattr(move_project, "_agent_workdirs", lambda *_: plan.workdirs)
        monkeypatch.setattr(move_project, "_require_prompt_absent", lambda *_: None)
        monkeypatch.setattr(move_project, "_fresh", lambda *_: False)
        monkeypatch.setattr(move_project, "_identity", lambda *_: plan.identity)
        monkeypatch.setattr(move_project, "_read_object", lambda *_: plan.init)
        action, expected = lambda: move_project._revalidate_cutover(plan), "process-quiescent"
    with pytest.raises(move_project.MoveProjectError, match=expected): action()
@pytest.mark.parametrize("unexpected", [False, True], ids=["exact-console", "unexpected-legacy"])
def test_target_liveness_fences_supported_process_forms(unexpected: bool, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    old_agent, old_sibling = source / ".lingtai/initiator", source / ".lingtai/sibling"
    agent, sibling = target / ".lingtai/initiator", target / ".lingtai/sibling"
    (agent / "logs").mkdir(parents=True); sibling.mkdir(); (agent / ".agent.heartbeat").touch()
    plan = SimpleNamespace(source=source, target=target, agent=old_agent, target_agent=agent,
        workdirs=(old_agent, old_sibling), identity=("id", "name", "initiator"), init={}, rebased_init={},
        resumed_runtime=target / "venv/bin/python", environment={})
    rows = [f"/venv/bin/lingtai-agent run {agent}"]
    if unexpected: rows.append(f"/venv/bin/lingtai run {sibling}")
    _ps_rows(monkeypatch, *rows)
    monkeypatch.setattr(move_project, "_identity", lambda *_: plan.identity)
    monkeypatch.setattr(move_project, "_probe", lambda *_: (plan.resumed_runtime, agent))
    monkeypatch.setattr(move_project, "_fresh", lambda *_: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda *_: True)
    monkeypatch.setattr(move_project.time, "time", lambda: 0)
    ticks = iter((0, 0, 2)); monkeypatch.setattr(move_project.time, "monotonic", lambda: next(ticks, 2))
    monkeypatch.setattr(move_project.time, "sleep", lambda *_: None)
    def fake_popen(*_args, **_kwargs):
        (agent / ".prompt").unlink(); return SimpleNamespace(pid=101, returncode=None, poll=lambda: None)
    monkeypatch.setattr(move_project.subprocess, "Popen", fake_popen)
    if unexpected:
        with pytest.raises(move_project.MoveProjectError, match="unexpected process appeared"): move_project._resume(plan, 1)
    else:
        assert move_project._resume(plan, 1) == 101
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_native_project_rename_never_overwrites_existing_target(tmp_path: Path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "sentinel").write_text("source", encoding="utf-8")
    (target / "sentinel").write_text("target", encoding="utf-8")
    with pytest.raises(OSError):
        move_project._rename_no_replace(source, target)
    assert (source / "sentinel").read_text(encoding="utf-8") == "source"
    assert (target / "sentinel").read_text(encoding="utf-8") == "target"
def test_another_live_project_agent_is_refused(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    agent, sibling = project / ".lingtai" / "initiator", project / ".lingtai" / "sibling"
    monkeypatch.setattr(move_project, "_project_processes", lambda *_: {101: agent, 202: sibling})
    with pytest.raises(move_project.MoveProjectError, match="another live Project Agent"):
        move_project._initiating_pid(project, agent, (agent, sibling))
def test_process_scan_failure_refuses_to_infer_absence(monkeypatch):
    move_project._PROCESS_OBSERVATION_ENV = os.environ.copy()
    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["ps"], 2)
    monkeypatch.setattr(move_project.subprocess, "run", fail)
    with pytest.raises(move_project.MoveProjectError, match="refusing to infer absence"):
        move_project._project_processes(Path("/tmp/project"), ())
@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin ps locale representation")
def test_actual_process_scan_round_trips_ascii_spaces_and_printable_unicode(tmp_path: Path, monkeypatch):
    projects = (tmp_path / "allowed interior space", tmp_path / "allowed 雪 project")
    workdirs = (projects[0] / ".lingtai/agent space", projects[1] / ".lingtai/agent 月")
    for workdir in workdirs: workdir.mkdir(parents=True)
    gate = tmp_path / "release-children"; monkeypatch.setenv("LC_ALL", "C")
    code = "from pathlib import Path;import sys,time;gate=Path(sys.argv[1]);exec('while not gate.exists(): time.sleep(.05)')"
    children = [subprocess.Popen([sys.executable, "-c", code, str(gate), "-m", "lingtai", "run", str(path)]) for path in workdirs]
    try:
        observed = {pid: path for project, path in zip(projects, workdirs)
                    for pid, path in move_project._project_processes(project, (path,)).items()}
        expected = {child.pid: path for child, path in zip(children, workdirs)}
        print(f"process-observation ascii={observed.get(children[0].pid) == workdirs[0]} unicode={observed.get(children[1].pid) == workdirs[1]}")
        assert observed == expected
    finally:
        gate.touch(); [child.wait(timeout=10) for child in children]
def test_verified_utf8_environment_is_private_cached_and_used_by_every_ps(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "ambient-locale"); before = os.environ.copy(); calls = []
    monkeypatch.setattr(move_project.sys, "platform", "linux")
    workdir = tmp_path / "project/.lingtai/agent 雪"; workdir.mkdir(parents=True)
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv == ["locale", "charmap"]:
            charmap = "US-ASCII" if kwargs["env"]["LC_ALL"] == "C.UTF-8" else "UTF-8"
            return SimpleNamespace(returncode=0, stdout=charmap + "\n")
        stdout = f"202 python -m lingtai run {workdir}\n" if any("command=" in arg for arg in argv) else f"{os.getpid()} 101\n101 1\n"
        return SimpleNamespace(returncode=0, stdout=stdout)
    monkeypatch.setattr(move_project.subprocess, "run", run)
    selected = move_project._process_observation_environment()
    assert move_project._process_observation_environment() is selected and selected["LC_ALL"] == "C.utf8"
    assert move_project._project_processes(tmp_path / "project", (workdir,)) == {202: workdir}
    plan = _fake_plan(tmp_path / "project", tmp_path / "target", (workdir,))
    monkeypatch.setattr(move_project, "process_identity", lambda _: plan.process_token)
    move_project._require_self_caller(plan)
    assert os.environ == before and [call[1]["env"]["LC_ALL"] for call in calls[:2]] == ["C.UTF-8", "C.utf8"]
    assert all(kwargs["env"] is selected and kwargs["encoding"] == "utf-8" and kwargs["errors"] == "strict"
               for argv, kwargs in calls if argv[0] == "ps")
def test_no_verified_utf8_locale_refuses_before_probe_or_mutation(tmp_path: Path, monkeypatch):
    source, target, agent = _process_preflight_fixture(tmp_path, monkeypatch)
    before = tuple(map(_receipt, (agent / "init.json", agent / ".agent.json"))); calls = []
    monkeypatch.setattr(move_project.sys, "platform", "darwin"); monkeypatch.setattr(move_project, "_PROCESS_OBSERVATION_ENV", None, raising=False)
    monkeypatch.setattr(move_project.subprocess, "run", lambda argv, **_: calls.append(argv) or SimpleNamespace(returncode=0, stdout="US-ASCII\n"))
    monkeypatch.setattr(move_project, "_probe", lambda *_: pytest.fail("runtime probe reached")); monkeypatch.setattr(move_project.subprocess, "Popen", lambda *_a, **_k: pytest.fail("supervisor or Agent launch reached"))
    with pytest.raises(move_project.MoveProjectError, match="verified UTF-8"): move_project.preflight(agent, target)
    assert calls == [["locale", "charmap"]] and before == tuple(map(_receipt, (agent / "init.json", agent / ".agent.json")))
    assert source.is_dir() and not target.exists() and not (agent / ".suspend").exists() and not (agent / ".prompt").exists()
def test_post_suspend_disappearance_requires_empty_rescan(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai" / "initiator"
    agent.mkdir(parents=True)
    plan = _fake_plan(source, target, (agent,))
    snapshots = iter(({plan.pid: plan.agent}, {}, {}))
    monkeypatch.setattr(move_project, "_project_processes", lambda *_: next(snapshots))
    monkeypatch.setattr(move_project, "process_identity", lambda _: None)
    monkeypatch.setattr(move_project, "_fresh", lambda _: False)
    lease = move_project._wait_stopped(plan, 1)
    lease.close()
    with pytest.raises(move_project.MoveProjectError, match="incarnation changed"):
        move_project._validate_observed_processes(plan, {plan.pid: plan.agent})
@pytest.mark.parametrize("case", ["retained-unavailable", "retained-reused", "replacement", "initial-reused"])
def test_post_suspend_rescan_refuses_uncertainty_or_replacement(case: str, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai" / "initiator"
    plan = _fake_plan(source, target, (agent,))
    second = "replacement-token" if case == "retained-reused" else None
    tokens = iter(("replacement-token",) if case == "initial-reused" else (None, second))
    rescanned = {202: source / ".lingtai" / "replacement"} if case == "replacement" else {plan.pid: plan.agent}
    monkeypatch.setattr(move_project, "process_identity", lambda _: next(tokens))
    def rescan(*_):
        assert case != "initial-reused", "nonempty replacement token must refuse without a rescan"
        return rescanned
    monkeypatch.setattr(move_project, "_project_processes", rescan)
    with pytest.raises(move_project.MoveProjectError):
        move_project._validate_observed_processes(plan, {plan.pid: plan.agent}, post_suspend=True)
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
@pytest.mark.parametrize("field", ["missing", "empty"])
def test_project_seed_managed_fallback_uses_prepared_default_read_only(field: str, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"; agent = _create_project(source, tmp_path / "preset.json")
    _make_live_seed(agent); managed, marker = _prepared_runtime(tmp_path, monkeypatch, "match")
    if field == "empty":
        init = json.loads((agent / "init.json").read_text()); init["venv_path"] = ""; (agent / "init.json").write_text(json.dumps(init), encoding="utf-8")
    paths = (agent / "init.json", managed / "pyvenv.cfg", marker); before = tuple(map(_receipt, paths))
    for name, value in (("_initiating_pid", 101), ("_process_token", "stable-token"), ("_fresh", True), ("_lock_held", True)):
        monkeypatch.setattr(move_project, name, lambda *_, value=value: value)
    plan = move_project.preflight(agent, target)
    assert plan.workdirs == (agent,) and plan.resumed_runtime == managed / "bin/python" and plan.init == plan.rebased_init
    assert plan.init.get("venv_path", "") == "" and before == tuple(map(_receipt, paths)) and not target.exists() and not (agent / ".suspend").exists()
@pytest.mark.parametrize("value", [None, False, 0, [], {}, "relative", "   "], ids=["null", "false", "zero", "list", "object", "relative", "whitespace"])
def test_invalid_present_venv_values_refuse_before_selection_or_mutation(value, tmp_path: Path, monkeypatch):
    source, target, managed = tmp_path / "source", tmp_path / "target", tmp_path / "managed"; agent = _create_project(source, tmp_path / "preset.json")
    _make_live_seed(agent); managed.mkdir(); init = json.loads((agent / "init.json").read_text()); init["venv_path"] = value; (agent / "init.json").write_text(json.dumps(init), encoding="utf-8")
    marker = managed / ".lingtai-env.json"; marker.write_text("sentinel", encoding="utf-8"); cfg = managed / "pyvenv.cfg"; cfg.write_text("runtime", encoding="utf-8")
    paths = (agent / "init.json", cfg, marker); before = tuple(map(_receipt, paths)); monkeypatch.setattr(venv_resolve, "_DEFAULT_RUNTIME_DIR", managed); move_project._PROCESS_OBSERVATION_ENV = os.environ.copy()
    monkeypatch.setattr(move_project, "_probe", lambda *_: pytest.fail("runtime probe reached")); monkeypatch.setattr(move_project, "_initiating_pid", lambda *_: pytest.fail("process scan reached"))
    monkeypatch.setattr(move_project.subprocess, "Popen", lambda *_a, **_k: pytest.fail("supervisor reached"))
    with pytest.raises(move_project.MoveProjectError): move_project.preflight(agent, target)
    assert before == tuple(map(_receipt, paths)) and source.is_dir() and not target.exists() and not (agent / ".suspend").exists()
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
@pytest.mark.parametrize(("managed", "field", "status", "passes"), [
    (True, "fallback", "missing", False), (True, "fallback", "error", False), (True, "fallback", "mismatch", False), (True, "fallback", "match", True),
    (True, "explicit", "missing", False), (True, "explicit", "match", True), (True, "fallback", "policy-error-missing", False), (True, "fallback", "policy-error-match", True),
    (True, "fallback", "obsolete-missing", False), (False, "explicit", "missing", True), (False, "explicit", "error", True), (False, "explicit", "mismatch", False),
])
def test_owner_classified_marker_policy_is_read_only_before_suspend(managed: bool, field: str, status: str, passes: bool, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"; runtime, marker = _prepared_runtime(tmp_path, monkeypatch, status, managed)
    agent = _create_project(source, tmp_path / "preset.json"); _make_live_seed(agent); init = _write_init(agent, runtime); _write_identity(agent)
    if field == "fallback": init.pop("venv_path"); (agent / "init.json").write_text(json.dumps(init))
    paths = (agent / "init.json", runtime / "pyvenv.cfg", marker); before = tuple(map(_receipt, paths))
    for name, value in (("_initiating_pid", 101), ("_process_token", "stable-token"), ("_fresh", True), ("_lock_held", True)):
        monkeypatch.setattr(move_project, name, lambda *_, value=value: value)
    if passes: assert move_project.preflight(agent, target).resumed_runtime == runtime / "bin/python"
    else:
        with pytest.raises(move_project.MoveProjectError, match="marker"): move_project.preflight(agent, target)
    assert before == tuple(map(_receipt, paths)) and source.is_dir() and not target.exists() and not (agent / ".suspend").exists()
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
@pytest.mark.parametrize(("status", "passes"), [("missing", False), ("error", False), ("mismatch", False), ("match", True)])
def test_target_origin_managed_marker_must_match(status: str, passes: bool, tmp_path: Path, monkeypatch):
    runtime, marker = _prepared_runtime(tmp_path, monkeypatch, status); source, target = tmp_path / "old", tmp_path / "new"; agent = target / ".lingtai/initiator"
    (agent / "logs").mkdir(parents=True); init = _write_init(agent, runtime); _write_identity(agent); old_agent = source / ".lingtai/initiator"
    plan = SimpleNamespace(source=source, target=target, agent=old_agent, target_agent=agent,
        identity=("id", "temporary-true-name", "initiator"), init=init, rebased_init=init,
        resumed_runtime=runtime / "bin/python", environment=os.environ.copy(), workdirs=(old_agent,))
    paths = (agent / "init.json", runtime / "pyvenv.cfg", marker); before = tuple(map(_receipt, paths)); launched = []
    monkeypatch.setattr(move_project, "_project_processes", lambda root, *_: {303: agent} if root == target else {}); monkeypatch.setattr(move_project, "_fresh", lambda *_: True); monkeypatch.setattr(move_project, "_lock_held", lambda *_: True)
    def launch(*_a, **_k):
        launched.append(True); (agent / ".prompt").unlink(); (agent / ".agent.heartbeat").touch(); return SimpleNamespace(pid=303, returncode=None, poll=lambda: None)
    process_api = SimpleNamespace(run=subprocess.run, Popen=launch, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT, SubprocessError=subprocess.SubprocessError); monkeypatch.setattr(move_project, "subprocess", process_api)
    if passes: assert move_project._resume(plan, 3) == 303
    else:
        with pytest.raises(move_project.MoveProjectError, match="marker"): move_project._resume(plan, 3)
        assert not launched and not (agent / ".prompt").exists()
    assert before == tuple(map(_receipt, paths))
@pytest.mark.parametrize(("location", "name"), [("target", "target\nroot"), ("target", "target "), ("source", "source\nroot"), ("initiator", "initiator\t"), ("sibling", "sibling\x07root")])
def test_process_table_non_round_trip_paths_refuse_at_real_path_gate(location: str, name: str, tmp_path: Path):
    source = tmp_path / (name if location == "source" else "source"); agent = source / ".lingtai" / (name if location == "initiator" else "initiator")
    target = tmp_path / (name if location == "target" else "target"); agent.mkdir(parents=True)
    if location == "sibling":
        sibling = source / ".lingtai" / name; sibling.mkdir()
        for workdir in (agent, sibling): _write_init(workdir, tmp_path); _write_identity(workdir)
        action = lambda: move_project._agent_workdirs(source)
    else: action = lambda: move_project._roots(agent, target)
    with pytest.raises(move_project.MoveProjectError, match="process-table round-trip"): action()
    assert source.is_dir() and not target.exists() and not (agent / ".suspend").exists()
def test_process_table_path_gate_allows_interior_spaces_and_printable_unicode(tmp_path: Path):
    source, target = tmp_path / "source space 雪", tmp_path / "target space 山"; agent, sibling = source / ".lingtai/initiator space 月", source / ".lingtai/sibling space 光"
    agent.mkdir(parents=True); sibling.mkdir()
    for workdir in (agent, sibling): _write_init(workdir, tmp_path); _write_identity(workdir)
    assert move_project._roots(agent, target) == (source, target, agent) and set(move_project._agent_workdirs(source)) == {agent, sibling}
@pytest.mark.parametrize("case", ["absent", "unusable", "source-contained", "truthy-nonstring"] )
def test_missing_managed_default_failures_precede_mutation(case: str, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = _create_project(source, tmp_path / "preset.json"); _make_live_seed(agent)
    managed = (source / "runtime/venv") if case == "source-contained" else tmp_path / "home/.lingtai-tui/runtime/venv"
    if case not in {"absent", "truthy-nonstring"}: managed.mkdir(parents=True)
    if case == "truthy-nonstring":
        data = json.loads((agent / "init.json").read_text()); data["venv_path"] = {"bad": True}
        (agent / "init.json").write_text(json.dumps(data))
    monkeypatch.setattr(venv_resolve, "_DEFAULT_RUNTIME_DIR", managed)
    with pytest.raises(move_project.MoveProjectError): move_project.preflight(agent, target)
    assert not target.exists() and not (agent / ".suspend").exists()
    if case == "absent": assert not managed.exists()
def test_inside_project_venv_rebases_and_other_init_values_are_preserved(tmp_path: Path):
    source, target, venv = tmp_path / "source", tmp_path / "target", tmp_path / "source/runtime/venv"
    venv.mkdir(parents=True)
    original = {
        "venv_path": str(venv),
        "manifest": {"agent_name": "true-name", "provider": {"key": "value"}},
        "list": [1, False, None, {"text": "preserved-value"}],
        "number": 3.25,
    }
    untouched = deepcopy(original)
    rebased = move_project._rebased_init(original, source, target)
    assert original == untouched
    assert rebased == {**original, "venv_path": str(target / "runtime/venv")}
    external = tmp_path / "external-venv"
    external.mkdir()
    external_init = {**original, "venv_path": str(external)}
    assert move_project._rebased_init(external_init, source, target) == external_init
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_broken_symlink_target_is_refused(tmp_path: Path):
    agent, target = tmp_path / "source/.lingtai/initiator", tmp_path / "target-link"
    agent.mkdir(parents=True)
    target.symlink_to(tmp_path / "missing-target")
    with pytest.raises(move_project.MoveProjectError):
        move_project._roots(agent, target)
def test_handoff_detaches_supervisor_with_cwd_outside_source(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai" / "initiator"
    (agent / "logs").mkdir(parents=True)
    plan = _fake_plan(source, target, (agent,))
    launched = {}
    def fake_popen(command, **kwargs):
        launched.update(command=command, **kwargs)
        return SimpleNamespace(pid=303)
    _stub_supervision(monkeypatch, plan)
    monkeypatch.setattr(move_project, "_require_self_caller", lambda _: None)
    monkeypatch.setattr(move_project, "_fresh", lambda _: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda _: True)
    sent, parent_closed, marker_fds, marker_closed = [], [], set(), set()
    marker = agent / ".suspend"
    real_open, real_close = move_project.os.open, move_project.os.close
    def tracked_open(path, *args):
        fd = real_open(path, *args); marker_fds.update({fd} if Path(path) == marker else ()); return fd
    def tracked_close(fd):
        marker_closed.update({fd} if fd in marker_fds else ()); return real_close(fd)
    parent = SimpleNamespace(close=lambda: parent_closed.append(True), settimeout=lambda _: None,
        sendall=lambda data: sent.append((data, marker.is_file(), marker_fds <= marker_closed,
            stat.S_IMODE(marker.stat().st_mode) if marker.is_file() else None)), recv=lambda _: b"R")
    child_gate = SimpleNamespace(fileno=lambda: 9, close=lambda: None)
    monkeypatch.setattr(move_project.socket, "socketpair", lambda: (parent, child_gate))
    monkeypatch.setattr(move_project.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(move_project.os, "open", tracked_open); monkeypatch.setattr(move_project.os, "close", tracked_close)
    previous_umask = os.umask(0o777)
    try: assert move_project.handoff(agent, target, 30) == 0
    finally: os.umask(previous_umask)
    assert sent == [(b"P", False, True, None), (b"C", True, True, 0o600)] and parent_closed
    assert "--_supervise" in launched["command"] and "--_gate-fd" in launched["command"]
    assert not any("agent-id" in value or "agent-name" in value for value in launched["command"])
    assert launched["cwd"] == target.parent and not move_project._within(launched["cwd"], source)
    assert launched["start_new_session"] is True and len(launched["pass_fds"]) == 1
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
@pytest.mark.parametrize("target_name", ["target-project", "target 雪 project"])
def test_real_agent_handoff_moves_project_and_relaunches_only_initiator(target_name: str, tmp_path: Path):
    source, target, home = tmp_path / "source-project", tmp_path / target_name, tmp_path / "home"
    network_barrier = socket.socket()
    network_barrier.bind(("127.0.0.1", 0))  # test-owned, non-listening loopback refusal
    base_url = f"http://127.0.0.1:{network_barrier.getsockname()[1]}"
    agent = _create_project(source, tmp_path / "preset.json", base_url)
    human, sibling = source / ".lingtai/human", source / ".lingtai/stopped-sibling"
    sibling.mkdir(); (sibling / "never-started").write_text("stays stopped", encoding="utf-8")
    managed = home / ".lingtai-tui/runtime/venv"
    subprocess.run([venv_resolve._find_python(), "-m", "venv", "--copies", "--without-pip",
        "--system-site-packages", str(managed)], check=True, capture_output=True, text=True, timeout=60)
    venv_resolve._write_env_marker(managed)
    before_init = json.loads((agent / "init.json").read_text(encoding="utf-8"))
    marker = managed / ".lingtai-env.json"; stable_paths = (agent / "init.json", managed / "pyvenv.cfg", marker)
    before_runtime = (managed.stat().st_ino, tuple(map(_receipt, stable_paths)))
    _write_init(sibling, managed, base_url); _write_identity(sibling, "stopped-id")
    env = os.environ.copy(); env["HOME"] = str(home)
    sites = [path for key in ("purelib", "platlib") if (path := sysconfig.get_paths().get(key))]
    boot_path = source / "boot-path"; boot_path.mkdir()
    paths = [str(ROOT / "src"), str(boot_path), *sites, *env.get("PYTHONPATH", "").split(os.pathsep)]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(filter(None, paths)))
    env["VIRTUAL_ENV"] = str(managed)
    boot_log = tmp_path / "initial-boot.log"
    stream = boot_log.open("w", encoding="utf-8")
    trigger, receipt = tmp_path / "invoke-handoff", tmp_path / "handoff-result.json"
    wrapper = """import json,os,subprocess,sys,threading,time;from pathlib import Path
agent,script,target,trigger,receipt=sys.argv[1:6]
def invoke():
    while not (Path(agent,'.agent.heartbeat').is_file() and Path(trigger).is_file()): time.sleep(.05)
    result=subprocess.run([sys.executable,script,'--agent-dir',agent,'--to',target,'--timeout','20'],cwd=agent,env=os.environ.copy(),capture_output=True,text=True,timeout=25)
    Path(receipt).write_text(json.dumps({'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}),encoding='utf-8')
thread=threading.Thread(target=invoke); thread.start()
from lingtai.cli import run; run(Path(agent)); thread.join()
"""
    command = [str(managed / "bin/python"), "-c", wrapper, str(agent), str(SCRIPT), str(target),
               str(trigger), str(receipt), "-m", "lingtai", "run", str(agent)]
    initial = subprocess.Popen(command, cwd=agent, env=env, stdin=subprocess.DEVNULL,
                               stdout=stream, stderr=subprocess.STDOUT)
    supervisor_pid = None
    owned = {initial.pid}
    target_agent, target_sibling = target / ".lingtai" / agent.name, target / ".lingtai" / sibling.name
    target_human = target / ".lingtai/human"
    try:
        for record in (".agent.heartbeat", ".agent.lock", ".agent.json"):
            assert _wait_until(lambda record=record: (agent / record).exists()), boot_log.read_text()
        before_identity = json.loads((agent / ".agent.json").read_text(encoding="utf-8"))
        old_heartbeat = (agent / ".agent.heartbeat").stat().st_mtime
        started = time.monotonic()
        trigger.touch()
        assert _wait_until(receipt.is_file, 25), boot_log.read_text()
        result = SimpleNamespace(**json.loads(receipt.read_text(encoding="utf-8")))
        assert result.returncode == 0, result.stdout + result.stderr + boot_log.read_text()
        assert time.monotonic() - started < 10, "outer handoff must return before lifecycle completion"
        match = re.search(r"supervisor started \((\d+)\)", result.stdout)
        assert match, result.stdout
        supervisor_pid = int(match.group(1))
        def moved_and_live():
            heartbeat = target_agent / ".agent.heartbeat"
            return target_agent.exists() and heartbeat.exists() and heartbeat.stat().st_mtime > old_heartbeat and bool(
                _agents_at(target_agent)
            )
        logs = (agent / "logs/move-project.log", target_agent / "logs/move-project.log", boot_log)
        assert _wait_until(moved_and_live, 35), "\n".join(
            path.read_text(encoding="utf-8") for path in logs if path.is_file()
        )
        relaunched = _agents_at(target_agent)
        assert len(relaunched) == 1 and initial.pid not in relaunched
        owned.update(relaunched)
        assert _wait_until(lambda: not _supervisor_at(supervisor_pid, source), 20)
        after_identity = json.loads((target_agent / ".agent.json").read_text(encoding="utf-8"))
        after_init = json.loads((target_agent / "init.json").read_text(encoding="utf-8"))
        assert not source.exists() and target.is_dir()
        assert after_init == before_init and "venv_path" not in after_init
        after_paths = (target_agent / "init.json", managed / "pyvenv.cfg", marker)
        assert before_runtime == (managed.stat().st_ino, tuple(map(_receipt, after_paths)))
        assert json.loads((target_human / ".agent.json").read_text())["address"] == "human"
        assert not (target_human / "init.json").exists() and not _agents_at(target_human)
        assert all(after_identity[key] == before_identity[key] for key in ("agent_id", "agent_name", "address"))
        assert before_identity["address"] == agent.name
        assert (target_sibling / "never-started").read_text(encoding="utf-8") == "stays stopped"
        assert (target_sibling / ".agent.lock").is_file() and not _agents_at(target_sibling)
        expected_prompt = _continuation_prompt(source, target)
        def continuation_received():
            events = _events(target_agent)
            kinds = {event.get("type") for event in events}
            return (not (target_agent / ".prompt").exists() and {"prompt_received", "wake"} <= kinds
                    and any(event.get("type") == "text_input"
                            and event.get("text", "").endswith(expected_prompt) for event in events))
        assert _wait_until(continuation_received, 10), "relaunch stayed asleep without the exact continuation prompt"
        print(f"wake-receipt endpoint={base_url} mode=non-listening prompt_received+wake+continuation_suffix=true")
    finally:
        network_barrier.close()
        stream.close()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            for candidate in (agent, target_agent):
                if candidate.is_dir():
                    owned.update(_agents_at(candidate))
                    (candidate / ".suspend").touch()
            remaining = {pid for candidate in (agent, target_agent) for pid in _agents_at(candidate) if pid in owned}
            if not remaining and not _supervisor_at(supervisor_pid, source): break
            time.sleep(0.1)
        remaining = {pid for candidate in (agent, target_agent) for pid in _agents_at(candidate) if pid in owned}
        assert not remaining, f"test-owned Agent PIDs failed cooperative shutdown: {remaining}"
        assert not _supervisor_at(supervisor_pid, source)
        initial.wait(timeout=5)
def _lock_probe(path: Path, phase: str) -> str:
    code = """import fcntl, sys
stream = open(sys.argv[1], 'a+')
try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
except (BlockingIOError, OSError): print('blocked')
else: print('acquired')
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, stderr
    print(f"sibling-lock-probe phase={phase} pid={child.pid} result={stdout.strip()}")
    return stdout.strip()
@pytest.mark.parametrize(("marker_kind", "commit", "succeeds"), [
    ("consumed", b"C", True), ("eof", b"", False), ("wrong", b"X", False),
    ("trailing", b"CX", False), ("regular", b"C", True), ("symlink", b"C", False),
    ("directory", b"C", False), ("fifo", b"C", False),
], ids=lambda value: value if isinstance(value, str) else None)
def test_supervisor_commit_protocol_classifies_consumed_and_present_markers(
    marker_kind: str, commit: bytes, succeeds: bool, tmp_path: Path, monkeypatch, capsys,
):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai/initiator"; agent.mkdir(parents=True)
    plan = _fake_plan(source, target, (agent,)); marker = agent / ".suspend"
    if marker_kind == "regular": marker.write_text("", encoding="utf-8")
    elif marker_kind == "symlink": marker.symlink_to(tmp_path / "missing")
    elif marker_kind == "directory": marker.mkdir()
    elif marker_kind == "fifo": os.mkfifo(marker)
    _stub_supervision(monkeypatch, plan); reached = []
    monkeypatch.setattr(move_project, "_wait_stopped", lambda *_: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(move_project, "_revalidate_cutover", lambda _: reached.append("cutover"))
    monkeypatch.setattr(move_project, "_rename_no_replace", lambda *_: reached.append("rename"))
    monkeypatch.setattr(move_project, "_resume", lambda *_: reached.append("resume") or 303)
    peer, child = socket.socketpair()
    if marker_kind == "consumed":
        results = []; thread = threading.Thread(target=lambda: results.append(
            move_project.supervise(agent, target, 5, 101, "stable-token", child.detach())), daemon=True)
        thread.start(); peer.sendall(b"P"); assert peer.recv(1) == b"R"
        os.close(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600))
        peer.sendall(b"C"); marker.unlink(); peer.shutdown(socket.SHUT_WR); thread.join(5)
        assert not thread.is_alive(); result = results[0]; peer.close(); child.close()
    else:
        peer.sendall(b"P" + commit); peer.shutdown(socket.SHUT_WR); result = move_project.supervise(
            agent, target, 5, 101, "stable-token", child.detach()); peer.close(); child.close()
    assert (result == 0) is succeeds and (("rename" in reached and "resume" in reached) is succeeds)
    if not succeeds: assert "source is authoritative" in capsys.readouterr().err
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_sibling_lease_fences_final_scan_through_target_liveness(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent, sibling = source / ".lingtai" / "initiator", source / ".lingtai" / "sibling"
    agent.mkdir(parents=True)
    sibling.mkdir()
    plan = _fake_plan(source, target, (agent, sibling))
    observed = []
    _stub_supervision(monkeypatch, plan)
    monkeypatch.setattr(move_project, "_fresh", lambda _: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda _: True)
    monkeypatch.setattr(move_project, "_wait_stopped", lambda *_: move_project._try_lock(agent))
    monkeypatch.setattr(move_project, "_revalidate_cutover", lambda _: None)
    monkeypatch.setattr(move_project, "_rename_no_replace", lambda *_: observed.append(_lock_probe(sibling / ".agent.lock", "cutover")))
    monkeypatch.setattr(move_project, "_resume", lambda *_: observed.append(_lock_probe(sibling / ".agent.lock", "liveness")) or 303)
    (agent / ".suspend").touch()
    assert move_project.supervise(agent, target, 5, 101, "stable-token") == 0
    assert observed == ["blocked", "blocked"]
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_launchable_malformed_sibling_cannot_take_lease_after_final_scan(tmp_path: Path):
    source, target = tmp_path / "source", tmp_path / "target"
    agent, sibling = source / ".lingtai" / "initiator", source / ".lingtai" / "sibling"
    for workdir in (agent, sibling):
        workdir.mkdir(parents=True)
        _write_init(workdir, tmp_path)
    _write_identity(agent)
    (sibling / ".agent.json").write_text("{", encoding="utf-8")
    plan = _fake_plan(source, target, (agent,))
    leases = []
    try:
        leases = move_project._fence_siblings(plan)
        outcome = _lock_probe(sibling / ".agent.lock", "post-final-scan-launch")
    except move_project.MoveProjectError:
        outcome = "refused-before-suspend"
    finally:
        for lease in leases:
            lease.close()
    assert outcome in {"blocked", "refused-before-suspend"} and not (agent / ".suspend").exists()
def test_replacement_at_validation_to_suspend_boundary_never_receives_marker(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai" / "initiator"
    (agent / "logs").mkdir(parents=True)
    plan = _fake_plan(source, target, (agent,))
    _stub_supervision(monkeypatch, plan)
    monkeypatch.setattr(move_project, "_fresh", lambda _: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda _: True)
    ownership_checks = []
    def require_owner(_):
        ownership_checks.append(True)
        if len(ownership_checks) == 2:
            raise move_project.MoveProjectError("initiating process incarnation changed")
    gate = SimpleNamespace(fileno=lambda: 9, close=lambda: None, settimeout=lambda _: None,
                           sendall=lambda _: None, recv=lambda _: b"R")
    monkeypatch.setattr(move_project, "_require_self_caller", require_owner)
    monkeypatch.setattr(move_project.socket, "socketpair", lambda: (gate, gate))
    monkeypatch.setattr(move_project.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=303))
    assert move_project.handoff(agent, target, 5) == 1
    assert len(ownership_checks) == 2 and not (agent / ".suspend").exists()
@pytest.mark.parametrize("failure", ["exclusive-create", "mode-finalization"])
def test_handoff_sends_no_commit_when_marker_finalization_fails(failure: str, tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai/initiator"; (agent / "logs").mkdir(parents=True)
    if failure == "exclusive-create": (agent / ".suspend").mkdir()
    else:
        monkeypatch.setattr(move_project.os, "fchmod",
            lambda *_: (_ for _ in ()).throw(OSError("mode finalization failed")))
    plan = _fake_plan(source, target, (agent,)); sent = []
    _stub_supervision(monkeypatch, plan)
    monkeypatch.setattr(move_project, "_require_self_caller", lambda _: None)
    monkeypatch.setattr(move_project, "_fresh", lambda _: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda _: True)
    parent = SimpleNamespace(close=lambda: None, settimeout=lambda _: None,
                             sendall=lambda data: sent.append(data), recv=lambda _: b"R")
    child_gate = SimpleNamespace(fileno=lambda: 9, close=lambda: None)
    monkeypatch.setattr(move_project.socket, "socketpair", lambda: (parent, child_gate))
    monkeypatch.setattr(move_project.subprocess, "Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=303))
    assert move_project.handoff(agent, target, 5) == 1 and sent == [b"P"]
@pytest.mark.parametrize("kind", ["dotdot", "symlink"])
def test_noncanonical_venv_is_refused(kind: str, tmp_path: Path):
    source, target, external = tmp_path / "source", tmp_path / "target", tmp_path / "external-venv"
    source.mkdir()
    external.mkdir()
    if kind == "dotdot":
        part = tmp_path / "path-part"
        part.mkdir()
        configured = part / ".." / external.name
    else:
        configured = tmp_path / "venv-link"
        configured.symlink_to(external, target_is_directory=True)
    with pytest.raises(move_project.MoveProjectError, match="canonical.*non-symlink"):
        move_project._rebased_init({"venv_path": str(configured)}, source, target)
def test_configured_venv_marker_mismatch_refuses_before_suspend_or_rename(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent, venv = source / ".lingtai" / "initiator", tmp_path / "venv"
    (agent / "logs").mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", "--system-site-packages", str(venv)],
        check=True, capture_output=True, text=True, timeout=60)
    _write_init(agent, venv)
    _write_identity(agent)
    marker = venv_resolve._current_process_env_marker()
    marker["os"] = "other-os"
    marker_path = venv / ".lingtai-env.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))
    with pytest.raises(move_project.MoveProjectError, match="environment marker mismatch"):
        move_project.preflight(agent, target)
    assert json.loads(marker_path.read_text(encoding="utf-8")) == marker
    assert source.is_dir() and not target.exists() and not (agent / ".suspend").exists()
def test_managed_default_venv_policy_mismatch_refuses_before_suspend_or_rename(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai" / "initiator"
    managed = tmp_path / ".lingtai-tui/runtime/venv"
    (agent / "logs").mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", "--system-site-packages", str(managed)],
        check=True, capture_output=True, text=True, timeout=60)
    init = _write_init(agent, managed)
    init.pop("venv_path")
    (agent / "init.json").write_text(json.dumps(init), encoding="utf-8")
    _write_identity(agent)
    marker = venv_resolve._current_process_env_marker()
    marker["python"].update(version_major=3, version_minor=14)
    marker_path = managed / ".lingtai-env.json"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    seam = tmp_path / "policy-seam"
    seam.mkdir()
    (seam / "sitecustomize.py").write_text("""from lingtai import venv_resolve as v
m=v._current_process_env_marker();m['python'].update(version_major=3,version_minor=14)
v._current_venv_env_marker=lambda _path:m
v._python_selection_policy=lambda:v._PythonSelectionPolicy(v.sys.platform,v.platform.machine(),None,None,(3,11),(3,13),())
""", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(venv_resolve, "_DEFAULT_RUNTIME_DIR", managed)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(seam), str(ROOT / "src"))))
    monkeypatch.setattr(move_project, "_initiating_pid", lambda *_: 101)
    monkeypatch.setattr(move_project, "_process_token", lambda *_: "stable-token")
    monkeypatch.setattr(move_project, "_fresh", lambda *_: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda *_: True)
    with pytest.raises(move_project.MoveProjectError, match="outside supported range 3.11-3.13"):
        move_project.preflight(agent, target)
    assert json.loads(marker_path.read_text(encoding="utf-8")) == marker
    assert source.is_dir() and not target.exists() and not (agent / ".suspend").exists()
def test_target_probe_and_relaunch_share_old_root_free_environment(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    target_agent = target / ".lingtai" / "initiator"
    (target_agent / "logs").mkdir(parents=True)
    (target_agent / ".agent.heartbeat").touch()
    names = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    for name in names:
        monkeypatch.setenv(name, str(source / name.lower()))
    environment = {**os.environ, **{name: str(target / name.lower()) for name in names}, "LC_ALL": "agent-ambient"}
    monkeypatch.setattr(move_project, "_PROCESS_OBSERVATION_ENV", {**environment, "LC_ALL": "verified-ps"}, raising=False)
    plan = SimpleNamespace(source=source, target=target, target_agent=target_agent,
        resumed_runtime=target / "venv/bin/python", environment=environment, workdirs=(source / ".lingtai/initiator",),
        identity=("id", "name", "initiator"), init={}, rebased_init={})
    probes, launched = [], {}
    monkeypatch.setattr(move_project, "_identity", lambda _: plan.identity)
    monkeypatch.setattr(move_project, "_probe", lambda runtime, cwd, env: probes.append((runtime, cwd, env)) or (runtime, cwd))
    monkeypatch.setattr(move_project, "_project_processes",
                        lambda root, *_: {} if root == source else {303: target_agent})
    monkeypatch.setattr(move_project, "_fresh", lambda _: True)
    monkeypatch.setattr(move_project, "_lock_held", lambda _: True)
    monkeypatch.setattr(move_project.time, "time", lambda: 0)
    def fake_popen(command, **kwargs):
        launched.update(command=command, **kwargs)
        (target_agent / ".prompt").unlink()  # model the existing lifecycle consumer
        return SimpleNamespace(pid=303, returncode=None, poll=lambda: None)
    monkeypatch.setattr(move_project.subprocess, "Popen", fake_popen)
    assert move_project._resume(plan, 5) == 303
    assert probes == [(plan.resumed_runtime, target_agent, environment)] and launched["env"] is environment
    assert launched["env"]["LC_ALL"] == "agent-ambient" and all(str(source) not in environment[name] for name in names)
    old_agent = source / ".lingtai" / "late-sibling"
    monkeypatch.setattr(move_project, "_project_processes",
                        lambda root, *_: {404: old_agent} if root == source else {303: target_agent})
    with pytest.raises(move_project.MoveProjectError, match="old source"):
        move_project._resume(plan, 5)
def test_python_boot_environment_rebases_only_canonical_old_root_paths(tmp_path: Path, monkeypatch):
    source, target, external = tmp_path / "source", tmp_path / "target", tmp_path / "external"
    names = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")
    old = {name: source / name.lower() for name in names}
    for path in (*old.values(), external):
        path.mkdir(parents=True)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(old["PYTHONPATH"]), str(external))))
    for name in names[1:]:
        monkeypatch.setenv(name, str(old[name]))
    monkeypatch.setenv("UNRELATED_VALUE", str(source))
    environment, roots = move_project._relaunch_environment(source, target)
    assert environment["PYTHONPATH"] == os.pathsep.join((str(target / "pythonpath"), str(external)))
    assert {name: environment[name] for name in names[1:]} == {
        "PYTHONHOME": str(target / "pythonhome"), "VIRTUAL_ENV": str(target / "virtual_env")}
    assert environment["UNRELATED_VALUE"] == str(source) and set(roots) == set(old.values())
    (source / "part").mkdir()
    monkeypatch.setenv("PYTHONHOME", str(source / "part/../pythonhome"))
    with pytest.raises(move_project.MoveProjectError, match="noncanonical old-root"):
        move_project._relaunch_environment(source, target)
def test_stable_foreign_caller_cannot_start_self_handoff(tmp_path: Path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    agent = source / ".lingtai" / "initiator"
    (agent / "logs").mkdir(parents=True)
    plan = _fake_plan(source, target, (agent,))
    monkeypatch.setattr(move_project, "preflight", lambda *_: plan)
    with pytest.raises(move_project.MoveProjectError, match="not descended"):
        move_project._require_self_caller(plan)
    assert move_project.handoff(agent, target, 5) == 1
    assert not (agent / ".suspend").exists()
def test_nonfinite_timeout_refuses_public_and_direct_paths_before_preflight(tmp_path: Path, monkeypatch):
    agent, target = tmp_path / "agent", tmp_path / "target"
    monkeypatch.setattr(move_project, "preflight", lambda *_: pytest.fail("preflight reached"))
    for text in ("nan", "inf"):
        assert move_project.handoff(agent, target, float(text)) == 1
        with pytest.raises(SystemExit):
            move_project.main(["--agent-dir", str(agent), "--to", str(target), "--timeout", text])
    assert not (agent / ".suspend").exists()
@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_init_write_preserves_mode_and_fsyncs_file_then_parent(tmp_path: Path, monkeypatch):
    path = tmp_path / "init.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o640)
    real_fsync, observed = os.fsync, []
    def record(fd):
        observed.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        real_fsync(fd)
    monkeypatch.setattr(move_project.os, "fsync", record)
    move_project._write_json(path, {"semantic": [1, True, None]})
    assert json.loads(path.read_text(encoding="utf-8")) == {"semantic": [1, True, None]}
    assert stat.S_IMODE(path.stat().st_mode) == 0o640 and observed == [False, True]
    prompt = tmp_path / ".prompt"
    move_project._write_exclusive_prompt(prompt, "continue")
    with pytest.raises(FileExistsError): move_project._write_exclusive_prompt(prompt, "overwrite")
    assert prompt.read_text() == "continue\n" and stat.S_ISREG(prompt.stat().st_mode)
    assert stat.S_IMODE(prompt.stat().st_mode) == 0o600 and observed == [False, True, False, True]
