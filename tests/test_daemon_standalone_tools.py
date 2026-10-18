"""Standalone host-tool regression: public dispatch, then detached reconstruction.

Guards daemon CONTRACT standalone boundary / BEHAVIORS D011. No live LLM,
provider credentials, or public-network requests are used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from lingtai.services.daemon import DaemonService
from lingtai.tools.daemon import DaemonManager
from lingtai.kernel.daemon_supervisor.manifest import read_manifest


@pytest.mark.parametrize("entry", ["service", "cli"])
@pytest.mark.parametrize("selected", [["shell"], [], ["web"], None])
def test_standalone_model_only_preset_dispatch_and_detached_tools(tmp_path, monkeypatch, capsys, selected, entry):
    preset = tmp_path / "model-only.json"
    original = {"description": {"summary": "Model-only local fixture"}, "manifest": {"llm": {"provider": "openai", "model": "non-network-fixture"}}}
    preset.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr("lingtai.kernel.preset_connectivity.check_connectivity", lambda **kw: {"status": "ok"})
    captured = []

    def capture(manager, request, **kwargs):
        manifest = read_manifest(Path(request.manifest_path))
        run_path = Path(manifest["run_dir"])
        capsule = kwargs["capsule"]
        # This fixture supplies no credentials. Exercise production manifest /
        # capsule preparation, intercepting only the process-launch boundary.
        assert not capsule.get("llm", {}).get("api_key")
        (run_path / "fixture-capsule.json").write_text(json.dumps(capsule))
        captured.append(run_path)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(DaemonManager, "_should_use_central_daemon_manager", lambda *a: True)
    monkeypatch.setattr(DaemonManager, "_enqueue_central_daemon_manager_run", capture)
    service = DaemonService(tmp_path)
    task = {"task": "local tool regression only", "preset": str(preset)}
    if selected is not None:
        task["tools"] = selected
    if entry == "service":
        result = service.emanate([task], max_turns=2, timeout=20)
    else:
        from lingtai.cli_daemon import add_daemon_parser, handle_daemon_command
        batch = tmp_path / "batch.json"
        batch.write_text(json.dumps({"tasks": [task], "max_turns": 2, "timeout": 20}))
        parser = argparse.ArgumentParser()
        add_daemon_parser(parser.add_subparsers(dest="command"))
        args = parser.parse_args(["daemon", "emanate", "--state-root", str(tmp_path), "--tasks", str(batch)])
        handle_daemon_command(args)
        result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dispatched", result
    assert len(captured) == 1
    assert json.loads(preset.read_text()) == original  # model preset remains read-only
    # Deserialize in a fresh process: no in-memory handler/schema or Agent can
    # accidentally mask lost composition across the manager handoff.
    script = r'''
import json, sys, threading
from pathlib import Path
from lingtai.tools.daemon.run_dir import DaemonRunDir
from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
from lingtai.kernel.daemon_supervisor.manifest import read_manifest
run_path = Path(sys.argv[1])
manifest = read_manifest(run_path / "supervisor_manifest.json")
run = DaemonRunDir.attach(run_path)
host = DetachedDaemonExecutionHost(run, manifest, threading.Event(), threading.Event(), capsule=json.loads((run_path / "fixture-capsule.json").read_text()))
schemas, handlers = host._build_lingtai_surface()
names = {s.name for s in schemas}
expected = set(json.loads(sys.argv[2]))
assert names & {"shell", "web"} == expected, names
assert not (names & {"email", "avatar", "daemon", "notification", "system", "context"}), names
output = {"names": sorted(names)}
if "shell" in expected:
    reply = handlers["shell"]({"action": "run", "input": {"command": "printf standalone-shell-ok", "working_dir": str(run_path.parent.parent), "timeout": 10, "async": False, "reminder": None}, "reasoning": "harmless local regression"})
    assert reply["exit_code"] == 0, reply
    assert reply["stdout"] == "standalone-shell-ok", reply
    output["shell"] = reply["stdout"]
if "web" in expected:
    manager = handlers["web"].__self__
    assert manager._default_engine == "duckduckgo"
    assert set(manager._specs) == {"duckduckgo"}
    assert all(not spec.api_key and not spec.api_key_env for spec in manager._specs.values())
    reply = handlers["web"]({"action": "settings", "input": {}, "reasoning": "read-only no-network regression"})
    assert "settings" in reply, reply
    assert manager._credential_configured("openai") is False
    assert manager._credential_configured("anthropic") is False
    # Exercise official public action dispatch with deterministic transport
    # fixtures, never a public network call or a provider/auth adapter.
    from lingtai.services.websearch import SearchResult
    from lingtai.services.websearch.duckduckgo import DuckDuckGoSearchService
    DuckDuckGoSearchService.search = lambda self, query, max_results=None: [SearchResult(title="Local fixture", url="https://example.com/", snippet="public fixture")]
    search = handlers["web"]({"action": "search", "input": {"query": "local fixture"}, "reasoning": "hermetic dispatch"})
    assert search["status"] == "ok" and search["actual_engine"] == "duckduckgo", search
    from lingtai.adapters.browser_transport import VettedHttpTransport
    from lingtai.tools.browser.port import TransportResponse
    VettedHttpTransport.resolve = lambda self, hostname, timeout_s: ("93.184.216.34",)
    VettedHttpTransport.request = lambda self, url, **kw: TransportResponse(200, {"content-type": "text/html"}, b"<html><body><p>Standalone browse fixture.</p></body></html>", False, url)
    browse = handlers["web"]({"action": "browse", "input": {"url": "https://example.com/", "link_ref": None, "cursor": None, "extract": None, "max_chars": None}, "reasoning": "hermetic browse"})
    assert browse["status"] == "ok", browse
    assert "Standalone browse fixture" in json.dumps(browse), browse
    output["web"] = {"engine": search["actual_engine"], "browse": browse["status"], "credentials_borrowed": False}
print(json.dumps(output))
'''
    expected = selected if selected is not None else ["shell", "web"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    for key in ("LINGTAI_WEB_ENGINE", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    # Ambient provider credentials must not expand the DDG-only host route.
    env["OPENAI_API_KEY"] = "fixture-not-a-real-credential"
    env["ANTHROPIC_API_KEY"] = "fixture-not-a-real-credential"
    child = subprocess.run([sys.executable, "-c", script, str(captured[0]), json.dumps(expected)],
                           env=env, text=True, capture_output=True, timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr
    print(child.stdout.strip())


def test_standalone_readback_does_not_mount_host_tools(tmp_path):
    service = DaemonService(tmp_path)
    assert service._runtime._host_surface is None
    service.list()
    service.check("em-missing")
    assert service._runtime._host_surface is None
    assert service._manager is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX local capsule acceptance, not native Windows")
def test_standalone_real_supervisor_capsule_executes_shell_and_web(tmp_path, monkeypatch):
    """Unintercepted production spawn/capsule/runner, deterministic fake LLM."""
    import time
    from tests._fake_llm_adapter import register
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    register()
    fixture_dir = tmp_path / "provider-fixture"
    fixture_dir.mkdir()
    (fixture_dir / "_fake_llm_adapter.py").write_text('''
from tests._fake_llm_adapter import _FakeAdapter, _FakeChatSession, PROVIDER_NAME
from lingtai.kernel.llm.base import LLMResponse, ToolCall
class Session(_FakeChatSession):
    def send(self, message):
        self._send_count += 1
        if self._send_count == 1:
            name, args = "shell", {"action": "run", "input": {"command": "printf standalone-transport-ok", "working_dir": None, "timeout": 10, "async": False, "reminder": None}, "reasoning": "harmless local command"}
        elif self._send_count == 2:
            assert "standalone-transport-ok" in str(message), str(message)
            name, args = "web", {"action": "settings", "input": {}, "reasoning": "no-network Web handler proof"}
        elif self._send_count == 3:
            assert "duckduckgo" in str(message), str(message)
            name, args = "finish", {"action": "finish", "input": {"status": "done", "summary": "standalone real capsule Shell/Web passed"}, "reasoning": "fixture complete"}
        else:
            return LLMResponse(text="fixture complete")
        if name != "finish":
            args["summarize"] = False
        return LLMResponse(text="fixture local dispatch", tool_calls=[ToolCall(name=name, args=args, id="fixture-" + str(self._send_count))])
class Adapter(_FakeAdapter):
    def create_chat(self, **kwargs):
        assert self._api_key == "fixture-capsule-token", "synthetic capsule value was lost"
        return Session()
def register():
    from lingtai.llm.service import LLMService
    LLMService.register_adapter(PROVIDER_NAME, lambda **kw: Adapter(**kw))
''')
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(fixture_dir), str(repo / "src"), str(repo))))
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM", "1")
    monkeypatch.setenv("STANDALONE_FIXTURE_KEY", "fixture-capsule-token")
    monkeypatch.delenv("LINGTAI_WEB_ENGINE", raising=False)
    monkeypatch.setattr("lingtai.kernel.preset_connectivity.check_connectivity", lambda **kw: {"status": "ok"})
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon/daemon.json").write_text('{"manager_pool_size": 0}')
    preset = tmp_path / "model-only.json"
    preset.write_text(json.dumps({"description": {"summary": "local non-network model"}, "manifest": {"llm": {"provider": "lingtai-supervisor-test-fake", "model": "fake-model", "api_key_env": "STANDALONE_FIXTURE_KEY"}}}))
    service = DaemonService(tmp_path)
    result = service.emanate([{"task": "local synthetic tool acceptance", "preset": str(preset)}], max_turns=5, timeout=20)
    assert result["status"] == "dispatched", result
    run_path = tmp_path / "daemons" / result["ids"][0]
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline:
            state = DaemonRunDir.read_state_from_disk(run_path)
            if state.get("state") in {"done", "failed", "cancelled", "timeout"}:
                break
            time.sleep(0.1)
        assert state.get("state") == "done", state
        assert state.get("supervisor_pid") and state.get("execution_pid"), state
        assert "fixture-capsule-token" not in (run_path / "supervisor_manifest.json").read_text()
        print("real supervisor/capsule: standalone-transport-ok; Web DDG settings; done")
    finally:
        service.reclaim()
