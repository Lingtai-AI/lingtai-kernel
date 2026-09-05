"""Guard the shared documentation recipe, not a new runtime cleanup API."""
from pathlib import Path
import json
import re
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md"
MANUALS = (
    "src/lingtai/tools/avatar/manual/SKILL.md",
    "src/lingtai/tools/daemon/manual/reference/cleanup/SKILL.md",
    "src/lingtai/tools/email/manual/SKILL.md",
    "src/lingtai/tools/knowledge/manual/SKILL.md",
    "src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md",
    "src/lingtai/tools/skills/manual/SKILL.md",
    "src/lingtai/tools/bash/manual/reference/debugging-cleanup/SKILL.md",
)


@pytest.mark.parametrize("relative", MANUALS)
def test_footprint_manual_routes_to_one_real_recipe(relative):
    manual = ROOT / relative
    text = manual.read_text(encoding="utf-8")
    route = re.search(r"\[shared inspection recipe\]\(([^)]+)\)", text)
    assert route
    assert (manual.parent / route[1].split("#")[0]).resolve() == OWNER
    assert "rows, total = footprint_check(items," in text
    assert "def size(p)" not in text
    assert 'log.open("a"' not in text


def test_shared_recipe_inspects_without_writing_and_logs_only_explicitly(tmp_path):
    if shutil.which("rg") is None:
        pytest.skip("documentation recipe explicitly requires installed rg")
    code = re.search(r"```python\n(.*?)\n```", OWNER.read_text(encoding="utf-8"), re.S)[1]
    namespace = {}
    exec(compile(code, str(OWNER), "exec"), namespace)
    root = tmp_path / "selected"
    root.mkdir()
    (root / "plain").write_bytes(b"abc")
    (root / ".hidden").write_bytes(b"12")
    before = sorted((p.name, p.read_bytes()) for p in root.iterdir())
    rows, total = namespace["footprint_check"]([root], tool="test")
    assert rows == [(root, 5)] and total == 5
    assert not (tmp_path / "logs").exists()
    assert before == sorted((p.name, p.read_bytes()) for p in root.iterdir())
    namespace["append_cleanup_record"](
        tmp_path, tool="test", dry_run=True, candidates=len(rows),
        bytes_total=total, human_approved=False, summary="selected fixture",
    )
    record = json.loads((tmp_path / "logs/cleanup.jsonl").read_text())
    assert record["dry_run"] is True and record["bytes"] == 5
    assert record["human_approved"] is False
    assert before == sorted((p.name, p.read_bytes()) for p in root.iterdir())
