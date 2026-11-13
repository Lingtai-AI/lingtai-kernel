"""Each claude-code chat session owns its system-prompt file.

Regression: ``LLMService`` caches one ``ClaudeCodeAdapter`` per (provider,
base_url), so the main chat and the a-priori tool-result summarizer
(``turn._build_apriori_summarizer_fn``) are built on the same adapter. The
prompt-cache file used to belong to the adapter, so constructing the
summarizer session overwrote the main chat's system block with "You are a
tool-result summarizer" and "(no tools available ...)". The main chat then ran
every later turn on that prompt; once a molt dropped its ``--resume`` history it
only produced third-person ``final`` summaries and never called a tool again.

A fake ``claude`` executable records which system prompt each call received.
"""
from __future__ import annotations

import gc
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake CLI is a POSIX executable script"
)

_FAKE_CLI = """#!{python}
import json, sys
argv = sys.argv[1:]
prompt = ""
for flag in ("--system-prompt-file", "--append-system-prompt-file"):
    if flag in argv:
        with open(argv[argv.index(flag) + 1], encoding="utf-8") as f:
            prompt = f.read()
sys.stdin.read()
instructions = prompt.split("# AGENT SYSTEM INSTRUCTIONS\\n", 1)[-1].splitlines()
tools = prompt.split("# AVAILABLE TOOLS\\n", 1)[-1].splitlines()
with open({log!r}, "a", encoding="utf-8") as out:
    out.write(json.dumps({{
        "instructions": instructions[0] if instructions else "",
        "tools": tools[0] if tools else "",
    }}) + "\\n")
print(json.dumps({{
    "type": "result", "subtype": "success", "is_error": False,
    "result": json.dumps({{"action": "final", "text": "ok"}}),
    "session_id": "fake-session", "usage": {{}},
}}))
"""


@pytest.fixture
def fake_cli(tmp_path: Path):
    log = tmp_path / "calls.jsonl"
    cli = tmp_path / "claude"
    cli.write_text(_FAKE_CLI.format(python=sys.executable, log=str(log)), encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)

    def calls() -> list[dict]:
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    return str(cli), calls


def _telegram_tool() -> FunctionSchema:
    return FunctionSchema(
        name="telegram",
        description="Send Telegram messages.",
        parameters={"type": "object", "properties": {}},
    )


def test_second_session_does_not_overwrite_main_system_prompt(fake_cli):
    cli, calls = fake_cli
    adapter = ClaudeCodeAdapter(cli_path=cli)
    main = adapter.create_chat("opus", "MAIN AGENT: you are scientist.", tools=[_telegram_tool()])
    main.send("turn 1")

    # What the a-priori summarizer does: a fresh, tool-less session on the
    # same (cached) adapter.
    summarizer = adapter.create_chat("opus", "You are a tool-result summarizer.", tools=None)
    summarizer.send("summarize this")

    main.send("turn 2")

    first, summary, second = calls()
    assert first == {"instructions": "MAIN AGENT: you are scientist.", "tools": "## telegram"}
    assert summary["instructions"] == "You are a tool-result summarizer."
    assert summary["tools"].startswith("(no tools available")
    assert second == first


def test_session_prompt_file_is_removed_when_session_is_collected(fake_cli):
    cli, _ = fake_cli
    adapter = ClaudeCodeAdapter(cli_path=cli)
    session = adapter.create_chat("opus", "You are a tool-result summarizer.", tools=None)
    path = session._system_prompt_file
    assert path is not None and os.path.exists(path)

    del session
    gc.collect()

    assert not os.path.exists(path)


# -- Replaced sessions must be released, or their prompt files outlive them --
#
# ``LLMService`` registers every tracked session. ``SessionManager`` replaces
# its chat on rebuild (AED recovery, refresh, restore, MCP tool changes) and
# discards it on molt; the replaced session has to leave the registry so it can
# be collected and its prompt-cache file removed.


def _claude_code_service(cli: str):
    from lingtai.llm import LLMService

    service = LLMService("claude-code", "opus")
    service._adapters[("claude-code", None)] = ClaudeCodeAdapter(cli_path=cli)
    return service


def _session_manager(service):
    from lingtai.kernel.config import AgentConfig
    from lingtai.kernel.session import SessionManager

    return SessionManager(
        llm_service=service,
        config=AgentConfig(provider="claude-code", model="opus"),
        agent_name="scientist",
        streaming=False,
        build_system_prompt_fn=lambda: "MAIN AGENT: you are scientist.",
        build_tool_schemas_fn=lambda: [_telegram_tool()],
        logger_fn=None,
    )


def test_release_session_forgets_only_that_session(fake_cli):
    cli, _ = fake_cli
    service = _claude_code_service(cli)
    first = service.create_session("one", tracked=True)
    second = service.create_session("two", tracked=True)

    service.release_session(first.session_id)
    service.release_session("")  # untracked sessions have no id: a no-op

    assert service.get_session(first.session_id) is None
    assert service.get_session(second.session_id) is second


def test_rebuild_releases_replaced_session_and_its_prompt_file(fake_cli):
    cli, _ = fake_cli
    service = _claude_code_service(cli)
    manager = _session_manager(service)

    old = manager.ensure_session()
    old_id, old_path = old.session_id, old._system_prompt_file
    manager._rebuild_session(old.interface)
    new = manager.chat

    assert new is not old
    assert service.get_session(old_id) is None
    assert service.get_session(new.session_id) is new
    del old
    gc.collect()
    assert not os.path.exists(old_path)
    assert os.path.exists(new._system_prompt_file)


def test_discard_chat_releases_session_and_its_prompt_file(fake_cli):
    cli, _ = fake_cli
    service = _claude_code_service(cli)
    manager = _session_manager(service)

    chat = manager.ensure_session()
    chat_id, path = chat.session_id, chat._system_prompt_file
    manager.discard_chat()

    assert manager.chat is None
    assert service.get_session(chat_id) is None
    del chat
    gc.collect()
    assert not os.path.exists(path)
