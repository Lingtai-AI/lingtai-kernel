"""Tests that avatar spawn correctly inherits manifest.preset block."""
import json
from pathlib import Path

import pytest


def _baseline_parent_init(preset_path: str | None = None,
                          active_preset: str | None = None) -> dict:
    """Build a minimal but valid parent init.json dict."""
    manifest = {
        "agent_name": "parent",
        "language": "en",
        "llm": {"provider": "x", "model": "x", "api_key": None,
                "api_key_env": "X"},
        "capabilities": {},
        "soul": {"delay": 120}, "stamina": 3600,
        "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
        "admin": {}, "streaming": False,
    }
    if active_preset is not None:
        preset_block: dict = {"active": active_preset, "default": active_preset}
        if preset_path is not None:
            preset_block["path"] = preset_path
        manifest["preset"] = preset_block
    return {
        "manifest": manifest,
        "principle": "p", "covenant": "c", "pad": "", "prompt": "",
        "soul": "",
    }


def test_avatar_inherits_active_preset_and_absolute_path(tmp_path):
    """Avatar's init.json carries parent's default preset as active, path unchanged.

    When active == default (the normal base case), active is rewritten to
    default (same value), and materialized llm/capabilities are stripped.
    """
    parent_init = _baseline_parent_init(
        preset_path="/abs/path/to/presets", active_preset="minimax")

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    # active is rewritten to default (both were "minimax")
    assert avatar_init["manifest"]["preset"]["active"] == "minimax"
    assert avatar_init["manifest"]["preset"]["path"] == "/abs/path/to/presets"
    # Materialized fields stripped
    assert "llm" not in avatar_init["manifest"]
    assert "capabilities" not in avatar_init["manifest"]


def test_avatar_resolves_relative_preset_default(tmp_path):
    """If parent's preset.default is relative, avatar gets it re-rooted
    against the parent's working_dir (so the avatar can still resolve it
    from a different cwd)."""
    parent_wd = tmp_path / "parent"
    parent_wd.mkdir()
    parent_init = _baseline_parent_init(active_preset="./presets/x.json")

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(
        parent_init, "child", parent_working_dir=parent_wd)

    default_path = avatar_init["manifest"]["preset"]["default"]
    assert Path(default_path).is_absolute()
    assert Path(default_path) == (parent_wd / "presets" / "x.json").resolve()


def test_avatar_no_preset_unchanged(tmp_path):
    """Avatar with parent that has no preset block carries no preset."""
    parent_init = _baseline_parent_init()

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    assert "preset" not in avatar_init["manifest"]


def test_avatar_no_parent_working_dir_relative_path_unchanged(tmp_path):
    """If parent_working_dir is None, relative preset.path is left as-is.

    This preserves backward compatibility with callers that don't pass the new
    keyword. Production callers (avatar._spawn) always pass it; tests may not.
    """
    parent_init = _baseline_parent_init(
        preset_path="./presets", active_preset="x")

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    # Without parent_working_dir, the path is preserved verbatim
    assert avatar_init["manifest"]["preset"]["path"] == "./presets"
    # But materialized fields are still stripped (preset block exists with default)
    assert "llm" not in avatar_init["manifest"]
    assert "capabilities" not in avatar_init["manifest"]


def test_avatar_spawns_with_parent_default_when_active_differs(tmp_path):
    """Avatar's init.json carries parent's default preset as both active AND default,
    even if parent is currently swapped to a different active preset."""
    parent_init = {
        "manifest": {
            "agent_name": "parent", "language": "en",
            "preset": {
                "active": "minimax",   # parent is currently on minimax
                "default": "deepseek", # but default is deepseek
                "path": "/abs/preset/dir",
            },
            "llm": {"provider": "minimax", "model": "MiniMax-M2.7-highspeed",
                    "api_key": None, "api_key_env": "MINIMAX_API_KEY"},
            "capabilities": {"file": {}, "vision": {"provider": "minimax"}},
            "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "prompt": "", "soul": "",
    }

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    # Avatar's preset block: active rewritten to default, default unchanged
    avatar_preset = avatar_init["manifest"]["preset"]
    assert avatar_preset["active"] == "deepseek"
    assert avatar_preset["default"] == "deepseek"
    assert avatar_preset["path"] == "/abs/preset/dir"

    # Materialized fields stripped — _read_init will re-materialize from default
    assert "llm" not in avatar_init["manifest"]
    assert "capabilities" not in avatar_init["manifest"]


def test_avatar_no_preset_block_inherits_flat_config(tmp_path):
    """If parent has no manifest.preset block, avatar inherits the flat
    llm/capabilities verbatim (deep-copy unchanged)."""
    parent_init = {
        "manifest": {
            "agent_name": "parent", "language": "en",
            "llm": {"provider": "x", "model": "y",
                    "api_key": None, "api_key_env": "X"},
            "capabilities": {"file": {}},
            "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "prompt": "", "soul": "",
    }

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    # No preset block, no stripping
    assert "preset" not in avatar_init["manifest"]
    assert avatar_init["manifest"]["llm"]["provider"] == "x"
    assert avatar_init["manifest"]["capabilities"] == {"file": {}}


def test_avatar_strips_materialized_when_active_equals_default(tmp_path):
    """Strip is unconditional: even when active==default, materialized
    fields are stripped so _read_init does the materialization."""
    parent_init = {
        "manifest": {
            "agent_name": "parent", "language": "en",
            "preset": {"active": "deepseek", "default": "deepseek",
                       "path": "/abs/path"},
            "llm": {"provider": "deepseek", "model": "v4",
                    "api_key": None, "api_key_env": "DEEPSEEK_API_KEY"},
            "capabilities": {"file": {}},
            "soul": {"delay": 120}, "stamina": 3600,
            "molt_pressure": 0.8, "molt_prompt": "", "max_turns": 50,
            "admin": {}, "streaming": False,
        },
        "principle": "p", "covenant": "c", "pad": "", "prompt": "", "soul": "",
    }

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    avatar_preset = avatar_init["manifest"]["preset"]
    assert avatar_preset["active"] == "deepseek"
    assert "llm" not in avatar_init["manifest"]
    assert "capabilities" not in avatar_init["manifest"]
