---
name: email-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/email/CONTRACT.md
  - src/lingtai/tools/email/ANATOMY.md
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/email/manager.py
  - src/lingtai/tools/email/settings.py
  - tests/test_email_official_tool_plugin.py
  - tests/test_email_settings.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when an
  email tool behavior clause changes, update the guarding LABT here in the same
  change.
---
# Email Capability Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/email/CONTRACT.md` (closed LTP v2 envelope, validation
before mailbox I/O, reserved `unread` rejection, exact receipts, typed
manager-facing runtime, live manager replacement, five-field/redacted settings,
and absence of dynamic Email capability state). Pinned
pytest commands must run from the repo root with the project's Python.

## Behavior EM001 — envelope failures are rejected before any mailbox I/O, and send returns the exact sent receipt

- **id**: EM001
- **title**: envelope failures are rejected before any mailbox I/O, and send returns the exact sent receipt
- **guards**: `email-contract` § Tool surface
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_layers_email.py -q` and capture the outcome.
2. Call `email(action="send", input={"address": "peer@example", "message": "hi", "unknown_key": 1}, reasoning="probe")` and record the result; confirm no mailbox file was created or modified.
3. Call `email(action="unread", input={}, reasoning="probe")` directly and record the result; then send a valid message and confirm the receipt contains `status: "sent"`.

### Expected evidence
- [ ] Step 1: the email layer suite passes, pinning the LTP v2 envelope, receipts, and wire parity.
- [ ] Step 2: the cross-action input key is rejected before any mailbox I/O, delivery thread, or read-state change.
- [ ] Step 3: direct `unread` is rejected with the reserved-action error before family dispatch; a valid send returns `{status: "sent", to, cc, bcc, delay}` and `reasoning`/`summarize`/`_tc_id` never reach the handler.

### Pass / Fail
Pass when the suite passes and the before-I/O rejection and exact receipt hold. Fail on an envelope failure that touches the mailbox, on a direct `unread` that dispatches, or on a send receipt missing `status: "sent"`; record the evidence trail in the task report.

## Behavior EM002 — official Email runtime is typed and does not persist a capability row

- **id**: EM002
- **title**: official Email runtime is typed and does not persist a capability row
- **guards**: `email-contract` § Declared host plugin
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_email_official_tool_plugin.py`.
2. Confirm `test_email_runtime_port_is_domain_specific_and_rejects_foreign_action`
   exercises `AgentEmailRuntimeAdapter`: it rejects a foreign action before its
   fake manager is called and flattens one valid normalized request into exactly
   one manager call. Confirm
   `test_email_bound_family_normalizes_before_typed_runtime_and_preserves_results`
   captures `EmailRuntimeRequest` values with top-level/nested nulls removed
   before typed-port manager parity for `check` and `edit_contact`.
3. Confirm the construction and refresh cases inspect `agent._capabilities`,
   `agent._build_manifest()["capabilities"]`, and persisted `.agent.json`; none
   contains an `email` row, while exactly one official `email` schema and handler
   remain mounted for null and disabled inputs.
4. Confirm `test_email_official_adapter_reads_replaced_manager_after_refresh`
   first observes refresh replacing `EmailManager`, then proves an already-bound
   handler reads a later replacement manager at call time. Call `check` and
   `manual` to confirm the real manager and package-owned manual remain available.

### Expected evidence
- [ ] Step 1: the Email official-plugin suite passes.
- [ ] Step 2: the family-facing boundary is
      `EmailRuntimePort.handle_email(EmailRuntimeRequest(...))`; top-level and
      nested nulls are absent before invocation, results remain exact, a foreign
      action is rejected before any manager call, and no generic dispatch or
      intrinsic route is exposed by the adapter.
- [ ] Step 3: construction and refresh preserve one official surface without a
      dynamic capability or persisted `.agent.json` row, including null/disable
      input parity.
- [ ] Step 4: refresh replaces the manager and a bound handler observes a later
      replacement at call time; `check` and `manual` retain their receipts.

### Pass / Fail
Pass when all four steps hold and the official Email surface is mandatory by intrinsic/official registration rather than dynamic capability state. Fail on a generic/foreign operation reaching the manager, an Email capability/manifest row, a duplicate schema, a missing manager, or a non-package manual.

## Behavior EM003 — settings inventory is exact, read-only, and private

- **id**: EM003
- **title**: settings inventory is exact, read-only, and private
- **guards**: `email-contract` § Settings
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 10 minutes

### Steps
1. From `<repo>`, run `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_email_settings.py`.
2. Call `email(action="settings", input={}, reasoning="inventory Email policy")`; confirm four active public constants plus the redacted `manifest.pseudo_agent_subscriptions` row appear in exact order with exactly five fields.
3. Follow every `comment` to its exact heading in `email-manual`; pass non-empty `input` and confirm refusal without mutation, then exercise the unavailable-source case and confirm one fixed whole-action failure with no rows.
4. Confirm current/default subscription paths are both redacted, no mailbox/session/identity/content data appears, and an ordinary `contacts` call remains unchanged.

### Expected evidence
- [ ] Step 1 passes, including the operational-source and ordinary-action non-regressions.
- [ ] Every row has exactly `key`, `current`, `default`, `configurable`, and `comment`; the four fixed rows are non-configurable and the subscription row is configurable.
- [ ] Every comment names an existing owner-manual heading; the sensitive row redacts current/default through an unprojected private flag.
- [ ] Missing applied truth fails the complete response with `SETTINGS_UNAVAILABLE`, and non-empty input cannot invoke a set/reset/mutation path or create persistence.

### Pass / Fail
Pass when exact five-field inventory, manual pointers, full redaction, whole-action failure, and no-I/O behavior all hold. Fail on any extra field, mutation API, partial unavailable row, path/domain-data leak, missing manual target, or operational regression.
