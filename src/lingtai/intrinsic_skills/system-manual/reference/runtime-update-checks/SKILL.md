---
name: runtime-update-checks
description: >
  Nested system-manual reference for tracing LingTai kernel update nudges:
  runtime/version/source identity, packaged versus editable/source behavior,
  heartbeat dispatch, nudge persistence and notification delivery, refresh
  versus installation, human confirmation, and post-refresh verification. The
  ordered pre-flight for a refresh is the sibling `refresh-precheck`.
version: 0.3.0
tags: [lingtai, runtime, kernel, nudge, updates, refresh, editable, source, diagnostics]
last_changed_at: "2026-08-07T00:00:00Z"
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
  Keep this as the local detailed source for kernel update/nudge read-only
  diagnosis and refresh mechanics, not release-migration routing. Update it
  when the nudge producers, heartbeat dispatch, notification sync, runtime
  identity, refresh boundary, or supported update ownership changes; keep
  system-manual and notification-manual as routers.
---

# Runtime Update Checks

Kernel-local read-only diagnosis and refresh mechanics for update nudges. The
install/update route itself is owned by the official installer — see step 9 and
**Boundaries** below for the split. This is operational guidance, not permission
to download, install, change configuration, or relaunch a runtime.

## The lifecycle and its owners

The end-to-end path is:

1. **Discover runtime facts — `kernel_version.py` and runtime identity.** The
   kernel reads the already-loaded `lingtai` wrapper from `sys.modules` for the
   running version, and `importlib.metadata` for the installed distribution
   version. `runtime_identity.py` separately stamps durable event identity with
   package/dev mode, source kind, and available git state. No package-index
   request is needed for the local comparison.
2. **Check `kernel_version` — `kernel_version.py`.** This is the
   `release_version` nudge channel. In editable/source/dev runtimes it is
   silent and clears only its own stale release-version entry. If running and installed
   versions differ, it emits a local refresh nudge and preserves the existing
   local-only path. For packaged, non-editable, non-dev runtimes whose versions
   match, it fetches the exact `lingtai-kernel-release-manifest.json` asset from
   the latest GitHub and Gitee releases through the bounded producer probe gate.
   If both manifests are available, version, manifest content, and artifact
   hashes must agree; a mirror mismatch is reported without choosing a higher
   version. If one mirror is unavailable, the other may establish the result.
   A newer manifest version produces a package-availability nudge; network or
   response errors are recorded diagnostically and do not become an update.
3. **Check `source_drift` — `source_drift.py`.** This is the
   `source_integrity` nudge channel. It compares the startup runtime fingerprint
   (git revision and curated source digest when available) with a fresh on-disk
   fingerprint. Drift means the running process differs from code currently on
   disk. Editable/source/dev runtimes still emit this diagnostic; the finding is
   local read-only source diagnosis and refresh mechanics only, and does not
   enter the release-migration route.
4. **Dispatch — `nudge/__init__.py` and `lifecycle.py`.** The heartbeat loop
   runs the nudge dispatcher once per one-second tick. `kernel_version` and
   `source_drift` each have their own roughly 60-second in-memory probe gate;
   `goal` runs on each dispatch while IDLE and applies its configured
   idle-reminder delay. One failing producer is logged and does not block the
   others.
5. **Persist and publish — `nudge/__init__.py` and the Notification Store.**
   Shared Nudge policy state lives in `.notification/.nudge_state.json` only for
   dismissal mute expiries. The user-facing mirror is `.notification/nudge.json`;
   it is not a package state or migration registry. Each entry has a unique
   `kind`; `upsert` replaces that kind, `remove` deletes it, and clearing the
   last entry clears the channel file. Updates use the store's atomic
   compare/update operation.
6. **Sync and wake — `BaseAgent._sync_notifications`.** The heartbeat polls
   allowlisted `.notification` files by fingerprint. A changed nudge file is
   injected as a synthesized `notification(action="check")` pair when IDLE;
   ACTIVE work defers delivery until an IDLE boundary; ASLEEP is moved to IDLE,
   then the pair and `MSG_TC_WAKE` are queued. STUCK/SUSPENDED cannot inject.
   A failed injection leaves the fingerprint uncommitted for retry (with the
   documented degraded recovery path after healing pending tool calls).
7. **Interpret — the agent, using the stable installer plus this local reference.**
   Treat the payload as kernel-synchronized facts, not a human command. Compare
   `running`, `installed`, `latest`, and `source`; inspect the runtime when
   ambiguous. For a real update, let Shell execute the installer's concise
   `--help`, without reading or pasting the script source, and follow whatever
   it currently instructs; the installer owns
   release-interval migration navigation and the exact child command shape
   while this reference owns local diagnosis and refresh mechanics.
8. **Confirm — the human/config-owner.** After the installer has displayed the
   applicable release-interval migrations, obtain explicit human/config-owner
   authorization for every migration/config write and for refresh. Apply only
   authorized writes, validate
   the resulting configuration, and refresh last. A release-manifest availability
   nudge also requires telling the human what was found and receiving explicit confirmation
   before any download or package update. A nudge or this manual
   never grants authority; a local refresh remains gated by work safety and the
   human's intent when it could interrupt active work.
9. **Install/update — the single official installer.** Normal user-facing
   installation and updates, including `lingtai-tui`, `lingtai-portal`, the
   managed kernel runtime, exact-tag migration guidance, mirror comparison, and
   artifact verification, belong to `https://lingtai.ai/install.sh`. Bare
   `pip install --upgrade lingtai` is not the normal user instruction; pip/venv
   commands below are diagnostic or developer verification only.
10. **Refresh and verify — kernel lifecycle plus the operator.**
    After authorized writes are validated, `system(action="refresh")` requests a
    deferred relaunch only when the runtime can build a valid launch command and
    has a configured refresh watcher. Without a launch command it returns
    without relaunching; without a refresh watcher it raises. Diagnose either
    outcome before treating the refresh as successful. Refresh never pulls
    commits, downloads a release asset, installs a package, or switches an
    editable checkout. After a confirmed update or refresh, verify the new
    process's interpreter, `lingtai.__file__`, `lingtai.kernel.__file__`,
    version, and import path.

## Packaged, editable, and source/dev runtimes

For a packaged/non-editable install, the distribution metadata and release
manifest are meaningful. A local `running != installed` mismatch means a
package landed on disk after this process started: it is a refresh opportunity,
not a package upgrade. A `latest > installed` mismatch means an official release
manifest advertises a possible package upgrade; it does not mean that package
has been downloaded.

The editable/source/dev modes are detected through `direct_url.json` with
`dir_info.editable: true`; source checkouts are recognized from the module path
and the checkout's `.git` plus `pyproject.toml`; dev-version markers also
suppress only the `kernel_version` release-version check. Missing distribution
metadata is treated as source/dev rather than as permission to install. In these
modes, the checkout, branch, commit, dirty state, and import path are the useful
truth. A `source_drift` finding can still appear as a source-integrity
diagnostic, and an `init_config_shape` finding can still appear as
configuration-staleness guidance; neither is package-update authority.

## Nudge mechanics and dismissal

When a Nudge is emitted, inspect its self-describing policy message before
adjusting the process environment.

`kernel_version`, `source_drift`, and `init_config_shape` are producers of the
shared low-priority `.notification/nudge.json` envelope. The envelope carries
`data.nudges`, a `published_at` timestamp, channel-level instructions, and a
self-describing policy block. Those built-in producer kinds carry fixed
`nudge_channel` metadata: `release_version` for `kernel_version`,
`source_integrity` for `source_drift`, and `configuration_staleness` for
`init_config_shape`. Unrelated legacy/unknown entries remain channel-less.
`kind: kernel_version` has `running`, `installed`, `latest` (or `null` for a
local refresh), `source`, and a suggested action. `source_drift` carries startup
and disk fingerprints instead.

The shared Nudge policy records finding identity and dismissal mute expiry in
`.notification/.nudge_state.json`. The two global controls
(`LINGTAI_NUDGE_ENABLED`, `LINGTAI_NUDGE_REPEAT_INTERVAL`) suppress publication
and set the post-dismiss repeat window for an unresolved finding; their defaults,
accepted values, reload behavior, and invalid-value fallback are registered in the
repo-root `ENVIRONMENT_VARIABLES.md`, routed via
`reference/environment-variables/SKILL.md`. Producer probe gates are only bounded
observation costs, not product cadence. A producer removes an entry when its
real fact resolves; `kernel_version` also removes its own entry when the runtime
is intentionally release-version-silent. Dismissal is mute, not resolution;
only a later real-reader check with zero findings resolves a problem.

After interpreting a nudge, use the narrowest safe action:

```text
notification(action="dismiss_channel",
             input={"channel": "nudge", "force": null, "reason": null},
             reasoning="acknowledge the nudges")
```

Do not call `notification(action="check", input={})` merely to confirm
dismissal. The
generic notification protocol and guarded dismissal rules live in
`notification-manual`; this reference owns the meaning of these two kinds.

## Read-only diagnosis

Inspect the current mirror and durable state without changing them:

```bash
ls -l .notification/nudge.json .notification/.nudge_state.json 2>/dev/null
sed -n '1,240p' .notification/nudge.json 2>/dev/null
sed -n '1,240p' .notification/.nudge_state.json 2>/dev/null
```

Use the interpreter that launched the agent. The CLI exports
`LINGTAI_RUNTIME_PYTHON`; if it is absent, use the platform-specific TUI
runtime Python, not a convenient shell environment:

```bash
PYTHON=${LINGTAI_RUNTIME_PYTHON:-$HOME/.lingtai-tui/runtime/venv/bin/python}
"$PYTHON" - <<'PY'
import importlib.metadata as md
import sys
import lingtai, lingtai.kernel
print("python=", sys.executable)
print("lingtai_version=", getattr(lingtai, "__version__", "unknown"))
print("lingtai_dist=", md.version("lingtai"))
print("lingtai_file=", getattr(lingtai, "__file__", "unknown"))
print("lingtai.kernel_file=", getattr(lingtai.kernel, "__file__", "unknown"))
try:
    print("direct_url=", md.distribution("lingtai").read_text("direct_url.json"))
except Exception as exc:
    print("direct_url_error=", type(exc).__name__, str(exc)[:120])
PY
```

For source/dev disagreement, read-only git evidence is appropriate:

```bash
git -C /path/to/checkout status --short --branch
git -C /path/to/checkout log -1 --format='%H %cI %s'
git -C /path/to/checkout remote -v
```

Replace the checkout path with the path containing the imported module; do not
assume the current directory is that checkout. Do not print credentials or
environment dumps.

## Troubleshooting

**Running and installed versions disagree.** Confirm the live interpreter and
both module files first. If the installed distribution is newer, the safe
meaning is “code already on disk can be loaded by a refresh.” If the running
process is newer, inspect whether metadata or the import path is stale; do not
blindly downgrade or install.

**Runtime and checkout disagree.** `sys.executable`, `lingtai.__file__`,
`lingtai.kernel.__file__`, `direct_url.json`, and read-only git status identify
which checkout/environment is actually active. A checkout's version alone does
not prove what the running process imported.

**Release-manifest/network failure.** A failed remote request records bounded
`last_error` state in `.nudge_state.json`; it is not evidence that an update
exists. Check connectivity or release-mirror availability with the human. Do
not turn a transient failure into a manual install recommendation.

**Nudge is missing or stale.** Check both files above. Missing `nudge.json` can
mean no producer currently has an entry, a matching version cleared it, a dev
runtime skipped it, or it was dismissed. A stale entry can be a mirror waiting
for the next heartbeat sync; compare its kind and version pair with durable
state. Unknown JSON files are not collected as notification channels.

**Refresh did not activate new code.** A refresh only rebuilds/relaunches the
runtime from code already visible on disk. Re-run the read-only interpreter and
import-path probe in the new process, inspect the refresh/runtime logs, and
check for a still-held old process or a different environment. It cannot pull a
commit or repair an incomplete source checkout.

**Interpreter/import path is unknown.** Stop before any update action. Ask the
human or launcher owner which process is authoritative, then compare
`LINGTAI_RUNTIME_PYTHON`, `sys.executable`, the two module `__file__` values,
distribution metadata, and `direct_url.json`. Do not use an unrelated shell
Python as a substitute.

## Boundaries

`https://lingtai.ai/install.sh` owns install/update execution and release
migration navigation (step 9). The Notification package manual owns channel allowlisting,
sync concepts, and dismissal safety. `reference/refresh-precheck/SKILL.md` owns
the ordered pre-flight and post-refresh verification pass. This reference owns
only kernel-local read-only diagnosis and refresh mechanics; `source_drift` stays
local and does not enter release-migration routing.
