"""Focused permission coverage for Feishu's private conversation state."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from lingtai.mcp_servers.feishu.manager import (
    _harden_existing_state,
    _private_mkdir,
    _write_private_json,
)
from lingtai.mcp_servers.feishu.service import FeishuService
from lingtai.mcp_servers.feishu.task_card import FeishuTaskCardStore
from lingtai.mcp_servers.task_card import TaskCardRoute


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits are not portable to this platform",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_message_write_uses_owner_only_atomic_state(tmp_path: Path) -> None:
    message_dir = tmp_path / "feishu" / "main" / "inbox" / "one"
    _private_mkdir(message_dir)

    message_path = message_dir / "message.json"
    _write_private_json(message_path, {"text": "first"})
    first_inode = message_path.stat().st_ino
    _write_private_json(message_path, {"text": "second"})

    assert _mode(message_dir) == 0o700
    assert _mode(message_path) == 0o600
    assert message_path.stat().st_ino != first_inode
    assert not list(message_dir.glob(".message.json-*"))


def test_startup_migration_hardens_existing_tree_without_following_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "feishu"
    message_dir = root / "main" / "inbox" / "legacy"
    message_dir.mkdir(parents=True)
    message_path = message_dir / "message.json"
    message_path.write_text('{"text":"legacy"}', encoding="utf-8")
    message_dir.chmod(0o755)
    message_path.chmod(0o644)

    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    outside.chmod(0o644)
    (root / "outside-link").symlink_to(outside)

    _harden_existing_state(root)

    assert _mode(root) == 0o700
    assert _mode(root / "main") == 0o700
    assert _mode(root / "main" / "inbox") == 0o700
    assert _mode(message_dir) == 0o700
    assert _mode(message_path) == 0o600
    assert _mode(outside) == 0o644


def test_task_card_state_stays_owner_only_across_atomic_replacements(
    tmp_path: Path,
) -> None:
    old_umask = os.umask(0o022)
    try:
        service = FeishuService(
            tmp_path,
            [
                {
                    "alias": "main",
                    "app_id": "cli_test",
                    "app_secret": "test-secret",
                }
            ],
            lambda _alias, _data: None,
        )
        service.set_taskcard_enabled(False)
        preference_path = tmp_path / "feishu" / "taskcard.json"
        preference_initial_mode = _mode(preference_path)
        service.set_taskcard_normal_rows(7)

        resident_store = FeishuTaskCardStore(tmp_path / "feishu" / "task_cards.json")
        route = TaskCardRoute("main", "oc_chat")
        assert resident_store.set(route, "main:oc_chat:om_card_1") is True
        resident_path = tmp_path / "feishu" / "task_cards.json"
        resident_initial_mode = _mode(resident_path)
        assert resident_store.set(route, "main:oc_chat:om_card_2") is True
    finally:
        os.umask(old_umask)

    assert preference_initial_mode == 0o600
    assert _mode(preference_path) == 0o600
    assert resident_initial_mode == 0o600
    assert _mode(resident_path) == 0o600
