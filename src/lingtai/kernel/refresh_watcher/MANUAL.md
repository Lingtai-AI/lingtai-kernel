---
related_files:
  - src/lingtai/kernel/refresh_watcher/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/ANATOMY.md
  - src/lingtai/kernel/refresh_watcher/__init__.py
  - src/lingtai/kernel/refresh_watcher/watcher_program.py
  - src/lingtai/adapters/posix/refresh_watcher.py
maintenance: |
  Keep this manual's what/how/why in sync with RefreshWatcherPort,
  RefreshWatcherRequest, watcher_program.render_watcher_script, and the POSIX
  adapter whenever their behavior changes. Update related_files when a cited
  file moves. This manual is linked from both refresh_watcher/CONTRACT.md and
  refresh_watcher/ANATOMY.md per root CONTRACT.md Design principle 4 — keep
  both edges present.
---
# Refresh Watcher Manual

## What

Refresh watcher is the capability that hands a running agent's relaunch off
to a detached process that outlives the current process. When
`_perform_refresh` (`src/lingtai/kernel/base_agent/lifecycle.py`) finishes
the `.refresh`/`.refresh.taken` filesystem handshake, it builds a
`RefreshWatcherRequest` — the handshake paths, the relaunch command, and a
JSON snapshot of the agent's identity fields — and hands it to
`agent._refresh_watcher.spawn_detached(request)`. No raw program source or
caller environment crosses the Port; the request carries only the data the
watcher program needs, and its identity-field snapshot is taken once at
construction so it cannot be changed by anything that happens to the source
data afterward.

## How

1. **Core builds the request.** `_perform_refresh` never builds program text
   or an environment itself; it constructs a `RefreshWatcherRequest`
   (`src/lingtai/kernel/refresh_watcher/__init__.py`) from handshake paths it
   already computed and calls `spawn_detached(request)` exactly once, after
   the ACK invariant is established and before setting `_cancel_event`/`_shutdown`.
   The agent's runtime-identity fields (`agent._runtime_identity_event_fields`)
   are serialized to `identity_fields_json` with `json.dumps(...)` at this
   same call site — not carried as a live dict or a shallow container —
   because the identity payload nests a mutable `kernel_runtime` sub-dict (the
   same object as the process-wide identity cache) that would otherwise stay
   aliased and mutable even inside an "immutable" request.
2. **Core renders the program text.** `watcher_program.render_watcher_script(request)`
   (`src/lingtai/kernel/refresh_watcher/watcher_program.py`) first decodes
   `request.identity_fields_json` back to a `dict` via `_decode_identity_fields`
   — raising `ValueError` before generating any source if the snapshot is
   invalid JSON or not a JSON object — then renders a complete,
   self-contained Python program source: the ACK/lock handshake poll, the
   12-attempt relaunch loop with a 10s health-check wait, stale same-agent
   duplicate-process cleanup (SIGTERM → 5s grace → SIGKILL), redacted event
   logging, and the terminal failure artifact/notification. This module
   performs no OS calls itself.
3. **The POSIX adapter renders the environment and launches.**
   `PosixRefreshWatcherAdapter.spawn_detached(request)`
   (`src/lingtai/adapters/posix/refresh_watcher.py`) calls
   `render_watcher_script`, builds the launched process's environment via
   `build_watcher_env` (captures `os.environ`, applies the
   `env_overwrite` request field as `LINGTAI_REFRESH_ENV_OVERWRITE=1`), and
   launches `[sys.executable, "-c", script]` via `subprocess.Popen` with all
   three streams to `DEVNULL` and `start_new_session=True`.
4. **The watcher program runs standalone.** Once spawned it re-derives
   everything it needs from literals embedded by `render_watcher_script`; it
   holds no reference to the parent process's live objects and outlives it.

## Why

The Port used to accept a raw `script: str` and a full `env: Mapping[str,
str]` — Core built ~365 lines of Python source as an inline string literal
buried inside `_perform_refresh`'s control flow, indistinguishable from
ordinary lifecycle logic and impossible to read, diff, or call as a unit
without scrolling past unrelated handshake code. Splitting the request
(typed data), the renderer (`watcher_program.py`, Core-owned, pure,
*directly callable* to produce the program text), and the adapter (POSIX
process launch mechanism) follows the Contract's Core/Port/Adapter boundary:
Core owns *what data the watcher needs* and *what text describes its
policy*, never *how a process gets started* or *what the full OS environment
looks like*.

This is a text-generation extraction, not a code-shape change to the
watcher's own policy: `render_watcher_script(request)` is directly callable
and its *output text* can be asserted on (as the existing pinned-substring
and AST-structural tests do), but the watcher's actual runtime behavior —
the ACK/lock poll, the relaunch retry loop, stale-duplicate cleanup — is
still only exercisable by running the rendered text as a real detached
`python -c` subprocess, exactly as before this slice; it did not become
ordinary in-process importable/unit-testable module code, and this slice
does not claim otherwise. `render_watcher_script` remaining a pure
string-render function (rather than a templating engine or code-generation
framework) is deliberate — the smallest change that makes the program *text*
directly producible and inspectable without redesigning the watcher's
retry/heartbeat/duplicate-cleanup policy, or introducing a process-
supervision Port, both of which stay explicit non-goals for a later slice.
