"""lingtai — generic AI agent framework with intrinsic tools, composable capabilities, and pluggable services.

This top-level module is a lightweight, lazy facade. ``import lingtai`` loads
only the stdlib and the package version; every public name in ``__all__`` is
resolved on first access via :pep:`562` ``__getattr__`` from its canonical
source module. This keeps the wrapper import cheap and avoids pulling in
heavy provider SDKs, MCP servers, or the kernel until they are actually used.
"""
from __future__ import annotations

from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING

__version__ = _pkg_version("lingtai")

# PEP 562 lazy facade mapping: public name -> (canonical module, attribute).
# A None attribute means the public name is used as the attribute name.
_LAZY_EXPORTS: dict[str, tuple[str, str | None]] = {
    # Core kernel re-exports
    "BaseAgent": ("lingtai.kernel.base_agent", "BaseAgent"),
    "Agent": ("lingtai.agent", "Agent"),
    "AgentConfig": ("lingtai.kernel.config", "AgentConfig"),
    "AgentState": ("lingtai.kernel.state", "AgentState"),
    "Message": ("lingtai.kernel.message", "Message"),
    "MSG_REQUEST": ("lingtai.kernel.message", "MSG_REQUEST"),
    "MSG_USER_INPUT": ("lingtai.kernel.message", "MSG_USER_INPUT"),
    "UnknownToolError": ("lingtai.kernel.types", "UnknownToolError"),
    # Tools
    "setup_capability": ("lingtai.tools.registry", "setup_capability"),
    "ShellManager": ("lingtai.tools.bash", "ShellManager"),
    # Retained Python import compatibility; the registered capability/tool is
    # only ``shell``.
    "BashManager": ("lingtai.tools.bash", "BashManager"),
    "AvatarManager": ("lingtai.tools.avatar", "AvatarManager"),
    "EmailManager": ("lingtai.tools.email", "EmailManager"),
    # Services
    "FileIOBackend": ("lingtai.services.file_io", "FileIOBackend"),
    "FileIOService": ("lingtai.services.file_io", "FileIOService"),
    "GrepMatch": ("lingtai.services.file_io", "GrepMatch"),
    "LocalFileIOBackend": ("lingtai.services.file_io", "LocalFileIOBackend"),
    "LocalFileIOService": ("lingtai.services.file_io", "LocalFileIOService"),
    "BACKEND_ENV_VAR": ("lingtai.services.file_io_sidecar", "BACKEND_ENV_VAR"),
    "RustFileIOBackend": ("lingtai.services.file_io_sidecar", "RustFileIOBackend"),
    "SidecarAdapter": ("lingtai.services.file_io_sidecar", "SidecarAdapter"),
    "SidecarError": ("lingtai.services.file_io_sidecar", "SidecarError"),
    "default_file_io_service": (
        "lingtai.services.file_io_sidecar",
        "default_file_io_service",
    ),
    "resolve_sidecar_binary": (
        "lingtai.services.file_io_sidecar",
        "resolve_sidecar_binary",
    ),
    "MailService": ("lingtai.kernel.mail_transport", "MailTransportPort"),
    "MailTransportPort": ("lingtai.kernel.mail_transport", "MailTransportPort"),
    "FilesystemMailService": (
        "lingtai.adapters.posix.mail",
        "PosixFilesystemMailAdapter",
    ),
    "PosixFilesystemMailAdapter": (
        "lingtai.adapters.posix.mail",
        "PosixFilesystemMailAdapter",
    ),
    "LoggingService": ("lingtai.kernel.services.logging", "LoggingService"),
    "JSONLLoggingService": ("lingtai.kernel.services.logging", "JSONLLoggingService"),
    "VisionService": ("lingtai.services.vision", "VisionService"),
    "create_vision_service": ("lingtai.services.vision", "create_vision_service"),
    "SearchService": ("lingtai.services.websearch", "SearchService"),
    "SearchResult": ("lingtai.services.websearch", "SearchResult"),
    "create_search_service": ("lingtai.services.websearch", "create_search_service"),
}

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.kernel.config import AgentConfig
    from lingtai.kernel.message import MSG_REQUEST, MSG_USER_INPUT, Message
    from lingtai.kernel.state import AgentState
    from lingtai.kernel.types import UnknownToolError
    from lingtai.tools.avatar import AvatarManager
    from lingtai.tools.bash import BashManager, ShellManager
    from lingtai.tools.email import EmailManager
    from lingtai.tools.registry import setup_capability

    from .agent import Agent
    from .services.file_io import (
        FileIOBackend,
        FileIOService,
        GrepMatch,
        LocalFileIOBackend,
        LocalFileIOService,
    )
    from .services.file_io_sidecar import (
        BACKEND_ENV_VAR,
        RustFileIOBackend,
        SidecarAdapter,
        SidecarError,
        default_file_io_service,
        resolve_sidecar_binary,
    )
    from .services.vision import VisionService, create_vision_service
    from .services.websearch import SearchResult, SearchService, create_search_service
    from lingtai.kernel.services.logging import JSONLLoggingService, LoggingService
    from lingtai.adapters.posix.mail import (
        PosixFilesystemMailAdapter,
        PosixFilesystemMailAdapter as FilesystemMailService,
    )
    from lingtai.kernel.mail_transport import (
        MailTransportPort,
        MailTransportPort as MailService,
    )


def __getattr__(name: str) -> object:
    """Resolve a public facade name lazily from its canonical source module."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module_path, attr_name = target
    module = importlib.import_module(module_path)
    value = getattr(module, attr_name or name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [
    "__version__",
    # Core
    "BaseAgent",
    "Agent",
    "Message",
    "AgentState",
    "MSG_REQUEST",
    "MSG_USER_INPUT",
    "AgentConfig",
    "UnknownToolError",
    # Capabilities
    "setup_capability",
    "ShellManager",
    "BashManager",
    "AvatarManager",
    "EmailManager",
    # Services
    "FileIOService",
    "FileIOBackend",
    "LocalFileIOBackend",
    "LocalFileIOService",
    "RustFileIOBackend",
    "SidecarAdapter",
    "SidecarError",
    "BACKEND_ENV_VAR",
    "default_file_io_service",
    "resolve_sidecar_binary",
    "GrepMatch",
    "MailService",
    "MailTransportPort",
    "FilesystemMailService",
    "PosixFilesystemMailAdapter",
    "LoggingService",
    "JSONLLoggingService",
    "VisionService",
    "create_vision_service",
    "SearchService",
    "SearchResult",
    "create_search_service",
]
