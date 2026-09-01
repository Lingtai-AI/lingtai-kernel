"""Configurable Task Card projection language (locale en|zh).

The shared TaskCardEventProjection defaults to English; ``zh`` is an opt-in
per-agent locale (``/taskcard lang zh|en``) that localizes the fixed header,
footer, time prefix, stuck/offline refresh hint, and API-error state words
without hard-coding any channel behavior (revert of #1209's hard-coded
Chinese, Jason 2026-08-10).
"""

from __future__ import annotations

from lingtai.mcp_servers.task_card.event_projection import TaskCardEventProjection


def _render(rows, *, locale="en"):
    return TaskCardEventProjection.format_rows_task_card_text(
        rows,
        normal_rows=1,
        locale=locale,
    )


# ---------------------------------------------------------------------------
# Default English surface stays byte-identical to the canonical render
# ---------------------------------------------------------------------------

def test_default_locale_is_english():
    text = _render([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False},
    ])
    assert text.startswith("Don't reply to this Task Card")
    assert "\U0001f4cb ACTIVITIES\n" in text
    assert "Last Updated: " in text
    assert "Don't reply to this Task Card" in text


def test_normalize_locale_falls_back_to_english():
    assert TaskCardEventProjection.normalize_locale("en") == "en"
    assert TaskCardEventProjection.normalize_locale("zh") == "zh"
    assert TaskCardEventProjection.normalize_locale("fr") == "en"
    assert TaskCardEventProjection.normalize_locale(None) == "en"
    assert TaskCardEventProjection.normalize_locale("") == "en"


# ---------------------------------------------------------------------------
# zh locale renders the localized fixed surface
# ---------------------------------------------------------------------------

def test_zh_header_and_footer():
    assert TaskCardEventProjection.header("zh") == "\U0001f4cb \u6d3b\u52a8"
    assert TaskCardEventProjection.header("en") == "\U0001f4cb ACTIVITIES"
    assert TaskCardEventProjection.time_prefix("zh") == "\u6700\u540e\u66f4\u65b0: "
    assert TaskCardEventProjection.time_prefix("en") == "Last Updated: "
    assert TaskCardEventProjection.footer(3, "zh") == (
        "\u8bf7\u52ff\u56de\u590d\u6b64\u4efb\u52a1\u5361\u7247\u3002\u4f7f\u7528 /taskcard on|off \u5207\u6362\uff1b"
        "/taskcard N \u8bbe\u7f6e\u663e\u793a\u7ec4\u6570 (1-10\uff0c\u5f53\u524d: 3)\u3002"
    )
    assert TaskCardEventProjection.footer(3, "en") == (
        "Don't reply to this Task Card. Use /taskcard on|off to toggle; "
        "/taskcard N sets normal rows (1-10, current: 3)."
    )


def test_zh_rows_render_localized_surface():
    text = _render([
        {"tool": "bash", "tool_action": "run", "reasoning": "build",
         "elapsed_s": 3, "done": False},
    ], locale="zh")
    assert text.startswith("\u8bf7\u52ff\u56de\u590d\u6b64\u4efb\u52a1\u5361\u7247")
    assert "\U0001f4cb \u6d3b\u52a8\n" in text
    assert "\u6700\u540e\u66f4\u65b0: " in text
    assert "\u8bf7\u52ff\u56de\u590d\u6b64\u4efb\u52a1\u5361\u7247" in text
    assert "ACTIVITIES" not in text
    assert "Last Updated:" not in text


def test_zh_api_error_state_words():
    text = TaskCardEventProjection.format_rows_task_card_text(
        [
            {"kind": "api_error", "state": "error",
             "error_type": "TimeoutError", "provider": "openai",
             "model": "gpt-5", "status": 408},
        ],
        normal_rows=1,
        locale="zh",
    )
    assert "API \u9519\u8bef" in text
    assert "\u5931\u8d25" in text
    assert "API error" not in text


def test_zh_metadata_stuck_hint():
    lines = TaskCardEventProjection.format_metadata(
        {"agent_lifecycle": "stuck"}, locale="zh"
    )
    assert lines == ["\u4f1a\u8bdd \u00b7 agent \u00b7 stuck \u00b7 \u5c1d\u8bd5 /refresh"]
    lines_en = TaskCardEventProjection.format_metadata(
        {"agent_lifecycle": "stuck"}, locale="en"
    )
    assert lines_en == ["Session \u00b7 agent \u00b7 stuck \u00b7 try /refresh"]


def test_render_event_groups_locale():
    groups = [
        {
            "api_call_id": "call-1",
            "events": [
                {"kind": "tool", "tool": "bash", "tool_action": "run",
                 "reasoning": "x", "status": "???"},
            ],
        }
    ]
    text = TaskCardEventProjection.render_event_groups(
        groups, normal_rows=1, locale="zh"
    )
    assert text.startswith("\u8bf7\u52ff\u56de\u590d\u6b64\u4efb\u52a1\u5361\u7247")
    assert "\U0001f4cb \u6d3b\u52a8\n" in text


# ---------------------------------------------------------------------------
# Telegram service durable locale persistence (taskcard.json seam)
# ---------------------------------------------------------------------------

def _service(tmp_path):
    from lingtai.mcp_servers.telegram.service import TelegramService

    return TelegramService(
        tmp_path,
        [],
        on_message=lambda *_: None,
    )


def test_service_locale_defaults_to_english(tmp_path):
    svc = _service(tmp_path)
    assert svc.taskcard_locale() == "en"


def test_service_locale_persists_and_reloads(tmp_path):
    svc = _service(tmp_path)
    svc.set_taskcard_locale("zh")
    assert svc.taskcard_locale() == "zh"

    reborn = _service(tmp_path)
    assert reborn.taskcard_locale() == "zh"
    assert reborn.taskcard_enabled() is True
    assert reborn.taskcard_normal_rows() == 1


def test_service_locale_rejects_unknown(tmp_path):
    svc = _service(tmp_path)
    try:
        svc.set_taskcard_locale("fr")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported locale")
    assert svc.taskcard_locale() == "en"


def test_service_locale_survives_sibling_setting_change(tmp_path):
    svc = _service(tmp_path)
    svc.set_taskcard_locale("zh")
    svc.set_taskcard_normal_rows(4)
    assert svc.taskcard_locale() == "zh"
    assert svc.taskcard_normal_rows() == 4

    reborn = _service(tmp_path)
    assert reborn.taskcard_locale() == "zh"
    assert reborn.taskcard_normal_rows() == 4
