"""Focused safety checks for the POSIX name-change helper."""
from __future__ import annotations

import errno
import importlib.util
import json
import os
import stat
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "src/lingtai/intrinsic_skills/system-manual/reference/migration-guide/scripts/change_name.py"
spec = importlib.util.spec_from_file_location("change_name", SCRIPT)
change_name = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = change_name
assert spec.loader is not None
spec.loader.exec_module(change_name)


def _site_packages(venv: Path) -> Path:
    return venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"


def test_missing_old_never_writes_existing_destination(tmp_path: Path):
    destination = tmp_path / "new"
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_text("unchanged", encoding="utf-8")
    assert change_name.main([str(tmp_path / "missing"), "new"]) == 1
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_process_scan_failure_is_not_absence(monkeypatch):
    monkeypatch.setattr(
        change_name.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(["ps"], 2)),
    )
    with pytest.raises(change_name.ChangeNameError, match="refusing to infer absence"):
        change_name._processes(Path("/tmp/agent"))


@pytest.mark.skipif(os.name != "posix", reason="supported POSIX preflight")
def test_unsupported_posix_refuses_before_agent_inspection_or_suspension(
    tmp_path: Path, monkeypatch
):
    old = tmp_path / "old"
    old.mkdir()
    monkeypatch.setattr(change_name.sys, "platform", "freebsd-test")
    with pytest.raises(change_name.ChangeNameError, match="no supported no-replace rename"):
        change_name.preflight(old, "new")
    assert not (old / ".suspend").exists()
    assert not (old / change_name.INCOMPLETE_MARKER).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_no_replace_rename_keeps_a_racing_destination(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    with pytest.raises(OSError):
        change_name._rename_no_replace(old, new)
    assert old.is_dir() and new.is_dir()


def test_runtime_probe_ignores_inherited_pythonpath_and_does_not_create_pyc(
    tmp_path: Path, monkeypatch
):
    venv = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(venv)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    site = _site_packages(venv)
    package = site / "lingtai"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    masked = tmp_path / "masked"
    (masked / "lingtai").mkdir(parents=True)
    (masked / "lingtai" / "__init__.py").write_text(
        "raise RuntimeError('inherited PYTHONPATH was used')\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(masked))
    assert change_name._probe(venv / "bin" / "python", tmp_path) == package / "__init__.py"
    assert not (package / "__pycache__").exists()
    assert not (masked / "lingtai" / "__pycache__").exists()


def test_handoff_starts_authoritative_supervisor_with_clean_python_env(
    tmp_path: Path, monkeypatch
):
    old = tmp_path / "old"
    old.mkdir()
    plan = change_name.Plan(
        old=old, new=tmp_path / "new", agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=Path(change_name.__file__),
    )
    captured = {}

    class Child:
        pid = 123

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return Child()

    monkeypatch.setattr(change_name, "preflight", lambda old_arg, new_name: plan)
    monkeypatch.setattr(change_name.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "decoy"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "wrong-home"))
    assert change_name.handoff(str(old), "new", 1) == 0
    assert "PYTHONPATH" not in captured["env"]
    assert "PYTHONHOME" not in captured["env"]
    assert captured["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"


@pytest.mark.skipif(os.name != "posix", reason="POSIX v1")
def test_target_relocation_is_structured_mode_preserving_and_narrow(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    venv = old / "runtime" / "venv"
    bin_dir = venv / "bin"
    site = _site_packages(venv)
    source = old / "runtime" / "source"
    external = tmp_path / "external"
    bin_dir.mkdir(parents=True)
    site.mkdir(parents=True)
    source.mkdir(parents=True)
    external.mkdir()

    python = bin_dir / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    launcher = bin_dir / "lingtai-agent"
    launcher.write_text(f"#!{python}\nprint('launcher')\n", encoding="utf-8")
    launcher.chmod(0o751)
    external_launcher = bin_dir / "external-tool"
    external_launcher.write_text(f"#!{sys.executable}\nprint('external')\n", encoding="utf-8")
    external_launcher.chmod(0o755)
    non_executable = bin_dir / "example-script"
    non_executable.write_text(f"#!{python}\nexample = True\n", encoding="utf-8")
    non_executable.chmod(0o640)

    pth = site / "editable.pth"
    pth.write_text(
        f"{source}\n{external}\nimport marker; marker({str(old)!r})\n",
        encoding="utf-8",
    )
    direct = site / "lingtai-1.dist-info" / "direct_url.json"
    direct.parent.mkdir()
    encoded_source_url = urllib.parse.urlunsplit((
        "file", "LOCALHOST",
        urllib.parse.quote(str(source), safe="/").replace("/source", "/%73ource"),
        "download=1", "editable",
    ))
    direct.write_text(json.dumps({
        "dir_info": {"editable": True, "note": str(old)},
        "url": encoded_source_url,
    }), encoding="utf-8")
    external_direct = site / "other-1.dist-info" / "direct_url.json"
    external_direct.parent.mkdir()
    external_direct.write_text(json.dumps({
        "dir_info": {"editable": True}, "url": external.as_uri(),
    }), encoding="utf-8")

    registry_records = [
        {
            "name": "local", "summary": "local", "transport": "stdio",
            "command": str(old / "mcp" / "server"),
            "args": [str(old / "must-not-change")], "source": "human",
            "extra": {"old": str(old)},
        },
        {
            "name": "remote", "summary": "remote", "transport": "http",
            "url": "https://example.invalid/mcp", "command": str(old / "ignored"),
            "source": "human",
        },
        {
            "name": "external", "summary": "external", "transport": "stdio",
            "command": str(external / "server"), "args": [], "source": "human",
        },
    ]
    registry = old / "mcp_registry.jsonl"
    registry.write_text(
        "\n".join(json.dumps(record) for record in registry_records) + "\n",
        encoding="utf-8",
    )
    registry.chmod(0o640)

    rewrites = change_name._plan_runtime_rewrites(
        old, new, venv, source / "lingtai" / "__init__.py"
    )
    registry_rewrite, registry_sources = change_name._plan_registry_rewrite(old, new)
    assert registry_rewrite is not None
    assert registry_sources == {"local": "human", "remote": "human", "external": "human"}
    plan = change_name.Plan(
        old=old, new=new, agent_id="agent-id", agent_name="agent-name",
        runtime=python, resumed_runtime=new / "runtime" / "venv" / "bin" / "python",
        expected_import_source=new / "runtime" / "source" / "lingtai" / "__init__.py",
        rewrites=tuple([*rewrites, registry_rewrite]),
    )

    old.rename(new)
    change_name._apply_rewrites(plan)

    new_python = new / "runtime" / "venv" / "bin" / "python"
    new_launcher = new / "runtime" / "venv" / "bin" / "lingtai-agent"
    assert new_launcher.read_text(encoding="utf-8").splitlines()[0] == f"#!{new_python}"
    assert stat.S_IMODE(new_launcher.stat().st_mode) == 0o751
    assert (new / "runtime" / "venv" / "bin" / "external-tool").read_text(
        encoding="utf-8"
    ).splitlines()[0] == f"#!{sys.executable}"
    assert (new / "runtime" / "venv" / "bin" / "example-script").read_text(
        encoding="utf-8"
    ).splitlines()[0] == f"#!{python}"

    pth_lines = (new / pth.relative_to(old)).read_text(encoding="utf-8").splitlines()
    assert pth_lines == [
        str(new / "runtime" / "source"),
        str(external),
        f"import marker; marker({str(old)!r})",
    ]
    direct_data = json.loads((new / direct.relative_to(old)).read_text(encoding="utf-8"))
    moved_url = urllib.parse.urlsplit(direct_data["url"])
    assert urllib.parse.unquote(moved_url.path) == str(new / "runtime" / "source")
    assert (moved_url.netloc, moved_url.query, moved_url.fragment) == (
        "LOCALHOST", "download=1", "editable",
    )
    assert direct_data["dir_info"] == {"editable": True, "note": str(old)}
    assert json.loads((new / external_direct.relative_to(old)).read_text(encoding="utf-8"))[
        "url"
    ] == external.as_uri()

    moved_records = [
        json.loads(line)
        for line in (new / "mcp_registry.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert moved_records[0]["command"] == str(new / "mcp" / "server")
    assert moved_records[0]["args"] == [str(old / "must-not-change")]
    assert moved_records[0]["extra"] == {"old": str(old)}
    assert moved_records[1]["command"] == str(old / "ignored")
    assert moved_records[2]["command"] == str(external / "server")
    assert stat.S_IMODE((new / "mcp_registry.jsonl").stat().st_mode) == 0o640


def test_init_mcp_rebases_only_supported_stdio_command_fields(tmp_path: Path):
    old, new, external = tmp_path / "old", tmp_path / "new", tmp_path / "external"
    init = {
        "mcp": {
            "explicit": {
                "type": "stdio", "command": str(old / "bin" / "server"),
                "args": [str(old / "argument")], "env": {"ROOT": str(old)},
            },
            "default": {"command": str(old / "bin" / "other")},
            "http": {
                "type": "http", "url": "https://example.invalid/mcp",
                "command": str(old / "not-a-stdio-command"),
            },
            "external": {"type": "stdio", "command": str(external / "server")},
            "curated-activation": {"env": {"ACCOUNT": "unchanged"}},
        },
        "other": {"old": str(old)},
    }
    assert change_name._rebase_init_mcp(init, old, new)
    assert init["mcp"]["explicit"] == {
        "type": "stdio", "command": str(new / "bin" / "server"),
        "args": [str(old / "argument")], "env": {"ROOT": str(old)},
    }
    assert init["mcp"]["default"]["command"] == str(new / "bin" / "other")
    assert init["mcp"]["http"]["command"] == str(old / "not-a-stdio-command")
    assert init["mcp"]["external"]["command"] == str(external / "server")
    assert init["mcp"]["curated-activation"] == {"env": {"ACCOUNT": "unchanged"}}
    assert init["other"] == {"old": str(old)}


@pytest.mark.parametrize(
    "mcp",
    [
        [],
        {"broken": "not-an-object"},
        {"broken": {"type": "pipe", "command": "/tmp/old/server"}},
        {"broken": {"type": "stdio", "command": 3}},
    ],
)
def test_malformed_init_mcp_fails_closed(tmp_path: Path, mcp):
    with pytest.raises(change_name.ChangeNameError, match="init.json"):
        change_name._rebase_init_mcp({"mcp": mcp}, tmp_path / "old", tmp_path / "new")


@pytest.mark.parametrize(
    "content",
    [
        "{not-json}\n",
        json.dumps({
            "name": "broken", "summary": "broken", "transport": "stdio",
            "command": 3, "source": "human",
        }) + "\n",
        "\n".join([
            json.dumps({
                "name": "same", "summary": "one", "transport": "stdio",
                "command": "python", "source": "human",
            }),
            json.dumps({
                "name": "same", "summary": "two", "transport": "stdio",
                "command": "python", "source": "human",
            }),
        ]) + "\n",
    ],
)
def test_malformed_registry_fails_closed(tmp_path: Path, content: str):
    old = tmp_path / "old"
    old.mkdir()
    (old / "mcp_registry.jsonl").write_text(content, encoding="utf-8")
    with pytest.raises(change_name.ChangeNameError, match="mcp_registry.jsonl"):
        change_name._plan_registry_rewrite(old, tmp_path / "new")


def test_external_venv_is_not_planned_for_runtime_rewrite(tmp_path: Path, monkeypatch):
    old, external = tmp_path / "old", tmp_path / "external-venv"
    old.mkdir()
    (external / "bin").mkdir(parents=True)
    runtime = external / "bin" / "python"
    runtime.write_text("external\n", encoding="utf-8")
    runtime.chmod(0o755)
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "true-name", "address": "old",
    }), encoding="utf-8")
    (old / "init.json").write_text(json.dumps({
        "manifest": {"agent_name": "true-name"}, "venv_path": str(external),
    }), encoding="utf-8")
    monkeypatch.setattr(change_name, "_probe", lambda runtime, cwd: external / "lingtai.py")
    monkeypatch.setattr(change_name, "_fresh", lambda root: True)
    monkeypatch.setattr(change_name, "_processes", lambda root: [123])
    monkeypatch.setattr(change_name, "_lock_held", lambda root: True)

    plan = change_name.preflight(old, "new")
    assert plan.resumed_runtime == runtime
    assert not plan.rewrites


def test_changed_target_file_fails_loud_without_overwrite(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    path = old / "binding.pth"
    path.write_text(f"{old}\n", encoding="utf-8")
    rewrite = change_name._rewrite(path, old, "editable .pth", f"{new}\n".encode())
    assert rewrite is not None
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=Path(change_name.__file__), rewrites=(rewrite,),
    )
    old.rename(new)
    target = new / "binding.pth"
    target.write_text("changed after preflight\n", encoding="utf-8")
    with pytest.raises(change_name.ChangeNameError, match="target relocation stopped after 0/1"):
        change_name._apply_rewrites(plan)
    assert new.is_dir() and target.read_text(encoding="utf-8") == "changed after preflight\n"


def test_curated_registry_source_preserves_all_legacy_init_launcher_fields(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    record = {
        "name": "telegram", "summary": "curated", "transport": "stdio",
        "command": str(old / "legacy-registry-launcher"),
        "args": [str(old / "registry-arg")], "source": "lingtai-curated",
    }
    (old / "mcp_registry.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    rewrite, sources = change_name._plan_registry_rewrite(old, new)
    assert rewrite is not None
    assert sources == {"telegram": "lingtai-curated"}

    legacy = {
        "type": "stdio", "command": str(old / "legacy-init-launcher"),
        "args": [str(old / "init-arg")],
        "env": {"PYTHONPATH": str(old / "legacy-source"), "TOKEN": "opaque"},
        "prompt": f"do not rewrite {old}",
    }
    init = {"mcp": {"telegram": dict(legacy)}}
    assert not change_name._rebase_init_mcp(init, old, new, sources)
    assert init["mcp"]["telegram"] == legacy
    moved = json.loads(rewrite.after)
    assert moved["command"] == str(new / "legacy-registry-launcher")
    assert moved["args"] == [str(old / "registry-arg")]


def test_duplicate_json_keys_refuse_init_and_registry(tmp_path: Path):
    old = tmp_path / "old"
    old.mkdir()
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    (old / "init.json").write_text(
        '{"venv_path":"/one","venv_path":"/two",'
        '"manifest":{"agent_name":"name"}}',
        encoding="utf-8",
    )
    with pytest.raises(change_name.ChangeNameError, match="duplicate JSON key 'venv_path'"):
        change_name.preflight(old, "new")

    (old / "mcp_registry.jsonl").write_text(
        '{"name":"one","name":"two","summary":"x","transport":"stdio",'
        '"command":"python","source":"human"}\n',
        encoding="utf-8",
    )
    with pytest.raises(change_name.ChangeNameError, match="duplicate JSON key 'name'"):
        change_name._plan_registry_rewrite(old, tmp_path / "new")


def test_non_standard_json_constants_refuse_init_registry_and_direct_url(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    (old / "init.json").write_text(
        '{"venv_path":"/external","manifest":{"agent_name":"name"},'
        '"nested":{"value":NaN}}',
        encoding="utf-8",
    )
    with pytest.raises(change_name.ChangeNameError, match="non-standard JSON constant 'NaN'"):
        change_name.preflight(old, "new")
    assert not (old / ".suspend").exists()

    (old / "mcp_registry.jsonl").write_text(
        '{"name":"one","summary":"x","transport":"stdio",'
        '"command":"python","source":"human","extra":Infinity}\n',
        encoding="utf-8",
    )
    with pytest.raises(change_name.ChangeNameError, match="non-standard JSON constant 'Infinity'"):
        change_name._plan_registry_rewrite(old, new)

    venv = old / "venv"
    direct_url = _site_packages(venv) / "pkg.dist-info" / "direct_url.json"
    direct_url.parent.mkdir(parents=True)
    direct_url.write_text(
        '{"url":"file:///old/source","nested":{"value":-Infinity}}',
        encoding="utf-8",
    )
    with pytest.raises(change_name.ChangeNameError, match="non-standard JSON constant '-Infinity'"):
        change_name._plan_direct_url_rewrites(old, new, (_site_packages(venv),))


def test_external_venv_importing_from_old_root_refuses_before_suspension(
    tmp_path: Path, monkeypatch
):
    old, external = tmp_path / "old", tmp_path / "external"
    old.mkdir()
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    (old / "init.json").write_text(json.dumps({
        "venv_path": str(external), "manifest": {"agent_name": "name"},
    }), encoding="utf-8")
    monkeypatch.setattr(
        change_name, "_probe", lambda runtime, cwd: old / "src" / "lingtai" / "__init__.py"
    )
    monkeypatch.setattr(
        change_name, "_fresh",
        lambda root: (_ for _ in ()).throw(AssertionError("liveness reached")),
    )
    with pytest.raises(change_name.ChangeNameError, match="external environment metadata"):
        change_name.preflight(old, "new")
    assert not (old / ".suspend").exists()


def test_target_probe_requires_exact_planned_import_origin(tmp_path: Path, monkeypatch):
    new = tmp_path / "new"
    new.mkdir()
    (new / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    fence = change_name._create_transaction_marker(
        new / change_name.INCOMPLETE_MARKER, b"incomplete\n"
    )
    plan = change_name.Plan(
        old=tmp_path / "old", new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=new / "src" / "lingtai" / "__init__.py",
    )
    monkeypatch.setattr(
        change_name, "_probe", lambda runtime, cwd: tmp_path / "system" / "lingtai" / "__init__.py"
    )
    with pytest.raises(change_name.ChangeNameError, match="unexpected origin"):
        change_name._prepare_launch(plan, fence)
    assert (new / change_name.INCOMPLETE_MARKER).is_file()
    assert json.loads((new / ".agent.json").read_text())["address"] == "old"


def test_revalidate_refuses_new_project_local_binding_inventory_entry(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    venv = old / "runtime" / "venv"
    (venv / "bin").mkdir(parents=True)
    site = _site_packages(venv)
    site.mkdir(parents=True)
    (venv / "bin" / "python").write_bytes(b"python-binding\n")
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    inventory = change_name._runtime_inventory(old, venv)
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=venv / "bin" / "python", resumed_runtime=new / "runtime" / "venv/bin/python",
        expected_import_source=new / "lingtai/__init__.py",
        inventory_venv_relative=venv.relative_to(old), runtime_inventory=inventory,
    )
    (site / "late-editable.pth").write_text(str(old / "late-source") + "\n", encoding="utf-8")
    with pytest.raises(change_name.ChangeNameError, match="binding inventory changed"):
        change_name._revalidate_cutover(plan)


def test_relocated_shebang_over_portable_limit_refuses_before_suspend(tmp_path: Path):
    old = tmp_path / "old"
    venv = old / "venv"
    (venv / "bin").mkdir(parents=True)
    interpreter = venv / "bin" / "python"
    interpreter.write_bytes(b"#!/bin/sh\n")
    interpreter.chmod(0o755)
    launcher = venv / "bin" / "lingtai-agent"
    launcher.write_text(f"#!{interpreter}\n", encoding="utf-8")
    launcher.chmod(0o755)
    new = tmp_path / ("n" * 200)
    assert len(("#!" + str(interpreter) + "\n").encode()) <= (
        change_name.SHEBANG_SAFE_LIMIT
    )
    assert len(("#!" + str(new / "venv/bin/python") + "\n").encode()) > (
        change_name.SHEBANG_SAFE_LIMIT
    )
    with pytest.raises(change_name.ChangeNameError, match="255-byte portable limit"):
        change_name._plan_shebang_rewrites(old, new, venv)
    assert not (old / ".suspend").exists()


def test_existing_suspend_symlink_refuses_before_action(tmp_path: Path):
    old = tmp_path / "old"
    old.mkdir()
    target = tmp_path / "do-not-touch"
    target.write_text("sentinel", encoding="utf-8")
    (old / ".suspend").symlink_to(target)
    with pytest.raises(change_name.ChangeNameError, match="suspend marker must be absent"):
        change_name.preflight(old, "new")
    assert target.read_text(encoding="utf-8") == "sentinel"
    assert (old / ".suspend").is_symlink()


def test_pre_rename_failure_durably_removes_only_helper_fence(tmp_path: Path, monkeypatch):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=Path(change_name.__file__),
    )

    class Lease:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    lease = Lease()
    monkeypatch.setattr(change_name, "preflight", lambda old_arg, new_name: plan)
    monkeypatch.setattr(change_name, "_wait_stopped", lambda plan, timeout: lease)
    monkeypatch.setattr(
        change_name, "_revalidate_cutover",
        lambda plan: (_ for _ in ()).throw(change_name.ChangeNameError("injected pre-cutover")),
    )
    assert change_name.supervise(old, "new", 1) == 1
    assert lease.closed
    assert old.is_dir() and not new.exists()
    assert not (old / change_name.INCOMPLETE_MARKER).exists()
    assert (old / ".suspend").is_file()


def test_gated_child_is_prepared_while_fenced_and_committed_after_lease_release(
    tmp_path: Path, monkeypatch
):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    expected_source = new / "lingtai" / "__init__.py"
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=expected_source,
    )
    events = []

    class Lease:
        held = True

        def close(self):
            events.append("lease-release")
            self.held = False

    class Child:
        pid = 777
        returncode = None

        def poll(self):
            return None

    lease = Lease()
    gate_read = None
    monkeypatch.setattr(change_name, "preflight", lambda old_arg, new_name: plan)
    monkeypatch.setattr(change_name, "_wait_stopped", lambda plan, timeout: lease)
    monkeypatch.setattr(change_name, "_rename_no_replace", lambda source, target: source.rename(target))
    monkeypatch.setattr(change_name, "_probe", lambda runtime, cwd: expected_source)

    def fake_popen(*args, **kwargs):
        nonlocal gate_read
        events.append("gated-child")
        assert lease.held
        assert "PYTHONPATH" not in kwargs["env"]
        assert "PYTHONHOME" not in kwargs["env"]
        assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"
        assert (new / change_name.INCOMPLETE_MARKER).is_file()
        assert not (new / ".suspend").exists()
        assert json.loads((new / ".agent.json").read_text())["address"] == "new"
        gate_read = os.dup(kwargs["pass_fds"][0])
        return Child()

    def fake_wait_resumed(plan, launch, timeout):
        assert not lease.held
        assert not (new / change_name.INCOMPLETE_MARKER).exists()
        assert gate_read is not None and os.read(gate_read, 1) == b"1"
        os.close(gate_read)
        events.append("launch-committed")
        return launch.child.pid

    monkeypatch.setattr(change_name.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(change_name, "_wait_resumed", fake_wait_resumed)
    assert change_name.supervise(old, "new", 1) == 0
    assert events == ["gated-child", "lease-release", "launch-committed"]


def test_prepare_launch_failure_closes_gate_and_reaps_uncommitted_child(
    tmp_path: Path, monkeypatch
):
    new = tmp_path / "new"
    new.mkdir()
    expected_source = new / "lingtai" / "__init__.py"
    (new / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    fence = change_name._create_transaction_marker(
        new / change_name.INCOMPLETE_MARKER, b"incomplete\n"
    )
    plan = change_name.Plan(
        old=tmp_path / "old", new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=expected_source,
    )
    children = []

    class Child:
        returncode = None

        def __init__(self, read_fd):
            self.read_fd = read_fd
            self.reaped = False
            self.terminated = False

        def poll(self):
            return None

        def wait(self, timeout):
            assert os.read(self.read_fd, 1) == b""
            os.close(self.read_fd)
            self.reaped = True
            self.returncode = 125
            return self.returncode

        def terminate(self):
            self.terminated = True

    def fake_popen(*args, **kwargs):
        child = Child(os.dup(kwargs["pass_fds"][0]))
        children.append(child)
        return child

    monkeypatch.setattr(change_name, "_probe", lambda runtime, cwd: expected_source)
    monkeypatch.setattr(change_name.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        change_name, "_remove_proven_marker",
        lambda path, proof: (_ for _ in ()).throw(change_name.ChangeNameError("injected fence removal")),
    )
    with pytest.raises(change_name.ChangeNameError, match="injected fence removal"):
        change_name._prepare_launch(plan, fence)
    assert len(children) == 1 and children[0].reaped
    assert not children[0].terminated
    assert (new / change_name.INCOMPLETE_MARKER).is_file()


def test_lease_release_failure_restores_fence_and_reaps_uncommitted_child(
    tmp_path: Path, monkeypatch
):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=Path(change_name.__file__),
    )

    class Lease:
        def close(self):
            raise OSError("injected lease close failure")

    class Child:
        def __init__(self, read_fd):
            self.read_fd = read_fd
            self.reaped = False

        def wait(self, timeout):
            assert os.read(self.read_fd, 1) == b""
            os.close(self.read_fd)
            self.reaped = True
            return 125

        def terminate(self):
            raise AssertionError("gate EOF should stop the uncommitted child")

    child = None
    gate_fd = None

    def fake_prepare(plan, fence):
        nonlocal child, gate_fd
        read_fd, gate_fd = os.pipe()
        child = Child(read_fd)
        change_name._remove_proven_marker(new / change_name.INCOMPLETE_MARKER, fence)
        return change_name.PendingLaunch(child, gate_fd, new / "logs/change-name.log", 0)

    monkeypatch.setattr(change_name, "preflight", lambda old_arg, new_name: plan)
    monkeypatch.setattr(change_name, "_wait_stopped", lambda plan, timeout: Lease())
    monkeypatch.setattr(change_name, "_rename_no_replace", lambda source, target: source.rename(target))
    monkeypatch.setattr(change_name, "_prepare_launch", fake_prepare)
    assert change_name.supervise(old, "new", 1) == 1
    assert child is not None and child.reaped
    assert gate_fd is not None
    with pytest.raises(OSError) as raised:
        os.fstat(gate_fd)
    assert raised.value.errno == errno.EBADF
    assert (new / change_name.INCOMPLETE_MARKER).is_file()


def test_rename_parent_fsync_failure_is_target_authoritative_and_fenced(
    tmp_path: Path, monkeypatch
):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=Path(change_name.__file__),
    )

    class Lease:
        held = True

        def close(self):
            self.held = False

    lease = Lease()
    original_fsync = change_name._fsync_parent
    monkeypatch.setattr(change_name, "preflight", lambda old_arg, new_name: plan)
    monkeypatch.setattr(change_name, "_wait_stopped", lambda plan, timeout: lease)
    monkeypatch.setattr(change_name, "_rename_no_replace", lambda source, target: source.rename(target))

    def fail_publication_fsync(path):
        if path == new:
            assert lease.held
            raise OSError("injected parent fsync failure")
        original_fsync(path)

    monkeypatch.setattr(change_name, "_fsync_parent", fail_publication_fsync)
    prepare_calls = []
    monkeypatch.setattr(
        change_name, "_prepare_launch", lambda *args: prepare_calls.append(args)
    )
    assert change_name.supervise(old, "new", 1) == 1
    assert prepare_calls == []
    assert not old.exists() and new.is_dir()
    assert (new / change_name.INCOMPLETE_MARKER).is_file()
    assert (new / ".suspend").is_file()
    assert not lease.held


def test_post_rename_write_failure_stays_fenced_and_cli_refuses_before_cleanup(
    tmp_path: Path, monkeypatch
):
    from lingtai import cli

    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (old / ".agent.json").write_text(json.dumps({
        "agent_id": "id", "agent_name": "name", "address": "old",
    }), encoding="utf-8")
    first = old / "first.json"
    second = old / "second.json"
    first.write_text("first-before\n", encoding="utf-8")
    second.write_text("second-before\n", encoding="utf-8")
    rewrites = (
        change_name._rewrite(first, old, "first", b"first-after\n"),
        change_name._rewrite(second, old, "second", b"second-after\n"),
    )
    assert all(rewrite is not None for rewrite in rewrites)
    plan = change_name.Plan(
        old=old, new=new, agent_id="id", agent_name="name",
        runtime=Path(sys.executable), resumed_runtime=Path(sys.executable),
        expected_import_source=Path(change_name.__file__), rewrites=rewrites,
    )

    class Lease:
        def __init__(self):
            self.held = True

        def close(self):
            self.held = False

    lease = Lease()
    events = []
    cli_calls = []
    monkeypatch.setattr(change_name, "preflight", lambda old_arg, new_name: plan)
    monkeypatch.setattr(change_name, "_wait_stopped", lambda plan, timeout: lease)
    original_rename = change_name._rename_no_replace
    original_fsync = change_name._fsync_parent
    original_write = change_name._write_atomic

    def rename(source, target):
        events.append("rename")
        original_rename(source, target)

    def fsync_parent(path):
        if events:
            events.append("parent-fsync")
        original_fsync(path)

    def forbidden(name):
        return lambda *args, **kwargs: cli_calls.append(name)

    monkeypatch.setattr(cli, "_check_duplicate_process", forbidden("duplicate-check"))
    monkeypatch.setattr(cli, "_clean_signal_files", forbidden("signal-cleanup"))
    monkeypatch.setattr(cli, "load_init", forbidden("init-load"))
    monkeypatch.setattr(cli, "build_agent", forbidden("Agent-construction"))

    write_count = 0

    def write_atomic(path, content, mode):
        nonlocal write_count
        write_count += 1
        events.append(f"write-{path.name}")
        if write_count == 2:
            assert lease.held
            with pytest.raises(SystemExit):
                cli.run(new)
            assert (new / ".suspend").is_file()
            raise OSError("injected second target write failure")
        return original_write(path, content, mode)

    monkeypatch.setattr(change_name, "_rename_no_replace", rename)
    monkeypatch.setattr(change_name, "_fsync_parent", fsync_parent)
    monkeypatch.setattr(change_name, "_write_atomic", write_atomic)
    monkeypatch.setattr(
        change_name.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("launch reached")),
    )

    assert change_name.supervise(old, "new", 1) == 1
    assert events[:3] == ["rename", "parent-fsync", "write-first.json"]
    assert not lease.held
    marker = new / change_name.INCOMPLETE_MARKER
    assert marker.is_file()
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert (new / ".suspend").is_file()
    assert (new / "first.json").read_text(encoding="utf-8") == "first-after\n"
    assert (new / "second.json").read_text(encoding="utf-8") == "second-before\n"
    with pytest.raises(SystemExit):
        cli.run(new)
    assert marker.is_file() and (new / ".suspend").is_file()
    assert cli_calls == []
