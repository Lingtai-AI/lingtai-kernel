---
name: tool-family-behavior-tests
behavior_version: 5
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/tool_family/__init__.py
  - src/lingtai/tools/tool_family/settings.py
  - src/lingtai/tools/tool_family/manual.py
  - src/lingtai/intrinsic_skills/system-manual/reference/tool-plugin-settings/SKILL.md
  - src/lingtai/tools/avatar/CONTRACT.md
  - src/lingtai/tools/avatar/settings.py
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/mcp/CONTRACT.md
  - src/lingtai/tools/email/CONTRACT.md
  - tests/test_tool_family_avatar_migration.py
  - tests/test_tool_family_manual_contract.py
  - tests/test_tool_settings_contract.py
  - tests/test_psyche_family.py
  - tests/test_mcp_identity_discovery.py
  - tests/test_email_abs_reply_route.py
maintenance: |
  Written by the tool-family CONVERT_BEHAVIOR migration (2026-08). Keep in
  sync with the CONTRACT.md clauses this file guards and the ANATOMY.md entries
  for the generic ChildTool/ToolFamily infrastructure and the migrating
  families (avatar, psyche, mcp, email; the former file family and its
  T001-T003 LABTs were removed with it). When a guarded contract changes
  in a way that affects agent-observable behavior (envelope errors, receipts,
  settings SHOW inventory, manual result shape, identity projection, reply
  routing), update the matching LABT here in the same change. Each LABT is self-contained: an agent executes
  it verbatim with only the tools it names, never by opening another file.
---
# ToolFamily Behavior Tests

LABT v1. These are self-contained agent-executable behavioral tests for the
families built on the generic `src/lingtai/tools/tool_family/` infrastructure.
They prove the *observable* promises of the family contracts this package
serves: the `avatar` spawn/settings/manual envelope (and the absence of the retired
`rules` action), the reserved `manual` child's
canonical result contract (no double wrap), the `psyche` five-manual router
plus redacted Pad settings (pad + lingtai + knowledge + skills = psyche), `mcp` identity
discovery with secret-safe projection, and `email` abs-mode reply routing with
the #145 ambiguity guard. Low-level mechanics stay in pytest; each LABT below
is executable verbatim by an agent with the tools it names. T001-T003 guarded
the former `file` family and were deleted with it; the ids are retired, not
reused.

## Behavior T004 — avatar spawn envelope: dry-run, mission gate, receipts

- **id**: T004
- **title**: `avatar` spawn exposes dry-run, the mission-quality gate, name
  validation, a verbatim `ok` receipt plus ledger, never requires admin, and
  preserves the exact unknown/omitted-action error envelope
- **guards**: `avatar-contract` § Tool surface / § Invalid or missing
  `action` / § State & storage
  ([CONTRACT.md](../avatar/CONTRACT.md#tool-surface))
- **supersedes**: `tests/test_tool_family_avatar_migration.py` (schema,
  dispatch, spawn-envelope, and manual tests)
- **runner**: any LingTai agent with the `avatar` capability (no admin needed
  — spawning never checks admin)
- **prerequisites**: a parent agent working dir (`<wd>`) whose parent
  `<network-root>` is writable (avatars are siblings of the parent: the avatar
  dir is `<network-root>/<avatar-name>`); `init.json` exists in `<wd>`; a
  fresh avatar name `helper` must not already exist
- **estimate**: 3 min (includes one real spawn; boot may report `slow`)

### Steps
1. Call `avatar(action="spawn", input={"name": "helper", "dry_run": true},
   reasoning="Investigate the heartbeat regression and report back")`.
2. Call `avatar(action="spawn", input={"name": "helper"}, reasoning="test")`
   (a 4-char mission) and read the result.
3. Call `avatar(action="spawn", input={"name": "helper", "confirm": true},
   reasoning="test")`. Record the receipt, then check
   `<network-root>/helper/init.json`, `<network-root>/helper/.prompt`, and
   `<wd>/delegates/ledger.jsonl`.
4. Call `avatar(action="spawn", input={"name": "../evil", "confirm": true},
   reasoning="a genuine mission brief, long enough to pass")` and repeat with
   `name: "a" * 65`.
5. Call `avatar(action="bogus", input={}, reasoning="...")` and then
   `avatar(input={"name": "x", "confirm": true}, reasoning="...")` with the
   `action` key omitted entirely.
6. Call `avatar(action="spawn", input={"name": "x", "rules_content": "no"},
   reasoning="a genuine mission brief, long enough to pass")` (an unknown key
   — `rules_content` names no current avatar action's input) and then
   `avatar(action="spawn", input={"name": "x", "confirm": true},
   reasoning="...", summarize="yes")`.
7. Call `avatar(action="manual", input={}, reasoning="...")`; record
   `manual_path` and compare `manual` with the packaged file
   `src/lingtai/tools/avatar/manual/SKILL.md`.

### Expected evidence
- [ ] Step 1 returns `{"status": "dry_run", "preview": {...}}` with
      `preview.name == "helper"`, `preview.type == "shallow"`, and
      `preview.mission` containing the reasoning; no `<network-root>/helper`
      dir and no `<wd>/delegates/ledger.jsonl` were created.
- [ ] Step 2 returns `{"status": "confirmation_needed", ...}` whose `warning`
      contains `confirm=true`; nothing was launched and no avatar dir exists.
- [ ] Step 3 returns `{"status": "ok", "address": "helper", "agent_name":
      "helper", "type": "shallow", "pid": <int>, ...}` (a `warning` key is
      allowed when boot is slow); `<network-root>/helper/init.json` exists;
      `<network-root>/helper/.prompt` contains the reasoning text; the ledger
      contains a record with `event: "avatar"`, `name: "helper"`, and
      `boot_status: "ok"`.
- [ ] Step 4: both unsafe names return an `error` and no avatar dir was
      created (names must match `^[\w-]+$`, be ≤64 chars, with no dot, slash,
      or leading `.`).
- [ ] Step 5 returns exactly `{"error": "unknown action: 'bogus', only
      'spawn', 'settings', or 'manual' is supported"}` and `{"error":
      "unknown action: '', only 'spawn', 'settings', or 'manual' is
      supported"}` for the
      omitted case — no spawn, no ledger, no process.
- [ ] Step 6: the unknown key fails with `{"status": "failed",
      "error_code": "INVALID_ARGUMENT", "message": "unsupported avatar input
      field"}` before any I/O, and `summarize: "yes"` fails with
      `INVALID_ARGUMENT` before any spawn.
- [ ] Step 7 returns `{"status": "ok", "action": "manual", "manual":
      "<exact body>", "manual_path": "<host path>"}`; `manual` equals the
      packaged SKILL.md body; the result has no `content`/`structuredContent`
      keys (avatar owns its own manual child; nothing is double-wrapped).

### Pass / Fail
Pass when every evidence item holds. Fail if a dry-run or guarded call
launches a process, a guarded mission spawns without `confirm`, an unsafe
name is accepted, `action`-less payload is inferred as `spawn`, or the
manual result is wrapped. Forbidden side effect: any failed spawn must leave
no avatar dir, no ledger record, and no live process.

## Behavior T005 — avatar has no rules action or automatic rules fan-out

- **id**: T005
- **title**: `avatar` owns no `rules` action and performs no automatic
  post-spawn rules broadcast; `.rules` is unaffected as a separate, unchanged
  heartbeat mechanism
- **guards**: `avatar-contract` § Tool surface — contract_version 9
  ([CONTRACT.md](../avatar/CONTRACT.md#tool-surface))
- **supersedes**: `tests/test_avatar_rules.py::TestAvatarRulesActionRemoved`,
  `::TestSpawnNoAutoRulesDistribution`;
  `tests/test_layers_avatar.py::TestUnifiedAvatarTool::test_rules_action_is_unknown_regardless_of_admin`
- **runner**: an agent with the `avatar` capability whose `admin` block
  includes at least one truthy privilege (e.g. `admin: {"karma": true}`)
- **prerequisites**: `<wd>` writable; no `<wd>/.rules` file before the run;
  `<wd>/system/rules.md` pre-populated with some content
- **estimate**: 1 min

### Steps
1. Call `avatar(action="rules", input={"rules_content": "Be concise."},
   reasoning="...")` and check whether `<wd>/.rules` exists.
2. Call `avatar(action="spawn", input={"name": "helper", "confirm": true},
   reasoning="a genuine mission brief, long enough to pass")`; check the new
   sibling directory for a `.rules` file.
3. Call `avatar(action="manual", input={}, reasoning="...")` once more and
   list `<wd>` before/after to confirm no spawn or filesystem side effect.

### Expected evidence
- [ ] Step 1 returns avatar's ordinary unknown-action error (see T004 step 5's
      exact string) regardless of the caller's admin karma, and no `<wd>/.rules`
      file is written.
- [ ] Step 2: the spawn succeeds, but the newborn sibling directory has no
      `.rules` file even though `<wd>/system/rules.md` was pre-populated
      (shallow spawn never copies `system/` at all; deep spawn's ordinary
      copy of `system/` is a separate, unaffected mechanism — not exercised
      by this step).
- [ ] Step 3: the manual result is the same no-I/O flat shape as T004 step 7
      and the file listing is unchanged.

### Pass / Fail
Pass when every evidence item holds. Fail if `action="rules"` performs any
write, or if a spawn writes a `.rules` file to the newborn. Forbidden side
effect: this behavior must not observe any `.rules` file appear anywhere as
a result of an `avatar` call.

## Behavior T006 — manual action contract: canonical result, no double wrap

- **id**: T006
- **title**: the family-owned `manual` child uses the reserved name, strict
  empty input, and the canonical `content[0].text` /
  `structuredContent.manual_path` result — returned verbatim by dispatch, with
  any family-specific flat shape adapted only in that family's Host layer
- **guards**: `tool-family` § Contract rules — `build_manual_child`
  ([CONTRACT.md](CONTRACT.md#contract-rules))
- **supersedes**: `tests/test_tool_family_manual_contract.py`
- **runner**: any LingTai agent with the `daemon`, `shell`, and `avatar` tools
- **prerequisites**: `<wd>` and the `daemon`/`avatar` capabilities; no other
  setup (the generic manual child is exercised through `daemon`, which
  registers `build_manual_child` directly and returns its canonical result
  verbatim; the self-owned manual child through `avatar`)
- **estimate**: 1 min

### Steps
1. Call `daemon(action="manual", input={}, reasoning="...")`; record `status`,
   `content[0].text`, and `structuredContent.manual_path`; read the path with
   `shell` (for example `cat -- "<manual_path>"`, output bounded as needed).
2. Call `daemon(action="manual", input={"id": "x"}, reasoning="...")` and
   `avatar(action="manual", input={"name": "x"}, reasoning="...")`.
3. Call `avatar(action="manual", input={}, reasoning="...")` and compare its
   `manual` field with the packaged `src/lingtai/tools/avatar/manual/SKILL.md`.

### Expected evidence
- [ ] Step 1: `status == "ok"`; the result keys are exactly `status`,
      `content`, `structuredContent`; `content[0].text` is the full installed
      daemon manual body and equals the file read from
      `structuredContent.manual_path`; the path ends with
      `capabilities/daemon/SKILL.md`.
- [ ] Step 2: every non-empty `input` on a strict-empty manual child fails
      with `error_code: "INVALID_ARGUMENT"` before the manual is loaded.
- [ ] Step 3: avatar's manual returns its own flat shape `{status, action,
      manual, manual_path}` — `manual` equals the packaged SKILL.md body and
      `manual_path` is that packaged file — with no `content` or
      `structuredContent` keys.
- [ ] The contrast between steps 1 and 3 holds: the generic child's canonical
      `content`/`structuredContent` shape is what `ToolFamily.handle()`
      returns verbatim (no double wrap), while avatar's own child defines its
      own canonical flat shape; neither family wraps the other's result.

### Pass / Fail
Pass when every evidence item holds. Fail if a manual body is missing or
summarized, the path is absent or not model-visible, the result is wrapped a
second time, or a manual call performs any target I/O.

## Behavior T007 — psyche: five-manual routing plus two-row settings, read-only

- **id**: T007
- **title**: `psyche` routes five installed manuals, shows two fully redacted
  Pad settings, performs no mutation or catalog scan, and rejects every retired
  action and every `input` key
- **guards**: `psyche-tool-contract` § Port (action inventory) / § Behavior
  ([CONTRACT.md](../psyche/CONTRACT.md#port))
- **supersedes**: `tests/test_psyche_family.py` (inventory, routing,
  read-only, and retired-root tests)
- **runner**: any LingTai agent with `shell` (the `psyche` intrinsic is mandatory)
- **prerequisites**: a fresh `<wd>` whose `.library/intrinsic/capabilities/`
  holds the installed manuals `pad-manual`, `lingtai-manual`, `knowledge`,
  `skills`, and `psyche-manual` (installed by the agent initializer)
- **estimate**: 2 min

### Steps
1. Record a baseline listing of every file under `<wd>` with
   `shell(action="run", input={"command": "find <wd> -type f | sort"},
   reasoning="...")`.
2. For each action `a` in `["pad", "lingtai", "knowledge", "skills",
   "manual"]`, call `psyche(action="<a>", input={}, reasoning="...")` and
   record `status`, `manual`, and `manual_path`. Call `pad` and `manual` a
   second time and compare bodies.
3. Inspect the `manual` action's returned body for the four domain spellings
   and the shared mutation/rebuild model.
4. Inspect the `knowledge` and `skills` bodies for current-route call shapes.
5. Call `psyche(action="settings", input={}, reasoning="...")`; record every
   row field, then repeat with `input={"set": "pad"}`.
6. Call `psyche(action="pad_edit", input={}, reasoning="...")`, then
   `psyche(action="", input={}, reasoning="...")`, then `psyche(action=
   "manual", input={"files": ["x"]}, reasoning="...")`.
7. List `<wd>` again with the same `shell` command and compare with the step 1
   baseline.

### Expected evidence
- [ ] Step 2: every action returns `{"status": "ok", "manual": <non-empty
      body>, "manual_path": <path>}` with `manual_path` ending in
      `.library/intrinsic/capabilities/pad-manual/SKILL.md`,
      `lingtai-manual/SKILL.md`, `knowledge/SKILL.md`, `skills/SKILL.md`, and
      `psyche-manual/SKILL.md` respectively; the five bodies are all distinct;
      repeated calls return byte-identical bodies (no stateful side effect).
- [ ] Step 3: the `manual` body is the routing table — it contains
      `action="pad"`, `action="lingtai"`, `action="knowledge"`,
      `action="skills"`, plus `shell` as the durable-mutation route and
      `context(action="rebuild"`; it teaches no `file.write`/`file.edit`.
- [ ] Step 4: the `knowledge` body contains `psyche(action="knowledge"`,
      `shell(action="run"`, and `context(action="rebuild"`; the `skills`
      body contains `psyche(action="skills"`, `context(action="rebuild"`,
      `shell(action="run"`, `system(action="refresh"`,
      and `"revert_preset": null`. Neither body teaches a retired public root
      (`pad(action=`, `lingtai(action=`, `knowledge(action=`, `skills(action=`,
      `substrate(action=`, `file(action=`) nor `pad.append` / `knowledge.info`
      / `skills.info`.
- [ ] Step 5: success is exactly `pad`, then `pad_file`; each row contains
      exactly `key`, `current`, `default`, `configurable`, `comment` in that
      order, both values are `<redacted>`, both rows are configurable, and the
      comments point to `psyche-manual#setting-pad` / `#setting-pad-file`.
      Invalid input fails with no inventory and no source value.
- [ ] Step 6: `pad_edit` returns `{"error": "Unknown psyche action:
      pad_edit. Must be one of: pad, lingtai, knowledge, skills, settings, manual."}`;
      the omitted/empty and other retired spellings (`lingtai_update`,
      `lingtai_load`, `pad_load`, `pad_append`, `context_molt`, `name_set`,
      `name_nickname`, `molt`, `summarize`, `rebuild`) fail with the same
      `Unknown psyche action: ...` shape; any `input` key fails with
      `{"error_code": "INVALID_ARGUMENT", "message": "unsupported psyche
      input field"}` before the selected child runs.
- [ ] Step 7: the file listing is identical to the baseline — no `psyche`
      action created, edited, or deleted any file (every action is read-only).

### Pass / Fail
Pass when every evidence item holds. Fail if an action returns the wrong
manual, settings leak or drift from their exact shape/order, a body teaches a
retired root or an undispatchable call, any action mutates the tree, or a retired
action is accepted. Forbidden side effect: no `psyche` action may scan a
catalog, write a prompt or source file, change configuration, or reload prompt
state.

## Behavior T008 — mcp: identity discovery surfaces safe fields only

- **id**: T008
- **title**: `mcp` info surfaces per-server identity from
  `system/mcp_identities/<name>.json` projected to the non-secret allowlist,
  and keeps secrets and volatile timestamps out of the model-facing prompt XML
- **guards**: `mcp-contract` § Tool surface (info) / § Anchored claims
  (identity attached only when present; secrets stripped)
  ([CONTRACT.md](../mcp/CONTRACT.md#tool-surface))
- **supersedes**: `tests/test_mcp_identity_discovery.py`
- **runner**: any LingTai agent with the `mcp` capability and the `shell` tool
- **prerequisites**: `<wd>` is the agent working dir; the per-agent registry
  `<wd>/mcp_registry.jsonl` already contains a server named `telegram` (e.g.
  via `init.json` `addons: ["telegram"]` boot-time decompression, or an
  existing registry entry); the `shell` tool for writing identity files
  (`mkdir -p` the directory, then write with a quoted heredoc and `cat` the
  file back to verify)
- **estimate**: 2 min

### Steps
1. Write `<wd>/system/mcp_identities/telegram.json` with `shell` (verify the
   written bytes by reading the file back) where the
   JSON is `{"schema": "lingtai.mcp.identity.v1", "mcp": "telegram",
   "generated_at": "2026-06-24T10:00:00+00:00", "accounts": [{"alias":
   "main", "bot_username": "my_agent_bot", "bot_id": 123456789,
   "bot_display_name": "My Agent", "is_bot": true, "allowed_users_count":
   2, "contact_count": 5, "last_verified_at": "2026-06-24T09:59:00+00:00",
   "bot_token": "123:SUPERSECRET"}]}` — deliberately including a
   secret-shaped key.
2. Call `mcp(action="info", input={}, reasoning="...")` and inspect the
   `registered` list and `registry_path`.
3. Read the `<mcp>` section of your own current system prompt (the protected
   section the tool maintains) and look for `<registered_mcp>`, `<identity>`,
   `my_agent_bot`, `last_verified_at`, and `SUPERSECRET`.
4. Write `<wd>/system/mcp_identities/ghost.json` (same schema, `mcp`:
   `"ghost"`, one account) and `<wd>/system/mcp_identities/bad.json` with
   `schema: "something.else.v9"`; call `mcp(action="info", ...)` again. Then
   delete `<wd>/system/mcp_identities/telegram.json` and call `info` again.

### Expected evidence
- [ ] Step 2: `status == "ok"`, `registry_path == "<wd>/mcp_registry.jsonl"`,
      and the `registered` entry for `telegram` carries `identity` with
      `account_count == 1` and `accounts[0].bot_username == "my_agent_bot"`;
      the serialized `registered` payload contains none of `bot_token`,
      `SUPERSECRET`, `password`, `token`, `secret`, or any secret-shaped key.
- [ ] Step 3: the `<mcp>` prompt section contains `<registered_mcp>` and
      `<identity>` and `my_agent_bot`, but contains no `last_verified_at` (the
      volatile re-verification timestamp is excluded from the prompt XML) and
      no secret value.
- [ ] Step 4: `ghost` is NOT listed in `registered` (an identity without a
      registry match invents no entry); the malformed-schema `bad.json` is
      skipped without an error; after deleting the telegram identity, the
      `telegram` registered entry has no `identity` key.

### Pass / Fail
Pass when every evidence item holds. Fail if a secret-shaped key or value
appears anywhere in `info` output or the prompt XML, an identity for an
unregistered server appears, or a malformed identity file crashes `info`.
Forbidden side effect: `mcp` must never write or modify
`mcp_registry.jsonl` or any identity file (it is signpost-only).

## Behavior T009 — email abs reply routing and the ambiguity guard

- **id**: T009
- **title**: `email` abs-mode sends embed a `_return_route` so replies cross
  `.lingtai/` networks to the original sender's absolute path, and an
  ambiguous self-route is refused loudly instead of self-delivering
- **guards**: `email-contract` § Anchored claims — abs-mode replies / §
  Cross-platform invariants (`mode='abs'` return route)
  ([CONTRACT.md](../email/CONTRACT.md#anchored-claims))
- **supersedes**: `tests/test_email_abs_reply_route.py`
- **runner**: any LingTai agent with the `email` and `shell` tools
- **prerequisites**: `<wd>` is the agent working dir; the mailbox dirs
  `mailbox/inbox/`, `mailbox/sent/`, `mailbox/archive/` exist (created by the
  email capability); an original-sender absolute path `<abs-sender>` (e.g. a
  second network's agent dir) for the crafted inbound messages
- **estimate**: 2 min

### Steps
1. Call `email(action="send", input={"address": "peer-2", "subject":
   "hi", "message": "ping"}, reasoning="...")` (default peer mode), then
   call `email(action="send", input={"address": "<abs-sender>", "mode":
   "abs", "subject": "hi", "message": "ping"}, reasoning="...")` (abs
   mode). For each call, find the newest record under `<wd>/mailbox/sent/` and
   read its `message.json`.
2. Craft an inbound message: with `shell`, `mkdir -p
   <wd>/mailbox/inbox/<uuid>` and write `message.json` there via a quoted
   heredoc (read it back to verify), where `<uuid>` is a fresh id and the JSON is `{
   "from": "mimo-1", "to": ["mimo-1"], "subject": "cross-project ping",
   "message": "please reply", "received_at": "2026-06-24T10:00:00Z",
   "identity": {"agent_name": "mimo-1", "agent_id": "AGENT-DEV-1",
   "admin": {}}}` (bare `from` equal to your own name, a different agent_id,
   and NO `_return_route`). Then call `email(action="reply",
   input={"email_id": ["<uuid>"], "message": "should be refused"},
   reasoning="...")` and list `<wd>/mailbox/sent/` before/after.
3. Craft a second inbound message with the same fields PLUS `"_return_route":
   {"mode": "abs", "address": "<abs-sender>", "sender_agent_id":
   "AGENT-DEV-1"}` (new `<uuid2>`), then call `email(action="reply",
   input={"email_id": ["<uuid2>"], "message": "reply body"},
   reasoning="...")` and read the newest `<wd>/mailbox/sent/` record.

### Expected evidence
- [ ] Step 1: the peer-mode sent record has no `_return_route` key; the abs
      sent record carries `from` = your absolute working dir and
      `_return_route` = `{"mode": "abs", "address": "<your working dir>",
      "sender_agent_id": "<your agent id>"}`.
- [ ] Step 2: the reply returns an `error` starting `Reply target is
      ambiguous:` that contains `email(action='send', input={'mode': 'abs',`
      and `reasoning=`, and no new record appears under `<wd>/mailbox/sent/`
      (no outbound dispatch; the bare alias could have self-delivered).
- [ ] Step 3: the reply returns `{"status": "sent", ...}` and the newest
      sent record's `to` contains `<abs-sender>` with `mode: "abs"` (the
      `_return_route` address won over the bare `from` alias).

### Pass / Fail
Pass when every evidence item holds. Fail if an abs send omits
`_return_route`, a reply self-delivers to the responder's own inbox, or the
ambiguity guard silently sends instead of refusing. Forbidden side effect: an
ambiguous reply must create no sent record and dispatch no mail.

## Behavior T010 — molt's unsupported-input-field diagnostic is additive and local

- **id**: T010
- **title**: `context.molt`'s own declared diagnostic sidecar names the
  foreign `input` field and location on a failed molt call, the legacy
  three-key failure is unchanged underneath it, a sibling action with no
  sidecar still gets the plain legacy failure, and the diagnostic never
  claims `session_journal_path` must be relative
- **guards**: `tool-family` § Diagnostics sidecar
  ([CONTRACT.md](CONTRACT.md#diagnostics-sidecar))
- **runner**: any LingTai agent with the `context` tool
- **prerequisites**: a working dir (`<wd>`) with a valid session-journal entry
  path available for `session_journal_path` (see `../context/CONTRACT.md`);
  no other setup
- **estimate**: 1 min

### Steps
1. Call `context(action="molt", input={"summary": "s", "session_journal_path":
   "<valid path>", "keep_tool_calls": null, "keep_last": null, "files":
   ["a.txt"]}, reasoning="...")` — `files` is a wholly foreign key smuggled
   into `molt`'s own `input`.
2. Call `context(action="summarize", input={"items": [], "session_journal_path":
   "<valid path>"}, reasoning="...")` — `session_journal_path` here is a
   cross-action key that belongs to `molt`, not `summarize`.
3. Call `context(action="molt", input={"summary": "s", "session_journal_path":
   "<valid path>", "keep_tool_calls": null, "keep_last": null}, reasoning="...")`
   with every field correctly nested — a normal, well-formed call — to confirm
   the diagnostic path is not taken when nothing is foreign.

### Expected evidence
- [ ] Step 1 returns `{"status": "failed", "error_code": "INVALID_ARGUMENT",
      "message": "unsupported context input field", "diagnostics":
      [{"location": "context/molt/input.files", "code":
      "CTX_MOLT_UNSUPPORTED_INPUT_FIELD", "expected_form": "an input object
      containing only summary, session_journal_path, keep_tool_calls, and
      keep_last", "reason": "molt rejects foreign action input before it can
      shed context", "fix": "remove the foreign field or choose the action
      that owns it"}]}` and molt performed no shed/no I/O (no new
      `system/summaries/` file, no session state change).
- [ ] Step 2 returns the plain legacy `{"status": "failed", "error_code":
      "INVALID_ARGUMENT", "message": "unsupported context input field"}` with
      **no** `diagnostics` key — `summarize` declares no sidecar entry, so the
      cross-action key gets exactly the pre-existing failure shape.
- [ ] Neither step's message or diagnostic claims or implies that
      `session_journal_path` must be relative — an in-workdir absolute
      journal path remains a separate, valid, unrelated policy untouched by
      this feature.
- [ ] Step 3 succeeds (or fails only for reasons unrelated to field shape,
      e.g. an invalid journal) with no `diagnostics` key present — the
      sidecar never fires when `input` matches the declared schema.

### Pass / Fail
Pass when every evidence item holds. Fail if the `diagnostics` array is
missing, wraps a second envelope, appears for an opted-out action, contains a
raw value/path/exception string, or if any wording implies
`session_journal_path` must be relative. Forbidden side effect: any rejected
molt call (with or without a diagnostic) must shed no context and write no
new snapshot/summary/session state.

## Behavior T011 — settings SHOW is redaction-safe, bounded, and read-only

- **id**: T011
- **title**: settings SHOW is redaction-safe, bounded, and read-only
- **guards**: tool-family § [Optional settings provider](CONTRACT.md#optional-settings-provider)
  and § [Contract rules](CONTRACT.md#contract-rules)
- **runner**: any LingTai agent with shell access to a clean checkout
- **prerequisites**: a clean checkout; a working .venv/
- **estimate**: ≈ 1 minute

### Steps

1. Run:

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/src" .venv/bin/python \
     -m pytest -q -p no:cacheprovider tests/test_tool_settings_contract.py
   ```

### Expected evidence

- [ ] Opt-in/order, exact input, exact five-field success, private redaction,
      manual `comment` route, fixed whole-action failures, incremental bounding,
      exports, and exact production opt-in ownership pass.

### Pass / Fail

Pass when the suite passes; fail on any extra success field, leakage, partial
rows, mutation operation, or unbounded provider consumption. This task performs
no writes.
