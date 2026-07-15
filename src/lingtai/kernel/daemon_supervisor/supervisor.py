"""Core boundary marker for detached supervisor composition.

Concrete lifetime, notification, deadline, process and execution composition is
implemented by :mod:`lingtai.tools.daemon.supervisor_runtime`.  The kernel
package intentionally does not import the tools or adapter layers.  POSIX
entrypoints compose the outer runtime directly; this module remains as a
source-level marker for the old import location and owns no runtime state.
"""
from __future__ import annotations

# Kept deliberately empty: request/manifest/control contracts live beside this
# module, while the concrete owner is an outer composition concern.
__all__: list[str] = []
