---
name: feishu-behavior-tests
behavior_version: 1
labt_version: 2
contract: ../../mcp_servers/feishu/SKILL.md
anatomy: ../../mcp_servers/ANATOMY.md
related_files:
  - src/lingtai/mcp_servers/feishu/SKILL.md
  - src/lingtai/mcp_servers/feishu/_family.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/tools/CONTRACT.md
  - tests/test_feishu_toolfamily_ltpv2.py
maintenance: |
  LABT v2, migrated 2026-08 from tests/test_feishu_toolfamily_ltpv2.py
  (previously filed as C006 under src/lingtai/tools/telegram/BEHAVIORS.md;
  re-homed here). Feishu has no CONTRACT.md as of 2026-08 — neither
  src/lingtai/tools/feishu/CONTRACT.md nor
  src/lingtai/mcp_servers/feishu/CONTRACT.md exists — so the contract-ish
  source is the feishu-mcp-manual SKILL.md
  (src/lingtai/mcp_servers/feishu/SKILL.md, § PUBLIC TOOL FAMILY: strict
  LTP-v2) plus the shared LTP v2 envelope in src/lingtai/tools/CONTRACT.md
  (§ Envelope), with code under src/lingtai/mcp_servers/feishu (_family.py,
  manager.py). When a feishu CONTRACT.md lands, repoint contract:/guards at it
  and close the loop here and in the root BEHAVIORS.md related_files.
---
# Feishu Behavior Tests

LABT v2. FE001 is a self-contained agent-executable behavioral test for the
`feishu` tool family's LTP v2 envelope: envelope validation short-circuits
before manager I/O, `accounts` identity handling, child-schema scrub rules, and
flat-vs-ltpv2 dispatch parity. The closest contract-ish source is the
feishu-mcp-manual SKILL.md § PUBLIC TOOL FAMILY: strict LTP-v2
(`src/lingtai/mcp_servers/feishu/SKILL.md`) plus the shared LTP v2 envelope in
`src/lingtai/tools/CONTRACT.md` — feishu has no CONTRACT.md yet (see
maintenance).

## Behavior FE001 — Feishu tool family LTP v2 envelope

- **id**: FE001
- **title**: feishu LTP v2 envelope validation, child schemas, and flat dispatch
- **guards**: `feishu-mcp-manual` § PUBLIC TOOL FAMILY: strict LTP-v2 ([SKILL.md](../../mcp_servers/feishu/SKILL.md#public-tool-family-strict-ltp-v2))
- **supersedes**: tests/test_feishu_toolfamily_ltpv2.py (CONVERT_BEHAVIOR)
- **runner**: any LingTai agent with the feishu capability (recording manager, no network)
- **prerequisites**: repo checkout with src/lingtai/mcp_servers/feishu; the feishu manager is a recording stub — no network; identity path is `/tmp/identities.json`; envelope rules per `src/lingtai/tools/CONTRACT.md` § Envelope.
- **estimate**: 30 minutes

### Steps

1. Call the feishu tool with 9 invalid envelope shapes (missing/unknown `action`, wrong `payload` type, etc.).
2. Call `feishu(action="accounts")` on a cold identity path.
3. Exercise `send` (receive_id/text/body combinations), `remove_contact`, `manual`, and each empty-input action.
4. Inspect the generated child tool schemas for the family.

### Expected evidence

- [ ] **Actions**: `FEISHU_ACTIONS` is exactly `("send", "check", "read", "reply", "react", "search", "delete", "edit", "contacts", "add_contact", "remove_contact", "accounts", "settings", "manual")` — 14 actions.
- [ ] **Envelope validation**: each of the 9 invalid shapes returns a `failed`-status result **before** any manager I/O (the stub records zero calls for those inputs).
- [ ] **accounts**: the valid call makes the manager receive exactly `{"action": "accounts"}` and returns `{status: ok, accounts: [main], details, ...}` with `identity_path == "/tmp/identities.json"`; the ltpv2 flat result equals the family handle result.
- [ ] **send**: requires `receive_id` with **text XOR content** (body); a payload with both or neither is rejected.
- [ ] **remove_contact**: accepts **exactly one** of `alias` / `open_id`; both or neither is rejected.
- [ ] **manual**: echoes the input verbatim or returns `{status: ok, skill: "feishu-mcp-manual", manual: <str>}`.
- [ ] **Child schemas**: `input` uses `anyOf` (not `oneOf`); `reasoning`/`summarize` never appear in child schemas or handlers; scrub preserves `required`, the `action` enum, `anyOf`/`allOf`, an `allOf` length of 14, and `additionalProperties: false`.
- [ ] **Empty-input branches**: exactly `{check, contacts, accounts, settings, manual}` accept an empty payload;
  successful `settings` additionally needs a bound service (otherwise a no-row
  `SETTINGS_UNAVAILABLE`), not a recording manager without that owner.

### Pass / Fail

PASS when validation short-circuits before manager I/O, flat==ltpv2 for accounts, and child schemas match the scrub rules; FAIL on any manager call for invalid input, any schema drift (`oneOf`, leaked `reasoning`/`summarize`), or a wrong action count.
