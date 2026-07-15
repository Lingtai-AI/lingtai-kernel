---
related_files:
  - CLAUDE.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/reminders/__init__.py
  - src/lingtai/kernel/reminders/context_pressure.py
  - src/lingtai/kernel/config.py
  - src/lingtai/kernel/session.py
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/kernel/tool_executor.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# Reminders

Runtime reminder abstractions. Today this package holds exactly one:
`ContextPressureReminder`, the unified home for the molt / context-pressure
reminder. This is deliberately **one owned abstraction for one reminder**, not a
global reminder registry — that generalization is explicitly out of scope for
now (CLAUDE.md: "three similar lines beats a premature abstraction"; a registry
lands only when a second reminder proves the shape).

## What it owns

Before this package existed the reminder was split in two: `SessionManager` held
raw `_context_pressure_streak` / `_context_pressure_last_round_id` counters, and
`meta_block` held both the warn decision and the natural-language prose (in
`build_molt_context` and `build_reconstruction_tool_meta`). `ContextPressureReminder`
pulls the state machine + decisions + prose into one debuggable object:

- **Provider-round input** — `note_round(usage, *, round_id)` records one *fresh
  provider round*'s context usage. `round_id` (the `SessionManager._api_calls`
  counter) dedups multiple observations of the same round; usage semantics:
  `>= reconstruction_ratio` (0.85) advances the streak, `0 <= usage < ratio`
  resets it to 0 (relieved), `< 0` sentinel leaves it untouched.
- **Transient streak state** — `streak`, `last_round_id`, `last_usage`,
  `active` (derived: `streak >= warn_after_rounds`), and
  `last_transition_reason` (why the last observation moved the way it did:
  `initial` / `high_round` / `warning_active` / `relieved` / `duplicate_round` /
  `unknown_usage`). Not persisted — a fresh/restored session starts fresh.
- **Channel B — current-state reminder** — `current_molt_context(usage)` returns
  the natural-language string for `_meta.agent_meta.agent_state.context.molt`
  (current agent state, carried on the designated final batch result as part of
  the whole `agent_meta` snapshot), or `None` unless `active`.
- **Channel A — reconstruction annotation** — `annotate_reconstruction(after_usage,
  *, recovery_target=None)` returns the
  `_meta.agent_meta.agent_state.events.reconstruction.molt` string, or `None`
  when the rebuilt after-context is below the recovery target.
  It owns only the *warning decision + prose*; the event assembly (provider-vs-
  local after-context resolution, event shape, one-shot pop) stays in
  `meta_block.build_reconstruction_tool_meta`.
- **Emission descriptors** — `reminder_message_hash(text)` (short stable hex
  hash), `current_molt_emission_descriptor(reminder, *, usage, message)`, and
  `reconstruction_molt_emission_descriptor(event, *, message)` return a compact,
  JSON-safe `{event_name, payload}` used by the `_meta` assembly layer to log a
  structured runtime event when a reminder is *actually attached* to the
  current `agent_meta.agent_state` snapshot.
  Event names: `context_pressure_current_molt_reminder_emitted` and
  `context_pressure_reconstruction_molt_reminder_emitted`. Payloads carry a
  `message_hash` (never the full prose), the thresholds, and the state/branch
  fields. The abstraction stays pure — it builds descriptors, it does not log.
- **Debug view** — `to_debug_dict()` (aliased `snapshot()`) returns a flat
  JSON-friendly dict with the thresholds that drove decisions, the streak/active
  state, the last usage/round, and the last transition reason — suitable for
  tests, logs, and debugging.

Thresholds default to the kernel-fixed `CONTEXT_PRESSURE_*` constants
(`config.py`) but are stored on the instance so the debug dict reports exactly
which values applied and tests can inject variants.

## File layout

- `context_pressure.py` — `ContextPressureReminder` plus the pure prose
  renderers `render_current_molt_context(...)`, `render_reconstruction_molt(...)`,
  `render_forced_rebuild_warning(...)` (the one-shot channel-A reconstruction
  warning), and `render_forced_rebuild_failed_warning(usage)` (the persistent
  post-forced-rebuild overflow line — the fixed human-authored `100% context
  Forced Rebuilt Failed. … (xxx %) Molt IMMEDIATELY!!` sentence, one-decimal
  percentage). The renderers are the single source of truth for the wording and are
  shared by the class methods and by `meta_block` (its compatibility fallback and
  the overflow-warning merge, see below).
- `__init__.py` — re-exports `ContextPressureReminder`, the emission descriptors,
  and the two molt renderers (`render_current_molt_context` /
  `render_reconstruction_molt`); the two forced-rebuild renderers are imported
  directly from `context_pressure` by `meta_block`.
- `ANATOMY.md` — this file.

## Connections

- `session.py` — `SessionManager.__init__` constructs one
  `ContextPressureReminder` (`self._context_pressure`). `_track_usage` calls
  `note_context_pressure_round(...)` per real provider round. The properties
  `context_pressure_streak` / `context_pressure_warning_active`, the method
  `note_context_pressure_round`, and the new `context_pressure_reminder`
  accessor are thin compatibility shims that delegate to the reminder; existing
  callers/tests that read the streak surface keep working unchanged.
- `meta_block.py` — `build_molt_context` keeps the psyche-intrinsic gate and
  session lookup, then delegates rendering: it prefers
  `session.context_pressure_reminder.current_molt_context(usage)` and falls back
  to `render_current_molt_context(streak, usage)` for lightweight session
  stand-ins that only expose the compat `context_pressure_*` attributes.
  `build_meta` (side-effect-free) routes the returned text into current
  `agent_meta.agent_state.context.molt` via a transit key (`_tool_meta_context`)
  that `ToolExecutor._attach_tool_block` promotes, and always carries the
  emission-event payload (`_tool_meta_context_event`) while active. The
  cache-miss budget guard (`meta_block.build_cache_miss_budget_context`, NOT part
  of this abstraction) reuses the SAME `_tool_meta_context` sub-object: when both
  fire, `build_meta` appends its `molt now` line to the pressure prose and adds
  `cache_miss_budget`/`cache_miss_tokens` — the channel-B emission event still
  hashes only the pure pressure message, and the budget guard emits no event.
  `build_reconstruction_tool_meta` keeps the event assembly (molt text lands on
  `agent_meta.agent_state.events.reconstruction.molt`). Both emission events
  fire only on a real attach.
- **Persistent forced-rebuild overflow warning** — the Codex adapter
  (`CodexResponsesSession`) owns a one-shot forced-rebuild latch + post-rebuild
  verification and exposes `context_overflow_status()` (default `None` on the
  `ChatSession` base, forwarded through the gate proxy).
  `meta_block.build_context_overflow_warning` reads it via `session.chat` and, when
  active, renders `render_forced_rebuild_failed_warning(usage)` and merges the fixed
  sentence into the SAME current `agent_meta.agent_state.context.molt` channel (its
  own newline, preserving any coexisting sustained-pressure / cache-miss lines). This
  is a current-state warning, not an event route — it attaches no
  `_tool_meta_context_event`.
- `tool_executor.py` — `ToolExecutor._attach_tool_block` promotes the transit
  keys into current `agent_meta.agent_state.context` and emits both reminder events via
  `self._log` (best-effort, never breaks a turn). The current-molt event is
  deduped to once per provider round using the per-turn executor's own
  `_last_current_molt_event_round` memory (one executor per turn), so the
  permanent per-result restamping does not flood the log; the reconstruction
  event is one-shot at source. Dedup lives here (at the real emission site), not
  in the render-path `build_meta`, which must stay pure.
- `config.py` — owns the kernel-fixed thresholds
  (`CONTEXT_PRESSURE_HIGH_RATIO` 0.85,
  `CONTEXT_PRESSURE_FORCED_REBUILD_RATIO` 1.0 with back-compat alias
  `CONTEXT_PRESSURE_RECONSTRUCTION_RATIO`, `CONTEXT_PRESSURE_WARN_AFTER_ROUNDS`
  3, and `CONTEXT_PRESSURE_RECOVERY_TARGET` 0.75) that this abstraction defaults from.

## Behavior invariants

- Warn only after sustained high provider rounds (3 consecutive `>= 0.85`); the
  old immediate recovery-target trip-wire stays retired.
- The reconstruction event is one-shot current-agent evidence (channel A) at
  `agent_meta.agent_state.events.reconstruction`, distinct from the
  current-state reminder (channel B).
- The persistent `Forced Rebuilt Failed` overflow warning is a current-state line
  on `agent_meta.agent_state.context.molt`, gated by the adapter's one-shot
  forced-rebuild latch + post-rebuild verification (active only when the forced
  rebuild fired, its first post-rebuild provider response was observed, and
  current provider usage is strictly `> 1.0`; exactly `1.0` carries no warning).
  It coexists on its own newline with the sustained-pressure and
  cache-miss-budget molt lines and emits no event.
- Reminder prose and thresholds are unchanged. Under the two-axis `_meta`
  contract (root `_meta` has only `tool_meta` and `agent_meta`), both reminders
  are current agent state, not immutable tool facts: the sustained-pressure
  reminder lives at `agent_meta.agent_state.context.molt`; the reconstruction
  reminder lives at `agent_meta.agent_state.events.reconstruction.molt`. Both
  ride the designated final batch result as part of the whole current
  `agent_meta` snapshot — older snapshots remain historical traces.
- Reminder-emission events are logged only when the reminder text is actually
  attached to the current `agent_meta.agent_state` snapshot (in
  `_attach_tool_block`), never on a bare render / condition check. The
  current-molt event is deduped to once per provider round so the restamping
  on every result while active does not flood the log.
