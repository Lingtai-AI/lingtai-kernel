---
name: kernel-behavior-tests
behavior_version: 1
labt_version: 2
contract:
  - ../CONTRACT.md
  - ../tools/notification/CONTRACT.md
  - ../tools/context/CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/nudge/__init__.py
  - src/lingtai/kernel/nudge/kernel_version.py
  - src/lingtai/kernel/nudge/source_drift.py
  - src/lingtai/kernel/nudge/prompts.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/base_agent/messaging.py
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/kernel/tool_executor.py
  - src/lingtai/kernel/tool_result_artifacts.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/snapshot/__init__.py
  - src/lingtai/adapters/posix/git_cli.py
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/CONTRACT.md
  - tests/test_kernel_version_nudge.py
  - tests/test_eigen.py
  - tests/test_large_result_no_notification.py
  - tests/test_tool_meta_comment_overflow.py
  - tests/test_source_drift.py
maintenance: |
  Written by the kernel nudge/notification CONVERT_BEHAVIOR migration (2026-08)
  and trimmed per the fable PR review: K001/K002/K005/K006/K007 are now
  observable-outcome LABTs (real artifacts written by the real runtime into a
  scratch working dir: `.notification/nudge.json`, `.nudge_state.json`,
  `.notification/system.json`, `ToolResultBlock` metadata, real git revisions).
  No mocks, no private monkeypatching, no internal-call-shape assertions. The
  pytest files in `supersedes` REMAIN the bottom-level assertions for the parts
  that are not agent-observable without mocks: the strict "remote is never
  queried" proof (its fake `_fetch_latest_version` raises), release-mirror
  agreement/transport, malformed-remote-version non-promotion
  (`kv._is_newer`), source-digest drift via a synthetic package, and private
  helper units. Keep the pytest when you touch those paths; change this file
  only when agent-observable behavior changes.
  This file guards three contracts: `src/lingtai/CONTRACT.md` (Contract rules
  rule 6, Nudge transport/policy) for K001/K002/K007; `notification-tool`
  (large-result routing, no `large_tool_result` source, overflow guidance) for
  K005/K006; `context-contract` (eigen retirement, molt summary guard) for
  K003/K004. The frontmatter `contract:` list names all three (kernel/ has no
  CONTRACT.md of its own). Keep the guards names in sync with each target's
  frontmatter `name:` and with `src/lingtai/kernel/ANATOMY.md` entries for
  nudge, notifications, and meta_block. When any of those change
  agent-observable behavior, update the matching LABT here in the same change.
---
# Kernel Behavior Tests — nudge / notification family

LABT v2. These are self-contained agent-executable behavioral tests for the
kernel nudge/notification family: the kernel-version nudge (`kernel_version`,
including its source-drift/dev-runtime detection and fail-safe diagnostic
direction), the source-drift nudge (`source_drift`), the retired `eigen`
identity surface (K003), the `context.molt` summary guard (K004), the promise
that a large tool result never becomes a notification (it is ranked in
`_meta.agent_meta.current_tool_result_chars` instead), and the per-result
`_meta.tool_meta.comment.overflow` hint. Every LABT below observes real
artifacts written by the real runtime into a scratch working dir
(`.notification/*.json`, `.nudge_state.json`, `ToolResultBlock` metadata, real
git revisions) — no mocks, no private monkeypatching, no internal-call-shape
assertions. Low-level mechanics that are not agent-observable without mocks
(remote-mirror agreement, malformed-version promotion, digest-drift capture)
stay in the pytest files named in `supersedes`; they remain the bottom-level
assertions. Replace `<repo-root>` with the checkout path and `<scratch>` with
any empty working directory the executor owns. Harness scripts set the package
path themselves and write only into their own temp dirs, so they never touch
the executing agent's `.notification/` or session state.

## Behavior K001 — kernel version nudge emits the local refresh finding on the `release_version` channel

- **id**: K001
- **title**: an installed distribution newer than the running kernel produces a
  `kernel_version` nudge entry (`nudge_channel: release_version`) with a
  safe-refresh suggested action, without any remote probe
- **guards**: `src/lingtai/CONTRACT.md` § Contract rules rule 6 — every declared
  Nudge kind uses the ordinary `.notification/nudge.json` transport and the
  shared global Nudge policy
  ([CONTRACT.md](../CONTRACT.md#contract-rules))
- **supersedes**: `tests/test_kernel_version_nudge.py::test_installed_runtime_refresh_nudge_does_not_hit_remote`
- **runner**: any LingTai agent with `shell` and `file` tools at a checkout of
  this repository (Python 3.10+)
- **prerequisites**: a checkout of this repo at `<repo-root>`; an empty scratch
  dir `<scratch>`; python on PATH
- **estimate**: 2 min

### Steps
1. Write `<scratch>/k001.py` with the following content (self-contained
   harness; `REPO` is the only value to substitute). It stages a real
   "stale install" — a wrapper module at v0.14.1 plus installed distribution
   metadata v0.14.2 — on disk and boots the interpreter against it, so the
   real `_runtime_info` observes running 0.14.1 / installed 0.14.2 without any
   mocking:

```python
import sys, os, json, pathlib, tempfile, importlib.util
REPO = r"<repo-root>"
sys.path.insert(0, os.path.join(REPO, "src"))
os.chdir(REPO)

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter

# Stage a real "stale install": wrapper at v0.14.1, installed dist v0.14.2.
root = pathlib.Path(tempfile.mkdtemp(prefix="k001"))
site = root / "site"
wrapper_dir = site / "lingtai"
wrapper_dir.mkdir(parents=True)
(wrapper_dir / "__init__.py").write_text('__version__ = "0.14.1"\n', encoding="utf-8")
dist_dir = site / "lingtai-0.14.2.dist-info"
dist_dir.mkdir(parents=True)
(dist_dir / "METADATA").write_text("Name: lingtai\nVersion: 0.14.2\n", encoding="utf-8")

# Import the real kernel nudge module from the checkout first.
from lingtai.kernel.nudge import kernel_version as kv

# Boot the interpreter against the staged install: load the wrapper from the
# real file; importlib.metadata then reads the staged dist-info from sys.path.
spec = importlib.util.spec_from_file_location("lingtai", str(wrapper_dir / "__init__.py"))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
sys.modules["lingtai"] = wrapper
sys.path.insert(0, str(site))

class Agent:
    def __init__(self, workdir):
        self._working_dir = str(workdir)
        self._notification_store = PosixNotificationStoreAdapter(pathlib.Path(workdir))
        self.logs = []
    def _log(self, event, **fields):
        self.logs.append((event, fields))

workdir = root / "agent"
workdir.mkdir()
a = Agent(workdir)
kv.check(a)

nudge_path = workdir / ".notification" / "nudge.json"
assert nudge_path.is_file(), f"nudge.json missing: {nudge_path}"
data = json.loads(nudge_path.read_text(encoding="utf-8"))
entries = data["data"]["nudges"]
assert len(entries) == 1, entries
e = entries[0]
assert e["kind"] == "kernel_version", e
assert e["nudge_channel"] == "release_version", e
assert e["source"] == "installed-distribution", e
assert e["running"] == "0.14.1" and e["installed"] == "0.14.2", e
assert e.get("latest") is None, e
assert e["suggested_action"] == "refresh-installed-runtime-if-authorized-and-safe", e
assert "already on disk" in e["detail"], e
assert "system(action='refresh')" in e["detail"], e
assert e["title"] == "LingTai kernel refresh available: 0.14.1 -> 0.14.2", e

# No remote probe was initiated: the persistent state carries no remote fields.
state_path = workdir / ".notification" / ".nudge_state.json"
if state_path.is_file():
    state = json.loads(state_path.read_text(encoding="utf-8"))
    kv_state = state.get("kernel_version", {})
    assert "last_remote_check_date" not in kv_state, kv_state
    assert "latest_seen" not in kv_state, kv_state

print("K001 ENTRY:", json.dumps({
    k: e.get(k) for k in ("kind", "nudge_channel", "source", "running",
                           "installed", "suggested_action")}, ensure_ascii=False))
print("workdir:", workdir)
```

2. Run `python <scratch>/k001.py` from `<repo-root>`; the script exits 0 and
   prints `K001 ENTRY:` and the workdir path.
3. Read the notification file the script created: `<printed workdir>/.notification/nudge.json`.

### Expected evidence
- [ ] The script exits 0 and prints `K001 ENTRY:` with `kind: kernel_version`,
      `nudge_channel: release_version`, `source: installed-distribution`,
      `running: 0.14.1`, `installed: 0.14.2`, `suggested_action:
      refresh-installed-runtime-if-authorized-and-safe`.
- [ ] `<workdir>/.notification/nudge.json` exists and its `data.nudges` contains
      exactly one entry whose `title` is `LingTai kernel refresh available:
      0.14.1 -> 0.14.2`, whose `latest` is `null`, and whose `detail` contains
      `already on disk` and `system(action='refresh')`.
- [ ] The local-mismatch branch never initiates a remote probe: if
      `<workdir>/.notification/.nudge_state.json` exists, its
      `kernel_version` block has no `last_remote_check_date` or `latest_seen`
      keys (the harness asserts this). The strict "the remote fetch is never
      invoked" proof stays in the pytest, whose fake remote probe raises.

### Pass / Fail
Pass when all evidence items hold. Fail if the entry is missing, has a
different `nudge_channel`/`source`/`suggested_action`, or the state file shows a
remote probe was started (`last_remote_check_date`/`latest_seen` present).

## Behavior K002 — kernel version nudge is fail-safe: diagnostic direction and dev/source-drift runtimes

- **id**: K002
- **title**: running-newer/unparseable runtimes emit a read-only diagnostic
  (`installed-distribution-diagnostic`), dev/editable/source-checkout runtimes
  skip and clear the nudge with a recorded reason
- **guards**: `src/lingtai/CONTRACT.md` § Contract rules rule 6 — Nudge
  findings stay on the ordinary `.notification/nudge.json` transport under the
  shared policy
  ([CONTRACT.md](../CONTRACT.md#contract-rules))
- **supersedes**: `tests/test_kernel_version_nudge.py::test_runtime_version_direction_is_fail_safe_and_equal_pairs_probe_remote`,
  `tests/test_kernel_version_nudge.py::test_dev_or_editable_runtime_skips_and_clears_kernel_nudge`,
  `tests/test_kernel_version_nudge.py::test_runtime_info_detects_source_checkout_from_wrapper_file`,
  `tests/test_kernel_version_nudge.py::test_malformed_remote_version_cannot_be_promoted_by_numeric_substrings`
- **runner**: any LingTai agent with `shell` and `file` tools at a checkout of
  this repository
- **prerequisites**: a checkout of this repo at `<repo-root>`; an empty scratch
  dir `<scratch>`; python on PATH
- **estimate**: 3 min

### Steps
1. Write `<scratch>/k002.py` with the following content (`REPO` is the only
   value to substitute). Each case stages a real on-disk installation and
   boots the interpreter against it — the same real `_runtime_info` path the
   agent uses:

```python
import sys, os, json, pathlib, tempfile, importlib.util
from datetime import datetime, timezone
REPO = r"<repo-root>"
sys.path.insert(0, os.path.join(REPO, "src"))
os.chdir(REPO)

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.nudge import kernel_version as kv, upsert

root = pathlib.Path(tempfile.mkdtemp(prefix="k002"))

class Agent:
    def __init__(self, workdir):
        self._working_dir = str(workdir)
        self._notification_store = PosixNotificationStoreAdapter(pathlib.Path(workdir))
        self.logs = []
    def _log(self, event, **fields):
        self.logs.append((event, fields))

def load_wrapper(wrapper_path, site):
    spec = importlib.util.spec_from_file_location("lingtai", str(wrapper_path))
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    sys.modules["lingtai"] = w
    sys.path.insert(0, str(site))

def make_site(running, installed, *, editable=False, source_checkout=False, name="site"):
    site = root / name
    site.mkdir(parents=True, exist_ok=True)
    if source_checkout:
        base = root / "srccheck"
        (base / ".git").mkdir(parents=True, exist_ok=True)
        (base / "pyproject.toml").write_text("", encoding="utf-8")
        wdir = base / "src" / "lingtai"
        wdir.mkdir(parents=True, exist_ok=True)
    else:
        wdir = site / "lingtai"
        wdir.mkdir(parents=True, exist_ok=True)
    wrapper_file = wdir / "__init__.py"
    wrapper_file.write_text(f'__version__ = "{running}"\n', encoding="utf-8")
    dist_dir = site / f"lingtai-{installed}.dist-info"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "METADATA").write_text(f"Name: lingtai\nVersion: {installed}\n", encoding="utf-8")
    if editable:
        (dist_dir / "direct_url.json").write_text(
            json.dumps({"dir_info": {"editable": True}}), encoding="utf-8")
    return site, wrapper_file

def nudge_entries(d):
    p = d / ".notification" / "nudge.json"
    if not p.is_file():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["data"]["nudges"]

# (a) running newer than installed -> read-only diagnostic, never refresh
site, wrapper = make_site("0.17.0", "0.16.5", name="site_a")
load_wrapper(wrapper, site)
a = root / "a"; a.mkdir(exist_ok=True)
aa = Agent(a)
kv.check(aa)
ea = nudge_entries(a)[0]
assert ea["source"] == "installed-distribution-diagnostic", ea
assert ea["suggested_action"] == "inspect-runtime-interpreter-and-import-paths", ea
assert "Do not refresh" in ea["detail"], ea

# (b) unparseable running version -> same diagnostic
site, wrapper = make_site("not-a-version", "0.17.0", name="site_b")
load_wrapper(wrapper, site)
b = root / "b"; b.mkdir(exist_ok=True)
ab = Agent(b)
kv.check(ab)
eb = nudge_entries(b)[0]
assert eb["source"] == "installed-distribution-diagnostic", eb

# (c) editable install -> nudge skipped AND cleared, reason recorded
site, wrapper = make_site("0.14.1", "0.14.1", editable=True, name="site_c")
load_wrapper(wrapper, site)
c = root / "c"; c.mkdir(exist_ok=True)
ac = Agent(c)
upsert(ac, "kernel_version", {"title": "stale", "source": "release-manifest"})
assert nudge_entries(c), "pre-seeded nudge should exist"
kv.check(ac)
assert nudge_entries(c) == [], "stale nudge must be cleared"
state = json.loads((c / ".notification" / ".nudge_state.json").read_text(encoding="utf-8"))
assert state["kernel_version"]["skip_reason"] == "editable-install", state
assert state["kernel_version"]["last_skip_date"] == datetime.now(timezone.utc).date().isoformat(), state

# (d) source checkout -> skipped, reason recorded
site, wrapper = make_site("0.14.1", "0.14.1", source_checkout=True, name="site_d")
load_wrapper(wrapper, site)
d2 = root / "d"; d2.mkdir(exist_ok=True)
ad = Agent(d2)
upsert(ad, "kernel_version", {"title": "stale", "source": "release-manifest"})
kv.check(ad)
assert nudge_entries(d2) == []
state = json.loads((d2 / ".notification" / ".nudge_state.json").read_text(encoding="utf-8"))
assert state["kernel_version"]["skip_reason"] == "source-checkout", state

print("K002 OK: diagnostic(a,b), editable-skip+clear(c), source-checkout-skip+clear(d)")
```

2. Run `python <scratch>/k002.py` from `<repo-root>`; it must exit 0.

### Expected evidence
- [ ] The script exits 0 and prints `K002 OK: ...`.
- [ ] Case (a) and (b) entries carry `source: installed-distribution-diagnostic`,
      `suggested_action: inspect-runtime-interpreter-and-import-paths`, and
      `Do not refresh` in `detail` — an ambiguous direction never recommends
      refresh.
- [ ] Case (c) leaves `data.nudges` empty (a pre-seeded stale nudge is cleared)
      and `.nudge_state.json` records `kernel_version.skip_reason` =
      `editable-install` with `last_skip_date` equal to today's UTC date.
      Case (d) likewise leaves the channel empty and records `skip_reason` =
      `source-checkout`.
- [ ] Malformed remote candidates (`999-not-a-release`, `release-999`,
      `not-a-version`) are never promoted: that scenario is NOT part of this
      LABT — it requires a stubbed remote probe and stays in
      `tests/test_kernel_version_nudge.py::test_malformed_remote_version_cannot_be_promoted_by_numeric_substrings`
      as the bottom-level assertion.

### Pass / Fail
Pass when all evidence holds. Fail if a diagnostic case recommends refresh, a
dev/editable/source-checkout runtime still emits or keeps a nudge, or the skip
reason is not recorded.

## Behavior K003 — `eigen` is retired: LingTai identity lives in `context` + `psyche`, name changes are `system.name_set`

- **id**: K003
- **title**: the `eigen` intrinsic no longer exists; the identity/soul surface
  is `context` (molt owns the identity summary in `input`) plus the
  manual-only `psyche` family, and the true name is set once via
  `system.name_set` / `system.name_nickname`
- **guards**: `context-contract` § Purpose and public ownership — no OLD `psyche`
  action is reachable, `eigen` is gone, name changes remain
  `system.name_set | system.name_nickname`, and `molt` requires `summary` in
  its own strict input branch
  ([CONTRACT.md](../tools/context/CONTRACT.md#purpose-and-public-ownership))
- **supersedes**: `tests/test_eigen.py::test_eigen_is_gone_and_psyche_is_the_durable_domain_root`,
  `tests/test_eigen.py::test_eigen_schema_has_molt`,
  `tests/test_eigen.py::test_eigen_name_sets_agent_name`
- **runner**: any LingTai agent with `shell` tool at a checkout of this
  repository
- **prerequisites**: a checkout of this repo at `<repo-root>`; python on PATH
- **estimate**: 1 min

### Steps
1. Run the following from `<repo-root>` (single command, quoted):

```
python -c "import sys,os,json; sys.path.insert(0, os.path.join(r'<repo-root>','src')); from lingtai.tools.registry import INTRINSICS; from lingtai.tools.context import get_schema; from lingtai.tools.system import get_schema as sys_schema; i=sorted(INTRINSICS); print('intrinsics:', [k for k in i if k in ('context','psyche','eigen','pad','lingtai')]); s=get_schema('en'); print('context actions:', s['properties']['action']['enum']); print('summary-not-root:', 'summary' not in s['properties']); print('summarize-type:', s['properties']['summarize']['type']); print('required:', sorted(s['required'])); ss=sys_schema('en'); print('system name actions:', [a for a in ss['properties']['action']['enum'] if a in ('name_set','name_nickname')])"
```

2. Read the printed values against the checklist below.

### Expected evidence
- [ ] `intrinsics:` prints exactly `['context', 'psyche']` — `eigen`, `pad`,
      and `lingtai` are NOT registered intrinsics.
- [ ] `context actions:` is `['molt', 'summarize', 'rebuild', 'manual']` — no
      `context_molt`/`pad_edit`/`lingtai_update`/`name_set` old spellings.
- [ ] `summary-not-root:` is `True` (the molt summary lives in the `molt`
      action's `input` branch, not on the root), `summarize-type:` is
      `boolean` (the unrelated root post-processing control), and `required:`
      is `['action', 'input', 'reasoning']`.
- [ ] `system name actions:` prints `['name_set', 'name_nickname']` — identity
      naming is owned by the `system` tool.

### Pass / Fail
Pass when every printed value matches. Fail if `eigen` (or `pad`/`lingtai`)
appears as an intrinsic, or `name_set` is absent from the `system` schema.

## Behavior K004 — `context.molt` refuses an empty or missing summary before any context mutation

- **id**: K004
- **title**: `context(action="molt", input={...})` without a non-empty
  `summary` returns the pinned refusal error and sheds nothing
- **guards**: `context-contract` § Molt safety invariants — agent-initiated molt
  requires a nonempty retrospective; validation occurs before snapshot/
  archive/wipe or count mutation
  ([CONTRACT.md](../tools/context/CONTRACT.md#molt-safety-invariants))
- **supersedes**: `tests/test_eigen.py::test_context_molt_rejects_empty_summary`,
  `tests/test_eigen.py::test_context_molt_rejects_missing_summary`
- **runner**: any LingTai agent with `shell` tool at a checkout of this
  repository
- **prerequisites**: a checkout of this repo at `<repo-root>`; python on PATH
- **estimate**: 1 min

### Steps
1. Run the following from `<repo-root>` (single command, quoted):

```
python -c "import sys,os,json,types; sys.path.insert(0, os.path.join(r'<repo-root>','src')); from lingtai.tools.context import handle; stub=types.SimpleNamespace(); empty=handle(stub, {'action':'molt','input':{'summary':''},'reasoning':'t'}); missing=handle(stub, {'action':'molt','input':{},'reasoning':'t'}); print('empty:', json.dumps(empty, ensure_ascii=False)); print('missing:', json.dumps(missing, ensure_ascii=False))"
```

2. Read the printed errors.

### Expected evidence
- [ ] `empty:` prints `{"error": "summary cannot be empty — write what you need to remember."}`.
- [ ] `missing:` prints `{"error": "summary is required — write a briefing to your future self."}`.
- [ ] The refusal happens before any session wipe/snapshot/molt-count mutation
      (the stub has no session at all, yet the error returns cleanly).

### Pass / Fail
Pass when both pinned error strings are returned verbatim. Fail if an empty
summary is accepted or a non-error result is returned.

## Behavior K005 — a large tool result never becomes a notification; `_meta.agent_meta.current_tool_result_chars` ranks it instead

- **id**: K005
- **title**: large tool results produce no `large_tool_result` system
  notification (neither per-result nor at the turn boundary); the same result
  is reported through `_meta.agent_meta.current_tool_result_chars` with
  `total_chars`, `threshold`, `over_threshold_count`, and `top_results`
- **guards**: `notification-tool` § Behavior — agents MUST NOT route
  large-result compaction through the notification tool, and the kernel no
  longer publishes a `large_tool_result` source
  ([CONTRACT.md](../tools/notification/CONTRACT.md#behavior))
- **supersedes**: `tests/test_large_result_rescan.py::test_rescan_returns_zero_for_huge_history`,
  `tests/test_large_result_no_notification.py::test_large_result_still_reported_by_current_tool_result_chars`,
  `tests/test_large_result_no_notification.py::test_current_tool_result_chars_reports_threshold_and_over_count`
- **runner**: any LingTai agent with `shell` and `file` tools at a checkout of
  this repository
- **prerequisites**: a checkout of this repo at `<repo-root>`; python on PATH
- **estimate**: 2 min

### Steps
1. Write `<scratch>/k005.py` with the following content (`REPO` is the only
   value to substitute). It drives the real `ChatInterface`, the real
   `meta_block.current_tool_result_chars`, the real retained no-op hooks, and
   a real notification store — no mocks:

```python
import sys, os, json, pathlib, tempfile
REPO = r"<repo-root>"
sys.path.insert(0, os.path.join(REPO, "src"))
os.chdir(REPO)

from lingtai.kernel.llm.interface import ChatInterface, ToolCallBlock, ToolResultBlock
from lingtai.kernel import meta_block
from lingtai.kernel.base_agent.messaging import _rescan_large_tool_results, _enqueue_system_notification
from lingtai.kernel.base_agent import BaseAgent
from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter

class Agent:
    def __init__(self, workdir):
        self._working_dir = str(workdir)
        self._notification_store = PosixNotificationStoreAdapter(pathlib.Path(workdir))
        self.logs = []
    def _log(self, event, **fields):
        self.logs.append((event, fields))
    def _wake_nap(self, reason):
        self.logs.append(("wake_nap", {"reason": reason}))

workdir = pathlib.Path(tempfile.mkdtemp(prefix="k005"))
a = Agent(workdir)

iface = ChatInterface()
iface.add_assistant_message([ToolCallBlock(id="tc-rank", name="bash", args={})])
iface.add_tool_results([ToolResultBlock(id="tc-rank", name="bash",
                                        content={"output": "Q" * 40000, "status": "ok"})])
chat = type("Chat", (), {"interface": iface})()
session = type("Session", (), {"chat": chat})()
a._session = session

# The large result is ranked, not notified: default threshold is 3000 chars.
s = meta_block.current_tool_result_chars(a)
assert s["total_chars"] >= 40000, s
assert s["threshold"] == 3000, s
assert "tc-rank" in [r["id"] for r in s["top_results"]], s
assert s["top_results"][0]["tool_name"] == "bash", s

# Configured threshold: only results over it are counted.
a._summarize_notification_threshold = 5000
s2 = meta_block.current_tool_result_chars(a)
assert s2["threshold"] == 5000 and s2["over_threshold_count"] == 1, s2

# Retained no-ops: the turn-boundary rescan and the per-result hook publish
# nothing.
assert _rescan_large_tool_results(a) == 0
assert BaseAgent._maybe_notify_large_tool_result(
    a, "bash", {"output": "X" * 80000, "status": "ok"}, tool_call_id="tc-huge") is None

# No system notification was published: .notification/system.json does not exist.
sys_json = workdir / ".notification" / "system.json"
assert not sys_json.is_file(), f"system.json must not exist yet: {sys_json}"

# Positive control: the store channel works; a real publish creates system.json.
ev = _enqueue_system_notification(a, source="labt-control", ref_id="ctrl-1", body="control")
assert ev, "control publish should return an event id"
assert sys_json.is_file(), "control publish should create system.json"
payload = json.loads(sys_json.read_text(encoding="utf-8"))
events = payload["data"]["events"]
assert len(events) == 1 and events[0]["source"] == "labt-control", events
assert all(evt["source"] != "large_tool_result" for evt in events)

print("K005 OK: ranked, no large_tool_result notification, control published")
```

2. Run `python <scratch>/k005.py` from `<repo-root>`; it must exit 0.

### Expected evidence
- [ ] The script exits 0 and prints `K005 OK: ...`.
- [ ] `current_tool_result_chars` reports `total_chars >= 40000`, the default
      `threshold: 3000`, and `over_threshold_count: 1`; with
      `_summarize_notification_threshold = 5000` it reports `threshold: 5000`
      and `over_threshold_count: 1`, and `top_results` lists `id: tc-rank`
      with `tool_name: bash`.
- [ ] The turn-boundary rescan returns `0` and the per-result hook returns
      `None` — retained no-ops. Before the control publish,
      `<workdir>/.notification/system.json` does NOT exist: no
      `large_tool_result` (or any) system notification was produced for the
      40000-char result.
- [ ] The positive control proves the channel works: after publishing one
      `labt-control` event through the real `_enqueue_system_notification`,
      `system.json` exists with exactly one event, and no event carries
      `source: large_tool_result`.

### Pass / Fail
Pass when all evidence holds. Fail if any `large_tool_result` notification is
published, the rescan returns a nonzero count, the ranked summary lacks
`total_chars`/`threshold`/`over_threshold_count`/`top_results`, or the control
publish does not land in `system.json`.

## Behavior K006 — capped/large results carry `_meta.tool_meta.comment.overflow`

- **id**: K006
- **title**: spilled and large-but-inline tool results carry exactly one
  machine-generated guidance topic `_meta.tool_meta.comment.overflow` pointing
  at `logs/events.jsonl` by `tool_call_id` (never a sidecar `saved_path`),
  while ordinary small results carry no comment and the `tool_meta` identity
  fields (`id`, `char_count`, `elapsed_ms`) stay intact
- **guards**: `notification-tool` § Behavior — large-result compaction is
  guidance, not notification; the digest action is `system(action="summarize")`
  ([CONTRACT.md](../tools/notification/CONTRACT.md#behavior))
- **supersedes**: `tests/test_tool_meta_comment_overflow.py::test_spilled_result_carries_overflow_comment`,
  `tests/test_tool_meta_comment_overflow.py::test_large_inline_result_carries_overflow_comment`,
  `tests/test_tool_meta_comment_overflow.py::test_small_result_has_no_overflow_comment`
- **runner**: any LingTai agent with `shell` and `file` tools at a checkout of
  this repository
- **prerequisites**: a checkout of this repo at `<repo-root>`; python on PATH
- **estimate**: 2 min

### Steps
1. Write `<scratch>/k006.py` with the following content (`REPO` is the only
   value to substitute). It drives the real `ToolExecutor` (the same executor
   the agent's turn loop builds) against real payload files on disk and
   inspects the resulting model-visible `ToolResultBlock` metadata — no
   mocks:

```python
import sys, os, json, pathlib, tempfile
REPO = r"<repo-root>"
sys.path.insert(0, os.path.join(REPO, "src"))
os.chdir(REPO)

from lingtai.kernel.llm.base import ToolCall
from lingtai.kernel.llm.interface import ToolResultBlock
from lingtai.kernel.loop_guard import LoopGuard
from lingtai.kernel.meta_block import build_tool_meta_overflow_comment
from lingtai.kernel.tool_executor import ToolExecutor, _DEFAULT_MAX_RESULT_CHARS

# The spill hard ceiling (PREVENTIVE_MAX_CHARS, src/lingtai/kernel/tool_result_artifacts.py).
assert _DEFAULT_MAX_RESULT_CHARS == 200_000

root = pathlib.Path(tempfile.mkdtemp(prefix="k006"))
big = root / "big.txt"; big.write_text("Z" * 1200, encoding="utf-8")
med = root / "med.txt"; med.write_text("Q" * 400, encoding="utf-8")

# Wire-format builder: the same shape BaseAgent.turn passes to ToolExecutor
# (turn.py: make_tool_result_fn=lambda name, result, **kw: agent.service.make_tool_result(...)).
def make_tool_result(name, result, **kw):
    return ToolResultBlock(kw.get("tool_call_id", ""), name, result)

def make_executor(dispatch, workdir, max_result_chars, threshold):
    return ToolExecutor(
        dispatch_fn=dispatch,
        make_tool_result_fn=make_tool_result,
        guard=LoopGuard(max_total_calls=50),
        working_dir=workdir,
        max_result_chars=max_result_chars,
        summarize_notification_threshold=threshold,
    )

# Builder shape: exactly one guidance topic, four subkeys, references the durable log.
c = build_tool_meta_overflow_comment("tc-abc")
blob = json.dumps(c)
assert set(c) == {"summary", "full_original", "how_to_retrieve", "after_consuming"}
assert "logs/events.jsonl" in blob and "tool_call_id=tc-abc" in blob
assert "saved_path" not in blob
assert "grep" in c["how_to_retrieve"] and "lingtai-agent log query" in c["how_to_retrieve"]
assert ("daemon" in c["how_to_retrieve"] or "subagent" in c["how_to_retrieve"])
assert "summarize" in c["after_consuming"]

# Spilled result (payload over the 500-char cap) -> status spilled + comment.
ex = make_executor(lambda tc: {"data": big.read_text(encoding="utf-8")}, root / "wd1", 500, None)
block = ex.execute([ToolCall(name="read", args={}, id="tc-spill")])[0][0]
assert block.content["status"] == "spilled", block.content
assert (root / "wd1" / "tmp" / "tool-results").is_dir(), "spill artifact dir missing"
tm = block.metadata.get("tool_meta", {})
assert set(tm["comment"].keys()) == {"overflow"}, tm
assert tm["id"] == "tc-spill"
assert isinstance(tm["char_count"], int) and isinstance(tm["elapsed_ms"], int)
assert "spilled_char_count" in tm
assert "logs/events.jsonl" in json.dumps(tm["comment"])
assert "saved_path" not in json.dumps(tm["comment"])

# Large but inline (over the 100-char hint threshold, under the spill cap).
ex2 = make_executor(lambda tc: {"data": med.read_text(encoding="utf-8")}, root / "wd2", _DEFAULT_MAX_RESULT_CHARS, 100)
block2 = ex2.execute([ToolCall(name="read", args={}, id="tc-large")])[0][0]
assert block2.content.get("status") != "spilled"
tm2 = block2.metadata.get("tool_meta", {})
assert tm2["char_count"] > 100
assert "overflow" in tm2.get("comment", {})
assert "logs/events.jsonl" in tm2["comment"]["overflow"]["full_original"]

# Small result -> no comment; identity fields intact.
ex3 = make_executor(lambda tc: {"ok": True}, root / "wd3", _DEFAULT_MAX_RESULT_CHARS, 100)
block3 = ex3.execute([ToolCall(name="read", args={}, id="tc-small")])[0][0]
tm3 = block3.metadata.get("tool_meta", {})
assert "comment" not in tm3, tm3
assert tm3["id"] == "tc-small"
assert isinstance(tm3["char_count"], int) and isinstance(tm3["elapsed_ms"], int)

print("K006 OK: builder-shape, spilled, large-inline, small-no-comment")
```

2. Run `python <scratch>/k006.py` from `<repo-root>`; it must exit 0.

### Expected evidence
- [ ] The script exits 0 and prints `K006 OK: ...`.
- [ ] `_DEFAULT_MAX_RESULT_CHARS` is `200000` (the preventive spill ceiling).
- [ ] The builder returns exactly `{summary, full_original, how_to_retrieve,
      after_consuming}`; `full_original` names `logs/events.jsonl` and
      `tool_call_id=<id>`; no `saved_path`; `how_to_retrieve` offers `grep`
      and `lingtai-agent log query`; `after_consuming` recommends
      `system(action="summarize")`.
- [ ] A spilled result reports `status: spilled` with
      `tool_meta.comment.overflow`, `spilled_char_count`, and a preserved
      spill artifact under `<workdir>/tmp/tool-results/`; a large inline
      result (over the hint threshold) also carries the comment; a small
      result carries no `comment`. Identity fields `id`/`char_count`/
      `elapsed_ms` remain intact in all three.

### Pass / Fail
Pass when all evidence holds. Fail if the comment is split into multiple
headings, references a `saved_path`, appears on a small result, or drops the
identity fields.

## Behavior K007 — source-drift nudge: startup vs on-disk fingerprint mismatch emits the `source_integrity` finding

- **id**: K007
- **title**: when the current on-disk source fingerprint (git rev / source
  digest) differs from the startup fingerprint, the `source_drift` nudge entry
  is emitted on the `source_integrity` channel; when they match again the
  entry is cleared
- **guards**: `src/lingtai/CONTRACT.md` § Contract rules rule 6 — every
  declared Nudge kind uses the ordinary `.notification/nudge.json` transport
  and the shared global policy
  ([CONTRACT.md](../CONTRACT.md#contract-rules))
- **supersedes**: `tests/test_source_drift.py` (emit/clear scenarios)
- **runner**: any LingTai agent with `shell` and `file` tools at a checkout of
  this repository
- **prerequisites**: a checkout of this repo at `<repo-root>`; an empty scratch
  dir `<scratch>`; python and `git` on PATH
- **estimate**: 2 min

### Steps
1. Write `<scratch>/k007.py` with the following content (`REPO` is the only
   value to substitute). It creates a real throwaway git repo, captures the
   real runtime fingerprint through the real `_capture_runtime_fingerprint`
   and the real `PosixGitCliAdapter` revision port, then drives the real
   `source_drift.check` against real `.notification/nudge.json` artifacts —
   no mocks:

```python
import sys, os, json, pathlib, tempfile, subprocess
REPO = r"<repo-root>"
sys.path.insert(0, os.path.join(REPO, "src"))
os.chdir(REPO)

from lingtai.adapters.posix.git_cli import PosixGitCliAdapter
from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.base_agent.lifecycle import _capture_runtime_fingerprint
from lingtai.kernel.nudge import source_drift as sd

class Agent:
    def __init__(self, workdir):
        self._working_dir = str(workdir)
        self._notification_store = PosixNotificationStoreAdapter(pathlib.Path(workdir))
        self.logs = []
    def _log(self, event, **fields):
        self.logs.append((event, fields))

def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

root = pathlib.Path(tempfile.mkdtemp(prefix="k007"))
repo = root / "src_repo"
repo.mkdir()
git("init", cwd=repo)
git("config", "user.email", "agent@lingtai", cwd=repo)
git("config", "user.name", "agent", cwd=repo)
# Seed commits so the short-revision abbreviation is stable across the test.
(repo / "seed.txt").write_text("seed 1\n", encoding="utf-8")
git("add", "-A", cwd=repo)
git("commit", "-m", "seed 1", cwd=repo)
(repo / "seed.txt").write_text("seed 2\n", encoding="utf-8")
git("add", "-A", cwd=repo)
git("commit", "-m", "seed 2", cwd=repo)

# Startup fingerprint: the real capture at the current HEAD (call it rev A).
port = PosixGitCliAdapter(repo)
startup = _capture_runtime_fingerprint(port)
revA = startup["git_rev"]
assert revA and startup["source_digest"] and len(startup["source_digest"]) == 12, startup
revA_full = git("rev-parse", "HEAD", cwd=repo).stdout.strip()

# On-disk source moves to a new commit (rev B) -> drift.
(repo / "code.txt").write_text("v2\n", encoding="utf-8")
git("add", "-A", cwd=repo)
git("commit", "-m", "rev B", cwd=repo)
revB = port.current_revision(None, 2.0)
assert revA != revB, (revA, revB)

workdir = root / "agent"
workdir.mkdir()
a = Agent(workdir)
a._runtime_fingerprint = startup
a._source_revision_port = port  # on-disk repo is now at rev B

sd.check(a)
nudge_path = workdir / ".notification" / "nudge.json"
assert nudge_path.is_file(), nudge_path
data = json.loads(nudge_path.read_text(encoding="utf-8"))
entries = data["data"]["nudges"]
assert len(entries) == 1, entries
e = entries[0]
assert e["kind"] == "source_drift", e
assert e["nudge_channel"] == "source_integrity", e
assert e["title"] == "Source drift detected \u2014 running code is stale", e
assert e["suggested_action"] == "system(action='refresh')", e
assert f"git_rev: {revA} \u2192 {revB}" in e["detail"], e["detail"]
assert e["startup_fingerprint"]["git_rev"] == revA, e
assert e["disk_fingerprint"]["git_rev"] == revB, e

# On-disk source returns to the startup commit -> the finding is cleared.
git("reset", "--hard", revA_full, cwd=repo)
assert port.current_revision(None, 2.0) == revA, "on-disk source must match startup again"
a._nudge_source_drift_state["last_probe_ts"] = 0.0  # advance past the 60s throttle
sd.check(a)
assert not nudge_path.exists(), "stale source_drift finding must be cleared"

print("K007 OK: source_integrity emit + clear (git_rev %s -> %s)" % (revA, revB))
```

2. Run `python <scratch>/k007.py` from `<repo-root>`; it must exit 0.

### Expected evidence
- [ ] The script exits 0 and prints `K007 OK: source_integrity emit + clear
      (git_rev <revA> -> <revB>)`.
- [ ] The drift entry has `kind: source_drift`, `nudge_channel:
      source_integrity`, `title: Source drift detected — running code is
      stale`, `suggested_action: system(action='refresh')`, and a `detail`
      listing `git_rev: <revA> → <revB>`, plus `startup_fingerprint`/
      `disk_fingerprint` blocks whose `git_rev` values match the two commits.
- [ ] After `git reset --hard <revA>` the port again reports `<revA>` (on-disk
      source matches startup), and the second `check` clears the finding:
      `data.nudges` is empty and `.notification/nudge.json` is gone.
- [ ] Source-digest drift (a changed kernel package file) is not exercised
      here — it needs a synthetic package and stays in
      `tests/test_source_drift.py::TestCaptureRuntimeFingerprint` as the
      bottom-level assertion.

### Pass / Fail
Pass when all evidence holds. Fail if no entry is emitted on drift, the
channel/title/action differ, or a matching fingerprint does not clear the
finding.
