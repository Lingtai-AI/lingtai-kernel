"""Shared loader for manuals installed in an agent's intrinsic skill catalog."""
from __future__ import annotations


def load_installed_manual(agent, skill_name: str) -> dict:
    """Return one installed intrinsic manual without mutating agent state."""
    manual_path = (
        agent._working_dir
        / ".library"
        / "intrinsic"
        / "capabilities"
        / skill_name
        / "SKILL.md"
    )
    if not manual_path.is_file():
        return {
            "status": "degraded",
            "manual": "",
            "manual_path": str(manual_path),
            "error": (
                f"{skill_name} manual missing — initializer may have failed or "
                "capability not installed correctly"
            ),
        }
    return {
        "status": "ok",
        "manual": manual_path.read_text(encoding="utf-8"),
        "manual_path": str(manual_path),
    }
