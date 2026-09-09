---
name: soul-manual
description: |
  Read before calling `flow`, making consequential or unfamiliar Soul config/voice changes, or recovering a disabled/ongoing flow; routine inquiry, dismiss, and settings calls are schema-sufficient.
version: 1.6.0
last_changed_at: "2026-09-09T05:08:00Z"
related_files:
- src/lingtai/tools/soul/__init__.py
- src/lingtai/tools/soul/CONTRACT.md
- src/lingtai/tools/CONTRACT.md
- src/lingtai/tools/soul/flow.py
- src/lingtai/tools/soul/config.py
- src/lingtai/tools/soul/settings.py
- src/lingtai/tools/soul/consultation.py
- src/lingtai/tools/soul/manual/reference/flow.md
- src/lingtai/tools/soul/manual/reference/configuration.md
- src/lingtai/tools/soul/manual/reference/consultation.md
- tests/test_soul_settings.py
maintenance: |
  Keep this router and its focused references aligned with Soul's seven-action envelope, live settings anchors, opt-in gate, and consultation behavior.
---

# Soul Manual

Use the public schema for routine calls; keep root `summarize=false`. Read the matching procedure before `flow`, consequential/unfamiliar config or voice changes, or recovery. `manual` only returns this installed guide.

## Actions and routes

| Need | Read |
|---|---|
| Opt into flow, understand disabled/ongoing results, or stop recurring fires | [Flow and opt-in](reference/flow.md) |
| Change cadence/count or voice; verify live and saved values | [Configuration and voices](reference/configuration.md) |
| Understand inquiry, past-self fan-out, advisory output, or storage | [Consultation mechanics](reference/consultation.md) |

`inquiry` is synchronous reflection, independent of flow opt-in. `flow` is asynchronous and may read current/past-self context and run `1 + K` LLM calls. Its default-off operator gate protects cost and privacy: `status="disabled"` is expected, not a reason to retry. `dismiss` clears only Soul's notification; `settings` is read-only.

## Settings inventory anchors

SHOW comments link to these headings. SHOW grants no mutation authority; verify a change through its owner, then SHOW again.

### Flow enabled

Live `LINGTAI_SOUL_FLOW_ENABLED` gate; only the authorized environment owner changes it. Follow [flow](reference/flow.md), not `config`.

### Delay seconds

Enabled-flow interval, never an on/off switch. Change with `config`; see [configuration](reference/configuration.md).

### Consultation past count

Past-self count `K`, owned by `config`; see [configuration](reference/configuration.md).

### Voice

Flow profile, owned by `voice`; see [configuration](reference/configuration.md).

### Voice prompt

Sensitive custom prompt, redacted by SHOW; see [configuration](reference/configuration.md) before an authorized unredacted `voice` read.
