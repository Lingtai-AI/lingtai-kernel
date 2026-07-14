---
name: refresh-watcher
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/refresh_watcher/ANATOMY.md
  - src/lingtai/kernel/refresh_watcher/__init__.py
  - src/lingtai/kernel/refresh_watcher/watcher_program.py
  - src/lingtai/kernel/refresh_watcher/MANUAL.md
  - src/lingtai/adapters/posix/refresh_watcher.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/_refresh_watcher_helpers.py
  - tests/test_perform_refresh_handshake.py
  - tests/test_deep_refresh.py
  - tests/test_base_agent.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Refresh Watcher

## Purpose

Refresh watcher is Core's outbound boundary for handing a typed relaunch
request off to a detached process supervisor after `_perform_refresh`
completes the `.refresh`/`.refresh.taken` filesystem handshake. It separates
the invariant *"the relaunch watcher must survive this process's exit"* from
the concrete process mechanism, which today is a `subprocess.Popen` of a
fresh interpreter detached into its own POSIX session. Core builds a
`RefreshWatcherRequest` — handshake paths, the relaunch command, agent
identity fields, and the env-overwrite policy bit — and hands it to this Port
without knowing the interpreter path, stream wiring, full-environment
construction, or session/process-group mechanics. The current sole
authority-bearing consumer is `_perform_refresh`
(`src/lingtai/kernel/base_agent/lifecycle.py`). See
[`MANUAL.md`](MANUAL.md) for the what/how/why walkthrough.

This Port governs the hand-off of the watcher process itself, and Core owns a
directly callable *renderer* (`watcher_program.render_watcher_script`) that
produces the watcher program's text — the launched watcher is still
generated `python -c` program source, executed later as a detached
subprocess, not ordinary in-process executable/importable module code, and
this slice does not claim the watcher's own policy became independently
unit-testable. The watcher program's internal behavior — the
`.refresh`/`.refresh.taken` handshake, ACK/lock deadlines, relaunch retry
policy, stale-duplicate cleanup, and terminal-failure redaction/alerting —
is rendered by `watcher_program.render_watcher_script` from a
`RefreshWatcherRequest`; it is unaffected by which adapter spawns the
rendered text. `base_agent/ANATOMY.md` still documents the historical
behavior narrative for `lifecycle.py`'s role in building the request.

## Behavior

Agents and coding agents MUST preserve the current observable semantics: the
launched process runs the exact program text
`watcher_program.render_watcher_script(request)` produces, receives exactly
`build_watcher_env(request)` as its full environment (the POSIX adapter's
`os.environ` capture plus the request's `env_overwrite` policy bit
translated to `LINGTAI_REFRESH_ENV_OVERWRITE=1`), does not inherit the
caller's stdio, and outlives the caller's process. A consumer that receives a
refresh watcher receives a real detached-process capability: there is NO
disabled, `None`-means-skip, or no-op *watcher implementation* —
`spawn_detached` never silently no-ops. `RefreshWatcherRequest` is immutable
(frozen dataclass); Core MUST NOT pass raw program source or a
caller-supplied full environment through the Port.

Construction and use are deliberately separate obligations. `BaseAgent`
accepts `refresh_watcher` as an optional, defaulted-`None` constructor
parameter so the ~230 pre-existing raw `BaseAgent(...)` construction sites
unrelated to refresh (most of the test suite) keep constructing without
change. Composition roots (`lingtai.Agent`, `lingtai.cli.build_agent`) always
inject the production adapter; only a bare, hand-constructed `BaseAgent` may
omit it. The fail-loud obligation lives at *use*, not construction:
`_perform_refresh` raises `RuntimeError` if `agent._refresh_watcher` is
`None` — but only once `_build_launch_cmd()` has produced a real command and
strictly before any `.refresh`/`.refresh.taken` handshake mutation or
`_cancel_event`/`_shutdown` signaling, so a missing Port can never orphan an
agent mid-handshake. The no-launch-cmd path (`_build_launch_cmd()` returns
`None`, e.g. bare `BaseAgent`) remains fully usable without a watcher, since
it never reaches `spawn_detached`. Agents MUST NOT let concrete process
identities (interpreter path, `-c` invocation, stream detachment,
`start_new_session`, POSIX process groups) leak up through this Port, and
MUST NOT construct a concrete adapter inside Core. The Port makes no promise
about the launched process's exit code, output, or lifetime beyond "detached
and started" — those are the watcher script's own concern, not this Port's.

## Port

`RefreshWatcherPort` exposes exactly one observable operation:

- `spawn_detached(request: RefreshWatcherRequest) -> None` — launch the
  watcher program described by `request` as a detached process supervising
  relaunch. Returns once the process has been started; does not wait for
  completion and does not return process identity.

`RefreshWatcherRequest` (`src/lingtai/kernel/refresh_watcher/__init__.py`) is
an immutable (frozen dataclass) value object carrying exactly:
`taken_path`, `lock_path`, `events_path`, `stderr_log`, `working_dir`,
`cmd: tuple[str, ...]`, `agent_name`, `address`,
`identity_fields_json: str` (default `"{}"`), and `env_overwrite` (default
`True`). `cmd` is a tuple, not a `list`, because `frozen=True` alone only
prevents attribute *reassignment* — it does not make a mutable container's
contents immutable — so a `list`-typed field would let a caller mutate the
"immutable" request's owned data in place; `tuple[str, ...]` is safe because
its elements (strings) are already-immutable leaves. `identity_fields` is
**not** a tuple-of-pairs, because that shape is only *shallowly* immutable: the
producer, `runtime_identity_event_fields()`, returns a dict whose
`kernel_runtime` value is itself a nested mutable dict (the same object as
`runtime_identity.py`'s module-level cache, not a copy) — a tuple of `(key,
value)` pairs would still alias and expose that nested dict, so mutating it
after request construction could silently change what later gets rendered.
`identity_fields_json` is instead a JSON object string snapshot, serialized
once at the construction boundary (`_perform_refresh`); a JSON string is
genuinely immutable at any nesting depth, because no later mutation of the
source object (nested or not) can reach back through an already-serialized
string. The request carries no raw program source and no caller-supplied
full environment — those are rendered by the renderer and adapter below.

The Port names no `subprocess`, `os`, POSIX, interpreter-path, or stream
vocabulary. There is no wait, poll, signal, or process-identity query.

## Adapters

`PosixRefreshWatcherAdapter` is the only production adapter
(`src/lingtai/adapters/posix/refresh_watcher.py`). Its `spawn_detached`
renders the program text via the Core-owned
`watcher_program.render_watcher_script(request)`
(`src/lingtai/kernel/refresh_watcher/watcher_program.py`) and the process
environment via its own `build_watcher_env(request)` (captures `os.environ`
and applies `request.env_overwrite` as `LINGTAI_REFRESH_ENV_OVERWRITE=1`),
then launches `[sys.executable, "-c", script]` via `subprocess.Popen` with
`stdin`, `stdout`, and `stderr` all set to `subprocess.DEVNULL` and
`start_new_session=True` — the concrete detachment mechanism, POSIX-specific
because `start_new_session` is not available on Windows, so the adapter lives
under `adapters/posix` like `PosixWorkdirLeaseAdapter`. Core never constructs
it and never calls `os.environ` or `subprocess` itself. A deterministic
in-memory `FakeRefreshWatcher` in `tests/_refresh_watcher_helpers.py`
implements the same Port — recording every `RefreshWatcherRequest` and
translating it the same way the production adapter does (rendering script
and env) instead of launching a process — to prove substitutability. A
Windows production adapter is explicitly out of scope for this slice.

## Contract rules

1. `spawn_detached(request)` launches a process running exactly
   `watcher_program.render_watcher_script(request)` with exactly the
   adapter's `build_watcher_env(request)` as its full environment; the call
   returns once the process has been started and does not block on or track
   it.
2. `BaseAgent.__init__` accepts `refresh_watcher: RefreshWatcherPort | None =
   None`. Construction never fails for its absence — this is the deliberate
   exception among `BaseAgent`'s Ports, made so unrelated raw-construction
   tests are unaffected. Core stores it verbatim as `self._refresh_watcher`
   (including `None`) and never constructs the concrete adapter itself.
3. `_perform_refresh` fails loudly — `raise RuntimeError(...)` — when
   `agent._refresh_watcher is None` AND `_build_launch_cmd()` returned a real
   command, checked immediately after that command is obtained and strictly
   before any `.refresh`/`.refresh.taken` filesystem mutation or
   `_cancel_event`/`_shutdown` signaling. When `_build_launch_cmd()` returns
   `None` (no-launch-cmd path), `_perform_refresh` returns before this check
   and a missing Port is never observed. When the Port is present,
   `_perform_refresh` builds a `RefreshWatcherRequest` from the handshake
   paths, launch command, and identity fields, and calls
   `agent._refresh_watcher.spawn_detached(request)` exactly once per refresh,
   after the handshake has established the ACK invariant and before setting
   `_cancel_event` / `_shutdown`. A failed handshake (ack not established)
   MUST NOT call `spawn_detached` regardless of Port presence.
4. The composition roots `src/lingtai/agent.py` (`lingtai.Agent`, when
   `refresh_watcher` is not already in kwargs) and `src/lingtai/cli.py`
   (`build_agent`) always construct and inject `PosixRefreshWatcherAdapter`,
   so every agent built through a composition root has a real refresh-watcher
   capability. An explicitly injected watcher wins over composition-root
   construction. Only a raw, hand-constructed `BaseAgent` may omit it.
5. Core imports, receives, and invokes only the Port and
   `RefreshWatcherRequest`. Concrete process construction (interpreter path,
   stream detachment, session/group mechanics, `os.environ` capture, and the
   concrete environment-variable name — `ENV_OVERWRITE_VAR` — used to signal
   env-file overwrite) belongs to the outer adapter; Core never names or
   imports it. `watcher_program.py` is Core-owned and performs no OS calls;
   it knows only the boolean `request.env_overwrite` policy bit, never the
   env-var transport name, and is a pure function from `RefreshWatcherRequest`
   to program-source text.
6. The generated watcher program's terminal-failure metadata bounds and
   redacts `last_stderr_tail`, `last_cleanup_error`, and `last_relaunch_error`
   identically (via the shared `_redact_bounded` helper in the rendered
   program) before writing `refresh_failed_permanent.json`, the
   `.notification/system.json` alert, or the final `refresh_failed_permanent`
   event. No raw or unbounded exception text may reach any of those three
   sinks.
7. `build_watcher_env(request)` MUST make `request.env_overwrite` fully
   authoritative in both directions: when `True` it sets `ENV_OVERWRITE_VAR`
   in the copied environment; when `False` it explicitly removes
   `ENV_OVERWRITE_VAR` from the copy, even if the parent process's own
   `os.environ` already has it set. A `False` request MUST NOT silently
   inherit a stale `True` value from the parent environment. The parent
   process's `os.environ` itself is never mutated — `build_watcher_env`
   returns a fresh `dict` copy.
8. `RefreshWatcherRequest.cmd` is a tuple (`tuple[str, ...]`), not a `list`.
   `frozen=True` alone only blocks attribute reassignment; a mutable
   container field would let a caller mutate the request's owned data after
   construction despite the dataclass being "frozen". `RefreshWatcherRequest.
   identity_fields_json` is a JSON string snapshot, not a tuple-of-pairs,
   because the identity payload contains a nested mutable dict
   (`kernel_runtime`) that a shallow tuple-of-pairs would still alias — see
   the Port section above for the full rationale.
   `watcher_program._decode_identity_fields` decodes and validates
   `identity_fields_json` back to a `dict` inside `render_watcher_script`,
   failing loudly (`ValueError`) on invalid JSON or a non-object top-level
   value, before any generated source is produced — an invalid snapshot MUST
   NOT silently render broken or empty watcher-program text.

## Contract tests

`tests/test_perform_refresh_handshake.py` and `tests/test_deep_refresh.py`
inject `FakeRefreshWatcher` (`tests/_refresh_watcher_helpers.py`) in place of
the production adapter and assert `_perform_refresh`'s observable contract
through it: the watcher is spawned exactly once per successful refresh
(handshake variants: synthesized, preserved, renamed, both-marker cleanup);
it is NOT spawned when the ack cannot be established or when
`_build_launch_cmd()` returns `None`; the spawned `env` (translated by the
fake the same way the production adapter would) carries
`LINGTAI_REFRESH_ENV_OVERWRITE=1`; and the spawned `script` text (rendered
the same way) carries the generated watcher source (redaction wiring,
stale-duplicate cleanup, identity-field handoff) that the pre-existing
watcher-script assertions in those files already pin.
`test_posix_refresh_watcher_adapter_spawns_exact_detached_process`
(`tests/test_perform_refresh_handshake.py`) proves the production adapter's
`spawn_detached(request)` translates a `RefreshWatcherRequest` into the exact
`Popen` call via `render_watcher_script`/`build_watcher_env`. These tests
prove the Port is exercised without any real subprocess; the watcher
program's own runtime behavior (executed via a real interpreter subprocess in
a small number of existing tests) is unaffected by and independent of which
adapter performs the hand-off.

Two focused tests in `tests/test_perform_refresh_handshake.py` pin the
optional-construction / fail-at-use split:
`test_perform_refresh_no_launch_cmd_works_without_refresh_watcher` builds a
raw `BaseAgent` with `refresh_watcher` omitted entirely, asserts
`agent._refresh_watcher is None`, and proves `_perform_refresh()` no-ops
cleanly (no raise, no handshake file, no shutdown) on the default
no-launch-cmd path.
`test_perform_refresh_real_launch_cmd_without_watcher_raises_before_handshake`
rebinds `_build_launch_cmd` to a real command on the same watcher-less agent
and proves `_perform_refresh()` raises `RuntimeError` while `.refresh`,
`.refresh.taken`, `_shutdown`, and `_cancel_event` all remain untouched.
`tests/test_base_agent.py` (unrelated to refresh) proves the ~230-site raw
`BaseAgent(...)` construction pattern across the suite is unaffected: it
builds agents without a `refresh_watcher` kwarg and never touches
`_perform_refresh`.

Two focused tests in `tests/test_perform_refresh_handshake.py` pin the
`identity_fields_json` immutability contract (rule 8):
`test_identity_fields_json_snapshot_immune_to_nested_source_mutation` builds
a `RefreshWatcherRequest` from a source dict with a nested sub-dict, mutates
both the top-level and nested values *after* construction, and proves the
rendered `identity_fields` literal still reflects the pre-mutation snapshot.
`test_identity_fields_json_invalid_or_non_object_fails_loudly` parametrizes
over invalid JSON and valid-but-non-object JSON (a list, a bare string, a
number, `null`) and proves `render_watcher_script` raises `ValueError` for
each rather than producing broken generated source.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes
require synchronized Port, adapter, contract-test, and contract updates;
structural or composition changes also update the paired Anatomy and
reciprocal parents.
