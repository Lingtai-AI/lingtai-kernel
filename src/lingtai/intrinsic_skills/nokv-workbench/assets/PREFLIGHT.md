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

Tool-surface note: the 16-tool surface documented in SKILL.md (including
workbench_append / workbench_edit / workbench_search / workbench_aggregate /
workbench_catalog and conditional reads) requires a NoKV build that ships the
specialized workbench MCP. Older 9-tool NoKV servers still work with this
skill; the extra tools are simply absent from tools/list and the SKILL
sections about them do not apply.

The checkpoint-lifecycle surface — workbench_snapshot_renew and
workbench_snapshot_list, the workbench_snapshot `name`/`ttl_days` parameters
and its `lease_expires_at`/`expiry_warning` output, and the `at_snapshot`
parameter on workbench_read / workbench_list / workbench_stat — needs a NoKV
build that ships Phase 1 snapshot leasing. Against an older build these tools
and parameters are absent, and the "Checkpoints and leases" SKILL section does
not apply; snapshots there fall back to the legacy 1-hour lease with no
renewal path.
