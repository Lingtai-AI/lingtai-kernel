# tests/test_system_refresh_rebuild_context.py
"""Regression tests for the opt-in ``system(action='refresh', rebuild_context=...)``.

Jason's requirement (Telegram mimo-1, 2026-07-02):
  - A default ``system.refresh`` must NOT rebuild provider context.
  - Only an explicit opt-in ``rebuild_context=true`` may trigger a context
    rebuild on refresh.
  - The preferred explicit provider-context rebuild path remains
    ``system(action='summarize', rebuild_only=true)``; refresh rebuild is the
    exceptional, explicit escape hatch.

The one code lever that explicitly forces a provider-context rebuild
attributable to *refresh* is the Codex adapter rebuild in the in-process live
refresh path (``Agent._setup_from_init``): rebuilding the adapter drops the warm
in-memory continuation/cache epoch, so the next model call is a full replay.
These tests pin that this rebuild is now gated on the opt-in flag, that the flag
threads correctly from the tool schema/arg through the refresh handshake, and
that omitted/false/true behave as specified.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Schema: the tool advertises rebuild_context as an optional boolean (default
# false), so tool-call providers can serialize it.
# ---------------------------------------------------------------------------


def test_schema_exposes_rebuild_context_boolean():
    from lingtai_kernel.intrinsics.system.schema import get_schema

    schema = get_schema("en")
    props = schema["properties"]
    assert "rebuild_context" in props, "system tool schema must expose rebuild_context"
    assert props["rebuild_context"]["type"] == "boolean"
    # Optional — not required; default is false semantics.
    assert "rebuild_context" not in schema.get("required", [])
    # Description must make the opt-in and the summarize-first guidance explicit.
    desc = props["rebuild_context"]["description"].lower()
    assert "refresh" in desc
    assert "rebuild" in desc
    assert "summarize" in desc  # points at the preferred explicit path


def test_schema_is_json_serializable_with_rebuild_context():
    from lingtai_kernel.intrinsics.system.schema import get_schema

    # Tool schemas are shipped to providers as JSON; ensure the new field
    # round-trips without loss.
    schema = get_schema("en")
    dumped = json.dumps(schema)
    reloaded = json.loads(dumped)
    assert reloaded["properties"]["rebuild_context"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# _refresh: the flag is parsed (truthy-only) and threaded into _perform_refresh
# as the rebuild_context kwarg. Omitted/false → False; true → True.
# ---------------------------------------------------------------------------


def _refresh_agent():
    """A minimal agent stub for exercising _refresh's arg handling."""
    agent = MagicMock()
    agent._config.language = "en"
    # No preset swap, no MCP retry surprises.
    agent._retry_failed_mcps = None
    return agent


def _call_refresh(args: dict):
    from lingtai_kernel.intrinsics.system.preset import _refresh

    agent = _refresh_agent()
    # _refresh reads agent._retry_failed_mcps via getattr; ensure it's absent.
    del agent._retry_failed_mcps
    result = _refresh(agent, args)
    return agent, result


def test_refresh_default_does_not_request_rebuild():
    agent, result = _call_refresh({"action": "refresh"})
    assert result["status"] == "ok"
    agent._perform_refresh.assert_called_once()
    _, kwargs = agent._perform_refresh.call_args
    assert kwargs.get("rebuild_context") is False


def test_refresh_false_does_not_request_rebuild():
    agent, result = _call_refresh({"action": "refresh", "rebuild_context": False})
    assert result["status"] == "ok"
    _, kwargs = agent._perform_refresh.call_args
    assert kwargs.get("rebuild_context") is False


def test_refresh_true_requests_rebuild():
    agent, result = _call_refresh({"action": "refresh", "rebuild_context": True})
    assert result["status"] == "ok"
    _, kwargs = agent._perform_refresh.call_args
    assert kwargs.get("rebuild_context") is True


def test_refresh_rebuild_context_truthy_string():
    # Some tool-call providers serialize booleans as strings; accept explicit
    # truthy strings but never treat an empty/absent value as opt-in.
    agent, result = _call_refresh({"action": "refresh", "rebuild_context": "true"})
    _, kwargs = agent._perform_refresh.call_args
    assert kwargs.get("rebuild_context") is True

    agent2, _ = _call_refresh({"action": "refresh", "rebuild_context": ""})
    _, kwargs2 = agent2._perform_refresh.call_args
    assert kwargs2.get("rebuild_context") is False


# ---------------------------------------------------------------------------
# _perform_refresh: the rebuild_context intent is threaded to the relaunched
# process via a one-shot env marker on the watcher env.
# ---------------------------------------------------------------------------


def _perform_refresh_agent(tmp_path: Path):
    from lingtai_kernel.base_agent.lifecycle import _perform_refresh  # noqa: F401

    agent = MagicMock()
    agent._working_dir = tmp_path
    agent._llm_worker_interface_poisoned = False
    agent.agent_name = "test-agent"
    # A real cmd so we get past the _build_launch_cmd None guard.
    agent._build_launch_cmd.return_value = ["lingtai-agent", "run"]
    (tmp_path / "logs").mkdir(exist_ok=True)
    return agent


def test_perform_refresh_sets_rebuild_env_only_when_opted_in(tmp_path):
    from lingtai_kernel.base_agent import lifecycle

    captured = {}

    def fake_popen(*a, **kw):
        captured["env"] = kw.get("env")
        return MagicMock()

    # rebuild_context=True → env marker present.
    agent = _perform_refresh_agent(tmp_path)
    with patch.object(lifecycle.subprocess, "Popen", side_effect=fake_popen):
        lifecycle._perform_refresh(agent, rebuild_context=True)
    assert captured["env"].get("LINGTAI_REFRESH_REBUILD_CONTEXT") == "1"

    # Default (no rebuild_context) → env marker absent.
    (tmp_path / ".refresh.taken").unlink(missing_ok=True)
    agent2 = _perform_refresh_agent(tmp_path)
    with patch.object(lifecycle.subprocess, "Popen", side_effect=fake_popen):
        lifecycle._perform_refresh(agent2)
    assert "LINGTAI_REFRESH_REBUILD_CONTEXT" not in captured["env"]


# ---------------------------------------------------------------------------
# _setup_from_init (live Codex refresh): the codex adapter rebuild — the one
# refresh-attributable provider-context rebuild lever — fires ONLY when
# rebuild_context is opted in. Omitted/false → adapter kept (warm prefix);
# true → fresh adapter (context rebuild). The affinity id is stable regardless.
# ---------------------------------------------------------------------------


def _codex_agent(tmp_path: Path, epoch: float):
    """Real Agent backed by a real Codex LLMService (mirrors test_deep_refresh)."""
    from lingtai.agent import Agent
    from lingtai.llm.service import (
        LLMService,
        build_provider_defaults_from_manifest_llm,
    )
    from lingtai_kernel.config import AgentConfig
    import lingtai  # noqa: F401  (registers the codex adapter factory)

    from test_deep_refresh import _make_init

    init = _make_init(provider="codex", model="gpt-5.5")
    init["manifest"]["max_rpm"] = 60
    (tmp_path / "init.json").write_text(json.dumps(init))

    llm = init["manifest"]["llm"]
    provider_defaults = build_provider_defaults_from_manifest_llm(
        llm, max_rpm=60, working_dir=tmp_path
    )
    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=epoch
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        service = LLMService(
            provider="codex",
            model="gpt-5.5",
            api_key="fake",
            provider_defaults=provider_defaults,
        )
        agent = Agent(
            service,
            agent_name="test-agent",
            working_dir=tmp_path,
            config=AgentConfig(),
        )
    return agent


def _live_refresh_session(agent):
    mock_interface = MagicMock()
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = mock_interface
    agent._session = mock_session
    return mock_session, mock_interface


def test_codex_default_refresh_keeps_adapter(tmp_path, monkeypatch):
    """Default (rebuild_context omitted) live refresh must NOT rebuild the
    Codex adapter — the warm continuation/cache prefix is preserved."""
    monkeypatch.delenv("LINGTAI_REFRESH_REBUILD_CONTEXT", raising=False)
    agent = _codex_agent(tmp_path, epoch=1_700_000_000)
    agent._sealed = True
    old_adapter = agent.service.get_adapter("codex")
    mock_session, mock_interface = _live_refresh_session(agent)

    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=1_700_000_500
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        agent._setup_from_init()  # default: no rebuild_context

    new_adapter = agent.service.get_adapter("codex")
    # Same adapter instance kept — no forced provider-context rebuild.
    assert new_adapter is old_adapter
    # History is still replayed onto the (unchanged) session/adapter.
    mock_session._rebuild_session.assert_called_once_with(mock_interface)


def test_codex_refresh_false_keeps_adapter(tmp_path, monkeypatch):
    monkeypatch.delenv("LINGTAI_REFRESH_REBUILD_CONTEXT", raising=False)
    agent = _codex_agent(tmp_path, epoch=1_700_000_000)
    agent._sealed = True
    old_adapter = agent.service.get_adapter("codex")
    _live_refresh_session(agent)

    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=1_700_000_500
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        agent._setup_from_init(rebuild_context=False)

    assert agent.service.get_adapter("codex") is old_adapter


def test_codex_refresh_true_rebuilds_adapter_with_stable_id(tmp_path, monkeypatch):
    """Explicit rebuild_context=true live refresh rebuilds the Codex adapter
    (fresh epoch = provider-context rebuild) while KEEPING the affinity id."""
    from lingtai.llm.openai.adapter import _codex_session_id

    monkeypatch.delenv("LINGTAI_REFRESH_REBUILD_CONTEXT", raising=False)
    agent = _codex_agent(tmp_path, epoch=1_700_000_000)
    agent._sealed = True
    old_adapter = agent.service.get_adapter("codex")
    old_id, _ = old_adapter._resolve_codex_ids("gpt-5.5")
    mock_session, mock_interface = _live_refresh_session(agent)

    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=1_700_000_500
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        agent._setup_from_init(rebuild_context=True)

    new_adapter = agent.service.get_adapter("codex")
    new_id, _ = new_adapter._resolve_codex_ids("gpt-5.5")
    assert new_adapter is not old_adapter  # genuinely fresh adapter
    assert new_id == old_id  # affinity id stable across the rebuild
    anchor = str((tmp_path / "init.json").resolve())
    assert new_id == _codex_session_id(anchor, 0)
    mock_session._rebuild_session.assert_called_once_with(mock_interface)


def test_codex_refresh_env_marker_opts_in(tmp_path, monkeypatch):
    """The one-shot env marker (set by _perform_refresh on the relaunch env)
    opts the boot _setup_from_init into the rebuild, then is consumed."""
    agent = _codex_agent(tmp_path, epoch=1_700_000_000)
    agent._sealed = True
    old_adapter = agent.service.get_adapter("codex")
    _live_refresh_session(agent)

    monkeypatch.setenv("LINGTAI_REFRESH_REBUILD_CONTEXT", "1")
    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=1_700_000_500
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        agent._setup_from_init()  # reads the marker → rebuild

    assert agent.service.get_adapter("codex") is not old_adapter
    # Marker is consumed (one-shot) so a later molt-reload does not rebuild.
    import os

    assert "LINGTAI_REFRESH_REBUILD_CONTEXT" not in os.environ
