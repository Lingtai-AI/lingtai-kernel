---
name: mail-transport
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/mail_transport/ANATOMY.md
  - src/lingtai/kernel/mail_transport/__init__.py
  - src/lingtai/adapters/posix/mail.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/services/mail.py
  - src/lingtai/cli.py
  - tests/test_mail_transport.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Mail Transport

## Purpose

Mail transport is Core's outbound boundary for fire-and-forget agent messaging.
It separates the mail intrinsic's use of message delivery and inbox arrival from
the concrete transport, which today is a POSIX filesystem mailbox. Core sends
peer messages and receives arrivals without knowing addressing, storage paths,
polling, or concurrency mechanisms.

## Behavior

Agents and coding agents MUST preserve the current observable semantics:
fire-and-forget `send` (no request/response coupling), background non-blocking
`listen`, explicit idempotent `stop`, handshake-before-delivery, mailbox metadata
injection, attachment copy, atomic `message.json` write, pseudo-outbox priority
over the own-inbox scan, optimistic claim/rollback for subscribed outboxes,
0.5-second polling, and the current string error / `None` success result of
`send`. They MUST NOT let concrete-transport identities (POSIX vs Windows,
filesystem paths, `Path`, mailbox layout, polling cadence) leak up through this
Port, and MUST NOT construct a concrete transport inside Core. A `BaseAgent`
without an injected transport has the mail intrinsic disabled and MUST NOT create
one implicitly.

## Port

`MailTransportPort` exposes exactly four observable operations:

- `send(address: str, message: dict, *, mode: str = "peer") -> str | None` —
  deliver `message` to `address` fire-and-forget. Returns `None` on success;
  implementations return a human-readable error string for the failure cases
  they explicitly handle. The Port does not claim that every underlying failure
  is converted to a string. `mode` selects how the transport interprets
  `address` (`"peer"` or `"abs"`); the `address` vocabulary is the transport's
  and Core passes it through opaquely.
- `listen(on_message: Callable[[dict], None]) -> None` — start non-blocking
  background delivery. Received payloads are passed to `on_message` according to
  adapter delivery semantics; the Port does not promise end-to-end exactly-once
  delivery across process crashes or restarts.
- `stop() -> None` — stop background delivery and release resources; idempotent.
- `address` (property) `-> str` — this transport's own address, an opaque string.

The Port names no filesystem vocabulary (`working_dir`, `mailbox_rel`,
pseudo-agent subscriptions, `Path`). Those are adapter construction concerns.

## Adapters

`PosixFilesystemMailAdapter` is the only production adapter
(`src/lingtai/adapters/posix/mail.py`). It delivers messages as files written
into the recipient's inbox and polls its own inbox plus subscribed pseudo-agent
outboxes. It owns peer/abs address resolution, the
`mailbox/{inbox,outbox,sent}/<id>/message.json` layout and `attachments/`,
handshake liveness checks, atomic tmp→rename writes, the optimistic
outbox→claim→sent rename protocol with rollback, 0.5-second polling with
per-phase `OSError` isolation, and the runtime-probe ack. A test-only in-memory
fake in `tests/test_mail_transport.py` implements the same Port to prove
substitutability. IMAP/SMTP/network transports are explicitly out of scope.

## Contract rules

1. `send` is fire-and-forget and returns `None` on success. The POSIX adapter
   returns the current exact error strings for no agent, failed liveness,
   missing attachment, and final `message.json` write failure. Other filesystem
   exceptions retain existing behavior and may propagate; the Port does not
   upgrade them into a blanket no-raise guarantee.
2. Delivery is gated by the handshake (recipient is an agent and is alive) before
   any inbox write, except human pseudo-agents which skip the liveness check.
3. Payloads delivered through `send` carry injected `_mailbox_id` and
   `received_at`; `message.json` is written atomically (tmp → rename).
   Pseudo-outbox pickup preserves the already-authored payload rather than
   reinjecting those fields.
4. `listen` is non-blocking. Within one active listener, each newly observed
   inbox entry is dispatched at most once and entries already present when
   listening starts are not re-delivered. This is not an end-to-end exactly-once
   guarantee across crashes or restarts.
5. Subscribed pseudo-agent outboxes are polled before the own-inbox scan, with
   the two phases isolated so an error in one cannot skip the other in a tick.
6. Outbox claiming is optimistic: exactly one poller wins the initial
   outbox→private-claim rename. Losers do not write or dispatch; a winner that
   cannot persist its own-inbox copy or publish claim→sent rolls back its inbox
   copy and restores the claim for retry.
7. Polling cadence is 0.5 seconds. `stop` is idempotent.
8. Core imports, receives, invokes, and closes only the Port. Concrete POSIX
   construction belongs to outer composition roots; Core never names the adapter.

## Contract tests

`tests/test_mail_transport.py` runs the production adapter and an independent
in-memory fake through the shared Port contract (success→`None`, unknown
address→error string, delivery to `on_message`, `address` is a str, idempotent
`stop`), asserts the Port surface is exactly `send`/`listen`/`stop`/`address`
with no filesystem vocabulary, proves Core `base_agent` never names the concrete
adapter and `kernel/services/mail.py` never imports `lingtai.adapters`, checks the
public `MailService`/`FilesystemMailService` names resolve to the Port and
adapter, and proves the CLI composition root injects `PosixFilesystemMailAdapter`.
The POSIX mechanism itself (handshake strings, attachments, atomic write,
pseudo-outbox claim/rollback, probe ack, seen-skip, phase isolation) is
characterized in `tests/test_filesystem_mail.py` and `tests/test_services_mail.py`.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes require
synchronized Port, adapter, contract-test, and contract updates; structural or
composition changes also update the paired Anatomy and reciprocal parents.
