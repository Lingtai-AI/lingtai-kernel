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
    """Avatar's init.json carries parent's preset.active and absolute preset.path verbatim."""
    parent_init = _baseline_parent_init(
        preset_path="/abs/path/to/presets", active_preset="minimax")

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(parent_init, "child")

    assert avatar_init["manifest"]["preset"]["active"] == "minimax"
    assert avatar_init["manifest"]["preset"]["path"] == "/abs/path/to/presets"


def test_avatar_resolves_relative_presets_path(tmp_path):
    """If parent's preset.path is relative, avatar gets it resolved to absolute."""
    parent_wd = tmp_path / "parent"
    parent_wd.mkdir()
    (parent_wd / "presets").mkdir()
    parent_init = _baseline_parent_init(
        preset_path="./presets", active_preset="x")

    from lingtai.core.avatar import AvatarManager
    avatar_init = AvatarManager._make_avatar_init(
        parent_init, "child", parent_working_dir=parent_wd)

    preset_path = avatar_init["manifest"]["preset"]["path"]
    # Must be absolute and point to the parent's presets folder
    assert Path(preset_path).is_absolute()
    assert Path(preset_path) == (parent_wd / "presets").resolve()


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
