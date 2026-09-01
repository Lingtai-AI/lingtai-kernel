"""Executable packaging and platform truth for Telegram Agent switching V1."""
from __future__ import annotations

import importlib.resources
import tomllib
from pathlib import Path

import pytest

from lingtai.adapters import channel_reply_state_lock as selector
from lingtai.adapters.posix.channel_reply_state_lock import (
    PosixChannelReplyStateLockAdapter,
)


def _telegram_resource(relative: str) -> str:
    root = importlib.resources.files("lingtai.mcp_servers.telegram")
    return root.joinpath(*relative.split("/")).read_text(encoding="utf-8")


def test_packaged_switching_docs_state_exact_platform_start_ttl_and_failure_truth():
    skill = _telegram_resource("SKILL.md")
    contract = _telegram_resource("reference/agent-switching/CONTRACT.md")
    anatomy = _telegram_resource("reference/agent-switching/ANATOMY.md")
    combined = "\n".join((skill, contract, anatomy))

    assert 'os.name == "posix"' in skill
    assert 'sys.platform == "darwin"' in skill
    assert "Linux, Windows, FreeBSD, and every other host are unsupported" in skill
    assert "explicitly enabling it makes eager manager construction fail closed" in skill
    assert "the MCP remains error-only" in skill
    assert skill.index("**Platform warning:**") < skill.index('"agent_switching": {"enabled": true}')

    assert "/start <args>" in combined
    assert "normal owner-local setup path" in contract
    assert "switching creates no response, authority, target" in contract
    assert "expire exactly two hours after" in contract
    assert "grant's `created_at` is immutable owner/router issuance time" in contract
    assert "request `created_at` as the current UTC submission time" in contract
    assert "not an exact owner authority value" in contract
    assert "Core still rejects stale or future" in contract
    assert "Grant `created_at` is immutable owner/router" in skill
    assert "current UTC submission" in skill
    assert "stale/future request checks still" in skill
    assert "Reset or reselection controls future routing only" in contract
    assert "delivery is indeterminate" in contract
    assert "does not retry, remint" in contract
    assert "may remain silent" in contract
    assert "every five minutes" in contract
    assert "budgets 128" in contract
    assert "permanent" in contract
    assert "selection-unavailable" in anatomy
    assert ".telegram-agent-switching/router-decisions" in anatomy
    assert "V1 never routes an edited message" in skill
    assert "one generic local unsupported-content error" in skill
    assert "ordinary admin edit behavior" in skill
    assert "opaque digest of the exact account/chat/user/message identity" in skill
    assert "state/original-ownership/<digest>.json" in skill
    assert "Before creating any target-visible capsule" in skill
    assert "malformed, unreadable, conflicting" in skill
    assert "edit-rejections/<digest>.json" in skill
    assert "local-reply idempotent across restart" in skill
    assert "four owner classes rotate fairly" in skill

    assert contract.startswith(
        "---\nname: telegram-agent-switching-v1\ncontract_version: 1\n"
        "root_contract: CONTRACT.md\nrelated_files:"
    )
    contract_sections = (
        "## Purpose",
        "## Behavior",
        "## Port",
        "## Adapters",
        "## Contract rules",
        "## Contract tests",
        "## Maintenance",
    )
    assert [contract.index(section) for section in contract_sections] == sorted(
        contract.index(section) for section in contract_sections
    )
    assert "sticky source-chat ownership" in contract
    assert "genuinely ordinary unselected, unmarked, non-directive edit" in contract
    assert "fallback records the edit and publishes it with `wake=false`" in contract
    assert contract.index("**Raw-first ingress order.**") < contract.index(
        "**Edited-message ownership.**"
    ) < contract.index("**At-most-once edit rejection.**")
    assert "strict body-free v1" in contract
    assert "Original ownership must be durable before any target-visible capsule" in contract
    assert "cleanup schema v4" in contract
    assert "`menus`, `dead`," in contract
    assert "`original_ownership`, and `edit_rejections`" in contract
    assert "one total owner-state budget" in contract
    assert "budgets 128 total owner-state inspections" in contract
    assert "Hidden marker/temp names, callback tokens, selector text" in contract
    assert "edit anchors" in contract
    assert "stale/corrupt/malicious local state" in contract

    anatomy_sections = (
        "## Components",
        "## Connections",
        "## Composition",
        "## State",
        "## Notes",
    )
    assert [anatomy.index(section) for section in anatomy_sections] == sorted(
        anatomy.index(section) for section in anatomy_sections
    )
    assert "agent_switching.py:517-1409" in anatomy
    assert "agent_switching.py:1504-2784" in anatomy
    assert "manager.py:1394-1529" in anatomy
    assert "strict schema v4" in anatomy
    assert "The total switching-state cleanup budget is 128" in anatomy


def test_build_metadata_packages_switching_reference_tree():
    project_root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    telegram_data = config["tool"]["setuptools"]["package-data"][
        "lingtai.mcp_servers.telegram"
    ]
    assert "SKILL.md" in telegram_data
    assert "reference/**/*" in telegram_data
    assert (project_root / "MANIFEST.in").read_text(encoding="utf-8").count(
        "src/lingtai/mcp_servers/telegram/reference"
    ) >= 1


@pytest.mark.parametrize(
    "identity",
    [
        ("posix", "linux"),
        ("nt", "win32"),
        ("posix", "freebsd14"),
        ("java", "darwin"),
    ],
)
def test_automatic_lock_selection_is_exactly_darwin_posix_and_other_hosts_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    identity: tuple[str, str],
):
    monkeypatch.setattr(selector, "_platform_identity", lambda: identity)
    with pytest.raises(selector.UnsupportedChannelReplyPlatform, match="macOS only"):
        selector.select_channel_reply_state_lock()


def test_automatic_lock_selection_accepts_exact_darwin_posix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(selector, "_platform_identity", lambda: ("posix", "darwin"))
    assert isinstance(selector.select_channel_reply_state_lock(), PosixChannelReplyStateLockAdapter)


def test_canonical_switching_docs_only_cite_valid_repository_line_ranges():
    project_root = Path(__file__).resolve().parents[1]
    canonical_docs = (
        Path("src/lingtai/mcp_servers/telegram/reference/agent-switching/CONTRACT.md"),
        Path("src/lingtai/mcp_servers/telegram/reference/agent-switching/ANATOMY.md"),
    )
    references: list[tuple[Path, int, str, int, int]] = []

    for document_relative in canonical_docs:
        document_path = project_root / document_relative
        for document_line, text in enumerate(
            document_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for index, fragment in enumerate(text.split("`")):
                if index % 2 == 0 or not fragment.startswith(("src/", "tests/")):
                    continue
                cited_path, separator, line_range = fragment.rpartition(":")
                start_text, dash, end_text = line_range.partition("-")
                if not (
                    separator
                    and dash
                    and start_text.isdecimal()
                    and end_text.isdecimal()
                ):
                    continue
                references.append(
                    (
                        document_relative,
                        document_line,
                        cited_path,
                        int(start_text),
                        int(end_text),
                    )
                )

    assert references, "canonical switching docs contain no explicit repository line ranges"

    invalid: list[str] = []
    for document, document_line, cited_path, start, end in references:
        source_path = project_root / cited_path
        citation = f"{document}:{document_line} -> `{cited_path}:{start}-{end}`"
        if not source_path.exists():
            invalid.append(f"{citation}; actual_lines=unavailable; error=missing")
            continue
        if source_path.is_symlink() or not source_path.is_file():
            invalid.append(f"{citation}; actual_lines=unavailable; error=not-regular-file")
            continue

        actual_line_count = len(source_path.read_text(encoding="utf-8").splitlines())
        if not 1 <= start <= end <= actual_line_count:
            invalid.append(
                f"{citation}; actual_lines={actual_line_count}; "
                "required=1<=start<=end<=actual_lines"
            )

    assert not invalid, "invalid canonical repository line ranges:\n" + "\n".join(invalid)
