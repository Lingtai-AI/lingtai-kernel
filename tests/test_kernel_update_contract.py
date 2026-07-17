"""Release-candidate and route exclusivity regressions for kernel updates."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SUBSTRATE = ROOT / "src/lingtai/prompts/substrate/substrate.md"
_CHANNEL_MODEL = ROOT / "src/lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md"
_RUNTIME_UPDATE = ROOT / "src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no YAML frontmatter"
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        parsed = re.match(r"^(release_version|release_tag):\s*[\"']?([^\"']+?)[\"']?\s*$", line)
        if parsed:
            values[parsed.group(1)] = parsed.group(2)
    return values


def test_migration_frontmatter_matches_authoritative_package_version_and_tag():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = package["project"]["version"]
    metadata = _frontmatter(ROOT / "migration/migration.md")
    assert metadata["release_version"] == version
    assert metadata["release_tag"] == f"v{version}"


def test_kernel_update_guidance_uses_only_the_installer_route():
    for path in (_SUBSTRATE, _CHANNEL_MODEL, _RUNTIME_UPDATE):
        text = path.read_text(encoding="utf-8")
        assert "https://lingtai.ai/install.sh" in text
        assert "--help" in text
        assert "update --help" in text
        assert "explicit human/config-owner" in text
        assert "https://lingtai.ai/skill.md" not in text


def test_repository_kernel_version_guidance_cannot_restore_obsolete_routes():
    """Scan every source Markdown guidance surface; historical exemptions must
    be added here with a path and a written reason rather than silently widening
    the banned-route pattern.
    """
    historical_exemptions: dict[str, str] = {}
    banned = (
        "https://lingtai.ai/skill.md",
        "normal install/update commands remain TUI-managed",
        "normal installation/update commands remain TUI-managed",
    )
    for path in ROOT.rglob("*.md"):
        if ".git" in path.parts or "scratch" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative in historical_exemptions:
            assert historical_exemptions[relative], f"{relative} needs an exemption rationale"
            continue
        if re.search(r"kernel[_ -]?version|kernel version", text, re.IGNORECASE):
            for phrase in banned:
                assert phrase not in text, f"obsolete kernel-version route in {relative}: {phrase}"
            assert not re.search(r"separate\s+TUI\s+(?:update|updater)", text, re.IGNORECASE), relative


def test_update_guidance_keeps_source_drift_local_only():
    substrate = _SUBSTRATE.read_text(encoding="utf-8")
    channel = _CHANNEL_MODEL.read_text(encoding="utf-8")
    runtime = _RUNTIME_UPDATE.read_text(encoding="utf-8")
    for text in (substrate, channel, runtime):
        assert "source_drift" in text
        assert "local" in text
        assert "release-migration" in text
