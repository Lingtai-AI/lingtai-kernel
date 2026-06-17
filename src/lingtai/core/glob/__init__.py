"""Compatibility shim — the glob capability moved to lingtai_sdk (SDK-02).

The implementation now lives in
:mod:`lingtai_sdk.capabilities.file.glob`. This module rebinds that SDK
module into ``sys.modules`` under the historical name
``lingtai.core.glob`` so ``from lingtai.core.glob import setup`` (and
``get_schema``/``get_description``/``PROVIDERS``) keep resolving to the same
module object.
"""
from __future__ import annotations

import sys

from lingtai_sdk.capabilities.file import glob as _impl

sys.modules[__name__] = _impl
