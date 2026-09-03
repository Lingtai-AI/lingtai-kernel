"""End-to-end: preset library on disk → active_preset in init.json →
materialization at boot → _activate_preset rewrites init.json → re-read
yields new manifest."""
import json
from pathlib import Path

import pytest


def _build_lib(plib: Path):
    """Build a two-preset library (alpha and beta) for swap tests."""
    plib.mkdir(parents=True, exist_ok=True)
    (plib / "alpha.json").write_text(json.dumps({
        "name": "alpha",
        "description": {"summary": "alpha — text-only"},
        "manifest": {
            "llm": {"provider": "p1", "model": "m1",
                    "api_key": None, "api_key_env": "P1KEY"},
            "capabilities": {"file": {}, "web_search": {"provider": "duckduckgo"}},
        },
    }))
    (plib / "beta.json").write_text(json.dumps({
        "name": "beta",
        "description": {"summary": "beta — vision-capable",
                        "gains": ["vision"], "loses": ["text-only optimization"]},
        "manifest": {
            "llm": {"provider": "p2", "model": "m2",
                    "api_key": None, "api_key_env": "P2KEY"},
            "capabilities": {"file": {}, "vision": {"provider": "p2",
                                                    "api_key_env": "P2KEY"}},
        },
    }))


def _build_workdir(wd: Path, plib: Path, active: str, *,
                   allowed: list[str] | None = None,
                   default: str | None = None):
    """Build a workdir with init.json pointing at the named preset.

    Includes a stub .env file because validate_init requires env_file when
    api_key_env is set without api_key (which is true for our test presets).

    `allowed` defaults to a single-entry list containing `active` (and
    `default`, if it differs).
    """
    wd.mkdir(parents=True, exist_ok=True)
    env = wd / ".env"
    env.write_text("P1KEY=sk-test\nP2KEY=sk-test\n")

    if default is None:
        default = active
    if allowed is None:
        allowed = [default] if default == active else [default, active]

    init = {
        "manifest": {
            "agent_name": "test",
            "language": "en",
            "preset": {
                "active": active,
                "default": default,
                "allowed": allowed,
            },
            "llm": {"provider": "PLACEHOLDER", "model": "PLACEHOLDER",
                    "api_key": None, "api_key_env": "PLACEHOLDER"},
            "capabilities": {},
            "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {"karma": True}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "lingtai": "",
        "soul": "",
        "env_file": str(env),
    }
    (wd / "init.json").write_text(json.dumps(init))


def _make_probe(wd: Path):
    """Build a minimal Agent probe that exposes _read_init and _activate_preset
    without triggering full agent construction."""
    from lingtai.agent import Agent

    class _Probe(Agent):
        def __init__(self, working_dir):
            self._working_dir = Path(working_dir)
            self._log_events = []
        def _log(self, event, **kw):
            self._log_events.append((event, kw))
    return _Probe(wd)


def test_e2e_boot_with_alpha_then_swap_to_beta(tmp_path, monkeypatch):
    """Boot agent with active_preset=alpha → materializes alpha.
       Call _activate_preset('beta') → init.json now reflects beta.
       Re-read → materializes beta. Identity preserved."""
    plib = tmp_path / "presets"
    _build_lib(plib)
    wd = tmp_path / "agent"
    alpha_path = str(plib / "alpha.json")
    beta_path = str(plib / "beta.json")
    _build_workdir(wd, plib, alpha_path)
    monkeypatch.setenv("P1KEY", "sk-test")
    monkeypatch.setenv("P2KEY", "sk-test")

    agent = _make_probe(wd)

    # Initial boot: alpha materialized
    data1 = agent._read_init()
    assert data1 is not None, "initial _read_init failed"
    assert data1["manifest"]["llm"]["provider"] == "p1"
    assert data1["manifest"]["llm"]["model"] == "m1"
    assert "vision" not in data1["manifest"]["capabilities"]
    assert data1["manifest"]["agent_name"] == "test"

    # Swap to beta
    agent._activate_preset(beta_path)

    # Re-read: beta materialized, identity preserved
    data2 = agent._read_init()
    assert data2 is not None, "post-swap _read_init failed"
    assert data2["manifest"]["llm"]["provider"] == "p2"
    assert data2["manifest"]["llm"]["model"] == "m2"
    assert "vision" in data2["manifest"]["capabilities"]
    assert data2["manifest"]["agent_name"] == "test"  # identity preserved
    assert data2["manifest"]["admin"]["karma"] is True  # admin preserved
    assert data2["manifest"]["soul"]["delay"] == 120  # soul preserved
    assert "stamina" not in data2["manifest"]  # legacy runtime knob removed
    assert data2["manifest"]["preset"]["active"] == beta_path
    assert data2["manifest"]["preset"]["default"] == alpha_path  # original default preserved


def test_activation_does_not_persist_preset_plaintext_key_but_reread_materializes(tmp_path, monkeypatch):
    plib = tmp_path / "presets"
    _build_lib(plib)
    alpha = plib / "alpha.json"
    preset = json.loads(alpha.read_text())
    preset_secret = "sk-preset-placeholder"
    old_secret = "sk-old-placeholder"
    capability_secret = "sk-capability-placeholder"
    preset["manifest"]["llm"].update({
        "api_key": preset_secret,
        "base_url": "https://provider.placeholder/v1",
    })
    preset["manifest"]["capabilities"]["file"].update({
        "api_key": capability_secret,
        "api_key_env": "FILE_KEY",
        "base_url": "https://files.placeholder/v1",
    })
    alpha.write_text(json.dumps(preset))
    original_preset = alpha.read_bytes()

    wd = tmp_path / "agent"
    _build_workdir(wd, plib, str(alpha))
    init = json.loads((wd / "init.json").read_text())
    init["manifest"]["llm"]["api_key"] = old_secret
    (wd / "init.json").write_text(json.dumps(init))
    agent = _make_probe(wd)

    agent._activate_preset(str(alpha))
    written_text = (wd / "init.json").read_text()
    written = json.loads(written_text)
    assert preset_secret not in written_text
    assert old_secret not in written_text
    assert capability_secret not in written_text
    assert written["manifest"]["llm"] == {
        "provider": "p1",
        "model": "m1",
        "base_url": "https://provider.placeholder/v1",
        "api_key_env": "P1KEY",
    }
    assert written["manifest"]["capabilities"]["file"] == {
        "api_key_env": "FILE_KEY",
        "base_url": "https://files.placeholder/v1",
    }
    assert alpha.read_bytes() == original_preset
    assert not (wd / "init.json.tmp").exists()
    assert not list(wd.glob("*.bak"))

    monkeypatch.setenv("P1KEY", "sk-runtime-placeholder")
    effective = agent._read_init()
    assert effective["manifest"]["llm"]["api_key"] == preset_secret
    artifact_text = (wd / "system" / "manifest.resolved.json").read_text()
    assert preset_secret not in artifact_text
    assert capability_secret not in artifact_text
    assert "api_key" not in json.loads(artifact_text)["manifest"]["llm"]
    assert json.loads(artifact_text)["manifest"]["llm"]["api_key_env"] == "P1KEY"
    assert json.loads(artifact_text)["manifest"]["capabilities"]["file"]["api_key_env"] == "FILE_KEY"
    assert not list((wd / "system").glob("*.tmp"))
    assert not list((wd / "system").glob(".*.tmp"))
    assert preset_secret not in json.dumps(agent._log_events)
    assert capability_secret not in json.dumps(agent._log_events)


def test_update_default_preset_redacts_existing_credentials_atomically(tmp_path):
    """The post-activation default writer must not copy old secrets back."""
    plib = tmp_path / "presets"
    _build_lib(plib)
    alpha_path = plib / "alpha.json"
    wd = tmp_path / "agent"
    _build_workdir(wd, plib, str(alpha_path))

    init_path = wd / "init.json"
    init = json.loads(init_path.read_text())
    init["manifest"]["llm"].update({
        "provider": "provider-placeholder",
        "model": "model-placeholder",
        "base_url": "https://provider.placeholder/v1",
        "api_key": "sk-direct-writer-placeholder",
        "api_key_env": "PLACEHOLDER_LLM_KEY",
    })
    init["manifest"]["capabilities"] = {
        "file": {
            "api_key": "sk-direct-capability-placeholder",
            "api_key_env": "PLACEHOLDER_FILE_KEY",
            "base_url": "https://files.placeholder/v1",
        },
    }
    init_path.write_text(json.dumps(init))

    agent = _make_probe(wd)
    from lingtai.tools.system.preset import _update_default_preset

    _update_default_preset(agent, str(plib / "beta.json"))

    written_text = init_path.read_text()
    written = json.loads(written_text)
    assert "sk-direct-writer-placeholder" not in written_text
    assert "sk-direct-capability-placeholder" not in written_text
    assert written["manifest"]["preset"]["default"] == str(plib / "beta.json")
    assert written["manifest"]["llm"] == {
        "provider": "provider-placeholder",
        "model": "model-placeholder",
        "base_url": "https://provider.placeholder/v1",
        "api_key_env": "PLACEHOLDER_LLM_KEY",
    }
    assert written["manifest"]["capabilities"]["file"] == {
        "api_key_env": "PLACEHOLDER_FILE_KEY",
        "base_url": "https://files.placeholder/v1",
    }
    assert not (wd / "init.json.tmp").exists()
    assert not list(wd.glob("*.bak"))
    assert not list(wd.glob("*.tmp"))
    assert not list(wd.glob(".*.tmp"))
    assert "sk-direct-writer-placeholder" not in json.dumps(agent._log_events)


def test_activation_replace_failure_cleans_temp_and_keeps_secret_out_of_artifacts(tmp_path, monkeypatch):
    plib = tmp_path / "presets"
    _build_lib(plib)
    alpha = plib / "alpha.json"
    preset = json.loads(alpha.read_text())
    preset["manifest"]["llm"]["api_key"] = "sk-failed-write-placeholder"
    alpha.write_text(json.dumps(preset))
    wd = tmp_path / "agent"
    _build_workdir(wd, plib, str(alpha))
    original_init = (wd / "init.json").read_text()
    agent = _make_probe(wd)

    import os
    real_replace = os.replace
    def fail_replace(source, target):
        if str(target) == str(wd / "init.json"):
            raise OSError("simulated replace failure")
        return real_replace(source, target)
    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        agent._activate_preset(str(alpha))
    assert (wd / "init.json").read_text() == original_init
    assert not (wd / "init.json.tmp").exists()
    assert not list(wd.glob("*.tmp"))
    assert not list(wd.glob(".*.tmp"))
    assert "sk-failed-write-placeholder" not in " ".join(
        event for event, _fields in agent._log_events
    )


def test_e2e_swap_to_unknown_preserves_init(tmp_path, monkeypatch):
    """Swap to nonexistent preset raises KeyError; init.json on disk untouched."""
    plib = tmp_path / "presets"
    _build_lib(plib)
    wd = tmp_path / "agent"
    _build_workdir(wd, plib, str(plib / "alpha.json"))
    monkeypatch.setenv("P1KEY", "sk-test")

    original = (wd / "init.json").read_text()
    agent = _make_probe(wd)

    with pytest.raises(KeyError):
        agent._activate_preset(str(plib / "ghost.json"))

    assert (wd / "init.json").read_text() == original


def test_e2e_inherit_resolves_after_swap(tmp_path, monkeypatch):
    """A preset that uses provider:'inherit' resolves to its own llm at boot."""
    plib = tmp_path / "presets"
    plib.mkdir(parents=True, exist_ok=True)
    (plib / "smart.json").write_text(json.dumps({
        "name": "smart",
        "description": {"summary": "vision via inherit"},
        "manifest": {
            "llm": {"provider": "gemini", "model": "gemini-2.5-pro",
                    "api_key": None, "api_key_env": "GEMINI_API_KEY"},
            "capabilities": {
                "file": {},
                "web_search": {"provider": "inherit"},
                "vision": {"provider": "inherit"},
            },
        },
    }))
    wd = tmp_path / "agent"

    # Build workdir with stub .env including GEMINI_API_KEY
    wd.mkdir(parents=True, exist_ok=True)
    env = wd / ".env"
    env.write_text("GEMINI_API_KEY=sk-test\n")
    init = {
        "manifest": {
            "agent_name": "test",
            "language": "en",
            "preset": {
                "active": str(plib / "smart.json"),
                "default": str(plib / "smart.json"),
                "allowed": [str(plib / "smart.json")],
            },
            "llm": {"provider": "PLACEHOLDER", "model": "PLACEHOLDER",
                    "api_key": None, "api_key_env": "PLACEHOLDER"},
            "capabilities": {},
            "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {"karma": True}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "lingtai": "",
        "soul": "",
        "env_file": str(env),
    }
    (wd / "init.json").write_text(json.dumps(init))

    monkeypatch.setenv("GEMINI_API_KEY", "sk-test")

    agent = _make_probe(wd)
    data = agent._read_init()
    assert data is not None

    caps = data["manifest"]["capabilities"]
    assert caps["web"]["provider"] == "gemini"
    assert caps["web"]["api_key_env"] == "GEMINI_API_KEY"
    assert caps["vision"]["provider"] == "gemini"
    assert caps["vision"]["api_key_env"] == "GEMINI_API_KEY"
    # model is NOT inherited
    assert "model" not in caps["web"]
    assert "model" not in caps["vision"]


def test_persistent_api_key_scrub_is_narrow_and_preserves_auth_metadata():
    """Durable init cleanup removes only API-key values, not authored config."""
    from lingtai.kernel.workdir import _redact_persisted_api_keys

    source = {
        "manifest": {
            "llm": {
                "provider": "placeholder-provider",
                "model": "placeholder-model",
                "base_url": "https://provider.placeholder/v1",
                "api_key": "sk-persistent-placeholder",
                "api_key_env": "PR9_LLM_PLACEHOLDER_ENV",
                "service_tier": "default",
                "wire_api": "auto",
            },
            "capabilities": {
                "file": {
                    "provider": "placeholder-provider",
                    "base_url": "https://files.placeholder/v1",
                    "api_key": "sk-capability-placeholder",
                    "api_key_env": "PR9_CAP_PLACEHOLDER_ENV",
                },
            },
        },
    }

    cleaned = _redact_persisted_api_keys(source)
    assert cleaned["manifest"]["llm"] == {
        "provider": "placeholder-provider",
        "model": "placeholder-model",
        "base_url": "https://provider.placeholder/v1",
        "api_key_env": "PR9_LLM_PLACEHOLDER_ENV",
        "service_tier": "default",
        "wire_api": "auto",
    }
    assert cleaned["manifest"]["capabilities"]["file"] == {
        "provider": "placeholder-provider",
        "base_url": "https://files.placeholder/v1",
        "api_key_env": "PR9_CAP_PLACEHOLDER_ENV",
    }
    assert source["manifest"]["llm"]["api_key"] == "sk-persistent-placeholder"
    assert source["manifest"]["capabilities"]["file"]["api_key"] == "sk-capability-placeholder"


def test_activation_reread_from_fresh_agent_materializes_selected_key(tmp_path, monkeypatch):
    """A fresh probe (restart simulation) gets the selected key only in memory."""
    plib = tmp_path / "presets"
    _build_lib(plib)
    alpha = plib / "alpha.json"
    preset = json.loads(alpha.read_text())
    selected_secret = "sk-restart-selected-placeholder"
    preset["manifest"]["llm"].update({
        "api_key": selected_secret,
        "base_url": "https://provider.placeholder/v1",
    })
    alpha.write_text(json.dumps(preset))
    authored_preset = alpha.read_bytes()
    wd = tmp_path / "agent"
    _build_workdir(wd, plib, str(alpha))

    first = _make_probe(wd)
    first._activate_preset(str(alpha))
    safe_init_text = (wd / "init.json").read_text(encoding="utf-8")
    assert selected_secret not in safe_init_text

    # Rebuild the probe after activation: no in-process state is reused.
    monkeypatch.setenv("P1KEY", "sk-runtime-restart-placeholder")
    restarted = _make_probe(wd)
    effective = restarted._read_init()
    assert effective is not None
    assert effective["manifest"]["llm"]["provider"] == "p1"
    assert effective["manifest"]["llm"]["base_url"] == "https://provider.placeholder/v1"
    assert effective["manifest"]["llm"]["api_key"] == selected_secret
    artifact_text = (wd / "system" / "manifest.resolved.json").read_text()
    assert selected_secret not in artifact_text
    assert restarted._log_events
    assert selected_secret not in json.dumps(restarted._log_events)
    assert alpha.read_bytes() == authored_preset


def test_default_writer_replace_failure_keeps_existing_safe_init_and_cleans(tmp_path, monkeypatch):
    """A failed default update leaves the already-safe init bytes untouched."""
    plib = tmp_path / "presets"
    _build_lib(plib)
    alpha = plib / "alpha.json"
    preset = json.loads(alpha.read_text())
    preset["manifest"]["llm"]["api_key"] = "sk-default-failure-placeholder"
    alpha.write_text(json.dumps(preset))
    wd = tmp_path / "agent"
    _build_workdir(wd, plib, str(alpha))
    agent = _make_probe(wd)
    agent._activate_preset(str(alpha))
    init_path = wd / "init.json"
    before = init_path.read_bytes()
    assert b"sk-default-failure-placeholder" not in before

    import os
    real_replace = os.replace

    def fail_replace(source, target):
        if str(target) == str(init_path):
            raise OSError("simulated default replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_replace)
    from lingtai.tools.system.preset import _update_default_preset
    _update_default_preset(agent, str(plib / "beta.json"))

    assert init_path.read_bytes() == before
    assert not list(wd.glob("*.tmp"))
    assert not list(wd.glob(".*.tmp"))
    assert not list(wd.glob("*.bak"))
    assert "sk-default-failure-placeholder" not in json.dumps(agent._log_events)
