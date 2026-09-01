---
related_files:
  - ANATOMY.md
  - src/lingtai/tools/channel_reply/CONTRACT.md
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
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/registry.py
  - src/lingtai/agent.py
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/channel_reply.py
  - tests/test_channel_reply.py
maintenance: |
  Keep this Anatomy reciprocal with CONTRACT.md and connected to the tools
  parent. Update it with code when composition, durable state, retention, or the
  owner adapter route changes.
---
# channel_reply Anatomy

The `channel_reply` intrinsic is a static, normally unauthorized tool family for
one grant-bound plain-text reply through an owning channel adapter.

## Components

- `src/lingtai/tools/channel_reply/__init__.py` — composes the LTP v2
  `submit|manual` family, strips intrinsic `_tc_id`, parses the Core request, and
  dispatches only through `agent._channel_reply_submit_port`.
- `schema.py` — closed model-facing input: `version`, `grant_ref`, `request_id`,
  `created_at`, `text`, and `proof` only.
- `manual/SKILL.md` — discoverable Agent procedure and no-resend guidance.
- `src/lingtai/kernel/channel_reply/__init__.py` — Core Port and domain values;
  strict grant/request/route store; claim-token owner controller; target capsule
  and outbox submitter; atomic owner claimer/drainer; receipt, consumed, dead,
  recovery, proof-free route-decision, retention, and the executable
  `CHANNEL_REPLY_STATE_INVENTORY` covering all canonical/temp state kinds and cuts.
- `src/lingtai/kernel/channel_reply/_mutation_lock.py` — Core mutation-lock Port
  and Phase A v1 mutation-session value types: root/object identity, opaque
  directory token Protocol, scan/write/move/removal budgets and results, and the
  `ChannelReplyMutationSession` surface yielded by `exclusive(...)`.
  `src/lingtai/adapters/channel_reply_state_lock.py` selects the POSIX production
  lock only for the exact Darwin/macOS identity; all other identities
  receive a dedicated unsupported-platform result.
- `src/lingtai/mcp_servers/telegram/channel_reply.py` — Telegram owner adapter.
  It derives account/chat/reply anchor from grant state and exposes the Core owner
  file transport for an already-authorized target.
- `src/lingtai/agent.py` — ordinary composition root. When the final static
  intrinsic exists and no Port was supplied, it installs the target file submitter
  on macOS only. The dedicated unsupported-platform selector result
  installs a reason-bearing closed Port instead; real supported-adapter errors
  propagate. Final manifest writes advertise only a non-closed submitter.

## Connections

`lingtai.tools.registry.INTRINSICS` statically registers the family. `BaseAgent`
only stores an optional Core Port and has no Telegram import. On macOS,
`lingtai.Agent` composes `ChannelReplyTargetFileSubmitPort` without creating
capsule state or changing the caller-owned workdir mode. On Windows, FreeBSD, and
all other unsupported platforms it keeps the same static tool on a local
reason-bearing `ClosedChannelReplySubmitPort`. Submit is deterministic `dead`
with no file/provider/channel side effect, and the manifest omits
`route_capabilities.channel_reply`. A caller-injected non-closed Port retains the
existing marker semantics.

The target submitter reads an owner-created capsule and, under the same native
mutation lock as its owner drainer, file-syncs a complete hidden sibling before a
local hard-link no-replace operation publishes the tuple-digested outbox record.
Startup, submit, recovery, drain, and cleanup consume the inventory's exact temp
grammar first: hidden-only files and nonempty directory impostors are removed by
bounded parent-anchored no-follow cleanup without payload reads, chmod/chown, or
symlink traversal, and an exact same-inode/two-link cut is normalized before
strict canonical reading. Malformed exact outbox/claim shapes are rejected before
dispatch, receive one deterministic proof-free target-dead marker, and are purged
idempotently across recovery.
`ChannelReplyOwnerFileTransport` atomically claims that record, passes the parsed
Core request to an owner submit Port, and compare-preserves the first valid
bounded receipt/consumed decision across concurrent recovery and every crash cut.
The Telegram adapter depends inward on that transport and
`OwnerChannelReplyController`; its pure positive-private-anchor validator runs
before `prepared`/`sending`, and Core never imports Telegram.

## Composition

Parent: `src/lingtai/tools/ANATOMY.md`. Interface owner:
`src/lingtai/tools/channel_reply/CONTRACT.md`. Platform locks remain outside Core
under `src/lingtai/adapters/`; Telegram remains an adapter under
`src/lingtai/mcp_servers/telegram/`.

## State

Owner state contains private `grants/`, tuple-digested `requests/`, proof-bearing
`route_events/`, authoritative proof-free `route_decisions/`, and quarantine
`.dead/`. The decision is checked before every canonical event/factory and is
retained indefinitely; constructor migration, issue/reuse, revoke, cleanup,
retirement, and sent/ambiguous terminalization all establish it first. Therefore
a surviving nonempty/nested canonical directory, or its later removal, cannot
reopen minting. Terminal request then consumed-grant persistence precede
non-throwing route proof cleanup after any possible send.

Atomic private JSON writes use inventoried sibling temps, platform-qualified sync,
and native mutation locks. The POSIX adapter supplies FD-relative session
primitives for root/child directory tokens, metadata scans, strict exact-size
reads, atomic writes with hidden-temp cleanup, durable private directory creation,
identity-bound staged moves, expected-removal quarantine, bounded removal, and
directory fsync. Session acquisition requires native descriptor-relative
no-replace rename. Both move dispositions retain a durable source-bound hidden
backup through final destination identity verification, and all move/expected-
remove hidden-to-canonical recovery is no-replace: canonical collision retains
both objects and raises. Compensation paths fsync after private backup deletion
and canonical restore before any normal moved/removed/mismatch result; failed
cleanup, restore, or required fsync raises as ambiguous primitive failure rather
than returning a clean retry state. All three production Core transactions bind
the yielded verified session and resolve lexical state names to session-owned
root-relative directory tokens. Reads, scans, creates, writes, moves, removals,
and fsync cannot silently fall back to the mutable root path while the transaction
is active. Strict reads bind filename
and embedded identity with no-follow/open-handle `fstat`, reject
link-count/mode/owner and path replacement anomalies. Quarantine unlinks a rejected canonical name without
reading/chmodding/following its backing inode, then creates separate proof-free
timestamped metadata; recursive no-follow retention removes old metadata and
legacy/raw dead material. Canonical cleanup is schema-strict, only exact owned
temps are reconciled, and inaccessible/wrong-owner/OS-denied trees remain
retryable rather than being force-repaired. Unrelated hidden names remain
untouched.

Target state under `<workdir>/.channel_reply/` contains `active_capsule.json`,
`outbox/`, `claims/`, `receipts/`, `consumed/`, and `.dead/`. Full
`(grant_id, request_id)` digests name each tuple. Outbox hidden temps are not
candidates; canonical publication occurs only after complete file-synced content,
with directory durability qualified by the native platform/filesystem contract.
Raw/claimed recovery rolls back; `dispatching` recovers to terminal ambiguity;
target terminal receipt/consumed decisions are first-writer immutable, and
target duplicates read the committed receipt or stop on consumed/dead state.
Default authority/queue/sanitized-dead retention is seven days. Normal sent and
possible-send ambiguous terminalization commits request/grant terminal truth,
then changes a matching active route event to proof-free `retired`; other terminal
route decisions also strip proof. Canonical event tombstones may age out after the
default 30-day replay horizon, but the separate proof-free decision remains so
minting never reopens. PR2 owns cleanup cadence.

## Security and platform evidence

The normative threat boundary is
`src/lingtai/tools/channel_reply/CONTRACT.md#cooperative-same-uid-threat-boundary`:
V1 assumes cooperative descendants sharing one OS UID and does not authenticate a
malicious same-UID sibling that can read sibling files. Hostile-sibling isolation
belongs to OS/container/process boundaries. This reciprocal edge must remain with
that Contract section whenever filesystem or bearer language changes.

V1 active file-backed channel_reply support is macOS only. The Windows
adapter has been removed; Windows, FreeBSD, and every other unsupported platform
remain statically visible but locally closed, emit no route marker, and perform no
file/provider/channel side effect on submit. Darwin tests exercise the accepted
POSIX algorithm; deterministic identity tests prove that Linux automatic
selection is rejected before adapter construction. The dormant Linux no-replace
primitive remains available only to explicitly injected/test composition and is
neither automatic activation nor a native Linux support claim. The platform gate
remains outside Core state/recovery logic.

## Notes

PR1 creates no Telegram selector grants, routed target events, ingress branch,
menus, discovery, or configuration UX. An ordinary Agent without a valid capsule
is closed and performs no provider or channel call; an owner-created capsule
activates only the local queue path. A possibly committed external send is never
automatically resent.
