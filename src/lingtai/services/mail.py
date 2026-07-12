"""Re-export the mail transport Port and its production POSIX adapter.

This wrapper is a high-level composition surface, so it may import the adapter.
``MailService`` is the Core-owned Port; ``FilesystemMailService`` is the
back-compat public name for the production adapter
(``PosixFilesystemMailAdapter``). Both names resolve to a single implementation
each — no shim, no dual implementation.
"""
from lingtai.kernel.mail_transport import MailTransportPort as MailService
from lingtai.adapters.posix.mail import (
    PosixFilesystemMailAdapter,
    PosixFilesystemMailAdapter as FilesystemMailService,
)

__all__ = [
    "MailService",
    "FilesystemMailService",
    "PosixFilesystemMailAdapter",
]
