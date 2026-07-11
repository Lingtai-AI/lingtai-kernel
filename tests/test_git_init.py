"""Tests for git-controlled agent working directory.

Note: ``init_git()`` only fires when snapshots are enabled
(``AgentConfig.snapshot_interval`` is non-None). These tests construct an
agent with a snapshot interval set so the git path is exercised.
"""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

import subprocess
from unittest.mock import MagicMock

import pytest

from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.config import AgentConfig
from tests._service_helpers import make_gemini_mock_service as make_mock_service




def _git_enabled_config() -> AgentConfig:
    """Config with snapshots on so init_git() is exercised."""
    return AgentConfig(snapshot_interval=60.0)


def test_start_creates_git_repo(tmp_path):
    """agent.start() should git init the working directory."""
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test",
                       working_dir=tmp_path / "test",
                       config=_git_enabled_config())
    agent.start()
    try:
        git_dir = agent.working_dir / ".git"
        assert git_dir.is_dir(), "Working dir should have .git after start()"
    finally:
        agent.stop()


def test_start_creates_gitignore(tmp_path):
    """agent.start() should create a .gitignore protecting local secrets."""
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test",
                       working_dir=tmp_path / "test",
                       config=_git_enabled_config())
    agent.start()
    try:
        gitignore = agent.working_dir / ".gitignore"
        assert gitignore.is_file(), ".gitignore should exist after start()"
        expected = (
            "# Secrets — MCP addon credentials (bot tokens, API keys)\n"
            ".secrets/\n"
            "\n"
            "# Transient lifecycle signal files\n"
            ".sleep\n"
            ".suspend\n"
            ".agent.heartbeat\n"
            ".timemachine.pid\n"
        )
        assert gitignore.read_text() == expected
    finally:
        agent.stop()


def test_start_creates_system_dir(tmp_path):
    """agent.start() should create system/ directory with covenant.md and pad.md."""
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test",
                       working_dir=tmp_path / "test",
                       config=_git_enabled_config())
    agent.start()
    try:
        system_dir = agent.working_dir / "system"
        assert system_dir.is_dir()
        assert (system_dir / "covenant.md").is_file()
        assert (system_dir / "pad.md").is_file()
    finally:
        agent.stop()


def test_start_makes_initial_commit(tmp_path):
    """agent.start() should make an initial git commit."""
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test",
                       working_dir=tmp_path / "test",
                       config=_git_enabled_config())
    agent.start()
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=agent.working_dir,
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "init" in result.stdout.lower()
    finally:
        agent.stop()


def test_start_skips_git_init_if_git_exists(tmp_path):
    """If .git already exists, start() should not run git init again.

    This is checked by counting the number of ``init: agent working
    directory`` commits — there should always be exactly one even after
    multiple starts. (Periodic Time Machine snapshots may add other
    commits; those are unrelated to the init-git skip semantics.)
    """
    agent = BaseAgent(intrinsics=_TEST_INTRINSICS, service=make_mock_service(), agent_name="test",
                       working_dir=tmp_path / "test",
                       config=_git_enabled_config())
    agent.start()
    agent.stop()
    agent.start()
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--grep=init: agent working directory"],
            cwd=agent.working_dir,
            capture_output=True, text=True,
        )
        init_commits = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(init_commits) == 1, (
            f"init_git should run exactly once; got {len(init_commits)} init commits:\n"
            f"{result.stdout}"
        )
    finally:
        agent.stop()
