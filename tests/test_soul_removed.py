"""The Soul subsystem is gone — and its historical bytes stay readable.

Negative contract: no ``soul`` tool family, intrinsic, host port, adapter,
config field, i18n key, timer, ``.inquiry`` signal, or notification producer
exists in the active runtime.

Inert compatibility window: an older agent's ``init.json`` that still carries
``manifest.soul`` (or the long-retired top-level ``soul_file``) parses without
error, warning, or rewrite; leftover ``.notification/soul.json`` /
``.notification/btw.json`` files, ``source="soul"`` token-ledger rows, and
archived ``soul`` tool-call pairs in chat history are tolerated by the generic
readers without being deleted, migrated, or executed.
"""
from __future__ import annotations

import importlib
import importlib.resources
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.init_reader import InitReadStatus, read_init
from lingtai.init_schema import validate_init
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.kernel.notifications import (
    is_channel_allowed,
    validate_allowed_channel,
)
from lingtai.kernel.token_ledger import (
    append_token_entry,
    is_daemon_entry,
    sum_token_ledger,
)
from tests._notification_store_helpers import notification_store_for


# ---------------------------------------------------------------------------
# Negative contract — the active runtime owns nothing named Soul
# ---------------------------------------------------------------------------


def test_soul_package_and_declaration_are_gone():
    with pytest.raises(ImportError):
        importlib.import_module("lingtai.tools.soul")

    from lingtai.kernel import tool_plugin
    from lingtai.tools.registry import INTRINSICS

    assert "soul" not in INTRINSICS
    assert "soul" not in tool_plugin.OFFICIAL_TOOL_PLUGIN_NAMES
    assert "soul_runtime" not in tool_plugin.GRANTABLE_HOST_PORTS
    assert not hasattr(tool_plugin, "SoulRuntimePort")

    from lingtai.adapters import tool_plugin_host

    assert not hasattr(tool_plugin_host, "AgentSoulRuntimeAdapter")
    assert not hasattr(tool_plugin_host, "agent_soul_runtime")
    assert not any("soul" in name.lower() for name in tool_plugin_host.__all__)


def test_kernel_agent_owns_no_soul_hooks_or_config():
    from lingtai.kernel.base_agent import BaseAgent

    for retired in (
        "_start_soul_timer",
        "_cancel_soul_timer",
        "_soul_whisper",
        "_run_inquiry",
        "_persist_soul_entry",
        "_append_soul_flow_record",
        "_flatten_v3_for_pair",
        "_run_consultation_fire",
        "_rehydrate_appendix_tracking",
    ):
        assert not hasattr(BaseAgent, retired), retired

    cfg = AgentConfig()
    for retired in (
        "soul_delay",
        "consultation_past_count",
        "soul_voice",
        "soul_voice_prompt",
        "insights_interval",
    ):
        assert not hasattr(cfg, retired), retired

    import lingtai.kernel.config as config_module

    assert not hasattr(config_module, "DEFAULT_SOUL_DELAY_SECONDS")


def test_no_soul_or_auto_insight_strings_remain_in_the_catalogs():
    for package, prefixes in (
        ("lingtai.tools.i18n", ("soul.",)),
        ("lingtai.kernel.i18n", ("soul.", "insight.")),
    ):
        for lang in ("en", "zh", "wen"):
            resource = importlib.resources.files(package) / f"{lang}.json"
            catalog = json.loads(resource.read_text(encoding="utf-8"))
            leaked = [key for key in catalog if key.startswith(prefixes)]
            assert leaked == [], (package, lang, leaked)


def test_soul_and_btw_are_not_publishable_channels(tmp_path: Path):
    for channel in ("soul", "btw"):
        assert is_channel_allowed(channel, workdir=str(tmp_path)) is False
        with pytest.raises(ValueError):
            validate_allowed_channel(channel, workdir=str(tmp_path))


def test_lifecycle_no_longer_consumes_an_inquiry_signal_file():
    from lingtai.kernel.base_agent import lifecycle

    source = Path(lifecycle.__file__).read_text(encoding="utf-8")
    assert ".inquiry" not in source

    from lingtai.adapters.posix import git_cli

    assert ".inquiry" not in git_cli._GITIGNORE


# ---------------------------------------------------------------------------
# Inert compatibility — old bytes stay readable, untouched, and unexecuted
# ---------------------------------------------------------------------------


def test_old_init_with_manifest_soul_reads_inert_and_is_not_rewritten(tmp_path: Path):
    raw = json.dumps(
        {
            "manifest": {
                "llm": {"provider": "openai", "model": "gpt-4o"},
                "soul": {
                    "delay": 120,
                    "consultation_past_count": 2,
                    "voice": "custom",
                    "voice_prompt": "speak plainly",
                    "unknown_future_key": ["anything"],
                },
            },
            "soul_file": "soul.md",
            "pad": "durable state",
        },
        indent=2,
    )
    (tmp_path / "init.json").write_text(raw, encoding="utf-8")

    assert validate_init(json.loads(raw)) == []

    outcome = read_init(tmp_path)

    assert outcome.status is InitReadStatus.READ_OK_WITH_IGNORED_FIELDS
    assert "manifest.soul" in outcome.ignored_paths
    assert "soul_file" in outcome.ignored_paths
    assert outcome.data is not None
    assert outcome.data["manifest"]["soul"]["delay"] == 120
    assert (tmp_path / "init.json").read_text(encoding="utf-8") == raw


def test_manifest_soul_of_any_shape_never_becomes_agent_config():
    from lingtai.agent import build_agent_config

    for legacy in ({"delay": 5}, "inner", 7, None, {"voice": "custom"}):
        cfg = build_agent_config(
            {"llm": {"provider": "openai", "model": "gpt-4o"}, "soul": legacy},
            max_rpm=0,
        )
        assert not hasattr(cfg, "soul_delay")


def test_stale_soul_and_btw_notification_files_are_ignored_not_crashed(tmp_path: Path):
    notif_dir = tmp_path / ".notification"
    notif_dir.mkdir()
    soul_bytes = json.dumps(
        {
            "header": "soul flow",
            "icon": "🌊",
            "priority": "normal",
            "published_at": "2026-09-01T00:00:00Z",
            "data": {"fire_id": "fire_1", "voices": [{"source": "insights", "voice": "…"}]},
        }
    ).encode("utf-8")
    btw_bytes = json.dumps(
        {
            "header": "/btw side inquiry answered",
            "icon": "💭",
            "published_at": "2026-09-01T00:00:00Z",
            "data": {"source": "human", "mode": "inquiry", "question": "q", "answer": "a"},
        }
    ).encode("utf-8")
    (notif_dir / "soul.json").write_bytes(soul_bytes)
    (notif_dir / "btw.json").write_bytes(btw_bytes)
    (notif_dir / "system.json").write_text(
        json.dumps({"header": "1 system notification", "data": {"events": []}}),
        encoding="utf-8",
    )

    store = notification_store_for(tmp_path)
    workdir = str(tmp_path)

    def allow(channel: str) -> bool:
        return is_channel_allowed(channel, workdir=workdir)

    snapshot = store.snapshot(allow)
    assert set(snapshot) == {"system"}
    names = {entry[0] for entry in store.fingerprint(allow)}
    assert names == {"system.json"}

    # The historical bytes are neither deleted, rewritten, nor migrated.
    assert (notif_dir / "soul.json").read_bytes() == soul_bytes
    assert (notif_dir / "btw.json").read_bytes() == btw_bytes


def test_legacy_soul_token_ledger_rows_stay_readable(tmp_path: Path):
    ledger = tmp_path / "logs" / "token_ledger.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(
        json.dumps({"ts": "2026-09-01T00:00:00Z", "source": "soul", "input": 40,
                    "output": 4, "thinking": 0, "cached": 0}) + "\n",
        encoding="utf-8",
    )
    legacy_bytes = ledger.read_bytes()

    assert is_daemon_entry({"source": "soul", "input": 40}) is False
    append_token_entry(ledger, input=10, output=1, thinking=0, cached=0,
                       extra={"source": "main"})
    assert ledger.read_bytes().startswith(legacy_bytes)

    totals = sum_token_ledger(ledger)
    assert totals["input_tokens"] == 50
    assert totals["api_calls"] == 2
    assert sum_token_ledger(ledger, scope="main_agent")["api_calls"] == 2


def test_archived_soul_flow_pair_round_trips_through_chat_history():
    iface = ChatInterface()
    iface.add_user_message("hello")
    iface.add_assistant_message([
        ToolCallBlock(id="fire_1", name="soul", args={"action": "flow"}),
    ])
    iface.add_tool_results([
        ToolResultBlock(
            id="fire_1",
            name="soul",
            content={"status": "ok", "voices": [{"source": "insights", "voice": "…"}]},
        ),
    ])
    iface.add_assistant_message([TextBlock(text="noted")])

    serialized = iface.to_dict()
    restored = ChatInterface.from_dict(json.loads(json.dumps(serialized)))

    assert restored.to_dict() == serialized
    assert restored.has_pending_tool_calls() is False
    # The generic single-slot helper still recognises the archived shape; it
    # is only ever invoked by a live producer, and no Soul producer exists.
    assert restored.remove_pair_by_call_id("fire_1") is True
    assert restored.remove_pair_by_call_id("fire_1") is False


def test_retention_never_targets_a_leftover_soul_flow_log(tmp_path: Path):
    from lingtai.kernel.maintenance import retention

    assert not hasattr(retention, "FOOTPRINT_SOUL_FLOW")
    assert "soul" not in " ".join(retention.FOOTPRINT_CATEGORIES)


def test_goal_reminder_fallback_no_longer_reads_a_soul_delay():
    from lingtai.kernel.nudge import goal

    agent = SimpleNamespace()  # no ``_soul_delay`` attribute anywhere
    assert goal._reminder_delay(agent, {"data": {}}) == goal._DEFAULT_DELAY_SECONDS
    assert goal._reminder_delay(agent, {"data": {"reminder_delay_seconds": 7}}) == 7.0
