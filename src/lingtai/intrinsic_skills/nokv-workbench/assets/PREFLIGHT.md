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

Tool-surface note: the 14-tool surface documented in SKILL.md (including
workbench_append / workbench_edit / workbench_search / workbench_aggregate /
workbench_catalog and conditional reads) requires a NoKV build that ships the
specialized workbench MCP. Older 9-tool NoKV servers still work with this
skill; the extra tools are simply absent from tools/list and the SKILL
sections about them do not apply.
