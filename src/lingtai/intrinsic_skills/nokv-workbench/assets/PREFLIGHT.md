---
related_files:
- src/lingtai/intrinsic_skills/nokv-workbench/SKILL.md
maintenance: |
  Developer-facing TUI deployment preflight pointed to by nokv-workbench/SKILL.md; update it whenever the workbench MCP tool surface (9-tool vs 16-tool, checkpoint-lifecycle fields) or the runtime-version compatibility check changes.
---

# TUI runtime preflight (developer-facing)

This is deployment guidance for developers installing a workbench-enabled
LingTai branch into the TUI runtime. It is not agent-facing instruction and
is deliberately kept out of SKILL.md.

Check the runtime package version first:

```bash
~/.lingtai-tui/runtime/venv/bin/python - <<'PY'
import importlib.metadata as md
print(md.version("lingtai"))
PY
```

Do not install a source branch that is older than the runtime package already
used by TUI. Rebase or cherry-pick the workbench skill onto the matching or
newer upstream LingTai release, build/install that branch, then verify that
the runtime can see the skill:

```bash
~/.lingtai-tui/runtime/venv/bin/python - <<'PY'
from pathlib import Path
import lingtai.intrinsic_skills as skills
root = Path(skills.__file__).parent
print((root / "nokv-workbench" / "SKILL.md").exists())
PY
```

Tool-surface note: parts of the SKILL.md surface depend on the NoKV build. An
older server still works with this skill — the newer tools and parameters are
simply absent from tools/list, and the SKILL sections about them do not apply.

- **17-tool workbench MCP** (workbench_append / workbench_edit /
  workbench_search / workbench_aggregate / workbench_catalog, conditional
  reads, and workbench_restore) requires a build shipping the specialized
  workbench MCP; older 9-tool NoKV servers lack it.
- **Checkpoint lifecycle** (workbench_snapshot_renew, workbench_snapshot_list,
  the workbench_snapshot `name`/`ttl_days` parameters and its
  `lease_expires_at`/`expiry_warning` output, and the `at_snapshot` parameter
  on workbench_read / workbench_list / workbench_stat) requires a build
  shipping Phase 1 snapshot leasing. Without it, snapshots fall back to the
  legacy 1-hour lease with no renewal path.
- **Restore-to-fork** (workbench_restore) requires a build with strict
  terminal expiry and root-bound snapshot operations. Treat a missing
  `workbench_restore` tool or schema field as a failed preflight — do not run
  the restore workflow against an older surface and silently downgrade
  checkpoint guarantees. Verify that `workbench_restore` requires `id`,
  `at_snapshot`, and `destination_id`. Expired checkpoints must return a
  structured error and must not be described as renewable within a grace
  period.

Run the checked-in NoKV/LingTai live acceptance harness before deploying a new
pair of builds that includes workbench_restore. It must exercise the real
RustFS service, NoKV metadata server, workbench MCP subprocess, and LingTai
MCPClient; unit-only schema checks are not a deployment acceptance substitute.
