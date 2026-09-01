---
name: channel-reply
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/channel_reply/ANATOMY.md
  - src/lingtai/tools/channel_reply/__init__.py
  - src/lingtai/tools/channel_reply/schema.py
  - src/lingtai/tools/channel_reply/glossary-en.md
  - src/lingtai/tools/channel_reply/glossary-zh.md
  - src/lingtai/tools/channel_reply/glossary-wen.md
  - src/lingtai/tools/channel_reply/manual/SKILL.md
  - src/lingtai/kernel/channel_reply/__init__.py
  - src/lingtai/kernel/channel_reply/_mutation_lock.py
  - src/lingtai/adapters/channel_reply_state_lock.py
  - src/lingtai/adapters/posix/channel_reply_state_lock.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/registry.py
  - src/lingtai/agent.py
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/channel_reply.py
  - tests/test_channel_reply.py
maintenance: |
  Keep the paired Anatomy, manual, intrinsic schema, Core Port/store/transport,
  Telegram adapter, Agent composition, and shared contract tests aligned whenever
  request fields, authority boundaries, durability, retention, or receipts change.
---
# Channel Reply

## Purpose

`channel_reply` is the channel-neutral reply capability for routed messages. It
lets a target Agent submit one bounded plain-text reply using an opaque
owner-issued grant without learning channel credentials or destinations.

## Behavior

The intrinsic is statically registered. When the final intrinsic set of an
ordinary `lingtai.Agent` contains `channel_reply` and no submit Port was supplied,
the Agent composes `ChannelReplyTargetFileSubmitPort` only for the exact
`(os.name == "posix", sys.platform == "darwin")` macOS identity. Constructing that
Port validates the existing workdir: it neither creates `.channel_reply` nor
changes workdir permissions. Linux, Windows, and every other platform instead
receive a reason-bearing
`ClosedChannelReplySubmitPort`; Agent startup remains viable and submit returns a
deterministic local `dead` receipt stating the macOS only limitation,
with no file, provider, channel, or other external side effect. On a supported
platform, absence of an owner-created active capsule is likewise closed. A valid
capsule changes the same static intrinsic to a local queue writer without a
dynamic tool refresh or provider call.

The manifest advertises `channel_reply/v1` only when the final intrinsic exists
and a callable non-closed submit implementation is composed. A raw `BaseAgent`
may still receive an optional Port; absent means closed. A valid model call may
contain only protocol version, grant reference, target-bound request id, a fresh
target-authored request `created_at`, bounded plain text, and narrow bearer proof.
That request timestamp is generated for the concrete submit attempt and remains
subject to Core age/future-skew validation; it is distinct from immutable owner
grant issuance time. Unknown or authority-bearing fields fail closed before
dispatch.

Owner request state transitions through
`pending/claimed/prepared/sending/sent/failed/dead/ambiguous`. The store binds a
grant to one `request_id` before external send. One random claim token owns each
live dispatch: exact duplicates observing `claimed` or `prepared` return pending
and cannot send; a duplicate observing `sending` durably commits terminal
ambiguity. A committed exact `(grant_id, request_id)` returns the same terminal
receipt, while a different request after claim fails closed. Since channels may
lack outbound idempotency, `sending` is durable before the external call; restart
maps it to terminal `ambiguous` and never auto-resends. Proven pre-send states
recover to `pending` with their stale claim tokens cleared.

## Port and target transport

`ChannelReplySubmitPort.submit_channel_reply(ChannelReplySubmitRequest) ->
ChannelReplyReceipt` is the Core-owned channel-neutral boundary. Core and the
intrinsic do not branch on Telegram destinations, parse modes, media, retries,
paths, or tokens.

`ChannelReplyTargetFileSubmitPort` reads only the owner-created capsule at the
derived `.channel_reply/active_capsule.json`. Queue, claim, receipt, consumed, and
dead names use a framed SHA-256 digest of the full `(grant_id, request_id)` tuple,
so equal request ids under different grants cannot collide. Under the same native
mutation lock used by the owner drainer, it writes and file-syncs a hidden sibling,
uses the local platform's hard-link no-replace operation to publish the complete
inode, requests a directory sync where the platform exposes that primitive, then
removes the hidden name. Before every strict queue read, startup/recovery, drain,
and cleanup reconcile only exact inventoried hidden sibling names. A hidden-only
private regular inode or empty directory is removed by bounded parent-anchored
no-follow cleanup without reading payload bytes or changing permissions. A
nonempty directory obstruction is preserved without traversal; an exact private
same-inode two-link publication cut is normalized by
unlinking and syncing the hidden name before the canonical record follows the
normal strict path. Conflicts are never delivered as hidden bytes, and unknown
hidden names remain untouched.
These semantics have native Darwin/POSIX process-cut evidence. Automatic
selection rejects Linux before adapter construction and publishes no capability
marker. The POSIX adapter retains a dormant Linux no-replace primitive only for
explicit injected/test use; that code is not automatic activation, supported
platform scope, or native Linux evidence. There is no Windows channel_reply lock
implementation in V1.

`ChannelReplyOwnerFileTransport` validates that an exact outbox candidate is a
stable private regular single-link file before atomically renaming it into the
owner claim directory. Invalid exact candidates are never parsed, claimed, or
dispatched; they receive deterministic proof-free dead metadata before bounded
source purge. Raw/`claimed` recovery rolls back because no send boundary was
crossed. `dispatching` is persisted before adapter submission and any crash
thereafter becomes a durable terminal ambiguous receipt. Terminal commit compares and preserves the first valid receipt or
consumed decision: a blocked drainer returns a concurrent recovery's committed
result, and restart repairs missing proof-free bookkeeping/removes the claim
without changing receipt bytes or dispatching again. Terminal owner receipts are
bounded and target-readable; consumed and dead markers contain no text or proof.
A target duplicate returns its terminal receipt, treats a consumed marker with a
missing receipt or a dead marker as terminal fail-closed state, and never
requeues it.

## Adapters

Adapters implement the submit Port for their channel owner. The Telegram adapter
depends inward on Core, derives account/chat/message anchor only from owner-held
grant state, and composes the owner file transport for an already-authorized
target. Targets never provide those values. Before Core persists `prepared` or
`sending`, the adapter requires the exact anchor keys, a strict nonempty canonical
account alias, and positive non-bool private-user `chat_id` and
`reply_to_message_id`; rejection is terminal pre-send failure with zero account
lookup/call, so the unchanged Telegram payload builder can never omit a falsey
reply anchor. Invalid, expired, revoked, wrong-target, replayed, unknown, or
unsupported grants likewise fail closed with no owner send. Production owner
stores/transports and ordinary target submitters select the POSIX mutation lock
outside Core only for the exact Darwin/macOS identity. The selector's
dedicated unsupported-platform result is the only selection result ordinary
Agent composition converts to a closed Port; import, construction, or other real
POSIX adapter failures on a supported identity propagate. The Core-owned
mutation-lock Port defines a v1 verified mutation session:
`exclusive(state_dir, expected_root=...)` yields a root identity, opaque
session-owned directory tokens, one-component no-follow metadata inspection,
bounded scans and reads, atomic replace/no-replace publication, identity-bound
moves, exact-name bounded removal, and already-open directory fsync. Directory
tokens are session/root-bound and invalid after the context exits. The POSIX
implementation fails closed unless descriptor directory scans and the host's
native no-replace primitive are proven before session acquisition. Strict reads
obtain exactly the prevalidated regular-file size, hidden publication temps are
cleaned and parent synced after handled preparation/publication failures, and
private directory creation syncs the child and parent directories. Expected
move/removal first uses private staging/quarantine names so a deterministic
source replacement is not published or deleted as the expected object. Both move
dispositions retain a durable source-bound hidden backup until final destination
identity verification; a mismatch restores source without altering the observed
destination. Every hidden-to-canonical move/removal recovery is native
no-replace: a canonical collision preserves the canonical occupant and hidden
expected object and raises. Normal move/remove results are returned only after
required rollback, private backup cleanup, canonical restore, and
parent-directory syncs are proven; any unproven cleanup, restore, or required
fsync raises instead of being collapsed into `source-changed`, `retryable`, or
another clean state.

All three production Core transactions bind and consume the yielded verified
session. Once a transaction begins, every state read, bounded scan, private
directory creation, atomic write, move, removal, and fsync is dispatched through
session-owned root-relative directory tokens; mutable-root path fallback is not
available inside the transaction. Root identity is captured at construction and
supplied as `expected_root` at each acquisition. A production lock adapter must
validate that
the state root already exists as a private non-symlink/non-reparse directory
before opening the lock leaf; it must not create, chmod, truncate, append to,
seed, or repair a rejected root or lock leaf. The lock leaf is opened or created
privately without following a symlink/reparse point and is bound by stable
root/leaf identity before mutation begins. V1 has no Windows channel_reply state
lock, fallback lock, or adapter. Linux, Windows, FreeBSD, and every other
non-Darwin identity are explicitly unsupported by automatic selection and fail
closed as described above; they are not pending acceptance. Directly injected
functional Ports remain explicit host/test composition seams and do not widen the
automatic platform claim. PR1 does not add Telegram ingress, selectors, menus, discovery,
configuration UX, or cleanup cadence; those remain PR2 responsibilities.

## Authority, identity, and retention

Route-event IDs have one proof-free authoritative decision in the separate strict
`route_decisions/` namespace. The identity-bound decision is checked before the
proof-bearing canonical event and before every factory. Durable `reserved` is
written before the first factory call; `active` binds only framed event/authority
digests, never raw proof. Expired, revoked, missing, quarantined, and retired are
terminal no-remint decisions retained independently of canonical route-event
cleanup. Legacy canonical events are migrated at constructor recovery. Invalid
canonical shapes, including nonempty/nested directories, establish a
`quarantined` decision before return; that decision survives restart and remains
authoritative even if the obstructing path survives or is later removed.

A valid active event may replay the same grant/proof while the bound grant remains
live and unconsumed. Every revoke, cleanup, recovery, retirement, and sent or
possible-send ambiguous completion updates the proof-free decision before
best-effort canonical proof stripping. After a possible external send, the
terminal request receipt is persisted first, the grant is marked consumed second,
and route retirement is non-throwing cleanup last. Thus malformed route state
cannot interrupt conservative terminal accounting or enable transport replay.

Every authority read binds requested id/ref, canonical filename, embedded ids,
request tuple and receipt, and route-event input/id/grant/ref/proof digest. Strict
reads reject duplicate/unknown keys, noncanonical timestamps, bool versions,
symlinks, non-regular files, hardlinks/link-count anomalies, wrong private mode
or owner where meaningful, oversize records, and path/descriptor replacement.
Reads use no-follow where available plus open-handle `fstat` and before/after path
identity. Quarantine never reads, renames, chmods, or preserves an untrusted
symlink/hardlink/nonregular backing inode: it unlinks only the rejected canonical
entry and writes a separately created proof-free timestamped metadata record in
the private owner `.dead/` tree. Recursive no-follow cleanup removes old valid
metadata and immediately removes malformed/legacy raw dead records. Cleanup
strictly parses canonical grant/request/target schemas; absent, non-string, or
noncanonical timestamps are malformed and cannot retain proof or text forever.
Exact recognized orphan temps and malformed exact target outbox/claim directory
trees converge through bounded parent-anchored removal. Inaccessible,
wrong-owner, mounted, immutable, or OS-denied trees remain retryable rather than
being followed, chmodded, or force-erased. Target dead markers are deterministic,
proof-free, durable before source purge, and pinned while the matching source
still exists, so recovery is idempotent and cannot amplify markers. Unrelated
unknown names remain untouched.

Default grant lifetime is two hours (bounded to 60 seconds through 24 hours).
Proof-bearing authority, grants/requests, capsule, target outbox/claims/receipts,
consumed markers, and sanitized dead metadata have a default seven-day retention
bound. Terminal canonical route records lose raw proof immediately and may age out
after the 30-day canonical replay horizon. The separate proof-free no-remint
decision remains indefinitely, so retention can never turn absence into authority
to invoke a factory. These methods define policy and mechanics only; PR2 owns
scheduling/cadence.

## Persistence inventory

`CHANNEL_REPLY_STATE_INVENTORY` in Core is the mechanically checked source of
truth. It covers the owner root lock plus `grants/`, `requests/`,
`route_events/`, `route_decisions/`, and sanitized `.dead/<source>/`; and the
target root lock plus `active_capsule.json`, `outbox/`, `claims/`, `receipts/`,
`consumed/`, and `.dead/`. For every kind it records canonical filename grammar,
exact owned hidden-temp grammar (or explicitly none for lock files), proof/text/
authority sensitivity, writer algorithm and every interruption cut, recovery
owner, terminalization, and retention/sanitization. Runtime atomic writers reject
uninventoried paths or algorithm mismatches; structural tests compare the exact
directories/kinds and require recovery to consume the same temp grammar.

## Cooperative same-UID threat boundary

V1 assumes cooperative descendants that share the same OS UID. Within that
model, it protects against accidental routing, malformed input, traversal,
confused-deputy destination choice, duplicates, unauthorized non-holders, and
unsafe filesystem shapes. It does **not** authenticate against a malicious sibling
process running as the same OS user and able to read sibling-private files. Such
hostile same-UID isolation belongs to OS accounts, containers, sandboxes, or other
process-isolation work, not this V1 bearer/private-file contract. User-facing
manual prose remains task-oriented and does not imply a stronger boundary.

## Contract Rules

1. The target schema is closed to `version`, `grant_ref`, `request_id`,
   `created_at`, `text`, and `proof`.
2. Grant refs are opaque. Grant state binds target identity, protocol, expiry,
   revocation, one claimed request, one possible success, and owner anchor.
3. The target cannot choose account, token, chat, user, message, destination,
   parse/render mode, media/entities, retries, backoff, attempts, or paths.
4. Only one claim token may cross the external send boundary for a logical
   request; duplicates in active pre-send states do not dispatch.
5. Terminal receipts are bounded and omit user/reply text, proof, secrets,
   absolute paths, account aliases, and channel ids. Success refs are opaque.
6. Unknown post-send outcomes are terminal ambiguity and are never resent.
7. Once reserved, a route-event id never mints fresh authority, including after
   expiry, revoke, missing state, quarantine, canonical cleanup, or obstruction removal.
8. Canonical file identity and strict descriptor binding are required before any
   owner-private anchor may be used.
9. The capability marker describes a functional composed submit path, not merely
   the presence of schema/manual registration.
10. Active file-backed composition is macOS only. Every other platform
    remains statically visible, locally terminal `dead`, side-effect-free, and
    unadvertised; only the dedicated unsupported result may be converted to the
    closed Port.
11. Private files and bearer proof enforce the cooperative same-UID boundary only;
    they are not malicious-sibling authentication.

## Contract Tests

`tests/test_channel_reply.py` covers exact Darwin selector acceptance and Linux rejection,
Windows/FreeBSD rejection without a Windows adapter, propagation of supported
POSIX adapter construction errors, unsupported real-Agent startup with a
reason-bearing local `dead` receipt and zero marker/file/provider/channel side
effect, supported/injected marker preservation, ordinary supported-Agent
closed-to-queued composition with zero provider/live calls, schema authority
exclusion and LTP-v2 summary control, deterministic `claimed`/`prepared`/`sending`
overlap, request/grant/target binding, every recovery state, immutable route-event
decisions across restart/concurrency and the full issue/revoke/cleanup/retire/
constructor/sent/ambiguous obstruction cross-product, strict filename/ref/receipt/
proof binding, no-mutation symlink/hardlink/directory-child and mode/owner/path-
swap rejection, exact inventoried temp cleanup, POSIX child-process adjacent
hard-link cuts, blocked-dispatch and every terminal commit crash cut, recursive
sanitized dead retention, terminal-first sent/ambiguous proof retirement, real Telegram
`send_message` payload construction with positive private anchors, redacted
receipts, and temporary end-to-end target outbox → fake owner adapter → target
receipt.

## Maintenance

Update this Contract, its Anatomy, manual, schema, Core store/controller/
transport, Telegram adapter, Agent/registry wiring, and tests together whenever
the boundary changes.
