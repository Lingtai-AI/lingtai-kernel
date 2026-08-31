---
name: agent-guardian-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - BEHAVIORS.md
  - src/lingtai/kernel/agent_guardian/CONTRACT.md
  - src/lingtai/kernel/agent_guardian/ANATOMY.md
  - src/lingtai/kernel/agent_guardian/MANUAL.md
  - src/lingtai/adapters/windows/_win32.py
  - tests/test_agent_guardian.py
  - tests/test_karma.py
maintenance: |
  Keep this scenario matrix synchronized with the guardian Contract and
  Anatomy. Preserve hermetic execution and the explicit no-actuator evidence.
---
# Agent Guardian Behavior Tests

## Behavior G001 — guardian reports evidence-bound shadow plans without action

- **id**: G001
- **title**: guardian reports evidence-bound shadow plans without action
- **guards**: `agent-guardian` § Behavior ([CONTRACT.md](CONTRACT.md#behavior))
- **runner**: a coding agent with a POSIX shell and Git in a repository worktree
- **prerequisites**: run from any worktree of `lingtai-kernel`; the repository
  virtualenv is `<common-repository-root>/.venv`; tests use only pytest-owned
  temporary directories and fake observation Ports; no live LingTai agent
- **estimate**: 2 minutes

### Steps
1. Resolve the current worktree and repository virtualenv, then run the exact
   guardian and System ordering/refusal tests with candidate source and no
   bytecode or pytest cache:
   ```bash
   REPO_ROOT="$(git rev-parse --show-toplevel)"
   REPO_BASE="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
   if test -x "$REPO_BASE/.venv/bin/python"; then
     VENV_PYTHON="$REPO_BASE/.venv/bin/python"
   else
     VENV_PYTHON="$REPO_BASE/.venv/Scripts/python.exe"
   fi
   test -x "$VENV_PYTHON"
   cd "$REPO_ROOT"
   PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$VENV_PYTHON" -m pytest -p no:cacheprovider -q \
     tests/test_agent_guardian.py \
     tests/test_karma.py
   ```
2. Verify the exact implementation symbols exist once at their owned paths:
   ```bash
   REPO_ROOT="$(git rev-parse --show-toplevel)"
   REPO_BASE="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
   if test -x "$REPO_BASE/.venv/bin/python"; then
     VENV_PYTHON="$REPO_BASE/.venv/bin/python"
   else
     VENV_PYTHON="$REPO_BASE/.venv/Scripts/python.exe"
   fi
   test -x "$VENV_PYTHON"
   cd "$REPO_ROOT"
   PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 "$VENV_PYTHON" - <<'PY'
   from pathlib import Path

   checks = {
       "src/lingtai/kernel/agent_guardian/__init__.py": (
           "def validate_guardian_payload_semantics(",
           "def validate_lifecycle_event(",
           "def reduce_lifecycle_events(",
           "def validate_presence_sample(",
           "def evaluate_presence(",
       ),
       "src/lingtai/adapters/agent_guardian.py": (
           "class FilesystemLifecycleLedgerAdapter:",
           "class LocalAgentGuardianHostAdapter:",
           "def _pid_existence(",
           "def _linux_observation(",
           "def _darwin_observation(",
       ),
       "src/lingtai/adapters/windows/_win32.py": (
           "def process_liveness(",
       ),
       "src/lingtai/cli_guardian.py": ("def run_guardian_cli(",),
       "src/lingtai/tools/system/karma.py": (
           "def _suspend(",
           "def _cpr(",
       ),
       "src/lingtai/agent.py": ("def _cpr_agent(",),
   }
   for path_text, symbols in checks.items():
       text = Path(path_text).read_text(encoding="utf-8")
       for symbol in symbols:
           assert text.count(symbol) == 1, (path_text, symbol)
   print("guardian-symbol-navigation: pass")
   PY
   ```
3. Confirm the table-driven evidence covers: exact running→`alive/none`, a
   platform `T`/SIGSTOP observation→`frozen/would_sigcont`, confirmed
   absent+free→`dead/would_launch`,
   explicit intent→`hold_explicit_suspend`, PID/start/workdir/command or lease
   contradiction→`unknown/observe_only`, every supported Core sample
   round-tripping through the strict event validator, ordinary-boot refusal
   until matching CPR, impossible physical suspend→boot refusal, both real
   boot/suspend lock orders, descriptor-growth and replacement bounds,
   last-readable-record refusal/idempotent retry, daily checkpoint boundaries,
   corrupt/deep/large-integer/invalid-Unicode ledger failure, and second
   guardian refusal.
4. Use the passing assertions in `tests/test_agent_guardian.py` as the exact
   inspection: the macOS libproc-miss probe calls `os.kill(pid, 0)` with literal
   signal zero, `ESRCH` alone maps to absent, Linux/macOS changed or missing
   second incarnation tokens never map to exact evidence, missing/inaccessible
   Linux procfs stays unknown, Windows open/query failure stays unknown unless
   the strict tri-state helper mechanically proves absence, Windows missing
   exact command/executable/stopped evidence stays unknown, and the CLI
   actuation APIs remain patched to fail if called.

### Expected evidence
- [ ] The pytest command exits 0 with every selected test passing; only the two
  explicitly POSIX-permission cases may be skipped on Native Windows.
- [ ] The symbol check prints exactly `guardian-symbol-navigation: pass` and
  exits 0.
- [ ] Tests cover physical duplicate-row count preflight, strict boot/verdict
  semantics, bound-address/workdir refusal, same-descriptor byte preflight,
  parent-link fsync under the shared lock plus failed-first-sync retry,
  file/child-directory fsync order, two-token incarnation stability, strict Core
  sample closure, and typed setup/lease errors in addition to stale two-sample
  and ledger-first failures.
- [ ] `--once` emits one mechanical JSON object and audits one verdict; corrupt
  or ownership-ambiguous truth exits nonzero.
- [ ] Either a legacy `.suspend` marker or early active intent prevents
  construction and preserves the marker; a legacy marker visible at the
  post-construction recheck and a durable-intent late race both stop the
  constructed Agent before start with no boot row, while the opposite lock order
  records boot then suspend without corruption.
- [ ] Guardian setup and loop sampling bound `.agent.json` and Agent Record to
  1 MiB from one binary descriptor; oversized/deep/invalid/allocation-failing
  inputs become conservative typed evidence rather than raw failures.
- [ ] No test observes a delivered/nonzero signal, process launch, Agent/provider/
  MCP construction, service install, or guardian mutation of heartbeat,
  manifest, signal, or agent-lease state. The macOS signal-zero existence probe
  is read-only and confined to its libproc-miss fallback.

### Pass / Fail
Pass only when both commands exit 0 and every evidence item holds. Any launch or
delivered/nonzero-signal path, single-sample stale action plan, silent
corruption, address drift, post-limit append, or duplicate guardian win is a
failure.
