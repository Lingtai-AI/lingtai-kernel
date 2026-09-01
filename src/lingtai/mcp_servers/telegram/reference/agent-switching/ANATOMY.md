---
related_files:
  - src/lingtai/mcp_servers/telegram/reference/agent-switching/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/SKILL.md
  - src/lingtai/mcp_servers/telegram/agent_switching.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/channel_reply.py
  - src/lingtai/mcp_servers/telegram/security.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/services/mcp_licc.py
  - src/lingtai/adapters/channel_reply_state_lock.py
  - src/lingtai/adapters/posix/channel_reply_state_lock.py
  - src/lingtai/kernel/channel_reply/__init__.py
  - src/lingtai/kernel/channel_reply/_mutation_lock.py
  - src/lingtai/tools/channel_reply/CONTRACT.md
  - src/lingtai/tools/channel_reply/ANATOMY.md
  - src/lingtai/mcp_servers/ANATOMY.md
  - tests/test_telegram_agent_switching.py
  - tests/test_telegram_agent_switching_ingress.py
  - tests/test_telegram_agent_switching_contract_docs.py
  - tests/test_telegram_secret_redaction.py
  - tests/test_channel_reply.py
maintenance: |
  Keep this inventory reciprocal with the paired Contract and synchronized with
  the packaged manual, implementation ownership, state namespaces, and focused
  tests. Reverify every cited line range whenever any listed source moves.
---
# Telegram Agent Switching V1 Anatomy

This slice is the Telegram-owned adapter around Core `channel_reply/v1`. Telegram
owns admission, selection, raw-first ingress, target-task publication, anchored Bot
API replies, and producer state. Core owns reply authority and its filesystem
protocol. The paired Contract owns normative ordering, failure, privacy, and
retention promises.

## Components

- `src/lingtai/mcp_servers/telegram/agent_switching.py:47-139` defines the feature
  constants, five-minute/128/seven-day limits, datatypes, and executable state
  inventory. `AgentSwitchingStateStore` at `agent_switching.py:517-1409` owns
  selections, unavailable tombstones, body-free original-ownership and edit-
  rejection ledgers, picker/quarantine records, schema v4 cleanup progress,
  native-directory paging, and four-class retention. `TelegramAgentSwitchingRouter`
  at `agent_switching.py:1504-2784` owns worker lifecycle, descendant discovery,
  parsing/callbacks, sticky edit classification, route reservation/publication,
  reply drains, target cleanup rotation, and the optional feature builder.
- `src/lingtai/mcp_servers/telegram/manager.py:1394-1529` is the critical ingress
  seam: it persists the lossless raw envelope, calls the router, returns on every
  handled or fail-closed result, and only then builds ordinary owner preview/LICC.
  `manager.py:1459-1466` assigns ordinary edited-message fallback `wake=false`.
  Router start/stop integration is at `manager.py:972-989`.
- `src/lingtai/mcp_servers/telegram/server.py:643-689` prepares account config,
  constructs `TelegramService`, builds the optional switching router, injects it
  into `TelegramManager`, and keeps ordinary owner inbound LICC as the fallback
  callback.
- `src/lingtai/mcp_servers/telegram/channel_reply.py:18-199` is the Telegram owner
  adapter for Core. It drains/cleans one exact target root without creating absent
  state, pins root identity across cleanup, validates channel/positive private
  chat/original-message anchors, rechecks target eligibility, and performs the
  only Bot API reply send.
- `src/lingtai/services/mcp_licc.py:80-178` validates and atomically publishes the
  complete target LICC event under the selected Agent's `.mcp_inbox`; its failure
  logs are content-free. The consumer protocol remains in
  `src/lingtai/services/mcp_inbox.py`.
- `src/lingtai/adapters/channel_reply_state_lock.py:11-31` is the exact platform
  gate: it selects the Darwin POSIX adapter only for `(os.name, sys.platform) ==
  ("posix", "darwin")` and otherwise raises the fail-closed platform error.
- `src/lingtai/adapters/posix/channel_reply_state_lock.py:268-1469` implements the
  descriptor-pinned mutation session and native adapter: exact-root verification,
  no-symlink private directories/files, strict bounded reads, atomic writes,
  no-replace moves, owned removal, fsync, and Darwin descriptor scanning.
- `src/lingtai/kernel/channel_reply/_mutation_lock.py:18-205` defines the mutation
  identities, directory tokens, bounded scan/result types, session Port, and lock
  Port consumed by Core and the POSIX adapter.
- `src/lingtai/kernel/channel_reply/__init__.py:368-3120` owns the v1 request,
  receipt, grant, owner file store, target capsule/submit Port, owner transport,
  controller, single-use state machine, recovery, and retention. Its proof-bearing
  state and permanent no-remint decisions are not Telegram producer state.
- `src/lingtai/mcp_servers/telegram/security.py:29-227` owns message/token/path
  redaction for public failures, log records, manager boundaries, and process-
  lifetime logging safety.
- `src/lingtai/mcp_servers/telegram/SKILL.md:252-398` is the packaged operator
  manual for activation, controls, limits, routing/reply behavior, edited-message
  policy, retention, and the cooperative same-UID boundary. This `ANATOMY.md` and
  paired `CONTRACT.md` are the canonical architecture/normative documents packaged
  with it through `pyproject.toml` and `MANIFEST.in`.
- `tests/test_telegram_agent_switching.py:114-1916` is the router/state/cleanup
  suite; its sticky edit, v1 ledger, prepublication, and retention slice begins at
  line 1676. `tests/test_telegram_agent_switching_ingress.py:311-1566` is the
  manager composition/raw-first/target-only/fallback/edit matrix.
  `tests/test_telegram_agent_switching_contract_docs.py:21-148` pins packaged docs,
  platform truth, and build metadata. `tests/test_channel_reply.py:190-5167` owns
  platform/Core/adapter/filesystem protocol coverage.
  `tests/test_telegram_secret_redaction.py:37-526` owns the secret/error boundary.

## Connections

1. `server.build_manager()` normalizes account commands, constructs the service,
   builds a router only when an account explicitly enables switching, and injects
   the router into `TelegramManager`.
2. `TelegramManager` receives one Telegram update, computes the raw envelope,
   writes/merges `message.json`, and invokes `router.handle()` immediately after
   that durable owner record. This is the raw-first boundary.
3. The router handles callbacks and new-message selection/routing. For edits it
   reads directive, selection, and exact original ownership, then reserves one
   body-free rejection before any anchored local error. `True` returns through the
   manager before every ordinary owner surface; only `False` reaches the existing
   fallback and its edited-message `wake=false` policy.
4. A new target route revalidates the descendant pin, issues/reuses Core authority,
   commits body-free original ownership, creates the target capsule, reserves the
   target producer decision, and finally calls `push_inbox_event()` once. Target
   decision recovery never republishes a reserved/terminal/ambiguous event.
5. The selected target answers through the intrinsic `channel_reply` Port. Core
   writes target outbox state; the router's drain worker passes it to
   `TelegramChannelReplyAdapter`; Core claims/receipts enforce single use while the
   adapter derives and revalidates the owner-held Telegram anchor before sending.
6. The router's separate cleanup worker runs immediately and on a monotonic
   five-minute cadence. It gives owner switching state one total budget, owner Core
   one record, and a durably rotated bounded set of target roots one Core record
   each. Surface failures are isolated from polling, reply drain, and other cleanup
   surfaces.

## Composition

- Parent: [`src/lingtai/mcp_servers/ANATOMY.md`](../../../ANATOMY.md)
- Paired contract: [`CONTRACT.md`](CONTRACT.md)
- Packaged manual: [`../../SKILL.md`](../../SKILL.md)
- Public reply contract: [`src/lingtai/tools/channel_reply/CONTRACT.md`](../../../../tools/channel_reply/CONTRACT.md)
- Public reply anatomy: [`src/lingtai/tools/channel_reply/ANATOMY.md`](../../../../tools/channel_reply/ANATOMY.md)
- Core mutation Port: `src/lingtai/kernel/channel_reply/_mutation_lock.py`
- Target LICC producer/consumer: `src/lingtai/services/mcp_licc.py` and
  `src/lingtai/services/mcp_inbox.py`

## State

- `<owner>/telegram/agent_switching/state/selections/<identity>.json` — exact
  account/chat/human target pin; retained until reset or reselection.
- `<owner>/telegram/agent_switching/state/selection-unavailable/<identity>.json` —
  proof-free fail-closed tombstone for corrupt/unsafe/unreadable selection; retained
  until reset or a valid replacement.
- `<owner>/telegram/agent_switching/state/original-ownership/<digest>.json` — strict
  body-free v1 sticky source-chat ownership, keyed by exact
  account/chat/human/message identity; seven-day expiry when valid.
- `<owner>/telegram/agent_switching/state/edit-rejections/<digest>.json` — strict
  body-free v1 at-most-once local rejection, additionally bound to update id;
  seven-day expiry when valid.
- `<owner>/telegram/agent_switching/state/menus/<token>.json` and `.dead/*.dead` —
  picker binding/terminal result and raw switching quarantine. Menus use their own
  expiry; raw quarantine retains seven days.
- `<owner>/telegram/agent_switching/state/cleanup-progress.json` — strict schema v4:
  `next_class`, native `{position,pending}` cursors for `menus`, `dead`,
  `original_ownership`, and `edit_rejections`, plus `targets_after` for persistent
  target-root fairness.
- `<owner>/telegram/agent_switching/channel_reply_owner/` — Core-owned grants,
  requests, route/no-remint decisions, receipts, quarantine, and Core progress.
  Telegram calls Core APIs but adds no files to this schema.
- `<target>/.channel_reply/` — Core-owned capsule, outbox, claim, receipt, consumed,
  dead, temporary, and cleanup/recovery progress. Complete absence is ordinary
  no-work; an existing malformed or replaced root fails closed.
- `<target>/.telegram-agent-switching/router-decisions/<route-event>.json` — strict
  body-free Telegram producer truth binding target pin, opaque grant ref, event and
  payload/body digests, timestamps, and `reserved|published|failed`; permanent so
  absence or ambiguity never authorizes republication.
- `<target>/.mcp_inbox/telegram-agent-switching/<event>.json` — the only target-
  visible routed body, atomically published as one LICC event after owner ownership
  and target reservation are durable.
- `<owner>/telegram/<account>/inbox/.../message.json` — lossless raw Telegram owner
  record. Raw edit evidence is append-only even when the normalized current text is
  updated.

## Notes

- Sticky ownership is about the original Telegram message identity, not its current
  text. Reset/reselection changes future new-message routing only.
- Only the final target LICC carries the routed body. Ownership, rejection,
  cleanup, route-decision, and Core public receipt records are body-free or
  proof-free according to their owning schema.
- Hidden markers, callback tokens, ambiguous selectors/discovery, edit anchors, and
  stale/corrupt/malicious state are inputs, never authority by mere existence.
  Exact schema and identity validation precede trust; unsafe evidence is preserved
  or terminalized rather than converted into absence.
- The total switching-state cleanup budget is 128 across the four owner classes,
  not 128 per class. Target-root service uses a separate cap of 128 roots and one
  Core record per selected root.
- Platform support is deliberately narrower than generic POSIX: automatic state
  mutation is exact Darwin+POSIX only. Disabled accounts do not touch the gate.
- Same-UID sibling Agents are cooperative peers, not mutually hostile sandboxes.
  The design prevents accidental authority sharing and conversation projection; it
  does not claim OS isolation from a malicious sibling process.
