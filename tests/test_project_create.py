"""Tests for kernel-owned project creation (`lingtai-agent project create`).

The behaviour under test is a port of the Go TUI's ``process.InitProject`` and
``preset.GenerateInitJSONWithOpts``; each test names the Go behaviour it pins
so a future reader can check the port against the original rather than against
this file's assumptions.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from lingtai.init_schema import validate_init
from lingtai.kernel._fsutil import JSONNumber
from lingtai.kernel.project_create import (
    AgentOpts,
    CapabilityConflictError,
    ProjectCreateError,
    canonicalize_capabilities,
    clamp_aed_attempts,
    covenant_body,
    create_project,
    generate_init_json,
    init_project,
    preset_ref_for,
    set_env_var,
    strip_obsolete_init_fields,
    sync_capability_api_key_env,
    validate_safe_name,
    write_covenant,
)


# --- fixtures -------------------------------------------------------------


MINIMAL_PRESET = {
    "name": "minimax",
    "description": {"summary": "Test preset", "tier": "3"},
    "manifest": {
        "llm": {
            "provider": "minimax",
            "model": "MiniMax-M2",
            "api_key_env": "MINIMAX_CN_1_API_KEY",
            "base_url": "https://api.minimaxi.com/v1",
        },
        "capabilities": {
            "shell": {"enabled": True},
            "web_search": {"provider": "minimax", "api_key_env": "MINIMAX_API_KEY"},
        },
    },
}


def _write_preset(path: Path, data: dict | None = None) -> Path:
    payload = json.loads(json.dumps(data if data is not None else MINIMAL_PRESET))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def preset_path(tmp_path: Path) -> Path:
    return _write_preset(tmp_path / "presets" / "minimax.json")


@pytest.fixture
def global_dir(tmp_path: Path) -> Path:
    return tmp_path / "global"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- pure helpers ---------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\\b", "/abs", "..\\x"])
def test_validate_safe_name_rejects_escapes(name: str) -> None:
    """Go: preset.ValidateSafeName — both separators, on every platform."""
    with pytest.raises(ProjectCreateError):
        validate_safe_name(name)


@pytest.mark.parametrize("name", ["orchestrator", "灵台", "a.b", "with space", "-dash"])
def test_validate_safe_name_accepts_contained_segments(name: str) -> None:
    validate_safe_name(name)


@pytest.mark.parametrize(
    "given,expected",
    [(0, 5), (-3, 5), (1, 1), (5, 5), (100, 100), (101, 100), (10_000, 100)],
)
def test_clamp_aed_attempts(given: int, expected: int) -> None:
    """Go: preset.ClampAedAttempts — zero means 'unset', not 'never retry'."""
    assert clamp_aed_attempts(given) == expected


def test_canonicalize_capabilities_moves_bash_to_shell() -> None:
    caps = {"bash": {"enabled": True}}
    assert canonicalize_capabilities(caps) is True
    assert caps == {"shell": {"enabled": True}}


def test_canonicalize_capabilities_identical_values_collapse() -> None:
    caps = {"bash": {"enabled": True}, "shell": {"enabled": True}}
    assert canonicalize_capabilities(caps) is True
    assert caps == {"shell": {"enabled": True}}


def test_canonicalize_capabilities_conflict_fails_closed() -> None:
    """Go: preset.ErrCapabilityConflict — never silently pick a winner."""
    caps = {"bash": {"enabled": True}, "shell": {"enabled": False}}
    with pytest.raises(CapabilityConflictError):
        canonicalize_capabilities(caps)
    assert caps == {"bash": {"enabled": True}, "shell": {"enabled": False}}


def test_canonicalize_capabilities_noop_without_legacy() -> None:
    caps = {"shell": {"enabled": True}}
    assert canonicalize_capabilities(caps) is False
    assert caps == {"shell": {"enabled": True}}


def test_sync_capability_api_key_env_matches_provider_only() -> None:
    """Go: preset.SyncCapabilityAPIKeyEnv."""
    manifest = {
        "llm": {"provider": "zhipu", "api_key_env": "ZHIPU_CN_1_API_KEY"},
        "capabilities": {
            "web_search": {"provider": "zhipu", "api_key_env": "ZHIPU_API_KEY"},
            "vision": {"provider": "openai", "api_key_env": "OPENAI_API_KEY"},
            "shell": {"enabled": True},
        },
    }
    sync_capability_api_key_env(manifest)
    caps = manifest["capabilities"]
    assert caps["web_search"]["api_key_env"] == "ZHIPU_CN_1_API_KEY"
    assert caps["vision"]["api_key_env"] == "OPENAI_API_KEY"
    assert caps["shell"] == {"enabled": True}


def test_strip_obsolete_init_fields() -> None:
    """Go: preset.stripObsoleteInitFields — kernel-ignored legacy pointers."""
    data = {"principle_file": "/x", "procedures_file": "/y", "covenant_file": "/z"}
    strip_obsolete_init_fields(data)
    assert data == {"covenant_file": "/z"}


def test_preset_ref_for_home_shortens(tmp_path: Path, monkeypatch) -> None:
    """Go: preset.RefFor produced ~/.lingtai-tui/presets/<subdir>/<name>.json."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    ref = preset_ref_for(tmp_path / ".lingtai-tui" / "presets" / "saved" / "cheap.json")
    assert ref == "~/.lingtai-tui/presets/saved/cheap.json"


def test_preset_ref_for_outside_home_stays_absolute(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    target = tmp_path / "elsewhere" / "p.json"
    assert preset_ref_for(target) == str(target)


# --- InitProject ----------------------------------------------------------


def test_init_project_scaffolds_skeleton(tmp_path: Path) -> None:
    """Go: process.InitProject — .lingtai/, human mailbox, .tui-asset,
    .library_shared."""
    lingtai_dir = tmp_path / ".lingtai"
    init_project(lingtai_dir)

    assert (lingtai_dir / "human" / "mailbox" / "inbox").is_dir()
    assert (lingtai_dir / "human" / "mailbox" / "sent").is_dir()
    assert (lingtai_dir / "human" / "mailbox" / "archive").is_dir()
    assert (lingtai_dir / ".tui-asset").is_dir()
    assert (lingtai_dir / ".library_shared").is_dir()

    manifest = _read(lingtai_dir / "human" / ".agent.json")
    assert manifest == {"agent_name": "human", "address": "human", "admin": None}
    assert (lingtai_dir / "human" / "mailbox" / "contacts.json").read_text() == "[]"


def test_init_project_is_idempotent_and_preserves_edits(tmp_path: Path) -> None:
    """Go: an existing human/.agent.json short-circuits the whole function."""
    lingtai_dir = tmp_path / ".lingtai"
    init_project(lingtai_dir)

    contacts = lingtai_dir / "human" / "mailbox" / "contacts.json"
    contacts.write_text('[{"address": "peer"}]', encoding="utf-8")
    manifest_path = lingtai_dir / "human" / ".agent.json"
    manifest_path.write_text('{"agent_name": "renamed"}', encoding="utf-8")

    init_project(lingtai_dir)

    assert contacts.read_text() == '[{"address": "peer"}]'
    assert _read(manifest_path) == {"agent_name": "renamed"}


def test_init_project_repairs_a_lingtai_dir_with_no_human(tmp_path: Path) -> None:
    """The Part-C startup-repair case: .lingtai/ exists, human/ does not."""
    lingtai_dir = tmp_path / ".lingtai"
    (lingtai_dir / "someagent").mkdir(parents=True)
    init_project(lingtai_dir)
    assert (lingtai_dir / "human" / ".agent.json").is_file()
    assert (lingtai_dir / "someagent").is_dir()


# --- covenant -------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "zh", "wen"])
def test_covenant_body_is_packaged_per_language(lang: str) -> None:
    body = covenant_body(lang)
    assert body.startswith("# ")
    assert "灵台" in body or "Lingtai" in body
    assert len(body.splitlines()) > 50


def test_covenant_bodies_differ_per_language() -> None:
    bodies = {lang: covenant_body(lang) for lang in ("en", "zh", "wen")}
    assert len(set(bodies.values())) == 3


def test_covenant_body_falls_back_to_english() -> None:
    assert covenant_body("klingon") == covenant_body("en")


def test_covenant_matches_the_go_source_byte_for_byte() -> None:
    """The port is a copy, not a rewrite: compare against the packaged asset.

    The packaged files under lingtai/project_assets/covenant/ are the record of
    what was ported; this pins that ``covenant_body`` reads exactly them.
    """
    from importlib import resources

    for lang in ("en", "zh", "wen"):
        ref = resources.files("lingtai.project_assets") / "covenant" / lang / "covenant.md"
        assert covenant_body(lang) == ref.read_text(encoding="utf-8")


def test_write_covenant_targets_the_kernel_mirror_path(tmp_path: Path) -> None:
    """prompts/covenant/covenant.yaml declares mirror_path: system/covenant.md."""
    agent_dir = tmp_path / "agent"
    written = write_covenant(agent_dir, "en")
    assert written == agent_dir / "system" / "covenant.md"
    assert written.read_text(encoding="utf-8") == covenant_body("en")


def test_write_covenant_preserves_operator_edits(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    write_covenant(agent_dir, "en")
    target = agent_dir / "system" / "covenant.md"
    target.write_text("# my own covenant", encoding="utf-8")

    write_covenant(agent_dir, "en")
    assert target.read_text(encoding="utf-8") == "# my own covenant"

    write_covenant(agent_dir, "en", overwrite=True)
    assert target.read_text(encoding="utf-8") == covenant_body("en")


# --- generate_init_json: fresh ------------------------------------------


def test_generate_init_json_fresh_shape(tmp_path: Path, global_dir: Path) -> None:
    lingtai_dir = tmp_path / ".lingtai"
    manifest = json.loads(json.dumps(MINIMAL_PRESET["manifest"]))
    path = generate_init_json(
        manifest, "~/.lingtai-tui/presets/templates/minimax.json",
        "orchestrator", "orchestrator", lingtai_dir, global_dir,
    )
    data = _read(path)

    assert data["manifest"]["agent_name"] == "orchestrator"
    assert data["manifest"]["language"] == "en"
    assert data["manifest"]["llm"] == MINIMAL_PRESET["manifest"]["llm"]
    assert data["manifest"]["admin"] == {"karma": True, "nirvana": False}
    assert data["manifest"]["context_limit"] == 300000
    assert data["manifest"]["max_turns"] == 500
    assert data["manifest"]["max_rpm"] == 60
    assert data["manifest"]["max_aed_attempts"] == 5
    assert data["manifest"]["streaming"] is False
    # soul is omitted entirely when soul_delay is None (kernel default applies).
    assert "soul" not in data["manifest"]
    assert data["manifest"]["preset"] == {
        "active": "~/.lingtai-tui/presets/templates/minimax.json",
        "default": "~/.lingtai-tui/presets/templates/minimax.json",
        "allowed": ["~/.lingtai-tui/presets/templates/minimax.json"],
    }
    assert data["env_file"] == str(global_dir / ".env")
    assert data["venv_path"] == str(global_dir / "runtime" / "venv")
    assert data["pad"] == ""
    assert data["covenant_file"] == "system/covenant.md"
    # Not populated: dead-code prompt layers and the retired soul mechanism.
    for absent in ("procedures", "procedures_file", "principle", "principle_file",
                   "soul", "soul_file", "addons", "mcp", "comment_file", "lingtai",
                   "prompt"):
        assert absent not in data, absent


def test_generate_init_json_writes_agent_manifest(tmp_path: Path, global_dir: Path) -> None:
    lingtai_dir = tmp_path / ".lingtai"
    generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "灵台", "lingtai", lingtai_dir, global_dir,
    )
    agent_dir = lingtai_dir / "lingtai"
    assert _read(agent_dir / ".agent.json") == {
        "agent_name": "灵台",
        "address": "lingtai",
        "admin": {"karma": True, "nirvana": False},
        "state": "",
    }
    for sub in ("inbox", "sent", "archive"):
        assert (agent_dir / "mailbox" / sub).is_dir()


def test_generate_init_json_normalizes_legacy_bash_capability(
    tmp_path: Path, global_dir: Path
) -> None:
    manifest = {"llm": {"provider": "x", "model": "y"}, "capabilities": {"bash": {"a": 1}}}
    path = generate_init_json(
        manifest, "ref.json", "a", "a", tmp_path / ".lingtai", global_dir,
    )
    caps = _read(path)["manifest"]["capabilities"]
    assert caps == {"shell": {"a": 1}}


def test_generate_init_json_syncs_capability_api_key_env(
    tmp_path: Path, global_dir: Path
) -> None:
    manifest = json.loads(json.dumps(MINIMAL_PRESET["manifest"]))
    path = generate_init_json(
        manifest, "ref.json", "a", "a", tmp_path / ".lingtai", global_dir,
    )
    caps = _read(path)["manifest"]["capabilities"]
    assert caps["web_search"]["api_key_env"] == "MINIMAX_CN_1_API_KEY"


def test_generate_init_json_rejects_unsafe_dir_name(tmp_path: Path, global_dir: Path) -> None:
    with pytest.raises(ProjectCreateError):
        generate_init_json(
            json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
            "a", "../escape", tmp_path / ".lingtai", global_dir,
        )
    assert not (tmp_path / "escape").exists()


def test_generate_init_json_omits_preset_block_without_ref(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: the whole preset block is guarded by `if p.Name != ""`."""
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "",
        "a", "a", tmp_path / ".lingtai", global_dir,
    )
    assert "preset" not in _read(path)["manifest"]


def test_generate_init_json_applies_agent_opts(tmp_path: Path, global_dir: Path) -> None:
    opts = AgentOpts(
        language="zh",
        context_limit=120_000,
        soul_delay=7200.0,
        max_rpm=0,
        max_aed_attempts=0,  # zero-value normalizes to the default, not 0
        karma=False,
        nirvana=True,
        comment_file="notes.md",
    )
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", tmp_path / ".lingtai", global_dir, opts,
    )
    data = _read(path)
    assert data["manifest"]["language"] == "zh"
    assert data["manifest"]["context_limit"] == 120_000
    assert data["manifest"]["soul"] == {"delay": 7200.0}
    assert data["manifest"]["max_rpm"] == 0
    assert data["manifest"]["max_aed_attempts"] == 5
    assert data["manifest"]["admin"] == {"karma": False, "nirvana": True}
    assert data["comment_file"] == "notes.md"


# --- generate_init_json: addons + mcp ------------------------------------


def test_generate_init_json_seeds_addon_mcp_specs(tmp_path: Path, global_dir: Path) -> None:
    opts = AgentOpts(addons=["telegram", "wechat", "not-a-real-addon"])
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", tmp_path / ".lingtai", global_dir, opts,
    )
    data = _read(path)
    assert data["addons"] == ["telegram", "wechat", "not-a-real-addon"]
    # Unknown names stay in `addons` (the kernel surfaces the warning) but get
    # no invented mcp spec.
    assert set(data["mcp"]) == {"telegram", "wechat"}
    assert data["mcp"]["telegram"] == {
        "type": "stdio",
        "command": str(global_dir / "runtime" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
        "args": ["-m", "lingtai.mcp_servers.telegram"],
        "env": {"LINGTAI_TELEGRAM_CONFIG": os.path.join(".secrets", "telegram.json")},
    }
    assert data["mcp"]["wechat"]["env"] == {
        "LINGTAI_WECHAT_CONFIG": os.path.join(".secrets", "wechat", "config.json")
    }


# --- generate_init_json: read-modify-write -------------------------------


def _existing_init(agent_dir: Path, data: dict) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "init.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_generate_init_json_preserves_unrelated_fields(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: known generated fields overwrite; unrelated user/kernel fields stay."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {
        "manifest": {
            "llm": {"provider": "old", "model": "old"},
            "aed_timeout": 900,
            "timezone_awareness": True,
        },
        "some_user_field": {"keep": "me"},
        "principle_file": "/stale/principle.md",
        "procedures_file": "/stale/procedures.md",
        "pad": "carried over",
        "env_file": "/custom/.env",
        "venv_path": "/custom/venv",
        "covenant_file": "/custom/covenant.md",
    })

    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir,
    )
    data = _read(path)

    # Preserved.
    assert data["some_user_field"] == {"keep": "me"}
    assert data["manifest"]["aed_timeout"] == 900
    assert data["manifest"]["timezone_awareness"] is True
    assert data["pad"] == "carried over"
    assert data["env_file"] == "/custom/.env"
    assert data["venv_path"] == "/custom/venv"
    assert data["covenant_file"] == "/custom/covenant.md"
    # Overwritten by the preset.
    assert data["manifest"]["llm"]["provider"] == "minimax"
    # Stripped: kernel-ignored legacy pointers.
    assert "principle_file" not in data
    assert "procedures_file" not in data


def test_generate_init_json_preserves_agent_identity_fields(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: molt_count etc. must survive a /setup-driven regeneration."""
    lingtai_dir = tmp_path / ".lingtai"
    agent_dir = lingtai_dir / "a"
    agent_dir.mkdir(parents=True)
    (agent_dir / ".agent.json").write_text(json.dumps({
        "agent_name": "old-name",
        "address": "a",
        "agent_id": "abc-123",
        "molt_count": 17,
        "created_at": "2026-01-01T00:00:00Z",
        "state": "ASLEEP",
    }), encoding="utf-8")

    generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "new-name", "a", lingtai_dir, global_dir,
    )
    data = _read(agent_dir / ".agent.json")
    assert data["agent_id"] == "abc-123"
    assert data["molt_count"] == 17
    assert data["created_at"] == "2026-01-01T00:00:00Z"
    assert data["state"] == "ASLEEP"  # not reset — only fresh agents get ""
    assert data["agent_name"] == "new-name"


def test_generate_init_json_preserves_existing_addons_and_mcp(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: user edits win — opts.addons only seeds on first creation."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {
        "addons": ["imap"],
        "mcp": {"imap": {"type": "stdio", "command": "/my/python", "args": ["-m", "x"]}},
    })
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir, AgentOpts(addons=["telegram"]),
    )
    data = _read(path)
    assert data["addons"] == ["imap"]
    assert data["mcp"]["imap"]["command"] == "/my/python"
    assert "telegram" not in data["mcp"]


def test_generate_init_json_normalizes_legacy_addons_dict(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: pre-v0.7.3 wrote a dict; it is rewritten as a list of names, and
    keys that fail the addon-identifier contract are dropped."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {
        "addons": {"imap": {"config": "x"}, "bad;key": {}, "telegram": {}},
    })
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir,
    )
    data = _read(path)
    assert data["addons"] == ["imap", "telegram"]
    assert set(data["mcp"]) == {"imap", "telegram"}


def test_generate_init_json_empty_addons_list_is_preserved(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: an existing (non-nil) empty array stays an empty array — it is a
    deliberate 'no addons' statement, not an absent field."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {"addons": []})
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir, AgentOpts(addons=["telegram"]),
    )
    data = _read(path)
    assert data["addons"] == []
    assert "mcp" not in data


def test_generate_init_json_ignores_unreadable_existing_file(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: a decode failure sets existingInit = nil — regeneration proceeds."""
    lingtai_dir = tmp_path / ".lingtai"
    agent_dir = lingtai_dir / "a"
    agent_dir.mkdir(parents=True)
    (agent_dir / "init.json").write_text("{not json", encoding="utf-8")
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir,
    )
    assert _read(path)["manifest"]["agent_name"] == "a"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits required")
def test_generate_init_json_regeneration_preserves_private_manifest_modes(
    tmp_path: Path, global_dir: Path
) -> None:
    lingtai_dir = tmp_path / ".lingtai"
    old_umask = os.umask(0o022)
    try:
        init_path = generate_init_json(
            json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
            "a", "a", lingtai_dir, global_dir,
        )
        agent_path = init_path.parent / ".agent.json"
        assert stat.S_IMODE(init_path.stat().st_mode) == 0o644
        assert stat.S_IMODE(agent_path.stat().st_mode) == 0o644
        init_path.chmod(0o600)
        agent_path.chmod(0o600)

        generate_init_json(
            json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
            "a", "a", lingtai_dir, global_dir,
        )
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(init_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(agent_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_generate_init_json_discards_nonstandard_existing_json_constants(
    tmp_path: Path, global_dir: Path, constant: str
) -> None:
    lingtai_dir = tmp_path / ".lingtai"
    agent_dir = lingtai_dir / "a"
    agent_dir.mkdir(parents=True)
    (agent_dir / "init.json").write_text(
        f'{{"unrelated": {constant}}}', encoding="utf-8"
    )
    (agent_dir / ".agent.json").write_text(
        f'{{"agent_id": {constant}}}', encoding="utf-8"
    )

    init_path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir,
    )
    init_data = _read(init_path)
    agent_data = _read(agent_dir / ".agent.json")

    assert "unrelated" not in init_data
    assert "agent_id" not in agent_data
    # The existing file drove the Go parse-failure branch, so this is not a
    # fresh agent and state must not be synthesized.
    assert "state" not in agent_data


def test_generate_init_json_preserves_large_existing_exponent_tokens(
    tmp_path: Path, global_dir: Path
) -> None:
    lingtai_dir = tmp_path / ".lingtai"
    agent_dir = lingtai_dir / "a"
    agent_dir.mkdir(parents=True)
    (agent_dir / "init.json").write_text(
        '{"unrelated": {"large": 1e400}}', encoding="utf-8"
    )
    (agent_dir / ".agent.json").write_text(
        '{"agent_id": "abc", "unrelated": {"large": 1e400}}', encoding="utf-8"
    )

    init_path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "ref.json",
        "a", "a", lingtai_dir, global_dir,
    )

    def load_with_number_tokens(path: Path) -> dict:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_int=JSONNumber,
            parse_float=JSONNumber,
            parse_constant=lambda value: pytest.fail(f"non-standard JSON constant: {value}"),
        )

    init_data = load_with_number_tokens(init_path)
    agent_data = load_with_number_tokens(agent_dir / ".agent.json")
    assert init_data["unrelated"]["large"] == JSONNumber("1e400")
    assert agent_data["unrelated"]["large"] == JSONNumber("1e400")
    assert "Infinity" not in init_path.read_text(encoding="utf-8")
    assert "Infinity" not in (agent_dir / ".agent.json").read_text(encoding="utf-8")


# --- preset {active, default, allowed} reconciliation --------------------


def test_preset_block_preserve_active_keeps_running_preset(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: /setup only moves `default`; the running agent keeps `active`."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {"manifest": {"preset": {
        "active": "~/p/old.json", "default": "~/p/old.json",
        "allowed": ["~/p/old.json", "~/p/other.json"],
    }}})
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "~/p/new.json",
        "a", "a", lingtai_dir, global_dir, AgentOpts(preserve_active_preset=True),
    )
    block = _read(path)["manifest"]["preset"]
    assert block["active"] == "~/p/old.json"
    assert block["default"] == "~/p/new.json"
    assert block["allowed"] == ["~/p/old.json", "~/p/other.json", "~/p/new.json"]


def test_preset_block_without_preserve_moves_active_too(
    tmp_path: Path, global_dir: Path
) -> None:
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {"manifest": {"preset": {
        "active": "~/p/old.json", "default": "~/p/old.json",
        "allowed": ["~/p/old.json"],
    }}})
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "~/p/new.json",
        "a", "a", lingtai_dir, global_dir,
    )
    block = _read(path)["manifest"]["preset"]
    # No preserve flag → the existing allowed list is not consulted at all.
    assert block == {
        "active": "~/p/new.json",
        "default": "~/p/new.json",
        "allowed": ["~/p/new.json"],
    }


def test_preset_block_caller_allowed_list_demotes_revoked_active(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: force-adding `active` back would re-authorize a just-revoked preset,
    so `active` is snapped to `default` instead."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {"manifest": {"preset": {
        "active": "~/p/revoked.json", "default": "~/p/old.json",
        "allowed": ["~/p/revoked.json", "~/p/old.json"],
    }}})
    opts = AgentOpts(
        preserve_active_preset=True,
        allowed_presets=["~/p/keep.json"],
    )
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "~/p/new.json",
        "a", "a", lingtai_dir, global_dir, opts,
    )
    block = _read(path)["manifest"]["preset"]
    assert block["allowed"] == ["~/p/keep.json", "~/p/new.json"]
    assert block["active"] == "~/p/new.json"
    assert "~/p/revoked.json" not in block["allowed"]


def test_preset_block_without_caller_list_keeps_active_authorized(
    tmp_path: Path, global_dir: Path
) -> None:
    """Go: nobody touched the authorization surface, so `active` is included."""
    lingtai_dir = tmp_path / ".lingtai"
    _existing_init(lingtai_dir / "a", {"manifest": {"preset": {
        "active": "~/p/running.json", "default": "~/p/old.json", "allowed": [],
    }}})
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "~/p/new.json",
        "a", "a", lingtai_dir, global_dir, AgentOpts(preserve_active_preset=True),
    )
    block = _read(path)["manifest"]["preset"]
    assert block["allowed"] == ["~/p/new.json", "~/p/running.json"]
    assert block["active"] == "~/p/running.json"


def test_preset_block_deduplicates_and_drops_blanks(
    tmp_path: Path, global_dir: Path
) -> None:
    opts = AgentOpts(allowed_presets=["~/p/a.json", "", "~/p/a.json", "~/p/b.json"])
    path = generate_init_json(
        json.loads(json.dumps(MINIMAL_PRESET["manifest"])), "~/p/a.json",
        "a", "a", tmp_path / ".lingtai", global_dir, opts,
    )
    block = _read(path)["manifest"]["preset"]
    assert block["allowed"] == ["~/p/a.json", "~/p/b.json"]


# --- soul-flow opt-in -----------------------------------------------------


def test_set_env_var_upserts_preserving_the_rest(tmp_path: Path) -> None:
    """Go: config.SetEnvVar — comments, blanks, and unrelated keys survive."""
    env = tmp_path / ".env"
    env.write_text("# comment\n\nOPENAI_API_KEY=sk-1\nLINGTAI_SOUL_FLOW_ENABLED=1\n",
                   encoding="utf-8")
    set_env_var(tmp_path, "LINGTAI_SOUL_FLOW_ENABLED", "")
    assert env.read_text(encoding="utf-8") == "# comment\n\nOPENAI_API_KEY=sk-1\n"

    set_env_var(tmp_path, "LINGTAI_SOUL_FLOW_ENABLED", "1")
    assert env.read_text(encoding="utf-8").endswith("LINGTAI_SOUL_FLOW_ENABLED=1\n")
    assert "OPENAI_API_KEY=sk-1" in env.read_text(encoding="utf-8")


def test_set_env_var_removal_never_creates_a_file(tmp_path: Path) -> None:
    """Go: unsetting nothing must not materialize a spurious empty .env."""
    set_env_var(tmp_path / "nope", "LINGTAI_SOUL_FLOW_ENABLED", "")
    assert not (tmp_path / "nope" / ".env").exists()


def test_set_env_var_creates_private_file(tmp_path: Path) -> None:
    set_env_var(tmp_path, "K", "v")
    env = tmp_path / ".env"
    assert env.read_text(encoding="utf-8") == "K=v\n"
    if os.name != "nt":
        assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_create_project_leaves_global_env_alone_by_default(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    """DEVIATION from Go, pinned deliberately: a plain create does not mutate
    machine-global state the caller never mentioned."""
    global_dir.mkdir(parents=True)
    (global_dir / ".env").write_text("LINGTAI_SOUL_FLOW_ENABLED=1\n", encoding="utf-8")
    create_project(tmp_path / "proj", "a", preset_path, global_dir=global_dir)
    assert (global_dir / ".env").read_text() == "LINGTAI_SOUL_FLOW_ENABLED=1\n"


def test_create_project_soul_flow_off_removes_the_key(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    global_dir.mkdir(parents=True)
    (global_dir / ".env").write_text("LINGTAI_SOUL_FLOW_ENABLED=1\n", encoding="utf-8")
    create_project(
        tmp_path / "proj", "a", preset_path, global_dir=global_dir,
        opts=AgentOpts(soul_flow_enabled=False),
    )
    assert (global_dir / ".env").read_text() == ""


def test_create_project_soul_flow_on_writes_the_key(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    create_project(
        tmp_path / "proj", "a", preset_path, global_dir=global_dir,
        opts=AgentOpts(soul_flow_enabled=True),
    )
    assert (global_dir / ".env").read_text() == "LINGTAI_SOUL_FLOW_ENABLED=1\n"


# --- create_project end to end -------------------------------------------


@pytest.mark.parametrize("soul_delay", [float("nan"), float("inf"), float("-inf")])
def test_create_project_rejects_nonfinite_soul_delay_before_scaffolding(
    tmp_path: Path, preset_path: Path, global_dir: Path, soul_delay: float
) -> None:
    project_root = tmp_path / "project"

    with pytest.raises(ProjectCreateError, match="soul_delay must be a finite number"):
        create_project(
            project_root,
            "a",
            preset_path,
            global_dir=global_dir,
            opts=AgentOpts(soul_delay=soul_delay),
        )

    assert not project_root.exists()


def test_create_project_rejects_bool_number_capability_conflict_before_output(
    tmp_path: Path, global_dir: Path
) -> None:
    preset = json.loads(json.dumps(MINIMAL_PRESET))
    preset["manifest"]["capabilities"] = {"bash": True, "shell": 1}
    preset_path = _write_preset(tmp_path / "presets" / "conflict.json", preset)
    project_root = tmp_path / "project"

    with pytest.raises(CapabilityConflictError):
        create_project(project_root, "a", preset_path, global_dir=global_dir)

    assert not project_root.exists()


def test_create_project_collapses_identical_nested_capability_configs(
    tmp_path: Path, global_dir: Path
) -> None:
    nested = {"options": [True, {"retries": 3, "labels": ["fast", "safe"]}]}
    preset = json.loads(json.dumps(MINIMAL_PRESET))
    preset["manifest"]["capabilities"] = {"bash": nested, "shell": nested}
    preset_path = _write_preset(tmp_path / "presets" / "identical.json", preset)

    result = create_project(tmp_path / "project", "a", preset_path, global_dir=global_dir)

    assert _read(Path(result["init_json"]))["manifest"]["capabilities"] == {"shell": nested}


def test_create_project_rejects_nested_capability_number_token_conflict_before_output(
    tmp_path: Path, global_dir: Path
) -> None:
    preset_path = tmp_path / "presets" / "numeric-conflict.json"
    preset_path.parent.mkdir(parents=True)
    preset_path.write_text(
        """{
  "name": "numeric-conflict",
  "description": {"summary": "Test preset", "tier": "3"},
  "manifest": {
    "llm": {"provider": "minimax", "model": "MiniMax-M2"},
    "capabilities": {
      "bash": {"nested": [1.0]},
      "shell": {"nested": [1.00]}
    }
  }
}""",
        encoding="utf-8",
    )
    project_root = tmp_path / "project"

    with pytest.raises(CapabilityConflictError):
        create_project(project_root, "a", preset_path, global_dir=global_dir)

    assert not project_root.exists()


def test_create_project_end_to_end(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    project_root = tmp_path / "proj"
    result = create_project(project_root, "orchestrator", preset_path, global_dir=global_dir)

    lingtai_dir = project_root / ".lingtai"
    agent_dir = lingtai_dir / "orchestrator"
    assert result["lingtai_dir"] == str(lingtai_dir)
    assert result["agent_dir"] == str(agent_dir)
    assert result["preset_ref"] == str(preset_path)

    # 1. scaffolding
    assert (lingtai_dir / "human" / ".agent.json").is_file()
    assert (lingtai_dir / ".library_shared").is_dir()
    assert (lingtai_dir / ".tui-asset").is_dir()
    # 2. init.json + .agent.json
    assert (agent_dir / "init.json").is_file()
    assert (agent_dir / ".agent.json").is_file()
    assert (agent_dir / "mailbox" / "inbox").is_dir()
    # 3. covenant mirror
    covenant = agent_dir / "system" / "covenant.md"
    assert covenant.read_text(encoding="utf-8") == covenant_body("en")
    # 4. explicitly not populated
    assert not (agent_dir / "system" / "procedures.md").exists()
    assert not (agent_dir / "system" / "principle.md").exists()
    assert not (agent_dir / "soul").exists()
    assert not (agent_dir / "skills").exists()
    assert not (project_root / ".recipe").exists()


def test_create_project_output_passes_kernel_init_validation(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    """The whole point of the port: what it writes must boot."""
    result = create_project(tmp_path / "proj", "a", preset_path, global_dir=global_dir)
    data = _read(Path(result["init_json"]))
    warnings = validate_init(data)
    assert warnings == []


def test_create_project_covenant_resolves_the_way_the_agent_reads_it(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    """`covenant_file` is relative; resolve_paths re-roots it against the agent
    working dir at boot, which must land on the mirror we just wrote."""
    from lingtai.kernel.config_resolve import resolve_file, resolve_paths

    result = create_project(tmp_path / "proj", "a", preset_path, global_dir=global_dir)
    agent_dir = Path(result["agent_dir"])
    data = _read(Path(result["init_json"]))

    resolve_paths(data, agent_dir)
    resolved = resolve_file(data.get("covenant"), data.pop("covenant_file"))
    assert resolved == covenant_body("en")


def test_create_project_language_selects_covenant(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    result = create_project(
        tmp_path / "proj", "a", preset_path, global_dir=global_dir,
        opts=AgentOpts(language="zh"),
    )
    assert Path(result["covenant_file"]).read_text(encoding="utf-8") == covenant_body("zh")
    assert _read(Path(result["init_json"]))["manifest"]["language"] == "zh"


def test_create_project_rejects_a_missing_preset(tmp_path: Path, global_dir: Path) -> None:
    with pytest.raises(ProjectCreateError):
        create_project(tmp_path / "proj", "a", tmp_path / "nope.json", global_dir=global_dir)


def test_create_project_rejects_an_invalid_preset(tmp_path: Path, global_dir: Path) -> None:
    bad = _write_preset(tmp_path / "p" / "bad.json", {"name": "bad", "manifest": {}})
    with pytest.raises(ProjectCreateError):
        create_project(tmp_path / "proj", "a", bad, global_dir=global_dir)


def test_create_project_is_rerunnable(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    project_root = tmp_path / "proj"
    first = create_project(project_root, "a", preset_path, global_dir=global_dir)
    before = Path(first["init_json"]).read_text(encoding="utf-8")
    second = create_project(project_root, "a", preset_path, global_dir=global_dir)
    assert Path(second["init_json"]).read_text(encoding="utf-8") == before


def test_create_project_second_agent_shares_the_project(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    project_root = tmp_path / "proj"
    create_project(project_root, "first", preset_path, global_dir=global_dir)
    create_project(project_root, "second", preset_path, global_dir=global_dir)
    lingtai_dir = project_root / ".lingtai"
    assert (lingtai_dir / "first" / "init.json").is_file()
    assert (lingtai_dir / "second" / "init.json").is_file()
    assert (lingtai_dir / "second" / "system" / "covenant.md").is_file()


def test_create_project_rejects_unsafe_name(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    with pytest.raises(ProjectCreateError):
        create_project(tmp_path / "proj", "../escape", preset_path, global_dir=global_dir)


def test_create_project_dir_name_may_differ_from_agent_name(
    tmp_path: Path, preset_path: Path, global_dir: Path
) -> None:
    result = create_project(
        tmp_path / "proj", "澄一", preset_path, dir_name="chengyi", global_dir=global_dir,
    )
    agent_dir = Path(result["agent_dir"])
    assert agent_dir.name == "chengyi"
    assert _read(agent_dir / "init.json")["manifest"]["agent_name"] == "澄一"
    assert _read(agent_dir / ".agent.json")["address"] == "chengyi"


# --- CLI surface ----------------------------------------------------------


def _run_cli(argv: list[str], monkeypatch, capsys) -> tuple[int, str, str]:
    from lingtai.cli import main

    monkeypatch.setattr("sys.argv", ["lingtai-agent", *argv])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code or 0
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_project_create(tmp_path: Path, preset_path: Path, global_dir: Path,
                            monkeypatch, capsys) -> None:
    project_root = tmp_path / "proj"
    code, out, err = _run_cli([
        "project", "create",
        "--name", "orchestrator",
        "--dir", str(project_root),
        "--preset", str(preset_path),
        "--global-dir", str(global_dir),
        "--json",
    ], monkeypatch, capsys)
    assert code == 0, err
    result = json.loads(out)
    assert Path(result["init_json"]).is_file()
    assert Path(result["covenant_file"]).read_text(encoding="utf-8") == covenant_body("en")
    assert (project_root / ".lingtai" / "human" / ".agent.json").is_file()


def test_cli_project_create_flags_reach_the_manifest(
    tmp_path: Path, preset_path: Path, global_dir: Path, monkeypatch, capsys
) -> None:
    code, out, err = _run_cli([
        "project", "create",
        "--name", "a", "--dir", str(tmp_path / "proj"),
        "--preset", str(preset_path), "--global-dir", str(global_dir),
        "--language", "wen", "--context-limit", "50000", "--max-rpm", "10",
        "--max-aed-attempts", "3", "--no-karma", "--nirvana",
        "--addon", "telegram", "--soul-delay", "60",
        "--json",
    ], monkeypatch, capsys)
    assert code == 0, err
    data = _read(Path(json.loads(out)["init_json"]))
    m = data["manifest"]
    assert m["language"] == "wen"
    assert m["context_limit"] == 50000
    assert m["max_rpm"] == 10
    assert m["max_aed_attempts"] == 3
    assert m["admin"] == {"karma": False, "nirvana": True}
    assert m["soul"] == {"delay": 60.0}
    assert data["addons"] == ["telegram"]


@pytest.mark.parametrize("soul_delay", ["nan", "inf", "-inf"])
def test_cli_project_create_rejects_nonfinite_soul_delay_before_scaffolding(
    tmp_path: Path, preset_path: Path, global_dir: Path, monkeypatch, capsys, soul_delay: str
) -> None:
    project_root = tmp_path / "proj"
    code, out, err = _run_cli([
        "project", "create",
        "--name", "a", "--dir", str(project_root),
        "--preset", str(preset_path), "--global-dir", str(global_dir),
        f"--soul-delay={soul_delay}",
    ], monkeypatch, capsys)

    assert code == 2
    assert out == ""
    assert "argument --soul-delay: must be a finite number" in err
    assert "Traceback" not in err
    assert not project_root.exists()


def test_cli_project_create_reports_a_bad_preset(
    tmp_path: Path, global_dir: Path, monkeypatch, capsys
) -> None:
    code, out, err = _run_cli([
        "project", "create",
        "--name", "a", "--dir", str(tmp_path / "proj"),
        "--preset", str(tmp_path / "missing.json"), "--global-dir", str(global_dir),
    ], monkeypatch, capsys)
    assert code == 1
    assert "preset" in err
