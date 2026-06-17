"""Compatibility shim — the edit capability moved to lingtai_sdk (SDK-02).

The implementation now lives in
:mod:`lingtai_sdk.capabilities.file.edit`. This module rebinds that SDK
module into ``sys.modules`` under the historical name
``lingtai.core.edit`` so ``from lingtai.core.edit import setup`` (and
``get_schema``/``get_description``/``PROVIDERS``) keep resolving to the same
module object.
"""
from __future__ import annotations

import sys

from lingtai_sdk.capabilities.file import edit as _impl

sys.modules[__name__] = _impl
