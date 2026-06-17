"""Compatibility shim — the write capability moved to lingtai_sdk (SDK-02).

The implementation now lives in
:mod:`lingtai_sdk.capabilities.file.write`. This module rebinds that SDK
module into ``sys.modules`` under the historical name
``lingtai.core.write`` so ``from lingtai.core.write import setup`` (and
``get_schema``/``get_description``/``PROVIDERS``) keep resolving to the same
module object.
"""
from __future__ import annotations

import sys

from lingtai_sdk.capabilities.file import write as _impl

sys.modules[__name__] = _impl
