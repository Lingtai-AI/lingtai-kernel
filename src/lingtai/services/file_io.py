"""Compatibility shim — ``FileIOService`` moved to ``lingtai_sdk`` (SDK-02).

The implementation now lives in :mod:`lingtai_sdk.services.file_io`. This module
is an *alias*, not a copy: it rebinds the SDK module object into ``sys.modules``
under the historical name ``lingtai.services.file_io`` so that

* ``from lingtai.services.file_io import LocalFileIOService`` keeps working, and
* ``monkeypatch.setattr(lingtai.services.file_io, "...", ...)`` patches the same
  module object the implementation reads its globals from.

Preserving module identity (rather than star-importing into a fresh module) is
what makes existing monkeypatch-based tests continue to pass unchanged.
"""
from __future__ import annotations

import sys

from lingtai_sdk.services import file_io as _impl

# Rebind the old dotted name to the real implementation module so that any code
# importing ``lingtai.services.file_io`` — including ``importlib.import_module``
# and ``monkeypatch.setattr`` — operates on the SDK module object itself.
sys.modules[__name__] = _impl
