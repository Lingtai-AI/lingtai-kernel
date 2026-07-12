---
related_files:
  - src/lingtai/kernel/mail_transport/CONTRACT.md
  - src/lingtai/kernel/mail_transport/__init__.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/services/mail.py
  - src/lingtai/services/ANATOMY.md
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Mail Transport Port Anatomy

This folder is the Core-owned mail transport boundary: the technology-neutral
Port that lets Core send peer messages and receive inbox arrivals without knowing
the concrete transport. The production POSIX adapter that implements it lives
outside Core; its promises are defined in the paired
[`CONTRACT.md`](CONTRACT.md).

## Components

- `MailTransportPort` — abstract outbound Port with exactly `send`, `listen`,
  `stop`, and the `address` property
  (`src/lingtai/kernel/mail_transport/__init__.py:16-62`).
- `_new_mailbox_id` — the Core-neutral sortable mailbox-id generator kept in
  `src/lingtai/kernel/services/mail.py:29-44`; the adapter imports it from there.

## Connections

- Core receives a `MailTransportPort` as the `mail_service` constructor argument
  and uses only its methods: `listen` at start
  (`src/lingtai/kernel/base_agent/lifecycle.py:225`), `stop` at teardown
  (`src/lingtai/kernel/base_agent/lifecycle.py:277`), `send` from the email tool,
  and `address` for identity.
- The only production adapter is `PosixFilesystemMailAdapter`
  (`src/lingtai/adapters/posix/mail.py`), mapped structurally by
  [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
- The composition root `src/lingtai/cli.py` constructs and injects that adapter.

## Composition

- **Parent:** `src/lingtai/kernel/` (see
  [`ANATOMY.md`](../ANATOMY.md)).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns the Port's behavioral
  promises and lists the adapter and contract tests.
- **Adapter package:** [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).

## State

The Port itself owns no state; it is an abstract boundary. Persistent mailbox
state (`mailbox/{inbox,outbox,sent}/<id>/message.json` and `attachments/`) and
ephemeral polling state (`_seen`, the daemon poll thread) are owned by the POSIX
adapter and described in its anatomy and the paired contract.

## Notes

This is a navigation-only Port anatomy; the concrete mechanism, its ordering, and
its recovery semantics are normative in the paired `CONTRACT.md`. There is no
second concrete transport in the kernel — the old `MailService` ABC and
`FilesystemMailService` class were removed from `kernel/services/mail.py`, which
now retains only `_new_mailbox_id`.
