---
name: soul-manual
description: |
  Operational guide for the `soul` tool — your inner voice. Read this when: you call `soul(action='flow')` and get a `status: disabled` result; you want to understand why soul flow is off by default and how the operator enables it; you are tuning `delay_seconds`/`consultation_past_count` with `config` and want to know whether fires will actually happen; or you need the difference between the always-available actions (inquiry/config/voice/dismiss) and the opt-in `flow`. Covers the `LINGTAI_SOUL_FLOW_ENABLED` env gate, disabled-flow behavior, delay-vs-off-switch semantics, enabling/disabling, troubleshooting, and the privacy/cost rationale.
version: 1.0.0
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/tools/soul/__init__.py
- src/lingtai/tools/soul/flow.py
- src/lingtai/tools/soul/config.py
- src/lingtai/tools/soul/consultation.py
maintenance: |
  Tracks the tool/capability behavior it teaches; update when that tool's behavior changes.
---

# Soul Manual

`soul` is your inner voice. `inquiry`, `config`, `voice`, and `dismiss` are
**always available**. `flow` is **opt-in and disabled by default**.

## 1. The soul-flow gate

**Soul flow does not run unless an operator turns it on.** It is gated by one
environment variable, `LINGTAI_SOUL_FLOW_ENABLED`:

- **Enabled** when the value is `1`, `true`, `yes`, or `on` (case-insensitive,
  surrounding whitespace ignored).
- **Disabled** when unset, empty, or anything else (`0`, `false`, `no`,
  `off`, ...).

The gate governs **both** firing paths:

1. **The wall-clock timer** — the periodic cadence that would otherwise fire
   every `delay_seconds` while you are IDLE. When disabled, no timer is armed.
2. **Voluntary `soul(action='flow')`** — a call you make yourself. When
   disabled, it returns immediately and never spawns a fire.

A defensive last-line check inside the fire itself means even a stray residual
caller cannot fire while the gate is off.

## 2. Calling `flow` while disabled

`soul(action='flow')` returns, **before** taking any lock or spawning any
thread:

```json
{
  "status": "disabled",
  "enabled": false,
  "env_var": "LINGTAI_SOUL_FLOW_ENABLED",
  "message": "Soul flow is disabled by default ... set LINGTAI_SOUL_FLOW_ENABLED=1 ... See soul-manual skill."
}
```

**This is expected configuration state, not an error.** Do **not** retry it in
a loop — the result will not change until an operator sets the env var. If you
want soul flow, ask the operator to enable it (§4); otherwise use `inquiry` for
on-demand self-reflection.

## 3. `delay_seconds` is cadence, not an off switch

After the env opt-in, `delay_seconds` (set via
`soul(action='config', delay_seconds=...)`) controls **how often** the timer
fires — e.g. `300` = every 5 minutes, `7200` = every 2 hours; minimum `30`.
That is *all* it does:

- A **large** `delay_seconds` does not suppress flow — the env gate decides
  whether flow runs at all.
- A **small** `delay_seconds` does not enable flow — with the env var unset, no
  fires occur regardless of the delay.
- `config` itself never enables flow. It tunes and persists the knobs
  (`delay_seconds`, `consultation_past_count`) to `init.json`; while flow is
  disabled the result carries `soul_flow_enabled: false` and a `note` saying the
  knobs are saved but no fires will occur. Enabling is an **operator** action.

Historically flow was "muted" by setting `delay_seconds` to a huge sentinel
(e.g. `999999999`, ~31.7 years). That was a trust-based non-trigger and it was
unsafe: the giant delay only muted the **timer**, while the **voluntary** path
stayed live and could loop against the sleep gate, producing retry storms
(observed in trajectory audits: repeated
`voluntary_waiting_idle → voluntary_triggered` and simultaneous
`soul_fire_gate_check state=asleep` bursts). The sentinel also had to be
re-applied by hand and was easy to get wrong. The env gate replaces that
fragile convention with an explicit opt-in covering both paths.

## 4. How to enable / disable

Enabling is an operator/deployment action, not something the agent does to
itself:

1. Set `LINGTAI_SOUL_FLOW_ENABLED=1` (or `true`/`yes`/`on`) in the agent's
   runtime environment.
2. Refresh/restart the agent so the new environment is loaded.
3. (Optional) tune cadence with `soul(action='config', delay_seconds=...)` and
   voice count with `consultation_past_count`.

To **disable** again: unset the variable (or set it to `0`/`false`) and
refresh/restart. No `delay_seconds` sentinel is needed — the gate is the off
switch.

## 5. Checking the current state

- **Is flow enabled right now?** Run `soul(action='flow')` — `status: ok`
  means enabled, `status: disabled` means the env var is not set. You can also
  run `soul(action='config', ...)` and read `soul_flow_enabled` in the result.
- **Check the env from a shell:**
  `shell({"command": "printenv LINGTAI_SOUL_FLOW_ENABLED"})` — empty output
  means unset (disabled).
- **Enabled but no fires?** Fires only happen while you are IDLE and only after
  `delay_seconds` elapses. Confirm `delay_seconds` is a small, sane value and
  that you actually reach IDLE between turns.

## 6. Actions that always work (flow disabled or not)

None of these depend on the env gate.

- **`inquiry`** — ask a deep copy of yourself a question; the answer returns in
  the tool result. Use this for deliberate, on-demand self-reflection instead
  of waiting on flow. Requires the `inquiry` field.
- **`config`** — tune `delay_seconds` / `consultation_past_count`; persists to
  `init.json`. (Does not enable flow — see §3.)
- **`voice`** — read or set how your own soul-flow voice sounds
  (`inner`/`observer`/`custom`). Yours to choose; persists to `init.json`.
- **`dismiss`** — clear the current soul-flow notification from the panel.

## 7. Privacy and cost rationale

Soul flow is **off by default** deliberately:

- **Cost.** Each fire runs `M = 1 + K` parallel LLM calls (one stepped-back
  read of your current chat, plus `K` past-snapshot voices). Left on with a low
  delay, this is a recurring, silent token cost on top of your own turns.
- **Privacy / surprise.** Flow reads your current chat and past-self snapshots
  and injects involuntary voices into your history. Making it opt-in means an
  operator consciously decides to spend those tokens and surface that
  reflection, rather than it happening implicitly.

Enable it when the reflection is worth the cost; otherwise reach for `inquiry`
when you specifically want a considered pause.
