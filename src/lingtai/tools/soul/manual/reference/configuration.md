---
name: soul-configuration-reference
description: Procedures for Soul cadence, voice, and read-only settings.
related_files:
- src/lingtai/tools/soul/manual/SKILL.md
- src/lingtai/tools/soul/config.py
- src/lingtai/tools/soul/settings.py
- src/lingtai/tools/soul/flow.py
- src/lingtai/tools/soul/__init__.py
- tests/test_soul_settings.py
- tests/test_soul.py
maintenance: |
  Keep bounds, persistence owners, voice sensitivity, and SHOW behavior aligned with Soul's implementation and router anchors.
---

# Soul configuration and voices

## `config`

Send both nullable keys and at least one non-null. Use finite `delay_seconds >= 30` and integer `consultation_past_count` in `0..5`; `null` leaves that knob unchanged.

```json
{"action":"config","input":{"delay_seconds":300,"consultation_past_count":null},"reasoning":"slow the cadence"}
```

Cadence/count update live state and use `init.json` fields `manifest.soul.delay` and `manifest.soul.consultation_past_count` for persistence. A changed delay restarts the timer when applicable. `config` reads but never changes the flow gate; while disabled it returns `status: "ok"`, `soul_flow_enabled: false`, and an explanatory note. It cannot enable flow; see [flow](flow.md).

**Existing limitation, not a rollback guarantee:** a combined call with valid delay and invalid count can change the live delay before returning an error, without saving it. Validate both values before submitting; after an error inspect live and saved state before deciding on a correction. This guide does not change the implementation or its governing contract.

## `voice`

```json
{"action":"voice","input":{"set":null,"prompt":null},"reasoning":"inspect the Soul voice"}
```

Null/null reads. Set `inner` or `observer`, or `custom` with a non-empty prompt of at most 4000 characters. Built-ins ignore the supplied prompt and clear a previous custom prompt. Invalid voice input leaves the profile/prompt unchanged. Changes apply to the next consultation and use `manifest.soul.voice` and `voice_prompt` for persistence.

Custom text is sensitive: SHOW redacts current/default prompt values; `voice` read can expose the resolved prompt. Use that read only when disclosure is authorized.

## Verification and settings

`settings` with `input={}` returns five rows in order: `flow_enabled`, `delay_seconds`, `consultation_past_count`, `voice`, `voice_prompt`. Rows have `key`, `current`, `default`, `configurable`, `comment`. Unavailable or non-JSON-safe current truth fails the whole inventory, never partial rows.

`config`/`voice` attempt atomic disk replacement, but a persistence failure is currently logged as `persist_error` without changing their `status: "ok"`. **SHOW proves live values, not saved bytes.** When restart survival matters, verify only the relevant `manifest.soul` fields in `init.json` as well; do not dump the file or sensitive prompt. An `ok` result or the disabled note is not durable-save confirmation. Report a mismatch rather than treating this limitation as permission to repair or weaken the contract.

There is no `settings/soul.json`. The process environment owns the gate; `init.json` owns the other values. SHOW writes neither and changes no timers.
