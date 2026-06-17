"""Compatibility shim — Rust FileIO sidecar moved to ``lingtai_sdk`` (SDK-02).

The implementation now lives in :mod:`lingtai_sdk.services.file_io_sidecar`.
This module is an *alias*, not a copy: it rebinds the SDK module object into
``sys.modules`` under the historical name ``lingtai.services.file_io_sidecar``
so that

* ``from lingtai.services.file_io_sidecar import default_file_io_service`` keeps
  working (including ``Agent.__init__``'s auto-create path), and
* ``monkeypatch.setattr(lingtai.services.file_io_sidecar, "_packaged_binary",
  ...)`` patches the same module object ``resolve_sidecar_binary`` reads from.

The packaged Rust binary is intentionally left under ``lingtai/bin/`` for this
slice; ``_packaged_binary`` still resolves it via ``files("lingtai")``.
"""
from __future__ import annotations

import sys

from lingtai_sdk.services import file_io_sidecar as _impl

# Rebind the old dotted name to the real implementation module so importers and
# monkeypatchers operate on the SDK module object itself (module identity).
sys.modules[__name__] = _impl
