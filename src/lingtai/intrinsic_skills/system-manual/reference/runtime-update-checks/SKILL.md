---
name: runtime-update-checks
description: >
  Nested system-manual reference for update, source, install, nudge, and
  runtime-mismatch diagnosis. Owns interpreter/provenance evidence and the
  source/venv cutover handoff; routes refresh sequencing to refresh-precheck.
version: 0.4.0
tags: [lingtai, runtime, kernel, nudge, updates, install, editable, source, diagnostics]
last_changed_at: "2026-09-09T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
- src/lingtai/tools/notification/manual/SKILL.md
- src/lingtai/tools/notification/manual/reference/channel-model/SKILL.md
- src/lingtai/kernel/nudge/ANATOMY.md
- src/lingtai/kernel/nudge/__init__.py
- src/lingtai/kernel/nudge/kernel_version.py
- src/lingtai/kernel/nudge/source_drift.py
- src/lingtai/intrinsic_skills/system-manual/reference/refresh-precheck/SKILL.md
- scripts/lib/release_manifest.py
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/__init__.py
- src/lingtai/kernel/notification_store/__init__.py
- src/lingtai/kernel/runtime_identity.py
- src/lingtai/kernel/snapshot/__init__.py
- src/lingtai/kernel/snapshot/ANATOMY.md
- src/lingtai/cli.py
- src/lingtai/venv_resolve.py
- tests/test_kernel_version_nudge.py
- tests/test_source_drift.py
- tests/test_runtime_identity.py
maintenance: |
  Keep this as the detailed owner for kernel update/nudge facts, source and
  install provenance, mismatch diagnosis, and the frozen cutover handoff.
  Update it with the producers, heartbeat dispatch, notification sync, runtime
  identity, or installer ownership; keep refresh transaction order only in the
  sibling refresh-precheck reference.
---

# Runtime Update Checks

Use this reference to determine **what is running, what is on disk, and who owns
a change**. It is read-only guidance, not permission to download, install,
modify configuration, switch a checkout, or relaunch. Once a target source/venv
is proven and authorized, `refresh-precheck` owns the complete refresh
transaction; do not copy its happy-path sequence here.

## Ownership map

| Question | Owner / answer |
|---|---|
| Is an official package update available? | `kernel_version.py` plus the release-manifest path described below. |
| Did source already on disk change under this process? | `source_drift.py`; local source-integrity diagnosis only. |
| Which interpreter/source/version is authoritative? | Runtime identity plus the exact probe in this reference. |
| How is a normal user install/update performed? | The current `https://lingtai.ai/install.sh --help` output. |
| May a download, migration, config write, or disruptive relaunch proceed? | Only explicit human/config-owner authority after the proposed action is shown. |
| How is the one refresh performed and receipted? | `reference/refresh-precheck/SKILL.md`. |
| How is a nudge dismissed? | `notification-manual`, after the matching fact is interpreted or resolved. |

## Nudge lifecycle and meanings

1. **Local facts.** `kernel_version.py` reads the already-loaded `lingtai`
   wrapper for the running version and distribution metadata for the installed
   version. `runtime_identity.py` stamps package/dev mode, source kind, and
   available Git identity into durable events.
2. **Release-version producer.** Packaged, non-editable, non-dev runtimes compare
   matching local versions with the exact release-manifest asset on GitHub and
   Gitee through a bounded probe gate. Two available mirrors must agree on
   version, content, and hashes; one available mirror may establish the result.
   Network or mirror disagreement is diagnosis, not an update. Editable/source/
   dev runtimes are release-version-silent and clear only their stale entry.
3. **Source-integrity producer.** `source_drift.py` compares the startup runtime
   fingerprint with a fresh on-disk fingerprint. `source_drift` means this
   process differs from local disk; it never becomes release-migration or
   package-install authority.
4. **Dispatch.** Heartbeat runs the nudge dispatcher once per one-second tick.
   `kernel_version` and `source_drift` each use a roughly 60-second in-memory
   probe gate. One producer failure is logged and does not block the others.
5. **Persistence and delivery.** The user-facing aggregate is
   `.notification/nudge.json`; dismissal mute expiries live in
   `.notification/.nudge_state.json`. Each producer owns one `kind`. Atomic
   upsert replaces that kind and resolution removes it. Notification sync
   injects changed allowlisted files at an IDLE boundary, wakes ASLEEP, defers
   during ACTIVE, and cannot inject into STUCK/SUSPENDED.
6. **Interpretation.** A nudge is synchronized kernel evidence, not a human
   command. Compare its fields and diagnose ambiguity before proposing action.

Built-in nudge kinds are:

- `kernel_version` on `release_version`: `running`, `installed`, `latest` (or
  null for code already on disk), `source`, and a suggested action.
- `source_drift` on `source_integrity`: startup and disk fingerprints.
- `init_config_shape` on `configuration_staleness`: configuration-shape
  guidance, not update authority.

The controls `LINGTAI_NUDGE_ENABLED` and
`LINGTAI_NUDGE_REPEAT_INTERVAL` govern publication and the post-dismiss mute
window. Their values, invalid fallback, read point, and reload behavior live in
`environment-variables`; producer probe gates are observation costs, not user
cadence. Dismissal is mute, not resolution.

## Packaged versus source/dev evidence

For a packaged/non-editable runtime:

- `running != installed` normally means newer package code is already on disk
  than this process loaded: investigate a reload, not another package install.
- `latest > installed` means an official release manifest advertises an update;
  it does not mean anything was downloaded.
- Running newer than metadata indicates stale metadata or an import-path
  mismatch; never blindly downgrade.

Editable/source/dev detection uses `direct_url.json` with
`dir_info.editable: true`, source checkout markers, module paths, and dev-version
markers. Missing distribution metadata is source/dev evidence, not permission
to install. For these runtimes, exact interpreter, both imported module paths,
checkout, branch, commit, dirty state, and selector values are the useful facts.
Read Git only at the source path identified by the import probe; generic Git
status in the current agent directory proves nothing.

## Exact read-only probe

Use the interpreter exported by the launcher. If it is absent, stop and ask the
launcher owner; do not substitute PATH Python or guess a venv.

```bash
PYTHON="$LINGTAI_RUNTIME_PYTHON"
[ -n "$PYTHON" ] || { echo "LINGTAI_RUNTIME_PYTHON is unset; stop" >&2; return 1 2>/dev/null || exit 1; }
"$PYTHON" - <<'PY'
import importlib.metadata as md
import sys
import lingtai, lingtai.kernel
print("python=", sys.executable)
print("lingtai_version=", getattr(lingtai, "__version__", "unknown"))
try:
    print("lingtai_dist=", md.version("lingtai"))
    print("direct_url=", md.distribution("lingtai").read_text("direct_url.json"))
except Exception as exc:
    print("distribution_metadata=", type(exc).__name__, str(exc)[:120])
print("lingtai_file=", getattr(lingtai, "__file__", "unknown"))
print("lingtai.kernel_file=", getattr(lingtai.kernel, "__file__", "unknown"))
PY
```

When those paths identify a source checkout, the narrow follow-up is:

```bash
git -C <identified-source-checkout> status --short --branch
git -C <identified-source-checkout> log -1 --format='%H %cI %s'
```

Do not print environment dumps, credentials, or unrelated remotes. Inspect
`.pth`, `direct_url.json`, dist-info, or installer state only when this probe
shows an install/import mismatch or a source/venv cutover requires it; none is a
universal refresh check.

## Update/install route and authority

For a real user-facing install or update, let Shell execute
`https://lingtai.ai/install.sh --help` and follow its current output without
reading or pasting the script source. That installer owns managed kernel/TUI/
portal installation, exact-tag migration navigation, mirror comparison,
artifact verification, and child command shapes. Bare
`pip install --upgrade lingtai` is not the normal user instruction; pip/venv
commands are limited to authorized diagnosis or developer verification.

Tell the human what the nudge and probe established. After applicable migrations
and writes are displayed, proceed only after receiving explicit confirmation
and explicit human/config-owner authority for each download, install, migration,
configuration write, and disruptive relaunch. A nudge and this manual grant none.

The update/build owner validates its own work before handoff and freezes one
cutover receipt containing:

```text
target: <exact agent/workdir>
runtime_tuple:
  sys_executable: <exact executable>
  lingtai_file: <exact lingtai.__file__>
  lingtai_kernel_file: <exact lingtai.kernel.__file__>
  version_or_head: <exact expected version/commit>
source_root: <optional diagnostic root>
selectors: <intended launcher/init interpreter selector values>
authority: <who authorized install/write/cutover>
owner_validation: <targeted update/build/install result>
```

Hand that receipt to `refresh-precheck` mode B. That reference owns selector
comparison, exactly one refresh, the fresh runtime-tuple check, originating-
channel round trip, and failure recovery. Do not duplicate them here.

## Mismatch diagnosis

| Symptom | Read-only interpretation and next owner |
|---|---|
| Running and installed versions differ | Verify interpreter and both module paths. Installed newer is code-on-disk reload territory; running newer requires metadata/import diagnosis. |
| Runtime and checkout disagree | Compare executable, module paths, `direct_url.json`, then exact Git evidence at the identified source. Resolve source/venv ownership before refresh. |
| Release-manifest/network failure | Read bounded producer error state. It is not evidence of an update; ask about connectivity/mirror availability. |
| Nudge is missing or stale | Compare `.notification/nudge.json` with `.notification/.nudge_state.json`; consider dev-mode silence, dismissal mute, producer resolution, and the next heartbeat sync. |
| New code did not activate | Refresh cannot pull or repair code. Compare the expected cutover receipt to the new process and route the transaction failure to `refresh-precheck` mode C. |
| Interpreter/import source is unknown | Stop all update/cutover action and ask the launcher owner which process and selectors are authoritative. |

To acknowledge interpreted nudges, follow `notification-manual`; its narrow form
is `notification(action="dismiss_channel", input={"channel":"nudge","force":null,"reason":null}, reasoning="acknowledge interpreted nudges")`.
Do not call notification check merely to confirm dismissal.

## Boundaries

This reference owns update/source/install/nudge/mismatch diagnosis and the
cutover handoff. It does not own refresh ordering, preset activation, retries,
or post-refresh receipts. It performs no download, write, install, checkout
switch, migration, relaunch, or notification mutation by itself. `source_drift`
stays local and outside release-migration routing; `refresh-precheck` is the
single owner of every happy-path or recovery refresh transaction.
