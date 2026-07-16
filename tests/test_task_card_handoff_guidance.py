"""Focused checks for Task Card progress guidance in async handoffs."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDANCE_FRAGMENTS = [
    "Task Card",
    "telegram(action='manual')",
    "Programmable Task Card",
]
DOCS = [
    ROOT / "src/lingtai/tools/bash/CONTRACT.md",
    ROOT / "src/lingtai/tools/daemon/CONTRACT.md",
    ROOT / "src/lingtai/tools/bash/manual/SKILL.md",
    ROOT / "src/lingtai/tools/daemon/manual/SKILL.md",
]


def test_contracts_and_manuals_carry_task_card_handoff_guidance():
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        for fragment in GUIDANCE_FRAGMENTS:
            assert fragment in text, f"{fragment!r} missing from {path}"
