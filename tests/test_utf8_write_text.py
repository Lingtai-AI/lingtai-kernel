"""Regression tests for the Chinese-Windows cp936/GBK Path.write_text() defect.

The runtime reads almost all text files as UTF-8.  When ``Path.write_text()``
is called without an explicit ``encoding=`` keyword, Python falls back to the
process locale (cp936 on Chinese Windows).  Ordinary Chinese can be encoded as
GBK and written successfully, but the subsequent UTF-8 read raises
``UnicodeDecodeError``.  Emoji and rare CJK can fail at write time with
``UnicodeEncodeError`` and leave the target file truncated.

These tests enforce the project-wide invariant that every production
``Path.write_text()`` call under ``src/lingtai/`` must pass ``encoding="utf-8"``
explicitly, and they exercise the concrete chains identified in the 2026-08-01
audit.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Static invariant and locale test double
# ---------------------------------------------------------------------------

def _service_mock():
    """Return a minimal service stub that lets ``Agent`` construct in tests."""
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "anthropic"
    svc.model = "claude-test"
    return svc


@contextmanager
def _cp936_default_for(target: Path):
    """Make an omitted encoding use cp936 for one audited target path.

    Explicit encodings remain untouched.  This deterministically reproduces a
    Chinese-Windows default without changing the host locale or pretending that
    the test ran on Windows.
    """
    original = Path.write_text

    def write_text(self, data, encoding=None, errors=None, newline=None):
        if self == target and encoding is None:
            encoding = "cp936"
        return original(self, data, encoding=encoding, errors=errors, newline=newline)

    with patch.object(Path, "write_text", new=write_text):
        yield


def test_source_write_text_calls_pin_utf8_encoding() -> None:
    """Production code must not depend on the host locale when writing text."""
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted((root / "src" / "lingtai").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_text"
            ):
                continue
            encoding_values = [kw.value for kw in node.keywords if kw.arg == "encoding"]
            if (
                len(encoding_values) == 1
                and isinstance(encoding_values[0], ast.Constant)
                and encoding_values[0].value == "utf-8"
            ):
                continue
            rel = path.relative_to(root)
            offenders.append(f"{rel}:{node.lineno}: {lines[node.lineno - 1].strip()}")

    assert offenders == []


# ---------------------------------------------------------------------------
# Behavioral regression tests for the audited crash chains
# ---------------------------------------------------------------------------

def test_flush_system_prompt_persists_emoji_with_cp936_default(tmp_path):
    """An omitted encoding would fail immediately for emoji under cp936.

    The target-only test double makes only a missing encoding on ``system.md``
    inherit cp936.  The patched explicit UTF-8 write must bypass that default;
    the frozen base raises ``UnicodeEncodeError`` and can truncate the file.
    """
    from lingtai.agent import Agent
    from lingtai.kernel.base_agent.prompt import _flush_system_prompt

    agent = Agent(
        service=_service_mock(),
        agent_name="test",
        working_dir=tmp_path / "agent",
    )
    prompt = "系统提示包含 emoji 👀"
    agent._build_system_prompt = lambda: prompt
    system_md = agent._working_dir / "system" / "system.md"

    with _cp936_default_for(system_md):
        _flush_system_prompt(agent)

    assert system_md.read_text(encoding="utf-8") == prompt


def test_lingtai_seed_chinese_roundtrip_with_cp936_default(tmp_path):
    """The Agent seed write survives the cp936 write -> UTF-8-read chain.

    Ordinary Chinese is intentionally used without emoji: an omitted encoding
    writes GBK successfully, then ``_lingtai_load`` raises ``UnicodeDecodeError``.
    Passing a nonempty value through ``_reload_prompt_sections`` proves the
    changed Agent write itself is reached instead of pre-seeding the file.
    """
    from lingtai.agent import Agent

    agent = Agent(
        service=_service_mock(),
        agent_name="test",
        working_dir=tmp_path / "agent",
    )
    content = "灵台内容为中文"
    lingtai_path = agent._working_dir / "system" / "lingtai.md"

    with _cp936_default_for(lingtai_path):
        agent._reload_prompt_sections({"lingtai": content})

    assert lingtai_path.read_text(encoding="utf-8") == content
    assert agent._prompt_manager.read_section("character") == content


def test_pad_chinese_roundtrip(tmp_path):
    """A Chinese pad seed is written as UTF-8 and then read back as UTF-8."""
    from lingtai.agent import Agent

    content = "中文 pad 内容"
    agent = Agent(
        service=_service_mock(),
        agent_name="test",
        working_dir=tmp_path / "agent",
        pad=content,
    )

    pad_file = agent._working_dir / "system" / "pad.md"
    assert pad_file.read_text(encoding="utf-8") == content


def test_rules_chinese_roundtrip(tmp_path):
    """A Chinese ``.rules`` signal is persisted to ``system/rules.md`` as UTF-8."""
    from lingtai.agent import Agent

    agent = Agent(
        service=_service_mock(),
        agent_name="test",
        working_dir=tmp_path / "agent",
    )
    content = "规则内容：禁止删除文件。👀"
    (agent._working_dir / ".rules").write_text(content, encoding="utf-8")

    agent._check_rules_file()

    assert (agent._working_dir / "system" / "rules.md").read_text(encoding="utf-8") == content
    assert agent._prompt_manager.read_section("rules") == content


def test_email_inbox_persists_chinese_ensure_ascii_false(tmp_path):
    """``ensure_ascii=False`` Chinese email JSON is written and re-read as UTF-8.

    The original code in ``tools/email/primitives.py`` wrote the JSON without
    encoding, so ``ensure_ascii=False`` Chinese bodies would be persisted as
    GBK on Chinese Windows and later fail the UTF-8 read in ``_load_message``
    and ``_list_inbox``.
    """
    from lingtai.tools.email.primitives import _persist_to_inbox

    agent = SimpleNamespace(_working_dir=tmp_path / "agent")
    payload = {
        "from": "sender",
        "to": "recipient",
        "subject": "中文主题",
        "message": "中文邮件正文",
    }

    msg_id = _persist_to_inbox(agent, payload)

    msg_file = agent._working_dir / "mailbox" / "inbox" / msg_id / "message.json"
    assert msg_file.is_file()
    # The persisted file must decode as UTF-8 and round-trip the Chinese body.
    raw = msg_file.read_text(encoding="utf-8")
    assert "中文邮件正文" in raw
    data = json.loads(raw)
    assert data["message"] == "中文邮件正文"
    assert data["subject"] == "中文主题"
