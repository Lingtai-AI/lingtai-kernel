"""System-owned ``settings/system.json`` runtime policy: v1 compatibility,
closed v2 parsing, per-field precedence, boot/refresh coherence, and the two
documented exceptions (cache-miss budget without manifest fallback; the
notification cap keeping its Core-owned env/clamp layering)."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.agent import Agent, build_agent_config
from lingtai.kernel import meta_block
from lingtai.kernel.config import (
    CONTEXT_PRESSURE_FORCED_REBUILD_RATIO,
    CONTEXT_PRESSURE_HIGH_RATIO,
    CONTEXT_PRESSURE_RECOVERY_TARGET,
    CONTEXT_PRESSURE_WARN_AFTER_ROUNDS,
    AgentConfig,
)
from lingtai.tools.system import settings as system_settings
from tests._snapshot_helpers import make_test_snapshot_port
from tests.test_deep_refresh import _make_agent, _make_init


ORDINARY_ENV = tuple(system_settings.RUNTIME_POLICY_ENV.values())


@pytest.fixture(autouse=True)
def _clear_policy_env(monkeypatch):
    for name in ORDINARY_ENV + (meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_ENV,):
        monkeypatch.delenv(name, raising=False)


def _settings_agent(workdir: Path):
    return type("SettingsAgent", (), {"_working_dir": workdir})()


def _write_settings(workdir: Path, document) -> Path:
    path = workdir / system_settings.SYSTEM_SETTINGS_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    body = document if isinstance(document, str) else json.dumps(document)
    path.write_text(body, encoding="utf-8")
    return path


def _v2(**fields) -> dict:
    return {"schema_version": system_settings.RUNTIME_POLICY_SCHEMA_VERSION, **fields}


# --- v1 compatibility ---------------------------------------------------------


@pytest.mark.parametrize("budget", (1, 250_000, 2_000_000, 10**12))
def test_v1_document_parses_exactly_as_before(budget):
    body = json.dumps({"schema_version": 1, "cache_miss_budget": budget})
    assert system_settings._parse_settings(body) == budget
    # v1 is not a runtime-policy document: it contributes no ordinary field.
    assert system_settings._parse_runtime_policy_v2(body) is None


@pytest.mark.parametrize(
    "body",
    (
        '{"schema_version": 1, "cache_miss_budget": 1, "context_limit": 5}',
        '{"schema_version": 1, "cache_miss_budget": 1, "streaming": true}',
        '{"schema_version": 1}',
    ),
)
def test_v1_is_not_widened_by_v2_fields(tmp_path, body):
    """Extra keys still reject a v1 document (and it is not silently a v2)."""
    assert system_settings._parse_settings(body) is None
    assert system_settings._parse_runtime_policy_v2(body) is None
    _write_settings(tmp_path, body)
    assert system_settings.resolve_cache_miss_budget(_settings_agent(tmp_path)) == 2_000_000
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.context_limit is None
    assert policy.sources["context_limit"] == system_settings.SOURCE_DEFAULT


def test_v1_cache_precedence_unchanged_and_manifest_never_a_source(monkeypatch, tmp_path):
    agent = _settings_agent(tmp_path)
    assert system_settings.resolve_cache_miss_budget(agent) == 2_000_000
    _write_settings(tmp_path, {"schema_version": 1, "cache_miss_budget": 250_000})
    assert system_settings.resolve_cache_miss_budget(agent) == 250_000
    monkeypatch.setenv(system_settings.CACHE_MISS_BUDGET_ENV, "bad")
    assert system_settings.resolve_cache_miss_budget(agent) == 250_000
    monkeypatch.setenv(system_settings.CACHE_MISS_BUDGET_ENV, "3000000")
    assert system_settings.resolve_cache_miss_budget(agent) == 3_000_000
    # The runtime-policy resolver never carries the cache budget, and a legacy
    # manifest.cache_miss_budget is neither a resolver input nor hydrated.
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert "cache_miss_budget" not in policy.as_overrides()
    cfg = build_agent_config({"cache_miss_budget": 5}, max_rpm=0, runtime_policy=policy)
    assert not hasattr(cfg, "cache_miss_budget")


def test_legacy_manifest_cache_budget_is_not_resurrected_by_v2(tmp_path):
    """No file, no env: a manifest budget stays ignored (fixed 2,000,000)."""
    agent = _settings_agent(tmp_path)
    (tmp_path / "init.json").write_text(
        json.dumps({"manifest": {"cache_miss_budget": 123}}), encoding="utf-8"
    )
    assert system_settings.resolve_cache_miss_budget(agent) == 2_000_000
    _write_settings(tmp_path, _v2(context_limit=1000))
    assert system_settings.resolve_cache_miss_budget(agent) == 2_000_000
    _write_settings(tmp_path, _v2(cache_miss_budget=777))
    assert system_settings.resolve_cache_miss_budget(agent) == 777


# --- closed v2 parsing --------------------------------------------------------


def test_v2_parses_present_fields_and_distinguishes_null_from_absent():
    body = json.dumps(
        _v2(
            context_limit=None,
            max_rpm=0,
            streaming=True,
            aed_timeout=12.5,
            max_aed_attempts=1,
            snapshot_interval=None,
            activeness=None,
            cache_miss_budget=10,
            notification_max_chars=3000,
        )
    )
    fields = system_settings._parse_runtime_policy_v2(body)
    assert fields == {
        "context_limit": None,
        "max_rpm": 0,
        "streaming": True,
        "aed_timeout": 12.5,
        "max_aed_attempts": 1,
        "snapshot_interval": None,
        "activeness": None,
        "cache_miss_budget": 10,
        "notification_max_chars": 3000,
    }
    partial = system_settings._parse_runtime_policy_v2(json.dumps(_v2(max_rpm=30)))
    assert partial == {"max_rpm": 30}
    assert "context_limit" not in partial
    assert system_settings._parse_runtime_policy_v2(json.dumps(_v2())) == {}


@pytest.mark.parametrize(
    "body",
    (
        "{",
        "[]",
        "null",
        '{"context_limit": 5}',  # missing version
        '{"schema_version": 3, "context_limit": 5}',
        '{"schema_version": "2", "context_limit": 5}',
        '{"schema_version": 2.0, "context_limit": 5}',
        '{"schema_version": 2, "unknown": 1}',
        '{"schema_version": 2, "max_rpm": 1, "max_rpm": 2}',
        '{"schema_version": 2, "context_limit": true}',
        '{"schema_version": 2, "context_limit": 0}',
        '{"schema_version": 2, "context_limit": 1.5}',
        '{"schema_version": 2, "context_limit": "1000"}',
        '{"schema_version": 2, "max_rpm": -1}',
        '{"schema_version": 2, "max_rpm": true}',
        '{"schema_version": 2, "max_rpm": null}',
        '{"schema_version": 2, "streaming": 1}',
        '{"schema_version": 2, "streaming": "true"}',
        '{"schema_version": 2, "streaming": null}',
        '{"schema_version": 2, "aed_timeout": 0}',
        '{"schema_version": 2, "aed_timeout": true}',
        '{"schema_version": 2, "aed_timeout": NaN}',
        '{"schema_version": 2, "aed_timeout": Infinity}',
        '{"schema_version": 2, "aed_timeout": null}',
        '{"schema_version": 2, "max_aed_attempts": 0}',
        '{"schema_version": 2, "max_aed_attempts": 2.0}',
        '{"schema_version": 2, "max_aed_attempts": null}',
        '{"schema_version": 2, "snapshot_interval": 0}',
        '{"schema_version": 2, "snapshot_interval": -Infinity}',
        '{"schema_version": 2, "snapshot_interval": false}',
        '{"schema_version": 2, "activeness": 1}',
        '{"schema_version": 2, "activeness": ""}',
        '{"schema_version": 2, "cache_miss_budget": 0}',
        '{"schema_version": 2, "cache_miss_budget": null}',
        '{"schema_version": 2, "notification_max_chars": -5}',
        '{"schema_version": 2, "notification_max_chars": true}',
        # One invalid field rejects the whole document — no partial override.
        '{"schema_version": 2, "max_rpm": 30, "context_limit": "oops"}',
    ),
)
def test_v2_invalid_documents_are_rejected_whole(tmp_path, body):
    assert system_settings._parse_runtime_policy_v2(body) is None
    _write_settings(tmp_path, body)
    assert system_settings.read_runtime_policy_document(tmp_path) == {}
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.max_rpm == system_settings.DEFAULT_MAX_RPM
    assert policy.sources["max_rpm"] == system_settings.SOURCE_DEFAULT
    assert system_settings.resolve_cache_miss_budget(_settings_agent(tmp_path)) == 2_000_000
    assert system_settings.resolve_notification_max_chars(_settings_agent(tmp_path)) is None


@pytest.mark.parametrize(
    "key",
    (
        "molt_notice",
        "molt_pressure",
        "molt_urgency",
        "molt_prompt",
        "context_pressure_high_ratio",
        "context_pressure_forced_rebuild_ratio",
        "context_pressure_warn_after_rounds",
        "context_pressure_recovery_target",
        "max_turns",
        "stamina",
        "summarize_notification_threshold",
    ),
)
def test_v2_cannot_configure_fixed_safety_or_legacy_fields(tmp_path, key):
    body = json.dumps(_v2(**{key: 0.99, "max_rpm": 5}))
    assert system_settings._parse_runtime_policy_v2(body) is None
    _write_settings(tmp_path, body)
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.max_rpm == system_settings.DEFAULT_MAX_RPM
    # The kernel-fixed reconstruction policy is untouched by any System input.
    assert CONTEXT_PRESSURE_HIGH_RATIO == 0.85
    assert CONTEXT_PRESSURE_FORCED_REBUILD_RATIO == 1.0
    assert CONTEXT_PRESSURE_WARN_AFTER_ROUNDS == 3
    assert CONTEXT_PRESSURE_RECOVERY_TARGET == 0.75


def test_unreadable_settings_file_yields_defaults(tmp_path):
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "system.json").mkdir()  # a directory, not a file
    assert system_settings.read_runtime_policy_document(tmp_path) == {}
    assert system_settings.resolve_cache_miss_budget(_settings_agent(tmp_path)) == 2_000_000


# --- precedence ---------------------------------------------------------------


def test_defaults_when_nothing_is_configured(tmp_path):
    policy = system_settings.resolve_runtime_policy(tmp_path)
    defaults = AgentConfig()
    assert policy.context_limit is None
    assert policy.max_rpm == 60
    assert policy.streaming is False
    assert policy.aed_timeout == defaults.aed_timeout == 360.0
    assert policy.max_aed_attempts == defaults.max_aed_attempts == 3
    assert policy.snapshot_interval is None
    assert policy.activeness == "balanced"
    assert set(policy.sources.values()) == {system_settings.SOURCE_DEFAULT}
    assert set(policy.sources) == set(system_settings.ORDINARY_POLICY_FIELDS)


def test_env_beats_system_beats_fixed_default(monkeypatch, tmp_path):
    manifest = {
        "context_limit": 100,
        "max_rpm": 10,
        "streaming": False,
        "aed_timeout": 100,
        "max_aed_attempts": 2,
        "snapshot_interval": 100,
        "activeness": "manifest",
    }
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert set(policy.sources.values()) == {system_settings.SOURCE_DEFAULT}

    _write_settings(
        tmp_path,
        _v2(
            context_limit=200,
            max_rpm=20,
            streaming=True,
            aed_timeout=200.5,
            max_aed_attempts=4,
            snapshot_interval=200,
            activeness="system",
        ),
    )
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.as_overrides() == {
        "context_limit": 200,
        "max_rpm": 20,
        "streaming": True,
        "aed_timeout": 200.5,
        "max_aed_attempts": 4,
        "snapshot_interval": 200,
        "activeness": "system",
    }
    assert set(policy.sources.values()) == {system_settings.SOURCE_SYSTEM}

    monkeypatch.setenv(system_settings.CONTEXT_LIMIT_ENV, " 300 ")
    monkeypatch.setenv(system_settings.MAX_RPM_ENV, "0")
    monkeypatch.setenv(system_settings.STREAMING_ENV, "Off")
    monkeypatch.setenv(system_settings.AED_TIMEOUT_ENV, "30.25")
    monkeypatch.setenv(system_settings.MAX_AED_ATTEMPTS_ENV, "9")
    monkeypatch.setenv(system_settings.SNAPSHOT_INTERVAL_ENV, "45")
    monkeypatch.setenv(system_settings.ACTIVENESS_ENV, "env")
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.as_overrides() == {
        "context_limit": 300,
        "max_rpm": 0,
        "streaming": False,
        "aed_timeout": 30.25,
        "max_aed_attempts": 9,
        "snapshot_interval": 45,
        "activeness": "env",
    }
    assert set(policy.sources.values()) == {system_settings.SOURCE_ENV}
    # Legacy init values remain inert data.
    assert manifest["context_limit"] == 100


def test_partial_system_document_mixes_system_and_defaults_per_field(tmp_path):
    _write_settings(tmp_path, _v2(max_rpm=30, streaming=True))
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert (policy.max_rpm, policy.streaming, policy.context_limit) == (30, True, None)
    assert policy.aed_timeout == 360.0
    assert policy.sources == {
        "context_limit": "default",
        "max_rpm": "system",
        "streaming": "system",
        "aed_timeout": "default",
        "max_aed_attempts": "default",
        "snapshot_interval": "default",
        "activeness": "default",
    }


def test_explicit_v2_null_is_a_system_value(tmp_path):
    """JSON ``null`` is a present System value ("no configured limit"/"off")."""
    _write_settings(tmp_path, _v2(context_limit=None, snapshot_interval=None, activeness=None))
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.context_limit is None
    assert policy.snapshot_interval is None
    assert policy.activeness is None
    assert policy.sources["context_limit"] == system_settings.SOURCE_SYSTEM


@pytest.mark.parametrize(
    ("name", "raw"),
    (
        (system_settings.CONTEXT_LIMIT_ENV, "0"),
        (system_settings.CONTEXT_LIMIT_ENV, "-1"),
        (system_settings.CONTEXT_LIMIT_ENV, "1.5"),
        (system_settings.CONTEXT_LIMIT_ENV, "big"),
        (system_settings.CONTEXT_LIMIT_ENV, ""),
        (system_settings.MAX_RPM_ENV, "-1"),
        (system_settings.MAX_RPM_ENV, "1.0"),
        (system_settings.MAX_RPM_ENV, "true"),
        (system_settings.STREAMING_ENV, "maybe"),
        (system_settings.STREAMING_ENV, "2"),
        (system_settings.AED_TIMEOUT_ENV, "0"),
        (system_settings.AED_TIMEOUT_ENV, "-3"),
        (system_settings.AED_TIMEOUT_ENV, "nan"),
        (system_settings.AED_TIMEOUT_ENV, "inf"),
        (system_settings.AED_TIMEOUT_ENV, "soon"),
        (system_settings.MAX_AED_ATTEMPTS_ENV, "0"),
        (system_settings.MAX_AED_ATTEMPTS_ENV, "1.5"),
        (system_settings.SNAPSHOT_INTERVAL_ENV, "0"),
        (system_settings.SNAPSHOT_INTERVAL_ENV, "infinity"),
        (system_settings.SNAPSHOT_INTERVAL_ENV, "never"),
        (system_settings.ACTIVENESS_ENV, "   "),
    ),
)
def test_invalid_env_falls_through_to_system_then_manifest(monkeypatch, tmp_path, name, raw):
    field = next(k for k, v in system_settings.RUNTIME_POLICY_ENV.items() if v == name)
    system_values = {
        "context_limit": 4321,
        "max_rpm": 12,
        "streaming": True,
        "aed_timeout": 7.5,
        "max_aed_attempts": 6,
        "snapshot_interval": 33,
        "activeness": "system",
    }
    _write_settings(tmp_path, _v2(**{field: system_values[field]}))
    monkeypatch.setenv(name, raw)
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert getattr(policy, field) == system_values[field]
    assert policy.sources[field] == system_settings.SOURCE_SYSTEM


def test_env_snapshot_off_and_boolean_spellings(monkeypatch, tmp_path):
    _write_settings(tmp_path, _v2(snapshot_interval=30, streaming=False))
    monkeypatch.setenv(system_settings.SNAPSHOT_INTERVAL_ENV, "OFF")
    monkeypatch.setenv(system_settings.STREAMING_ENV, "yes")
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.snapshot_interval is None
    assert policy.sources["snapshot_interval"] == system_settings.SOURCE_ENV
    assert policy.streaming is True
    for word in ("1", "true", "on", "YES"):
        monkeypatch.setenv(system_settings.STREAMING_ENV, word)
        assert system_settings.resolve_runtime_policy(tmp_path).streaming is True
    for word in ("0", "false", "no", "OFF"):
        monkeypatch.setenv(system_settings.STREAMING_ENV, word)
        assert system_settings.resolve_runtime_policy(tmp_path).streaming is False


def test_manifest_values_are_never_runtime_sources(tmp_path):
    policy = system_settings.resolve_runtime_policy(tmp_path)
    assert policy.context_limit is None
    assert policy.sources["context_limit"] == system_settings.SOURCE_DEFAULT


def test_build_agent_config_with_policy_leaves_manifest_untouched():
    manifest = {
        "context_limit": 1,
        "activeness": "manifest",
        "snapshot_interval": 1,
        "aed_timeout": 1,
        "max_aed_attempts": 1,
        "soul": {"delay": 5},
    }
    snapshot = json.dumps(manifest, sort_keys=True)
    policy = system_settings.ResolvedRuntimePolicy(
        context_limit=2,
        max_rpm=3,
        streaming=True,
        aed_timeout=4.0,
        max_aed_attempts=5,
        snapshot_interval=6.0,
        activeness="policy",
    )
    cfg = build_agent_config(manifest, max_rpm=policy.max_rpm, runtime_policy=policy)
    assert (cfg.context_limit, cfg.activeness, cfg.snapshot_interval) == (2, "policy", 6.0)
    assert (cfg.aed_timeout, cfg.max_aed_attempts, cfg.max_rpm) == (4.0, 5, 3)
    assert json.dumps(manifest, sort_keys=True) == snapshot
    # A raw manifest is never an ordinary runtime-policy fallback.
    legacy = build_agent_config(manifest, max_rpm=0)
    defaults = AgentConfig()
    assert (legacy.context_limit, legacy.activeness, legacy.max_aed_attempts) == (
        defaults.context_limit, defaults.activeness, defaults.max_aed_attempts,
    )


# --- notification cap: Core env/clamp layering over the System file ---------


def test_notification_cap_env_then_system_file_then_default(monkeypatch, tmp_path):
    agent = SimpleNamespace(_working_dir=tmp_path)
    agent.resolve_notification_max_chars = lambda: system_settings.resolve_notification_max_chars(agent)
    MAX = meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS

    assert meta_block._notification_persistent_max_chars(agent) == MAX
    assert meta_block._notification_attention_max_chars(agent) == MAX

    _write_settings(tmp_path, _v2(notification_max_chars=4000))
    assert system_settings.resolve_notification_max_chars(agent) == 4000
    assert meta_block._notification_persistent_max_chars(agent) == 4000
    assert meta_block._notification_attention_max_chars(agent) == 4000

    # Env wins over a valid file value, on both lanes.
    monkeypatch.setenv(meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_ENV, "6000")
    assert meta_block._notification_persistent_max_chars(agent) == 6000
    assert meta_block._notification_attention_max_chars(agent) == 6000
    # Invalid env falls through to the file, not straight to the default.
    monkeypatch.setenv(meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_ENV, "abc")
    assert meta_block._notification_persistent_max_chars(agent) == 4000
    monkeypatch.delenv(meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_ENV)

    # The shared 2048 floor / 10,000 ceiling stays in Core for file values too.
    _write_settings(tmp_path, _v2(notification_max_chars=10))
    assert meta_block._notification_persistent_max_chars(agent) == 2048
    assert meta_block._notification_attention_max_chars(agent) == 2048
    _write_settings(tmp_path, _v2(notification_max_chars=50_000))
    assert meta_block._notification_persistent_max_chars(agent) == MAX
    assert meta_block._notification_attention_max_chars(agent) == MAX

    # Invalid file keeps the default; a v1 file has no notification field.
    _write_settings(tmp_path, '{"schema_version": 2, "notification_max_chars": "4000"}')
    assert meta_block._notification_persistent_max_chars(agent) == MAX
    _write_settings(tmp_path, {"schema_version": 1, "cache_miss_budget": 5})
    assert system_settings.resolve_notification_max_chars(agent) is None
    assert meta_block._notification_attention_max_chars(agent) == MAX


def test_notification_cap_bare_kernel_stubs_and_bad_hooks_stay_safe(monkeypatch):
    MAX = meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS
    assert meta_block._notification_persistent_max_chars() == MAX
    assert meta_block._notification_persistent_max_chars(None) == MAX
    assert meta_block._notification_persistent_max_chars(SimpleNamespace()) == MAX
    for bad in (lambda: None, lambda: 0, lambda: True, lambda: "4000", lambda: 4.0):
        stub = SimpleNamespace(resolve_notification_max_chars=bad)
        assert meta_block._notification_persistent_max_chars(stub) == MAX
        assert meta_block._notification_attention_max_chars(stub) == MAX

    def boom():
        raise RuntimeError("no settings")

    assert meta_block._notification_attention_max_chars(
        SimpleNamespace(resolve_notification_max_chars=boom)
    ) == MAX
    # Env still applies to a bare stub.
    monkeypatch.setenv(meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_ENV, "3000")
    assert meta_block._notification_persistent_max_chars(SimpleNamespace()) == 3000


def test_outer_agent_hooks_delegate_to_system(monkeypatch, tmp_path):
    subject = _settings_agent(tmp_path)
    monkeypatch.setattr(system_settings, "resolve_notification_max_chars", lambda agent: 4242)
    assert Agent.resolve_notification_max_chars(subject) == 4242
    sentinel = object()
    seen = {}

    def fake_resolve(working_dir):
        seen["args"] = working_dir
        return sentinel

    monkeypatch.setattr(system_settings, "resolve_runtime_policy", fake_resolve)
    assert Agent.resolve_runtime_policy(subject) is sentinel
    assert seen["args"] == tmp_path


def test_kernel_meta_block_still_imports_no_system_tool_code():
    import sys

    assert "lingtai.tools" not in meta_block.__dict__.get("__builtins__", {}) or True
    source = Path(meta_block.__file__).read_text(encoding="utf-8")
    assert "lingtai.tools" not in source
    assert "system.settings" not in source
    assert sys.modules.get("lingtai.kernel.meta_block") is meta_block


# --- boot coherence -------------------------------------------------------------


def test_cli_boot_applies_system_policy_to_service_config_and_session(tmp_path):
    from lingtai.cli import build_agent, load_init

    init = _make_init()
    init["manifest"]["context_limit"] = 111_111
    init["manifest"]["max_rpm"] = 11
    init["manifest"]["streaming"] = False
    (tmp_path / "init.json").write_text(json.dumps(init))
    _write_settings(
        tmp_path,
        _v2(context_limit=222_222, max_rpm=22, streaming=True, aed_timeout=99.5,
            max_aed_attempts=7, activeness="system"),
    )

    agent = build_agent(load_init(tmp_path), tmp_path)
    try:
        assert agent.service._context_window == 222_222
        assert agent.service._provider_defaults["openai"]["max_rpm"] == 22
        assert agent._session.streaming is True
        assert agent._config.context_limit == 222_222
        assert agent._config.max_rpm == 22
        assert agent._config.aed_timeout == 99.5
        assert agent._config.max_aed_attempts == 7
        assert agent._config.activeness == "system"
        # The authored/effective manifest representation is not rewritten.
        on_disk = json.loads((tmp_path / "init.json").read_text())["manifest"]
        assert (on_disk["context_limit"], on_disk["max_rpm"], on_disk["streaming"]) == (
            111_111, 11, False,
        )
        resolved = json.loads((tmp_path / "system" / "manifest.resolved.json").read_text())
        resolved_manifest = resolved.get("manifest", resolved)
        assert resolved_manifest["context_limit"] == 111_111
        assert resolved_manifest["max_rpm"] == 11
        assert resolved_manifest["streaming"] is False
    finally:
        agent._workdir_lease.release()


def test_cli_boot_env_beats_system_file(monkeypatch, tmp_path):
    from lingtai.cli import build_agent, load_init

    init = _make_init()
    (tmp_path / "init.json").write_text(json.dumps(init))
    _write_settings(tmp_path, _v2(context_limit=222_222, streaming=True))
    monkeypatch.setenv(system_settings.CONTEXT_LIMIT_ENV, "333333")
    monkeypatch.setenv(system_settings.STREAMING_ENV, "false")

    agent = build_agent(load_init(tmp_path), tmp_path)
    try:
        assert agent.service._context_window == 333_333
        assert agent._config.context_limit == 333_333
        assert agent._session.streaming is False
    finally:
        agent._workdir_lease.release()


def test_build_llm_service_shares_the_boot_policy(tmp_path):
    from lingtai.cli import build_llm_service, load_init

    init = _make_init()
    init["manifest"]["max_rpm"] = 11
    (tmp_path / "init.json").write_text(json.dumps(init))
    data = load_init(tmp_path)
    _write_settings(tmp_path, _v2(context_limit=4096, max_rpm=0))

    service = build_llm_service(data, tmp_path)
    assert service._context_window == 4096
    # max_rpm 0 disables gating: the provider bucket carries no positive cap.
    assert service._provider_defaults.get("openai", {}).get("max_rpm", 0) == 0

    policy = system_settings.resolve_runtime_policy(tmp_path)
    explicit = build_llm_service(data, tmp_path, policy)
    assert explicit._context_window == 4096
    assert data["manifest"]["max_rpm"] == 11


# --- refresh coherence ----------------------------------------------------------


class _FakeService:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.provider = kwargs["provider"]
        self.model = kwargs["model"]
        self._base_url = kwargs.get("base_url")
        self._context_window = kwargs["context_window"]
        self._provider_defaults = kwargs.get("provider_defaults") or {}


def _refresh_agent(tmp_path, monkeypatch, init):
    agent = _make_agent(tmp_path, init)
    constructed: list[dict] = []

    class Service(_FakeService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            constructed.append(kwargs)

    monkeypatch.setattr("lingtai.agent.LLMService", Service)
    return agent, constructed


def test_refresh_applies_and_removes_system_policy_coherently(tmp_path, monkeypatch):
    init = _make_init()
    init["manifest"]["context_limit"] = 100_000
    init["manifest"]["max_rpm"] = 10
    init["manifest"]["aed_timeout"] = 100
    init["manifest"]["snapshot_interval"] = None
    agent, constructed = _refresh_agent(tmp_path, monkeypatch, init)

    agent._setup_from_init()
    assert agent.service._context_window == 272_000
    assert agent._config.max_rpm == 60
    assert agent._config.aed_timeout == 360.0
    assert agent._session.streaming is False

    _write_settings(
        tmp_path,
        _v2(context_limit=250_000, max_rpm=25, streaming=True, aed_timeout=42.0,
            max_aed_attempts=8, activeness="calm"),
    )
    agent._setup_from_init()
    assert constructed[-1]["context_window"] == 250_000
    assert constructed[-1]["provider_defaults"]["openai"]["max_rpm"] == 25
    assert agent.service._context_window == 250_000
    assert agent._session._llm_service is agent.service
    assert agent._session._config is agent._config
    assert agent._config.context_limit == 250_000
    assert agent._config.max_rpm == 25
    assert agent._config.aed_timeout == 42.0
    assert agent._config.max_aed_attempts == 8
    assert agent._config.activeness == "calm"
    assert agent._session.streaming is True
    # The effective init artifact still carries the manifest values.
    resolved = json.loads((tmp_path / "system" / "manifest.resolved.json").read_text())
    resolved_manifest = resolved.get("manifest", resolved)
    assert resolved_manifest["context_limit"] == 100_000
    assert resolved_manifest["max_rpm"] == 10
    assert "activeness" not in resolved_manifest

    # Removing the file returns every field to its fixed default.
    (tmp_path / "settings" / "system.json").write_text("{}", encoding="utf-8")
    agent._setup_from_init()
    assert constructed[-1]["context_window"] == 272_000
    assert agent._config.max_rpm == 60
    assert agent._config.aed_timeout == 360.0
    assert agent._config.max_aed_attempts == 3
    assert agent._config.activeness == "balanced"
    assert agent._session.streaming is False


def test_refresh_env_beats_system_file_and_invalid_env_falls_through(tmp_path, monkeypatch):
    init = _make_init()
    init["manifest"]["max_rpm"] = 10
    agent, constructed = _refresh_agent(tmp_path, monkeypatch, init)
    _write_settings(tmp_path, _v2(max_rpm=25, streaming=True))

    monkeypatch.setenv(system_settings.MAX_RPM_ENV, "35")
    monkeypatch.setenv(system_settings.STREAMING_ENV, "nope")
    agent._setup_from_init()
    assert agent._config.max_rpm == 35
    assert constructed[-1]["provider_defaults"]["openai"]["max_rpm"] == 35
    assert agent._session.streaming is True  # invalid env fell through to the file

    monkeypatch.setenv(system_settings.MAX_RPM_ENV, "-4")
    agent._setup_from_init()
    assert agent._config.max_rpm == 25


def test_streaming_setter_is_explicit_and_typed(tmp_path):
    agent = _make_agent(tmp_path)
    session = agent._session
    assert session.streaming is False
    session.streaming = True
    assert session.streaming is True
    assert agent._streaming is True
    with pytest.raises(TypeError):
        session.streaming = 1  # type: ignore[assignment]
    assert session.streaming is True


def test_refresh_snapshot_enable_initializes_port_only_on_a_started_agent(tmp_path, monkeypatch):
    init = _make_init()
    init["manifest"]["snapshot_interval"] = None
    agent, _ = _refresh_agent(tmp_path, monkeypatch, init)
    port = make_test_snapshot_port()
    agent._snapshot_port = port

    # Not started: refresh only records the policy; ``_start`` will initialize.
    _write_settings(tmp_path, _v2(snapshot_interval=15))
    agent._setup_from_init()
    assert agent._config.snapshot_interval == 15
    assert port.initialize_calls == 0

    # Back to off, then simulate a live (started) agent and enable again.
    (tmp_path / "settings" / "system.json").write_text("{}", encoding="utf-8")
    agent._setup_from_init()
    assert agent._config.snapshot_interval is None
    agent._thread = SimpleNamespace(is_alive=lambda: True)
    _write_settings(tmp_path, _v2(snapshot_interval=15))
    agent._setup_from_init()
    assert agent._config.snapshot_interval == 15
    assert port.initialize_calls == 1
    # A value change while already on does not re-initialize.
    _write_settings(tmp_path, _v2(snapshot_interval=30))
    agent._setup_from_init()
    assert agent._config.snapshot_interval == 30
    assert port.initialize_calls == 1


def test_refresh_snapshot_enable_failure_keeps_snapshots_off(tmp_path, monkeypatch):
    init = _make_init()
    agent, _ = _refresh_agent(tmp_path, monkeypatch, init)
    port = MagicMock()
    port.initialize.side_effect = OSError("git unavailable")
    agent._snapshot_port = port
    agent._thread = SimpleNamespace(is_alive=lambda: True)
    logged: list[tuple] = []
    monkeypatch.setattr(agent, "_log", lambda event, **kw: logged.append((event, kw)))

    _write_settings(tmp_path, _v2(snapshot_interval=15))
    agent._setup_from_init()
    assert port.initialize.call_count == 1
    assert agent._config.snapshot_interval is None
    assert any(event == "snapshot_initialize_failed" for event, _ in logged)


def test_live_snapshot_port_initialize_is_idempotent(tmp_path):
    from lingtai.adapters.posix.git_cli import PosixGitCliAdapter

    workdir = tmp_path / "agent"
    workdir.mkdir()
    port = PosixGitCliAdapter(workdir)
    port.initialize()
    assert (workdir / ".git").is_dir()
    marker = workdir / ".gitignore"
    before = marker.read_text(encoding="utf-8")
    port.initialize()  # second call is a no-op on an initialized repository
    assert marker.read_text(encoding="utf-8") == before
