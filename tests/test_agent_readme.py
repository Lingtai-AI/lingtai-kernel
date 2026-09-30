"""Contract tests for the agent-directory README (see agent_readme/CONTRACT.md)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lingtai.agent import Agent
from lingtai.kernel.agent_readme import ensure_agent_readme, render_readme, template_version
from lingtai.kernel.workdir import WorkdirLayout, workdir_layout
from tests._service_helpers import make_mock_llm_service as make_mock_service


@pytest.fixture()
def agent_root(tmp_path: Path) -> Path:
    (tmp_path / "system").mkdir()
    (tmp_path / "knowledge").mkdir()
    return tmp_path


def test_rendered_template_points_at_substrate(agent_root: Path) -> None:
    """CONTRACT #1: written README exists and carries the substrate relative link."""
    written = ensure_agent_readme(agent_root)
    assert written is not None
    assert written == agent_root / "README.md"
    body = written.read_text(encoding="utf-8")
    assert "system/substrate.md" in body
    # It is a navigation entry point, not a pure listing: rows carry explanation.
    assert "Where to look" in body
    assert "Open when" in body


def test_missing_file_is_written_and_version_match_is_noop(agent_root: Path) -> None:
    """CONTRACT #2: missing → write; stale version → rewrite; match → no-op."""
    layout = workdir_layout(agent_root)
    assert not layout.readme.exists()

    # Missing: write.
    written = ensure_agent_readme(agent_root)
    assert written == layout.readme
    first = layout.readme.read_text(encoding="utf-8")
    assert template_version(first) == template_version(render_readme())

    # Version match: strict no-op (returns None, mtime/content unchanged).
    assert ensure_agent_readme(agent_root) is None
    assert layout.readme.read_text(encoding="utf-8") == first

    # Stale: rewrite with the packaged template.
    layout.readme.write_text(
        textwrap.dedent(
            """\
            ---
            template_version: agent-readme/v0
            ---
            # Old README
            """
        ),
        encoding="utf-8",
    )
    written = ensure_agent_readme(agent_root)
    assert written == layout.readme
    assert layout.readme.read_text(encoding="utf-8") == first


def test_readme_has_no_identity_or_live_values(agent_root: Path) -> None:
    """CONTRACT #3: README carries neither the agent name nor live/dynamic fields."""
    ensure_agent_readme(agent_root)
    body = (agent_root / "README.md").read_text(encoding="utf-8")
    # No placeholder-looking identity slot.
    assert "{{agent_name}}" not in body
    assert "<agent_name>" not in body
    # No live/heartbeat/provider-style dynamic fields.
    for token in ("heartbeat", "provider", "context_limit", "molt_count"):
        assert token not in body


def test_unversioned_existing_readme_is_taken_over_with_backup(agent_root: Path) -> None:
    """CONTRACT/P1-1: a user-authored unversioned README is preserved as .bak before takeover."""
    user_readme = "# My notes\n\nThis is my own README, not kernel-owned.\n"
    (agent_root / "README.md").write_text(user_readme, encoding="utf-8")
    events: list[str] = []

    def _log(name: str, **kwargs: object) -> None:
        events.append(f"{name}:{kwargs}")

    written = ensure_agent_readme(agent_root, _log=_log)
    assert written == agent_root / "README.md"
    # Original content preserved, not silently clobbered.
    assert (agent_root / "README.md.bak").read_text(encoding="utf-8") == user_readme
    # Takeover logged.
    assert any(e.startswith("agent_readme_takeover") for e in events)
    # Navigation entry now in place.
    assert "system/substrate.md" in written.read_text(encoding="utf-8")


def test_non_utf8_existing_readme_still_rewrites(agent_root: Path) -> None:
    """P2-1: a non-UTF-8 existing README is treated as unreadable and rewritten."""
    target = agent_root / "README.md"
    target.write_bytes(b"\xff\xfe\x00binary\n")
    written = ensure_agent_readme(agent_root)
    assert written == target
    body = target.read_text(encoding="utf-8")
    assert body.startswith("---\ntemplate_version:")
    assert "system/substrate.md" in body


def test_packaged_template_without_version_head_raises(agent_root: Path) -> None:
    """P2-5: a broken packaged template (no version head) fails loudly, not silently."""
    with pytest.raises(ValueError):
        ensure_agent_readme(agent_root, template="# Broken\n")


def test_baseagent_construction_writes_readme(tmp_path: Path) -> None:
    """P1-2: the construction mount point actually lands README.md in a real agent."""
    svc = make_mock_service()
    working_dir = tmp_path / "test"
    # Construction itself runs ensure_agent_readme (fail-soft mount).
    Agent(service=svc, agent_name="test", working_dir=working_dir)
    readme = working_dir / "README.md"
    assert readme.is_file()
    body = readme.read_text(encoding="utf-8")
    assert body.startswith("---\ntemplate_version:")
    assert "system/substrate.md" in body


def test_workdir_layout_names_readme(agent_root: Path) -> None:
    """WorkdirLayout names the README path."""
    assert WorkdirLayout(agent_root).readme == agent_root / "README.md"


def test_substrate_related_files_links_agent_readme() -> None:
    """CONTRACT #4: substrate frontmatter related_files links agent_readme docs."""
    substrate = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "lingtai"
        / "prompts"
        / "substrate"
        / "substrate.md"
    )
    assert substrate.is_file()
    head = substrate.read_text(encoding="utf-8")[:4096]
    assert "src/lingtai/kernel/agent_readme/CONTRACT.md" in head
    assert "src/lingtai/kernel/agent_readme/README.md.tpl" in head
