"""Tests for the lazy ``lingtai`` top-level facade.

After the namespace consolidation, ``src/lingtai/__init__.py`` is a lightweight
PEP-562 facade: ``import lingtai`` does only stdlib/importlib.metadata work,
and every public name in ``__all__`` is resolved lazily from its canonical
source module on first access.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


# Modules that must NOT be loaded by a bare ``import lingtai``.
_FORBIDDEN_AFTER_BARE_IMPORT: frozenset[str] = frozenset(
    {
        "lingtai.agent",
        "lingtai.kernel",
        "lingtai.tools",
        "lingtai.llm",
        "lingtai.services.file_io",
        "lingtai.services.file_io_sidecar",
        "lingtai.services.vision",
        "lingtai.services.websearch",
        "lingtai.mcp_servers",
    }
    | {
        # Concrete providers and LLM submodules
        f"lingtai.llm.{name}"
        for name in (
            "anthropic",
            "claude_code",
            "custom",
            "deepseek",
            "gemini",
            "minimax",
            "mimo",
            "openai",
            "openrouter",
            "zhipu",
        )
    }
)


def _run_in_subprocess(code: str) -> subprocess.CompletedProcess[str]:
    """Run Python code in a fresh subprocess with this worktree's src on PYTHONPATH."""
    repo_root = Path(__file__).resolve().parents[1]
    # Drop inherited PYTHONPATH (e.g. another worktree's src) so the subprocess
    # resolves only this worktree's source tree.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(repo_root / "src")
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env=env,
    )


def test_bare_import_lingtai_is_lightweight():
    """``import lingtai`` must not eagerly load heavy implementation modules."""
    code = (
        "import sys; "
        "import lingtai; "
        "loaded = set(sys.modules); "
        f"exact = {_FORBIDDEN_AFTER_BARE_IMPORT!r}; "
        "prefixes = ('lingtai.kernel.', 'lingtai.tools.', 'lingtai.services.', 'lingtai.llm.', 'lingtai.mcp_servers.'); "
        "forbidden = sorted((loaded & exact) | {m for m in loaded if any(m.startswith(p) for p in prefixes)}); "
        "print('FORBIDDEN:', forbidden) if forbidden else print('LIGHTWEIGHT')"
    )
    result = _run_in_subprocess(code)
    assert result.returncode == 0, (
        f"Subprocess failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "LIGHTWEIGHT" in result.stdout, (
        f"Bare import lingtai loaded forbidden modules.\nstdout: {result.stdout}"
    )


def test_facade_names_match_canonical_objects():
    """Every lazy facade name is identical to the object in its canonical module."""
    import lingtai

    # Pre-load canonical modules so the identity check is unambiguous.
    import lingtai.agent
    import lingtai.kernel.base_agent
    import lingtai.kernel.config
    import lingtai.kernel.message
    import lingtai.kernel.services.logging
    import lingtai.kernel.services.mail
    import lingtai.kernel.state
    import lingtai.kernel.types
    import lingtai.services.file_io
    import lingtai.services.file_io_sidecar
    import lingtai.services.vision
    import lingtai.services.websearch
    import lingtai.tools.avatar
    import lingtai.tools.bash
    import lingtai.tools.email
    import lingtai.tools.registry

    assert lingtai.Agent is lingtai.agent.Agent
    assert lingtai.BaseAgent is lingtai.kernel.base_agent.BaseAgent
    assert lingtai.AgentConfig is lingtai.kernel.config.AgentConfig
    assert lingtai.AgentState is lingtai.kernel.state.AgentState
    assert lingtai.Message is lingtai.kernel.message.Message
    assert lingtai.MSG_REQUEST is lingtai.kernel.message.MSG_REQUEST
    assert lingtai.MSG_USER_INPUT is lingtai.kernel.message.MSG_USER_INPUT
    assert lingtai.UnknownToolError is lingtai.kernel.types.UnknownToolError

    assert lingtai.setup_capability is lingtai.tools.registry.setup_capability
    assert lingtai.BashManager is lingtai.tools.bash.BashManager
    assert lingtai.AvatarManager is lingtai.tools.avatar.AvatarManager
    assert lingtai.EmailManager is lingtai.tools.email.EmailManager

    assert lingtai.FileIOBackend is lingtai.services.file_io.FileIOBackend
    assert lingtai.FileIOService is lingtai.services.file_io.FileIOService
    assert lingtai.GrepMatch is lingtai.services.file_io.GrepMatch
    assert lingtai.LocalFileIOBackend is lingtai.services.file_io.LocalFileIOBackend
    assert lingtai.LocalFileIOService is lingtai.services.file_io.LocalFileIOService
    assert lingtai.BACKEND_ENV_VAR is lingtai.services.file_io_sidecar.BACKEND_ENV_VAR
    assert lingtai.RustFileIOBackend is lingtai.services.file_io_sidecar.RustFileIOBackend
    assert lingtai.SidecarAdapter is lingtai.services.file_io_sidecar.SidecarAdapter
    assert lingtai.SidecarError is lingtai.services.file_io_sidecar.SidecarError
    assert (
        lingtai.default_file_io_service
        is lingtai.services.file_io_sidecar.default_file_io_service
    )
    assert (
        lingtai.resolve_sidecar_binary
        is lingtai.services.file_io_sidecar.resolve_sidecar_binary
    )

    assert lingtai.MailService is lingtai.kernel.services.mail.MailService
    assert (
        lingtai.FilesystemMailService
        is lingtai.kernel.services.mail.FilesystemMailService
    )
    assert lingtai.LoggingService is lingtai.kernel.services.logging.LoggingService
    assert lingtai.JSONLLoggingService is lingtai.kernel.services.logging.JSONLLoggingService

    assert lingtai.VisionService is lingtai.services.vision.VisionService
    assert (
        lingtai.create_vision_service is lingtai.services.vision.create_vision_service
    )
    assert lingtai.SearchService is lingtai.services.websearch.SearchService
    assert lingtai.SearchResult is lingtai.services.websearch.SearchResult
    assert (
        lingtai.create_search_service
        is lingtai.services.websearch.create_search_service
    )


def test_unknown_attribute_raises_attribute_error():
    """Accessing an undeclared name on ``lingtai`` raises ``AttributeError``."""
    import lingtai

    with pytest.raises(AttributeError, match="module 'lingtai' has no attribute"):
        _ = lingtai.this_name_does_not_exist


def test_dir_includes_standard_module_attributes_and_all_declared_names():
    """``dir(lingtai)`` contains standard module attributes and every name in ``__all__``."""
    import lingtai

    names = dir(lingtai)
    for name in ("__name__", "__doc__", "__file__", "__version__"):
        assert name in names, f"{name} missing from dir(lingtai)"
    for name in lingtai.__all__:
        assert name in names, f"{name} missing from dir(lingtai)"


def test_lazy_resolution_caches_in_module_globals():
    """After first access, the resolved object is cached in ``lingtai.__dict__``."""
    import sys

    import lingtai

    # Ensure a fresh resolution path in case a previous test already touched it.
    lingtai.__dict__.pop("Agent", None)
    assert "Agent" not in lingtai.__dict__
    _ = lingtai.Agent
    assert "Agent" in lingtai.__dict__
    assert lingtai.__dict__["Agent"] is sys.modules["lingtai.agent"].Agent
