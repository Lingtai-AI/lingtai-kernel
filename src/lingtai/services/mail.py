"""Re-export kernel mail services."""
from lingtai.kernel.services.mail import MailService, FilesystemMailService

__all__ = ["MailService", "FilesystemMailService"]
