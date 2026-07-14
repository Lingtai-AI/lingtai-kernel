import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch


def _write_init(tmp_path: Path, overrides: dict | None = None) -> Path:
    """Write a valid init.json to tmp_path and return the path."""
    data = {
        "manifest": {
            "agent_name": "test-agent",
            "language": "en",
            "llm": {
                "provider": "anthropic",
                "model": "test-model",
                "api_key": "test-key",
                "base_url": None,
            },
            "capabilities": {},
            "soul": {"delay": 30},
            "stamina": 60,
            "context_limit": None,
            "molt_pressure": 0.8,
            "molt_prompt": "",
            "max_turns": 10,
            "admin": {"karma": True},
            "streaming": False,
        },
        "principle": "",
        "covenant": "Be helpful.",
        "pad": "I remember nothing.",
        "lingtai": "",
    }
    if overrides:
        # Deep merge manifest if provided
        for k, v in overrides.items():
            if k == "manifest" and isinstance(v, dict):
                data["manifest"].update(v)
            else:
                data[k] = v
    init_path = tmp_path / "init.json"
    init_path.write_text(json.dumps(data))
    return tmp_path


def test_load_init_reads_file(tmp_path):
    from lingtai.cli import load_init
    _write_init(tmp_path)
    data = load_init(tmp_path)
    assert data["manifest"]["agent_name"] == "test-agent"


def test_load_init_missing_file(tmp_path):
    from lingtai.cli import load_init
    with pytest.raises(SystemExit):
        load_init(tmp_path)


def test_load_init_invalid_json(tmp_path):
    (tmp_path / "init.json").write_text("{bad json")
    from lingtai.cli import load_init
    with pytest.raises(SystemExit):
        load_init(tmp_path)


def test_load_init_validation_error(tmp_path):
    (tmp_path / "init.json").write_text(json.dumps({"manifest": {}}))
    from lingtai.cli import load_init
    with pytest.raises(SystemExit):
        load_init(tmp_path)


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_constructs_correctly(mock_mail, mock_agent, mock_llm, tmp_path):
    from lingtai.cli import load_init, build_agent
    _write_init(tmp_path)
    data = load_init(tmp_path)
    build_agent(data, tmp_path)

    mock_llm.assert_called_once()
    llm_kwargs = mock_llm.call_args.kwargs
    assert llm_kwargs["provider"] == "anthropic"
    assert llm_kwargs["model"] == "test-model"
    assert llm_kwargs["api_key"] == "test-key"
    assert llm_kwargs["base_url"] is None
    mock_mail.assert_called_once()
    assert mock_mail.call_args.kwargs["working_dir"] == tmp_path
    mock_agent.assert_called_once()
    call_kwargs = mock_agent.call_args
    assert call_kwargs.kwargs["agent_name"] == "test-agent"
    assert call_kwargs.kwargs["working_dir"] == tmp_path
    assert call_kwargs.kwargs["streaming"] is False
    # covenant, memory, capabilities, addons no longer passed to constructor —
    # they are loaded by _setup_from_init() from init.json
    assert "covenant" not in call_kwargs.kwargs
    assert "pad" not in call_kwargs.kwargs
    assert "capabilities" not in call_kwargs.kwargs
    assert "addons" not in call_kwargs.kwargs
    # _setup_from_init() is called on the constructed agent
    mock_agent.return_value._setup_from_init.assert_called_once()


# --- env file and env var resolution ---


def test_load_env_file(tmp_path):
    from lingtai.cli import load_env_file
    env_path = tmp_path / ".env"
    env_path.write_text("TEST_CLI_KEY=secret123\nTEST_CLI_OTHER='quoted'\n")

    # Clean up after test
    for k in ("TEST_CLI_KEY", "TEST_CLI_OTHER"):
        os.environ.pop(k, None)

    load_env_file(env_path)
    assert os.environ["TEST_CLI_KEY"] == "secret123"
    assert os.environ["TEST_CLI_OTHER"] == "quoted"

    # Does not overwrite existing by default
    os.environ["TEST_CLI_KEY"] = "original"
    load_env_file(env_path)
    assert os.environ["TEST_CLI_KEY"] == "original"

    # Explicit refresh reloads may choose to overwrite stale process env.
    load_env_file(env_path, overwrite=True)
    assert os.environ["TEST_CLI_KEY"] == "secret123"

    # Clean up
    os.environ.pop("TEST_CLI_KEY", None)
    os.environ.pop("TEST_CLI_OTHER", None)


def test_load_env_file_missing():
    from lingtai.cli import load_env_file
    # Should not raise on missing file
    load_env_file("/nonexistent/.env")


def test_resolve_env_prefers_env_var():
    from lingtai.cli import resolve_env
    os.environ["TEST_RESOLVE_KEY"] = "from-env"
    try:
        assert resolve_env("raw-value", "TEST_RESOLVE_KEY") == "from-env"
    finally:
        os.environ.pop("TEST_RESOLVE_KEY", None)


def test_resolve_env_falls_back_to_raw():
    from lingtai.cli import resolve_env
    os.environ.pop("NONEXISTENT_KEY_12345", None)
    assert resolve_env("raw-value", "NONEXISTENT_KEY_12345") == "raw-value"


def test_resolve_env_no_env_name():
    from lingtai.cli import resolve_env
    assert resolve_env("raw-value", None) == "raw-value"
    assert resolve_env(None, None) is None


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_resolves_api_key_env(mock_mail, mock_agent, mock_llm, tmp_path):
    """api_key_env resolves from environment, overriding raw api_key."""
    from lingtai.cli import load_init, build_agent

    _write_init(tmp_path)
    data = load_init(tmp_path)
    data["manifest"]["llm"]["api_key_env"] = "TEST_LLM_KEY"
    data["manifest"]["llm"]["api_key"] = "fallback-key"

    os.environ["TEST_LLM_KEY"] = "env-key-value"
    try:
        build_agent(data, tmp_path)
    finally:
        os.environ.pop("TEST_LLM_KEY", None)

    mock_llm.assert_called_once()
    llm_kwargs = mock_llm.call_args.kwargs
    assert llm_kwargs["api_key"] == "env-key-value"
    assert llm_kwargs["provider"] == "anthropic"
    assert llm_kwargs["model"] == "test-model"


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_env_file_loaded(mock_mail, mock_agent, mock_llm, tmp_path):
    """env_file is loaded before resolving env vars."""
    from lingtai.cli import load_init, build_agent

    env_path = tmp_path / "secrets.env"
    env_path.write_text("TEST_ENV_FILE_KEY=from-file\n")

    _write_init(tmp_path)
    data = load_init(tmp_path)
    data["env_file"] = str(env_path)
    data["manifest"]["llm"]["api_key_env"] = "TEST_ENV_FILE_KEY"

    os.environ.pop("TEST_ENV_FILE_KEY", None)
    try:
        build_agent(data, tmp_path)
    finally:
        os.environ.pop("TEST_ENV_FILE_KEY", None)

    mock_llm.assert_called_once()
    llm_kwargs = mock_llm.call_args.kwargs
    assert llm_kwargs["api_key"] == "from-file"
    assert llm_kwargs["provider"] == "anthropic"
    assert llm_kwargs["model"] == "test-model"


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_env_file_overwrites_on_refresh_marker(mock_mail, mock_agent, mock_llm, tmp_path):
    """Refresh relaunches should let an edited env_file replace stale
    inherited process environment values before resolving api_key_env.
    """
    from lingtai.cli import load_init, build_agent

    env_path = tmp_path / "secrets.env"
    env_path.write_text("TEST_ENV_FILE_REFRESH_KEY=fresh-from-file\n")

    _write_init(tmp_path)
    data = load_init(tmp_path)
    data["env_file"] = str(env_path)
    data["manifest"]["llm"]["api_key_env"] = "TEST_ENV_FILE_REFRESH_KEY"

    os.environ["TEST_ENV_FILE_REFRESH_KEY"] = "stale-process-value"
    os.environ["LINGTAI_REFRESH_ENV_OVERWRITE"] = "1"
    try:
        build_agent(data, tmp_path)
    finally:
        os.environ.pop("TEST_ENV_FILE_REFRESH_KEY", None)
        os.environ.pop("LINGTAI_REFRESH_ENV_OVERWRITE", None)

    mock_llm.assert_called_once()
    assert mock_llm.call_args.kwargs["api_key"] == "fresh-from-file"
    assert "LINGTAI_REFRESH_ENV_OVERWRITE" not in os.environ


# --- addons ---


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_passes_addons(mock_mail, mock_agent, mock_llm, tmp_path):
    """Addons from init.json are handled by _setup_from_init, not constructor."""
    from lingtai.cli import load_init, build_agent

    _write_init(tmp_path)
    data = load_init(tmp_path)
    data["addons"] = {
        "imap": {
            "email_address": "test@gmail.com",
            "email_password": "secret",
            "imap_host": "imap.gmail.com",
            "smtp_host": "smtp.gmail.com",
        },
    }

    build_agent(data, tmp_path)

    # Addons no longer passed to constructor — handled by _setup_from_init
    call_kwargs = mock_agent.call_args.kwargs
    assert "addons" not in call_kwargs
    mock_agent.return_value._setup_from_init.assert_called_once()


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_resolves_addon_env(mock_mail, mock_agent, mock_llm, tmp_path):
    """Addon *_env fields are resolved by _setup_from_init via init.json."""
    from lingtai.cli import load_init, build_agent

    _write_init(tmp_path)
    data = load_init(tmp_path)
    data["addons"] = {
        "imap": {
            "email_address": "test@gmail.com",
            "email_password_env": "TEST_IMAP_PASS",
        },
        "telegram": {
            "bot_token_env": "TEST_TG_TOKEN",
        },
    }

    os.environ["TEST_IMAP_PASS"] = "imap-secret"
    os.environ["TEST_TG_TOKEN"] = "tg-secret"
    try:
        build_agent(data, tmp_path)
    finally:
        os.environ.pop("TEST_IMAP_PASS", None)
        os.environ.pop("TEST_TG_TOKEN", None)

    # Addons no longer passed to constructor — handled by _setup_from_init
    assert "addons" not in mock_agent.call_args.kwargs
    mock_agent.return_value._setup_from_init.assert_called_once()


@patch("lingtai.cli.LLMService")
@patch("lingtai.cli.Agent")
@patch("lingtai.cli.PosixFilesystemMailAdapter")
def test_build_agent_no_addons(mock_mail, mock_agent, mock_llm, tmp_path):
    """No addons field — _setup_from_init handles this gracefully."""
    from lingtai.cli import load_init, build_agent

    _write_init(tmp_path)
    data = load_init(tmp_path)
    build_agent(data, tmp_path)

    assert "addons" not in mock_agent.call_args.kwargs
    mock_agent.return_value._setup_from_init.assert_called_once()


def test_log_rebuild_doctor_query_cli(tmp_path, capsys):
    from lingtai.cli import main
    import sys

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.jsonl").write_text(json.dumps({"type": "cli_event", "ts": 1}) + "\n", encoding="utf-8")

    old_argv = sys.argv
    try:
        sys.argv = ["lingtai-agent", "log", "rebuild", str(tmp_path)]
        main()
        rebuild_out = json.loads(capsys.readouterr().out)
        assert rebuild_out["status"] == "ok"

        sys.argv = ["lingtai-agent", "log", "doctor", str(tmp_path)]
        main()
        doctor_out = json.loads(capsys.readouterr().out)
        assert doctor_out["event_count"] == 1
        assert doctor_out["chat_entry_count"] == 0

        sys.argv = ["lingtai-agent", "log", "query", str(tmp_path), "SELECT type FROM events"]
        main()
        query_out = json.loads(capsys.readouterr().out)
        assert query_out == [{"type": "cli_event"}]
    finally:
        sys.argv = old_argv


def test_log_query_missing_sqlite_requires_rebuild_cli(tmp_path, capsys):
    from lingtai.cli import main
    import sys

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "events.jsonl").write_text(json.dumps({"type": "cli_event", "ts": 1}) + "\n", encoding="utf-8")

    old_argv = sys.argv
    try:
        sys.argv = ["lingtai-agent", "log", "query", str(tmp_path), "SELECT type FROM events"]
        try:
            main()
            assert False, "query should exit when sqlite sidecar is missing"
        except SystemExit as exc:
            assert exc.code == 1
        captured = capsys.readouterr()
        assert "rebuild" in captured.err
        assert not (logs / "log.sqlite").exists()
    finally:
        sys.argv = old_argv


def test_maintenance_cleanup_json_cli_reports_candidates(tmp_path, capsys):
    from lingtai.cli import main
    import sys

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / ".agent.json").write_text(
        json.dumps({"agent_name": "agent", "admin": {"karma": True}}),
        encoding="utf-8",
    )
    sent = agent / "mailbox" / "sent" / "20260401T010101-abcd"
    sent.mkdir(parents=True)
    (sent / "message.json").write_text("{}", encoding="utf-8")

    old_argv = sys.argv
    try:
        sys.argv = [
            "lingtai-agent",
            "maintenance",
            "cleanup",
            str(agent),
            "--older-than-days",
            "30",
            "--json",
        ]
        main()
    finally:
        sys.argv = old_argv

    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "dry_run"
    assert data["classes"]["sent_mail"]["candidates"] == 1
    assert sent.exists()


def test_maintenance_cleanup_human_cli_says_no_files_changed(tmp_path, capsys):
    from lingtai.cli import main
    import sys

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / ".agent.json").write_text(
        json.dumps({"agent_name": "agent", "admin": {"karma": True}}),
        encoding="utf-8",
    )

    old_argv = sys.argv
    try:
        sys.argv = ["lingtai-agent", "maintenance", "cleanup", str(agent)]
        main()
    finally:
        sys.argv = old_argv

    out = capsys.readouterr().out
    assert "Retention cleanup dry-run" in out
    assert "No files were changed" in out


def test_maintenance_cleanup_human_cli_reports_footprints(tmp_path, capsys):
    from lingtai.cli import main
    import sys

    agent = tmp_path / "agent"
    agent.mkdir()
    (agent / ".agent.json").write_text(
        json.dumps({"agent_name": "agent", "admin": {"karma": True}}),
        encoding="utf-8",
    )
    events = agent / "logs" / "events.jsonl"
    events.parent.mkdir()
    events.write_text("{}\n", encoding="utf-8")

    old_argv = sys.argv
    try:
        sys.argv = ["lingtai-agent", "maintenance", "cleanup", str(agent)]
        main()
    finally:
        sys.argv = old_argv

    out = capsys.readouterr().out
    assert "footprints: 1" in out
    assert "footprint agent_authoritative_events_log" in out
    assert "risk=authoritative_do_not_delete" in out
    assert "recommendation=Preserve as the authoritative recovery log" in out


def test_maintenance_cleanup_rejects_invalid_days(tmp_path):
    from lingtai.cli import main
    import sys

    old_argv = sys.argv
    try:
        sys.argv = [
            "lingtai-agent",
            "maintenance",
            "cleanup",
            str(tmp_path),
            "--older-than-days",
            "0",
        ]
        with pytest.raises(SystemExit) as exc:
            main()
    finally:
        sys.argv = old_argv

    assert exc.value.code == 2


def test_load_init_runs_agent_migrations_before_validation(tmp_path):
    """CLI boot must normalize legacy procedures fields before validating init.json."""
    import hashlib

    from lingtai.cli import load_init

    legacy = "legacy CLI procedures"
    init = {
        "manifest": {
            "agent_name": "cli-agent",
            "llm": {"provider": "p", "model": "m"},
            "capabilities": {},
        },
        "principle": "",
        "covenant": "",
        "pad": "",
        "lingtai": "",
        "procedures": legacy,
        "procedures_file": "old/procedures.md",
    }
    (tmp_path / "init.json").write_text(json.dumps(init), encoding="utf-8")

    data = load_init(tmp_path)

    assert "procedures" not in data
    assert "procedures_file" not in data
    on_disk = json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))
    assert "procedures" not in on_disk
    assert "procedures_file" not in on_disk
    digest = hashlib.sha256(legacy.encode("utf-8")).hexdigest()
    archive = tmp_path / "system" / "migrations" / f"init-procedures-{digest}.md"
    assert archive.read_text(encoding="utf-8") == legacy


# ---------------------------------------------------------------------------
# _check_duplicate_process — must match the working-dir argument exactly
# ---------------------------------------------------------------------------


def _ps_line(pid: int, working_dir) -> str:
    """A realistic `ps -eo pid=,command=` line for a `lingtai run <dir>` agent."""
    py = "/usr/local/.../Python"
    return f"{pid} {py} -m lingtai run {working_dir}"


def test_check_duplicate_process_ignores_prefix_sibling(tmp_path):
    """Starting `.../codex` must not be blocked by a running `.../codex_colleague`.

    Regression: `codex` is a prefix of `codex_colleague`, and the old substring
    match treated the colleague's `ps` line as a duplicate codex process.
    """
    from lingtai.cli import _check_duplicate_process

    codex = tmp_path / "codex"
    colleague = tmp_path / "codex_colleague"
    codex.mkdir()
    colleague.mkdir()

    ps_out = _ps_line(42779, colleague.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        # Must NOT raise SystemExit — the colleague is a different agent.
        _check_duplicate_process(codex)


def test_check_duplicate_process_detects_exact_match(tmp_path):
    """A genuine same-workdir `lingtai run` process is still flagged."""
    from lingtai.cli import _check_duplicate_process

    codex = tmp_path / "codex"
    codex.mkdir()

    ps_out = _ps_line(99999, codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)


def test_check_duplicate_process_ignores_shell_wrapper(tmp_path):
    """A shell wrapper that merely *evals* the run command is not a duplicate.

    Regression (discussions/covenant-distillation-and-per-agent-profile.md): a
    `zsh -ic '... lingtai run <dir> ...'` wrapper carries the whole command as a
    single quoted argv token, so `run` is never its own token and must not match.
    """
    from lingtai.cli import _check_duplicate_process

    codex = tmp_path / "codex"
    codex.mkdir()

    wrapper = f"67192 /bin/zsh -ic 'lingtai run {codex.resolve()} && echo done'"
    with patch("subprocess.check_output", return_value=wrapper + "\n"):
        _check_duplicate_process(codex)


def test_check_duplicate_process_excludes_own_pid(tmp_path):
    """The current process's own `ps` line must never count as a duplicate."""
    from lingtai.cli import _check_duplicate_process

    codex = tmp_path / "codex"
    codex.mkdir()

    ps_out = _ps_line(os.getpid(), codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        _check_duplicate_process(codex)


# ---------------------------------------------------------------------------
# _check_duplicate_process — verified refresh-host exemption
# ---------------------------------------------------------------------------


def _commit_live_marker_for(working_dir, *, pid, command_label="module", start_ticks=1):
    """Commit a marker for ``pid`` at ``working_dir`` using the accepted
    ``refresh_host`` primitives — no fake/shortcut marker construction.

    ``start_ticks`` is caller-supplied (not probed) so the marker's claimed
    identity can be tested against a monkeypatched liveness probe, exactly
    as the accepted ``test_daemon_refresh_survival.py`` suite already does
    for stale/dead/mismatched pids.
    """
    from lingtai.tools.daemon.refresh_host import RefreshHostMarker, commit_marker

    marker = RefreshHostMarker.build(
        pid=pid, start_ticks=start_ticks, command_label=command_label,
        working_dir=str(working_dir), owned_run_ids=["em-test"],
    )
    commit_marker(working_dir, marker)


def test_check_duplicate_process_allows_verified_refresh_host(tmp_path, monkeypatch):
    """An exact verified live refresh-host PID must not abort boot."""
    from lingtai.cli import _check_duplicate_process
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    codex = tmp_path / "codex"
    codex.mkdir()
    host_pid = 55001
    _commit_live_marker_for(codex.resolve(), pid=host_pid)

    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: refresh_host_module.ProcessStartIdentity(pid=pid, start_ticks=1),
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {codex.resolve()}" if pid == host_pid else None,
    )
    ps_out = _ps_line(host_pid, codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        # Must NOT raise SystemExit — this exact pid is a verified host.
        _check_duplicate_process(codex)


def test_check_duplicate_process_rejects_unverified_pid_despite_marker_dir(tmp_path, monkeypatch):
    """A same-workdir duplicate with NO live marker remains fatal even when the
    hosts directory exists for an unrelated pid (ordinary behavior unchanged)."""
    from lingtai.cli import _check_duplicate_process
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    codex = tmp_path / "codex"
    codex.mkdir()
    # Marker exists for a different pid than the one in `ps`.
    _commit_live_marker_for(codex.resolve(), pid=55002)
    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: refresh_host_module.ProcessStartIdentity(pid=pid, start_ticks=1)
        if pid == 55002 else None,
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {codex.resolve()}" if pid == 55002 else None,
    )
    rogue_pid = 99998
    ps_out = _ps_line(rogue_pid, codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)


def test_check_duplicate_process_rejects_stale_marker_pid(tmp_path, monkeypatch):
    """A marker whose pid is no longer live (stale/dead) never exempts that pid
    — the ordinary fatal-duplicate path still applies."""
    from lingtai.cli import _check_duplicate_process
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    codex = tmp_path / "codex"
    codex.mkdir()
    dead_pid = 55003
    _commit_live_marker_for(codex.resolve(), pid=dead_pid)
    # Marker pid is dead now (probe returns None for it).
    monkeypatch.setattr(refresh_host_module, "probe_process_start_identity", lambda pid: None)

    ps_out = _ps_line(dead_pid, codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)


def test_check_duplicate_process_rejects_duplicate_marker_claim(tmp_path, monkeypatch):
    """Two distinct valid markers naming the SAME pid fail closed — the
    ambiguous pid is never exempted from the fatal-duplicate path."""
    from lingtai.cli import _check_duplicate_process
    from lingtai.tools.daemon.refresh_host import RefreshHostMarker, commit_marker
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    codex = tmp_path / "codex"
    codex.mkdir()
    dup_pid = 55004
    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: refresh_host_module.ProcessStartIdentity(pid=pid, start_ticks=1),
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {codex.resolve()}",
    )
    first = RefreshHostMarker.build(
        pid=dup_pid, start_ticks=1, command_label="module",
        working_dir=str(codex.resolve()), owned_run_ids=["em-1"],
    )
    second = RefreshHostMarker.build(
        pid=dup_pid, start_ticks=1, command_label="module",
        working_dir=str(codex.resolve()), owned_run_ids=["em-2"],
    )
    commit_marker(codex.resolve(), first)
    commit_marker(codex.resolve(), second)

    ps_out = _ps_line(dup_pid, codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)


def test_check_duplicate_process_verified_host_plus_rogue_still_rejects(tmp_path, monkeypatch):
    """A verified host coexisting with an additional rogue same-workdir pid
    must still reject boot — regardless of `ps` row order."""
    from lingtai.cli import _check_duplicate_process
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    codex = tmp_path / "codex"
    codex.mkdir()
    host_pid = 55005
    rogue_pid = 55006
    _commit_live_marker_for(codex.resolve(), pid=host_pid)
    monkeypatch.setattr(
        refresh_host_module, "probe_process_start_identity",
        lambda pid: refresh_host_module.ProcessStartIdentity(pid=pid, start_ticks=1)
        if pid == host_pid else None,
    )
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {codex.resolve()}" if pid == host_pid else None,
    )

    # Verified-host row first, rogue row second.
    ps_out = (
        _ps_line(host_pid, codex.resolve()) + "\n"
        + _ps_line(rogue_pid, codex.resolve()) + "\n"
    )
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)

    # Rogue row first, verified-host row second — order must not matter.
    ps_out_reordered = (
        _ps_line(rogue_pid, codex.resolve()) + "\n"
        + _ps_line(host_pid, codex.resolve()) + "\n"
    )
    with patch("subprocess.check_output", return_value=ps_out_reordered):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)


def test_check_duplicate_process_matching_pid_plus_discovery_failure_still_rejects(
    tmp_path, monkeypatch
):
    """A same-workdir candidate PID whose marker discovery/enumeration
    itself fails (e.g. an unreadable hosts directory) must still hit the
    ordinary fatal duplicate-process path — the CLI relies on
    is_verified_refresh_host's own fail-closed contract (Correction 2) to
    never raise out of a realistic enumeration failure, so this exercises
    the real, unmocked call chain: CLI must NOT crash boot with the
    discovery exception, and must NOT silently exempt the candidate."""
    from lingtai.cli import _check_duplicate_process
    import lingtai.tools.daemon.refresh_host as refresh_host_module

    codex = tmp_path / "codex"
    codex.mkdir()
    candidate_pid = 55007

    def _boom(_parent_working_dir):
        raise OSError("simulated enumeration failure")
        yield  # pragma: no cover - unreachable, keeps this a generator

    monkeypatch.setattr(refresh_host_module, "iter_marker_paths", _boom)

    ps_out = _ps_line(candidate_pid, codex.resolve()) + "\n"
    with patch("subprocess.check_output", return_value=ps_out):
        with pytest.raises(SystemExit):
            _check_duplicate_process(codex)
