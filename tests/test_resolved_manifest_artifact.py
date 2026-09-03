"""Tests for the kernel-published resolved-manifest artifact (issue #259).

``Agent._read_init`` materializes the active preset in memory; raw init.json
is only a creation-time snapshot. After every successful materialization +
validation + path resolution, the kernel publishes the fully-resolved manifest
to ``<agent>/system/manifest.resolved.json`` so consumers (TUI/portal) read
the effective config instead of re-implementing the merge.
"""
import json
from pathlib import Path


def _make_workdir(tmp_path: Path, active_preset: str | None = None,
                  manifest_extra: dict | None = None,
                  llm: dict | None = None) -> Path:
    """Create a working dir with init.json. Optionally points at a preset."""
    wd = tmp_path / "agent"
    wd.mkdir()
    manifest = {
        "agent_name": "alice",
        "language": "en",
        "llm": llm or {"provider": "deepseek", "model": "deepseek-v4-flash",
                       "api_key": None, "api_key_env": "DEEPSEEK_API_KEY"},
        "capabilities": {"file": {}},
        "soul": {"delay": 120},
        "stamina": 3600,
        "molt_pressure": 0.8,
        "molt_prompt": "",
        "max_turns": 50,
        "admin": {"karma": True},
        "streaming": False,
    }
    if active_preset is not None:
        manifest["preset"] = {
            "active": active_preset,
            "default": active_preset,
            "allowed": [active_preset],
        }
    if manifest_extra:
        manifest.update(manifest_extra)
    env_file = wd / ".env"
    env_file.write_text("")
    init = {
        "manifest": manifest,
        "principle": "p", "covenant": "c", "pad": "", "lingtai": "",
        "env_file": str(env_file),
    }
    (wd / "init.json").write_text(json.dumps(init))
    return wd


def _make_preset_lib(tmp_path: Path, presets: dict[str, dict]) -> Path:
    """Create a presets dir with the given name → preset-content mapping."""
    pdir = tmp_path / "presets"
    pdir.mkdir()
    for name, content in presets.items():
        (pdir / f"{name}.json").write_text(json.dumps(content))
    return pdir


def _make_probe_agent(wd: Path):
    """Minimal Agent shim exposing _read_init without full construction."""
    from lingtai.agent import Agent

    class _Probe(Agent):
        def __init__(self, working_dir):
            self._working_dir = Path(working_dir)
            self._log_events = []
        def _log(self, event, **kw):
            self._log_events.append((event, kw))
    return _Probe(wd)


def _read_artifact(wd: Path) -> dict:
    return json.loads(
        (wd / "system" / "manifest.resolved.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Redaction helper (kernel-owned)
# ---------------------------------------------------------------------------

def test_redact_secrets_drops_secret_keys_keeps_public():
    from lingtai.kernel.workdir import _redact_secrets

    value = {
        "llm": {
            "provider": "deepseek", "model": "v4", "base_url": "https://x",
            "api_compat": "openai", "context_limit": 128000,
            "api_key": "sk-live-SECRET", "api_key_env": "DEEPSEEK_API_KEY",
        },
        "capabilities": {
            "web_search": {"provider": "gemini", "api_key": "sk-2"},
            "telegram": {"botToken": "bot-secret", "chat_id": 123},
            "feishu": {"appSecret": "app-secret", "app_id": "cli_x"},
            "imap": {"accounts": [{"host": "h", "password": "hunter2",
                                   "auth_token": "tok-abc"}]},
            "daemon": {"manager_pool_size": 30, "max_tokens": 4096},
        },
        "secretary": {"enabled": True},
    }
    out = _redact_secrets(value)
    llm = out["llm"]
    assert llm["provider"] == "deepseek"
    assert llm["model"] == "v4"
    assert llm["base_url"] == "https://x"
    assert llm["api_compat"] == "openai"
    assert llm["context_limit"] == 128000
    assert "api_key" not in llm
    assert llm["api_key_env"] == "DEEPSEEK_API_KEY"
    caps = out["capabilities"]
    assert "api_key" not in caps["web_search"]
    assert caps["telegram"] == {"chat_id": 123}
    assert caps["feishu"] == {"app_id": "cli_x"}
    account = caps["imap"]["accounts"][0]
    assert account == {"host": "h"}  # password + auth_token dropped
    # token-LIKE keys go, but plural "tokens" (e.g. max_tokens) is not a secret
    assert caps["daemon"] == {"manager_pool_size": 30, "max_tokens": 4096}
    # non-secret words that merely contain "secret" must survive
    assert out["secretary"] == {"enabled": True}
    # input untouched (pure function)
    assert value["llm"]["api_key"] == "sk-live-SECRET"


# ---------------------------------------------------------------------------
# Artifact written by _read_init after materialization
# ---------------------------------------------------------------------------

def test_artifact_publishes_materialized_skills_paths(tmp_path, monkeypatch):
    """The active preset's skills.paths show up in manifest.resolved.json even
    though raw init.json never mentions skills — the exact stale-snapshot
    failure from issue #259."""
    plib = _make_preset_lib(tmp_path, {
        "smart": {
            "name": "smart",
            "description": {"summary": "smart preset with skills"},
            "manifest": {
                "llm": {"provider": "gemini", "model": "gemini-2.5-pro",
                        "api_key": None, "api_key_env": "GEMINI_API_KEY"},
                "capabilities": {"file": {},
                                 "skills": {"paths": ["~/skills/curated"]}},
            },
        },
    })
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test")
    wd = _make_workdir(tmp_path, active_preset=str(plib / "smart.json"))
    raw_before = json.loads((wd / "init.json").read_text())
    assert "skills" not in raw_before["manifest"]["capabilities"]

    a = _make_probe_agent(wd)
    data = a._read_init()
    assert data is not None

    artifact = _read_artifact(wd)
    assert artifact["schema"] == "lingtai.manifest.resolved/v1"
    assert artifact["schema_version"] == 1
    assert artifact["source"] == "kernel"
    assert artifact["generated_at"].endswith("Z")
    assert artifact["preset"]["active"] == str(plib / "smart.json")
    caps = artifact["manifest"]["capabilities"]
    assert caps["skills"]["paths"] == ["~/skills/curated"]
    assert artifact["manifest"]["llm"]["provider"] == "gemini"

    # init.json stays user-owned input — the resolved manifest is NOT
    # written back (skills still absent in the raw file).
    raw_after = json.loads((wd / "init.json").read_text())
    assert "skills" not in raw_after["manifest"]["capabilities"]
    assert raw_after["manifest"]["llm"]["provider"] == "deepseek"


def test_artifact_merges_init_extras_per_materialize_semantics(tmp_path, monkeypatch):
    """init.json skills.paths extras append after the preset's curated paths
    (deduped), exactly as materialize_active_preset defines — and the merged
    result is what the artifact publishes."""
    plib = _make_preset_lib(tmp_path, {
        "smart": {
            "name": "smart",
            "description": {"summary": "smart preset"},
            "manifest": {
                "llm": {"provider": "gemini", "model": "gemini-2.5-pro",
                        "api_key": None, "api_key_env": "GEMINI_API_KEY"},
                "capabilities": {"skills": {"paths": ["~/skills/curated"]},
                                 "daemon": {"manager_pool_size": 10}},
            },
        },
    })
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test")
    wd = _make_workdir(
        tmp_path, active_preset=str(plib / "smart.json"),
        manifest_extra={"capabilities": {
            "skills": {"paths": ["~/skills/mine", "~/skills/curated"]},
            "daemon": {"manager_pool_size": 30},
        }},
    )
    a = _make_probe_agent(wd)
    assert a._read_init() is not None

    caps = _read_artifact(wd)["manifest"]["capabilities"]
    # preset paths first, init extras appended, duplicates dropped
    assert caps["skills"]["paths"] == ["~/skills/curated", "~/skills/mine"]
    # per-key override: init.json wins for daemon.manager_pool_size
    assert caps["daemon"]["manager_pool_size"] == 30


def test_artifact_redacts_api_key_like_secrets(tmp_path, monkeypatch):
    """Literal secrets in init.json (and those copied into capability kwargs
    by provider:inherit expansion) never reach the artifact."""
    secret = "sk-live-SUPERSECRET-123"
    wd = _make_workdir(
        tmp_path,
        llm={"provider": "deepseek", "model": "deepseek-v4-flash",
             "api_key": secret, "api_key_env": "DEEPSEEK_API_KEY"},
        manifest_extra={"capabilities": {
            "file": {"provider": "inherit"},
        }},
    )
    a = _make_probe_agent(wd)
    data = a._read_init()
    assert data is not None
    # inherit expansion really copied the secret into capability kwargs
    assert data["manifest"]["capabilities"]["file"]["api_key"] == secret

    artifact_text = (wd / "system" / "manifest.resolved.json").read_text()
    assert secret not in artifact_text
    artifact = _read_artifact(wd)
    llm = artifact["manifest"]["llm"]
    assert "api_key" not in llm
    assert llm["provider"] == "deepseek"
    assert llm["model"] == "deepseek-v4-flash"
    assert llm["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "api_key" not in artifact["manifest"]["capabilities"]["file"]
    # no half-written temp file left behind by the atomic write
    assert not (wd / "system" / "manifest.resolved.json.tmp").exists()


def test_refresh_rewrites_artifact_after_preset_change(tmp_path, monkeypatch):
    """End-to-end: boot via _setup_from_init publishes preset A; switching
    manifest.preset.active to B and refreshing republishes with B's llm."""
    from unittest.mock import MagicMock
    from lingtai.agent import Agent
    from lingtai.kernel.config import AgentConfig

    plib = _make_preset_lib(tmp_path, {
        "fast": {
            "name": "fast",
            "description": {"summary": "fast preset"},
            "manifest": {
                "llm": {"provider": "deepseek", "model": "deepseek-v4-flash",
                        "api_key": None, "api_key_env": "DEEPSEEK_API_KEY"},
                "capabilities": {"file": {}},
            },
        },
        "smart": {
            "name": "smart",
            "description": {"summary": "smart preset"},
            "manifest": {
                "llm": {"provider": "gemini", "model": "gemini-2.5-pro",
                        "api_key": None, "api_key_env": "GEMINI_API_KEY"},
                "capabilities": {"file": {}, "skills": {"paths": ["~/s"]}},
            },
        },
    })
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test")
    fast, smart = str(plib / "fast.json"), str(plib / "smart.json")

    wd = _make_workdir(tmp_path, active_preset=fast)
    init = json.loads((wd / "init.json").read_text())
    init["manifest"]["preset"]["allowed"] = [fast, smart]
    (wd / "init.json").write_text(json.dumps(init))

    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    agent = Agent(svc, working_dir=wd, config=AgentConfig())
    agent._setup_from_init()

    artifact = _read_artifact(wd)
    assert artifact["manifest"]["llm"]["provider"] == "deepseek"
    assert artifact["preset"]["active"] == fast

    # Swap the active preset (what system(refresh) does before re-setup).
    agent._activate_preset(smart)
    agent._setup_from_init()

    artifact = _read_artifact(wd)
    assert artifact["manifest"]["llm"]["provider"] == "gemini"
    assert artifact["manifest"]["llm"]["model"] == "gemini-2.5-pro"
    assert artifact["preset"]["active"] == smart
    assert artifact["manifest"]["capabilities"]["skills"]["paths"] == ["~/s"]


# ---------------------------------------------------------------------------
# write_resolved_manifest — _fsutil migration golden + concurrency coverage
# ---------------------------------------------------------------------------

def test_write_resolved_manifest_byte_identical_to_legacy_format(tmp_path, monkeypatch):
    """Golden-bytes: the _fsutil-migrated write keeps the legacy format
    exactly (indent=2, ensure_ascii=False, trailing newline), redacts
    secrets, and leaves no fixed ``*.tmp`` sibling behind."""
    import datetime as _dt

    from lingtai.kernel import workdir as _workdir

    class _FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 7, 6, 12, 0, 0, tzinfo=_dt.timezone.utc)

    monkeypatch.setattr(_workdir, "datetime", _FakeDatetime)

    wd = tmp_path / "agent"
    data = {
        "manifest": {
            "agent_name": "内省",
            "llm": {"provider": "deepseek", "model": "deepseek-v4-flash"},
            "soul": {"voice": "inner", "delay": 120},
        },
        "principle": "p",
    }
    target = _workdir.write_resolved_manifest(wd, data)
    assert target is not None

    # The legacy implementation serialized exactly this dict with
    # json.dumps(indent=2, ensure_ascii=False) plus a trailing newline.
    expected_artifact = {
        "schema": "lingtai.manifest.resolved/v1",
        "schema_version": 1,
        "generated_at": "2026-07-06T12:00:00Z",
        "source": "kernel",
        "manifest": data["manifest"],
    }
    expected = json.dumps(expected_artifact, indent=2, ensure_ascii=False) + "\n"
    assert target.read_text(encoding="utf-8") == expected
    # ensure_ascii=False really round-trips non-ASCII bytes
    assert "内省" in target.read_text(encoding="utf-8")
    # no transient temp siblings remain (fixed or unique)
    assert not (wd / "system" / "manifest.resolved.json.tmp").exists()
    assert not list((wd / "system").glob("*.tmp"))


def test_write_resolved_manifest_concurrent_writers(tmp_path):
    """Concurrent same-workdir writers must all succeed (no silent None), the
    final artifact must parse as valid JSON with the expected schema, and no
    ``*.tmp`` litter may remain — the fixed-temp-name race this migration
    removes."""
    import threading

    from lingtai.kernel.workdir import write_resolved_manifest

    wd = tmp_path / "agent"
    errors: list[str] = []
    barrier = threading.Barrier(8)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:  # pragma: no cover
            errors.append(f"worker {idx}: barrier broken")
            return
        for i in range(20):
            payload = {
                "manifest": {
                    "agent_name": f"alice-{idx}",
                    "soul": {"delay": 120 + i},
                }
            }
            try:
                result = write_resolved_manifest(wd, payload)
            except Exception as e:  # pragma: no cover
                errors.append(f"worker {idx} iter {i}: raised {e!r}")
                return
            if result is None:
                errors.append(f"worker {idx} iter {i}: returned None")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors[:5]
    artifact = json.loads(
        (wd / "system" / "manifest.resolved.json").read_text(encoding="utf-8")
    )
    assert artifact["schema"] == "lingtai.manifest.resolved/v1"
    assert artifact["schema_version"] == 1
    assert artifact["manifest"]["agent_name"].startswith("alice-")
    assert not list((wd / "system").glob("*.tmp"))
