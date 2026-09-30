"""Claude Code provider — drive the local ``claude`` CLI as the agent brain.

This adapter lets a LingTai agent think on a Claude **subscription** (Pro/Max)
through the official ``claude`` command-line binary, rather than calling the
Anthropic API with a key. It is the Claude analogue of the Codex provider:
point a preset at ``provider: "claude-code"``.

Design: the ``claude`` CLI is used in print mode (``claude -p --output-format
json``) as a *stateless reasoning core*. Each turn the adapter serialises the
canonical ChatInterface (system prompt + tool schemas + conversation) into a
single prompt, asks the CLI to emit exactly one JSON *action* (a tool call or a
final answer), and parses that back into the kernel's ``LLMResponse``. LingTai's
own message loop still executes the tools — so the main agent loop, intrinsics,
capabilities and MCP all stay intact. Claude's built-in tools are disabled and
its native system prompt is replaced by LingTai's, so it behaves as a pure
brain.

Auth, in order (see ``adapter.py``):

1. A long-lived OAuth token from ``claude setup-token`` — the preset's
   ``api_key_env`` (default ``CLAUDE_CODE_OAUTH_TOKEN``), else that process env
   var. The child sees exactly that token plus a private, LingTai-owned
   ``CLAUDE_CONFIG_DIR``, so this machine's ``~/.claude`` state is never read.
2. Otherwise the installed CLI's own local login, when ``claude auth status``
   reports one; user/project/local settings files are not loaded
   (``--setting-sources ""``).
3. Otherwise a request-time auth error with guidance (no AED retries).

``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL`` and the
``CLAUDE_CODE_USE_*`` cloud-provider switches are always stripped from the child
env, so the token only reaches Anthropic and nothing bills an API key or a cloud
account. The policy lives in ``auth.py`` and is shared with the daemon
``claude`` / ``claude-p`` / ``claude-code`` CLI backends. With no
configured ``model`` / ``thinking`` the CLI's own defaults apply (no
``--model`` / ``--effort`` flag).

Reasoning effort is the one Claude Code special case: the CLI controls
reasoning depth through its own ``--effort <level>`` flag, so a configured
thinking level is normalized to ``--effort`` argv exactly once per session
(vocabulary ``low | medium | high | xhigh | max``; omitted/default sends no
flag, and a caller-supplied ``--effort`` in ``extra_argv`` is never
duplicated). No other provider shares this wire.
"""

from __future__ import annotations

from .adapter import ClaudeCodeAdapter, ClaudeCodeChatSession

__all__ = ["ClaudeCodeAdapter", "ClaudeCodeChatSession"]
