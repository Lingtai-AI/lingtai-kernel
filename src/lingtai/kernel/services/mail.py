"""Mailbox id generator for the mail transport boundary.

The mail transport boundary is Ports & Adapters:

- The Core-owned Port is ``lingtai.kernel.mail_transport.MailTransportPort``
  (technology-neutral ``send``/``listen``/``stop``/``address``).
- The production filesystem mechanism lives outside Core as
  ``lingtai.adapters.posix.mail.PosixFilesystemMailAdapter``.

Core no longer defines or constructs any concrete mail transport; there is no
``MailService`` ABC or ``FilesystemMailService`` class here anymore (the Port
supersedes the ABC and the adapter owns the concrete class). The composition
root (``lingtai.cli``) wires the adapter.

This module keeps only ``_new_mailbox_id`` — the sortable mailbox-id generator.
It is a Core-neutral, side-effect-free helper (no filesystem access, no adapter
dependency), kept here so the email tool
(``lingtai/tools/email/primitives.py``) imports it without depending on the
adapter, and so the adapter imports it from here (adapter → kernel, the allowed
direction). Keeping it in the kernel preserves the kernel-isolation invariant
(the kernel tree never imports ``lingtai.adapters``).
"""
from __future__ import annotations

import uuid


def _new_mailbox_id() -> str:
    """Build a sortable, human-scannable mailbox id.

    Format: ``<YYYYMMDDTHHMMSS>-<4 hex>`` — 20 chars total.

    This is the canonical generator for mailbox message ids. It lives in the
    kernel mail service so that the adapter's ``send()`` does not depend on the
    email tool; the email tool (``lingtai/tools/email/primitives.py``) imports
    it from here.
    """
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:4]}"


__all__ = ["_new_mailbox_id"]
