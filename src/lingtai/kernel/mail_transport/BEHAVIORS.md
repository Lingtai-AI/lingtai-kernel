---
name: mail-transport-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/mail_transport/CONTRACT.md
  - src/lingtai/kernel/mail_transport/ANATOMY.md
  - src/lingtai/kernel/mail_transport/__init__.py
  - src/lingtai/adapters/posix/mail.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  mail-transport behavior clause changes, update the guarding LABT here in the
  same change.
---
# Mail Transport Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/mail_transport/CONTRACT.md` (fire-and-forget send,
atomic stage-then-rename delivery, handshake-before-delivery). Pinned pytest
commands must run from the repo root with the project's Python.

## Behavior MT001 — send returns None on success and a partial inbox entry is never observable

- **id**: MT001
- **title**: send returns None on success and a partial inbox entry is never observable
- **guards**: `mail-transport` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>`
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_mail_transport.py -q` and capture the outcome.
2. Run `python -m pytest tests/test_filesystem_mail.py -q` and capture the outcome.
3. Send a peer message with an attachment to a live recipient in `<scratch>` and observe the recipient inbox: the listener never observes a dot-prefixed staging entry, and a failed send (e.g. missing attachment) leaves no partial inbox entry.

### Expected evidence
- [ ] Step 1: the mail-transport Port suite passes (success → `None`, unknown address → error string, delivery to `on_message`, idempotent `stop`).
- [ ] Step 2: the filesystem mechanism suite passes (handshake strings, attachments, atomic write, pseudo-outbox claim/rollback, seen-skip, phase isolation).
- [ ] Step 3: delivered messages carry injected `_mailbox_id` and `received_at`; the listener sees only complete entries; a failed send leaves no partial entry in the recipient's inbox.

### Pass / Fail
Pass when both suites pass and the atomic-delivery observation holds. Fail on a visible partial inbox entry, on a send that returns anything but `None` on success, or on delivery without the handshake; record the evidence trail in the task report.
