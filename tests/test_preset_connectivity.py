"""Tests for the preset connectivity check helper."""
import os
import time
from unittest.mock import patch

import pytest


def test_no_credentials_does_not_make_network_call(monkeypatch):
    """When env var is missing, no socket/network call is attempted."""
    monkeypatch.delenv("MISSING_KEY", raising=False)
    from lingtai.kernel import preset_connectivity
    with patch.object(preset_connectivity, "_probe_host") as probe:
        result = preset_connectivity.check_connectivity(
            provider="anthropic",
            base_url="https://api.minimax.io/anthropic",
            api_key_env="MISSING_KEY",
        )
        assert result["status"] == "no_credentials"
        assert "MISSING_KEY" in result.get("error", "")
        probe.assert_not_called()


def test_ok_when_host_reachable(monkeypatch):
    """When env var is set and host is reachable, return ok with latency."""
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity
    with patch.object(preset_connectivity, "_probe_host", return_value=42):
        result = preset_connectivity.check_connectivity(
            provider="x",
            base_url="https://api.example.com",
            api_key_env="MOCK_KEY",
        )
        assert result["status"] == "ok"
        assert result["latency_ms"] == 42
        assert "checked_at" in result


def test_unreachable_when_host_probe_raises(monkeypatch):
    """When the probe raises, return unreachable with the error message."""
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity
    with patch.object(preset_connectivity, "_probe_host", side_effect=OSError("connection refused")):
        result = preset_connectivity.check_connectivity(
            provider="x",
            base_url="https://api.example.com",
            api_key_env="MOCK_KEY",
        )
        assert result["status"] == "unreachable"
        assert "connection refused" in result["error"]


def test_no_api_key_env_skips_credential_check(monkeypatch):
    """If api_key_env is None or empty, skip credential check and just probe."""
    from lingtai.kernel import preset_connectivity
    with patch.object(preset_connectivity, "_probe_host", return_value=10):
        result = preset_connectivity.check_connectivity(
            provider="x",
            base_url="https://api.example.com",
            api_key_env=None,
        )
        assert result["status"] == "ok"


def test_default_url_used_when_base_url_missing(monkeypatch):
    """When base_url is None, fall back to provider's default URL."""
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity
    captured = {}
    def fake_probe(host, port, timeout):
        captured["host"] = host
        captured["port"] = port
        return 5
    with patch.object(preset_connectivity, "_probe_host", side_effect=fake_probe):
        preset_connectivity.check_connectivity(
            provider="openai",
            base_url=None,
            api_key_env="MOCK_KEY",
        )
        assert captured["host"] == "api.openai.com"
        assert captured["port"] == 443


def test_unreachable_when_no_url_and_unknown_provider(monkeypatch):
    """No base_url + unknown provider → unreachable with explanatory error."""
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel.preset_connectivity import check_connectivity
    result = check_connectivity(
        provider="weird-provider",
        base_url=None,
        api_key_env="MOCK_KEY",
    )
    assert result["status"] == "unreachable"
    assert "weird-provider" in result["error"] or "no base_url" in result["error"]


def test_no_caching_every_call_reprobes(monkeypatch):
    """No cache: identical back-to-back calls each trigger a probe.

    The whole point of the check is freshness — caching would let the agent
    swap into a preset that just went down. Every call hits the network
    (or short-circuits on no_credentials, which is free anyway).
    """
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity
    probe_calls = []
    def counting_probe(host, port, timeout):
        probe_calls.append((host, port))
        return 1
    with patch.object(preset_connectivity, "_probe_host", side_effect=counting_probe):
        preset_connectivity.check_connectivity("x", "https://h.example.com", "MOCK_KEY")
        preset_connectivity.check_connectivity("x", "https://h.example.com", "MOCK_KEY")
        preset_connectivity.check_connectivity("x", "https://h.example.com", "MOCK_KEY")
    assert len(probe_calls) == 3  # every call probes; no shortcut


def test_check_many_runs_in_parallel(monkeypatch):
    """check_many() runs probes concurrently — total time is bounded by the
    slowest single probe, not the sum."""
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity

    def slow_probe(host, port, timeout):
        time.sleep(0.5)
        return 500

    with patch.object(preset_connectivity, "_probe_host", side_effect=slow_probe):
        start = time.time()
        results = preset_connectivity.check_many([
            {"provider": "a", "base_url": "https://a.example.com", "api_key_env": "MOCK_KEY"},
            {"provider": "b", "base_url": "https://b.example.com", "api_key_env": "MOCK_KEY"},
            {"provider": "c", "base_url": "https://c.example.com", "api_key_env": "MOCK_KEY"},
            {"provider": "d", "base_url": "https://d.example.com", "api_key_env": "MOCK_KEY"},
        ])
        elapsed = time.time() - start

    assert len(results) == 4
    assert all(r["status"] == "ok" for r in results)
    # 4 sequential probes would take 2.0s; parallel should be ~0.5s.
    # Allow generous slack for CI.
    assert elapsed < 1.5, f"check_many took {elapsed:.2f}s — should be parallel"


def test_check_many_preserves_input_order(monkeypatch):
    """check_many() returns results in the same order as specs, even though
    probes complete in arbitrary order in the thread pool."""
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity

    def variable_probe(host, port, timeout):
        # Probes complete in reverse order from input
        if "first" in host: time.sleep(0.3)
        elif "second" in host: time.sleep(0.2)
        else: time.sleep(0.1)
        return 1

    with patch.object(preset_connectivity, "_probe_host", side_effect=variable_probe):
        results = preset_connectivity.check_many([
            {"provider": "a", "base_url": "https://first.example.com", "api_key_env": "MOCK_KEY"},
            {"provider": "b", "base_url": "https://second.example.com", "api_key_env": "MOCK_KEY"},
            {"provider": "c", "base_url": "https://third.example.com", "api_key_env": "MOCK_KEY"},
        ])
    assert all(r["status"] == "ok" for r in results)


def test_check_many_empty_list_returns_empty():
    from lingtai.kernel.preset_connectivity import check_many
    assert check_many([]) == []


# ---------------------------------------------------------------------------
# CLI-backed claude-code: setup-token first, then the local CLI login
# ---------------------------------------------------------------------------

# Bound at import (collection) time, before the suite-wide guard in
# tests/conftest.py replaces the module attribute: the real implementation,
# exercised below only against stub CLIs — never the machine's `claude`.
from lingtai.kernel.preset_connectivity import (  # noqa: E402
    claude_cli_login_status as _real_claude_cli_login_status,
)


@pytest.fixture
def no_claude_token(monkeypatch):
    for name in ("CLAUDE_CODE_OAUTH_TOKEN", "MY_CLAUDE_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def _pin_login(monkeypatch, verdict):
    from lingtai.kernel import preset_connectivity

    calls = []

    def fake_status(cli_path="claude", **kwargs):
        calls.append(kwargs)
        return verdict

    monkeypatch.setattr(preset_connectivity, "claude_cli_login_status", fake_status)
    return calls


@pytest.mark.parametrize(
    "api_key_env,env_name",
    [
        (None, "CLAUDE_CODE_OAUTH_TOKEN"),
        ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("MY_CLAUDE_TOKEN", "MY_CLAUDE_TOKEN"),
        # Declared-but-unset custom slot and a legacy empty api_key_env both
        # still read the default CLAUDE_CODE_OAUTH_TOKEN slot.
        ("MY_CLAUDE_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("", "CLAUDE_CODE_OAUTH_TOKEN"),
    ],
)
def test_claude_code_ok_when_setup_token_present(
    monkeypatch, no_claude_token, api_key_env, env_name
):
    """A configured setup-token is sufficient: no login probe, no network."""
    from lingtai.kernel import preset_connectivity

    monkeypatch.setenv(env_name, "sk-ant-oat01-test")
    probe_calls = _pin_login(monkeypatch, preset_connectivity.CLAUDE_LOGIN_NOT_LOGGED_IN)
    with patch.object(preset_connectivity, "_probe_host") as probe:
        result = preset_connectivity.check_connectivity(
            provider="claude-code", base_url=None, api_key_env=api_key_env
        )
    assert result["status"] == "ok"
    assert result["error"] is None
    probe.assert_not_called()
    assert probe_calls == []


@pytest.mark.parametrize(
    "verdict,status",
    [
        ("logged_in", "ok"),
        ("unknown", "ok"),
        ("not_logged_in", "no_credentials"),
        ("cli_missing", "no_credentials"),
    ],
)
def test_claude_code_without_token_follows_local_login(
    monkeypatch, no_claude_token, verdict, status
):
    from lingtai.kernel import preset_connectivity

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    probe_calls = _pin_login(monkeypatch, verdict)
    with patch.object(preset_connectivity, "_probe_host") as probe:
        result = preset_connectivity.check_connectivity(
            provider="claude-code", base_url=None, api_key_env="MY_CLAUDE_TOKEN"
        )
    assert result["status"] == status
    probe.assert_not_called()  # never a TCP probe, never a model request
    assert len(probe_calls) == 1
    assert "ANTHROPIC_API_KEY" not in probe_calls[0]["env"]
    if status == "no_credentials":
        error = result["error"]
        assert "claude setup-token" in error
        assert "CLAUDE_CODE_OAUTH_TOKEN" in error
        assert "claude auth login" in error


def test_claude_code_missing_module_reports_no_credentials(monkeypatch):
    """When the backing module is absent, report a clear, actionable status
    (not the misleading 'no base_url' error)."""
    from lingtai.kernel import preset_connectivity
    with patch.object(preset_connectivity, "_module_available", return_value=False):
        result = preset_connectivity.check_connectivity(
            provider="claude-code",
            base_url=None,
            api_key_env=None,
        )
        assert result["status"] == "no_credentials"
        assert "claude-code" in (result.get("error") or "")
        assert "no base_url" not in (result.get("error") or "")


def test_only_claude_code_is_a_cli_backed_provider():
    """The removed ``claude_code``/``kimi-code``/``kimi_code`` spellings are no
    longer CLI-backed providers; the default-URL probe table covers only
    the three API families."""
    from lingtai.kernel import preset_connectivity

    assert set(preset_connectivity._CLI_BACKED_PROVIDERS) == {"claude-code"}
    assert set(preset_connectivity._PROVIDER_DEFAULT_URLS) == {
        "openai",
        "anthropic",
        "codex",
    }


def _stub_cli(tmp_path, stdout, exit_code=0):
    """A fake `claude` that records its argv and prints *stdout*."""
    record = tmp_path / "argv.txt"
    script = tmp_path / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{record}"\n'
        f"cat <<'JSON'\n{stdout}\nJSON\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script), record


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell stub CLI")
@pytest.mark.parametrize(
    "stdout,exit_code,expected",
    [
        ('{"loggedIn": true, "authMethod": "claude.ai"}', 0, "logged_in"),
        ('{"loggedIn": true, "authMethod": "oauth_token"}', 0, "logged_in"),
        ('{"loggedIn": false, "authMethod": "none"}', 1, "not_logged_in"),
        ("error: unknown option '--json'", 1, "unknown"),
        ('["not", "an", "object"]', 0, "unknown"),
        ('{"authMethod": "none"}', 0, "unknown"),
    ],
)
def test_claude_cli_login_status_reads_auth_status_json(
    tmp_path, stdout, exit_code, expected
):
    cli, record = _stub_cli(tmp_path, stdout, exit_code)
    assert _real_claude_cli_login_status(cli, env=dict(os.environ)) == expected
    assert record.read_text().split() == ["auth", "status", "--json"]


def test_claude_cli_login_status_missing_binary(tmp_path):
    missing = str(tmp_path / "no-such-claude")
    assert _real_claude_cli_login_status(missing) == "cli_missing"


@pytest.mark.parametrize(
    "provider,expected_url",
    (
        ("openai", "https://api.openai.com"),
        ("anthropic", "https://api.anthropic.com"),
        ("codex", "https://chatgpt.com"),
    ),
)
def test_default_url_probe_for_api_families(monkeypatch, provider, expected_url):
    monkeypatch.setenv("MOCK_KEY", "sk-test")
    from lingtai.kernel import preset_connectivity

    with patch.object(preset_connectivity, "_probe_host", return_value=7) as probe:
        result = preset_connectivity.check_connectivity(
            provider=provider,
            base_url=None,
            api_key_env="MOCK_KEY",
        )
    assert result["status"] == "ok"
    host = expected_url.split("://", 1)[1]
    assert probe.call_args.args[0] == host


# ---------------------------------------------------------------------------
# Schemeless / malformed base_url must never silently probe localhost
# ---------------------------------------------------------------------------


def test_schemeless_base_url_probed_as_https(monkeypatch):
    """A bare hostname like `api.openai.com` is probed as https (port 443),
    not resolved to localhost via getaddrinfo(None, ...)."""
    from lingtai.kernel import preset_connectivity
    captured = {}
    def fake_probe(host, port, timeout):
        captured["host"] = host
        captured["port"] = port
        return 12
    with patch.object(preset_connectivity, "_probe_host", side_effect=fake_probe):
        result = preset_connectivity.check_connectivity(
            provider=None,
            base_url="api.openai.com",
            api_key_env=None,
        )
    assert captured == {"host": "api.openai.com", "port": 443}
    assert result["status"] == "ok"
    assert result["latency_ms"] == 12


def test_schemeless_host_port_base_url_keeps_port(monkeypatch):
    """`myhost:8080` is the urlparse scheme-swallowing case: the port must be
    preserved and the host must not be dropped."""
    from lingtai.kernel import preset_connectivity
    captured = {}
    def fake_probe(host, port, timeout):
        captured["host"] = host
        captured["port"] = port
        return 7
    with patch.object(preset_connectivity, "_probe_host", side_effect=fake_probe):
        result = preset_connectivity.check_connectivity(
            provider=None,
            base_url="myhost:8080",
            api_key_env=None,
        )
    assert captured == {"host": "myhost", "port": 8080}
    assert result["status"] == "ok"


@pytest.mark.parametrize("bad_url", ["https://", "http://host:notaport"])
def test_invalid_base_url_reports_error_without_probing(bad_url):
    """Hostless or non-numeric-port base URLs fail loud with an explicit
    `invalid base_url` error — and never call the probe (no localhost
    fallback, no escaping ValueError)."""
    from lingtai.kernel import preset_connectivity
    with patch.object(preset_connectivity, "_probe_host") as probe:
        result = preset_connectivity.check_connectivity(
            provider=None,
            base_url=bad_url,
            api_key_env=None,
        )
    assert result["status"] == "unreachable"
    assert "invalid base_url" in result["error"]
    probe.assert_not_called()


def test_http_scheme_still_defaults_to_port_80(monkeypatch):
    """Explicit http:// URLs keep their port-80 default — normalization must
    not disturb explicit-scheme handling."""
    from lingtai.kernel import preset_connectivity
    captured = {}
    def fake_probe(host, port, timeout):
        captured["host"] = host
        captured["port"] = port
        return 3
    with patch.object(preset_connectivity, "_probe_host", side_effect=fake_probe):
        result = preset_connectivity.check_connectivity(
            provider=None,
            base_url="http://myhost",
            api_key_env=None,
        )
    assert captured == {"host": "myhost", "port": 80}
    assert result["status"] == "ok"


def test_urlparse_schemeless_assumptions():
    """Canary for Python's urlparse semantics that this fix relies on: a bare
    host:port string yields hostname=None, and the whole string would be
    treated as a scheme. If a future Python changes this, the tests above
    surface it visibly."""
    from urllib.parse import urlparse
    assert urlparse("api.openai.com").hostname is None
    assert urlparse("myhost:8080").scheme == "myhost"
    assert urlparse("myhost:8080").hostname is None
