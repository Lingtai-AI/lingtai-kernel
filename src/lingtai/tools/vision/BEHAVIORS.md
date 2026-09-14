---
name: vision-behavior-tests
behavior_version: 3
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/vision/CONTRACT.md
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/tools/vision/settings.py
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - tests/test_tool_plugin_declaration.py
  - tests/test_tool_family_vision_migration.py
  - tests/test_vision_capability.py
  - tests/test_vision_settings.py
  - tests/test_inherit_fallback.py
maintenance: |
  Keep this file reciprocal with CONTRACT.md and ANATOMY.md (tridirectional
  loop): when a Vision behavior clause changes, update the guarding LABT here
  and its family-specific executable evidence in the same change. Shared host
  fixture evidence is integration-owned and must not be copied into this file.
---
# Vision Capability Behavior Tests

These self-contained LingTai Agent Behavior Tasks (LABTs) guard the observable
five-action Vision contract. Pinned pytest commands run from the repository root
with the project's Python, `PYTHONDONTWRITEBYTECODE=1`, and pytest's cache
provider disabled.

## Behavior VN001 — declaration exposes five strict correlated actions

- **id**: VN001
- **title**: declaration exposes analyze/check/list/settings/manual with strict correlated input branches
- **guards**: `vision-contract` § Scope and declaration
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: a clean checkout of `<repo>`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_tool_family_vision_migration.py -k 'public_actions or root_schema or child_input_schemas or correlated'`.
2. Inspect `vision.get_schema()` and record the action enum and the five input
   branch titles.
3. Send an unknown action, a non-object input, and a cross-action field through
   the manager; record that each fails before handler/provider work.

### Expected evidence
- [ ] The action enum and branch titles are exactly `analyze`, `check`, `list`,
  `settings`, `manual`; `analyze` contains `image_path`, `question`, `preset`;
  `check` contains only `preset`; `list`, `settings`, and `manual` are strict
  empty objects.
- [ ] Root `action`/`input` correlations survive schema composition and invalid
  envelopes return an error before any provider I/O.

### Pass / Fail
Pass when all five actions, strict branches, and correlation guards are present
and invalid/cross-action calls are rejected before a child runs.

## Behavior VN002 — analyze uses one explicit route and fails closed

- **id**: VN002
- **title**: analyze preserves exact results and never performs an automatic provider or MCP fallback
- **guards**: `vision-contract` § Routing and preset authorization; § Results, errors, and state
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: `<repo>` and a small disposable image `<scratch>/img.png`
- **estimate**: ≈ 20 minutes

### Steps
1. Run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_tool_family_vision_migration.py tests/test_vision_capability.py -k 'analyze or fallback or default_vision_failure'`.
2. With a recording service, call `analyze` for an existing image and with
   `question: null`; record the exact success shape and one request.
3. Make that service raise a provider error and call `analyze` without a
   `preset`; record that only the selected service was called, no borrowed
   service/factory/MCP action was invoked, and the result is sanitized guidance.

### Expected evidence
- [ ] Success is exactly `{status: "ok", analysis: text}` and null question uses
  the stable default prompt.
- [ ] Missing/empty/request failures are structured and sanitized, with an
  accepted full manual envelope pointer.
- [ ] Alternatives in an error are instructions only: there is no automatic
  provider switch, legacy credential, preset borrow, or MCP invocation.

### Pass / Fail
Pass when the direct route is used once, result/error shapes remain exact, and a
failed route does not cause an implicit fallback.

## Behavior VN003 — check resolves identity without an image request

- **id**: VN003
- **title**: check reports default or allowed-preset identity without sending an image
- **guards**: `vision-contract` § Routing and preset authorization; § Results, errors, and state
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: the disposable allowed-preset fixture in the migration test
- **estimate**: ≈ 10 minutes

### Steps
1. Run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_tool_family_vision_migration.py -k 'check_'`.
2. Call `check` with `preset: null`; record its default provider/model result.
3. Call `check` with an allowed preset and with an unlisted preset; record the
   borrowed identity, no `analyze_image` call, and sanitized denial.

### Expected evidence
- [ ] Successful check has `status`, `route`, `provider`, and `model`, and no
  image request occurs.
- [ ] Only `manifest.preset.allowed` references can be borrowed; unlisted
  references fail closed with a manual pointer.

### Pass / Fail
Pass when check can construct the selected route for identity reporting but
never sends an image/provider request after route construction, and authorization failures are explicit.

## Behavior VN004 — list is mechanical and authorization-bounded

- **id**: VN004
- **title**: list classifies the active route and only allowed preset declarations without constructing services
- **guards**: `vision-contract` § Results, errors, and state
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: the disposable list fixture with one allowed vision preset and one unlisted text preset
- **estimate**: ≈ 10 minutes

### Steps
1. Run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_tool_family_vision_migration.py -k 'list_action or endpoint_classification'`.
2. Call `list` with a recording factory and record the default route, endpoint
   classification, and `responses_vision` flag.
3. Record that only the allowed vision preset appears; no service factory,
   credential resolver, image request, or unlisted preset is touched.

### Expected evidence
- [ ] `list` returns `default`, `presets`, and `count` with no credential fields.
- [ ] The unlisted text-only preset is absent and the provider factory is not
  called.

### Pass / Fail
Pass when enumeration is read-only and stops at the allowed-preset boundary.

## Behavior VN005 — manual is package-owned, exact, and side-effect free

- **id**: VN005
- **title**: manual returns the installed body/path once without provider or configuration reads
- **guards**: `vision-contract` § Results, errors, and state
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: a disposable workdir containing an installed Vision manual
- **estimate**: ≈ 10 minutes

### Steps
1. Run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_tool_family_vision_migration.py -k 'manual'`.
2. Call `manual` with a live service that would fail if used and record the
   installed body and host-local `manual_path`.
3. Call `manual` with an active-provider port that raises if read; record that
   manual still succeeds. Repeat with no installed manual and record the honest
   `degraded` result.

### Expected evidence
- [ ] Success is exactly the flat `status/action/manual/manual_path` result; the
  body is the installed `capabilities/vision/SKILL.md` content.
- [ ] Manual constructs no provider, reads no credential/configured route, and
  is not nested or double-wrapped; missing content is degraded honestly.

### Pass / Fail
Pass when the package manual is the only operational source and manual execution
has no provider/configuration side effects.

## Behavior VN006 — explicit preset credential identity and host ports stay narrow

- **id**: VN006
- **title**: allowed-preset credential routing uses the requested preset while bind uses live provider plus configuration ports
- **guards**: `vision-contract` § Ports and composition; § Routing and preset authorization
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: a disposable allowed-preset fixture and no real credential values
- **estimate**: ≈ 15 minutes

### Steps
1. Run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_tool_family_vision_migration.py tests/test_tool_plugin_declaration.py -k 'preset or declaration or configuration'`.
2. Inspect `DECLARATION.requires` and record `workdir`, `active_provider`, and
   `configuration`; record that `VisionConfiguration` is the only setup snapshot.
3. Borrow an allowed preset whose fixture declares an `api_key_env`; record that
   the resolver receives that preset's environment name/identity, not the active
   preset's credential, and that no credential value is printed or asserted.

### Expected evidence
- [ ] The Vision declaration requires exactly the three narrow ports and setup
  carries public kwargs through `VisionConfiguration`.
- [ ] The allowed preset's own provider/model/credential route is used only for
  the explicit borrow request. No active-preset switch or automatic fallback is
  implied.

### Pass / Fail
Pass when the local tests prove the narrow Vision-side contract. The shared
registrar fixture and cumulative port/name union remain serialized integration
gates, not parallel-lane evidence.

## Behavior VN007 — settings is exact, applied, redacted, and read-only

- **id**: VN007
- **title**: settings shows one exact five-field applied snapshot without exposing private routing
- **guards**: `vision-contract` § Settings discovery; § Results, errors, and state
- **runner**: any LingTai agent with shell access to this repository
- **prerequisites**: a disposable workdir and clearly fake private sentinels
- **estimate**: ≈ 15 minutes

### Steps
1. Run:
   `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_vision_settings.py tests/test_tool_settings_contract.py`.
2. Bind a valid local route, change its owner file afterward, and record the
   exact 13-key order, five-field order, applied current/default values, and
   unchanged second SHOW.
3. Bind fake OpenAI and Codex routes carrying endpoint, key pointer/value,
   header, token-path, and instructions sentinels; verify none renders.
4. Call settings with a set/reset-shaped input and compare the owner file bytes;
   then bind an invalid/manual-only route and record the fixed no-row failure.
5. Run one ordinary analyze call through a recording service.

### Expected evidence
- [ ] `DECLARATION.settings is True`; `settings` appears exactly once immediately
  before `manual` on the internal, Chat, and Responses schemas.
- [ ] Each row is exactly key/current/default/configurable/comment in that order,
  each comment resolves to its owner heading, and current stays the applied bind
  snapshot until refresh.
- [ ] Sensitive inputs never render or remain in the provider snapshot; non-empty
  input writes nothing; unavailable truth yields no partial rows.
- [ ] The generic 65,536-byte gate and an ordinary analyze result remain intact.

### Pass / Fail
Pass when focused tests prove exact order and values, manual anchors, complete
redaction, all-or-nothing failure, no writer/client construction on SHOW, and
ordinary-action non-regression.
